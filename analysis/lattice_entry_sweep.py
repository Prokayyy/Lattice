#!/usr/bin/env python3
"""Sweep the participation-blind Lattice entry floor over full snapshot history.

The live runner currently constructs ConvictionPipeline without a participation
provider, so this replay deliberately scores the same three axes as the live
floor: flow, liquidity, and structure.  All other entry and exit settings stay
at their current configured values; only the Lattice floor changes.

The hot and archive SQLite databases are read through storage.history.  No
multi-gigabyte analysis cache is created.
"""

import argparse
import datetime
import json
import statistics
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config  # noqa: E402
import discovery.manager as manager  # noqa: E402
from discovery import features as feature_vector  # noqa: E402
from discovery.lattice import lattice_verdict  # noqa: E402
from discovery.pipeline import ConvictionPipeline  # noqa: E402
from storage.history import open_history  # noqa: E402


DEFAULT_FLOORS = (
    0.00, 0.45, 0.50, 0.55, 0.60, 0.65, 0.68, 0.70, 0.72,
    0.74, 0.76, 0.78, 0.80, 0.82, 0.85, 0.90,
)


def number(row, key, default=0.0):
    try:
        value = row.get(key)
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def install_history_candles(con):
    """Keep Q3 candle reads historical and look-ahead safe across hot+archive."""
    cache = {}
    cap = 20_000

    def recent(address, as_of_ts=None):
        period = int(config.POSITION_ATR_STOP_PERIOD)
        timeframe = int(config.POSITION_ATR_STOP_TIMEFRAME_SECONDS)
        limit = period * 4 + 10
        bucket = None if as_of_ts is None else int(float(as_of_ts) // timeframe)
        key = (str(address), timeframe, bucket)
        if key in cache:
            return cache[key]
        if as_of_ts is None:
            rows = con.execute(
                "SELECT bucket_start, high, low, close FROM token_candles_all "
                "WHERE token_address=? AND timeframe_seconds=? "
                "ORDER BY bucket_start DESC LIMIT ?",
                (str(address), timeframe, limit),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT bucket_start, high, low, close FROM token_candles_all "
                "WHERE token_address=? AND timeframe_seconds=? AND bucket_start<=? "
                "ORDER BY bucket_start DESC LIMIT ?",
                (str(address), timeframe, float(as_of_ts), limit),
            ).fetchall()
        value = [
            {"bucket_start": r[0], "high": r[1], "low": r[2], "close": r[3]}
            for r in reversed(rows)
            if r[1] and r[2] and r[3]
        ]
        if len(cache) >= cap:
            for old in list(cache)[: cap // 4]:
                del cache[old]
        cache[key] = value
        return value

    manager._recent_candles_for_atr = recent


def eligible(pipe, row, max_pc1h, max_pc24h, min_conviction):
    if not pipe.universe(row):
        return None
    if max_pc1h and number(row, "price_change_1h") > max_pc1h:
        return None
    if max_pc24h and number(row, "price_change_24h") > max_pc24h:
        return None
    verdict = lattice_verdict(row, participation=None,
                              liquidity_change_pct=row.get("liquidity_change_pct"))
    if not verdict["passed"]:
        return None
    conviction = 0.0
    if pipe.model is not None:
        conviction = pipe.model.proba(feature_vector.extract(row, participation=None))
    if conviction < min_conviction:
        return None
    return float(verdict["composite"]), float(conviction)


def book(start_cash):
    return {
        "cash": start_cash,
        "open": {},
        "cooldown": {},
        "closed": [],
        "qualified": 0,
        "skipped_cash": 0,
    }


def close_position(state, token, pos, ts, price, size_usd, cooldown_s):
    pos["exit_ts"] = ts
    pos["exit_price"] = price
    pos["pnl_usd"] = pos["proceeds"] - size_usd
    pos["peak_mult"] = pos["peak"] / pos["entry_price"]
    state["closed"].append(pos)
    del state["open"][token]
    state["cooldown"][token] = ts + cooldown_s


def summarize(trades, split_ts):
    def one(rows):
        pnls = [float(t["pnl_usd"]) for t in rows]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        gross_loss = -sum(losses)
        return {
            "trades": len(rows),
            "win_rate_pct": round(100 * len(wins) / max(len(rows), 1), 2),
            "total_pnl_usd": round(sum(pnls), 2),
            "mean_pnl_usd": round(statistics.fmean(pnls), 3) if pnls else 0.0,
            "profit_factor": round(sum(wins) / gross_loss, 3) if gross_loss else None,
            "reached_2x": sum(float(t.get("peak_mult") or 0) >= 2.0 for t in rows),
            "runner_rate_pct": round(
                100 * sum(float(t.get("peak_mult") or 0) >= 2.0 for t in rows)
                / max(len(rows), 1), 2
            ),
            "best_usd": round(max(pnls), 2) if pnls else 0.0,
            "worst_usd": round(min(pnls), 2) if pnls else 0.0,
        }

    daily = {}
    for trade in trades:
        day = datetime.datetime.fromtimestamp(
            float(trade["entry_ts"]), datetime.timezone.utc
        ).strftime("%Y-%m-%d")
        daily.setdefault(day, []).append(trade)

    return {
        "all": one(trades),
        "first_half": one([t for t in trades if t["entry_ts"] < split_ts]),
        "second_half": one([t for t in trades if t["entry_ts"] >= split_ts]),
        "daily": {day: one(rows) for day, rows in sorted(daily.items())},
        "exit_breakdown": dict(Counter(str(t.get("reason") or "unknown") for t in trades)),
    }


def run(args):
    floors = tuple(sorted(set(args.floors)))
    con = open_history()
    con.row_factory = __import__("sqlite3").Row
    install_history_candles(con)

    end_ts = con.execute(
        "SELECT MAX(timestamp) FROM signal_snapshots_all WHERE price>0"
    ).fetchone()[0]
    start_ts = end_ts - args.days * 86400
    split_ts = start_ts + (end_ts - start_ts) / 2

    max_pc1h = float(getattr(config, "LATTICE_MAX_ENTRY_PRICE_CHANGE_1H", 0.0) or 0.0)
    max_pc24h = float(getattr(config, "LATTICE_MAX_ENTRY_PRICE_CHANGE_24H", 0.0) or 0.0)
    size_usd = float(getattr(config, "POSITION_POSITION_SIZE_USD", 20.0) or 20.0)
    configured_cash = (
        float(getattr(config, "POSITION_INITIAL_BALANCE_SOL", 100.0) or 100.0)
        * float(getattr(config, "POSITION_SOL_USD", 150.0) or 150.0)
    )
    start_cash = args.start_cash_usd or configured_cash
    cooldown_s = args.cooldown_h * 3600
    max_hold_s = args.max_hold_h * 3600 if args.max_hold_h else None
    pipe = ConvictionPipeline(
        min_conviction=args.min_conviction,
        min_lattice=0.0,
        max_price_change_1h=max_pc1h,
        max_price_change_24h=max_pc24h,
    )
    states = {floor: book(start_cash) for floor in floors}
    last_tick = {}
    scanned = base_eligible = 0
    started = time.time()

    query = (
        "SELECT * FROM signal_snapshots_all WHERE price>0 "
        "AND price_change_5m IS NOT NULL AND timestamp>=? ORDER BY timestamp"
    )
    for sql_row in con.execute(query, (start_ts,)):
        scanned += 1
        row = dict(sql_row)
        token = row.get("token_address") or ""
        price = number(row, "price")
        ts = number(row, "timestamp")
        if not token or price <= 0:
            continue
        last_tick[token] = (ts, price)
        if args.progress_every and scanned % args.progress_every == 0:
            print(
                f"scanned {scanned:,} rows in {time.time() - started:.1f}s; "
                f"base eligible {base_eligible:,}",
                flush=True,
            )

        can_enter = []
        for floor, state in states.items():
            pos = state["open"].get(token)
            if pos is not None:
                fills = manager.manage(
                    pos, price, ts, max_hold_s=max_hold_s, features=row, engine="new"
                )
                for _, qty, fill_price in fills:
                    state["cash"] += qty * fill_price
                if pos.get("closed"):
                    close_position(
                        state, token, pos, ts, price, size_usd, cooldown_s
                    )
                continue
            if ts >= state["cooldown"].get(token, 0):
                can_enter.append((floor, state))

        if not can_enter:
            continue
        scored = eligible(pipe, row, max_pc1h, max_pc24h, args.min_conviction)
        if scored is None:
            continue
        base_eligible += 1
        lattice, conviction = scored
        for floor, state in can_enter:
            if lattice + 1e-12 < floor:
                continue
            state["qualified"] += 1
            if state["cash"] < size_usd:
                state["skipped_cash"] += 1
                continue
            state["cash"] -= size_usd
            state["open"][token] = {
                "token": token,
                "symbol": row.get("symbol", ""),
                "entry_ts": ts,
                "entry_price": price,
                "remaining": size_usd / price,
                "peak": price,
                "proceeds": 0.0,
                "scaled": False,
                "levels_done": set(),
                "conviction": conviction,
                "lattice": lattice,
                "participation_blind": True,
                "cost_usd": size_usd,
                "entry_liquidity": number(row, "liquidity") or number(row, "raw_liquidity"),
                "peak_liquidity": number(row, "liquidity") or number(row, "raw_liquidity"),
            }

    for state in states.values():
        for token, pos in list(state["open"].items()):
            ts, price = last_tick.get(token, (pos["entry_ts"], pos["entry_price"]))
            state["cash"] += pos["remaining"] * price
            pos["proceeds"] += pos["remaining"] * price
            pos["remaining"] = 0.0
            pos["closed"] = True
            pos["reason"] = "open_at_end"
            close_position(state, token, pos, ts, price, size_usd, cooldown_s)

    results = []
    for floor, state in states.items():
        result = summarize(state["closed"], split_ts)
        result.update({
            "floor": floor,
            "qualified_entries": state["qualified"],
            "skipped_cash": state["skipped_cash"],
        })
        results.append(result)

    payload = {
        "generated_at": time.time(),
        "method": "full-history replay; participation-blind live Lattice axes",
        "params": {
            "days": args.days,
            "start_ts": start_ts,
            "end_ts": end_ts,
            "split_ts": split_ts,
            "min_conviction": args.min_conviction,
            "cooldown_h": args.cooldown_h,
            "max_hold_h": args.max_hold_h,
            "max_price_change_1h": max_pc1h,
            "max_price_change_24h": max_pc24h,
            "size_usd": size_usd,
            "configured_start_cash_usd": configured_cash,
            "start_cash_usd": start_cash,
            "floors": floors,
        },
        "counts": {
            "snapshots_scanned": scanned,
            "base_eligible_evaluations": base_eligible,
        },
        "results": results,
    }
    con.close()
    Path(args.out).write_text(json.dumps(payload, indent=2, default=str))
    print(f"wrote {args.out}")
    print("floor  trades   win%       pnl    mean     PF   2x% | first pnl  second pnl")
    for result in results:
        all_ = result["all"]
        print(
            f"{result['floor']:>5.2f} {all_['trades']:>7} {all_['win_rate_pct']:>6.1f} "
            f"{all_['total_pnl_usd']:>9.2f} {all_['mean_pnl_usd']:>7.3f} "
            f"{str(all_['profit_factor']):>6} {all_['runner_rate_pct']:>5.1f} | "
            f"{result['first_half']['total_pnl_usd']:>9.2f} "
            f"{result['second_half']['total_pnl_usd']:>10.2f}"
        )
    return payload


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=float, default=20.0)
    parser.add_argument("--min-conviction", type=float, default=0.18)
    parser.add_argument("--cooldown-h", type=float, default=6.0)
    parser.add_argument(
        "--max-hold-h",
        type=float,
        default=float(getattr(config, "LATTICE_MAX_HOLD_H", 12.0) or 12.0),
    )
    parser.add_argument("--floors", type=float, nargs="+", default=DEFAULT_FLOORS)
    parser.add_argument("--progress-every", type=int, default=100_000)
    parser.add_argument(
        "--start-cash-usd", type=float, default=0.0,
        help="paper bank for the replay; 0 uses the configured bank",
    )
    parser.add_argument(
        "--out", default=str(ROOT / "analysis" / "lattice_entry_sweep_results.json")
    )
    args = parser.parse_args()
    run(args)


if __name__ == "__main__":
    main()
