import argparse
import asyncio
import json
import sqlite3
import statistics
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from storage.sqlite import (  # noqa: E402
    ALERT_OUTCOME_WINDOWS_SECONDS,
    DATABASE_NAME,
    ScannerStorage,
    safe_float
)


WINDOW_ALIASES = {
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "6h": 21600
}


def parse_date(value, end_of_day=False):

    if not value:
        return None

    if str(value).isdigit():
        return float(value)

    text = str(value).strip()

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


def window_label(seconds):

    if seconds % 3600 == 0:
        return f"{seconds // 3600}h"

    if seconds % 60 == 0:
        return f"{seconds // 60}m"

    return f"{seconds}s"


def parse_window(value):

    normalized = str(value or "1h").strip().lower()

    if normalized in WINDOW_ALIASES:
        return WINDOW_ALIASES[normalized]

    if normalized.isdigit():
        return int(normalized)

    raise argparse.ArgumentTypeError(
        "window must be one of 5m, 15m, 1h, 6h, or seconds"
    )


def load_outcomes(
    since=None,
    until=None,
    window_seconds=None,
    complete_only=True,
    require_snapshots=True
):

    clauses = []
    params = []

    if since is not None:
        clauses.append("alert_timestamp >= ?")
        params.append(since)

    if until is not None:
        clauses.append("alert_timestamp <= ?")
        params.append(until)

    if window_seconds is not None:
        clauses.append("window_seconds = ?")
        params.append(window_seconds)

    if complete_only:
        clauses.append("complete = 1")

    if require_snapshots:
        clauses.append("snapshot_count > 0")

    where = ""

    if clauses:
        where = "WHERE " + " AND ".join(clauses)

    with sqlite3.connect(DATABASE_NAME) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute(
            f"""
            SELECT *
            FROM alert_outcomes
            {where}
            ORDER BY window_seconds ASC,
                alert_timestamp ASC,
                alert_id ASC
            """,
            params
        ).fetchall()

    return [
        dict(row)
        for row in rows
    ]


def median(values):

    return statistics.median(values) if values else 0


def average(values):

    return statistics.mean(values) if values else 0


def pct(value):

    return f"{value:.1%}"


def summarize_rows(rows):

    if not rows:
        return {
            "alerts": 0,
            "hit_1_5x": 0,
            "hit_2x": 0,
            "hit_4x": 0,
            "close_positive": 0,
            "false_positive": 0,
            "avg_peak_multiple": 0,
            "median_peak_multiple": 0,
            "avg_close_multiple": 0,
            "median_close_multiple": 0,
            "avg_min_multiple": 0,
            "median_time_to_peak_minutes": 0
        }

    peak = [
        safe_float(row.get("max_multiple"))
        for row in rows
    ]
    close = [
        safe_float(row.get("close_multiple"))
        for row in rows
    ]
    min_values = [
        safe_float(row.get("min_multiple"))
        for row in rows
    ]
    time_to_peak = [
        safe_float(row.get("time_to_peak_seconds")) / 60
        for row in rows
    ]
    false_positive = [
        row
        for row in rows
        if safe_float(row.get("max_multiple")) < 1.20
        and safe_float(row.get("close_multiple")) < 1
    ]

    return {
        "alerts": len(rows),
        "hit_1_5x": sum(
            1
            for value in peak
            if value >= 1.5
        ),
        "hit_2x": sum(
            1
            for value in peak
            if value >= 2
        ),
        "hit_4x": sum(
            1
            for value in peak
            if value >= 4
        ),
        "close_positive": sum(
            1
            for value in close
            if value > 1
        ),
        "false_positive": len(false_positive),
        "avg_peak_multiple": average(peak),
        "median_peak_multiple": median(peak),
        "avg_close_multiple": average(close),
        "median_close_multiple": median(close),
        "avg_min_multiple": average(min_values),
        "median_time_to_peak_minutes": median(time_to_peak)
    }


def group_by_route(rows):

    grouped = {}

    for row in rows:
        key = (
            row.get("alert_route")
            or "none"
        )

        if key == "hyperevm_slow_cook":
            key = "hyperevm_ignition"

        grouped.setdefault(
            key,
            []
        ).append(row)

    summaries = []

    for route, route_rows in grouped.items():
        summary = summarize_rows(route_rows)
        summary["route"] = route
        summary["hit_2x_rate"] = (
            summary["hit_2x"] / summary["alerts"]
            if summary["alerts"]
            else 0
        )
        summary["false_positive_rate"] = (
            summary["false_positive"] / summary["alerts"]
            if summary["alerts"]
            else 0
        )
        summaries.append(summary)

    summaries.sort(
        key=lambda item: (
            item["hit_2x_rate"],
            item["avg_peak_multiple"],
            item["alerts"]
        ),
        reverse=True
    )

    return summaries


def print_window_report(
    rows,
    window_seconds,
    limit_routes,
    min_count
):

    summary = summarize_rows(rows)
    print(f"Post-alert outcomes: +{window_label(window_seconds)}")
    print(f"- Alerts with coverage: {summary['alerts']}")
    print(
        "- Peak hits: "
        f"1.5x {summary['hit_1_5x']} "
        f"({pct(summary['hit_1_5x'] / summary['alerts']) if summary['alerts'] else '0.0%'}), "
        f"2x {summary['hit_2x']} "
        f"({pct(summary['hit_2x'] / summary['alerts']) if summary['alerts'] else '0.0%'}), "
        f"4x {summary['hit_4x']}"
    )
    print(
        "- Avg/median peak: "
        f"{summary['avg_peak_multiple']:.2f}x / "
        f"{summary['median_peak_multiple']:.2f}x"
    )
    print(
        "- Avg/median close: "
        f"{summary['avg_close_multiple']:.2f}x / "
        f"{summary['median_close_multiple']:.2f}x"
    )
    print(
        "- False positives: "
        f"{summary['false_positive']} "
        f"({pct(summary['false_positive'] / summary['alerts']) if summary['alerts'] else '0.0%'})"
    )
    print("")
    print("Routes")

    route_summaries = [
        item
        for item in group_by_route(rows)
        if item["alerts"] >= min_count
    ][:limit_routes]

    for item in route_summaries:
        print(
            f"- {item['route']}: "
            f"n={item['alerts']} | "
            f"2x {pct(item['hit_2x_rate'])} | "
            f"peak avg {item['avg_peak_multiple']:.2f}x | "
            f"close avg {item['avg_close_multiple']:.2f}x | "
            f"false+ {pct(item['false_positive_rate'])}"
        )


async def maybe_backfill(args, since, until):

    if not args.backfill:
        return None

    storage = ScannerStorage()
    await storage.initialize()

    return await storage.backfill_alert_outcomes(
        since=since,
        until=until,
        limit=args.limit_backfill
    )


def main():

    parser = argparse.ArgumentParser(
        description=(
            "Backfill and report fixed-window outcomes after ignition alerts."
        )
    )
    parser.add_argument(
        "--backfill",
        action="store_true",
        help="Compute alert_outcomes from ignition_alerts and signal_snapshots."
    )
    parser.add_argument(
        "--limit-backfill",
        type=int,
        help="Backfill at most this many alerts."
    )
    parser.add_argument(
        "--days",
        type=float,
        help="Limit by alert timestamp to the last N days."
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
        "--window",
        type=parse_window,
        default=3600,
        help="Window to report: 5m, 15m, 1h, 6h, or seconds."
    )
    parser.add_argument(
        "--all-windows",
        action="store_true",
        help="Print one summary for every configured outcome window."
    )
    parser.add_argument(
        "--limit-routes",
        type=int,
        default=12,
        help="Maximum route rows to print."
    )
    parser.add_argument(
        "--min-route-count",
        type=int,
        default=3,
        help="Hide route rows with fewer alerts than this."
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON."
    )

    args = parser.parse_args()
    now = time.time()
    since = parse_date(args.since)
    until = parse_date(
        args.until,
        end_of_day=True
    )

    if args.days is not None:
        since = now - args.days * 86400
        until = None

    backfill_result = asyncio.run(
        maybe_backfill(
            args,
            since,
            until
        )
    )

    windows = (
        ALERT_OUTCOME_WINDOWS_SECONDS
        if args.all_windows
        else (args.window,)
    )
    reports = {}

    for window_seconds in windows:
        rows = load_outcomes(
            since=since,
            until=until,
            window_seconds=window_seconds
        )
        reports[window_label(window_seconds)] = {
            "summary": summarize_rows(rows),
            "routes": group_by_route(rows),
            "rows": rows
        }

    if args.json:
        print(json.dumps({
            "backfill": backfill_result,
            "reports": reports
        }, indent=2, sort_keys=True, default=str))
        return

    if backfill_result:
        print(
            "Backfilled "
            f"{backfill_result['outcomes']} outcomes "
            f"from {backfill_result['alerts']} alerts."
        )
        print("")

    for index, window_seconds in enumerate(windows):
        if index:
            print("")

        print_window_report(
            reports[window_label(window_seconds)]["rows"],
            window_seconds,
            args.limit_routes,
            args.min_route_count
        )


if __name__ == "__main__":

    main()
