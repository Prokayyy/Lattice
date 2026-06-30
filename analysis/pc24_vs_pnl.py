#!/usr/bin/env python3
"""Does entry-time 24h price change predict trade profitability?

Joins the actual trade ledger (discovery/trades.jsonl) to the entry-time
feature row (price_change_24h, etc.) recorded in discovery_outcomes.jsonl /
participation_log.jsonl, keyed by (token, entry timestamp). Then buckets trades
by price_change_24h and reports win rate, avg/total PnL, return %, and runner
capture (peak_mult >= 2) per bucket, plus a rank correlation.
"""
import json
import math
from collections import defaultdict

ROOT = "/home/iradei/lattice-scanner/discovery"


def load_jsonl(path):
    out = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return out


# ---- entry-time feature lookup: (token, round(ts,2)) -> row features ----
feat = {}
for fname in ("discovery_outcomes.jsonl", "participation_log.jsonl"):
    for d in load_jsonl(f"{ROOT}/{fname}"):
        tok = d.get("token")
        ts = d.get("alert_ts", d.get("ts"))
        row = d.get("row") or {}
        if tok is None or ts is None:
            continue
        pc24 = row.get("price_change_24h")
        if pc24 is None:
            continue
        key = (tok, round(float(ts), 2))
        # discovery_outcomes is read first; don't overwrite with participation_log
        feat.setdefault(key, {
            "pc24": float(pc24),
            "pc6": row.get("price_change_6h"),
            "pc1": row.get("price_change_1h"),
            "liq": row.get("liquidity"),
            "fdv": row.get("fdv"),
            "lifecycle": row.get("lifecycle"),
        })

# ---- trades ----
trades = load_jsonl(f"{ROOT}/trades.jsonl")

matched = []
unmatched = 0
for t in trades:
    tok = t.get("token")
    ts = t.get("entry_ts")
    pnl = t.get("pnl_usd")
    cost = t.get("cost_usd") or 0.0
    if tok is None or ts is None or pnl is None:
        continue
    key = (tok, round(float(ts), 2))
    f = feat.get(key)
    if f is None:
        # tolerance fallback: same token, ts within 2s
        cand = None
        for (k_tok, k_ts), v in feat.items():
            if k_tok == tok and abs(k_ts - float(ts)) <= 2.0:
                cand = v
                break
        f = cand
    if f is None:
        unmatched += 1
        continue
    ret = (pnl / cost) if cost else float("nan")
    matched.append({
        "token": tok, "symbol": t.get("symbol"), "pnl": float(pnl),
        "cost": float(cost), "ret": ret, "peak": t.get("peak_mult"),
        "reason": t.get("reason"), "conv": t.get("conviction"),
        "pc24": f["pc24"], "liq": f["liq"], "lifecycle": f["lifecycle"],
    })

print(f"trades total={len(trades)}  matched_with_pc24={len(matched)}  unmatched={unmatched}\n")


def summarize(rows, label):
    n = len(rows)
    if n == 0:
        return f"{label:>16} |   0 |     - |       - |        - |      - |     -"
    wins = sum(1 for r in rows if r["pnl"] > 0)
    total_pnl = sum(r["pnl"] for r in rows)
    avg_pnl = total_pnl / n
    rets = [r["ret"] for r in rows if not math.isnan(r["ret"])]
    avg_ret = (sum(rets) / len(rets) * 100) if rets else float("nan")
    runners = sum(1 for r in rows if (r["peak"] or 0) >= 2.0)
    return (f"{label:>16} | {n:3d} | {wins/n*100:4.0f}% | {avg_pnl:7.2f} | "
            f"{total_pnl:8.1f} | {avg_ret:5.0f}% | {runners/n*100:4.0f}%")


HEAD = (f"{'pc24 bucket':>16} |   n | win% | avgPnL$ | totPnL$ | avgRet | runner%")
SEP = "-" * len(HEAD)

# ---- fixed buckets ----
buckets = [
    ("<0%", lambda v: v < 0),
    ("0-50%", lambda v: 0 <= v < 50),
    ("50-100%", lambda v: 50 <= v < 100),
    ("100-200%", lambda v: 100 <= v < 200),
    ("200-300%", lambda v: 200 <= v < 300),
    ("300-500%", lambda v: 300 <= v < 500),
    ("500-1000%", lambda v: 500 <= v < 1000),
    (">=1000%", lambda v: v >= 1000),
]
print("=== Trades bucketed by entry-time price_change_24h (fixed bands) ===")
print(HEAD)
print(SEP)
for label, fn in buckets:
    rows = [r for r in matched if fn(r["pc24"])]
    print(summarize(rows, label))
print(SEP)
print(summarize(matched, "ALL"))

# ---- quartile buckets (equal trade count) ----
vals = sorted(r["pc24"] for r in matched)
n = len(vals)
if n >= 8:
    def q(p):
        return vals[min(n - 1, int(p * n))]
    qs = [q(0.0), q(0.25), q(0.5), q(0.75), q(1.0)]
    qbuckets = [
        (f"Q1 [{qs[0]:.0f},{qs[1]:.0f})", lambda v, lo=qs[0], hi=qs[1]: lo <= v < hi),
        (f"Q2 [{qs[1]:.0f},{qs[2]:.0f})", lambda v, lo=qs[1], hi=qs[2]: lo <= v < hi),
        (f"Q3 [{qs[2]:.0f},{qs[3]:.0f})", lambda v, lo=qs[2], hi=qs[3]: lo <= v < hi),
        (f"Q4 [{qs[3]:.0f},{qs[4]:.0f}]", lambda v, lo=qs[3], hi=qs[4]: lo <= v <= hi),
    ]
    print("\n=== Same trades, equal-count quartiles of price_change_24h ===")
    print(HEAD)
    print(SEP)
    for label, fn in qbuckets:
        rows = [r for r in matched if fn(r["pc24"])]
        print(summarize(rows, label))

# ---- Spearman rank correlation pc24 vs pnl and vs return ----
def spearman(xs, ys):
    n = len(xs)
    if n < 3:
        return float("nan")
    def ranks(a):
        order = sorted(range(n), key=lambda i: a[i])
        r = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and a[order[j + 1]] == a[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r
    rx, ry = ranks(xs), ranks(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    cov = sum((rx[i] - mx) * (ry[i] - my) for i in range(n))
    vx = math.sqrt(sum((v - mx) ** 2 for v in rx))
    vy = math.sqrt(sum((v - my) ** 2 for v in ry))
    return cov / (vx * vy) if vx and vy else float("nan")

xs = [r["pc24"] for r in matched]
print(f"\nSpearman(pc24, pnl_usd)  = {spearman(xs, [r['pnl'] for r in matched]):+.3f}")
print(f"Spearman(pc24, peak_mult)= {spearman(xs, [(r['peak'] or 0) for r in matched]):+.3f}")

# ---- CONFOUNDER CHECK: is high pc24 just a proxy for thin liquidity? ----
with_liq = [r for r in matched if isinstance(r["liq"], (int, float)) and r["liq"] > 0]
print(f"\n=== Median entry liquidity per pc24 bucket (is pc24 a liq proxy?) ===")
print(f"{'pc24 bucket':>16} |   n | medLiq$ | avgLiq$")
print("-" * 46)
for label, fn in buckets:
    rows = [r for r in with_liq if fn(r["pc24"])]
    if not rows:
        continue
    liqs = sorted(r["liq"] for r in rows)
    med = liqs[len(liqs) // 2]
    avg = sum(liqs) / len(liqs)
    print(f"{label:>16} | {len(rows):3d} | {med:7.0f} | {avg:7.0f}")
print(f"Spearman(pc24, liquidity)= {spearman([r['pc24'] for r in with_liq], [r['liq'] for r in with_liq]):+.3f}")

# ---- 2D: liquidity tier x pc24 tier (does pc24 hurt AMONG liquid tokens?) ----
LIQ_CUT = 10000.0
PC_CUT = 200.0
print(f"\n=== liq tier x pc24 tier (liq cut ${LIQ_CUT:.0f}, pc24 cut {PC_CUT:.0f}%) ===")
print(f"{'cell':>22} |   n | win% | avgPnL$ | totPnL$")
print("-" * 60)
for lname, lfn in [("liq>=10k", lambda r: r["liq"] >= LIQ_CUT),
                   ("liq<10k", lambda r: r["liq"] < LIQ_CUT)]:
    for pname, pfn in [("pc24<200", lambda r: r["pc24"] < PC_CUT),
                       ("pc24>=200", lambda r: r["pc24"] >= PC_CUT)]:
        rows = [r for r in with_liq if lfn(r) and pfn(r)]
        n = len(rows)
        if n == 0:
            print(f"{lname+' & '+pname:>22} |   0 |    - |       - |       -")
            continue
        wins = sum(1 for r in rows if r["pnl"] > 0)
        tot = sum(r["pnl"] for r in rows)
        print(f"{lname+' & '+pname:>22} | {n:3d} | {wins/n*100:4.0f}% | {tot/n:7.2f} | {tot:8.1f}")

# ---- FILTER SIMULATION: net PnL impact of candidate skip rules ----
def simulate(rule, label):
    kept = [r for r in matched if not rule(r)]
    dropped = [r for r in matched if rule(r)]
    kp = sum(r["pnl"] for r in kept)
    dp = sum(r["pnl"] for r in dropped)
    drun = sum(1 for r in dropped if (r["peak"] or 0) >= 2.0)
    kwin = (sum(1 for r in kept if r["pnl"] > 0) / len(kept) * 100) if kept else 0
    print(f"{label:>34} | drop {len(dropped):3d} (PnL ${dp:7.1f}, {drun} runners) | "
          f"keep {len(kept):3d} (PnL ${kp:7.1f}, win {kwin:.0f}%)")

base = sum(r["pnl"] for r in matched)
print(f"\n=== Filter simulation (baseline: {len(matched)} trades, PnL ${base:.1f}, "
      f"win {sum(1 for r in matched if r['pnl']>0)/len(matched)*100:.0f}%) ===")
print(f"{'rule = SKIP if...':>34} | {'dropped':^30} | {'kept':^28}")
print("-" * 100)
simulate(lambda r: r["pc24"] >= 1000, "pc24 >= 1000")
simulate(lambda r: r["pc24"] >= 500, "pc24 >= 500")
simulate(lambda r: r["pc24"] >= 300, "pc24 >= 300")
simulate(lambda r: r["pc24"] >= 100, "pc24 >= 100")
simulate(lambda r: r["pc24"] >= 50, "pc24 >= 50")
simulate(lambda r: isinstance(r["liq"], (int, float)) and r["liq"] < 10000, "liq < 10000")
simulate(lambda r: isinstance(r["liq"], (int, float)) and r["liq"] < 20000, "liq < 20000")
simulate(lambda r: (r["pc24"] >= 500) or (isinstance(r["liq"], (int, float)) and r["liq"] < 10000), "pc24>=500 OR liq<10k")
simulate(lambda r: (r["pc24"] >= 300) or (isinstance(r["liq"], (int, float)) and r["liq"] < 10000), "pc24>=300 OR liq<10k")
