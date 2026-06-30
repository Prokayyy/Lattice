import argparse
import contextlib
import io
import json
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from models import TokenMetrics  # noqa: E402
from storage.sqlite import DATABASE_NAME  # noqa: E402
from trading.position_engine import PositionEngine, safe_float  # noqa: E402


def parse_date(value, end_of_day=False):

    if not value:
        return None

    if value.isdigit():
        return float(value)

    text = value.strip()

    if len(text) == 10:
        dt = datetime.fromisoformat(text)

        if end_of_day:
            dt = dt + timedelta(days=1)

        return dt.replace(
            tzinfo=timezone.utc
        ).timestamp()

    dt = datetime.fromisoformat(
        text.replace("Z", "+00:00")
    )

    if not dt.tzinfo:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(
        timezone.utc
    ).timestamp()


def decode_json_list(value):

    if not value:
        return []

    if isinstance(value, list):
        return value

    try:
        decoded = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return []

    if isinstance(decoded, list):
        return decoded

    return []


def load_snapshots(
    db_path,
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

    with sqlite3.connect(db_path) as db:
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

    snapshots = []

    for row in rows:
        snapshot = dict(row)
        snapshot["missing"] = decode_json_list(
            snapshot.get("missing")
        )
        snapshot["risk_flags"] = decode_json_list(
            snapshot.get("risk_flags")
        )
        snapshot["alert_eligible"] = bool(
            snapshot.get("alert_eligible")
        )
        snapshots.append(snapshot)

    return snapshots


def metrics_from_snapshot(snapshot):

    return TokenMetrics(
        address=snapshot.get("token_address", ""),
        symbol=snapshot.get("symbol", "UNKNOWN"),
        name=snapshot.get("name", ""),
        pair_address=snapshot.get("pair_address", ""),
        liquidity=safe_float(snapshot.get("liquidity"), 0),
        fdv=safe_float(snapshot.get("fdv"), 0),
        price=safe_float(snapshot.get("price"), 0),
        volume_5m=safe_float(snapshot.get("volume_5m"), 0),
        volume_1h=safe_float(snapshot.get("volume_1h"), 0),
        buy_volume_5m=safe_float(snapshot.get("buy_volume_5m"), 0),
        sell_volume_5m=safe_float(snapshot.get("sell_volume_5m"), 0),
        buy_volume_1h=safe_float(snapshot.get("buy_volume_1h"), 0),
        sell_volume_1h=safe_float(snapshot.get("sell_volume_1h"), 0),
        buys_5m=int(safe_float(snapshot.get("buys_5m"), 0)),
        sells_5m=int(safe_float(snapshot.get("sells_5m"), 0)),
        buys_1h=int(safe_float(snapshot.get("buys_1h"), 0)),
        sells_1h=int(safe_float(snapshot.get("sells_1h"), 0)),
        price_change_5m=safe_float(
            snapshot.get("price_change_5m"),
            0
        ),
        price_change_1h=safe_float(
            snapshot.get("price_change_1h"),
            0
        ),
        price_change_6h=safe_float(
            snapshot.get("price_change_6h"),
            0
        ),
        price_change_24h=safe_float(
            snapshot.get("price_change_24h"),
            0
        ),
        age_hours=0,
        age_source="replay",
        chain=snapshot.get("chain_name", "solana"),
        source="snapshot_replay",
        lifecycle=snapshot.get("lifecycle", "unknown"),
        raw_liquidity=safe_float(
            snapshot.get("raw_liquidity"),
            0
        ),
        raw_base_reserve=safe_float(
            snapshot.get("raw_base_reserve"),
            0
        ),
        raw_quote_reserve=safe_float(
            snapshot.get("raw_quote_reserve"),
            0
        ),
        liquidity_source="snapshot",
        migration_fdv=safe_float(
            snapshot.get("migration_fdv"),
            0
        ),
        migration_distance_usd=safe_float(
            snapshot.get("migration_distance_usd"),
            0
        ),
        migration_distance_pct=safe_float(
            snapshot.get("migration_distance_pct"),
            0
        ),
        migration_fdv_source=snapshot.get(
            "migration_fdv_source",
            ""
        )
    )


def details_from_snapshot(snapshot):

    return {
        "price_jump": safe_float(snapshot.get("impulse"), 0),
        "volume_liquidity_ratio": safe_float(
            snapshot.get("volume_liquidity_ratio"),
            0
        ),
        "buy_sell_ratio": safe_float(
            snapshot.get("buy_sell_ratio"),
            0
        ),
        "h1_volume_liquidity_ratio": safe_float(
            snapshot.get("h1_volume_liquidity_ratio"),
            0
        ),
        "h1_buy_sell_ratio": safe_float(
            snapshot.get("h1_buy_sell_ratio"),
            0
        ),
        "txns_5m": int(safe_float(snapshot.get("txns_5m"), 0)),
        "h1_txns": int(safe_float(snapshot.get("txns_1h"), 0)),
        "raw_score": int(safe_float(snapshot.get("raw_score"), 0)),
        "penalty": safe_float(snapshot.get("penalty"), 0),
        "quality_tag": snapshot.get("quality_tag", "standard"),
        "alert_route": snapshot.get("alert_route", "none"),
        "alert_eligible": bool(snapshot.get("alert_eligible")),
        "missing": snapshot.get("missing", []),
        "risk_flags": snapshot.get("risk_flags", [])
    }


def summarize_state(state):

    closed = state.get("closed", [])
    open_positions = state.get("open", {})
    pnl_values = [
        safe_float(position.get("pnl_usd"), 0)
        for position in closed
    ]
    wins = [
        value
        for value in pnl_values
        if value > 0
    ]
    losses = [
        value
        for value in pnl_values
        if value < 0
    ]

    total_pnl = sum(pnl_values)

    return {
        "closed_trades": len(closed),
        "open_positions": len(open_positions),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": (
            len(wins) / len(closed)
            if closed
            else 0
        ),
        "pnl_usd": total_pnl,
        "average_pnl_usd": (
            total_pnl / len(closed)
            if closed
            else 0
        ),
        "best_pnl_usd": max(pnl_values) if pnl_values else 0,
        "worst_pnl_usd": min(pnl_values) if pnl_values else 0,
        "cash_sol": safe_float(state.get("cash_sol"), 0)
    }


def print_summary(summary):

    print("Position replay results")
    print(f"- Closed trades: {summary['closed_trades']}")
    print(f"- Open positions: {summary['open_positions']}")
    print(
        f"- Wins/Losses: {summary['wins']}/"
        f"{summary['losses']} "
        f"({summary['win_rate']:.1%} win rate)"
    )
    print(f"- PnL: ${summary['pnl_usd']:,.2f}")
    print(
        "- Avg / Best / Worst: "
        f"${summary['average_pnl_usd']:,.2f} / "
        f"${summary['best_pnl_usd']:,.2f} / "
        f"${summary['worst_pnl_usd']:,.2f}"
    )
    print(f"- Ending cash: {summary['cash_sol']:.2f} SOL")


def run_replay(
    snapshots,
    state_file,
    quiet=False,
):

    engine = PositionEngine(
        state_file=state_file
    )
    engine.reset_state()
    recent_by_address = {}
    candles_by_address = {}

    for snapshot in snapshots:
        address = snapshot.get("token_address")

        if not address:
            continue

        recent = recent_by_address.setdefault(
            address,
            []
        )
        recent.append(snapshot)

        if len(recent) > 60:
            del recent[:-60]

        metrics = metrics_from_snapshot(snapshot)
        details = details_from_snapshot(snapshot)
        score = int(
            safe_float(snapshot.get("score"), 0)
        )
        pressure = safe_float(
            snapshot.get("pressure"),
            0
        )
        now = safe_float(
            snapshot.get("timestamp"),
            0
        )
        details["pressure"] = pressure

        if quiet:
            with contextlib.redirect_stdout(io.StringIO()):
                engine.handle_scan(
                    metrics,
                    score,
                    details,
                    now,
                    pressure=pressure,
                    recent_snapshots=recent
                )
        else:
            engine.handle_scan(
                metrics,
                score,
                details,
                now,
                pressure=pressure,
                recent_snapshots=recent
            )

    engine.save_state()
    return engine.load_state()


def main():

    parser = argparse.ArgumentParser(
        description="Replay saved signal snapshots through the position engine."
    )
    parser.add_argument(
        "--db",
        default=str(ROOT / DATABASE_NAME),
        help="SQLite database path."
    )
    parser.add_argument(
        "--days",
        type=float,
        help="Look back this many days from now."
    )
    parser.add_argument(
        "--since",
        help="Start date/time, e.g. 2026-05-10 or ISO timestamp."
    )
    parser.add_argument(
        "--until",
        help="End date/time, e.g. 2026-05-11 or ISO timestamp."
    )
    parser.add_argument(
        "--state-file",
        default=str(
            Path(tempfile.gettempdir())
            / "organic_revival_position_replay.json"
        ),
        help="Replay state path. Defaults to /tmp."
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON."
    )

    args = parser.parse_args()

    since = parse_date(args.since)
    until = parse_date(
        args.until,
        end_of_day=True
    )

    if args.days is not None:
        since = (
            datetime.now(timezone.utc).timestamp()
            - args.days * 86400
        )

    if os.path.exists(args.state_file):
        os.remove(args.state_file)

    snapshots = load_snapshots(
        args.db,
        since=since,
        until=until
    )
    state = run_replay(
        snapshots,
        args.state_file,
        quiet=args.json,
    )
    summary = summarize_state(state)
    summary["snapshots"] = len(snapshots)

    if args.json:
        print(
            json.dumps(
                summary,
                indent=2,
                sort_keys=True
            )
        )
        return

    print(f"Snapshots replayed: {len(snapshots)}")
    print_summary(summary)


if __name__ == "__main__":
    main()
