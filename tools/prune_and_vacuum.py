#!/usr/bin/env python3
"""One-time telemetry archive/prune + VACUUM to reclaim scanner.db.

Run this with the SCANNER STOPPED — VACUUM needs exclusive access and will
fail with "database is locked" against a live scanner. It moves telemetry older
than the retention window into scanner_archive.db, deletes only confirmed
archived hot rows, then VACUUMs scanner.db to shrink the file on disk.

VACUUM rewrites the whole database, so it needs roughly as much free disk as
the current DB size (~1.2 GB) and can take a few minutes. The recurring
in-process prune (telemetry_prune_loop) keeps the DB small afterward, so this
only needs to be run once to clear the existing backlog.

Usage:
    python tools/prune_and_vacuum.py
"""

import os
import sys
import sqlite3

sys.path.insert(
    0,
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)

from storage.sqlite import (
    ARCHIVE_DATABASE_NAME,
    DATABASE_NAME,
    ScannerStorage
)
from config import (
    SCANNER_TELEMETRY_ARCHIVE_DATABASE,
    SCANNER_TELEMETRY_RETENTION_BY_TABLE
)

def main():
    retention = SCANNER_TELEMETRY_RETENTION_BY_TABLE

    if not os.path.exists(DATABASE_NAME):
        print(f"DB not found: {DATABASE_NAME}")
        return

    size_before = os.path.getsize(DATABASE_NAME) / 1e6
    print(
        f"DB: {DATABASE_NAME}  size {size_before:.0f} MB  "
        f"retention {retention}"
    )
    archive_db = SCANNER_TELEMETRY_ARCHIVE_DATABASE or ARCHIVE_DATABASE_NAME
    print(f"Archive DB: {archive_db}")

    try:
        stats = ScannerStorage().archive_telemetry(
            retention,
            archive_database_name=archive_db,
            batch=50000
        )
        for table, table_stats in stats.items():
            print(
                f"  archived {table}: +{table_stats.get('archived', 0)} "
                f"archive rows, -{table_stats.get('deleted', 0)} hot rows"
            )
    except sqlite3.OperationalError as exc:
        print(
            f"\nArchive/prune failed ({exc}). Is the scanner still running? "
            "Stop it and retry."
        )
        return

    print("VACUUM (rewriting the DB; can take a few minutes)...")
    db = sqlite3.connect(DATABASE_NAME, timeout=120)
    db.execute("PRAGMA busy_timeout=120000")
    db.isolation_level = None  # VACUUM cannot run inside a transaction
    # Switch to incremental auto-vacuum so future deletes can be reclaimed
    # online (PRAGMA incremental_vacuum) without another full, exclusive VACUUM.
    # The mode change takes effect during the VACUUM below.
    db.execute("PRAGMA auto_vacuum=INCREMENTAL")
    try:
        db.execute("VACUUM")
    except sqlite3.OperationalError as exc:
        print(
            f"\nVACUUM failed ({exc}). Needs exclusive access (scanner "
            "stopped) and ~{:.0f} MB free disk.".format(size_before)
        )
        db.close()
        return
    db.close()

    size_after = os.path.getsize(DATABASE_NAME) / 1e6
    print(
        f"done. {size_before:.0f} MB -> {size_after:.0f} MB "
        f"(reclaimed {size_before - size_after:.0f} MB)"
    )


if __name__ == "__main__":
    main()
