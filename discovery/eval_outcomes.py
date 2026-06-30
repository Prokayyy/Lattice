"""Evaluate the conviction model on the discovery-native outcomes store.

The payoff test: train on the ALIGNED population (discovery's own conviction
survivors, with participation breadth attached 99.8% of the time) instead of the
dead/mismatched main-bot alert_outcomes. Reports OOS AUC of base (breadth-neutral)
vs participation-aware vs the existing `score`, for both labels (touch-2x and
profit-under-engine), and whether `participation_breadth` finally carries weight.

Reads discovery_outcomes.jsonl only — no DB, no replay. Touches no live model.

Run: env/bin/python -m discovery.eval_outcomes
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from discovery import features as F
from discovery.jsonl_archive import iter_records
from discovery.ranker import ConvictionRanker, roc_auc
from discovery.train_ranker import kfold_oos, lift_at

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "discovery", "discovery_outcomes.jsonl")


def load():
    # Spans the live file AND its rolled .archive.jsonl.gz, so training on the
    # full accrued population is unaffected once tools/jsonl_roll.py ages old
    # outcomes into the archive (identical to reading OUT directly until then).
    rows = []
    for r in iter_records(OUT):
        if r.get("no_data") or not r.get("row"):
            continue
        rows.append(r)
    return rows


def main():
    rows = load()
    n = len(rows)
    if n < 40:
        print(f"not enough rows (n={n})")
        return
    X_base, X_part, score = [], [], []
    y_touch, y_profit, br_n = [], [], 0
    for r in rows:
        row = r["row"]
        X_base.append(F.extract(row))
        br = r.get("breadth")
        if br is not None:
            br_n += 1
        X_part.append(F.extract(row, participation=br))
        score.append(float(row.get("score") or 0))
        y_touch.append(1 if (r.get("max_mult_1h") or 0) >= 2.0 else 0)
        y_profit.append(1 if (r.get("realized_pnl") or 0) > 0 else 0)

    print(f"discovery_outcomes training set: n={n} | breadth attached {br_n} "
          f"({100*br_n/n:.1f}%)")
    print(f"base rates: >=2x@1h {sum(y_touch)} ({100*sum(y_touch)/n:.1f}%) | "
          f"profit {sum(y_profit)} ({100*sum(y_profit)/n:.1f}%)")

    for tgt_name, y in (("TOUCH-2x", y_touch), ("PROFIT-under-engine", y_profit)):
        oos_base = kfold_oos(X_base, y)
        oos_part = kfold_oos(X_part, y)
        print(f"\n== target: {tgt_name} ==")
        print(f"  OOS AUC: base {roc_auc(oos_base, y):.3f} | "
              f"+participation {roc_auc(oos_part, y):.3f} | "
              f"score {roc_auc(score, y):.3f}")
        for frac in (0.10, 0.25):
            _, hb, _, lb = lift_at(oos_part, y, frac)
            _, hs, _, ls = lift_at(score, y, frac)
            print(f"  top {int(frac*100):>2}%: +participation lift {lb:.2f}x "
                  f"({100*hb:.1f}%) | score lift {ls:.2f}x ({100*hs:.1f}%)")

    # does participation finally carry weight? (train on the profit target)
    final = ConvictionRanker(F.FEATURE_NAMES)
    final.fit(X_part, y_profit, lr=0.1, epochs=4000, l2=2.0)
    imp = dict(final.importance())
    print(f"\nparticipation_breadth standardized weight (profit model): "
          f"{imp.get('participation_breadth', 0.0):+.3f}  "
          f"(was +0.000 on the dead source)")
    print("top 6 features:", [f"{k}={v:+.2f}" for k, v in final.importance()[:6]])


if __name__ == "__main__":
    main()
