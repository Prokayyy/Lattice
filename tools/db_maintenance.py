#!/usr/bin/env python3
"""Recurring telemetry maintenance — runs independently of the scanner.

The prune loop inside main.py only runs while the scanner process is alive, so
when that service is down the hot DB grows unbounded (this is how scanner.db
reached ~4.5 GB). This script does the same retention work on a schedule (cron /
systemd timer) regardless of whether main.py is up, so the hot DB stays small no
matter what:

  1. archive_telemetry  — move rows past their per-table window into the warm
                          archive DB (scanner_archive.db), deleting from hot
                          only after they are safely archived. WAL-safe.
  2. incremental_vacuum — return the pages those deletes freed back to the OS,
                          online (no exclusive lock, unlike full VACUUM).
  3. parquet_roll       — roll archive rows past the warm window off to cold
                          Parquet (runs in the isolated analysis venv; optional,
                          never blocks steps 1-2 if it fails).

Safe to run while the scanner is live (steps 1-2 use short WAL transactions).
For a deep, full-file compaction, use tools/prune_and_vacuum.py in a window with
the scanner stopped.

Usage:
    python tools/db_maintenance.py
"""

import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
# chdir before importing storage so DATABASE_NAME / ARCHIVE_DATABASE_NAME (which
# resolve relative to cwd) point at this repo's scanner.db symlink.
os.chdir(REPO)
sys.path.insert(0, str(REPO))

from config import (  # noqa: E402
    SCANNER_ARCHIVE_PARQUET_DIR,
    SCANNER_ARCHIVE_PARQUET_ENABLED,
    SCANNER_ARCHIVE_WARM_RETENTION_DAYS,
    SCANNER_TELEMETRY_ARCHIVE_DATABASE,
    SCANNER_TELEMETRY_ARCHIVE_ENABLED,
    SCANNER_TELEMETRY_RETENTION_BY_TABLE,
)
from storage.sqlite import (  # noqa: E402
    ARCHIVE_DATABASE_NAME,
    ScannerStorage,
)


def _log(msg):
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{stamp}] db_maintenance: {msg}", flush=True)


def archive_step(storage):
    archive_db = SCANNER_TELEMETRY_ARCHIVE_DATABASE or None
    if SCANNER_TELEMETRY_ARCHIVE_ENABLED:
        stats = storage.archive_telemetry(
            SCANNER_TELEMETRY_RETENTION_BY_TABLE,
            archive_database_name=archive_db,
        )
        for table, s in stats.items():
            _log(
                f"archive {table}: +{s.get('archived', 0)} archived, "
                f"-{s.get('deleted', 0)} hot"
            )
    else:
        deleted = storage.prune_telemetry(SCANNER_TELEMETRY_RETENTION_BY_TABLE)
        for table, n in deleted.items():
            _log(f"prune {table}: -{n} hot")


def incremental_vacuum_step(storage):
    """Return freed pages to the OS without a full rewrite. No-op unless the DB
    was set to auto_vacuum=INCREMENTAL (done by the one-time reclaim)."""
    try:
        with storage.connect() as db:
            free = db.execute("PRAGMA freelist_count").fetchone()[0]
            mode = db.execute("PRAGMA auto_vacuum").fetchone()[0]
            if mode == 2 and free > 0:  # 2 == INCREMENTAL
                db.execute("PRAGMA incremental_vacuum")
                db.commit()
                _log(f"incremental_vacuum: reclaimed up to {free} free pages")
            else:
                _log(
                    f"incremental_vacuum: skipped (auto_vacuum={mode}, "
                    f"freelist={free})"
                )
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except Exception as exc:  # never let vacuum failure abort the run
        _log(f"incremental_vacuum error (ignored): {exc}")


def parquet_step():
    if not SCANNER_ARCHIVE_PARQUET_ENABLED:
        return

    analysis_py = REPO / "env-analysis" / "bin" / "python"
    if not analysis_py.exists():
        _log(
            "parquet roll skipped: analysis venv missing "
            f"({analysis_py}). Create it with pandas+pyarrow to enable."
        )
        return

    archive_db = SCANNER_TELEMETRY_ARCHIVE_DATABASE or ARCHIVE_DATABASE_NAME
    parquet_dir = SCANNER_ARCHIVE_PARQUET_DIR or str(
        Path(archive_db).resolve().with_name("parquet")
    )

    cmd = [
        str(analysis_py),
        str(REPO / "tools" / "parquet_roll.py"),
        "--archive-db", str(archive_db),
        "--parquet-dir", str(parquet_dir),
        "--warm-days", str(SCANNER_ARCHIVE_WARM_RETENTION_DAYS),
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=3600
        )
        for line in (result.stdout or "").splitlines():
            _log(line)
        if result.returncode != 0:
            _log(f"parquet roll failed (rc={result.returncode}): {result.stderr.strip()}")
    except Exception as exc:
        _log(f"parquet roll error (ignored): {exc}")


def finalize_outcomes_step(storage):
    """Complete overdue alert_outcomes windows for tokens that left the scan
    set early. Must run BEFORE archive_step so the window snapshots are still
    in the hot DB (analysis/runner_trainability_report.md rec #2a)."""
    try:
        updated = storage.finalize_overdue_alert_outcomes()
        _log(f"finalize_alert_outcomes: {updated} window rows updated")
    except Exception as exc:
        _log(f"finalize_alert_outcomes error (ignored): {exc}")


def candle_labels_step(storage):
    """Backfill fixed-horizon candle labels (6h/24h) for alerts and candidate
    events whose horizon has elapsed — the canonical training target
    (analysis/runner_trainability_report.md rec #2b)."""
    try:
        labeled = storage.update_alert_candle_labels()
        _log(f"candle_labels: {labeled} subjects labeled")
    except Exception as exc:
        _log(f"candle_labels error (ignored): {exc}")


def confirmations_step(storage):
    """Catch-up pass for two-stage confirmation rows the in-process loop
    missed (e.g. scanner downtime). Shadow data only."""
    try:
        evaluated = storage.evaluate_due_confirmations()
        _log(f"confirmations: {evaluated} candidate events evaluated")
    except Exception as exc:
        _log(f"confirmations error (ignored): {exc}")


def main():
    start = time.time()
    _log("start")
    storage = ScannerStorage()
    # Idempotent CREATE IF NOT EXISTS pass — the outcome/label steps need the
    # candidate_events + alert_candle_labels tables even if the scanner hasn't
    # restarted onto the schema that introduces them.
    try:
        import asyncio
        asyncio.run(storage.initialize())
    except Exception as exc:
        _log(f"schema init failed (continuing): {exc}")
    finalize_outcomes_step(storage)
    candle_labels_step(storage)
    confirmations_step(storage)
    try:
        archive_step(storage)
    except Exception as exc:
        _log(f"archive step failed: {exc}")
    incremental_vacuum_step(storage)
    parquet_step()
    _log(f"done in {time.time() - start:.1f}s")


if __name__ == "__main__":
    main()
