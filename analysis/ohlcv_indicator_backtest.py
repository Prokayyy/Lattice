"""Trade-anchored, blind-controlled backtest of the 5 OHLCV indicators.

Methodology (matches the house standard that killed RSI-cross-up and Kaufman ER):
  - Anchor to REAL closed trades in discovery/trades.jsonl (no synthetic replays).
  - Pull 60s OHLC + per-candle liquidity from the local token_candles table
    (offline, deterministic -- no API).
  - Build each trade's candle path from a pre-entry context window through its
    real exit, then simulate what each indicator would have done.
  - EVERY exit rule is judged against a BLIND CONTROL: exiting the same fired
    trades at a *random* bar (matched aggressiveness). An exit rule that cannot
    beat a coin-flip exit on the same trades has no timing edge -- it is just
    "exit sooner on a winner-poor slice" (the trap ER fell into).
  - Liquidity-at-entry indicators (impact, spread) are judged as ENTRY filters:
    do worse-liquidity entries actually lose more, vs a random filter removing
    the same count?

Single-lot PnL model (consistent across all arms so deltas are clean):
  pnl(exit_price) = cost_usd * (exit_price/entry_price - 1) - cost_usd*FEE
  Returns are anchored to the ledger entry_price; alternative exits use candle
  closes. The "actual (candle)" arm holds to the last in-window candle, giving an
  apples-to-apples baseline for the candle-based indicator arms; the ledger's own
  realized pnl is printed alongside for context.

Usage:
  python3 analysis/ohlcv_indicator_backtest.py
  python3 analysis/ohlcv_indicator_backtest.py --limit 300 --fee 0.01 --seed 7
"""
import argparse
import json
import math
import os
import random
import sqlite3
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from trading.ohlcv_indicators import (   # noqa: E402
    illiquidity_spike, price_impact,
    money_flow_divergence,
    simulate_chandelier,
    spread_state, corwin_schultz_spread,
    volume_climax,
)

DB_PATH = os.path.join(ROOT, "scanner.db")
TRADES = os.path.join(ROOT, "discovery", "trades.jsonl")
TF = 60                       # 1-minute candles
PRE_CTX = 30                  # pre-entry candles for indicator history
MIN_EXIT_BAR = 3             # don't let any rule exit in the first N bars
RAND_DRAWS = 25              # blind-control random exits per fired trade


# --------------------------------------------------------------------------- #
# data
# --------------------------------------------------------------------------- #
def load_trades(limit=0):
    rows = []
    with open(TRADES) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            if (d.get("token") and d.get("entry_ts") and d.get("exit_ts")
                    and d.get("entry_price") and d.get("cost_usd") is not None):
                rows.append(d)
    rows.sort(key=lambda r: float(r["entry_ts"]))
    return rows[-limit:] if limit else rows


def load_candles(conn, token, t0, t1):
    cur = conn.execute(
        "SELECT bucket_start, open, high, low, close, volume_5m, volume_1h, "
        "liquidity FROM token_candles WHERE token_address=? AND "
        "timeframe_seconds=? AND bucket_start BETWEEN ? AND ? ORDER BY bucket_start",
        (token, TF, t0, t1),
    )
    out = []
    for bs, o, h, l, c, v5, v1, liq in cur.fetchall():
        out.append({
            "bucket_start": bs, "open": o, "high": h, "low": l, "close": c,
            "volume": v5, "volume_5m": v5, "volume_1h": v1, "liquidity": liq,
        })
    return out


def build_path(conn, trade):
    """Return (pre_ctx_candles, path_from_entry_to_exit) or (None,None)."""
    e = float(trade["entry_ts"])
    x = float(trade["exit_ts"])
    cands = load_candles(conn, trade["token"], e - PRE_CTX * TF - TF, x + 2 * TF)
    if not cands:
        return None, None
    # entry index: first candle at/after entry; exit index: last at/before exit
    entry_idx = next((i for i, c in enumerate(cands)
                      if c["bucket_start"] >= e - TF), None)
    if entry_idx is None:
        return None, None
    exit_idx = max((i for i, c in enumerate(cands)
                    if c["bucket_start"] <= x + TF), default=len(cands) - 1)
    if exit_idx <= entry_idx:
        return None, None
    pre = cands[:entry_idx]
    path = cands[entry_idx:exit_idx + 1]
    return pre, path


# --------------------------------------------------------------------------- #
# pnl + book
# --------------------------------------------------------------------------- #
def lot_pnl(entry_price, exit_price, cost_usd, fee):
    if entry_price <= 0:
        return 0.0
    return cost_usd * (exit_price / entry_price - 1.0) - cost_usd * fee


def book(items):
    """items: list of (pnl, peak_mult). Return summary stats."""
    if not items:
        return dict(n=0, pnl=0.0, win=0.0, pf=0.0, runners=0, run_rate=0.0)
    pnls = [p for p, _ in items]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    pf = (sum(wins) / abs(sum(losses))) if losses else (999.0 if wins else 0.0)
    runners = sum(1 for _, m in items if float(m or 0) >= 2.0)
    return dict(n=len(items), pnl=sum(pnls), win=len(wins) / len(items),
                pf=pf, runners=runners, run_rate=runners / len(items))


def fmt(label, b, base_pnl=None):
    delta = "" if base_pnl is None else f"{b['pnl'] - base_pnl:>+10.0f}"
    return (f"  {label:<30} n={b['n']:>4}  pnl={b['pnl']:>+9.0f}  {delta:>10}  "
            f"win={b['win']:>5.1%}  pf={b['pf']:>5.2f}  run={b['run_rate']:>4.1%}")


# --------------------------------------------------------------------------- #
# exit-rule simulators: walk the path, return (exit_idx, exit_price, fired)
# --------------------------------------------------------------------------- #
def exit_money_flow(pre, path):
    for i in range(MIN_EXIT_BAR, len(path)):
        sig = money_flow_divergence(pre + path[:i + 1], price_lookback=8, period=15)
        if sig.get("bearish_divergence"):
            return i, path[i]["close"], True
    return len(path) - 1, path[-1]["close"], False


def exit_illiquidity(pre, path):
    for i in range(MIN_EXIT_BAR, len(path)):
        sig = illiquidity_spike(pre + path[:i + 1], window=20)
        if sig.get("spiking"):
            return i, path[i]["close"], True
    return len(path) - 1, path[-1]["close"], False


def exit_climax(pre, path):
    for i in range(MIN_EXIT_BAR, len(path)):
        sig = volume_climax(pre + path[:i + 1], window=25, z_thresh=2.5)
        if sig.get("blow_off_top"):
            return i, path[i]["close"], True
    return len(path) - 1, path[-1]["close"], False


def exit_chandelier(pre, path, k=3.0):
    res = simulate_chandelier(path, entry_idx=0, k=k, period=10,
                              giveback_cap=0.5, min_atr_bars=5)
    idx = res.get("exit_idx") or len(path) - 1
    fired = res.get("reason") in ("chandelier", "giveback_cap")
    return idx, path[idx]["close"], fired


def rand_exit_idx(path, rng):
    """A single uniformly-random exit bar (the fair coin-flip control)."""
    hi = len(path) - 1
    if hi <= MIN_EXIT_BAR:
        return hi
    return rng.randint(MIN_EXIT_BAR, hi)


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def run(args):
    rng = random.Random(args.seed)
    trades = load_trades(args.limit)
    conn = sqlite3.connect(DB_PATH)

    enriched = []          # trades with usable candle path
    no_cov = 0
    for t in trades:
        pre, path = build_path(conn, t)
        if not path or len(path) < MIN_EXIT_BAR + 2:
            no_cov += 1
            continue
        t = dict(t)
        t["_pre"], t["_path"] = pre, path
        enriched.append(t)

    print(f"Trades: {len(trades)}  |  with usable 1m path: {len(enriched)}  "
          f"|  no/short coverage: {no_cov}")
    print(f"Single-lot model, fee={args.fee:.1%}, blind draws={RAND_DRAWS}, seed={args.seed}\n")
    if not enriched:
        print("No candle coverage -- aborting.")
        return

    fee = args.fee
    ledger_pnl = sum(float(t.get("pnl_usd") or 0) for t in enriched)

    def entry_price(t):
        return float(t["entry_price"])

    def cost(t):
        return float(t["cost_usd"])

    def peak(t):
        return t.get("peak_mult")

    # candle baseline: hold to last in-window candle (apples-to-apples)
    base_items = [(lot_pnl(entry_price(t), t["_path"][-1]["close"], cost(t), fee),
                   peak(t)) for t in enriched]
    base = book(base_items)
    base_pnl = base["pnl"]

    print("=" * 104)
    print("BASELINE (single-lot, hold to last candle)")
    print(fmt("hold-to-end (candle baseline)", base))
    print(f"   [context] real ledger pnl over same trades: {ledger_pnl:>+.0f} "
          f"(includes scaling/partial exits)\n")

    # ---- EXIT-RULE INDICATORS (blind-controlled) ------------------------- #
    exit_rules = [
        ("money-flow divergence (#2)", exit_money_flow),
        ("illiquidity-spike exit (#1)", exit_illiquidity),
        ("volume-climax blow-off (#5)", exit_climax),
        ("downside-ATR chandelier (#3)", exit_chandelier),
    ]
    print("=" * 104)
    print("EXIT RULES  -- act on signal vs HOLD-TO-END vs BLIND control (random-bar exit, same fired trades)")
    print("  verdict logic: real edge requires (a) acting beats hold-to-end AND (b) acting beats the blind control")
    print("=" * 104)

    MC = 400
    for name, rule in exit_rules:
        act_items, hold_items = [], []
        fired_flags = []        # per-trade: (fired, ep, cu, pk, path)
        fired_hold = []
        n_fired = 0
        for t in enriched:
            pre, path = t["_pre"], t["_path"]
            idx, px, fired = rule(pre, path)
            ep, cu, pk = entry_price(t), cost(t), peak(t)
            act_items.append((lot_pnl(ep, px, cu, fee), pk))
            hold = lot_pnl(ep, path[-1]["close"], cu, fee)
            hold_items.append((hold, pk))
            fired_flags.append((fired, ep, cu, pk, path))
            if fired:
                n_fired += 1
                fired_hold.append((hold, pk))
        act_b, hold_b = book(act_items), book(hold_items)
        # Monte-Carlo blind control: each iter exits the SAME fired trades at a
        # single random bar; non-fired hold to end. Distribution of book pnl.
        blind_pnls = []
        for _ in range(MC):
            items = []
            for fired, ep, cu, pk, path in fired_flags:
                if fired:
                    px = path[rand_exit_idx(path, rng)]["close"]
                    items.append((lot_pnl(ep, px, cu, fee), pk))
                else:
                    items.append((lot_pnl(ep, path[-1]["close"], cu, fee), pk))
            blind_pnls.append(book(items)["pnl"])
        blind_pnls.sort()
        p05 = blind_pnls[int(0.05 * MC)]
        p50 = blind_pnls[int(0.50 * MC)]
        p95 = blind_pnls[int(0.95 * MC)]
        act_pct = sum(1 for b in blind_pnls if b <= act_b["pnl"]) / MC
        print(f"\n{name}   (fired on {n_fired}/{len(enriched)} = {n_fired/len(enriched):.1%})")
        print(fmt("  act on signal", act_b, base_pnl))
        print(fmt("  hold-to-end", hold_b, base_pnl))
        print(f"  {'BLIND single-exit control':<30} "
              f"p05={p05:>+7.0f}  p50={p50:>+7.0f}  p95={p95:>+7.0f}  "
              f"| act at {act_pct:>5.0%} of blind dist")
        if fired_hold:
            fh = book(fired_hold)
            print(f"     selectivity: fired-trade hold pnl={fh['pnl']:>+.0f} "
                  f"win={fh['win']:.1%} (a real exit rule fires on LOSERS)")
        beats_hold = act_b["pnl"] > hold_b["pnl"]
        beats_blind = act_pct >= 0.95          # better than 95% of coin-flips
        verdict = ("REAL EDGE" if (beats_hold and beats_blind)
                   else "NO EDGE vs control" if beats_hold and not beats_blind
                   else "HARMFUL" if not beats_hold else "INCONCLUSIVE")
        print(f"     -> beats hold: {beats_hold} | beats blind(>=p95): {beats_blind} "
              f"| VERDICT: {verdict}")

    # chandelier k-sweep (runner capture is sensitive to k)
    print("\n" + "-" * 104)
    print("chandelier k-sweep (runner capture):")
    for k in (1.5, 2.0, 3.0, 4.0, 6.0):
        items = []
        for t in enriched:
            idx, px, _ = exit_chandelier(t["_pre"], t["_path"], k=k)
            items.append((lot_pnl(entry_price(t), px, cost(t), fee), peak(t)))
        b = book(items)
        print(fmt(f"  k={k}", b, base_pnl))

    # ---- ENTRY-FILTER INDICATORS (liquidity at entry) -------------------- #
    print("\n" + "=" * 104)
    print("ENTRY FILTERS  -- split trades by liquidity state AT ENTRY; do worse-liquidity entries lose more?")
    print("  blind control = random split removing the same number of trades")
    print("=" * 104)

    # compute entry-time impact + spread STRICTLY from pre-entry candles
    # (the entry candle is not complete at decision time -> no look-ahead)
    for t in enriched:
        pre = t["_pre"]
        sig = illiquidity_spike(pre, window=20) if len(pre) >= 5 else {}
        t["_entry_impact"] = sig.get("impact_percentile")
        ss = spread_state(pre, window=20) if len(pre) >= 6 else {}
        t["_entry_spread"] = ss.get("spread")

    def filter_test(label, keyfn, hi_is_bad=True):
        vals = [(t, keyfn(t)) for t in enriched if keyfn(t) is not None]
        if len(vals) < 20:
            print(f"\n{label}: only {len(vals)} trades have the signal -- skip")
            return
        vals.sort(key=lambda kv: kv[1])
        n = len(vals)
        q = n // 4
        good_liq = vals[:q] if hi_is_bad else vals[-q:]   # best-liquidity quartile
        bad_liq = vals[-q:] if hi_is_bad else vals[:q]    # worst-liquidity quartile
        gb = book([(lot_pnl(float(t["entry_price"]), t["_path"][-1]["close"],
                            float(t["cost_usd"]), fee), t.get("peak_mult"))
                   for t, _ in good_liq])
        bb = book([(lot_pnl(float(t["entry_price"]), t["_path"][-1]["close"],
                            float(t["cost_usd"]), fee), t.get("peak_mult"))
                   for t, _ in bad_liq])
        print(f"\n{label} (n={n}, quartile={q})")
        print(fmt("  best-liquidity quartile", gb))
        print(fmt("  worst-liquidity quartile", bb))
        # blind: random quartile mean pnl
        rnd = []
        allv = [t for t, _ in vals]
        for _ in range(200):
            samp = rng.sample(allv, q)
            rnd.append(sum(lot_pnl(float(t["entry_price"]), t["_path"][-1]["close"],
                                   float(t["cost_usd"]), fee) for t in samp))
        rnd_mean = sum(rnd) / len(rnd)
        edge = bb["pnl"] - rnd_mean
        print(f"     blind random-quartile mean pnl={rnd_mean:>+.0f} | "
              f"worst-quartile vs blind: {edge:>+.0f} "
              f"({'separates' if abs(bb['pnl']-gb['pnl'])>abs(rnd_mean)*0.5+1 else 'weak'})")

    filter_test("Amihud illiquidity at entry (#1)",
                lambda t: t.get("_entry_impact"), hi_is_bad=True)
    filter_test("Corwin-Schultz spread at entry (#4)",
                lambda t: t.get("_entry_spread"), hi_is_bad=True)

    print("\nDone.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--fee", type=float, default=0.01)
    ap.add_argument("--seed", type=int, default=7)
    run(ap.parse_args())


if __name__ == "__main__":
    main()
