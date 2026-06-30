#!/usr/bin/env python3
"""Why is the trade book net-negative at a 26% win rate?

Decomposes discovery/trades.jsonl into: expectancy (win/loss asymmetry +
breakeven win rate), the realized-return distribution, PnL attribution by exit
reason, and runner-capture / leakage (did we hold winners; did losers blow
through the stop). The goal is to locate WHERE expectancy is destroyed —
entry selection, exits, or trade economics.
"""
import json
import math
from collections import defaultdict

PATH = "/home/iradei/lattice-scanner/discovery/trades.jsonl"


def load(path):
    out = []
    for line in open(path):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out


rows = []
for t in load(PATH):
    pnl = t.get("pnl_usd")
    cost = t.get("cost_usd") or 0.0
    if pnl is None or cost <= 0:
        continue
    proceeds = t.get("proceeds")
    realized_mult = (proceeds / cost) if proceeds is not None else (1 + pnl / cost)
    rows.append({
        "pnl": float(pnl),
        "cost": float(cost),
        "ret": float(pnl) / cost,
        "rmult": realized_mult,
        "peak": float(t.get("peak_mult") or 0),
        "reason": t.get("reason") or "?",
        "conv": t.get("conv", t.get("conviction")),
        "sym": t.get("symbol"),
    })

n = len(rows)
wins = [r for r in rows if r["pnl"] > 0]
losses = [r for r in rows if r["pnl"] <= 0]
def mean(xs): return sum(xs) / len(xs) if xs else float("nan")
def median(xs):
    if not xs: return float("nan")
    s = sorted(xs); m = len(s) // 2
    return s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2

print(f"=== Expectancy decomposition (n={n}) ===")
wr = len(wins) / n
avg_win = mean([r["pnl"] for r in wins])
avg_loss = mean([r["pnl"] for r in losses])
print(f"win rate            : {wr*100:.1f}%  ({len(wins)} win / {len(losses)} loss)")
print(f"avg winner          : +${avg_win:.2f}   (median +${median([r['pnl'] for r in wins]):.2f})")
print(f"avg loser           : ${avg_loss:.2f}   (median ${median([r['pnl'] for r in losses]):.2f})")
payoff = avg_win / abs(avg_loss)
print(f"payoff ratio (W/L)  : {payoff:.2f}x")
be = 1 / (1 + payoff)
print(f"breakeven win rate  : {be*100:.1f}%   <-- need this to break even at current payoff")
print(f"actual win rate     : {wr*100:.1f}%   (gap: {(wr-be)*100:+.1f} pts)")
print(f"expectancy / trade  : ${mean([r['pnl'] for r in rows]):.2f}   (median ${median([r['pnl'] for r in rows]):.2f})")
print(f"avg winner mult     : {mean([r['rmult'] for r in wins]):.2f}x   best: {max(r['rmult'] for r in rows):.2f}x")
print(f"avg loser mult      : {mean([r['rmult'] for r in losses]):.2f}x   worst: {min(r['rmult'] for r in rows):.2f}x")

print(f"\n=== Realized-return distribution ===")
bands = [("<= -70%", lambda x: x <= -0.70), ("-70..-50%", lambda x: -0.70 < x <= -0.50),
         ("-50..-30%", lambda x: -0.50 < x <= -0.30), ("-30..-10%", lambda x: -0.30 < x <= -0.10),
         ("-10..0%", lambda x: -0.10 < x <= 0), ("0..25%", lambda x: 0 < x <= 0.25),
         ("25..100%", lambda x: 0.25 < x <= 1.0), ("100..300%", lambda x: 1.0 < x <= 3.0),
         (">300%", lambda x: x > 3.0)]
for label, fn in bands:
    b = [r for r in rows if fn(r["ret"])]
    if not b: continue
    print(f"  {label:>10} | n={len(b):3d} ({len(b)/n*100:4.1f}%) | totPnL ${sum(r['pnl'] for r in b):8.1f}")

print(f"\n=== PnL attribution by EXIT REASON (sorted by total PnL) ===")
by = defaultdict(list)
for r in rows: by[r["reason"]].append(r)
print(f"{'reason':>28} |   n | win% | avgPnL$ | totPnL$ | avgPeak | avgRealMult")
print("-" * 92)
for reason, b in sorted(by.items(), key=lambda kv: sum(x['pnl'] for x in kv[1])):
    bn = len(b); bw = sum(1 for x in b if x['pnl'] > 0)
    print(f"{reason[:28]:>28} | {bn:3d} | {bw/bn*100:4.0f}% | {mean([x['pnl'] for x in b]):7.2f} | "
          f"{sum(x['pnl'] for x in b):8.1f} | {mean([x['peak'] for x in b]):6.2f}x | {mean([x['rmult'] for x in b]):6.2f}x")

print(f"\n=== Runner capture / leakage ===")
gaveup = [r for r in rows if r["peak"] >= 1.10 and r["rmult"] < 1.0]
print(f"trades that were UP >=10% at peak but closed at a LOSS: {len(gaveup)} ({len(gaveup)/n*100:.0f}%), "
      f"bleeding ${sum(r['pnl'] for r in gaveup):.1f}")
for thr in (1.5, 2.0, 3.0):
    avail = [r for r in rows if r["peak"] >= thr]
    if not avail: continue
    captured = sum(1 for r in avail if r["rmult"] >= thr)
    netwin = sum(1 for r in avail if r["pnl"] > 0)
    print(f"  peak >= {thr:.1f}x available in {len(avail):3d} trades | held to >={thr:.1f}x: {captured:3d} "
          f"| closed net-positive: {netwin:3d} | avg realized {mean([r['rmult'] for r in avail]):.2f}x")
# capture ratio among winners
wcap = [ (r['rmult']-1)/(r['peak']-1) for r in wins if r['peak'] > 1.0001 ]
print(f"avg capture ratio among winners (realized gain / peak gain): {mean(wcap)*100:.0f}%")
left = sum((r['peak'] - r['rmult']) * r['cost'] for r in rows if r['peak'] > r['rmult'])
print(f"upside left on table (sum (peak-realized)*cost, theoretical ceiling): ${left:.0f}")
