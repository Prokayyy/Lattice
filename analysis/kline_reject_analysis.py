"""Counterfactual: would the GMGN kline fade-filter have rejected WINNERS?

The filter blocks an entry on a blow-off upper wick or a fade from the window
high. It is NOT logged per token, so re-apply it to every conviction-survivor in
discovery/participation_log.jsonl using local token_candles (offline), then check
each token's SUBSEQUENT peak multiple. Compare the would-REJECT cohort vs the
would-PASS cohort: if the rejects run MORE often / higher, the gate is throwing
away winners.

Thresholds mirror config:
  reject if  last-candle upper-wick ratio  >  WICK_MAX (0.5)
         or  drawdown-from-window-high %    <  DD_MAX  (-25)

Caveat: token_candles is observation-based OHLC (the scanner's price stream), so
the wick proxy is weaker than GMGN's real klines; the drawdown-from-high (the
main fade signal) is robust from the close/high series.

  python3 analysis/kline_reject_analysis.py [--pre 3600] [--post 21600] [--runner 2.0]
"""
import argparse
import json
import os
import sqlite3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "scanner.db")
CAND = os.path.join(ROOT, "discovery", "participation_log.jsonl")
TF = 60
WICK_MAX = 0.5
DD_MAX = -25.0


def load_candidates():
    rows = []
    if not os.path.exists(CAND):
        return rows
    for line in open(CAND):
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        ts = d.get("ts")
        token = d.get("token")
        ep = d.get("entry_price")
        if token and ts and ep:
            rows.append({"ts": float(ts), "token": token,
                         "symbol": d.get("symbol"), "entry": float(ep)})
    return rows


def candles(con, token, t0, t1):
    return con.execute(
        "SELECT bucket_start, open, high, low, close FROM token_candles WHERE "
        "token_address=? AND timeframe_seconds=? AND bucket_start BETWEEN ? AND ? "
        "ORDER BY bucket_start", (token, TF, t0, t1)).fetchall()


def kline_reject(pre):
    """Re-apply the fade-filter on a pre-entry candle window. Returns
    (reject, drawdown_pct, wick_ratio) or None if not evaluable."""
    pre = [c for c in pre if c[2] and c[3] and c[4]]      # high/low/close present
    if len(pre) < 5:
        return None
    highs = [c[2] for c in pre]
    closes = [c[4] for c in pre]
    hi = max(highs)
    dd = (closes[-1] / hi - 1.0) * 100.0 if hi > 0 else 0.0
    o, h, lo, cl = pre[-1][1], pre[-1][2], pre[-1][3], pre[-1][4]
    rng = (h - lo) or 1e-18
    wick = (h - max(o, cl)) / rng
    reject = (wick > WICK_MAX) or (dd < DD_MAX)
    return reject, dd, wick


def book(rows, runner):
    if not rows:
        return dict(n=0, run=0, run_rate=0.0, avg_peak=0.0, med_peak=0.0)
    peaks = sorted(r["peak_mult"] for r in rows)
    run = sum(1 for p in peaks if p >= runner)
    return dict(n=len(rows), run=run, run_rate=run / len(rows),
                avg_peak=sum(peaks) / len(peaks), med_peak=peaks[len(peaks) // 2])


def run(args):
    cands = load_candidates()
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    evaluable, no_pre, no_post = [], 0, 0
    for c in cands:
        ts, token, entry = c["ts"], c["token"], c["entry"]
        if entry <= 0:
            continue
        pre = candles(con, token, ts - args.pre, ts + TF)
        kr = kline_reject(pre)
        if kr is None:
            no_pre += 1
            continue
        post = candles(con, token, ts, ts + args.post)
        if len(post) < 2:
            no_post += 1
            continue
        peak = max((p[2] or p[4] or 0) for p in post)      # high, fallback close
        if peak <= 0:
            no_post += 1
            continue
        reject, dd, wick = kr
        evaluable.append({"token": token, "symbol": c["symbol"], "ts": ts,
                          "reject": reject, "dd": dd, "wick": wick,
                          "peak_mult": peak / entry})
    con.close()

    rej = [r for r in evaluable if r["reject"]]
    pas = [r for r in evaluable if not r["reject"]]
    rb, pb = book(rej, args.runner), book(pas, args.runner)

    print(f"candidates: {len(cands)}  |  evaluable: {len(evaluable)}  "
          f"(skipped: {no_pre} no-pre-window, {no_post} no-post-window)")
    print(f"filter: reject if wick>{WICK_MAX} or drawdown%<{DD_MAX} | "
          f"pre={args.pre/60:.0f}m post={args.post/3600:.1f}h runner={args.runner}x\n")
    print(f"  {'cohort':<22} {'n':>5} {'runners':>8} {'run_rate':>9} "
          f"{'avg_peak':>9} {'med_peak':>9}")
    print("  " + "-" * 66)
    for name, b in (("WOULD-REJECT (fade)", rb), ("WOULD-PASS", pb)):
        print(f"  {name:<22} {b['n']:>5} {b['run']:>8} {b['run_rate']:>8.1%} "
              f"{b['avg_peak']:>8.2f}x {b['med_peak']:>8.2f}x")

    if rb["n"] and pb["n"]:
        verdict = ("REJECTS WINNERS (gate hurts)"
                   if rb["run_rate"] > pb["run_rate"]
                   else "rejects fades correctly (gate helps)")
        print(f"\n  reject vs pass runner-rate: {rb['run_rate']:.1%} vs "
              f"{pb['run_rate']:.1%}  ->  {verdict}")
        # which rule does the rejecting? (cohorts overlap when both fire)
        bw = book([r for r in rej if r["wick"] > WICK_MAX], args.runner)
        bd = book([r for r in rej if r["dd"] < DD_MAX], args.runner)
        print("\n  reject-reason split (vs pass run_rate "
              f"{pb['run_rate']:.1%}):")
        print(f"    wick > {WICK_MAX}      n={bw['n']:>5}  run_rate {bw['run_rate']:>5.1%}"
              f"  avg_peak {bw['avg_peak']:.2f}x")
        print(f"    drawdown < {DD_MAX:.0f}%  n={bd['n']:>5}  run_rate {bd['run_rate']:>5.1%}"
              f"  avg_peak {bd['avg_peak']:.2f}x")

        # biggest winners the gate would have rejected
        big = sorted([r for r in rej if r["peak_mult"] >= args.runner],
                     key=lambda r: -r["peak_mult"])[:8]
        if big:
            print("\n  winners the kline gate would have BLOCKED:")
            for r in big:
                print(f"    ${str(r['symbol'] or '?')[:10]:<10} "
                      f"peak {r['peak_mult']:>6.2f}x  "
                      f"(dd {r['dd']:>5.0f}% wick {r['wick']:.2f})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pre", type=int, default=3600)
    ap.add_argument("--post", type=int, default=21600)
    ap.add_argument("--runner", type=float, default=2.0)
    run(ap.parse_args())


if __name__ == "__main__":
    main()
