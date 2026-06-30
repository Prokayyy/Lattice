"""Build the labeled dataset from alert_outcomes x signal_snapshots, evaluate
the conviction ranker out-of-sample (5-fold), compare it to the existing
`score` as a ranker, train the final model, and write model + report.

Run: env/bin/python -m discovery.train_ranker
"""

import os
import random
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from discovery import features as F
from discovery.ranker import ConvictionRanker, roc_auc
from storage.history import open_history

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scanner.db")
WINDOW = "1h"
RUN_MULT = 2.0          # label: did the alert reach >=2x within the window
SNAP_BEFORE = 300       # seconds before alert to look for the decision snapshot
SNAP_AFTER = 10


def build_dataset():
    con = open_history(read_only=True)  # spans hot + archive (post-trim safe)
    outs = con.execute(
        "SELECT alert_id, token_address, alert_timestamp, max_multiple "
        "FROM alert_outcomes WHERE window_label=? AND complete=1 "
        "AND max_multiple IS NOT NULL",
        (WINDOW,),
    ).fetchall()

    X, y, score_only, meta = [], [], [], []
    matched = 0
    for o in outs:
        snap = con.execute(
            "SELECT * FROM signal_snapshots_all WHERE token_address=? "
            "AND timestamp BETWEEN ? AND ? ORDER BY timestamp DESC LIMIT 1",
            (o["token_address"], o["alert_timestamp"] - SNAP_BEFORE,
             o["alert_timestamp"] + SNAP_AFTER),
        ).fetchone()
        if snap is None:
            continue
        matched += 1
        row = dict(snap)
        X.append(F.extract(row))
        label = 1 if (o["max_multiple"] or 0) >= RUN_MULT else 0
        y.append(label)
        # existing score available at decision time, for the baseline ranker
        score_only.append(float(row.get("score") or 0))
        meta.append((o["token_address"], o["max_multiple"]))
    con.close()
    return X, y, score_only, meta, len(outs), matched


def kfold_oos(X, y, k=5, seed=13):
    idx = list(range(len(X)))
    random.Random(seed).shuffle(idx)
    folds = [idx[i::k] for i in range(k)]
    oos = [0.0] * len(X)
    for f in range(k):
        test = set(folds[f])
        tr = [i for i in idx if i not in test]
        m = ConvictionRanker(F.FEATURE_NAMES)
        m.fit([X[i] for i in tr], [y[i] for i in tr], lr=0.1, epochs=3000, l2=2.0)
        for i in folds[f]:
            oos[i] = m.proba(X[i])
    return oos


def lift_at(scores, labels, top_frac):
    base = sum(labels) / len(labels)
    order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
    k = max(1, int(len(scores) * top_frac))
    top = order[:k]
    hit = sum(labels[i] for i in top) / k
    return k, hit, base, (hit / base if base > 0 else float("nan"))


def main():
    X, y, score_only, meta, n_out, matched = build_dataset()
    n = len(X)
    pos = sum(y)
    print(f"alert_outcomes({WINDOW},complete)={n_out}  with feature snapshot={matched}")
    print(f"usable rows n={n}  >=2x positives={pos} ({100*pos/max(n,1):.1f}%)")
    if n < 40 or pos < 8:
        print("NOT ENOUGH DATA for a stable model; stopping.")
        return

    oos = kfold_oos(X, y)
    auc_model = roc_auc(oos, y)
    auc_score = roc_auc(score_only, y)   # existing score as a ranker (baseline)
    print(f"\nOut-of-sample AUC (5-fold):")
    print(f"  conviction ranker : {auc_model:.3f}")
    print(f"  existing `score`  : {auc_score:.3f}   (baseline the gates use)")

    print(f"\nTop-quartile hit-rate (out-of-sample, base rate {100*pos/n:.1f}%):")
    for frac in (0.10, 0.25, 0.50):
        k, hit, base, lift = lift_at(oos, y, frac)
        ks, hs, _, lifts = lift_at(score_only, y, frac)
        print(f"  top {int(frac*100):>2}% (n={k:>3}): ranker hit {100*hit:>5.1f}% (lift {lift:.2f}x) | "
              f"score hit {100*hs:>5.1f}% (lift {lifts:.2f}x)")

    # final model on all data + importance
    final = ConvictionRanker(F.FEATURE_NAMES)
    final.fit(X, y, lr=0.1, epochs=4000, l2=2.0)
    print("\nFeature importance (standardized weights):")
    for name, wgt in final.importance():
        print(f"  {name:22s} {wgt:+.3f}")

    os.makedirs(os.path.join(os.path.dirname(__file__), "models"), exist_ok=True)
    mp = os.path.join(os.path.dirname(__file__), "models", "conviction_ranker.json")
    final.save(mp)
    print(f"\nsaved model -> {mp}")

    # machine-readable result for the report
    return {
        "n": n, "positives": pos, "base_rate": pos / n,
        "auc_model": auc_model, "auc_score": auc_score,
        "importance": final.importance(),
    }


if __name__ == "__main__":
    main()
