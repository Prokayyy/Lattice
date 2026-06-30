"""Does the alert P(>=2x) conviction relate to tokens ACTUALLY doing >=2x?

Calibration + predictive-power check on the conviction pipeline. For every
participation_log candidate: take its conviction and the token's subsequent peak
multiple (from token_candles), then
  - bucket by conviction DECILE and compare the ACTUAL >=2x rate per bucket
    (flat across deciles = conviction is uninformative; rising = it ranks);
  - report Spearman(conviction, did_2x) and AUC (Mann-Whitney) -- 0.50 = coin
    flip;
  - compare top-decile vs bottom-decile vs base rate (lift).

  python3 analysis/conviction_vs_2x.py [--post 86400] [--runner 2.0]
"""
import argparse
import json
import os
import sqlite3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "scanner.db")
CAND = os.path.join(ROOT, "discovery", "participation_log.jsonl")
TF = 60


def load():
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
        conv, ts, token, ep = (d.get("conviction"), d.get("ts"),
                               d.get("token"), d.get("entry_price"))
        if token and ts and ep and conv is not None:
            rows.append((float(ts), token, float(ep), float(conv)))
    return rows


def peak_after(con, token, ts, horizon):
    rows = con.execute(
        "SELECT high, close FROM token_candles WHERE token_address=? AND "
        "timeframe_seconds=? AND bucket_start BETWEEN ? AND ? ORDER BY bucket_start",
        (token, TF, ts, ts + horizon)).fetchall()
    if len(rows) < 2:
        return None
    return max((r[0] or r[1] or 0) for r in rows)


def _ranks(v):
    order = sorted(range(len(v)), key=lambda i: v[i])
    r = [0.0] * len(v)
    i = 0
    while i < len(v):
        j = i
        while j < len(v) and v[order[j]] == v[order[i]]:
            j += 1
        avg = (i + 1 + j) / 2.0
        for k in range(i, j):
            r[order[k]] = avg
        i = j
    return r


def spearman(xs, ys):
    rx, ry = _ranks(xs), _ranks(ys)
    n = len(xs)
    mx, my = sum(rx) / n, sum(ry) / n
    cov = sum((rx[i] - mx) * (ry[i] - my) for i in range(n))
    vx = sum((rx[i] - mx) ** 2 for i in range(n)) ** 0.5
    vy = sum((ry[i] - my) ** 2 for i in range(n)) ** 0.5
    return cov / (vx * vy) if vx and vy else 0.0


def auc(scores, labels):
    r = _ranks(scores)
    pos = [r[i] for i in range(len(labels)) if labels[i]]
    npos, nneg = len(pos), len(labels) - len(pos)
    if npos == 0 or nneg == 0:
        return None
    return (sum(pos) - npos * (npos + 1) / 2.0) / (npos * nneg)


def run(args):
    cands = load()
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    data = []
    for ts, token, ep, conv in cands:
        if ep <= 0:
            continue
        peak = peak_after(con, token, ts, args.post)
        if peak is None or peak <= 0:
            continue
        data.append((conv, 1 if peak / ep >= args.runner else 0, peak / ep))
    con.close()
    if len(data) < 50:
        print(f"too few evaluable ({len(data)})")
        return

    data.sort(key=lambda d: d[0])
    convs = [d[0] for d in data]
    labels = [d[1] for d in data]
    n = len(data)
    base = sum(labels) / n

    print(f"candidates: {len(cands)} | evaluable: {n} | "
          f"post={args.post/3600:.0f}h | runner={args.runner}x")
    print(f"base {args.runner}x rate: {base:.1%}  "
          f"(mean conviction {sum(convs)/n:.1%})\n")
    print(f"  {'decile':>7} {'conviction':>18} {'n':>6} "
          f"{'actual ≥2x':>11} {'avg peak':>9}")
    print("  " + "-" * 58)
    for q in range(10):
        lo = q * n // 10
        hi = (q + 1) * n // 10
        seg = data[lo:hi]
        if not seg:
            continue
        rate = sum(s[1] for s in seg) / len(seg)
        avgp = sum(s[2] for s in seg) / len(seg)
        print(f"  {q + 1:>7} {seg[0][0]:>7.1%}–{seg[-1][0]:<8.1%} {len(seg):>6} "
              f"{rate:>10.1%} {avgp:>8.2f}x")

    sp = spearman(convs, [float(x) for x in labels])
    au = auc(convs, labels)
    top = data[9 * n // 10:]
    bot = data[:n // 10]
    tr = sum(s[1] for s in top) / len(top)
    br = sum(s[1] for s in bot) / len(bot)
    print(f"\n  Spearman(conviction, did_2x): {sp:+.3f}")
    print(f"  AUC (rank-orders runners?):   {au:.3f}   (0.50 = coin flip)")
    print(f"  top-decile ≥2x {tr:.1%}  vs  bottom-decile {br:.1%}  "
          f"vs  base {base:.1%}")
    verdict = ("NO usable relation (≈ coin flip)" if au is None or au < 0.55
               else "weak relation" if au < 0.6 else "real relation")
    print(f"\n  verdict: {verdict}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--post", type=int, default=86400)
    ap.add_argument("--runner", type=float, default=2.0)
    run(ap.parse_args())


if __name__ == "__main__":
    main()
