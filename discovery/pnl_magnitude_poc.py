"""Magnitude-weighted PnL ranking PoC (the follow-on to pnl_label_poc).

The binary "profitable yes/no" model beat `score` at predicting profitability but
NOT at top-K dollars, because total PnL is fat-tail-dominated and the binary
target ignores trade SIZE. This script tests two magnitude-aware rankers that
target dollars directly, reusing pnl_label_poc's de-glitched forward-replay:

  - mag-logistic : the existing logistic ranker, but each training row is
                   SAMPLE-WEIGHTED by its dollar magnitude (big trades matter
                   more). Reuses ConvictionRanker.fit(sample_weight=...).
  - pnl-regressor: a small pure-Python Huber-loss linear regression that predicts
                   E[PnL|features] and ranks by predicted dollars — the ranking
                   objective that actually maximizes selected top-K PnL.

Compared against the binary model and the existing `score` on the same money
test (top-K realized PnL). Read-only; touches no live model.

Run: env/bin/python -m discovery.pnl_magnitude_poc
"""
import os
import random
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from discovery import features as F
from discovery.ranker import ConvictionRanker, roc_auc
from discovery.train_ranker import DB
from discovery.pnl_label_poc import build, topk, SIZE_USD


class PnLRegressor:
    """Dependency-free linear regression with Huber loss + L2, predicting PnL.

    Huber (vs plain squared loss) keeps the few large winners from dominating the
    fit on a small fat-tailed sample, so the estimate stays close to E[PnL]."""

    def __init__(self, feature_names):
        self.feature_names = list(feature_names)
        self.mean = [0.0] * len(feature_names)
        self.std = [1.0] * len(feature_names)
        self.w = [0.0] * len(feature_names)
        self.b = 0.0

    def _fit_scaler(self, X):
        import math
        n, d = len(X), len(self.feature_names)
        self.mean = [sum(r[j] for r in X) / n for j in range(d)]
        self.std = []
        for j in range(d):
            var = sum((r[j] - self.mean[j]) ** 2 for r in X) / max(n - 1, 1)
            s = math.sqrt(var)
            self.std.append(s if s > 1e-9 else 1.0)

    def _scale(self, x):
        return [(x[j] - self.mean[j]) / self.std[j] for j in range(len(x))]

    def fit(self, X, y, lr=0.02, epochs=4000, l2=1.0, huber=5.0):
        self._fit_scaler(X)
        Xs = [self._scale(r) for r in X]
        n, d = len(Xs), len(self.feature_names)
        self.w = [0.0] * d
        self.b = 0.0
        for _ in range(epochs):
            gw = [0.0] * d
            gb = 0.0
            for i in range(n):
                pred = self.b + sum(self.w[j] * Xs[i][j] for j in range(d))
                r = pred - y[i]
                g = r if abs(r) <= huber else huber * (1.0 if r > 0 else -1.0)
                for j in range(d):
                    gw[j] += g * Xs[i][j]
                gb += g
            for j in range(d):
                self.w[j] -= lr * (gw[j] / n + l2 * self.w[j] / n)
            self.b -= lr * (gb / n)
        return self

    def predict(self, x):
        xs = self._scale(x)
        return self.b + sum(self.w[j] * xs[j] for j in range(len(xs)))


def kfold_oos_generic(n, fit_predict, k=5, seed=13):
    """Return OOS predictions for each row using a fit_predict(train_idx, test_idx)
    closure. Same fold layout/seed as train_ranker.kfold_oos for fair comparison."""
    idx = list(range(n))
    random.Random(seed).shuffle(idx)
    folds = [idx[i::k] for i in range(k)]
    oos = [0.0] * n
    for f in range(k):
        test = folds[f]
        tr = [i for i in idx if i not in set(test)]
        for i, v in fit_predict(tr, test).items():
            oos[i] = v
    return oos


def main():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    X, pnl, touch, score, n_match, n_label = build(con)
    n = len(X)
    if n < 40:
        print(f"not enough labelable rows (n={n}); stopping.")
        return

    y_profit = [1 if p > 0 else 0 for p in pnl]
    # magnitude sample weight: bounded so no single trade dominates (1..~6)
    w_mag = [1.0 + min(abs(p), 50.0) / 10.0 for p in pnl]
    # regression target: clip the fat tails to a sane band
    y_reg = [max(-25.0, min(75.0, p)) for p in pnl]

    print(f"labelable n={n} | profitable {sum(y_profit)} ({100*sum(y_profit)/n:.1f}%) | "
          f"take-all ${sum(pnl):.2f} (mean ${sum(pnl)/n:+.2f}, size ${SIZE_USD:.0f})")

    def fp_binary(tr, te):
        m = ConvictionRanker(F.FEATURE_NAMES)
        m.fit([X[i] for i in tr], [y_profit[i] for i in tr], l2=2.0)
        return {i: m.proba(X[i]) for i in te}

    def fp_weighted(tr, te):
        m = ConvictionRanker(F.FEATURE_NAMES)
        m.fit([X[i] for i in tr], [y_profit[i] for i in tr], l2=2.0,
              sample_weight=[w_mag[i] for i in tr])
        return {i: m.proba(X[i]) for i in te}

    def fp_reg(tr, te):
        r = PnLRegressor(F.FEATURE_NAMES)
        r.fit([X[i] for i in tr], [y_reg[i] for i in tr], l2=2.0)
        return {i: r.predict(X[i]) for i in te}

    oos_bin = kfold_oos_generic(n, fp_binary)
    oos_mag = kfold_oos_generic(n, fp_weighted)
    oos_reg = kfold_oos_generic(n, fp_reg)

    print(f"\nOOS AUC predicting PROFIT: binary {roc_auc(oos_bin, y_profit):.3f} | "
          f"mag-logistic {roc_auc(oos_mag, y_profit):.3f} | "
          f"pnl-reg {roc_auc(oos_reg, y_profit):.3f} | score {roc_auc(score, y_profit):.3f}")

    print("\nThe money test — top-K realized PnL (the number that matters):")
    cols = [("binary", oos_bin), ("mag-logistic", oos_mag),
            ("pnl-reg", oos_reg), ("score", score)]
    head = f"{'selection':>13} " + " ".join(f"{c[0]:>16}" for c in cols)
    print(head)
    print("-" * len(head))
    for frac in (0.10, 0.20, 0.30, 0.50):
        cells = []
        k = 0
        for _, s in cols:
            k, tot, avg, win = topk(s, pnl, frac)
            cells.append(f"${tot:>7.2f}({avg:+.2f})")
        print(f"{'top '+str(int(frac*100))+'% (n='+str(k)+')':>13} " +
              " ".join(f"{c:>16}" for c in cells))
    print("\n($ = total realized PnL of the selected alerts; (/pick) in parens. "
          "Higher = picks more total dollars.)")


if __name__ == "__main__":
    main()
