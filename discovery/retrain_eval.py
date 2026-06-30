"""Participation-aware retrain + OOS evaluation for the conviction ranker.

Reuses train_ranker's OOS machinery but adds three things the plain trainer lacks:
  1. joins forward-collected participation breadth (participation_log.jsonl) onto
     each labeled decision snapshot (by token + nearest ts within a tolerance),
     so participation can enter the model as a REAL value instead of neutral-0;
  2. compares base (breadth-neutral) vs participation vectors on identical folds,
     and both vs the existing `score` baseline;
  3. writes a CANDIDATE model by default (never clobbers the live model) and
     recalibrates the conviction threshold from the OOS score distribution.

Promotion is deliberately a separate, explicit step: review the OOS numbers and
shadow the candidate before `--promote` overwrites the live model.

Run:
  env/bin/python -m discovery.retrain_eval            # evaluate + write candidate
  env/bin/python -m discovery.retrain_eval --promote  # also overwrite live model
"""
import argparse
import bisect
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from discovery import features as F
from discovery.jsonl_archive import iter_records
from discovery.ranker import ConvictionRanker, roc_auc
from discovery.train_ranker import (
    DB, WINDOW, RUN_MULT, SNAP_BEFORE, SNAP_AFTER, kfold_oos, lift_at,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLOG = os.path.join(ROOT, "discovery", "participation_log.jsonl")
MODELS = os.path.join(ROOT, "discovery", "models")
LIVE_MODEL = os.path.join(MODELS, "conviction_ranker.json")
CAND_MODEL = os.path.join(MODELS, "conviction_ranker.candidate.json")

BREADTH_TOL_S = 3600         # match breadth to a label within this time window
MIN_COVERED_POSITIVES = 30   # below this, the participation variant is not trusted


def load_breadth_index():
    """token -> sorted [(ts, breadth)] from forward-collected participation log.

    Spans the live participation_log AND its rolled .archive.jsonl.gz, so the
    breadth join keeps the full forward-collected history after a roll (identical
    to reading PLOG directly until tools/jsonl_roll.py first archives old lines).
    """
    idx = {}
    for r in iter_records(PLOG):
        if r.get("breadth") is None:
            continue
        tok, ts = r.get("token"), r.get("ts")
        if tok and ts:
            idx.setdefault(tok, []).append((float(ts), float(r["breadth"])))
    for t in idx:
        idx[t].sort()
    return idx


def breadth_at(idx, token, ts):
    arr = idx.get(token)
    if not arr:
        return None
    times = [a[0] for a in arr]
    i = bisect.bisect_left(times, ts)
    best_d, best_b = None, None
    for j in (i - 1, i):
        if 0 <= j < len(arr):
            d = abs(arr[j][0] - ts)
            if best_d is None or d < best_d:
                best_d, best_b = d, arr[j][1]
    return best_b if (best_d is not None and best_d <= BREADTH_TOL_S) else None


def build_dataset_with_breadth():
    idx = load_breadth_index()
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    outs = con.execute(
        "SELECT token_address, alert_timestamp, max_multiple FROM alert_outcomes "
        "WHERE window_label=? AND complete=1 AND max_multiple IS NOT NULL",
        (WINDOW,),
    ).fetchall()

    Xb, Xp, y, score_only = [], [], [], []
    covered = covered_pos = 0
    for o in outs:
        snap = con.execute(
            "SELECT * FROM signal_snapshots WHERE token_address=? "
            "AND timestamp BETWEEN ? AND ? ORDER BY timestamp DESC LIMIT 1",
            (o["token_address"], o["alert_timestamp"] - SNAP_BEFORE,
             o["alert_timestamp"] + SNAP_AFTER),
        ).fetchone()
        if snap is None:
            continue
        row = dict(snap)
        base = F.extract(row)
        br = breadth_at(idx, o["token_address"], float(o["alert_timestamp"]))
        part = F.extract(row, participation=br) if br is not None else list(base)
        label = 1 if (o["max_multiple"] or 0) >= RUN_MULT else 0
        Xb.append(base)
        Xp.append(part)
        y.append(label)
        score_only.append(float(row.get("score") or 0))
        if br is not None:
            covered += 1
            covered_pos += label
    con.close()
    return Xb, Xp, y, score_only, covered, covered_pos


def recalibrate(oos, y):
    order = sorted(range(len(oos)), key=lambda i: oos[i], reverse=True)
    rows = []
    for frac in (0.10, 0.20, 0.30):
        k = max(1, int(len(oos) * frac))
        thr = oos[order[k - 1]]
        prec = sum(y[i] for i in order[:k]) / k
        rows.append((frac, round(thr, 3), round(100 * prec, 1), k))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--promote", action="store_true",
                    help="overwrite the LIVE model (default: write candidate only)")
    args = ap.parse_args()

    Xb, Xp, y, score_only, covered, covered_pos = build_dataset_with_breadth()
    n, pos = len(Xb), sum(y)
    print(f"usable {WINDOW}-complete rows n={n} | >={RUN_MULT:g}x positives={pos} "
          f"({100 * pos / max(n, 1):.1f}%)")
    print(f"participation coverage: {covered} rows ({100 * covered / max(n, 1):.1f}%) "
          f"| covered positives={covered_pos}")
    if n < 40 or pos < 8:
        print("NOT ENOUGH DATA for a stable model; stopping.")
        return

    oos_b = kfold_oos(Xb, y)
    auc_b = roc_auc(oos_b, y)
    auc_s = roc_auc(score_only, y)
    print(f"\nOOS AUC (5-fold): base-retrain {auc_b:.3f} | existing `score` {auc_s:.3f} (baseline)")
    for frac in (0.10, 0.25, 0.50):
        k, hit, base, lift = lift_at(oos_b, y, frac)
        ks, hs, _, lifts = lift_at(score_only, y, frac)
        print(f"  top {int(frac * 100):>2}%: base hit {100 * hit:4.1f}% (lift {lift:.2f}) "
              f"| score hit {100 * hs:4.1f}% (lift {lifts:.2f})")

    if covered_pos >= MIN_COVERED_POSITIVES:
        oos_p = kfold_oos(Xp, y)
        auc_p = roc_auc(oos_p, y)
        print(f"\nparticipation variant OOS AUC {auc_p:.3f} "
              f"(vs base {auc_b:.3f}, score {auc_s:.3f})")
    else:
        print(f"\nparticipation variant SKIPPED — only {covered_pos} covered positives "
              f"(< {MIN_COVERED_POSITIVES}). Breadth collection started recently; the join "
              f"becomes meaningful as forward data accrues. Re-run on a schedule.")

    final = ConvictionRanker(F.FEATURE_NAMES)
    final.fit(Xb, y, lr=0.1, epochs=4000, l2=2.0)
    print("\nfeature importance (standardized):")
    for name, wgt in final.importance():
        print(f"  {name:22s} {wgt:+.3f}")

    print("\nthreshold recalibration (OOS base scores):")
    for frac, thr, prec, k in recalibrate(oos_b, y):
        print(f"  top {int(frac * 100):>2}% -> conviction>={thr} (n={k}, precision {prec}%)")

    os.makedirs(MODELS, exist_ok=True)
    out = LIVE_MODEL if args.promote else CAND_MODEL
    final.save(out)
    print(f"\nsaved {'LIVE (PROMOTED)' if args.promote else 'CANDIDATE'} model -> {out}")
    if not args.promote:
        print("(live model untouched — review OOS + shadow before --promote)")


if __name__ == "__main__":
    main()
