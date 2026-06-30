"""Q3 exit sweep over actual Lattice paper entries.

This is the ledger-anchored companion to stop_sweep.py. Instead of discovering
new replay entries from signal_snapshots, it reads the actual entries in
discovery/trades.jsonl for the requested window, then replays each token's
subsequent signal_snapshots through discovery.manager.PositionManager under a
set of Q3 take-profit/floor variants.

Use this when the question is: "for the trades we actually took, how would Q3
variants have managed them?"

Run:
  env/bin/python analysis/q3_sweep.py --days 15
"""
import argparse
import datetime as dt
import json
import os
import sqlite3
import statistics
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config  # noqa: E402
import discovery.manager as M  # noqa: E402

DB = ROOT / "scanner.db"
TRADES = ROOT / "discovery" / "trades.jsonl"
OUT = ROOT / "analysis" / "q3_sweep_results.json"


def variant(label, **overrides):
    base = {
        "tp_mode": "q3",
        "ladder": ((3.0, 0.50), (6.0, 0.95)),
        "floors": ((3.0, 1.50), (6.0, 3.00)),
        "q3_min": 2.0,
        "q3_atr": False,
        "q3_atr_k": 5.0,
        "q3_vp_buffer": 1.0,
        "fib": (2.618, 4.236),
    }
    base.update(overrides)
    return label, base


VARIANTS = [
    variant("Q3 live current 2.618/4.236 50/95 BE-only"),
    variant(
        "Q3 fib 2.618/4.236 50/80 BE-only",
        ladder=((3.0, 0.50), (6.0, 0.80)),
        fib=(2.618, 4.236),
    ),
    variant(
        "Q3 old fibs 50/95 BE-only",
        ladder=((3.0, 0.50), (6.0, 0.95)),
        fib=(1.272, 1.618, 2.0, 2.618, 4.236),
    ),
    variant(
        "Q3 live fibs 50/80 BE-only",
        ladder=((3.0, 0.50), (6.0, 0.80)),
    ),
    variant(
        "Q3 fib 2.618/4.236 40/80 BE-only",
        ladder=((3.0, 0.40), (6.0, 0.80)),
        fib=(2.618, 4.236),
    ),
    variant("Legacy 3x/6x current floors", tp_mode="legacy"),
]


def apply_variant(o):
    config.LATTICE_EXIT_TP_MODE = o["tp_mode"]
    config.LATTICE_EXIT_SCALE_OUT_LADDER = o["ladder"]
    config.LATTICE_EXIT_SCALE_STOP_FLOORS = o["floors"]
    config.LATTICE_Q3_MIN_TARGET_MULTIPLE = o["q3_min"]
    config.LATTICE_Q3_ATR_TRAIL_ENABLED = o["q3_atr"]
    config.LATTICE_Q3_ATR_TRAIL_K = o["q3_atr_k"]
    config.LATTICE_Q3_VP_FLOOR_BUFFER_PCT = o["q3_vp_buffer"]
    config.LATTICE_Q3_FIB_EXTENSIONS = o["fib"]
    M._NEW_MANAGER = M.PositionManager()


def f(x, default=0.0):
    try:
        return float(x) if x is not None else default
    except (TypeError, ValueError):
        return default


def load_trades(start_ts, include_manual=False):
    rows = []
    with TRADES.open() as fh:
        for line in fh:
            try:
                trade = json.loads(line)
            except json.JSONDecodeError:
                continue
            if f(trade.get("entry_ts")) < start_ts:
                continue
            if not include_manual and str(trade.get("reason")) == "manual_close_all":
                continue
            if f(trade.get("entry_price")) <= 0 or f(trade.get("cost_usd")) <= 0:
                continue
            rows.append(trade)
    return rows


def trade_day(trade):
    return dt.datetime.fromtimestamp(
        f(trade.get("entry_ts")), dt.timezone.utc
    ).date().isoformat()


def select_entry_dates(trades, requested_dates, worst_days, min_day_trades, scarce_rate):
    requested = set(requested_dates or [])
    if worst_days <= 0:
        return requested, []

    by_day = {}
    for trade in trades:
        by_day.setdefault(trade_day(trade), []).append(trade)

    daily = []
    for day, rows in sorted(by_day.items()):
        n = len(rows)
        if n < min_day_trades:
            continue
        pnl = sum(f(t.get("pnl_usd")) for t in rows)
        runners = sum(1 for t in rows if f(t.get("peak_mult")) >= 2.0)
        runner_rate = runners / max(n, 1)
        if runner_rate <= scarce_rate:
            daily.append({
                "day": day,
                "trades": n,
                "pnl_usd": round(pnl, 2),
                "runner_2x": runners,
                "runner_2x_rate_pct": round(runner_rate * 100, 1),
            })

    selected = sorted(daily, key=lambda d: d["pnl_usd"])[:worst_days]
    requested.update(d["day"] for d in selected)
    return requested, selected


def latest_signal_row(db, token, after_ts):
    row = db.execute(
        """
        SELECT *
        FROM signal_snapshots
        WHERE token_address = ?
          AND timestamp > ?
          AND price > 0
        ORDER BY timestamp DESC
        LIMIT 1
        """,
        (token, after_ts),
    ).fetchone()
    return dict(row) if row else None


def latest_candle_row(db, token, after_ts):
    row = db.execute(
        """
        SELECT
            bucket_start AS timestamp,
            close AS price,
            liquidity,
            volume_5m,
            open,
            high,
            low,
            close
        FROM token_candles
        WHERE token_address = ?
          AND timeframe_seconds = 60
          AND bucket_start > ?
          AND close > 0
        ORDER BY bucket_start DESC
        LIMIT 1
        """,
        (token, after_ts),
    ).fetchone()
    if not row:
        return None
    item = dict(row)
    item["_features_available"] = False
    return item


def maybe_append_latest_mark(db, token, path):
    if not path:
        return path
    last_ts = f(path[-1].get("timestamp"))
    if last_ts <= 0:
        return path
    latest = latest_signal_row(db, token, last_ts) or latest_candle_row(
        db, token, last_ts
    )
    if latest and f(latest.get("timestamp")) > last_ts:
        path.append(latest)
    return path


def load_paths(db, trades, now_ts, path_horizon_h):
    paths = []
    skipped = Counter()
    for trade in trades:
        token = trade.get("token")
        entry_ts = f(trade.get("entry_ts"))
        horizon_end = min(now_ts, entry_ts + path_horizon_h * 3600)
        rows = db.execute(
            """
            SELECT *
            FROM signal_snapshots
            WHERE token_address = ?
              AND timestamp >= ?
              AND timestamp <= ?
              AND price > 0
            ORDER BY timestamp ASC
            """,
            (token, entry_ts, horizon_end),
        ).fetchall()
        if rows:
            path = maybe_append_latest_mark(
                db,
                token,
                [dict(r) for r in rows],
            )
            paths.append((trade, path))
            continue

        candle_rows = db.execute(
            """
            SELECT
                bucket_start AS timestamp,
                close AS price,
                liquidity,
                volume_5m,
                open,
                high,
                low,
                close
            FROM token_candles
            WHERE token_address = ?
              AND timeframe_seconds = 60
              AND bucket_start >= ?
              AND bucket_start <= ?
              AND close > 0
            ORDER BY bucket_start ASC
            """,
            (token, entry_ts, horizon_end),
        ).fetchall()
        if candle_rows:
            skipped["used_candle_fallback"] += 1
            path = []
            for row in candle_rows:
                item = dict(row)
                item["_features_available"] = False
                path.append(item)
            path = maybe_append_latest_mark(db, token, path)
            paths.append((trade, path))
            continue

        skipped["no_price_path"] += 1
    return paths, skipped


def new_position(trade, first_row=None):
    entry_price = f(trade.get("entry_price"))
    cost = f(trade.get("cost_usd"), 20.0)
    liquidity = f((first_row or {}).get("liquidity")) or f(
        (first_row or {}).get("raw_liquidity")
    )
    return {
        "token": trade.get("token"),
        "symbol": trade.get("symbol", ""),
        "entry_ts": f(trade.get("entry_ts")),
        "entry_price": entry_price,
        "remaining": cost / entry_price,
        "peak": entry_price,
        "proceeds": 0.0,
        "scaled": False,
        "levels_done": set(),
        "conviction": f(trade.get("conviction")),
        "cost_usd": cost,
        "entry_liquidity": liquidity,
        "peak_liquidity": liquidity,
    }


def replay_one(trade, path, max_hold_s):
    pos = new_position(trade, path[0] if path else None)
    fills = []
    last_row = None
    for row in path:
        last_row = row
        price = f(row.get("price"))
        ts = f(row.get("timestamp"))
        features = row if row.get("_features_available", True) else None
        step_fills = M.manage(
            pos,
            price,
            ts,
            max_hold_s=max_hold_s,
            features=features,
        )
        fills.extend(step_fills)
        if pos.get("closed"):
            break

    if not pos.get("closed"):
        row = last_row or {}
        price = f(row.get("price"), pos["entry_price"])
        ts = f(row.get("timestamp"), pos["entry_ts"])
        qty = pos["remaining"]
        pos["remaining"] = 0.0
        pos["proceeds"] += qty * price
        pos["closed"] = True
        pos["reason"] = "open_at_end"
        fills.append(("open_at_end", qty, price))
    else:
        row = last_row or {}
        price = f(row.get("price"), pos["entry_price"])
        ts = f(row.get("timestamp"), pos["entry_ts"])

    pos["exit_ts"] = ts
    pos["exit_price"] = price
    pos["pnl_usd"] = pos["proceeds"] - pos["cost_usd"]
    pos["peak_mult"] = pos["peak"] / max(pos["entry_price"], 1e-18)
    pos["exit_mult"] = pos["exit_price"] / max(pos["entry_price"], 1e-18)
    pos["fills"] = [
        (kind, qty, fill_price)
        for kind, qty, fill_price in fills
    ]
    return pos


def summarize(rows):
    pnls = [f(r.get("pnl_usd")) for r in rows]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gross_w = sum(wins)
    gross_l = -sum(losses)
    reasons = Counter(r.get("reason") for r in rows)
    scaled = sum(1 for r in rows if r.get("scaled"))
    peak5 = [r for r in rows if f(r.get("peak_mult")) >= 5.0]
    amputated5 = [
        r for r in peak5
        if r.get("reason") == "scale_stop_floor" and f(r.get("exit_mult")) < 3.0
    ]
    return {
        "trades": len(rows),
        "win_rate_pct": round(100 * len(wins) / max(len(rows), 1), 1),
        "total_pnl_usd": round(sum(pnls), 2),
        "avg_pnl_usd": round(sum(pnls) / max(len(pnls), 1), 3),
        "median_pnl_usd": round(statistics.median(pnls), 3) if pnls else 0.0,
        "profit_factor": round(gross_w / gross_l, 3) if gross_l > 0 else None,
        "best_usd": round(max(pnls), 2) if pnls else 0.0,
        "worst_usd": round(min(pnls), 2) if pnls else 0.0,
        "n_reached_2x": sum(1 for r in rows if f(r.get("peak_mult")) >= 2.0),
        "n_reached_3x": sum(1 for r in rows if f(r.get("peak_mult")) >= 3.0),
        "n_reached_5x": len(peak5),
        "n_scaled": scaled,
        "n_open_at_end": reasons.get("open_at_end", 0),
        "n_5x_amputated_floor_lt3x": len(amputated5),
        "exit_breakdown": dict(reasons),
    }


def observed_summary(trades):
    rows = []
    for t in trades:
        if f(t.get("entry_price")) > 0 and f(t.get("exit_price")) > 0:
            t = dict(t)
            t["exit_mult"] = f(t.get("exit_price")) / f(t.get("entry_price"))
        rows.append(t)
    return summarize(rows)


def split_summary(rows, mid_ts):
    early = [r for r in rows if f(r.get("entry_ts")) < mid_ts]
    late = [r for r in rows if f(r.get("entry_ts")) >= mid_ts]
    return summarize(early), summarize(late)


def main():
    ap = argparse.ArgumentParser(description="Q3 sweep over actual paper entries")
    ap.add_argument("--days", type=float, default=15.0)
    ap.add_argument("--max-hold-h", type=float, default=None)
    ap.add_argument("--path-horizon-h", type=float, default=72.0)
    ap.add_argument("--include-manual", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument(
        "--entry-date",
        action="append",
        default=[],
        help="UTC entry date YYYY-MM-DD to include; repeat for multiple dates",
    )
    ap.add_argument(
        "--worst-runner-days",
        type=int,
        default=0,
        help="Select N worst observed-PnL days where 2x runners were scarce",
    )
    ap.add_argument("--min-day-trades", type=int, default=10)
    ap.add_argument("--scarce-runner-rate", type=float, default=0.10)
    args = ap.parse_args()

    db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    now = db.execute(
        "SELECT MAX(timestamp) FROM signal_snapshots WHERE price > 0"
    ).fetchone()[0]
    start = now - args.days * 86400
    max_hold_h = (
        args.max_hold_h
        if args.max_hold_h is not None
        else f(getattr(config, "LATTICE_MAX_HOLD_H", 12.0), 12.0)
    )
    max_hold_s = max_hold_h * 3600 if max_hold_h else None

    trades = load_trades(start, include_manual=args.include_manual)
    requested_dates, selected_worst_days = select_entry_dates(
        trades,
        args.entry_date,
        args.worst_runner_days,
        args.min_day_trades,
        args.scarce_runner_rate,
    )
    if requested_dates:
        trades = [t for t in trades if trade_day(t) in requested_dates]
    if args.limit:
        trades = trades[:args.limit]
    if selected_worst_days:
        print(
            "selected worst runner-scarce days: "
            + ", ".join(
                f"{d['day']} pnl=${d['pnl_usd']:+.2f} "
                f"2x={d['runner_2x']}/{d['trades']} "
                f"({d['runner_2x_rate_pct']}%)"
                for d in selected_worst_days
            ),
            flush=True,
        )
    print(
        f"loading price paths for {len(trades)} ledger entries "
        f"(horizon={args.path_horizon_h:g}h)...",
        flush=True,
    )
    paths, skipped = load_paths(db, trades, now, args.path_horizon_h)
    db.close()

    observed = observed_summary([trade for trade, _ in paths])
    print(
        f"Q3 sweep actual entries: days={args.days:g} "
        f"entries={len(trades)} replayable={len(paths)} skipped={dict(skipped)} "
        f"max_hold_h={max_hold_h:g}",
        flush=True,
    )
    print(
        f"Observed ledger          trades {observed['trades']:4d} "
        f"win {observed['win_rate_pct']:5.1f}% "
        f"total ${observed['total_pnl_usd']:+9.2f} "
        f"PF {observed['profit_factor']} "
        f"2x {observed['n_reached_2x']:3d} 5x {observed['n_reached_5x']:3d}",
        flush=True,
    )

    results = []
    base = None
    mid_ts = start + (now - start) / 2.0
    for i, (label, opts) in enumerate(VARIANTS, 1):
        apply_variant(opts)
        t0 = time.time()
        rows = [
            replay_one(trade, path, max_hold_s)
            for trade, path in paths
        ]
        summary = summarize(rows)
        early, late = split_summary(rows, mid_ts)
        if base is None:
            base = summary
        results.append({
            "label": label,
            "options": opts,
            "summary": summary,
            "early": early,
            "late": late,
            "top_trades": sorted(
                [
                    {
                        "symbol": r.get("symbol"),
                        "token": r.get("token"),
                        "entry_ts": r.get("entry_ts"),
                        "pnl_usd": round(f(r.get("pnl_usd")), 2),
                        "peak_mult": round(f(r.get("peak_mult")), 3),
                        "exit_mult": round(f(r.get("exit_mult")), 3),
                        "reason": r.get("reason"),
                    }
                    for r in rows
                ],
                key=lambda r: -r["pnl_usd"],
            )[:10],
        })
        print(
            f"[{i:02d}/{len(VARIANTS)} {time.time() - t0:5.1f}s] "
            f"{label:28s} trades {summary['trades']:4d} "
            f"win {summary['win_rate_pct']:5.1f}% "
            f"total ${summary['total_pnl_usd']:+9.2f} "
            f"PF {summary['profit_factor']} "
            f"best ${summary['best_usd']:+7.1f} worst ${summary['worst_usd']:+6.1f} "
            f"2x {summary['n_reached_2x']:3d} 5x {summary['n_reached_5x']:3d} "
            f"scaled {summary['n_scaled']:3d} "
            f"5x_floor<3x {summary['n_5x_amputated_floor_lt3x']:3d}",
            flush=True,
        )

    print("\nDelta vs current Q3:")
    for item in results:
        s = item["summary"]
        print(
            f"  {item['label']:28s} "
            f"dPnL ${s['total_pnl_usd'] - base['total_pnl_usd']:+9.2f} "
            f"dWin {s['win_rate_pct'] - base['win_rate_pct']:+5.1f}pp "
            f"d2x {s['n_reached_2x'] - base['n_reached_2x']:+4d} "
            f"d5x {s['n_reached_5x'] - base['n_reached_5x']:+4d} "
            f"total ${s['total_pnl_usd']:+9.2f}",
            flush=True,
        )

    print("\nWalk-forward split (first half tune / second half holdout):")
    print(f"{'variant':28s} {'early$':>10s} {'late$':>10s} {'late2x':>7s}")
    for item in results:
        print(
            f"{item['label']:28s} "
            f"{item['early']['total_pnl_usd']:>+10.2f} "
            f"{item['late']['total_pnl_usd']:>+10.2f} "
            f"{item['late']['n_reached_2x']:>7d}",
            flush=True,
        )

    payload = {
        "params": {
            "days": args.days,
            "start_ts": start,
            "end_ts": now,
            "max_hold_h": max_hold_h,
            "path_horizon_h": args.path_horizon_h,
            "entry_dates": sorted(requested_dates),
            "worst_runner_days": selected_worst_days,
            "min_day_trades": args.min_day_trades,
            "scarce_runner_rate": args.scarce_runner_rate,
            "entries": len(trades),
            "replayable": len(paths),
            "skipped": dict(skipped),
        },
        "observed": observed,
        "results": results,
    }
    OUT.write_text(json.dumps(payload, indent=2, default=list) + "\n")
    print(f"\nwrote {OUT.relative_to(ROOT)}", flush=True)
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
