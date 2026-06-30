import argparse
import asyncio
import json
import math
import sqlite3
import statistics
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from storage.sqlite import DATABASE_NAME, ScannerStorage  # noqa: E402
from config import (  # noqa: E402
    POSITION_MIN_ENTRY_BUY_SELL_VOLUME_RATIO,
    POSITION_MIN_ENTRY_VOLUME_1H_USD,
    POSITION_MIN_ENTRY_VOLUME_MULTIPLE
)


WINDOW_ALIASES = {
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "6h": 21600
}


def safe_float(value, default=0.0):
    try:
        return float(value if value is not None else default)
    except (TypeError, ValueError):
        return default


def safe_int(value, default=0):
    try:
        return int(float(value if value is not None else default))
    except (TypeError, ValueError):
        return default


def parse_date(value, end_of_day=False):
    if not value:
        return None

    text = str(value).strip()

    if text.isdigit():
        return float(text)

    if len(text) == 10:
        dt = datetime.fromisoformat(text)

        if end_of_day:
            dt += timedelta(days=1)

        return dt.replace(tzinfo=timezone.utc).timestamp()

    dt = datetime.fromisoformat(text.replace("Z", "+00:00"))

    if not dt.tzinfo:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(timezone.utc).timestamp()


def parse_window(value):
    text = str(value or "1h").strip().lower()

    if text in WINDOW_ALIASES:
        return WINDOW_ALIASES[text]

    if text.isdigit():
        return int(text)

    raise argparse.ArgumentTypeError(
        "window must be one of 5m, 15m, 1h, 6h, or seconds"
    )


def window_label(seconds):
    seconds = int(seconds)

    if seconds % 3600 == 0:
        return f"{seconds // 3600}h"

    if seconds % 60 == 0:
        return f"{seconds // 60}m"

    return f"{seconds}s"


def route_name(value):
    route = str(value or "none")

    if route == "hyperevm_slow_cook":
        return "hyperevm_ignition"

    return route


def score_bucket(score):
    score = safe_int(score)

    if score >= 100:
        return "100"

    if score >= 90:
        return "90-99"

    if score >= 80:
        return "80-89"

    if score >= 70:
        return "70-79"

    if score >= 60:
        return "60-69"

    if score >= 45:
        return "45-59"

    return "<45"


def pressure_bucket(value):
    pressure = safe_float(value)

    if pressure >= 70:
        return ">=70"

    if pressure >= 55:
        return "55-69"

    if pressure >= 45:
        return "45-54"

    return "<45"


def impulse_bucket(value):
    impulse = safe_float(value)

    if impulse >= 1.50:
        return ">=1.50"

    if impulse > 1.20:
        return "1.21-1.49"

    if impulse >= 0.90:
        return "0.90-1.20"

    return "<0.90"


def fdv_bucket(value):
    fdv = safe_float(value)

    if fdv >= 50000:
        return ">=50k"

    if fdv >= 20000:
        return "20k-50k"

    if fdv >= 10000:
        return "10k-20k"

    return "<10k"


def volume_multiple_bucket(value):
    multiple = safe_float(value)

    if multiple >= 5:
        return ">=5x"

    if multiple >= POSITION_MIN_ENTRY_VOLUME_MULTIPLE:
        return "3x-5x"

    if multiple >= 1:
        return "1x-3x"

    return "<1x"


def buy_sell_volume_bucket(value):
    ratio = safe_float(value)

    if ratio >= 2:
        return ">=2.0"

    if ratio >= POSITION_MIN_ENTRY_BUY_SELL_VOLUME_RATIO:
        return "1.1-2.0"

    return "<1.1"


def raw_score_bucket(score):
    score = safe_int(score)

    if score >= 150:
        return ">=150"

    if score >= 125:
        return "125-149"

    if score >= 100:
        return "100-124"

    if score >= 75:
        return "75-99"

    if score >= 45:
        return "45-74"

    return "<45"


def mean(values):
    return statistics.mean(values) if values else 0


def median(values):
    return statistics.median(values) if values else 0


def percentile(values, pct):
    if not values:
        return 0

    ordered = sorted(values)
    index = (len(ordered) - 1) * pct
    lower = math.floor(index)
    upper = math.ceil(index)

    if lower == upper:
        return ordered[int(index)]

    return (
        ordered[lower] * (upper - index)
        + ordered[upper] * (index - lower)
    )


def pearson(xs, ys):
    pairs = [
        (safe_float(x), safe_float(y))
        for x, y in zip(xs, ys)
        if x is not None and y is not None
    ]

    if len(pairs) < 3:
        return 0

    x_values = [item[0] for item in pairs]
    y_values = [item[1] for item in pairs]
    x_mean = mean(x_values)
    y_mean = mean(y_values)
    numerator = sum(
        (x - x_mean) * (y - y_mean)
        for x, y in pairs
    )
    x_var = sum((x - x_mean) ** 2 for x in x_values)
    y_var = sum((y - y_mean) ** 2 for y in y_values)

    if x_var <= 0 or y_var <= 0:
        return 0

    return numerator / math.sqrt(x_var * y_var)


def summarize_alert_rows(rows):
    peaks = [safe_float(row.get("max_multiple")) for row in rows]
    closes = [safe_float(row.get("close_multiple")) for row in rows]
    mins = [safe_float(row.get("min_multiple")) for row in rows]
    scores = [safe_float(row.get("score")) for row in rows]
    raw_scores = [safe_float(row.get("raw_score")) for row in rows]
    penalties = [safe_float(row.get("penalty")) for row in rows]

    alerts = len(rows)
    false_positives = [
        row
        for row in rows
        if safe_float(row.get("max_multiple")) < 1.20
        and safe_float(row.get("close_multiple")) < 1
    ]

    return {
        "alerts": alerts,
        "hit_1_5x": sum(1 for value in peaks if value >= 1.5),
        "hit_2x": sum(1 for value in peaks if value >= 2),
        "hit_4x": sum(1 for value in peaks if value >= 4),
        "close_positive": sum(1 for value in closes if value > 1),
        "false_positive": len(false_positives),
        "avg_peak_multiple": mean(peaks),
        "median_peak_multiple": median(peaks),
        "p75_peak_multiple": percentile(peaks, 0.75),
        "avg_close_multiple": mean(closes),
        "median_close_multiple": median(closes),
        "avg_min_multiple": mean(mins),
        "avg_score": mean(scores),
        "avg_raw_score": mean(raw_scores),
        "avg_penalty": mean(penalties),
        "score_peak_corr": pearson(scores, peaks),
        "raw_score_peak_corr": pearson(raw_scores, peaks),
        "penalty_peak_corr": pearson(penalties, peaks),
        "hit_2x_rate": (
            sum(1 for value in peaks if value >= 2) / alerts
            if alerts
            else 0
        ),
        "hit_4x_rate": (
            sum(1 for value in peaks if value >= 4) / alerts
            if alerts
            else 0
        ),
        "false_positive_rate": (
            len(false_positives) / alerts
            if alerts
            else 0
        ),
        "close_positive_rate": (
            sum(1 for value in closes if value > 1) / alerts
            if alerts
            else 0
        )
    }


def summarize_trade_rows(rows):
    pnls = [safe_float(row.get("pnl_usd")) for row in rows]
    pnl_pcts = [safe_float(row.get("pnl_pct")) for row in rows]
    peaks = [
        safe_float(row.get("peak_multiple"), 1)
        for row in rows
    ]
    scores = [safe_float(row.get("entry_score")) for row in rows]
    trades = len(rows)

    return {
        "trades": trades,
        "pnl_usd": sum(pnls),
        "avg_pnl_usd": mean(pnls),
        "median_pnl_usd": median(pnls),
        "win_rate": (
            sum(1 for value in pnls if value > 0) / trades
            if trades
            else 0
        ),
        "loss_rate": (
            sum(1 for value in pnls if value < 0) / trades
            if trades
            else 0
        ),
        "big_loss_rate": (
            sum(1 for value in pnl_pcts if value <= -0.20) / trades
            if trades
            else 0
        ),
        "hit_2x_rate": (
            sum(1 for value in peaks if value >= 2) / trades
            if trades
            else 0
        ),
        "hit_4x_rate": (
            sum(1 for value in peaks if value >= 4) / trades
            if trades
            else 0
        ),
        "avg_peak_multiple": mean(peaks),
        "median_peak_multiple": median(peaks),
        "avg_score": mean(scores),
        "score_peak_corr": pearson(scores, peaks),
        "score_pnl_corr": pearson(scores, pnls)
    }


def group_rows(rows, key_fn, summary_fn, min_count=1):
    grouped = {}

    for row in rows:
        grouped.setdefault(key_fn(row), []).append(row)

    summaries = []

    for key, group in grouped.items():
        if len(group) < min_count:
            continue

        summary = summary_fn(group)
        summary["key"] = key
        summaries.append(summary)

    return summaries


def load_alert_rows(db_path, window_seconds, since=None, until=None):
    clauses = [
        "ao.window_seconds = ?",
        "ao.complete = 1",
        "ao.snapshot_count > 0"
    ]
    params = [int(window_seconds)]

    if since is not None:
        clauses.append("ia.alert_timestamp >= ?")
        params.append(since)

    if until is not None:
        clauses.append("ia.alert_timestamp <= ?")
        params.append(until)

    where = "WHERE " + " AND ".join(clauses)

    with sqlite3.connect(db_path) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute(
            f"""
            SELECT
                ia.id,
                ia.token_address,
                ia.symbol,
                ia.chain_name,
                ia.alert_route,
                ia.quality_tag,
                ia.score,
                ia.raw_score,
                ia.penalty,
                ia.alert_price,
                ia.alert_fdv,
                ia.alert_liquidity,
                ia.alert_pressure,
                ia.alert_impulse,
                ia.alert_timestamp,
                ia.note,
                ao.window_seconds,
                ao.close_multiple,
                ao.max_multiple,
                ao.min_multiple,
                ao.time_to_peak_seconds,
                ao.liquidity_change_pct,
                ao.snapshot_count
            FROM ignition_alerts ia
            JOIN alert_outcomes ao
                ON ao.alert_id = ia.id
            {where}
            ORDER BY ia.alert_timestamp ASC,
                ia.id ASC
            """,
            params
        ).fetchall()

    return [
        dict(row)
        for row in rows
    ]


def snapshot_for_alert(db, row):
    timestamp = safe_float(row.get("alert_timestamp"))

    snapshot = db.execute(
        """
        SELECT
            volume_5m,
            volume_1h,
            buy_volume_5m,
            sell_volume_5m,
            volume_liquidity_ratio,
            h1_volume_liquidity_ratio,
            buy_sell_ratio,
            h1_buy_sell_ratio,
            pressure,
            impulse,
            fdv,
            liquidity,
            timestamp
        FROM signal_snapshots
        WHERE token_address = ?
            AND timestamp >= ?
            AND timestamp <= ?
        ORDER BY ABS(timestamp - ?) ASC,
            id ASC
        LIMIT 1
        """,
        (
            row.get("token_address"),
            timestamp - 180,
            timestamp + 180,
            timestamp
        )
    ).fetchone()

    return dict(snapshot) if snapshot else {}


def enrich_alert_rows(db_path, rows):
    if not rows:
        return rows

    with sqlite3.connect(db_path) as db:
        db.row_factory = sqlite3.Row

        for row in rows:
            snapshot = snapshot_for_alert(db, row)

            if not snapshot:
                row["snapshot_enriched"] = False
                continue

            row["snapshot_enriched"] = True

            for key, value in snapshot.items():
                row[f"snapshot_{key}"] = value

            volume_1h = safe_float(
                snapshot.get("volume_1h")
            )
            buy_volume_5m = safe_float(
                snapshot.get("buy_volume_5m")
            )
            sell_volume_5m = safe_float(
                snapshot.get("sell_volume_5m")
            )
            row["entry_volume_multiple"] = (
                volume_1h
                / max(POSITION_MIN_ENTRY_VOLUME_1H_USD, 1e-18)
            )
            row["entry_buy_sell_volume_ratio"] = (
                buy_volume_5m
                / max(sell_volume_5m, 1e-18)
                if sell_volume_5m > 0
                else 999 if buy_volume_5m > 0 else 0
            )

    return rows


def load_snapshot_for_trade(db, address, entry_at):
    if not address or not entry_at:
        return {}

    row = db.execute(
        """
        SELECT
            score,
            raw_score,
            penalty,
            alert_route,
            quality_tag,
            pressure,
            impulse,
            volume_1h,
            buy_volume_5m,
            sell_volume_5m,
            fdv,
            timestamp
        FROM signal_snapshots
        WHERE token_address = ?
            AND timestamp >= ?
            AND timestamp <= ?
        ORDER BY ABS(timestamp - ?) ASC,
            id ASC
        LIMIT 1
        """,
        (
            address,
            entry_at - 900,
            entry_at + 900,
            entry_at
        )
    ).fetchone()

    return dict(row) if row else {}


def load_trade_rows(state_file, db_path, since=None, until=None):
    path = Path(state_file)

    if not path.exists():
        return []

    data = json.loads(path.read_text(encoding="utf-8"))
    closed = data.get("closed", [])
    rows = []

    with sqlite3.connect(db_path) as db:
        db.row_factory = sqlite3.Row

        for trade in closed:
            entry_at = safe_float(trade.get("entry_at"))

            if since is not None and entry_at < since:
                continue

            if until is not None and entry_at > until:
                continue

            row = dict(trade)
            snapshot = load_snapshot_for_trade(
                db,
                row.get("address"),
                entry_at
            )

            row["entry_route"] = route_name(
                snapshot.get("alert_route")
                or row.get("entry_alert_route")
                or "unknown"
            )
            row["entry_quality_tag"] = (
                snapshot.get("quality_tag")
                or row.get("entry_quality_tag")
                or "unknown"
            )
            row["entry_raw_score"] = safe_float(
                snapshot.get("raw_score"),
                row.get("entry_raw_score")
            )
            row["entry_penalty"] = safe_float(
                snapshot.get("penalty"),
                row.get("entry_penalty")
            )
            row["entry_score"] = safe_float(
                row.get("entry_score"),
                snapshot.get("score")
            )

            rows.append(row)

    return rows


async def maybe_backfill(backfill, since, until):
    if not backfill:
        return None

    storage = ScannerStorage()
    await storage.initialize()

    return await storage.backfill_alert_outcomes(
        since=since,
        until=until
    )


def pct(value):
    return f"{value:.1%}"


def money(value):
    return f"${value:,.2f}"


def alert_line(item):
    return (
        f"{item['key']}: n={item['alerts']} | "
        f"2x {pct(item['hit_2x_rate'])} | "
        f"4x {pct(item['hit_4x_rate'])} | "
        f"peak avg/med {item['avg_peak_multiple']:.2f}x/"
        f"{item['median_peak_multiple']:.2f}x | "
        f"false+ {pct(item['false_positive_rate'])}"
    )


def trade_line(item):
    return (
        f"{item['key']}: n={item['trades']} | "
        f"PnL {money(item['pnl_usd'])} | "
        f"win {pct(item['win_rate'])} | "
        f"2x {pct(item['hit_2x_rate'])} | "
        f"peak avg/med {item['avg_peak_multiple']:.2f}x/"
        f"{item['median_peak_multiple']:.2f}x"
    )


def build_report(alert_rows, trade_rows, window_seconds, min_count):
    alert_summary = summarize_alert_rows(alert_rows)
    trade_summary = summarize_trade_rows(trade_rows)
    by_score = group_rows(
        alert_rows,
        lambda row: score_bucket(row.get("score")),
        summarize_alert_rows,
        min_count=min_count
    )
    by_raw_score = group_rows(
        alert_rows,
        lambda row: raw_score_bucket(row.get("raw_score")),
        summarize_alert_rows,
        min_count=min_count
    )
    by_route = group_rows(
        alert_rows,
        lambda row: route_name(row.get("alert_route")),
        summarize_alert_rows,
        min_count=min_count
    )
    by_route_score = group_rows(
        alert_rows,
        lambda row: (
            f"{route_name(row.get('alert_route'))}:"
            f"{score_bucket(row.get('score'))}"
        ),
        summarize_alert_rows,
        min_count=min_count
    )
    by_pressure = group_rows(
        alert_rows,
        lambda row: pressure_bucket(
            row.get("snapshot_pressure")
            if row.get("snapshot_enriched")
            else row.get("alert_pressure")
        ),
        summarize_alert_rows,
        min_count=min_count
    )
    by_impulse = group_rows(
        alert_rows,
        lambda row: impulse_bucket(
            row.get("snapshot_impulse")
            if row.get("snapshot_enriched")
            else row.get("alert_impulse")
        ),
        summarize_alert_rows,
        min_count=min_count
    )
    by_fdv = group_rows(
        alert_rows,
        lambda row: fdv_bucket(
            row.get("snapshot_fdv")
            if row.get("snapshot_enriched")
            else row.get("alert_fdv")
        ),
        summarize_alert_rows,
        min_count=min_count
    )
    by_volume_multiple = group_rows(
        [
            row for row in alert_rows
            if row.get("snapshot_enriched")
        ],
        lambda row: volume_multiple_bucket(
            row.get("entry_volume_multiple")
        ),
        summarize_alert_rows,
        min_count=min_count
    )
    by_buy_sell_volume = group_rows(
        [
            row for row in alert_rows
            if row.get("snapshot_enriched")
        ],
        lambda row: buy_sell_volume_bucket(
            row.get("entry_buy_sell_volume_ratio")
        ),
        summarize_alert_rows,
        min_count=min_count
    )
    trade_by_score = group_rows(
        trade_rows,
        lambda row: score_bucket(row.get("entry_score")),
        summarize_trade_rows,
        min_count=min_count
    )
    trade_by_route = group_rows(
        trade_rows,
        lambda row: route_name(row.get("entry_route")),
        summarize_trade_rows,
        min_count=min_count
    )

    by_score.sort(key=lambda item: item["key"])
    by_raw_score.sort(key=lambda item: item["key"])
    by_route.sort(
        key=lambda item: (
            item["hit_2x_rate"],
            item["avg_peak_multiple"],
            item["alerts"]
        ),
        reverse=True
    )
    by_route_score.sort(
        key=lambda item: (
            item["hit_2x_rate"],
            item["avg_peak_multiple"],
            item["alerts"]
        ),
        reverse=True
    )
    for collection in (
        by_pressure,
        by_impulse,
        by_fdv,
        by_volume_multiple,
        by_buy_sell_volume
    ):
        collection.sort(
            key=lambda item: (
                item["hit_2x_rate"],
                item["avg_peak_multiple"],
                item["alerts"]
            ),
            reverse=True
        )

    trade_by_score.sort(key=lambda item: item["key"])
    trade_by_route.sort(
        key=lambda item: (
            item["pnl_usd"],
            item["win_rate"],
            item["trades"]
        ),
        reverse=True
    )

    return {
        "window": window_label(window_seconds),
        "alerts": alert_summary,
        "trades": trade_summary,
        "alerts_by_score": by_score,
        "alerts_by_raw_score": by_raw_score,
        "alerts_by_route": by_route,
        "alerts_by_route_score": by_route_score,
        "alerts_by_pressure": by_pressure,
        "alerts_by_impulse": by_impulse,
        "alerts_by_fdv": by_fdv,
        "alerts_by_volume_multiple": by_volume_multiple,
        "alerts_by_buy_sell_volume": by_buy_sell_volume,
        "trades_by_score": trade_by_score,
        "trades_by_route": trade_by_route
    }


def markdown_report(report, backfill_result=None):
    lines = [
        "# Ignition Score Reassessment",
        "",
        f"- Outcome window: {report['window']}",
        f"- Alert coverage: {report['alerts']['alerts']} alerts",
        f"- Position trades: {report['trades']['trades']} closed trades",
    ]

    if backfill_result:
        lines.append(
            "- Backfill: "
            f"{backfill_result['outcomes']} outcomes from "
            f"{backfill_result['alerts']} alerts"
        )

    lines.extend([
        "",
        "## Alert Outcomes",
        "",
        (
            f"- 2x hit rate: {pct(report['alerts']['hit_2x_rate'])}; "
            f"4x hit rate: {pct(report['alerts']['hit_4x_rate'])}; "
            f"false positives: "
            f"{pct(report['alerts']['false_positive_rate'])}"
        ),
        (
            f"- Peak avg/median: "
            f"{report['alerts']['avg_peak_multiple']:.2f}x / "
            f"{report['alerts']['median_peak_multiple']:.2f}x"
        ),
        (
            f"- Score to peak correlation: "
            f"{report['alerts']['score_peak_corr']:.3f}; "
            f"raw score to peak: "
            f"{report['alerts']['raw_score_peak_corr']:.3f}; "
            f"penalty to peak: "
            f"{report['alerts']['penalty_peak_corr']:.3f}"
        ),
        "",
        "## Alerts By Score",
        ""
    ])

    lines.extend(
        f"- {alert_line(item)}"
        for item in report["alerts_by_score"]
    )
    lines.extend(["", "## Alerts By Raw Score", ""])
    lines.extend(
        f"- {alert_line(item)}"
        for item in report["alerts_by_raw_score"]
    )
    lines.extend(["", "## Alerts By Route", ""])
    lines.extend(
        f"- {alert_line(item)}"
        for item in report["alerts_by_route"]
    )
    lines.extend(["", "## Alerts By Route And Score", ""])
    lines.extend(
        f"- {alert_line(item)}"
        for item in report["alerts_by_route_score"][:20]
    )
    lines.extend(["", "## Alerts By Current Entry Inputs", ""])

    for title, key in (
        ("Pressure", "alerts_by_pressure"),
        ("Impulse", "alerts_by_impulse"),
        ("FDV", "alerts_by_fdv"),
        ("1h Volume Multiple", "alerts_by_volume_multiple"),
        ("5m Buy/Sell Dollar Volume", "alerts_by_buy_sell_volume")
    ):
        lines.extend(["", f"### {title}", ""])
        lines.extend(
            f"- {alert_line(item)}"
            for item in report[key]
        )

    lines.extend([
        "",
        "## Actual Paper Trades",
        "",
        (
            f"- PnL: {money(report['trades']['pnl_usd'])}; "
            f"win rate: {pct(report['trades']['win_rate'])}; "
            f"2x peak rate: {pct(report['trades']['hit_2x_rate'])}; "
            f"4x peak rate: {pct(report['trades']['hit_4x_rate'])}"
        ),
        (
            f"- Score to trade peak correlation: "
            f"{report['trades']['score_peak_corr']:.3f}; "
            f"score to PnL correlation: "
            f"{report['trades']['score_pnl_corr']:.3f}"
        ),
        "",
        "### Trades By Entry Score",
        ""
    ])
    lines.extend(
        f"- {trade_line(item)}"
        for item in report["trades_by_score"]
    )
    lines.extend(["", "### Trades By Entry Route", ""])
    lines.extend(
        f"- {trade_line(item)}"
        for item in report["trades_by_route"]
    )

    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Reassess whether ignition score buckets/routes predict "
            "post-alert and actual paper-trade outcomes."
        )
    )
    parser.add_argument("--db", default=DATABASE_NAME)
    parser.add_argument(
        "--state-file",
        default=str(ROOT / "data/position_state.json")
    )
    parser.add_argument(
        "--window",
        type=parse_window,
        default=3600
    )
    parser.add_argument("--days", type=float)
    parser.add_argument("--since")
    parser.add_argument("--until")
    parser.add_argument("--min-count", type=int, default=3)
    parser.add_argument("--backfill", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--output")

    args = parser.parse_args()
    now = time.time()
    since = parse_date(args.since)
    until = parse_date(args.until, end_of_day=True)

    if args.days is not None:
        since = now - args.days * 86400
        until = None

    backfill_result = asyncio.run(
        maybe_backfill(args.backfill, since, until)
    )
    alert_rows = load_alert_rows(
        args.db,
        args.window,
        since=since,
        until=until
    )
    alert_rows = enrich_alert_rows(
        args.db,
        alert_rows
    )
    trade_rows = load_trade_rows(
        args.state_file,
        args.db,
        since=since,
        until=until
    )
    report = build_report(
        alert_rows,
        trade_rows,
        args.window,
        args.min_count
    )

    if args.json:
        output = json.dumps(
            {
                "backfill": backfill_result,
                "report": report
            },
            indent=2,
            sort_keys=True
        )
    else:
        output = markdown_report(
            report,
            backfill_result=backfill_result
        )

    if args.output:
        Path(args.output).write_text(
            output,
            encoding="utf-8"
        )
    else:
        print(output)


if __name__ == "__main__":
    main()
