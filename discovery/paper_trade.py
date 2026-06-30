"""Paper-trading simulator for the discovery pipeline (REPLAY / backtest mode).

The discovery layer only EMITS alerts; it has no executor. This harness puts a
simulated executor on top so you can SEE how the new scanner's picks would play
out. It replays `signal_snapshots` chronologically, opens a paper position on
each pipeline EntryAlert, manages it with the live bot's exit rules, and books
PnL against a simulated wallet.

Balance + entry size are COPIED from the main bot's config:
  wallet   = POSITION_INITIAL_BALANCE_SOL  (100.0 SOL)
  per-trade= POSITION_POSITION_SIZE_USD    ($20, fixed-USD sizing)

Exit rules mirrored from config:
  initial stop = LATTICE_EXIT_INITIAL_STOP_PCT before any scale-out
  take profit  = LATTICE_EXIT_TP_MODE=tail by default:
                 recover cost near 2x, then sell configured fractions of the
                 remaining position at later multiples. Q3/fixed ladders remain
                 available through discovery.manager for rollback/replay.
  stop floors  = LATTICE_EXIT_SCALE_STOP_FLOORS
                 ((3.0,1.5),(6.0,3.0)).
                 LATTICE_MOONBAG_STEP_FLOORS then moves the stop to 10x at
                 20x, 20x at 30x, and so on in 10x steps.
                 Trailing stops are disabled in the active Lattice engine;
                 the remaining bag is a protected moonbag.
  max hold     = LATTICE_MAX_HOLD_H for stale positions; positions that
                 touched LATTICE_MAX_HOLD_PARTIAL_RUNNER_MULTIPLE and remain
                 above entry get LATTICE_MAX_HOLD_PARTIAL_RUNNER_H; positions
                 that touched LATTICE_MAX_HOLD_EXEMPT_MULTIPLE are exempt.

This is REPLAY paper trading on real recorded price paths. It places NO real
orders and is NOT wired to live execution. Granularity = one price per snapshot
(no intrabar high/low).

Run:  env/bin/python -m discovery.paper_trade --days 3 --min-conviction 0.18
"""
import argparse, json, os, sqlite3, time
import config
from discovery.manager import (
    PositionManager,
    _max_hold_close_due,
    manage as manager_manage,
)
from discovery.pipeline import ConvictionPipeline

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "scanner.db")

SIZE_USD    = float(getattr(config, "POSITION_POSITION_SIZE_USD", 20) or 20)
BALANCE_SOL = float(getattr(config, "POSITION_INITIAL_BALANCE_SOL", 1.0) or 1.0)
SOL_USD     = float(getattr(config, "POSITION_SOL_USD", 0) or 0) or 150.0
DEFAULT_STOP = float(
    getattr(
        config,
        "LATTICE_EXIT_INITIAL_STOP_PCT",
        getattr(config, "POSITION_INITIAL_STOP_LOSS_PCT", 0.30),
    )
    or 0.30
)
TRAIL_PCT    = float(getattr(config, "POSITION_RUNNER_RELAXED_TRAIL_PCT", 0.25) or 0.25)
HIGH_TRAIL_TRIGGER = float(getattr(config, "POSITION_HIGH_MULT_TRAIL_TRIGGER", 4.0) or 4.0)
HIGH_TRAIL_PCT = float(getattr(config, "POSITION_HIGH_MULT_TRAIL_PCT", 0.50) or 0.50)
MAX_HOLD_EXEMPT_MULT = float(getattr(config, "LATTICE_MAX_HOLD_EXEMPT_MULTIPLE", 2.0) or 2.0)
MAX_HOLD_PARTIAL_RUNNER_MULT = float(
    getattr(config, "LATTICE_MAX_HOLD_PARTIAL_RUNNER_MULTIPLE", 1.5)
    or 0.0
)
MAX_HOLD_PARTIAL_RUNNER_S = (
    float(getattr(config, "LATTICE_MAX_HOLD_PARTIAL_RUNNER_H", 24.0) or 0.0)
    * 3600
)
MAX_HOLD_PARTIAL_RUNNER_REQUIRE_PROFIT = bool(
    getattr(config, "LATTICE_MAX_HOLD_PARTIAL_RUNNER_REQUIRE_PROFIT", True)
)
DEFAULT_MAX_HOLD_H = float(getattr(config, "LATTICE_MAX_HOLD_H", 12.0) or 0.0)
LADDER = sorted([(float(m), float(f)) for m, f in
                 getattr(config, "POSITION_SCALE_OUT_LADDER", ((2.0, 0.50), (4.0, 0.60)))])
ARM_MULT = LADDER[0][0] if LADDER else 2.0   # trailing arms after this multiple


def f(row, k, d=0.0):
    try:
        v = row.get(k); return float(v) if v is not None else d
    except (TypeError, ValueError):
        return d


def manage(pos, price, ts, max_hold_s=None):
    return manager_manage(pos, price, ts, max_hold_s=max_hold_s)


def manage_with_features(pos, price, ts, max_hold_s=None, features=None, engine=None):
    return manager_manage(
        pos,
        price,
        ts,
        max_hold_s=max_hold_s,
        features=features,
        engine=engine,
    )


def old_manage(pos, price, ts, max_hold_s=None):
    """Apply exits to an open position at (price, ts). Mutates pos; returns a
    list of (kind, tokens_sold, price) fills and sets pos['closed']/'reason'."""
    fills = []
    ep = pos["entry_price"]
    pos["peak"] = max(pos["peak"], price)
    mult = price / ep if ep > 0 else 0.0
    peak_mult = pos["peak"] / ep if ep > 0 else 0.0

    # 1) initial catastrophe stop, only while unscaled
    if not pos["scaled"] and price <= ep * (1 - DEFAULT_STOP):
        q = pos["remaining"]; pos["remaining"] = 0.0
        pos["proceeds"] += q * price; fills.append(("initial_stop", q, price))
        pos["closed"] = True; pos["reason"] = "initial_stop"; return fills

    # 2) scale ladder (ascending), each level once. Ladder values are cumulative
    # sold targets, matching the main bot. Example: 2x:0.50, 4x:0.60 sells
    # 50% at 2x, then 10% of original (=20% of the remaining bag) at 4x.
    initial_tokens = pos["cost_usd"] / ep if ep > 0 else 0.0
    for lvl, target in LADDER:
        if mult >= lvl and lvl not in pos["levels_done"] and pos["remaining"] > 0:
            current_sold = (
                1.0 - pos["remaining"] / initial_tokens
                if initial_tokens > 0
                else 0.0
            )
            q = initial_tokens * max(target - current_sold, 0.0)
            q = min(q, pos["remaining"])
            if q <= 0:
                pos["levels_done"].add(lvl)
                continue
            pos["remaining"] -= q; pos["proceeds"] += q * price
            pos["levels_done"].add(lvl); pos["scaled"] = True
            fills.append((f"scale_{lvl:g}x", q, price))

    # 3) trailing stop, active only after first scale (2x printed)
    trail_pct = HIGH_TRAIL_PCT if peak_mult >= HIGH_TRAIL_TRIGGER else TRAIL_PCT
    if pos["scaled"] and pos["remaining"] > 0 and price <= pos["peak"] * (1 - trail_pct):
        q = pos["remaining"]; pos["remaining"] = 0.0
        pos["proceeds"] += q * price; fills.append(("trailing_stop", q, price))
        pos["closed"] = True; pos["reason"] = "trailing_stop"

    # max-hold time stop: recycle stagnant positions, give partial runners
    # extra time, and fully exempt anything that already printed runner status.
    if _max_hold_close_due(
        pos,
        ts,
        max_hold_s,
        mult,
        peak_mult,
        MAX_HOLD_EXEMPT_MULT,
        MAX_HOLD_PARTIAL_RUNNER_MULT,
        MAX_HOLD_PARTIAL_RUNNER_S,
        MAX_HOLD_PARTIAL_RUNNER_REQUIRE_PROFIT,
    ):
        q = pos["remaining"]; pos["remaining"] = 0.0
        pos["proceeds"] += q * price; fills.append(("max_hold", q, price))
        pos["closed"] = True; pos["reason"] = "max_hold"

    if pos["remaining"] <= 1e-12 and not pos.get("closed"):
        pos["closed"] = True; pos["reason"] = pos.get("reason") or "scaled_out"
    return fills


def run(days, min_conviction, cooldown_h, max_hold_h=DEFAULT_MAX_HOLD_H, limit=None, quiet=False,
        engine=None):
    db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True); db.row_factory = sqlite3.Row
    now = db.execute("SELECT MAX(timestamp) m FROM signal_snapshots WHERE price>0").fetchone()["m"]
    start = now - days * 86400
    pipe = ConvictionPipeline(min_conviction=min_conviction)

    cash = BALANCE_SOL * SOL_USD
    start_cash = cash
    open_pos = {}           # token -> pos
    cooldown = {}           # token -> ts allowed to re-enter
    closed = []
    n_seen = 0; n_alerts = 0; n_skipped_cash = 0; n_skipped_open = 0; n_cooldown = 0

    q = ("SELECT * FROM signal_snapshots WHERE price>0 AND price_change_5m IS NOT NULL "
         "AND timestamp>=? ORDER BY timestamp ASC")
    cur = db.execute(q, (start,))
    for r in cur:
        n_seen += 1
        if limit and n_seen > limit: break
        row = dict(r)
        token = row.get("token_address") or row.get("address") or ""
        price = f(row, "price"); ts = f(row, "timestamp")
        if not token or price <= 0:
            continue

        # (a) manage an open position for this token on its own price update
        pos = open_pos.get(token)
        if pos is not None:
            fills = manage_with_features(
                pos,
                price,
                ts,
                max_hold_s=max_hold_h * 3600 if max_hold_h else None,
                features=row,
                engine=engine,
            )
            for kind, qty, p in fills:
                cash += qty * p
            if pos.get("closed"):
                pos["exit_ts"] = ts; pos["exit_price"] = price
                pos["pnl_usd"] = pos["proceeds"] - SIZE_USD
                pos["peak_mult"] = pos["peak"] / pos["entry_price"]
                closed.append(pos); del open_pos[token]
                cooldown[token] = ts + cooldown_h * 3600
            continue   # never enter the same token on a tick we're managing it

        # (b) entry scan -- cheap universe gate first, then full pipeline
        if ts < cooldown.get(token, 0):
            continue
        if f(row, "price_change_5m") <= 2.0 or f(row, "volume_1h") <= 0:
            continue
        alert, reason = pipe.evaluate(row)
        if alert is None:
            continue
        n_alerts += 1
        if cash < SIZE_USD:
            n_skipped_cash += 1; continue
        cash -= SIZE_USD
        open_pos[token] = {
            "token": token, "symbol": row.get("symbol", ""),
            "entry_ts": ts, "entry_price": price,
            "remaining": SIZE_USD / price, "peak": price,
            "proceeds": 0.0, "scaled": False, "levels_done": set(),
            "conviction": alert.conviction, "revival_score": alert.revival_score,
            "participation_blind": alert.participation_blind,
            "cost_usd": SIZE_USD,
            "entry_liquidity": f(row, "liquidity") or f(row, "raw_liquidity"),
            "peak_liquidity": f(row, "liquidity") or f(row, "raw_liquidity"),
        }

    # close any still-open at last seen price (re-query each token's latest tick)
    for token, pos in list(open_pos.items()):
        lr = db.execute("SELECT price, timestamp FROM signal_snapshots WHERE token_address=? "
                        "AND timestamp>=? AND price>0 ORDER BY timestamp DESC LIMIT 1",
                        (token, pos["entry_ts"])).fetchone()
        lp = float(lr["price"]) if lr else pos["entry_price"]
        lts = float(lr["timestamp"]) if lr else pos["entry_ts"]
        pos["proceeds"] += pos["remaining"] * lp; cash += pos["remaining"] * lp
        pos["remaining"] = 0.0; pos["closed"] = True; pos["reason"] = "open_at_end"
        pos["exit_ts"] = lts; pos["exit_price"] = lp
        pos["pnl_usd"] = pos["proceeds"] - SIZE_USD
        pos["peak_mult"] = pos["peak"] / pos["entry_price"]
        closed.append(pos)

    # ---- summary ----
    manager = PositionManager()
    pnls = [c["pnl_usd"] for c in closed]
    wins = [p for p in pnls if p > 0]; losses = [p for p in pnls if p <= 0]
    gross_w = sum(wins); gross_l = -sum(losses)
    out = {
        "params": {"days": days, "min_conviction": min_conviction, "cooldown_h": cooldown_h,
                   "engine": engine or getattr(config, "LATTICE_EXIT_ENGINE", "new"),
                   "size_usd": SIZE_USD, "balance_sol": BALANCE_SOL, "sol_usd": SOL_USD,
                   "start_cash_usd": round(start_cash, 2),
                   "initial_stop": manager.initial_stop_pct,
                   "trail_pct": manager.trail_pct,
                   "ladder": manager.ladder,
                   "scale_stop_floors": manager.scale_stop_floors,
                   "moonbag_step_floors_enabled": manager.moonbag_step_floors_enabled,
                   "moonbag_step_trigger_mult": manager.moonbag_step_trigger_mult,
                   "moonbag_step_interval_mult": manager.moonbag_step_interval_mult,
                   "moonbag_step_floor_lag_mult": manager.moonbag_step_floor_lag_mult},
        "counts": {"snapshots_seen": n_seen, "alerts_taken": len(closed),
                   "alerts_total": n_alerts, "skipped_no_cash": n_skipped_cash},
        "result": {
            "trades": len(closed),
            "win_rate_pct": round(100 * len(wins) / max(len(closed), 1), 1),
            "total_pnl_usd": round(sum(pnls), 2),
            "ending_balance_usd": round(cash, 2),
            "return_pct": round(100 * (cash - start_cash) / max(start_cash, 1), 1),
            "profit_factor": round(gross_w / gross_l, 3) if gross_l > 0 else None,
            "best_usd": round(max(pnls), 2) if pnls else 0,
            "worst_usd": round(min(pnls), 2) if pnls else 0,
            "n_reached_2x": sum(1 for c in closed if c["peak_mult"] >= 2.0),
            "still_open_at_end": sum(1 for c in closed if c["reason"] == "open_at_end"),
        },
        "exit_breakdown": {},
        "top_trades": [],
    }
    for c in closed:
        out["exit_breakdown"][c["reason"]] = out["exit_breakdown"].get(c["reason"], 0) + 1
    for c in sorted(closed, key=lambda x: -x["pnl_usd"])[:8]:
        out["top_trades"].append({"symbol": c["symbol"], "conviction": round(c["conviction"], 3),
                                  "peak_mult": round(c["peak_mult"], 2), "reason": c["reason"],
                                  "pnl_usd": round(c["pnl_usd"], 2)})
    json.dump({"summary": out, "ledger": [
        {k: (round(v, 6) if isinstance(v, float) else (list(v) if isinstance(v, set) else v))
         for k, v in c.items() if k != "levels_done"} for c in closed]},
        open(os.path.join(os.path.dirname(__file__), "paper_results.json"), "w"),
        indent=2, default=str)

    if not quiet:
        p = out["params"]; res = out["result"]; cnt = out["counts"]
        print(f"=== discovery paper trade (REPLAY, last {days}d) ===")
        print(f"wallet {p['balance_sol']} SOL x ${p['sol_usd']:.0f} = ${p['start_cash_usd']} start | "
              f"size ${p['size_usd']:.0f} | min_conviction {p['min_conviction']} | cooldown {cooldown_h}h")
        print(f"scanned {cnt['snapshots_seen']} snaps -> {cnt['alerts_total']} alerts, "
              f"{cnt['alerts_total']-cnt['skipped_no_cash']} taken ({cnt['skipped_no_cash']} skipped: no cash)")
        print(f"--- result ---")
        print(f"  trades            {res['trades']}")
        print(f"  win rate          {res['win_rate_pct']}%")
        print(f"  total PnL         ${res['total_pnl_usd']}")
        print(f"  ending balance    ${res['ending_balance_usd']}  ({res['return_pct']:+}% )")
        print(f"  profit factor     {res['profit_factor']}")
        print(f"  best / worst      ${res['best_usd']} / ${res['worst_usd']}")
        print(f"  reached 2x        {res['n_reached_2x']}   still-open@end {res['still_open_at_end']}")
        print(f"  exit breakdown    {out['exit_breakdown']}")
        print(f"  top trades:")
        for t in out["top_trades"]:
            print(f"    {t['symbol']:>10}  conv {t['conviction']}  peak {t['peak_mult']}x  "
                  f"{t['reason']:<14} ${t['pnl_usd']}")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=3)
    ap.add_argument("--min-conviction", type=float, default=0.18)
    ap.add_argument("--cooldown-h", type=float, default=6)
    ap.add_argument("--max-hold-h", type=float, default=DEFAULT_MAX_HOLD_H)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--engine", choices=("old", "new"), default=None)
    args = ap.parse_args()
    run(
        args.days,
        args.min_conviction,
        args.cooldown_h,
        max_hold_h=args.max_hold_h,
        limit=args.limit,
        engine=args.engine,
    )
