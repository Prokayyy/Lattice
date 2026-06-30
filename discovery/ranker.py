"""Calibrated conviction ranker — dependency-free logistic regression.

Trained on the bot's own alert_outcomes (label = did the alert reach >=2x within
the window). Replaces hand-tuned pass/fail route gates with a continuous,
calibrated P(>=2x) so we RANK and size by conviction instead of dropping a
runner for being 0.1 under a threshold.

Pure Python (no numpy/sklearn in the venv): standardize features, batch
gradient descent with L2, sigmoid output. Small feature set + regularization
keeps it stable on a few hundred labeled rows. Serializes to JSON so the live
scanner loads weights without retraining.
"""

import json
import math


def _sigmoid(z):
    if z < -30:
        return 0.0
    if z > 30:
        return 1.0
    return 1.0 / (1.0 + math.exp(-z))


class ConvictionRanker:
    def __init__(self, feature_names):
        self.feature_names = list(feature_names)
        self.mean = [0.0] * len(feature_names)
        self.std = [1.0] * len(feature_names)
        self.w = [0.0] * len(feature_names)
        self.b = 0.0
        # The conviction floor this model was calibrated for (e.g. the pnl model
        # records 0.30). None on older models that predate the field; callers
        # fall back to a constant. Lets the live gate track the deployed model.
        self.recommended_min_conviction = None

    # ---- standardization ----
    def _fit_scaler(self, X):
        n = len(X)
        d = len(self.feature_names)
        self.mean = [sum(row[j] for row in X) / n for j in range(d)]
        self.std = []
        for j in range(d):
            var = sum((row[j] - self.mean[j]) ** 2 for row in X) / max(n - 1, 1)
            s = math.sqrt(var)
            self.std.append(s if s > 1e-9 else 1.0)

    def _scale(self, x):
        return [(x[j] - self.mean[j]) / self.std[j] for j in range(len(x))]

    # ---- training ----
    def fit(self, X, y, lr=0.1, epochs=4000, l2=1.0, sample_weight=None):
        self._fit_scaler(X)
        Xs = [self._scale(row) for row in X]
        n = len(Xs)
        d = len(self.feature_names)
        if sample_weight is None:
            sample_weight = [1.0] * n
        sw_sum = sum(sample_weight)
        self.w = [0.0] * d
        self.b = 0.0
        for _ in range(epochs):
            gw = [0.0] * d
            gb = 0.0
            for i in range(n):
                z = self.b + sum(self.w[j] * Xs[i][j] for j in range(d))
                err = (_sigmoid(z) - y[i]) * sample_weight[i]
                for j in range(d):
                    gw[j] += err * Xs[i][j]
                gb += err
            for j in range(d):
                # L2 shrinkage (not applied to bias)
                self.w[j] -= lr * (gw[j] / sw_sum + l2 * self.w[j] / n)
            self.b -= lr * (gb / sw_sum)
        return self

    # ---- inference ----
    def proba_scaled(self, xs):
        z = self.b + sum(self.w[j] * xs[j] for j in range(len(xs)))
        return _sigmoid(z)

    def proba(self, x):
        return self.proba_scaled(self._scale(x))

    # standardized weights = importance ranking (features are on equal scale)
    def importance(self):
        return sorted(
            zip(self.feature_names, self.w),
            key=lambda kv: abs(kv[1]),
            reverse=True,
        )

    # ---- persistence ----
    def to_dict(self):
        d = {
            "feature_names": self.feature_names,
            "mean": self.mean,
            "std": self.std,
            "w": self.w,
            "b": self.b,
        }
        if self.recommended_min_conviction is not None:
            d["recommended_min_conviction"] = self.recommended_min_conviction
        return d

    def save(self, path):
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, indent=2)

    @classmethod
    def from_dict(cls, d):
        m = cls(d["feature_names"])
        m.mean, m.std, m.w, m.b = d["mean"], d["std"], d["w"], d["b"]
        m.recommended_min_conviction = d.get("recommended_min_conviction")
        return m

    @classmethod
    def load(cls, path):
        with open(path, encoding="utf-8") as fh:
            return cls.from_dict(json.load(fh))


def roc_auc(scores, labels):
    """Mann-Whitney AUC; ties get 0.5 credit. No deps."""
    pos = [s for s, y in zip(scores, labels) if y == 1]
    neg = [s for s, y in zip(scores, labels) if y == 0]
    if not pos or not neg:
        return float("nan")
    wins = 0.0
    for p in pos:
        for q in neg:
            if p > q:
                wins += 1
            elif p == q:
                wins += 0.5
    return wins / (len(pos) * len(neg))
