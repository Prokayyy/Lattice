"""
Conviction-ranker training on Google Colab (free compute, saves your Claude tokens).

HOW TO USE
1. Locally:  env/bin/python tools/export_training_data.py
   -> writes discovery/models/training_data.csv
2. Open https://colab.research.google.com  -> New notebook.
3. Paste this whole file into one cell and run it.
4. When prompted, upload training_data.csv.
5. It prints out-of-sample AUC (model vs your existing `score`) + feature
   importances, trains the final model, and downloads conviction_ranker.json.
6. Drop that file into discovery/models/conviction_ranker.json on the box
   (back it up first). The live scanner loads it via ConvictionRanker.load —
   no code change, same JSON format.

This reproduces discovery/ranker.py EXACTLY (same standardize + GD logistic
regression + JSON format) so the output is a drop-in replacement, and ALSO
shows a LightGBM/LogReg comparison so you can see if a fancier model would
add signal before bothering to wire one in.
"""

import json
import math
import random

# ----- 0. load data (Colab upload, or a local path fallback) -----------------
try:
    from google.colab import files  # type: ignore
    import io
    up = files.upload()                       # pick training_data.csv
    import pandas as pd
    fname = list(up.keys())[0]
    df = pd.read_csv(io.BytesIO(up[fname]))
except Exception:
    import pandas as pd
    df = pd.read_csv("training_data.csv")     # local fallback

# Feature order is the contract with discovery/features.py — do not reorder.
FEATURE_NAMES = [
    "price_change_5m", "price_change_5m_sq", "price_change_1h", "impulse",
    "pressure", "vlr", "h1_vlr", "buy_sell_ratio", "h1_buy_sell_ratio",
    "buy_sell_asym_5m", "log_volume_1h", "log_liquidity", "score",
    "participation_breadth",
]
X = df[FEATURE_NAMES].astype(float).values.tolist()
y = df["label"].astype(int).tolist()
score_only = df["score"].astype(float).tolist()
n, pos = len(y), sum(y)
print(f"rows={n}  positives={pos} ({100*pos/max(n,1):.1f}% base rate)")
assert n >= 40 and pos >= 8, "too little data for a stable model"


# ----- 1. ConvictionRanker (identical to discovery/ranker.py) -----------------
def _sigmoid(z):
    if z < -30: return 0.0
    if z > 30:  return 1.0
    return 1.0 / (1.0 + math.exp(-z))


class ConvictionRanker:
    def __init__(self, feature_names):
        self.feature_names = list(feature_names)
        d = len(feature_names)
        self.mean = [0.0]*d; self.std = [1.0]*d; self.w = [0.0]*d; self.b = 0.0

    def _fit_scaler(self, X):
        n = len(X); d = len(self.feature_names)
        self.mean = [sum(r[j] for r in X)/n for j in range(d)]
        self.std = []
        for j in range(d):
            var = sum((r[j]-self.mean[j])**2 for r in X)/max(n-1, 1)
            s = math.sqrt(var); self.std.append(s if s > 1e-9 else 1.0)

    def _scale(self, x):
        return [(x[j]-self.mean[j])/self.std[j] for j in range(len(x))]

    def fit(self, X, y, lr=0.1, epochs=4000, l2=2.0):
        self._fit_scaler(X)
        Xs = [self._scale(r) for r in X]; n = len(Xs); d = len(self.feature_names)
        self.w = [0.0]*d; self.b = 0.0
        for _ in range(epochs):
            gw = [0.0]*d; gb = 0.0
            for i in range(n):
                z = self.b + sum(self.w[j]*Xs[i][j] for j in range(d))
                err = _sigmoid(z) - y[i]
                for j in range(d): gw[j] += err*Xs[i][j]
                gb += err
            for j in range(d): self.w[j] -= lr*(gw[j]/n + l2*self.w[j]/n)
            self.b -= lr*(gb/n)
        return self

    def proba(self, x):
        xs = self._scale(x)
        return _sigmoid(self.b + sum(self.w[j]*xs[j] for j in range(len(xs))))

    def importance(self):
        return sorted(zip(self.feature_names, self.w),
                      key=lambda kv: abs(kv[1]), reverse=True)

    def to_dict(self):
        return {"feature_names": self.feature_names, "mean": self.mean,
                "std": self.std, "w": self.w, "b": self.b}


def roc_auc(scores, labels):
    p = [s for s, l in zip(scores, labels) if l == 1]
    q = [s for s, l in zip(scores, labels) if l == 0]
    if not p or not q: return float("nan")
    wins = sum((1.0 if a > b else 0.5 if a == b else 0.0) for a in p for b in q)
    return wins/(len(p)*len(q))


# ----- 2. out-of-sample eval (5-fold) ---------------------------------------
def kfold_oos(X, y, k=5, seed=13):
    idx = list(range(len(X))); random.Random(seed).shuffle(idx)
    folds = [idx[i::k] for i in range(k)]; oos = [0.0]*len(X)
    for f in range(k):
        test = set(folds[f]); tr = [i for i in idx if i not in test]
        m = ConvictionRanker(FEATURE_NAMES)
        m.fit([X[i] for i in tr], [y[i] for i in tr])
        for i in folds[f]: oos[i] = m.proba(X[i])
    return oos


oos = kfold_oos(X, y)
print(f"\nOut-of-sample AUC (5-fold):")
print(f"  conviction ranker : {roc_auc(oos, y):.3f}")
print(f"  existing `score`  : {roc_auc(score_only, y):.3f}  (current baseline)")


def lift_at(scores, labels, frac):
    order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
    k = max(1, int(len(scores)*frac)); top = order[:k]
    base = sum(labels)/len(labels); hit = sum(labels[i] for i in top)/k
    return k, hit, (hit/base if base else float("nan"))


print("\nTop-bucket hit-rate (out-of-sample):")
for frac in (0.10, 0.25, 0.50):
    k, hit, lift = lift_at(oos, y, frac)
    _, hs, lifts = lift_at(score_only, y, frac)
    print(f"  top {int(frac*100):>2}% (n={k:>3}): ranker {100*hit:5.1f}% "
          f"(lift {lift:.2f}x) | score {100*hs:5.1f}% (lift {lifts:.2f}x)")


# ----- 3. final model + importance + export ---------------------------------
final = ConvictionRanker(FEATURE_NAMES).fit(X, y)
print("\nFeature importance (standardized weights):")
for name, w in final.importance():
    print(f"  {name:22s} {w:+.3f}")

with open("conviction_ranker.json", "w") as fh:
    json.dump(final.to_dict(), fh, indent=2)
print("\nwrote conviction_ranker.json (drop into discovery/models/)")
try:
    from google.colab import files  # type: ignore
    files.download("conviction_ranker.json")
except Exception:
    pass


# ----- 4. would a fancier model help? (Colab-only comparison) ----------------
print("\n--- comparison: does a stronger model beat the logistic ranker? ---")
try:
    import numpy as np
    from sklearn.model_selection import cross_val_predict
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    from sklearn.pipeline import make_pipeline
    Xn, yn = np.array(X), np.array(y)
    lr = make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000, C=0.5))
    p_lr = cross_val_predict(lr, Xn, yn, cv=5, method="predict_proba")[:, 1]
    print(f"  sklearn LogReg AUC : {roc_auc(list(p_lr), y):.3f}")
    try:
        import lightgbm as lgb
    except Exception:
        import subprocess, sys as _s
        subprocess.run([_s.executable, "-m", "pip", "install", "-q", "lightgbm"])
        import lightgbm as lgb
    gbm = lgb.LGBMClassifier(n_estimators=200, max_depth=3, learning_rate=0.05,
                             subsample=0.8, verbose=-1)
    p_gbm = cross_val_predict(gbm, Xn, yn, cv=5, method="predict_proba")[:, 1]
    print(f"  LightGBM AUC       : {roc_auc(list(p_gbm), y):.3f}")
    print("\nIf LightGBM clearly beats the logistic ranker (e.g. +0.05 AUC) on")
    print("ENOUGH data, it's worth wiring a tree model into the live scanner.")
    print("If not, the dependency-free logistic ranker is the right call.")
except Exception as e:
    print("  (sklearn/lightgbm comparison skipped:", e, ")")
