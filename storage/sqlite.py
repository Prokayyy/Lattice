import json
import sqlite3
import time
from pathlib import Path

from config import (
    LOCAL_RSI_TIMEFRAME_SECONDS,
    SCANNER_SQLITE_WAL_SIZE_LIMIT_BYTES
)

from trading.candles import (
    candle_bucket
)


DATABASE_NAME = "scanner.db"
ARCHIVE_DATABASE_NAME = str(
    Path(DATABASE_NAME).resolve().with_name("scanner_archive.db")
)

TELEMETRY_ARCHIVE_TARGETS = (
    ("signal_snapshots", "timestamp", ("id",)),
    ("token_candles", "bucket_start", (
        "token_address",
        "timeframe_seconds",
        "bucket_start"
    )),
)

ALERT_OUTCOME_WINDOWS_SECONDS = (
    300,
    900,
    3600,
    21600
)


def safe_float(
    value,
    default=0
):

    try:
        return float(value or default)
    except (TypeError, ValueError):
        return default


def ensure_column(
    db,
    table_name,
    column_name,
    column_sql
):

    existing = {
        row[1]
        for row in db.execute(
            f"PRAGMA table_info({table_name})"
        ).fetchall()
    }

    if column_name not in existing:
        db.execute(
            f"ALTER TABLE {table_name} "
            f"ADD COLUMN {column_name} {column_sql}"
        )


def quote_identifier(name):
    return '"' + str(name).replace('"', '""') + '"'


class ScannerStorage:

    def connect(self):

        db = sqlite3.connect(
            DATABASE_NAME,
            timeout=30
        )
        # WAL lets concurrent readers and a single writer proceed without the
        # whole-DB exclusive lock that DELETE journal mode takes — this is what
        # caused the "database is locked" errors under the scanner's concurrent
        # writes (RSI/VWAP/snapshot/candles). journal_mode=WAL is persistent on
        # the file (one-time conversion); busy_timeout/synchronous are per-conn.
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=10000")
        db.execute("PRAGMA synchronous=NORMAL")
        # Keep the WAL from ballooning: checkpoint every ~1000 pages and cap the
        # on-disk WAL so a stalled checkpoint can't grow it unbounded (it once
        # hit ~291 MB). journal_size_limit is in bytes and truncates the WAL
        # back down after each checkpoint.
        db.execute("PRAGMA wal_autocheckpoint=1000")
        db.execute(
            "PRAGMA journal_size_limit="
            f"{int(SCANNER_SQLITE_WAL_SIZE_LIMIT_BYTES)}"
        )
        return db

    def _table_columns(self, db, schema_name, table_name):
        return [
            {
                "name": row[1],
                "type": row[2] or "",
            }
            for row in db.execute(
                f"PRAGMA {schema_name}.table_info({quote_identifier(table_name)})"
            ).fetchall()
        ]

    def _ensure_archive_table(self, db, table_name, key_columns, cutoff_column):
        source_columns = self._table_columns(
            db,
            "main",
            table_name
        )

        if not source_columns:
            raise sqlite3.OperationalError(
                f"cannot archive missing table: {table_name}"
            )

        table_sql = ", ".join(
            (
                quote_identifier(column["name"])
                + (f" {column['type']}" if column["type"] else "")
            )
            for column in source_columns
        )

        db.execute(
            f"CREATE TABLE IF NOT EXISTS archive.{quote_identifier(table_name)} "
            f"({table_sql})"
        )

        archive_columns = {
            column["name"]
            for column in self._table_columns(
                db,
                "archive",
                table_name
            )
        }

        for column in source_columns:
            if column["name"] in archive_columns:
                continue

            db.execute(
                f"ALTER TABLE archive.{quote_identifier(table_name)} "
                f"ADD COLUMN {quote_identifier(column['name'])}"
                + (f" {column['type']}" if column["type"] else "")
            )

        key_sql = ", ".join(
            quote_identifier(column)
            for column in key_columns
        )
        key_slug = "_".join(key_columns)

        db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS "
            f"archive.{quote_identifier(f'idx_archive_{table_name}_{key_slug}')} "
            f"ON {quote_identifier(table_name)} ({key_sql})"
        )

        db.execute(
            "CREATE INDEX IF NOT EXISTS "
            f"archive.{quote_identifier(f'idx_archive_{table_name}_{cutoff_column}')} "
            f"ON {quote_identifier(table_name)} "
            f"({quote_identifier(cutoff_column)})"
        )

        return [
            column["name"]
            for column in source_columns
        ]

    @staticmethod
    def _retention_cutoff(retention_days, table):
        """Resolve a per-table retention cutoff (epoch seconds).

        ``retention_days`` may be an int (applied to every telemetry table)
        or a ``{table: days}`` dict for per-table windows. When a dict omits
        a table we return ``None`` so the caller skips it entirely — we never
        archive or prune a table we have no explicit policy for."""

        if isinstance(retention_days, dict):
            if table not in retention_days:
                return None
            days = retention_days[table]
        else:
            days = retention_days

        return time.time() - max(days, 0) * 86400

    def archive_telemetry(
        self,
        retention_days,
        archive_database_name=None,
        batch=20000
    ):
        """Move old high-volume telemetry into a cold archive DB.

        Rows are copied with INSERT OR IGNORE, then deleted from the hot DB only
        after a matching archive row exists. This keeps scanner.db small without
        losing backtest history if the process is interrupted mid-transfer.

        ``retention_days`` is an int (one window for every table) or a
        ``{table: days}`` dict for per-table windows.
        """

        archive_database_name = archive_database_name or ARCHIVE_DATABASE_NAME
        archived = {}

        with self.connect() as db:
            db.execute("PRAGMA busy_timeout=120000")
            db.execute(
                "ATTACH DATABASE ? AS archive",
                (archive_database_name,)
            )
            db.execute("PRAGMA archive.journal_mode=WAL")
            db.execute("PRAGMA archive.synchronous=NORMAL")
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS archive.archive_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at REAL
                )
                """
            )
            db.execute(
                """
                CREATE TEMP TABLE IF NOT EXISTS telemetry_archive_batch (
                    rowid INTEGER PRIMARY KEY
                )
                """
            )

            for table, cutoff_column, key_columns in TELEMETRY_ARCHIVE_TARGETS:
                cutoff = self._retention_cutoff(retention_days, table)
                if cutoff is None:
                    continue
                db.execute(
                    "CREATE INDEX IF NOT EXISTS "
                    f"{quote_identifier(f'idx_{table}_{cutoff_column}')} "
                    f"ON {quote_identifier(table)} "
                    f"({quote_identifier(cutoff_column)})"
                )
                columns = self._ensure_archive_table(
                    db,
                    table,
                    key_columns,
                    cutoff_column
                )
                column_sql = ", ".join(
                    quote_identifier(column)
                    for column in columns
                )
                source_sql = ", ".join(
                    f"src.{quote_identifier(column)}"
                    for column in columns
                )
                table_stats = {
                    "archived": 0,
                    "deleted": 0,
                }

                while True:
                    db.execute("DELETE FROM telemetry_archive_batch")
                    db.execute(
                        "INSERT INTO telemetry_archive_batch (rowid) "
                        f"SELECT rowid FROM main.{quote_identifier(table)} "
                        f"WHERE {quote_identifier(cutoff_column)} < ? "
                        f"ORDER BY {quote_identifier(cutoff_column)} "
                        "LIMIT ?",
                        (cutoff, batch)
                    )
                    selected = db.execute(
                        "SELECT COUNT(*) FROM telemetry_archive_batch"
                    ).fetchone()[0]

                    if selected <= 0:
                        db.commit()
                        break

                    insert_cursor = db.execute(
                        f"INSERT OR IGNORE INTO archive.{quote_identifier(table)} "
                        f"({column_sql}) "
                        f"SELECT {source_sql} "
                        f"FROM main.{quote_identifier(table)} AS src "
                        "JOIN telemetry_archive_batch AS batch "
                        "ON src.rowid = batch.rowid"
                    )
                    table_stats["archived"] += max(
                        insert_cursor.rowcount,
                        0
                    )

                    # The successful INSERT OR IGNORE above guarantees every
                    # selected row is now present in the archive: new rows were
                    # inserted, while ignored rows already matched the archive
                    # table's unique key. Deleting by the selected hot rowids is
                    # much faster than a cross-database correlated EXISTS over a
                    # multi-million-row backlog.
                    delete_cursor = db.execute(
                        f"DELETE FROM main.{quote_identifier(table)} "
                        "WHERE rowid IN ("
                        "SELECT rowid FROM telemetry_archive_batch"
                        ")"
                    )
                    table_stats["deleted"] += max(
                        delete_cursor.rowcount,
                        0
                    )
                    db.commit()

                archived[table] = table_stats

            run_at = time.time()
            db.execute(
                """
                INSERT INTO archive.archive_metadata (key, value, updated_at)
                VALUES ('last_archive_run', ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (str(run_at), run_at)
            )
            db.commit()
            db.execute("DETACH DATABASE archive")

        return archived

    def prune_telemetry(self, retention_days, batch=20000):
        """Delete telemetry rows older than retention_days from the
        high-volume analysis tables. Deletes in small batches (committing
        each) so a writer lock is never held long — safe to run while the
        scanner is live under WAL. Returns {table: rows_deleted}.

        ``retention_days`` is an int (one window for every table) or a
        ``{table: days}`` dict for per-table windows."""

        deleted = {}

        with self.connect() as db:
            for table, col, _key_columns in TELEMETRY_ARCHIVE_TARGETS:
                cutoff = self._retention_cutoff(retention_days, table)
                if cutoff is None:
                    continue
                total = 0
                while True:
                    cur = db.execute(
                        f"DELETE FROM {table} WHERE rowid IN ("
                        f"SELECT rowid FROM {table} WHERE {col} < ? "
                        f"LIMIT ?)",
                        (cutoff, batch),
                    )
                    db.commit()
                    if cur.rowcount <= 0:
                        break
                    total += cur.rowcount
                deleted[table] = total

        return deleted

    async def initialize(self):

        # Legacy tables (alerts, candidates, token_history) are no longer
        # created: the organic-revival alert path that wrote them was retired
        # and their writers removed 2026-06-11. Existing empty tables in old
        # DBs are harmless leftovers.
        with self.connect() as db:

            db.execute(
                """
                CREATE TABLE IF NOT EXISTS ignition_alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    token_address TEXT,
                    symbol TEXT,
                    pair_address TEXT,
                    chain_name TEXT,
                    alert_route TEXT,
                    quality_tag TEXT,
                    score INTEGER,
                    raw_score INTEGER,
                    penalty REAL,
                    alert_price REAL,
                    alert_fdv REAL,
                    alert_liquidity REAL,
                    alert_pressure REAL,
                    alert_impulse REAL,
                    alert_timestamp REAL,
                    delivered_chat_ids TEXT,
                    delivery_count INTEGER,
                    last_price REAL,
                    last_fdv REAL,
                    last_liquidity REAL,
                    last_pressure REAL,
                    last_impulse REAL,
                    last_timestamp REAL,
                    max_price REAL,
                    max_multiple REAL,
                    min_price REAL,
                    min_multiple REAL,
                    status TEXT,
                    note TEXT
                )
                """
            )

            db.execute(
                """
                CREATE INDEX IF NOT EXISTS
                idx_ignition_alerts_token_time
                ON ignition_alerts (
                    token_address,
                    alert_timestamp
                )
                """
            )

            ensure_column(
                db,
                "ignition_alerts",
                "pair_address",
                "TEXT"
            )

            db.execute(
                """
                CREATE TABLE IF NOT EXISTS alert_outcomes (
                    alert_id INTEGER,
                    token_address TEXT,
                    symbol TEXT,
                    chain_name TEXT,
                    alert_route TEXT,
                    quality_tag TEXT,
                    alert_timestamp REAL,
                    window_seconds INTEGER,
                    window_label TEXT,
                    due_timestamp REAL,
                    alert_price REAL,
                    close_price REAL,
                    close_multiple REAL,
                    max_price REAL,
                    max_multiple REAL,
                    min_price REAL,
                    min_multiple REAL,
                    time_to_peak_seconds REAL,
                    alert_liquidity REAL,
                    close_liquidity REAL,
                    min_liquidity REAL,
                    liquidity_change_pct REAL,
                    snapshot_count INTEGER,
                    complete INTEGER,
                    updated_at REAL,
                    PRIMARY KEY (
                        alert_id,
                        window_seconds
                    )
                )
                """
            )

            db.execute(
                """
                CREATE INDEX IF NOT EXISTS
                idx_alert_outcomes_route_window
                ON alert_outcomes (
                    alert_route,
                    window_seconds,
                    complete
                )
                """
            )

            db.execute(
                """
                CREATE INDEX IF NOT EXISTS
                idx_alert_outcomes_token_time
                ON alert_outcomes (
                    token_address,
                    alert_timestamp
                )
                """
            )

            # Control arm + uniform training rows: one event per alert-eligible
            # token per 24h, whether or not an alert was actually delivered
            # (alerted flag set by record_ignition_alert). Same alert-time
            # feature surface as ignition_alerts plus the post-analysis feature
            # families (token age, market breadth; holder concentration is a
            # nullable placeholder until a cheap source exists). Labels come
            # from alert_candle_labels (fixed-horizon, candle-based).
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS candidate_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    token_address TEXT,
                    symbol TEXT,
                    chain_name TEXT,
                    pair_address TEXT,
                    timestamp REAL,
                    price REAL,
                    fdv REAL,
                    liquidity REAL,
                    score INTEGER,
                    raw_score INTEGER,
                    penalty REAL,
                    pressure REAL,
                    impulse REAL,
                    volume_5m REAL,
                    volume_1h REAL,
                    volume_liquidity_ratio REAL,
                    buy_sell_ratio REAL,
                    h1_volume_liquidity_ratio REAL,
                    h1_buy_sell_ratio REAL,
                    price_change_5m REAL,
                    price_change_1h REAL,
                    momentum_score INTEGER,
                    local_rsi REAL,
                    alert_route TEXT,
                    quality_tag TEXT,
                    lifecycle TEXT,
                    source TEXT,
                    source_family TEXT,
                    novelty_factor REAL,
                    adjusted_score REAL,
                    data_completeness_score REAL,
                    evidence_bucket TEXT,
                    evidence_factor REAL,
                    bad_evidence_penalty REAL,
                    data_missing TEXT,
                    risk_flags TEXT,
                    token_age_seconds REAL,
                    breadth_eligible_30m INTEGER,
                    holder_top10_pct REAL,
                    alerted INTEGER DEFAULT 0
                )
                """
            )

            db.execute(
                """
                CREATE INDEX IF NOT EXISTS
                idx_candidate_events_token_time
                ON candidate_events (
                    token_address,
                    timestamp
                )
                """
            )

            db.execute(
                """
                CREATE INDEX IF NOT EXISTS
                idx_candidate_events_time
                ON candidate_events (
                    timestamp
                )
                """
            )

            # Two-stage confirmation (shadow): evaluated ~15 min after the
            # event from stored snapshots; components persisted so the policy
            # threshold can be re-derived from data before ever gating.
            for col, ctype in (
                ("confirm_evaluated_at", "REAL"),
                ("confirmed", "INTEGER"),
                ("confirm_price_multiple", "REAL"),
                ("confirm_min_multiple", "REAL"),
                ("confirm_flow_ratio", "REAL"),
                ("confirm_vol_ratio", "REAL"),
                ("confirm_snapshot_count", "INTEGER"),
                # Shadow probability from the blessed runner model (NULL until
                # a model passes the deployment bar and sklearn is installed).
                ("model_prob", "REAL"),
                # GMGN smart-money holders enrichment (sources/gmgn.py,
                # data-only; the single GMGN skill in use). Filled
                # asynchronously just after an ELIGIBLE candidate row is
                # written; NULL when GMGN was unavailable. (Older gmgn_*
                # columns from the broader first iteration may exist in the
                # live DB; they stay NULL and harmless.)
                ("gmgn_at", "REAL"),
                ("gmgn_smart_money", "INTEGER"),
                ("gmgn_smart_share_pct", "REAL"),
                ("gmgn_smart_usd", "REAL"),
                ("gmgn_smart_profit_n", "INTEGER"),
                ("gmgn_smart_fresh_n", "INTEGER"),
                ("gmgn_smart_suspicious_n", "INTEGER"),
                ("gmgn_raw", "TEXT"),
                # OpenTwitter CA-mention enrichment (sources/opentwitter.py);
                # inert until TWITTER_TOKEN is configured.
                ("tw_at", "REAL"),
                ("tw_mentions", "INTEGER"),
                ("tw_authors", "INTEGER"),
                ("tw_top_followers", "INTEGER"),
                ("tw_first_mention_ts", "REAL"),
                ("tw_raw", "TEXT"),
                ("source", "TEXT"),
                ("source_family", "TEXT"),
                ("novelty_factor", "REAL"),
                ("adjusted_score", "REAL"),
                ("data_completeness_score", "REAL"),
                ("evidence_bucket", "TEXT"),
                ("evidence_factor", "REAL"),
                ("bad_evidence_penalty", "REAL"),
                ("data_missing", "TEXT"),
            ):
                ensure_column(
                    db,
                    "candidate_events",
                    col,
                    ctype
                )

            # Fixed-horizon candle labels — the canonical training target.
            # ignition_alerts.max_multiple has a variable tracking horizon
            # (5h-358h observed) and must never be used as a label; these rows
            # are computed from token_candles once each horizon has elapsed.
            # subject_type: 'alert' (ignition_alerts.id) or 'candidate'
            # (candidate_events.id).
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS alert_candle_labels (
                    subject_type TEXT,
                    subject_id INTEGER,
                    token_address TEXT,
                    base_timestamp REAL,
                    base_price REAL,
                    h6_max_multiple REAL,
                    h6_close_multiple REAL,
                    h6_min_multiple REAL,
                    h6_candle_count INTEGER,
                    h24_max_multiple REAL,
                    h24_candle_count INTEGER,
                    computed_at REAL,
                    PRIMARY KEY (
                        subject_type,
                        subject_id
                    )
                )
                """
            )

            db.execute(
                """
                CREATE INDEX IF NOT EXISTS
                idx_alert_candle_labels_token
                ON alert_candle_labels (
                    token_address,
                    base_timestamp
                )
                """
            )

            db.execute(
                """
                CREATE TABLE IF NOT EXISTS llm_pattern_reports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    provider TEXT,
                    model TEXT,
                    lookback_hours REAL,
                    alert_count INTEGER,
                    report_text TEXT,
                    raw_payload TEXT,
                    created_at REAL
                )
                """
            )

            db.execute(
                """
                CREATE INDEX IF NOT EXISTS
                idx_llm_pattern_reports_created_at
                ON llm_pattern_reports (
                    created_at
                )
                """
            )

            db.execute(
                """
                CREATE TABLE IF NOT EXISTS news_items (
                    item_key TEXT PRIMARY KEY,
                    source TEXT,
                    title TEXT,
                    url TEXT,
                    summary TEXT,
                    published_at REAL,
                    fetched_at REAL
                )
                """
            )

            db.execute(
                """
                CREATE INDEX IF NOT EXISTS
                idx_news_items_published_at
                ON news_items (
                    published_at
                )
                """
            )

            db.execute(
                """
                CREATE TABLE IF NOT EXISTS news_signal_matches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    token_address TEXT,
                    symbol TEXT,
                    chain_name TEXT,
                    item_key TEXT,
                    source TEXT,
                    title TEXT,
                    url TEXT,
                    matched_terms TEXT,
                    score INTEGER,
                    alert_route TEXT,
                    quality_tag TEXT,
                    alerted INTEGER,
                    timestamp REAL,
                    UNIQUE (
                        token_address,
                        item_key,
                        alert_route
                    )
                )
                """
            )

            db.execute(
                """
                CREATE INDEX IF NOT EXISTS
                idx_news_signal_matches_token_time
                ON news_signal_matches (
                    token_address,
                    timestamp
                )
                """
            )

            db.execute(
                """
                CREATE TABLE IF NOT EXISTS narrative_signal_matches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    token_address TEXT,
                    symbol TEXT,
                    chain_name TEXT,
                    narrative_type TEXT,
                    narrative_score INTEGER,
                    ignition_score INTEGER,
                    sources TEXT,
                    matched_terms TEXT,
                    alert_route TEXT,
                    quality_tag TEXT,
                    alerted INTEGER,
                    timestamp REAL,
                    UNIQUE (
                        token_address,
                        narrative_type,
                        alert_route
                    )
                )
                """
            )

            db.execute(
                """
                CREATE INDEX IF NOT EXISTS
                idx_narrative_signal_matches_token_time
                ON narrative_signal_matches (
                    token_address,
                    timestamp
                )
                """
            )

            db.execute(
                """
                CREATE TABLE IF NOT EXISTS signal_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    token_address TEXT,
                    symbol TEXT,
                    pair_address TEXT,
                    chain_name TEXT,
                    lifecycle TEXT,
                    price REAL,
                    liquidity REAL,
                    raw_liquidity REAL,
                    raw_base_reserve REAL,
                    raw_quote_reserve REAL,
                    fdv REAL,
                    migration_fdv REAL,
                    migration_distance_usd REAL,
                    migration_distance_pct REAL,
                    migration_fdv_source TEXT,
                    volume_5m REAL,
                    volume_1h REAL,
                    buy_volume_5m REAL,
                    sell_volume_5m REAL,
                    buy_volume_1h REAL,
                    sell_volume_1h REAL,
                    buys_5m INTEGER,
                    sells_5m INTEGER,
                    buys_1h INTEGER,
                    sells_1h INTEGER,
                    txns_5m INTEGER,
                    txns_1h INTEGER,
                    price_change_5m REAL,
                    price_change_1h REAL,
                    price_change_6h REAL,
                    price_change_24h REAL,
                    pressure REAL,
                    impulse REAL,
                    volume_liquidity_ratio REAL,
                    buy_sell_ratio REAL,
                    h1_volume_liquidity_ratio REAL,
                    h1_buy_sell_ratio REAL,
                    score INTEGER,
                    raw_score INTEGER,
                    penalty REAL,
                    quality_tag TEXT,
                    alert_route TEXT,
                    alert_eligible INTEGER,
                    liquidity_lock_checked INTEGER,
                    liquidity_lock_required INTEGER,
                    liquidity_lock_locked INTEGER,
                    liquidity_lock_locked_percent REAL,
                    liquidity_lock_source TEXT,
                    liquidity_lock_reason TEXT,
                    local_rsi_ready INTEGER,
                    local_rsi REAL,
                    local_rsi_ema REAL,
                    local_rsi_bullish INTEGER,
                    local_rsi_bearish INTEGER,
                    local_rsi_crossed_up INTEGER,
                    local_rsi_crossed_down INTEGER,
                    local_rsi_entry_ok INTEGER,
                    local_rsi_reason TEXT,
                    local_rsi_candle_count INTEGER,
                    local_rsi_timeframe_seconds INTEGER,
                    source TEXT,
                    source_family TEXT,
                    novelty_factor REAL,
                    adjusted_score REAL,
                    data_completeness_score REAL,
                    evidence_bucket TEXT,
                    evidence_factor REAL,
                    bad_evidence_penalty REAL,
                    data_missing TEXT,
                    missing TEXT,
                    risk_flags TEXT,
                    experimental_features TEXT,
                    momentum_score INTEGER,
                    timestamp REAL
                )
                """
            )

            ensure_column(
                db,
                "signal_snapshots",
                "migration_fdv",
                "REAL"
            )

            ensure_column(
                db,
                "signal_snapshots",
                "migration_distance_usd",
                "REAL"
            )

            ensure_column(
                db,
                "signal_snapshots",
                "migration_distance_pct",
                "REAL"
            )

            ensure_column(
                db,
                "signal_snapshots",
                "migration_fdv_source",
                "TEXT"
            )

            ensure_column(
                db,
                "signal_snapshots",
                "raw_base_reserve",
                "REAL"
            )

            ensure_column(
                db,
                "signal_snapshots",
                "raw_quote_reserve",
                "REAL"
            )

            ensure_column(
                db,
                "signal_snapshots",
                "buy_volume_5m",
                "REAL"
            )

            ensure_column(
                db,
                "signal_snapshots",
                "sell_volume_5m",
                "REAL"
            )

            ensure_column(
                db,
                "signal_snapshots",
                "buy_volume_1h",
                "REAL"
            )

            ensure_column(
                db,
                "signal_snapshots",
                "sell_volume_1h",
                "REAL"
            )

            ensure_column(
                db,
                "signal_snapshots",
                "price_change_24h",
                "REAL"
            )

            ensure_column(
                db,
                "signal_snapshots",
                "experimental_features",
                "TEXT"
            )

            ensure_column(
                db,
                "signal_snapshots",
                "momentum_score",
                "INTEGER"
            )

            ensure_column(
                db,
                "signal_snapshots",
                "liquidity_lock_checked",
                "INTEGER"
            )

            ensure_column(
                db,
                "signal_snapshots",
                "liquidity_lock_required",
                "INTEGER"
            )

            ensure_column(
                db,
                "signal_snapshots",
                "liquidity_lock_locked",
                "INTEGER"
            )

            ensure_column(
                db,
                "signal_snapshots",
                "liquidity_lock_locked_percent",
                "REAL"
            )

            ensure_column(
                db,
                "signal_snapshots",
                "liquidity_lock_source",
                "TEXT"
            )

            ensure_column(
                db,
                "signal_snapshots",
                "liquidity_lock_reason",
                "TEXT"
            )

            for column_name, column_sql in (
                ("local_rsi_ready", "INTEGER"),
                ("local_rsi", "REAL"),
                ("local_rsi_ema", "REAL"),
                ("local_rsi_bullish", "INTEGER"),
                ("local_rsi_bearish", "INTEGER"),
                ("local_rsi_crossed_up", "INTEGER"),
                ("local_rsi_crossed_down", "INTEGER"),
                ("local_rsi_entry_ok", "INTEGER"),
                ("local_rsi_reason", "TEXT"),
                ("local_rsi_candle_count", "INTEGER"),
                ("local_rsi_timeframe_seconds", "INTEGER"),
                ("source", "TEXT"),
                ("source_family", "TEXT"),
                ("novelty_factor", "REAL"),
                ("adjusted_score", "REAL"),
                ("data_completeness_score", "REAL"),
                ("evidence_bucket", "TEXT"),
                ("evidence_factor", "REAL"),
                ("bad_evidence_penalty", "REAL"),
                ("data_missing", "TEXT")
            ):
                ensure_column(
                    db,
                    "signal_snapshots",
                    column_name,
                    column_sql
                )

            db.execute(
                """
                CREATE INDEX IF NOT EXISTS
                idx_signal_snapshots_token_time
                ON signal_snapshots (
                    token_address,
                    timestamp
                )
                """
            )

            db.execute(
                """
                CREATE TABLE IF NOT EXISTS token_candles (
                    token_address TEXT,
                    symbol TEXT,
                    pair_address TEXT,
                    chain_name TEXT,
                    timeframe_seconds INTEGER,
                    bucket_start REAL,
                    open REAL,
                    high REAL,
                    low REAL,
                    close REAL,
                    observations INTEGER,
                    first_observed_at REAL,
                    last_observed_at REAL,
                    price_native REAL,
                    volume_5m REAL,
                    volume_1h REAL,
                    liquidity REAL,
                    fdv REAL,
                    market_cap REAL,
                    source TEXT,
                    PRIMARY KEY (
                        token_address,
                        timeframe_seconds,
                        bucket_start
                    )
                )
                """
            )

            for column_name, column_sql in (
                ("price_native", "REAL"),
                ("volume_1h", "REAL"),
                ("fdv", "REAL"),
                ("market_cap", "REAL"),
                ("source", "TEXT")
            ):
                ensure_column(
                    db,
                    "token_candles",
                    column_name,
                    column_sql
                )

            db.execute(
                """
                CREATE INDEX IF NOT EXISTS
                idx_token_candles_token_time
                ON token_candles (
                    token_address,
                    timeframe_seconds,
                    bucket_start
                )
                """
            )

            db.execute(
                """
                CREATE INDEX IF NOT EXISTS
                idx_signal_snapshots_time
                ON signal_snapshots (
                    timestamp
                )
                """
            )

    async def record_news_items(
        self,
        items
    ):

        if not items:
            return 0

        inserted = 0

        with self.connect() as db:
            for item in items:
                cursor = db.execute(
                    """
                    INSERT OR IGNORE INTO news_items (
                        item_key,
                        source,
                        title,
                        url,
                        summary,
                        published_at,
                        fetched_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item.get("item_key"),
                        item.get("source"),
                        item.get("title"),
                        item.get("url"),
                        item.get("summary"),
                        item.get("published_at"),
                        item.get("fetched_at")
                    )
                )
                inserted += max(cursor.rowcount, 0)

        return inserted

    async def load_recent_news_items(
        self,
        since_timestamp,
        limit=250
    ):

        with self.connect() as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                """
                SELECT *
                FROM news_items
                WHERE published_at >= ?
                ORDER BY published_at DESC
                LIMIT ?
                """,
                (
                    since_timestamp,
                    limit
                )
            ).fetchall()

        return [
            dict(row)
            for row in rows
        ]

    async def record_news_signal_match(
        self,
        metrics,
        news_item,
        matched_terms,
        score,
        details,
        alerted,
        timestamp
    ):

        with self.connect() as db:
            cursor = db.execute(
                """
                INSERT INTO news_signal_matches (
                    token_address,
                    symbol,
                    chain_name,
                    item_key,
                    source,
                    title,
                    url,
                    matched_terms,
                    score,
                    alert_route,
                    quality_tag,
                    alerted,
                    timestamp
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (
                    token_address,
                    item_key,
                    alert_route
                )
                DO UPDATE SET
                    score = excluded.score,
                    quality_tag = excluded.quality_tag,
                    alerted = MAX(
                        news_signal_matches.alerted,
                        excluded.alerted
                    ),
                    timestamp = excluded.timestamp
                """,
                (
                    metrics.address,
                    metrics.symbol,
                    metrics.chain,
                    news_item.get("item_key"),
                    news_item.get("source"),
                    news_item.get("title"),
                    news_item.get("url"),
                    json.dumps(matched_terms or []),
                    score,
                    details.get("alert_route", "none"),
                    details.get("quality_tag", "standard"),
                    1 if alerted else 0,
                    timestamp
                )
            )

        return cursor.rowcount > 0

    async def record_narrative_signal_match(
        self,
        metrics,
        narrative,
        ignition_score,
        details,
        alerted,
        timestamp
    ):

        if not narrative or not narrative.get("detected"):
            return False

        with self.connect() as db:
            cursor = db.execute(
                """
                INSERT INTO narrative_signal_matches (
                    token_address,
                    symbol,
                    chain_name,
                    narrative_type,
                    narrative_score,
                    ignition_score,
                    sources,
                    matched_terms,
                    alert_route,
                    quality_tag,
                    alerted,
                    timestamp
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (
                    token_address,
                    narrative_type,
                    alert_route
                )
                DO UPDATE SET
                    narrative_score = excluded.narrative_score,
                    ignition_score = excluded.ignition_score,
                    sources = excluded.sources,
                    matched_terms = excluded.matched_terms,
                    quality_tag = excluded.quality_tag,
                    alerted = MAX(
                        narrative_signal_matches.alerted,
                        excluded.alerted
                    ),
                    timestamp = excluded.timestamp
                """,
                (
                    metrics.address,
                    metrics.symbol,
                    metrics.chain,
                    narrative.get(
                        "narrative_type",
                        "real_world_narrative"
                    ),
                    int(narrative.get("score", 0) or 0),
                    int(ignition_score or 0),
                    json.dumps(narrative.get("sources", [])),
                    json.dumps(narrative.get("matched_terms", [])),
                    details.get("alert_route", "none"),
                    details.get("quality_tag", "standard"),
                    1 if alerted else 0,
                    timestamp
                )
            )

        return cursor.rowcount > 0

    async def record_ignition_alert(
        self,
        metrics,
        score,
        details,
        alert_timestamp,
        snapshot=None,
        delivered_chat_ids=None,
        delivery_count=0,
        note=""
    ):

        snapshot = snapshot or {}

        alert_price = safe_float(
            metrics.price,
            0
        )
        alert_fdv = safe_float(
            metrics.fdv,
            0
        )
        alert_liquidity = safe_float(
            metrics.liquidity,
            0
        )
        alert_impulse = safe_float(
            snapshot.get(
                "impulse",
                details.get("price_jump")
            ),
            0
        )
        alert_pressure = safe_float(
            snapshot.get(
                "pressure",
                details.get("pressure")
            ),
            0
        )
        alert_multiple = 1

        with self.connect() as db:

            db.execute(
                """
                INSERT INTO ignition_alerts (
                    token_address,
                    symbol,
                    pair_address,
                    chain_name,
                    alert_route,
                    quality_tag,
                    score,
                    raw_score,
                    penalty,
                    alert_price,
                    alert_fdv,
                    alert_liquidity,
                    alert_pressure,
                    alert_impulse,
                    alert_timestamp,
                    delivered_chat_ids,
                    delivery_count,
                    last_price,
                    last_fdv,
                    last_liquidity,
                    last_pressure,
                    last_impulse,
                    last_timestamp,
                    max_price,
                    max_multiple,
                    min_price,
                    min_multiple,
                    status,
                    note
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    metrics.address,
                    metrics.symbol,
                    metrics.pair_address,
                    metrics.chain,
                    details.get("alert_route"),
                    details.get("quality_tag"),
                    score,
                    details.get("raw_score"),
                    details.get("penalty"),
                    alert_price,
                    alert_fdv,
                    alert_liquidity,
                    alert_pressure,
                    alert_impulse,
                    alert_timestamp,
                    json.dumps(list(delivered_chat_ids or [])),
                    int(delivery_count or 0),
                    alert_price,
                    alert_fdv,
                    alert_liquidity,
                    alert_pressure,
                    alert_impulse,
                    alert_timestamp,
                    alert_price,
                    alert_multiple,
                    alert_price,
                    alert_multiple,
                    "open",
                    note or ""
                )
            )

            # Mark the control-arm row for this token as alerted so training
            # can split alerted vs eligible-but-skipped candidates cleanly.
            db.execute(
                """
                UPDATE candidate_events
                SET alerted = 1
                WHERE id = (
                    SELECT id
                    FROM candidate_events
                    WHERE token_address = ?
                        AND timestamp >= ?
                    ORDER BY timestamp DESC
                    LIMIT 1
                )
                """,
                (
                    metrics.address,
                    safe_float(alert_timestamp, 0) - 24 * 3600
                )
            )

    async def update_ignition_alerts_for_snapshot(
        self,
        metrics,
        snapshot,
        timestamp
    ):

        current_price = safe_float(
            metrics.price,
            0
        )
        current_fdv = safe_float(
            metrics.fdv,
            0
        )
        current_liquidity = safe_float(
            metrics.liquidity,
            0
        )
        current_pressure = safe_float(
            snapshot.get("pressure"),
            0
        )
        current_impulse = safe_float(
            snapshot.get("impulse"),
            0
        )

        with self.connect() as db:

            db.row_factory = sqlite3.Row
            rows = db.execute(
                """
                SELECT id, alert_price
                FROM ignition_alerts
                WHERE token_address = ?
                    AND status = 'open'
                    AND alert_timestamp <= ?
                """,
                (
                    metrics.address,
                    timestamp
                )
            ).fetchall()

            for row in rows:

                alert_price = safe_float(
                    row["alert_price"],
                    0
                )

                if alert_price <= 0:
                    continue

                current_multiple = (
                    current_price / alert_price
                )

                db.execute(
                    """
                    UPDATE ignition_alerts
                    SET
                        last_price = ?,
                        last_fdv = ?,
                        last_liquidity = ?,
                        last_pressure = ?,
                        last_impulse = ?,
                        last_timestamp = ?,
                        max_price = CASE
                            WHEN ? > max_price THEN ?
                            ELSE max_price
                        END,
                        max_multiple = CASE
                            WHEN ? > max_multiple THEN ?
                            ELSE max_multiple
                        END,
                        min_price = CASE
                            WHEN ? < min_price THEN ?
                            ELSE min_price
                        END,
                        min_multiple = CASE
                            WHEN ? < min_multiple THEN ?
                            ELSE min_multiple
                        END
                    WHERE id = ?
                    """,
                    (
                        current_price,
                        current_fdv,
                        current_liquidity,
                        current_pressure,
                        current_impulse,
                        timestamp,
                        current_price,
                        current_price,
                        current_multiple,
                        current_multiple,
                        current_price,
                        current_price,
                        current_multiple,
                        current_multiple,
                        row["id"]
                    )
                )

    async def update_ignition_alert_live_price(
        self,
        token_address,
        price,
        fdv=0,
        liquidity=0,
        timestamp=None
    ):

        current_price = safe_float(
            price,
            0
        )

        if current_price <= 0:
            return 0

        current_fdv = safe_float(
            fdv,
            0
        )
        current_liquidity = safe_float(
            liquidity,
            0
        )
        timestamp = safe_float(
            timestamp,
            time.time()
        )

        with self.connect() as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                """
                SELECT id, alert_price
                FROM ignition_alerts
                WHERE token_address = ?
                    AND status = 'open'
                    AND alert_timestamp <= ?
                """,
                (
                    token_address,
                    timestamp
                )
            ).fetchall()

            updated = 0

            for row in rows:
                alert_price = safe_float(
                    row["alert_price"],
                    0
                )

                if alert_price <= 0:
                    continue

                current_multiple = current_price / alert_price

                db.execute(
                    """
                    UPDATE ignition_alerts
                    SET
                        last_price = ?,
                        last_fdv = CASE
                            WHEN ? > 0 THEN ?
                            ELSE last_fdv
                        END,
                        last_liquidity = CASE
                            WHEN ? > 0 THEN ?
                            ELSE last_liquidity
                        END,
                        last_timestamp = ?,
                        max_price = CASE
                            WHEN ? > max_price THEN ?
                            ELSE max_price
                        END,
                        max_multiple = CASE
                            WHEN ? > max_multiple THEN ?
                            ELSE max_multiple
                        END,
                        min_price = CASE
                            WHEN ? < min_price THEN ?
                            ELSE min_price
                        END,
                        min_multiple = CASE
                            WHEN ? < min_multiple THEN ?
                            ELSE min_multiple
                        END
                    WHERE id = ?
                    """,
                    (
                        current_price,
                        current_fdv,
                        current_fdv,
                        current_liquidity,
                        current_liquidity,
                        timestamp,
                        current_price,
                        current_price,
                        current_multiple,
                        current_multiple,
                        current_price,
                        current_price,
                        current_multiple,
                        current_multiple,
                        row["id"]
                    )
                )
                updated += 1

        return updated

    def alert_outcome_window_label(
        self,
        window_seconds
    ):

        if window_seconds % 3600 == 0:
            return f"{window_seconds // 3600}h"

        if window_seconds % 60 == 0:
            return f"{window_seconds // 60}m"

        return f"{window_seconds}s"

    def build_alert_outcome(
        self,
        alert,
        snapshots,
        window_seconds,
        observed_until=None
    ):

        alert_timestamp = safe_float(
            alert.get("alert_timestamp"),
            0
        )
        alert_price = safe_float(
            alert.get("alert_price"),
            0
        )
        alert_liquidity = safe_float(
            alert.get("alert_liquidity"),
            0
        )
        due_timestamp = alert_timestamp + window_seconds
        observed_until = safe_float(
            observed_until,
            time.time()
        )
        complete = (
            observed_until >= due_timestamp
        )

        close_price = alert_price
        max_price = alert_price
        min_price = alert_price
        peak_timestamp = alert_timestamp
        close_liquidity = alert_liquidity
        min_liquidity = alert_liquidity
        snapshot_count = 0

        for snapshot in snapshots:
            snapshot_price = safe_float(
                snapshot.get("price"),
                0
            )

            if snapshot_price <= 0:
                continue

            snapshot_count += 1
            close_price = snapshot_price

            snapshot_timestamp = safe_float(
                snapshot.get("timestamp"),
                alert_timestamp
            )

            if snapshot_price > max_price:
                max_price = snapshot_price
                peak_timestamp = snapshot_timestamp

            if min_price <= 0 or snapshot_price < min_price:
                min_price = snapshot_price

            snapshot_liquidity = safe_float(
                snapshot.get("liquidity"),
                0
            )

            if snapshot_liquidity > 0:
                close_liquidity = snapshot_liquidity

                if (
                    min_liquidity <= 0
                    or snapshot_liquidity < min_liquidity
                ):
                    min_liquidity = snapshot_liquidity

        return {
            "alert_id": alert.get("id"),
            "token_address": alert.get("token_address"),
            "symbol": alert.get("symbol"),
            "chain_name": alert.get("chain_name"),
            "alert_route": alert.get("alert_route") or "none",
            "quality_tag": alert.get("quality_tag") or "none",
            "alert_timestamp": alert_timestamp,
            "window_seconds": window_seconds,
            "window_label": self.alert_outcome_window_label(
                window_seconds
            ),
            "due_timestamp": due_timestamp,
            "alert_price": alert_price,
            "close_price": close_price,
            "close_multiple": (
                close_price / alert_price
                if alert_price > 0
                else 0
            ),
            "max_price": max_price,
            "max_multiple": (
                max_price / alert_price
                if alert_price > 0
                else 0
            ),
            "min_price": min_price,
            "min_multiple": (
                min_price / alert_price
                if alert_price > 0
                else 0
            ),
            "time_to_peak_seconds": max(
                peak_timestamp - alert_timestamp,
                0
            ),
            "alert_liquidity": alert_liquidity,
            "close_liquidity": close_liquidity,
            "min_liquidity": min_liquidity,
            "liquidity_change_pct": (
                (
                    close_liquidity
                    - alert_liquidity
                )
                / alert_liquidity
                if alert_liquidity > 0
                else 0
            ),
            "snapshot_count": snapshot_count,
            "complete": 1 if complete else 0,
            "updated_at": time.time()
        }

    def load_alert_window_snapshots(
        self,
        db,
        alert,
        window_seconds,
        observed_until=None
    ):

        alert_timestamp = safe_float(
            alert.get("alert_timestamp"),
            0
        )
        due_timestamp = alert_timestamp + window_seconds
        until = min(
            due_timestamp,
            safe_float(observed_until, time.time())
        )

        if alert_timestamp <= 0 or until < alert_timestamp:
            return []

        rows = db.execute(
            """
            SELECT
                price,
                liquidity,
                fdv,
                pressure,
                impulse,
                timestamp
            FROM signal_snapshots
            WHERE token_address = ?
                AND timestamp >= ?
                AND timestamp <= ?
                AND price > 0
            ORDER BY timestamp ASC,
                id ASC
            """,
            (
                alert.get("token_address"),
                alert_timestamp,
                until
            )
        ).fetchall()

        return [
            dict(row)
            for row in rows
        ]

    def upsert_alert_outcome(
        self,
        db,
        outcome
    ):

        db.execute(
            """
            INSERT INTO alert_outcomes (
                alert_id,
                token_address,
                symbol,
                chain_name,
                alert_route,
                quality_tag,
                alert_timestamp,
                window_seconds,
                window_label,
                due_timestamp,
                alert_price,
                close_price,
                close_multiple,
                max_price,
                max_multiple,
                min_price,
                min_multiple,
                time_to_peak_seconds,
                alert_liquidity,
                close_liquidity,
                min_liquidity,
                liquidity_change_pct,
                snapshot_count,
                complete,
                updated_at
            )
            VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?
            )
            ON CONFLICT (
                alert_id,
                window_seconds
            )
            DO UPDATE SET
                token_address = excluded.token_address,
                symbol = excluded.symbol,
                chain_name = excluded.chain_name,
                alert_route = excluded.alert_route,
                quality_tag = excluded.quality_tag,
                alert_timestamp = excluded.alert_timestamp,
                window_label = excluded.window_label,
                due_timestamp = excluded.due_timestamp,
                alert_price = excluded.alert_price,
                close_price = excluded.close_price,
                close_multiple = excluded.close_multiple,
                max_price = excluded.max_price,
                max_multiple = excluded.max_multiple,
                min_price = excluded.min_price,
                min_multiple = excluded.min_multiple,
                time_to_peak_seconds = excluded.time_to_peak_seconds,
                alert_liquidity = excluded.alert_liquidity,
                close_liquidity = excluded.close_liquidity,
                min_liquidity = excluded.min_liquidity,
                liquidity_change_pct = excluded.liquidity_change_pct,
                snapshot_count = excluded.snapshot_count,
                complete = excluded.complete,
                updated_at = excluded.updated_at
            """,
            (
                outcome.get("alert_id"),
                outcome.get("token_address"),
                outcome.get("symbol"),
                outcome.get("chain_name"),
                outcome.get("alert_route"),
                outcome.get("quality_tag"),
                outcome.get("alert_timestamp"),
                outcome.get("window_seconds"),
                outcome.get("window_label"),
                outcome.get("due_timestamp"),
                outcome.get("alert_price"),
                outcome.get("close_price"),
                outcome.get("close_multiple"),
                outcome.get("max_price"),
                outcome.get("max_multiple"),
                outcome.get("min_price"),
                outcome.get("min_multiple"),
                outcome.get("time_to_peak_seconds"),
                outcome.get("alert_liquidity"),
                outcome.get("close_liquidity"),
                outcome.get("min_liquidity"),
                outcome.get("liquidity_change_pct"),
                outcome.get("snapshot_count"),
                outcome.get("complete"),
                outcome.get("updated_at")
            )
        )

    def upsert_alert_outcomes_for_alert(
        self,
        db,
        alert,
        observed_until=None,
        windows=None
    ):

        windows = windows or ALERT_OUTCOME_WINDOWS_SECONDS
        updated = 0

        for window_seconds in windows:
            snapshots = self.load_alert_window_snapshots(
                db,
                alert,
                window_seconds,
                observed_until=observed_until
            )
            outcome = self.build_alert_outcome(
                alert,
                snapshots,
                window_seconds,
                observed_until=observed_until
            )
            self.upsert_alert_outcome(
                db,
                outcome
            )
            updated += 1

        return updated

    async def update_alert_outcomes_for_snapshot(
        self,
        metrics,
        timestamp,
        now=None
    ):

        timestamp = safe_float(
            timestamp,
            time.time()
        )
        max_window = max(
            ALERT_OUTCOME_WINDOWS_SECONDS
        )

        with self.connect() as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                """
                SELECT *
                FROM ignition_alerts
                WHERE token_address = ?
                    AND alert_timestamp <= ?
                    AND alert_timestamp >= ?
                    AND alert_price > 0
                ORDER BY alert_timestamp ASC,
                    id ASC
                """,
                (
                    metrics.address,
                    timestamp,
                    timestamp - max_window
                )
            ).fetchall()

            updated = 0

            for row in rows:
                updated += self.upsert_alert_outcomes_for_alert(
                    db,
                    dict(row),
                    observed_until=now or timestamp
                )

        return updated

    async def backfill_alert_outcomes(
        self,
        since=None,
        until=None,
        limit=None,
        now=None
    ):

        clauses = [
            "alert_price > 0"
        ]
        params = []

        if since is not None:
            clauses.append("alert_timestamp >= ?")
            params.append(since)

        if until is not None:
            clauses.append("alert_timestamp <= ?")
            params.append(until)

        where = "WHERE " + " AND ".join(clauses)
        limit_sql = ""

        if limit:
            limit_sql = "LIMIT ?"
            params.append(int(limit))

        with self.connect() as db:
            db.row_factory = sqlite3.Row
            alerts = db.execute(
                f"""
                SELECT *
                FROM ignition_alerts
                {where}
                ORDER BY alert_timestamp ASC,
                    id ASC
                {limit_sql}
                """,
                params
            ).fetchall()

            updated = 0

            for row in alerts:
                updated += self.upsert_alert_outcomes_for_alert(
                    db,
                    dict(row),
                    observed_until=now or time.time()
                )

        return {
            "alerts": len(alerts),
            "outcomes": updated
        }

    def finalize_overdue_alert_outcomes(
        self,
        now=None,
        max_age_seconds=48 * 3600
    ):
        """Complete overdue alert_outcomes windows for tokens that left the
        scan set before their window elapsed (the per-snapshot updater only
        runs while a token is still scanned, so dead tokens stayed complete=0
        forever — this is why completion collapsed after the May 22 backfill).

        Only touches alerts younger than max_age_seconds so the window
        snapshots are still in the hot DB, and only finalizes windows that
        observed at least one in-window snapshot — a window with zero
        snapshots has no price information and a fake close_multiple of 1.0
        would poison downstream stats. Returns the number of alerts updated."""

        now = safe_float(now, time.time())
        since = now - max_age_seconds
        updated = 0

        with self.connect() as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                """
                SELECT DISTINCT a.id
                FROM ignition_alerts a
                JOIN alert_outcomes o
                    ON o.alert_id = a.id
                WHERE o.complete = 0
                    AND o.due_timestamp <= ?
                    AND a.alert_timestamp >= ?
                    AND a.alert_price > 0
                """,
                (now, since)
            ).fetchall()

            for row in rows:
                alert = db.execute(
                    "SELECT * FROM ignition_alerts WHERE id = ?",
                    (row["id"],)
                ).fetchone()

                if alert is None:
                    continue

                alert = dict(alert)
                has_snapshot = db.execute(
                    """
                    SELECT 1
                    FROM signal_snapshots
                    WHERE token_address = ?
                        AND timestamp >= ?
                        AND price > 0
                    LIMIT 1
                    """,
                    (
                        alert.get("token_address"),
                        safe_float(alert.get("alert_timestamp"), 0)
                    )
                ).fetchone()

                if not has_snapshot:
                    continue

                updated += self.upsert_alert_outcomes_for_alert(
                    db,
                    alert,
                    observed_until=now
                )

        return updated

    async def record_candidate_event(
        self,
        snapshot,
        now=None
    ):
        """Control-arm logging: persist one row per alert-eligible token per
        24h with the alert-time feature surface, whether or not an alert is
        delivered (record_ignition_alert flips alerted=1 afterwards). Without
        the not-alerted arm, a future entry model can only re-rank what the
        current rules already chose. Returns True when a row was written."""

        if not snapshot.get("alert_eligible"):
            return False

        token_address = snapshot.get("token_address")
        if not token_address:
            return False

        now = safe_float(
            now,
            safe_float(snapshot.get("timestamp"), time.time())
        )

        with self.connect() as db:
            recent = db.execute(
                """
                SELECT 1
                FROM candidate_events
                WHERE token_address = ?
                    AND timestamp >= ?
                LIMIT 1
                """,
                (token_address, now - 24 * 3600)
            ).fetchone()

            if recent:
                return False

            # Lineage table is owned by ticker_lineage.py and may not exist
            # yet on a fresh DB — age is best-effort.
            try:
                lineage = db.execute(
                    """
                    SELECT pair_created_at, mint_time
                    FROM ticker_lineage_records
                    WHERE token_address = ?
                    ORDER BY updated_at DESC
                    LIMIT 1
                    """,
                    (token_address,)
                ).fetchone()
            except sqlite3.Error:
                lineage = None

            token_age_seconds = None
            if lineage:
                born = min(
                    (
                        t for t in (
                            safe_float(lineage[0], 0),
                            safe_float(lineage[1], 0)
                        )
                        if t > 0
                    ),
                    default=0
                )
                if 0 < born <= now:
                    token_age_seconds = now - born

            breadth = db.execute(
                """
                SELECT COUNT(DISTINCT token_address)
                FROM candidate_events
                WHERE timestamp >= ?
                """,
                (now - 1800,)
            ).fetchone()[0]
            breadth_30m = int(breadth or 0) + 1

            # Shadow probability from the blessed runner model. Lazy import:
            # the scorer never raises and returns None until an artifact
            # passes the deployment bar (and sklearn is installed).
            model_prob = None
            try:
                from scoring.runner_model import score_candidate
                model_prob = score_candidate({
                    "score": snapshot.get("score"),
                    "raw_score": snapshot.get("raw_score"),
                    "penalty": snapshot.get("penalty"),
                    "pressure": snapshot.get("pressure"),
                    "impulse": snapshot.get("impulse"),
                    "fdv": snapshot.get("fdv"),
                    "liquidity": snapshot.get("liquidity"),
                    "volume_5m": snapshot.get("volume_5m"),
                    "volume_1h": snapshot.get("volume_1h"),
                    "volume_liquidity_ratio": snapshot.get(
                        "volume_liquidity_ratio"
                    ),
                    "buy_sell_ratio": snapshot.get("buy_sell_ratio"),
                    "h1_volume_liquidity_ratio": snapshot.get(
                        "h1_volume_liquidity_ratio"
                    ),
                    "h1_buy_sell_ratio": snapshot.get("h1_buy_sell_ratio"),
                    "price_change_5m": snapshot.get("price_change_5m"),
                    "price_change_1h": snapshot.get("price_change_1h"),
                    "momentum_score": snapshot.get("momentum_score"),
                    "local_rsi": snapshot.get("local_rsi"),
                    "token_age_seconds": token_age_seconds,
                    "breadth_eligible_30m": breadth_30m,
                    "gmgn_smart_money": None,
                    "gmgn_smart_share_pct": None,
                    "gmgn_smart_usd": None,
                    "gmgn_smart_profit_n": None,
                    "gmgn_smart_fresh_n": None,
                    "gmgn_smart_suspicious_n": None,
                    "alert_route": snapshot.get("alert_route"),
                    "quality_tag": snapshot.get("quality_tag"),
                })
            except Exception:
                model_prob = None

            db.execute(
                """
                INSERT INTO candidate_events (
                    token_address, symbol, chain_name, pair_address,
                    timestamp, price, fdv, liquidity,
                    score, raw_score, penalty, pressure, impulse,
                    volume_5m, volume_1h,
                    volume_liquidity_ratio, buy_sell_ratio,
                    h1_volume_liquidity_ratio, h1_buy_sell_ratio,
                    price_change_5m, price_change_1h,
                    momentum_score, local_rsi,
                    alert_route, quality_tag, lifecycle, risk_flags,
                    source, source_family, novelty_factor, adjusted_score,
                    data_completeness_score, evidence_bucket, evidence_factor,
                    bad_evidence_penalty, data_missing,
                    token_age_seconds, breadth_eligible_30m,
                    holder_top10_pct, model_prob, alerted
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    0
                )
                """,
                (
                    token_address,
                    snapshot.get("symbol"),
                    snapshot.get("chain_name"),
                    snapshot.get("pair_address"),
                    now,
                    snapshot.get("price"),
                    snapshot.get("fdv"),
                    snapshot.get("liquidity"),
                    snapshot.get("score"),
                    snapshot.get("raw_score"),
                    snapshot.get("penalty"),
                    snapshot.get("pressure"),
                    snapshot.get("impulse"),
                    snapshot.get("volume_5m"),
                    snapshot.get("volume_1h"),
                    snapshot.get("volume_liquidity_ratio"),
                    snapshot.get("buy_sell_ratio"),
                    snapshot.get("h1_volume_liquidity_ratio"),
                    snapshot.get("h1_buy_sell_ratio"),
                    snapshot.get("price_change_5m"),
                    snapshot.get("price_change_1h"),
                    snapshot.get("momentum_score"),
                    snapshot.get("local_rsi"),
                    snapshot.get("alert_route"),
                    snapshot.get("quality_tag"),
                    snapshot.get("lifecycle"),
                    (
                        json.dumps(snapshot.get("risk_flags"))
                        if isinstance(
                            snapshot.get("risk_flags"), (list, dict)
                        )
                        else snapshot.get("risk_flags")
                    ),
                    snapshot.get("source"),
                    snapshot.get("source_family"),
                    snapshot.get("novelty_factor"),
                    snapshot.get("adjusted_score"),
                    snapshot.get("data_completeness_score"),
                    snapshot.get("evidence_bucket"),
                    snapshot.get("evidence_factor"),
                    snapshot.get("bad_evidence_penalty"),
                    json.dumps(snapshot.get("data_missing", [])),
                    token_age_seconds,
                    breadth_30m,
                    None,
                    model_prob
                )
            )

        return True

    def _update_latest_candidate(
        self,
        token_address,
        assignments,
        values,
        now
    ):
        """UPDATE the most recent candidate_events row (<=24h) for a token."""

        with self.connect() as db:
            cursor = db.execute(
                f"""
                UPDATE candidate_events
                SET {assignments}
                WHERE id = (
                    SELECT id
                    FROM candidate_events
                    WHERE token_address = ?
                        AND timestamp >= ?
                    ORDER BY timestamp DESC
                    LIMIT 1
                )
                """,
                tuple(values) + (token_address, now - 24 * 3600)
            )

            return cursor.rowcount > 0

    def _refresh_latest_candidate_model_prob(
        self,
        token_address,
        now
    ):
        """Re-score the latest candidate row after async feature enrichment."""

        if not token_address:
            return False

        try:
            with self.connect() as db:
                db.row_factory = sqlite3.Row
                row = db.execute(
                    """
                    SELECT
                        id,
                        score, raw_score, penalty, pressure, impulse,
                        fdv, liquidity, volume_5m, volume_1h,
                        volume_liquidity_ratio, buy_sell_ratio,
                        h1_volume_liquidity_ratio, h1_buy_sell_ratio,
                        price_change_5m, price_change_1h,
                        momentum_score, local_rsi,
                        token_age_seconds, breadth_eligible_30m,
                        gmgn_smart_money, gmgn_smart_share_pct,
                        gmgn_smart_usd, gmgn_smart_profit_n,
                        gmgn_smart_fresh_n, gmgn_smart_suspicious_n,
                        alert_route, quality_tag
                    FROM candidate_events
                    WHERE token_address = ?
                        AND timestamp >= ?
                    ORDER BY timestamp DESC
                    LIMIT 1
                    """,
                    (token_address, now - 24 * 3600)
                ).fetchone()

                if not row:
                    return False

                try:
                    from scoring.runner_model import score_candidate
                    model_prob = score_candidate(dict(row))
                except Exception:
                    model_prob = None

                if model_prob is None:
                    return False

                db.execute(
                    """
                    UPDATE candidate_events
                    SET model_prob = ?
                    WHERE id = ?
                    """,
                    (model_prob, row["id"])
                )
                return True
        except Exception:
            return False

    async def update_candidate_gmgn(
        self,
        token_address,
        features,
        now=None
    ):
        """Attach the GMGN smart-money holders aggregate to the most recent
        candidate_events row for the token. Returns True when a row updated."""

        if not token_address or not features:
            return False

        now = safe_float(now, time.time())

        updated = self._update_latest_candidate(
            token_address,
            "gmgn_at = ?, gmgn_smart_money = ?, gmgn_smart_share_pct = ?, "
            "gmgn_smart_usd = ?, gmgn_smart_profit_n = ?, "
            "gmgn_smart_fresh_n = ?, gmgn_smart_suspicious_n = ?, "
            "gmgn_raw = ?",
            (
                now,
                features.get("smart_count"),
                features.get("smart_share_pct"),
                features.get("smart_usd"),
                features.get("smart_profit_n"),
                features.get("smart_fresh_n"),
                features.get("smart_suspicious_n"),
                features.get("raw"),
            ),
            now
        )

        if updated:
            self._refresh_latest_candidate_model_prob(token_address, now)

        return updated

    async def update_candidate_twitter(
        self,
        token_address,
        features,
        now=None
    ):
        """Attach OpenTwitter CA-mention aggregates to the most recent
        candidate_events row for the token."""

        if not token_address or not features:
            return False

        now = safe_float(now, time.time())

        return self._update_latest_candidate(
            token_address,
            "tw_at = ?, tw_mentions = ?, tw_authors = ?, "
            "tw_top_followers = ?, tw_first_mention_ts = ?, tw_raw = ?",
            (
                now,
                features.get("mentions"),
                features.get("authors"),
                features.get("top_followers"),
                features.get("first_mention_ts"),
                features.get("raw"),
            ),
            now
        )

    def update_alert_candle_labels(
        self,
        now=None,
        archive_database_name=None
    ):
        """Backfill fixed-horizon candle labels for alerts and candidate
        events whose horizon has elapsed. Reads token_candles from hot plus
        the warm archive (attached read-only) so labels survive retention.
        Coverage floors (>=30 candles for 6h, >=60 for 24h) mirror
        analysis/_candle_labels.py; rows below the floor stay unlabeled
        rather than getting a thin fake label. The 6h row is written first
        and the 24h fields are filled in once that horizon passes."""

        now = safe_float(now, time.time())
        labeled = 0

        with self.connect() as db:
            db.row_factory = sqlite3.Row

            archive = archive_database_name or ARCHIVE_DATABASE_NAME
            candle_sources = ["token_candles"]
            # Plain-path ATTACH: the connection is not URI-enabled, so a
            # file:...?mode=ro string would be treated as a literal filename.
            # Existence-guarded (ATTACH would otherwise create an empty DB)
            # and SELECT-only against the warm schema.
            try:
                if archive and Path(archive).exists():
                    db.execute(
                        "ATTACH DATABASE ? AS warm",
                        (str(archive),)
                    )
                    candle_sources.append("warm.token_candles")
            except sqlite3.Error:
                pass

            candle_union = " UNION ALL ".join(
                f"SELECT bucket_start, high, close FROM {src} "
                "WHERE token_address = :token "
                "AND bucket_start > :start AND bucket_start <= :end"
                for src in candle_sources
            )

            subjects = db.execute(
                """
                SELECT 'alert' AS subject_type,
                    a.id AS subject_id,
                    a.token_address,
                    a.alert_timestamp AS base_timestamp,
                    a.alert_price AS base_price
                FROM ignition_alerts a
                LEFT JOIN alert_candle_labels l
                    ON l.subject_type = 'alert'
                    AND l.subject_id = a.id
                WHERE a.alert_price > 0
                    AND a.alert_timestamp <= :due6
                    AND (
                        l.subject_id IS NULL
                        OR (
                            l.h24_max_multiple IS NULL
                            AND :now - a.alert_timestamp >= 86400
                        )
                    )
                UNION ALL
                SELECT 'candidate',
                    c.id,
                    c.token_address,
                    c.timestamp,
                    c.price
                FROM candidate_events c
                LEFT JOIN alert_candle_labels l
                    ON l.subject_type = 'candidate'
                    AND l.subject_id = c.id
                WHERE c.price > 0
                    AND c.timestamp <= :due6
                    AND (
                        l.subject_id IS NULL
                        OR (
                            l.h24_max_multiple IS NULL
                            AND :now - c.timestamp >= 86400
                        )
                    )
                """,
                {"due6": now - 6 * 3600, "now": now}
            ).fetchall()

            for s in subjects:
                base_price = safe_float(s["base_price"], 0)
                base_ts = safe_float(s["base_timestamp"], 0)
                if base_price <= 0 or base_ts <= 0:
                    continue

                candles = db.execute(
                    candle_union,
                    {
                        "token": s["token_address"],
                        "start": base_ts,
                        "end": base_ts + 24 * 3600
                    }
                ).fetchall()

                h6 = [
                    c for c in candles
                    if c["bucket_start"] <= base_ts + 6 * 3600
                    and safe_float(c["high"], 0) > 0
                ]
                h24 = [
                    c for c in candles
                    if safe_float(c["high"], 0) > 0
                ]

                h6_max = h6_close = h6_min = None
                if len(h6) >= 30:
                    highs = [safe_float(c["high"], 0) for c in h6]
                    closes = [
                        safe_float(c["close"], 0)
                        for c in h6
                        if safe_float(c["close"], 0) > 0
                    ]
                    h6_max = max(highs) / base_price
                    h6_close = (
                        closes[-1] / base_price if closes else None
                    )
                    h6_min = (
                        min(closes) / base_price if closes else None
                    )

                h24_max = None
                if now - base_ts >= 86400 and len(h24) >= 60:
                    h24_max = max(
                        safe_float(c["high"], 0) for c in h24
                    ) / base_price

                if h6_max is None and h24_max is None:
                    continue

                db.execute(
                    """
                    INSERT INTO alert_candle_labels (
                        subject_type, subject_id, token_address,
                        base_timestamp, base_price,
                        h6_max_multiple, h6_close_multiple,
                        h6_min_multiple, h6_candle_count,
                        h24_max_multiple, h24_candle_count,
                        computed_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (subject_type, subject_id)
                    DO UPDATE SET
                        h6_max_multiple = COALESCE(
                            excluded.h6_max_multiple, h6_max_multiple
                        ),
                        h6_close_multiple = COALESCE(
                            excluded.h6_close_multiple, h6_close_multiple
                        ),
                        h6_min_multiple = COALESCE(
                            excluded.h6_min_multiple, h6_min_multiple
                        ),
                        h6_candle_count = MAX(
                            excluded.h6_candle_count, h6_candle_count
                        ),
                        h24_max_multiple = COALESCE(
                            excluded.h24_max_multiple, h24_max_multiple
                        ),
                        h24_candle_count = MAX(
                            excluded.h24_candle_count, h24_candle_count
                        ),
                        computed_at = excluded.computed_at
                    """,
                    (
                        s["subject_type"],
                        s["subject_id"],
                        s["token_address"],
                        base_ts,
                        base_price,
                        h6_max,
                        h6_close,
                        h6_min,
                        len(h6),
                        h24_max,
                        len(h24),
                        now
                    )
                )
                labeled += 1

            if len(candle_sources) > 1:
                try:
                    db.commit()
                    db.execute("DETACH DATABASE warm")
                except sqlite3.Error:
                    pass

        return labeled

    def evaluate_due_confirmations(
        self,
        now=None,
        window_seconds=900,
        max_batch=500
    ):
        """Two-stage confirmation, shadow mode: ~15 min after a candidate
        event, check whether the move survived its shakeout window — price at
        or above the event price, no flush below 0.85x, and buy flow still
        present. 32% of true runners dip below 0.7x before peaking, so the
        immediate alert is systematically early; this records whether a
        delayed-confirmation policy would time entries better. Components are
        stored so thresholds can be re-derived before anything gates on
        `confirmed`. A token with zero post-event snapshots left the scan set
        — that IS a failed confirmation, recorded with snapshot_count=0."""

        now = safe_float(now, time.time())
        evaluated = 0

        with self.connect() as db:
            db.row_factory = sqlite3.Row
            events = db.execute(
                """
                SELECT id, token_address, timestamp, price
                FROM candidate_events
                WHERE confirm_evaluated_at IS NULL
                    AND timestamp <= ?
                    AND timestamp >= ?
                    AND price > 0
                ORDER BY timestamp ASC
                LIMIT ?
                """,
                (now - window_seconds, now - 48 * 3600, max_batch)
            ).fetchall()

            for ev in events:
                base_ts = safe_float(ev["timestamp"], 0)
                base_price = safe_float(ev["price"], 0)
                snaps = db.execute(
                    """
                    SELECT price, buy_sell_ratio, volume_5m
                    FROM signal_snapshots
                    WHERE token_address = ?
                        AND timestamp > ?
                        AND timestamp <= ?
                        AND price > 0
                    ORDER BY timestamp ASC
                    """,
                    (ev["token_address"], base_ts, base_ts + window_seconds)
                ).fetchall()

                if snaps:
                    prices = [safe_float(s["price"], 0) for s in snaps]
                    last = snaps[-1]
                    price_multiple = prices[-1] / base_price
                    min_multiple = min(prices) / base_price
                    flow_ratio = safe_float(last["buy_sell_ratio"], 0)
                    first_vol = safe_float(snaps[0]["volume_5m"], 0)
                    vol_ratio = (
                        safe_float(last["volume_5m"], 0) / first_vol
                        if first_vol > 0
                        else None
                    )
                    confirmed = int(
                        price_multiple >= 1.0
                        and min_multiple >= 0.85
                        and flow_ratio >= 0.90
                    )
                else:
                    price_multiple = min_multiple = flow_ratio = None
                    vol_ratio = None
                    confirmed = 0

                db.execute(
                    """
                    UPDATE candidate_events
                    SET confirm_evaluated_at = ?,
                        confirmed = ?,
                        confirm_price_multiple = ?,
                        confirm_min_multiple = ?,
                        confirm_flow_ratio = ?,
                        confirm_vol_ratio = ?,
                        confirm_snapshot_count = ?
                    WHERE id = ?
                    """,
                    (
                        now,
                        confirmed,
                        price_multiple,
                        min_multiple,
                        flow_ratio,
                        vol_ratio,
                        len(snaps),
                        ev["id"]
                    )
                )
                evaluated += 1

        return evaluated

    def load_route_outcome_scores(
        self,
        since=None,
        until=None,
        window_seconds=3600,
        min_alerts=10,
        max_bonus=8,
        max_penalty=12,
        false_positive_penalty_scale=8
    ):

        clauses = [
            "window_seconds = ?",
            "complete = 1",
            "snapshot_count > 0"
        ]
        params = [
            int(window_seconds)
        ]

        if since is not None:
            clauses.append("alert_timestamp >= ?")
            params.append(since)

        if until is not None:
            clauses.append("alert_timestamp <= ?")
            params.append(until)

        where = "WHERE " + " AND ".join(clauses)

        with self.connect() as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                f"""
                SELECT
                    CASE
                        WHEN alert_route = 'hyperevm_slow_cook'
                            THEN 'hyperevm_ignition'
                        WHEN alert_route IS NULL OR alert_route = ''
                            THEN 'none'
                        ELSE alert_route
                    END AS route,
                    COUNT(*) AS alerts,
                    SUM(
                        CASE
                            WHEN max_multiple >= 1.5 THEN 1
                            ELSE 0
                        END
                    ) AS hit_1_5x,
                    SUM(
                        CASE
                            WHEN max_multiple >= 2 THEN 1
                            ELSE 0
                        END
                    ) AS hit_2x,
                    SUM(
                        CASE
                            WHEN max_multiple >= 4 THEN 1
                            ELSE 0
                        END
                    ) AS hit_4x,
                    SUM(
                        CASE
                            WHEN max_multiple < 1.20
                                AND close_multiple < 1
                            THEN 1
                            ELSE 0
                        END
                    ) AS false_positive,
                    AVG(max_multiple) AS avg_peak_multiple,
                    AVG(close_multiple) AS avg_close_multiple,
                    AVG(min_multiple) AS avg_min_multiple
                FROM alert_outcomes
                {where}
                GROUP BY route
                """,
                params
            ).fetchall()

        scores = {}

        for row in rows:
            alerts = int(row["alerts"] or 0)
            hit_2x = int(row["hit_2x"] or 0)
            false_positive = int(row["false_positive"] or 0)
            hit_2x_rate = hit_2x / alerts if alerts else 0
            false_positive_rate = (
                false_positive / alerts
                if alerts
                else 0
            )
            avg_peak_multiple = safe_float(
                row["avg_peak_multiple"],
                0
            )
            avg_close_multiple = safe_float(
                row["avg_close_multiple"],
                0
            )

            adjustment = 0
            tier = "unproven"

            if alerts >= min_alerts:
                adjustment = (
                    hit_2x_rate * max_bonus
                    - false_positive_rate
                    * false_positive_penalty_scale
                )
                adjustment = max(
                    -abs(max_penalty),
                    min(
                        max_bonus,
                        adjustment
                    )
                )

                if (
                    hit_2x_rate >= 0.20
                    and false_positive_rate <= 0.40
                ):
                    tier = "high_confidence"
                elif false_positive_rate >= 0.50:
                    tier = "exhaustion_risk"
                elif avg_close_multiple < 0.90:
                    tier = "exhaustion_risk"
                elif hit_2x_rate >= 0.10:
                    tier = "watch"
                else:
                    tier = "scout"

            route = row["route"] or "none"
            scores[route] = {
                "route": route,
                "alerts": alerts,
                "hit_1_5x": int(row["hit_1_5x"] or 0),
                "hit_2x": hit_2x,
                "hit_4x": int(row["hit_4x"] or 0),
                "false_positive": false_positive,
                "hit_2x_rate": hit_2x_rate,
                "false_positive_rate": false_positive_rate,
                "avg_peak_multiple": avg_peak_multiple,
                "avg_close_multiple": avg_close_multiple,
                "avg_min_multiple": safe_float(
                    row["avg_min_multiple"],
                    0
                ),
                "score_adjustment": adjustment,
                "confidence_tier": tier
            }

        return scores

    async def save_signal_snapshot(
        self,
        snapshot
    ):

        with self.connect() as db:

            db.execute(
                """
                INSERT INTO signal_snapshots (
                    token_address,
                    symbol,
                    pair_address,
                    chain_name,
                    lifecycle,
                    price,
                    liquidity,
                    raw_liquidity,
                    raw_base_reserve,
                    raw_quote_reserve,
                    fdv,
                    migration_fdv,
                    migration_distance_usd,
                    migration_distance_pct,
                    migration_fdv_source,
                    volume_5m,
                    volume_1h,
                    buy_volume_5m,
                    sell_volume_5m,
                    buy_volume_1h,
                    sell_volume_1h,
                    buys_5m,
                    sells_5m,
                    buys_1h,
                    sells_1h,
                    txns_5m,
                    txns_1h,
                    price_change_5m,
                    price_change_1h,
                    price_change_6h,
                    price_change_24h,
                    pressure,
                    impulse,
                    volume_liquidity_ratio,
                    buy_sell_ratio,
                    h1_volume_liquidity_ratio,
                    h1_buy_sell_ratio,
                    score,
                    raw_score,
                    penalty,
                    quality_tag,
                    alert_route,
                    alert_eligible,
                    liquidity_lock_checked,
                    liquidity_lock_required,
                    liquidity_lock_locked,
                    liquidity_lock_locked_percent,
                    liquidity_lock_source,
                    liquidity_lock_reason,
                    local_rsi_ready,
                    local_rsi,
                    local_rsi_ema,
                    local_rsi_bullish,
                    local_rsi_bearish,
                    local_rsi_crossed_up,
                    local_rsi_crossed_down,
                    local_rsi_entry_ok,
                    local_rsi_reason,
                    local_rsi_candle_count,
                    local_rsi_timeframe_seconds,
                    source,
                    source_family,
                    novelty_factor,
                    adjusted_score,
                    data_completeness_score,
                    evidence_bucket,
                    evidence_factor,
                    bad_evidence_penalty,
                    data_missing,
                    missing,
                    risk_flags,
                    experimental_features,
                    momentum_score,
                    timestamp
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?
                )
                """,
                (
                    snapshot.get("token_address"),
                    snapshot.get("symbol"),
                    snapshot.get("pair_address"),
                    snapshot.get("chain_name"),
                    snapshot.get("lifecycle"),
                    snapshot.get("price"),
                    snapshot.get("liquidity"),
                    snapshot.get("raw_liquidity"),
                    snapshot.get("raw_base_reserve"),
                    snapshot.get("raw_quote_reserve"),
                    snapshot.get("fdv"),
                    snapshot.get("migration_fdv"),
                    snapshot.get("migration_distance_usd"),
                    snapshot.get("migration_distance_pct"),
                    snapshot.get("migration_fdv_source"),
                    snapshot.get("volume_5m"),
                    snapshot.get("volume_1h"),
                    snapshot.get("buy_volume_5m"),
                    snapshot.get("sell_volume_5m"),
                    snapshot.get("buy_volume_1h"),
                    snapshot.get("sell_volume_1h"),
                    snapshot.get("buys_5m"),
                    snapshot.get("sells_5m"),
                    snapshot.get("buys_1h"),
                    snapshot.get("sells_1h"),
                    snapshot.get("txns_5m"),
                    snapshot.get("txns_1h"),
                    snapshot.get("price_change_5m"),
                    snapshot.get("price_change_1h"),
                    snapshot.get("price_change_6h"),
                    snapshot.get("price_change_24h"),
                    snapshot.get("pressure"),
                    snapshot.get("impulse"),
                    snapshot.get("volume_liquidity_ratio"),
                    snapshot.get("buy_sell_ratio"),
                    snapshot.get("h1_volume_liquidity_ratio"),
                    snapshot.get("h1_buy_sell_ratio"),
                    snapshot.get("score"),
                    snapshot.get("raw_score"),
                    snapshot.get("penalty"),
                    snapshot.get("quality_tag"),
                    snapshot.get("alert_route"),
                    1 if snapshot.get("alert_eligible") else 0,
                    1 if snapshot.get("liquidity_lock_checked") else 0,
                    1 if snapshot.get("liquidity_lock_required") else 0,
                    1 if snapshot.get("liquidity_lock_locked") else 0,
                    snapshot.get("liquidity_lock_locked_percent"),
                    snapshot.get("liquidity_lock_source"),
                    snapshot.get("liquidity_lock_reason"),
                    1 if snapshot.get("local_rsi_ready") else 0,
                    snapshot.get("local_rsi"),
                    snapshot.get("local_rsi_ema"),
                    1 if snapshot.get("local_rsi_bullish") else 0,
                    1 if snapshot.get("local_rsi_bearish") else 0,
                    1 if snapshot.get("local_rsi_crossed_up") else 0,
                    1 if snapshot.get("local_rsi_crossed_down") else 0,
                    1 if snapshot.get("local_rsi_entry_ok") else 0,
                    snapshot.get("local_rsi_reason"),
                    snapshot.get("local_rsi_candle_count"),
                    snapshot.get("local_rsi_timeframe_seconds"),
                    snapshot.get("source"),
                    snapshot.get("source_family"),
                    snapshot.get("novelty_factor"),
                    snapshot.get("adjusted_score"),
                    snapshot.get("data_completeness_score"),
                    snapshot.get("evidence_bucket"),
                    snapshot.get("evidence_factor"),
                    snapshot.get("bad_evidence_penalty"),
                    json.dumps(snapshot.get("data_missing", [])),
                    json.dumps(snapshot.get("missing", [])),
                    json.dumps(snapshot.get("risk_flags", [])),
                    json.dumps(snapshot.get("momentum_features", {})),
                    safe_float(
                        snapshot.get("momentum_features", {})
                        .get("momentum_score", 0),
                        0
                    ),
                    snapshot.get("timestamp")
                )
            )

    async def load_signal_snapshots(
        self,
        token_address,
        limit=20
    ):

        with self.connect() as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                """
                SELECT *
                FROM signal_snapshots
                WHERE token_address = ?
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (
                    token_address,
                    limit
                )
            ).fetchall()

        snapshots = [
            dict(row)
            for row in rows
        ]
        snapshots.reverse()

        return [
            self.decode_signal_snapshot(snapshot)
            for snapshot in snapshots
        ]

    async def save_token_candle_observation(
        self,
        snapshot,
        timeframe_seconds=LOCAL_RSI_TIMEFRAME_SECONDS
    ):

        price = safe_float(
            snapshot.get("price"),
            0
        )
        close = safe_float(
            snapshot.get("close"),
            price
        )
        open_price = safe_float(
            snapshot.get("open"),
            close
        )
        high = safe_float(
            snapshot.get("high"),
            close
        )
        low = safe_float(
            snapshot.get("low"),
            close
        )
        volume = safe_float(
            snapshot.get("volume"),
            safe_float(
                snapshot.get("volume_5m"),
                0
            )
        )
        timestamp = safe_float(
            snapshot.get("timestamp"),
            time.time()
        )
        token_address = snapshot.get("token_address")

        if not token_address or close <= 0 or timestamp <= 0:
            return

        timeframe_seconds = max(
            int(timeframe_seconds or 60),
            1
        )
        bucket_start = candle_bucket(
            timestamp,
            timeframe_seconds
        )

        with self.connect() as db:
            db.execute(
                """
                INSERT INTO token_candles (
                    token_address,
                    symbol,
                    pair_address,
                    chain_name,
                    timeframe_seconds,
                    bucket_start,
                    open,
                    high,
                    low,
                    close,
                    observations,
                    first_observed_at,
                    last_observed_at,
                    price_native,
                    volume_5m,
                    volume_1h,
                    liquidity,
                    fdv,
                    market_cap,
                    source
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                ON CONFLICT (
                    token_address,
                    timeframe_seconds,
                    bucket_start
                )
                DO UPDATE SET
                    symbol = excluded.symbol,
                    pair_address = excluded.pair_address,
                    chain_name = excluded.chain_name,
                    open = CASE
                        WHEN excluded.source LIKE '%ohlcv%'
                        THEN excluded.open
                        ELSE token_candles.open
                    END,
                    high = MAX(token_candles.high, excluded.high),
                    low = CASE
                        WHEN token_candles.low <= 0 THEN excluded.low
                        WHEN excluded.low <= 0 THEN token_candles.low
                        ELSE MIN(token_candles.low, excluded.low)
                    END,
                    close = excluded.close,
                    observations = token_candles.observations + 1,
                    last_observed_at = excluded.last_observed_at,
                    price_native = excluded.price_native,
                    volume_5m = excluded.volume_5m,
                    volume_1h = excluded.volume_1h,
                    liquidity = excluded.liquidity,
                    fdv = excluded.fdv,
                    market_cap = excluded.market_cap,
                    source = excluded.source
                """,
                (
                    token_address,
                    snapshot.get("symbol"),
                    snapshot.get("pair_address"),
                    snapshot.get("chain_name"),
                    timeframe_seconds,
                    bucket_start,
                    open_price,
                    high,
                    low,
                    close,
                    1,
                    timestamp,
                    timestamp,
                    snapshot.get("price_native"),
                    volume,
                    snapshot.get("volume_1h"),
                    snapshot.get("liquidity"),
                    snapshot.get("fdv"),
                    snapshot.get("market_cap"),
                    snapshot.get("source")
                )
            )

    async def load_token_candles(
        self,
        token_address,
        timeframe_seconds=LOCAL_RSI_TIMEFRAME_SECONDS,
        limit=120,
        until=None
    ):

        clauses = [
            "token_address = ?",
            "timeframe_seconds = ?"
        ]
        params = [
            token_address,
            int(timeframe_seconds or 60)
        ]

        if until is not None:
            clauses.append("bucket_start <= ?")
            params.append(
                candle_bucket(
                    until,
                    timeframe_seconds
                )
            )

        with self.connect() as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                f"""
                SELECT *
                FROM token_candles
                WHERE {" AND ".join(clauses)}
                ORDER BY bucket_start DESC
                LIMIT ?
                """,
                params + [limit]
            ).fetchall()

        candles = [
            dict(row)
            for row in rows
        ]
        candles.reverse()
        return candles

    async def load_replay_snapshots(
        self,
        since=None,
        until=None
    ):

        clauses = []
        params = []

        if since is not None:
            clauses.append("timestamp >= ?")
            params.append(since)

        if until is not None:
            clauses.append("timestamp <= ?")
            params.append(until)

        where = ""

        if clauses:
            where = "WHERE " + " AND ".join(clauses)

        with self.connect() as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                f"""
                SELECT *
                FROM signal_snapshots
                {where}
                ORDER BY timestamp ASC,
                    id ASC
                """,
                params
            ).fetchall()

        return [
            self.decode_signal_snapshot(dict(row))
            for row in rows
        ]

    async def load_ignition_alerts(
        self,
        since=None,
        until=None,
        open_only=False
    ):

        clauses = []
        params = []

        if since is not None:
            clauses.append("alert_timestamp >= ?")
            params.append(since)

        if until is not None:
            clauses.append("alert_timestamp <= ?")
            params.append(until)

        if open_only:
            clauses.append("status = 'open'")

        where = ""

        if clauses:
            where = "WHERE " + " AND ".join(clauses)

        with self.connect() as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                f"""
                SELECT *
                FROM ignition_alerts
                {where}
                ORDER BY alert_timestamp ASC,
                    id ASC
                """,
                params
            ).fetchall()

        return [dict(row) for row in rows]

    async def build_ignition_alert_report(
        self,
        now,
        since=None,
        until=None,
        open_only=False
    ):

        alerts = await self.load_ignition_alerts(
            since=since,
            until=until,
            open_only=open_only
        )

        valid = [
            alert
            for alert in alerts
            if safe_float(alert.get("alert_price"), 0) > 0
        ]

        def multiple(alert, field):
            alert_price = safe_float(alert.get("alert_price"), 0)
            if alert_price <= 0:
                return 0
            return safe_float(alert.get(field), 0) / alert_price

        current_multiples = [
            multiple(alert, "last_price")
            for alert in valid
        ]
        peak_multiples = [
            safe_float(alert.get("max_multiple"), 0)
            for alert in valid
        ]

        current_positive = [
            alert
            for alert in valid
            if multiple(alert, "last_price") > 1
        ]

        winners = [
            alert
            for alert in valid
            if safe_float(alert.get("max_multiple"), 0) >= 2
        ]

        summary = {
            "alerts": len(valid),
            "open_alerts": sum(
                1
                for alert in valid
                if alert.get("status") == "open"
            ),
            "winners": len(winners),
            "current_positive": len(current_positive),
            "win_rate": (
                len(winners) / len(valid)
                if valid
                else 0
            ),
            "current_multiple_avg": (
                sum(current_multiples) / len(current_multiples)
                if current_multiples
                else 0
            ),
            "peak_multiple_avg": (
                sum(peak_multiples) / len(peak_multiples)
                if peak_multiples
                else 0
            ),
            "best_current_multiple": (
                max(current_multiples)
                if current_multiples
                else 0
            ),
            "best_peak_multiple": (
                max(peak_multiples)
                if peak_multiples
                else 0
            ),
            "worst_current_multiple": (
                min(current_multiples)
                if current_multiples
                else 0
            ),
            "sum_peak_multiple": sum(peak_multiples),
            "sum_current_multiple": sum(current_multiples),
            "hit_1_5x": sum(
                1
                for value in peak_multiples
                if value >= 1.5
            ),
            "hit_2x": sum(
                1
                for value in peak_multiples
                if value >= 2
            ),
            "hit_4x": sum(
                1
                for value in peak_multiples
                if value >= 4
            ),
        }

        return {
            "window": {
                "since": since,
                "until": until,
                "now": now
            },
            "summary": summary,
            "alerts": alerts
        }

    async def record_llm_pattern_report(
        self,
        provider,
        model,
        lookback_hours,
        alert_count,
        report_text,
        raw_payload=None,
        created_at=None
    ):

        with self.connect() as db:
            db.execute(
                """
                INSERT INTO llm_pattern_reports (
                    provider,
                    model,
                    lookback_hours,
                    alert_count,
                    report_text,
                    raw_payload,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    provider,
                    model,
                    lookback_hours,
                    alert_count,
                    report_text,
                    json.dumps(raw_payload or {}),
                    created_at or time.time()
                )
            )

    @staticmethod
    def decode_signal_snapshot(snapshot):

        for key in (
            "missing",
            "risk_flags",
            "experimental_features"
        ):
            value = snapshot.get(key)

            if isinstance(value, str):
                try:
                    snapshot[key] = json.loads(value)
                except json.JSONDecodeError:
                    snapshot[key] = {} if key == "experimental_features" else []

        snapshot["alert_eligible"] = bool(
            snapshot.get("alert_eligible")
        )

        return snapshot
