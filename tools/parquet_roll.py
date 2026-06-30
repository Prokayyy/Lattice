#!/usr/bin/env python3
"""Roll the oldest archive telemetry off to compressed monthly Parquet.

The retention job moves rows out of the hot DB into scanner_archive.db (the warm
layer). This rolls rows older than the warm window out of scanner_archive.db
into partitioned, zstd-compressed Parquet (the cold layer), then deletes them
from the archive DB so the warm layer stays queryable-but-small. Parquet is
~5-10x smaller and is the natural format for backtests / fine-tuning.

Output layout (hive-partitioned, readable as one dataset):
    <parquet-dir>/<table>/year=YYYY/month=MM/*.parquet

This script is intentionally config-free (stdlib + pandas + pyarrow only) so it
runs in the isolated analysis venv that has no scanner runtime deps. All
parameters come from the command line; tools/db_maintenance.py passes them.

Read it back for analysis with:
    import pandas as pd
    df = pd.read_parquet("<parquet-dir>/signal_snapshots")   # all months

Usage:
    python tools/parquet_roll.py --archive-db PATH --parquet-dir PATH \
        --warm-days 30 [--batch 100000] [--dry-run]
"""

import argparse
import os
import sqlite3
import sys
import time

try:
    import pandas as pd
except ImportError:
    sys.exit(
        "parquet_roll requires pandas + pyarrow. Install them in the analysis "
        "venv: env-analysis/bin/pip install pandas pyarrow"
    )

# (table, cutoff_column) — same telemetry targets the archiver uses.
TARGETS = (
    ("signal_snapshots", "timestamp"),
    ("token_candles", "bucket_start"),
)

# SQLite type-affinity tokens we treat as numeric. Everything else -> string.
_NUMERIC_TOKENS = ("INT", "REAL", "FLOA", "DOUB", "NUM", "DEC")


def _table_exists(con, table):
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table,),
    ).fetchone() is not None


def _column_types(con, table):
    """Return {column_name: 'numeric'|'text'} from the declared SQLite types.

    SQLite is dynamically typed, so we coerce per the declared affinity to keep
    the Parquet schema stable across batches (otherwise an all-null batch could
    write a different column type than a populated one and break dataset reads).
    """

    types = {}
    for row in con.execute(f'PRAGMA table_info("{table}")').fetchall():
        name, decl = row[1], (row[2] or "").upper()
        kind = "numeric" if any(tok in decl for tok in _NUMERIC_TOKENS) else "text"
        types[name] = kind
    return types


def _coerce(df, col_types):
    for col in df.columns:
        if col_types.get(col) == "numeric":
            df[col] = pd.to_numeric(df[col], errors="coerce")
        else:
            df[col] = df[col].astype("string")
    return df


def roll_table(con, table, cutoff_col, cutoff, parquet_dir, batch, dry_run):
    if not _table_exists(con, table):
        return 0

    remaining = con.execute(
        f'SELECT COUNT(*) FROM "{table}" WHERE "{cutoff_col}" < ?',
        (cutoff,),
    ).fetchone()[0]
    if dry_run:
        print(f"  [dry-run] {table}: {remaining} rows older than warm window")
        return remaining
    if remaining <= 0:
        return 0

    col_types = _column_types(con, table)
    out_dir = os.path.join(parquet_dir, table)
    os.makedirs(out_dir, exist_ok=True)

    moved = 0
    while True:
        df = pd.read_sql_query(
            f'SELECT rowid AS _rid, * FROM "{table}" '
            f'WHERE "{cutoff_col}" < ? ORDER BY "{cutoff_col}" LIMIT ?',
            con,
            params=(cutoff, batch),
        )
        if df.empty:
            break

        rids = df["_rid"].tolist()
        df = df.drop(columns=["_rid"])
        df = _coerce(df, col_types)

        ts = pd.to_datetime(df[cutoff_col], unit="s", utc=True, errors="coerce")
        df["year"] = ts.dt.year.fillna(0).astype("int32")
        df["month"] = ts.dt.month.fillna(0).astype("int32")

        df.to_parquet(
            out_dir,
            engine="pyarrow",
            compression="zstd",
            partition_cols=["year", "month"],
            index=False,
        )

        con.executemany(
            f'DELETE FROM "{table}" WHERE rowid = ?',
            [(rid,) for rid in rids],
        )
        con.commit()
        moved += len(rids)
        print(f"  {table}: rolled {moved}/{remaining}")

    return moved


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--archive-db", required=True)
    ap.add_argument("--parquet-dir", required=True)
    ap.add_argument("--warm-days", type=float, default=30.0)
    ap.add_argument("--batch", type=int, default=100000)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not os.path.exists(args.archive_db):
        print(f"archive DB not found (nothing to roll): {args.archive_db}")
        return

    cutoff = time.time() - max(args.warm_days, 0) * 86400
    con = sqlite3.connect(args.archive_db, timeout=120)
    con.execute("PRAGMA busy_timeout=120000")
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")

    total = 0
    try:
        for table, cutoff_col in TARGETS:
            total += roll_table(
                con, table, cutoff_col, cutoff,
                args.parquet_dir, args.batch, args.dry_run,
            )
        if not args.dry_run and total > 0:
            # Reclaim the space the deletes freed inside the archive DB. The
            # archive has no live writers (only the maintenance job touches it),
            # so a full VACUUM is safe here and needs no incremental mode.
            con.commit()
            con.isolation_level = None
            con.execute("VACUUM")
            con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        con.close()

    verb = "would roll" if args.dry_run else "rolled"
    print(f"parquet_roll: {verb} {total} rows to {args.parquet_dir}")


if __name__ == "__main__":
    main()
