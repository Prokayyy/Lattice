#!/usr/bin/env python3
"""Are the deep stop-slippage losses single-tick GLITCHES or genuine CRASHES?

For each deep initial_stop trade (realized < 0.55x), pull the token's
signal_snapshots around the breach and classify the exit tick:
  - GLITCH  : price recovers back above the -30% stop level within the next
              few ticks  -> a confirmation-tick filter (B1) would have AVOIDED
              the bad exit.
  - CRASH   : price stays at/below the stop after the breach -> B1 only delays
              the exit (and can make it worse); inherent gap risk.
This resolves whether B1 / faster polling can recover the -$488 tail.
"""
import json
import sqlite3
from collections import Counter

ROOT = "/home/iradei/lattice-scanner"
DB = f"{ROOT}/scanner.db"
STOP_PCT = 0.30
TAIL = 0.55          # deep-tail = realized below this
RECOVER_TICKS = 3    # how many post-breach ticks count as a "glitch" recovery window

trades = []
for line in open(f"{ROOT}/discovery/trades.jsonl"):
    line = line.strip()
    if not line:
        continue
    try:
        t = json.loads(line)
    except Exception:
        continue
    if t.get("reason") != "initial_stop":
        continue
    ep = t.get("entry_price"); xp = t.get("exit_price")
    if not ep or not xp:
        continue
    if xp / ep < TAIL:
        trades.append(t)

db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
db.row_factory = sqlite3.Row

cls = Counter()
recoverable_usd = 0.0
crash_usd = 0.0
examples = {"GLITCH": [], "CRASH": [], "NO_DATA": []}

for t in trades:
    tok = t["token"]; ep = t["entry_price"]; ets = t["entry_ts"]; xts = t["exit_ts"]
    stop_level = ep * (1 - STOP_PCT)
    rows = db.execute(
        "SELECT price, timestamp FROM signal_snapshots WHERE token_address=? "
        "AND timestamp>=? AND timestamp<=? AND price>0 ORDER BY timestamp ASC",
        (tok, ets - 1, xts + 3600),
    ).fetchall()
    if len(rows) < 2:
        cls["NO_DATA"] += 1
        if len(examples["NO_DATA"]) < 3:
            examples["NO_DATA"].append(t["symbol"])
        continue
    prices = [float(r["price"]) for r in rows]
    # first breach index
    bi = next((i for i, p in enumerate(prices) if p <= stop_level), None)
    if bi is None:
        cls["NO_BREACH"] += 1
        continue
    # does it recover above the stop within RECOVER_TICKS after the breach?
    window = prices[bi + 1: bi + 1 + RECOVER_TICKS]
    recovered = any(p > stop_level for p in window)
    pnl = t.get("pnl_usd", 0.0)
    intended = -0.30 * t.get("cost_usd", 20.0)   # loss if exited exactly at stop
    slip_beyond = pnl - intended                  # extra loss past -30% (negative)
    if recovered:
        cls["GLITCH"] += 1
        recoverable_usd += slip_beyond
        if len(examples["GLITCH"]) < 5:
            examples["GLITCH"].append(
                f"{t['symbol']} breach@{prices[bi]/ep:.2f}x recov->{max(window)/ep:.2f}x pnl${pnl:.1f}")
    else:
        cls["CRASH"] += 1
        crash_usd += slip_beyond
        if len(examples["CRASH"]) < 5:
            examples["CRASH"].append(
                f"{t['symbol']} breach@{prices[bi]/ep:.2f}x stays<=stop pnl${pnl:.1f}")

n = len(trades)
print(f"Deep-tail initial_stop trades (realized < {TAIL}x): {n}\n")
print(f"{'class':>10} | count | share")
print("-" * 32)
for k in ("GLITCH", "CRASH", "NO_DATA", "NO_BREACH"):
    if cls.get(k):
        print(f"{k:>10} | {cls[k]:5d} | {cls[k]/n*100:4.0f}%")
print()
print(f"Extra-loss-beyond-30% in GLITCH trades (B1 could recover): ${recoverable_usd:.1f}")
print(f"Extra-loss-beyond-30% in CRASH trades (inherent, B1 can't): ${crash_usd:.1f}")
print()
for k in ("GLITCH", "CRASH", "NO_DATA"):
    if examples[k]:
        print(f"  {k} e.g.: " + " | ".join(examples[k]))
