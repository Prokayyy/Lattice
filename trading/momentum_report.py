import argparse
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from storage.sqlite import DATABASE_NAME  # noqa: E402


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


def safe_float(value, default=0):

    try:
        return float(value or default)
    except (TypeError, ValueError):
        return default


def derive_features(rows):

    if not rows:
        return {}

    recent = rows[-6:]
    prices = [
        safe_float(row.get("price"), 0)
        for row in recent
        if safe_float(row.get("price"), 0) > 0
    ]
    volumes = [
        safe_float(row.get("volume_5m"), 0)
        for row in recent
    ]
    liquidities = [
        safe_float(row.get("liquidity"), 0)
        for row in recent
    ]

    if not prices:
        return {}

    current = rows[-1]
    current_price = safe_float(current.get("price"), prices[-1])
    previous_price = prices[-2] if len(prices) >= 2 else current_price
    prior_prices = prices[:-1]
    recent_high = max(prior_prices) if prior_prices else current_price
    recent_low = min(prior_prices) if prior_prices else current_price

    current_return = current_price / max(previous_price, 1e-18) - 1
    previous_return = 0

    if len(prices) >= 3:
        previous_return = prices[-2] / max(prices[-3], 1e-18) - 1

    price_acceleration = current_return - previous_return

    avg_volume = (
        sum(volumes[:-1]) / max(len(volumes[:-1]), 1)
        if len(volumes) >= 2
        else safe_float(current.get("volume_5m"), 0)
    )
    current_volume = safe_float(current.get("volume_5m"), 0)
    volume_expansion = (
        current_volume / max(avg_volume, 1e-18)
        if avg_volume > 0
        else 0
    )

    vwap_numerator = 0
    vwap_denominator = 0

    for row in recent:
        price = safe_float(row.get("price"), 0)
        volume = safe_float(row.get("volume_5m"), 0)
        if price > 0 and volume > 0:
            vwap_numerator += price * volume
            vwap_denominator += volume

    vwap_proxy = (
        vwap_numerator / vwap_denominator
        if vwap_denominator > 0
        else current_price
    )

    vwap_reclaim = (
        current_price >= vwap_proxy
        and previous_price < vwap_proxy
    )

    higher_high = current_price > recent_high
    higher_low_proxy = current_price > recent_low
    breakout_strength = current_price / max(recent_high, 1e-18) - 1
    liquidity_peak = max(liquidities) if liquidities else 0
    liquidity_drain = 0

    if liquidity_peak > 0:
        liquidity_drain = 1 - safe_float(current.get("liquidity"), 0) / liquidity_peak

    volume_persistence = 0

    if len(volumes) >= 3:
        above_baseline = [
            volume > avg_volume * 1.1
            for volume in volumes[-3:]
        ]
        volume_persistence = sum(1 for item in above_baseline if item)

    momentum_score = 0
    if current_return > 0.15:
        momentum_score += 20
    if price_acceleration > 0.05:
        momentum_score += 15
    if volume_expansion >= 1.5:
        momentum_score += 20
    if higher_high:
        momentum_score += 15
    if vwap_reclaim:
        momentum_score += 15
    if liquidity_drain < 0.25:
        momentum_score += 10
    if volume_persistence >= 2:
        momentum_score += 5

    return {
        "current_return": round(current_return, 4),
        "previous_return": round(previous_return, 4),
        "price_acceleration": round(price_acceleration, 4),
        "volume_expansion": round(volume_expansion, 4),
        "current_volume": round(current_volume, 2),
        "avg_volume_lookback": round(avg_volume, 2),
        "higher_high": higher_high,
        "higher_low_proxy": higher_low_proxy,
        "breakout_strength": round(breakout_strength, 4),
        "vwap_proxy": round(vwap_proxy, 12),
        "vwap_reclaim": vwap_reclaim,
        "liquidity_drain": round(liquidity_drain, 4),
        "volume_persistence": volume_persistence,
        "momentum_score": min(momentum_score, 100)
    }


def load_snapshots(since=None, until=None):

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

    with sqlite3.connect(DATABASE_NAME) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute(
            f"""
            SELECT *
            FROM signal_snapshots
            {where}
            ORDER BY timestamp ASC, id ASC
            """,
            params
        ).fetchall()

    snapshots = []

    for row in rows:
        snapshot = dict(row)
        features = snapshot.get("experimental_features", {})

        if isinstance(features, str):
            try:
                features = json.loads(features)
            except json.JSONDecodeError:
                features = {}

        snapshot["experimental_features"] = (
            features if isinstance(features, dict) else {}
        )
        snapshots.append(snapshot)

    return snapshots


def format_time(timestamp):

    if not timestamp:
        return "unknown"

    return datetime.fromtimestamp(
        float(timestamp),
        timezone.utc
    ).strftime("%Y-%m-%d %H:%M UTC")


def print_report(snapshots, since, until):

    window = "all time"

    if since or until:
        window = (
            f"{format_time(since) if since else 'beginning'}"
            " to "
            f"{format_time(until) if until else 'now'}"
        )

    print(f"Momentum feature report: {window}")
    print("")

    if not snapshots:
        print("No snapshots found.")
        return

    by_token = {}

    for snapshot in snapshots:
        by_token.setdefault(
            snapshot.get("token_address", ""),
            []
        ).append(snapshot)

    token_rows = []

    for token, rows in by_token.items():
        latest = rows[-1]
        features = latest.get("experimental_features", {})

        if not features:
            features = derive_features(rows)

        token_rows.append({
            "symbol": latest.get("symbol", "UNKNOWN"),
            "token": token,
            "timestamp": latest.get("timestamp", 0),
            "momentum_score": safe_float(
                features.get("momentum_score"),
                latest.get("momentum_score", 0)
            ),
            "current_return": safe_float(
                features.get("current_return"),
                0
            ),
            "price_acceleration": safe_float(
                features.get("price_acceleration"),
                0
            ),
            "volume_expansion": safe_float(
                features.get("volume_expansion"),
                0
            ),
            "higher_high": bool(features.get("higher_high")),
            "vwap_reclaim": bool(features.get("vwap_reclaim")),
            "liquidity_drain": safe_float(
                features.get("liquidity_drain"),
                0
            ),
            "trade_quality_label": str(
                features.get("trade_quality_label", "neutral")
            ),
            "relative_strength_pct": safe_float(
                features.get("relative_strength_pct"),
                0
            )
        })

    token_rows.sort(
        key=lambda item: (
            item["momentum_score"],
            item["volume_expansion"]
        ),
        reverse=True
    )

    momentum_scores = [
        row["momentum_score"]
        for row in token_rows
    ]
    volume_expansions = [
        row["volume_expansion"]
        for row in token_rows
    ]

    print("Feature summary")
    print(f"- Tokens: {len(token_rows)}")
    print(
        f"- Avg momentum score: "
        f"{(sum(momentum_scores) / len(momentum_scores)) if momentum_scores else 0:.1f}"
    )
    print(
        f"- Avg volume expansion: "
        f"{(sum(volume_expansions) / len(volume_expansions)) if volume_expansions else 0:.2f}x"
    )
    print(
        f"- Hot / strong rows: "
        f"{sum(1 for value in momentum_scores if value >= 50)}/"
        f"{sum(1 for value in momentum_scores if value >= 70)}"
    )
    print(
        f"- Leading / neutral / lagging: "
        f"{sum(1 for row in token_rows if row['trade_quality_label'] == 'leading')}/"
        f"{sum(1 for row in token_rows if row['trade_quality_label'] == 'neutral')}/"
        f"{sum(1 for row in token_rows if row['trade_quality_label'] == 'lagging')}"
    )

    print("")
    print("Latest by token")

    for row in token_rows[:12]:
        print(
            "- "
            f"${row['symbol']} "
            f"{row['momentum_score']:.0f}/100 "
            f"vol {row['volume_expansion']:.2f}x "
            f"accel {row['price_acceleration']:.3f} "
            f"HH={row['higher_high']} "
            f"VWAP={row['vwap_reclaim']} "
            f"liq_drain {row['liquidity_drain']:.2%} "
            f"q={row['trade_quality_label']} "
            f"rs {row['relative_strength_pct']:+.1f}pp "
            f"at {format_time(row['timestamp'])}"
        )


def main():

    parser = argparse.ArgumentParser(
        description="Inspect experimental momentum features."
    )
    parser.add_argument("--days", type=float)
    parser.add_argument("--since")
    parser.add_argument("--until")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON."
    )
    args = parser.parse_args()

    since = parse_date(args.since)
    until = parse_date(args.until, end_of_day=True)

    if args.days is not None:
        since = (
            datetime.now(timezone.utc).timestamp()
            - args.days * 86400
        )

    snapshots = load_snapshots(
        since=since,
        until=until
    )

    if args.json:
        print(
            json.dumps(
                snapshots,
                indent=2,
                sort_keys=True,
                default=str
            )
        )
        return

    print_report(
        snapshots,
        since,
        until
    )


if __name__ == "__main__":
    main()
