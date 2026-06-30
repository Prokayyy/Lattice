"""Offline dormant-revival research pass.

This script is deliberately standalone: it reads local scanner data, writes
research CSV/Markdown files, and does not import or modify live scanner runtime.
"""

from __future__ import annotations

import argparse
import csv
import math
import sqlite3
import statistics
import time
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "scanner.db"
DEFAULT_OUT = ROOT / "analysis"


def sf(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def median(values, default=0.0):
    vals = [sf(v) for v in values if sf(v) > 0]
    if not vals:
        return default
    return statistics.median(vals)


def fmt_ts(ts):
    if not ts:
        return ""
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(ts)))


def clamp(value, lo=0.0, hi=1.0):
    return max(lo, min(hi, value))


def ratio(num, den, default=0.0):
    num = sf(num)
    den = sf(den)
    if den <= 0:
        return default
    return num / den


def setup_score(
    *,
    volume_expansion,
    base_compression,
    breakout_multiple,
    liquidity_stability,
    pre_volume_1h,
    pre_drawdown_from_prior_peak,
):
    volume_component = clamp(math.log1p(max(volume_expansion, 0.0)) / math.log1p(20.0))
    compression_component = clamp((2.5 - max(base_compression, 1.0)) / 1.5)
    breakout_component = clamp((breakout_multiple - 1.0) / 0.50)
    liquidity_component = clamp(liquidity_stability)
    quiet_component = clamp((5000.0 - pre_volume_1h) / 5000.0)
    drawdown_component = clamp(pre_drawdown_from_prior_peak / 0.70)
    return round(
        25 * volume_component
        + 20 * compression_component
        + 20 * breakout_component
        + 15 * liquidity_component
        + 10 * quiet_component
        + 10 * drawdown_component,
        2,
    )


def iter_token_candles(conn, *, timeframe_seconds, chain_name=None):
    where = ["timeframe_seconds=?"]
    params = [timeframe_seconds]
    if chain_name:
        where.append("LOWER(chain_name)=LOWER(?)")
        params.append(chain_name)

    sql = f"""
        SELECT
            token_address, symbol, pair_address, chain_name, bucket_start,
            open, high, low, close, observations, volume_5m, volume_1h,
            liquidity, fdv, market_cap
        FROM token_candles
        WHERE {" AND ".join(where)}
        ORDER BY token_address, bucket_start
    """

    current_token = None
    rows = []
    for row in conn.execute(sql, params):
        token = row["token_address"]
        if current_token is not None and token != current_token:
            yield current_token, rows
            rows = []
        current_token = token
        rows.append(dict(row))
    if current_token is not None:
        yield current_token, rows


def candle_price(row, key):
    value = sf(row.get(key))
    if value > 0:
        return value
    if key in {"high", "low"}:
        return sf(row.get("close"))
    return value


def clean_candles(rows):
    out = []
    for row in rows:
        close = candle_price(row, "close")
        high = max(candle_price(row, "high"), close)
        low = candle_price(row, "low")
        if close <= 0 or high <= 0:
            continue
        if low <= 0:
            low = close
        row = dict(row)
        row["close"] = close
        row["high"] = high
        row["low"] = min(low, close, high)
        out.append(row)
    return out


def token_outcome(token, candles):
    first = candles[0]
    last = candles[-1]
    first_price = sf(first.get("close"))
    max_high = max(sf(c.get("high")) for c in candles)
    min_low = min(sf(c.get("low")) for c in candles if sf(c.get("low")) > 0)
    max_i = max(range(len(candles)), key=lambda i: sf(candles[i].get("high")))
    return {
        "token_address": token,
        "symbol": first.get("symbol") or "",
        "chain_name": first.get("chain_name") or "",
        "pair_address": first.get("pair_address") or "",
        "first_seen": sf(first.get("bucket_start")),
        "last_seen": sf(last.get("bucket_start")),
        "span_hours": round((sf(last.get("bucket_start")) - sf(first.get("bucket_start"))) / 3600, 2),
        "n_candles": len(candles),
        "first_price": first_price,
        "last_price": sf(last.get("close")),
        "max_high": max_high,
        "min_low": min_low,
        "max_multiple_from_first": round(ratio(max_high, first_price), 4),
        "max_multiple_from_min": round(ratio(max_high, min_low), 4),
        "time_to_max_hours": round((sf(candles[max_i].get("bucket_start")) - sf(first.get("bucket_start"))) / 3600, 2),
        "hit_2x_from_first": int(ratio(max_high, first_price) >= 2),
        "hit_5x_from_first": int(ratio(max_high, first_price) >= 5),
        "hit_10x_from_first": int(ratio(max_high, first_price) >= 10),
    }


def find_revival_event(candles, *, lookback_candles, future_candles, min_future_candles):
    best = None
    for i in range(lookback_candles, len(candles)):
        current = candles[i]
        pre = candles[i - lookback_candles:i]
        future = candles[i:min(len(candles), i + future_candles + 1)]
        if len(future) < min_future_candles:
            continue

        close = sf(current.get("close"))
        if close <= 0:
            continue

        pre_closes = [sf(c.get("close")) for c in pre if sf(c.get("close")) > 0]
        if len(pre_closes) < max(4, lookback_candles // 3):
            continue

        pre_high = max(sf(c.get("high")) for c in pre)
        pre_low = min(sf(c.get("low")) for c in pre if sf(c.get("low")) > 0)
        pre_median_price = median(pre_closes)
        pre_volume_5m = median(c.get("volume_5m") for c in pre)
        pre_volume_1h = median(c.get("volume_1h") for c in pre)
        pre_liq = median(c.get("liquidity") for c in pre)
        pre_min_liq = min([sf(c.get("liquidity")) for c in pre if sf(c.get("liquidity")) > 0] or [0.0])
        current_volume_5m = sf(current.get("volume_5m"))
        current_volume_1h = sf(current.get("volume_1h"))
        current_liq = sf(current.get("liquidity"))

        if pre_high <= 0 or pre_low <= 0 or pre_median_price <= 0:
            continue

        future_highs = [sf(c.get("high")) for c in future if sf(c.get("high")) > 0]
        if not future_highs:
            continue
        future_peak = max(future_highs)
        peak_i = max(range(len(future)), key=lambda j: sf(future[j].get("high")))
        future_min_liq = min([sf(c.get("liquidity")) for c in future if sf(c.get("liquidity")) > 0] or [0.0])

        prior_high = max(sf(c.get("high")) for c in candles[:i + 1])
        pre_drawdown = 1.0 - ratio(pre_median_price, prior_high, 1.0)
        base_compression = ratio(pre_high, pre_low, 999.0)
        breakout_multiple = ratio(close, pre_high, 0.0)
        volume_expansion_5m = ratio(current_volume_5m + 1.0, pre_volume_5m + 1.0, 0.0)
        volume_expansion_1h = ratio(current_volume_1h + 1.0, pre_volume_1h + 1.0, 0.0)
        volume_expansion = max(volume_expansion_5m, volume_expansion_1h)
        liquidity_stability = min(
            ratio(pre_min_liq, pre_liq, 0.0),
            ratio(current_liq, pre_liq, 0.0) if current_liq > 0 else 0.0,
        )
        score = setup_score(
            volume_expansion=volume_expansion,
            base_compression=base_compression,
            breakout_multiple=breakout_multiple,
            liquidity_stability=liquidity_stability,
            pre_volume_1h=pre_volume_1h,
            pre_drawdown_from_prior_peak=pre_drawdown,
        )

        future_peak_multiple = ratio(future_peak, close, 0.0)
        base_to_peak_multiple = ratio(future_peak, pre_median_price, 0.0)
        research_score = round(
            score
            + min(40.0, max(future_peak_multiple - 1.0, 0.0) * 10.0)
            + min(20.0, max(base_to_peak_multiple - 1.0, 0.0) * 3.0),
            2,
        )
        event = {
            "event_ts": sf(current.get("bucket_start")),
            "event_time": fmt_ts(current.get("bucket_start")),
            "entry_price": close,
            "future_peak": future_peak,
            "future_peak_multiple": round(future_peak_multiple, 4),
            "base_to_peak_multiple": round(base_to_peak_multiple, 4),
            "time_to_peak_hours": round((sf(future[peak_i].get("bucket_start")) - sf(current.get("bucket_start"))) / 3600, 2),
            "setup_score": score,
            "research_score": research_score,
            "base_compression": round(base_compression, 4),
            "breakout_multiple": round(breakout_multiple, 4),
            "volume_expansion": round(volume_expansion, 4),
            "volume_expansion_5m": round(volume_expansion_5m, 4),
            "volume_expansion_1h": round(volume_expansion_1h, 4),
            "pre_volume_5m": round(pre_volume_5m, 2),
            "pre_volume_1h": round(pre_volume_1h, 2),
            "current_volume_5m": round(current_volume_5m, 2),
            "current_volume_1h": round(current_volume_1h, 2),
            "pre_liquidity": round(pre_liq, 2),
            "current_liquidity": round(current_liq, 2),
            "future_min_liquidity": round(future_min_liq, 2),
            "liquidity_stability": round(liquidity_stability, 4),
            "pre_drawdown_from_prior_peak": round(pre_drawdown, 4),
            "is_dormant_setup": int(
                score >= 55
                and base_compression <= 2.5
                and volume_expansion >= 2.0
                and liquidity_stability >= 0.45
            ),
            "is_explosive_2x": int(future_peak_multiple >= 2.0),
            "is_explosive_5x": int(future_peak_multiple >= 5.0),
            "is_explosive_10x": int(future_peak_multiple >= 10.0),
            "is_base_to_peak_2x": int(base_to_peak_multiple >= 2.0),
            "is_base_to_peak_5x": int(base_to_peak_multiple >= 5.0),
            "is_base_to_peak_10x": int(base_to_peak_multiple >= 10.0),
        }
        if best is None or event["research_score"] > best["research_score"]:
            best = event
    return best


def write_csv(path, rows):
    rows = list(rows)
    if not rows:
        path.write_text("")
        return
    fieldnames = []
    seen = set()
    for row in rows:
        for key in row.keys():
            if key not in seen:
                fieldnames.append(key)
                seen.add(key)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def pct(part, whole):
    if whole <= 0:
        return "0.0%"
    return f"{100.0 * part / whole:.1f}%"


def write_report(path, *, args, coverage, token_rows, event_rows):
    n_tokens = len(token_rows)
    event_count = len(event_rows)
    setup_events = [e for e in event_rows if int(e.get("is_dormant_setup", 0))]
    setup_n = len(setup_events)
    chain_counts = Counter(r.get("chain_name") or "unknown" for r in token_rows)
    hit_2 = sum(int(r.get("hit_2x_from_first", 0)) for r in token_rows)
    hit_5 = sum(int(r.get("hit_5x_from_first", 0)) for r in token_rows)
    hit_10 = sum(int(r.get("hit_10x_from_first", 0)) for r in token_rows)

    def setup_hit(key):
        return sum(int(e.get(key, 0)) for e in setup_events)

    top = sorted(event_rows, key=lambda r: sf(r.get("research_score")), reverse=True)[:20]
    explosive_setups = [
        e
        for e in event_rows
        if int(e.get("is_dormant_setup", 0))
        and int(e.get("is_base_to_peak_5x", 0))
    ]
    top_explosive = sorted(
        explosive_setups,
        key=lambda r: (sf(r.get("base_to_peak_multiple")), sf(r.get("setup_score"))),
        reverse=True,
    )[:20]
    lines = [
        "# Dormant Revival Research",
        "",
        "Standalone offline research output. This is not wired into the scanner.",
        "",
        "## Data Coverage",
        "",
        f"- Database: `{args.db}`",
        f"- Candle timeframe: `{args.timeframe_seconds}` seconds",
        f"- Candle rows scanned: `{coverage['candles']}`",
        f"- Tokens analyzed: `{n_tokens}`",
        f"- Time range: `{fmt_ts(coverage['min_ts'])}` to `{fmt_ts(coverage['max_ts'])}`",
        f"- Lookback: `{args.lookback_hours}`h; forward outcome window: `{args.future_hours}`h",
        "",
        "## Token Outcomes",
        "",
        f"- >=2x from first observed: `{hit_2}` / `{n_tokens}` ({pct(hit_2, n_tokens)})",
        f"- >=5x from first observed: `{hit_5}` / `{n_tokens}` ({pct(hit_5, n_tokens)})",
        f"- >=10x from first observed: `{hit_10}` / `{n_tokens}` ({pct(hit_10, n_tokens)})",
        f"- Chain mix: {', '.join(f'{k}={v}' for k, v in chain_counts.most_common(8))}",
        "",
        "## Dormant Setup Outcomes",
        "",
        f"- Best-event rows written: `{event_count}`",
        f"- Dormant setup candidates: `{setup_n}`",
        f"- Dormant setup -> 2x in forward window: `{setup_hit('is_explosive_2x')}` / `{setup_n}` ({pct(setup_hit('is_explosive_2x'), setup_n)})",
        f"- Dormant setup -> 5x in forward window: `{setup_hit('is_explosive_5x')}` / `{setup_n}` ({pct(setup_hit('is_explosive_5x'), setup_n)})",
        f"- Dormant setup -> 10x in forward window: `{setup_hit('is_explosive_10x')}` / `{setup_n}` ({pct(setup_hit('is_explosive_10x'), setup_n)})",
        f"- Dormant setup -> 5x from quiet base: `{setup_hit('is_base_to_peak_5x')}` / `{setup_n}` ({pct(setup_hit('is_base_to_peak_5x'), setup_n)})",
        f"- Dormant setup -> 10x from quiet base: `{setup_hit('is_base_to_peak_10x')}` / `{setup_n}` ({pct(setup_hit('is_base_to_peak_10x'), setup_n)})",
        "",
        "## Top Dormant-Revival Events",
        "",
        "| rank | symbol | chain | setup | future peak | base->peak | event time | notes |",
        "|---:|---|---|---:|---:|---:|---|---|",
    ]
    for i, row in enumerate(top, 1):
        notes = (
            f"volx {sf(row.get('volume_expansion')):.1f}; "
            f"compression {sf(row.get('base_compression')):.2f}; "
            f"liq {sf(row.get('liquidity_stability')):.2f}"
        )
        lines.append(
            f"| {i} | `{row.get('symbol') or '?'}` | {row.get('chain_name') or '?'} "
            f"| {sf(row.get('setup_score')):.1f} "
            f"| {sf(row.get('future_peak_multiple')):.2f}x "
            f"| {sf(row.get('base_to_peak_multiple')):.2f}x "
            f"| {row.get('event_time') or ''} | {notes} |"
        )

    lines += [
        "",
        "## Explosive Dormant Setups",
        "",
        "| rank | symbol | chain | setup | future peak | base->peak | event time | notes |",
        "|---:|---|---|---:|---:|---:|---|---|",
    ]
    for i, row in enumerate(top_explosive, 1):
        notes = (
            f"volx {sf(row.get('volume_expansion')):.1f}; "
            f"compression {sf(row.get('base_compression')):.2f}; "
            f"liq {sf(row.get('liquidity_stability')):.2f}"
        )
        lines.append(
            f"| {i} | `{row.get('symbol') or '?'}` | {row.get('chain_name') or '?'} "
            f"| {sf(row.get('setup_score')):.1f} "
            f"| {sf(row.get('future_peak_multiple')):.2f}x "
            f"| {sf(row.get('base_to_peak_multiple')):.2f}x "
            f"| {row.get('event_time') or ''} | {notes} |"
        )

    lines += [
        "",
        "## Outputs",
        "",
        "- `analysis/dormant_revival_tokens.csv`",
        "- `analysis/dormant_revival_events.csv`",
        "- `analysis/dormant_revival_explosive_setups.csv`",
        "",
        "## Current Limitation",
        "",
        "This pass is token-level only. Wallet attribution needs parsed swap/transfer",
        "history for the selected tokens, then wallet PnL and wallet clustering can be",
        "built on top of these candidate token windows.",
        "",
    ]
    path.write_text("\n".join(lines))


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT))
    parser.add_argument("--timeframe-seconds", type=int, default=900)
    parser.add_argument("--chain", default="", help="Optional chain filter, e.g. solana")
    parser.add_argument("--lookback-hours", type=float, default=12.0)
    parser.add_argument("--future-hours", type=float, default=24.0)
    parser.add_argument("--min-candles", type=int, default=12)
    return parser.parse_args()


def main():
    args = parse_args()
    db = Path(args.db)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    lookback_candles = max(1, int((args.lookback_hours * 3600) // args.timeframe_seconds))
    future_candles = max(1, int((args.future_hours * 3600) // args.timeframe_seconds))
    min_future_candles = max(1, min(future_candles, args.min_candles))

    token_rows = []
    event_rows = []
    coverage = {"candles": 0, "min_ts": 0.0, "max_ts": 0.0}

    for token, raw_rows in iter_token_candles(
        conn,
        timeframe_seconds=args.timeframe_seconds,
        chain_name=args.chain.strip() or None,
    ):
        candles = clean_candles(raw_rows)
        if len(candles) < max(args.min_candles, lookback_candles + 1):
            continue

        coverage["candles"] += len(candles)
        first_ts = sf(candles[0].get("bucket_start"))
        last_ts = sf(candles[-1].get("bucket_start"))
        coverage["min_ts"] = (
            first_ts if not coverage["min_ts"] else min(coverage["min_ts"], first_ts)
        )
        coverage["max_ts"] = max(coverage["max_ts"], last_ts)

        summary = token_outcome(token, candles)
        event = find_revival_event(
            candles,
            lookback_candles=lookback_candles,
            future_candles=future_candles,
            min_future_candles=min_future_candles,
        )
        if event:
            event_row = {
                **{
                    "token_address": token,
                    "symbol": summary["symbol"],
                    "chain_name": summary["chain_name"],
                    "pair_address": summary["pair_address"],
                },
                **event,
            }
            event_rows.append(event_row)
            summary.update({
                f"best_{k}": v
                for k, v in event.items()
                if k not in {"event_time"}
            })
            summary["best_event_time"] = event["event_time"]
        token_rows.append(summary)

    token_rows.sort(key=lambda r: sf(r.get("max_multiple_from_first")), reverse=True)
    event_rows.sort(key=lambda r: sf(r.get("research_score")), reverse=True)

    write_csv(out_dir / "dormant_revival_tokens.csv", token_rows)
    write_csv(out_dir / "dormant_revival_events.csv", event_rows)
    write_csv(
        out_dir / "dormant_revival_explosive_setups.csv",
        [
            row
            for row in event_rows
            if int(row.get("is_dormant_setup", 0))
            and int(row.get("is_base_to_peak_5x", 0))
        ],
    )
    write_report(
        out_dir / "dormant_revival_report.md",
        args=args,
        coverage=coverage,
        token_rows=token_rows,
        event_rows=event_rows,
    )

    print(f"tokens={len(token_rows)} events={len(event_rows)}")
    print(f"wrote {out_dir / 'dormant_revival_tokens.csv'}")
    print(f"wrote {out_dir / 'dormant_revival_events.csv'}")
    print(f"wrote {out_dir / 'dormant_revival_explosive_setups.csv'}")
    print(f"wrote {out_dir / 'dormant_revival_report.md'}")


if __name__ == "__main__":
    main()
