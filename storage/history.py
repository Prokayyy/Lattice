"""Read the full hot+cold telemetry history as one logical dataset.

The live scanner keeps only a short window in scanner.db (the "hot" DB); older
``signal_snapshots`` / ``token_candles`` rows are moved to scanner_archive.db
(the "warm" DB) by the retention/archive job, and the very oldest are rolled off
to Parquet (see ``tools/parquet_roll.py``).

``open_history()`` attaches hot + warm and exposes UNION-ALL views so analysis,
backtests and training can read across the hot/cold boundary transparently —
without caring where the line currently sits:

    from storage.history import open_history
    con = open_history()                       # read-only, both DBs attached
    rows = con.execute(
        "SELECT * FROM signal_snapshots_all "
        "WHERE token_address = ? ORDER BY timestamp",
        (addr,),
    ).fetchall()

For history older than the warm window, read the Parquet files under the
configured parquet dir (``SCANNER_ARCHIVE_PARQUET_DIR``) with pyarrow/pandas.

This module is intentionally dependency-free (stdlib only) so it works from any
virtualenv, including the analysis venv that has no scanner runtime deps.
"""

import sqlite3
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_HOT_DB = _REPO_ROOT / "scanner.db"

# Tables that get split across hot + warm by the retention job.
_SPLIT_TABLES = ("signal_snapshots", "token_candles")


def _quote(name):
    return '"' + str(name).replace('"', '""') + '"'


def archive_path_for(hot_db):
    """scanner_archive.db lives next to the *real* DB file (hot may be a symlink)."""
    return Path(hot_db).resolve().with_name("scanner_archive.db")


def _schema_has_table(con, schema, table):
    return con.execute(
        f"SELECT 1 FROM {schema}.sqlite_master "
        "WHERE type = 'table' AND name = ? LIMIT 1",
        (table,),
    ).fetchone() is not None


def _table_columns(con, schema, table):
    return [
        row[1]
        for row in con.execute(
            f"PRAGMA {schema}.table_info({_quote(table)})"
        ).fetchall()
    ]


def open_history(read_only=True, attach_archive=True, hot_db=None):
    """Open a connection that sees the full hot+warm telemetry history.

    Returns a ``sqlite3.Connection`` with ``row_factory = sqlite3.Row`` and two
    TEMP views — ``signal_snapshots_all`` and ``token_candles_all`` — each a
    ``UNION ALL`` of the hot table and its archive counterpart over the columns
    they share (so a schema that drifted via ``ALTER TABLE ADD COLUMN`` can't
    misalign the union). When the archive is absent the view falls back to the
    hot table alone, so callers can always query the ``*_all`` views.

    read_only defaults to True so analysis can never mutate live data.
    """

    hot = Path(hot_db) if hot_db else _DEFAULT_HOT_DB

    mode = "ro" if read_only else "rwc"
    con = sqlite3.connect(f"file:{hot}?mode={mode}", uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    # Keep TEMP objects (the UNION-ALL views below, and any query spill) off disk:
    # the root fs that holds the default temp dir can be full, which otherwise
    # fails view creation ("no such table" / "database or disk is full").
    con.execute("PRAGMA temp_store=MEMORY")

    archive = archive_path_for(hot)
    attached = False
    if attach_archive and archive.exists():
        con.execute(
            "ATTACH DATABASE ? AS archive",
            (f"file:{archive}?mode={mode}",),
        )
        attached = True

    for table in _SPLIT_TABLES:
        if not _schema_has_table(con, "main", table):
            continue

        view = _quote(table + "_all")

        if attached and _schema_has_table(con, "archive", table):
            archive_cols = set(_table_columns(con, "archive", table))
            shared = [
                col
                for col in _table_columns(con, "main", table)
                if col in archive_cols
            ]
            col_sql = ", ".join(_quote(col) for col in shared)
            con.execute(
                f"CREATE TEMP VIEW IF NOT EXISTS {view} AS "
                f"SELECT {col_sql} FROM main.{_quote(table)} "
                "UNION ALL "
                f"SELECT {col_sql} FROM archive.{_quote(table)}"
            )
        else:
            con.execute(
                f"CREATE TEMP VIEW IF NOT EXISTS {view} AS "
                f"SELECT * FROM main.{_quote(table)}"
            )

    return con


if __name__ == "__main__":
    # Quick smoke check: row counts across the hot/cold boundary.
    con = open_history()
    for table in _SPLIT_TABLES:
        view = table + "_all"
        try:
            total = con.execute(f"SELECT COUNT(*) FROM {view}").fetchone()[0]
            hot = con.execute(
                f"SELECT COUNT(*) FROM main.{table}"
            ).fetchone()[0]
            print(f"{view}: {total} rows total ({hot} hot, {total - hot} archived)")
        except sqlite3.Error as exc:
            print(f"{view}: {exc}")
