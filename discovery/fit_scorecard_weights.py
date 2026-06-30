"""Offline per-axis weight fit for the capital scorecard (scanner redesign, §6).

The scorecard (discovery/scorecard.py) ships DIRECTIONAL SEED weights. This tool
refreshes them by regressing recorded forward outcomes on the *raw* (unweighted)
axis contributions, so the capital tiers rank on fitted evidence instead of hand-
set priors -- WITHOUT the auto-deploy that made the conviction float dangerous
(discovery/redesign_validate.py shows the float anti-ranks outcomes). It only
PRINTS proposed LATTICE_SCORE_W_* and an offline before/after. DEPLOYING is a
deliberate, manual env change, re-gated by discovery/redesign_validate.py -- this
script never writes a config, a model, or restarts anything.

Method (deliberately conservative on thin, token-clustered data):
  features = discovery.scorecard.raw_axes   (the exact axes the live gate sums)
  label    = BIG  (max_mult_1h >= BIG_MULT, the Layer-2 promotion target)
  model    = L2-regularized, class-weighted logistic regression, fit in RAW axis
             space. The axes are already on a comparable [-1,1] scale, so we do
             NOT standardize -> the coefficients ARE the deployable weights, and
             there is no unstandardize blow-up on sparse axes like `actor`.
  split    = TEMPORAL (fit past -> score future): the deploy-time reality, and it
             keeps a token's time-clustered outcomes mostly on one side of the cut.
  accept   = recommend deploying ONLY if the fitted weights beat the seed weights'
             Tier-A lift OUT-OF-SAMPLE *and* Tier-A still beats base. Sparse /
             sign-flipped axes are flagged (their fitted value is unreliable).

Run:  env/bin/python discovery/fit_scorecard_weights.py
      env/bin/python discovery/fit_scorecard_weights.py --emit-env   # export lines
      env/bin/python discovery/fit_scorecard_weights.py --json out.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import numpy as np  # noqa: E402  (offline tooling only; venv has numpy 2.x)

from discovery import redesign_validate as RV  # noqa: E402  loaders + labels + ST join
from discovery import scorecard as SC          # noqa: E402
from discovery.ranker import roc_auc           # noqa: E402  house AUC, dep-free

AXES = list(SC.WEIGHTS.keys())     # canonical axis order (also the env-suffix order)
TIER_A_PCT = 60.0                  # match redesign_validate's Layer-2 default cut
TRAIN_FRAC = 0.70                  # temporal split
LOW_SUPPORT = 0.08                 # below this nonzero fraction the fit is unreliable


# ---- dataset ---------------------------------------------------------------

def build_dataset():
    """rows: list of dicts {ts, raw, vec, big, dead, floors_ok, token}. Reuses the
    harness loaders, the (token, int(alert_ts)) ST join, and the BIG/DEAD labels so
    the fit is judged on the same evidence as the promotion gate."""
    outs = RV.load_outcomes()
    bidx = RV.load_bundle_index()
    rows = []
    for o in outs:
        st = RV.st_for(o, bidx)
        row = RV._row(o)
        detail = {"buyers_sig": o.get("buyers_sig")}
        raw = SC.raw_axes(row, detail=detail, st_bundle=st,
                          conviction=RV._f(o.get("conviction")))
        floors_ok, _r = SC.passes_tier_a_floors(row, detail=detail, st_bundle=st)
        rows.append({
            "ts": RV._f(o.get("alert_ts")) or 0.0,
            "raw": raw,
            "vec": [raw[a] for a in AXES],
            "big": 1 if RV.is_big(o) else 0,
            "dead": 1 if RV.is_dead(o) else 0,
            "floors_ok": floors_ok,
            "token": o.get("token"),
        })
    rows.sort(key=lambda r: r["ts"])  # temporal order for the split
    return rows


# ---- model -----------------------------------------------------------------

def fit_logistic(X, y, l2=2.0, lr=0.3, epochs=8000, nonneg=False):
    """Full-batch GD logistic regression in RAW feature space. L2 on weights only
    (not bias); class-weighted so the ~6% BIG class is not ignored. Mirrors
    discovery/ranker.ConvictionRanker's recipe, vectorized with numpy for speed.

    nonneg: project weights to >= 0 each step. Every scorecard axis is oriented
    higher=better, so a negative weight is nonsensical (collinearity/noise); the
    projection forbids the sign-flips a free fit produces on correlated axes.
    Returns (w: np.ndarray[d], b: float)."""
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=float)
    n, d = X.shape
    npos = max(float(y.sum()), 1.0)
    nneg = max(n - npos, 1.0)
    sw = np.where(y > 0.5, n / (2.0 * npos), n / (2.0 * nneg))  # balanced
    sw_sum = sw.sum()
    w = np.zeros(d)
    b = 0.0
    for _ in range(epochs):
        z = np.clip(b + X @ w, -30, 30)
        p = 1.0 / (1.0 + np.exp(-z))
        err = (p - y) * sw
        gw = X.T @ err / sw_sum + l2 * w / n
        gb = err.sum() / sw_sum
        w -= lr * gw
        b -= lr * gb
        if nonneg:
            w = np.maximum(w, 0.0)  # projected gradient step
    return w, b


# ---- evaluation (mirrors redesign_validate.scorecard_tiers) ----------------

def score_rows(rows, weights):
    wv = np.array([weights[a] for a in AXES])
    return [float(np.dot(r["vec"], wv)) for r in rows]


def tier_a_eval(rows, weights, pct=TIER_A_PCT):
    """Tier-A = score >= the pct-percentile cut AND clears the absolute floors.
    Reports win% (Tier-A BIG-rate), base win%, lift, and Tier-A dead%."""
    if not rows:
        return None
    scores = score_rows(rows, weights)
    vals = sorted(scores)
    cut = vals[int(pct / 100.0 * (len(vals) - 1))]
    tier_a = [r for r, s in zip(rows, scores) if s >= cut and r["floors_ok"]]
    base_big = sum(r["big"] for r in rows) / len(rows)
    if not tier_a:
        return {"n": 0, "win": 0.0, "base": 100 * base_big, "lift": None, "dead": 0.0}
    a_big = sum(r["big"] for r in tier_a) / len(tier_a)
    a_dead = sum(r["dead"] for r in tier_a) / len(tier_a)
    return {"n": len(tier_a), "win": 100 * a_big, "base": 100 * base_big,
            "lift": (a_big / base_big) if base_big else None, "dead": 100 * a_dead}


def _auc(rows, weights):
    if not rows:
        return float("nan")
    return roc_auc(score_rows(rows, weights), [r["big"] for r in rows])


# ---- main ------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--emit-env", action="store_true",
                    help="also print ready-to-paste `export LATTICE_SCORE_W_*` lines")
    ap.add_argument("--json", help="write the full report as JSON to this path")
    ap.add_argument("--l2", type=float, default=2.0)
    ap.add_argument("--epochs", type=int, default=8000)
    args = ap.parse_args()

    rows = build_dataset()
    n = len(rows)
    if n < 200:
        print(f"only {n} outcomes -- too thin to fit; keep the seed weights.")
        return 1

    cut_i = int(TRAIN_FRAC * n)
    train, oos = rows[:cut_i], rows[cut_i:]
    import datetime as dt
    span = lambda rs: (f"{dt.datetime.fromtimestamp(rs[0]['ts'], dt.UTC):%Y-%m-%d}.."
                       f"{dt.datetime.fromtimestamp(rs[-1]['ts'], dt.UTC):%Y-%m-%d}")
    nbig = sum(r["big"] for r in rows)
    print(f"outcomes={n}  BIG={nbig} ({100*nbig/n:.1f}%)  "
          f"DEAD={sum(r['dead'] for r in rows)} ({100*sum(r['dead'] for r in rows)/n:.1f}%)")
    print(f"temporal split: train {len(train)} [{span(train)}]  ->  "
          f"OOS {len(oos)} [{span(oos)}]  (BIG train {sum(r['big'] for r in train)} / "
          f"OOS {sum(r['big'] for r in oos)})\n")

    # nonzero support per axis (flags low-confidence fits, e.g. `actor` ~ 4%)
    support = {a: sum(1 for r in rows if abs(r["raw"][a]) > 1e-9) / n for a in AXES}

    seed = dict(SC.WEIGHTS)
    seed_l1 = sum(abs(v) for v in seed.values())

    def _rescale(vec):
        """Rescale a weight dict to the seed L1 norm. Ranking is scale-free, so
        this only makes the emitted env values readable / seed-comparable."""
        l1 = sum(abs(v) for v in vec.values()) or 1.0
        return {a: vec[a] * seed_l1 / l1 for a in AXES}

    # Fit on the training slice only, NON-NEGATIVE: every axis is oriented
    # higher=better, so we forbid the nonsensical sign-flips a free fit produced
    # on the correlated momentum/flow axes.
    Xtr = [r["vec"] for r in train]
    ytr = [r["big"] for r in train]
    w, _b = fit_logistic(Xtr, ytr, l2=args.l2, epochs=args.epochs, nonneg=True)
    fitted = _rescale({a: float(w[i]) for i, a in enumerate(AXES)})

    print("== per-axis weights (seed -> fitted, non-negative) ==")
    print(f"  {'axis':<18}{'seed':>7}{'fitted':>9}{'support':>9}   flags")
    flags_by_axis = {}
    for a in AXES:
        fl = ["LOW-SUPPORT"] if support[a] < LOW_SUPPORT else []
        flags_by_axis[a] = fl
        print(f"  {a:<18}{seed[a]:>7.2f}{fitted[a]:>9.3f}{100*support[a]:>8.1f}%   "
              f"{' '.join(fl)}")

    # Shrink-to-prior blend sweep: lam=0 is the pure seed, lam=1 the pure
    # constrained fit. The in-between answers the real question -- does ANY
    # data-informed move off the prior help OUT-OF-SAMPLE? Endpoints are L1-unit
    # normalized so the blend is a true direction interpolation; lam=0 reproduces
    # the seed exactly.
    su = {a: seed[a] / seed_l1 for a in AXES}
    fl1 = sum(abs(v) for v in fitted.values()) or 1.0
    fu = {a: fitted[a] / fl1 for a in AXES}
    grid = [0.0, 0.25, 0.5, 0.75, 1.0]
    print("\n== shrink-to-seed blend sweep (Tier-A @ p60; OOS is the decision) ==")
    print(f"  {'lambda':>7}{'OOS win':>9}{'OOS lift':>9}{'OOS dead':>9}"
          f"{'full win':>9}{'full lift':>10}")
    sweep = []
    for lam in grid:
        wb = _rescale({a: (1 - lam) * su[a] + lam * fu[a] for a in AXES})
        eo = tier_a_eval(oos, wb)
        ef = tier_a_eval(rows, wb)
        sweep.append({"lam": lam, "weights": wb, "oos": eo, "full": ef})
        lo = f"{eo['lift']:.2f}x" if eo["lift"] else " n/a"
        lf = f"{ef['lift']:.2f}x" if ef["lift"] else " n/a"
        print(f"  {lam:>7.2f}{eo['win']:>8.1f}%{lo:>9}{eo['dead']:>8.1f}%"
              f"{ef['win']:>8.1f}%{lf:>10}")
    print(f"\n  OOS AUC(BIG):  seed {_auc(oos, seed):.3f}   "
          f"fit(lam=1) {_auc(oos, fitted):.3f}  (0.5 = no rank, <0.5 = anti-rank)")

    # Decision on OUT-OF-SAMPLE only: the best lambda must MATERIALLY beat the
    # pure seed (>=5% relative OOS lift) without going deadier; else the prior
    # wins and we keep the seeds.
    base_oos = sweep[0]              # lam=0 == pure seed
    best = max(sweep, key=lambda s: (s["oos"]["lift"] or 0))
    seed_lift = base_oos["oos"]["lift"] or 0
    best_lift = best["oos"]["lift"] or 0
    material = best["lam"] > 0 and best_lift >= 1.05 * seed_lift
    not_deadier = best["oos"]["dead"] <= base_oos["oos"]["dead"] + 1.0
    recommend = material and not_deadier and best["oos"]["win"] > best["oos"]["base"]
    final = best["weights"] if recommend else seed

    print("\n== recommendation ==")
    if recommend:
        print(f"  DEPLOY blended weights @ lambda={best['lam']:.2f}: OOS lift "
              f"{best_lift:.2f}x vs seed {seed_lift:.2f}x (>=5% better), not deadier. "
              f"Re-run redesign_validate.py with the env below BEFORE you deploy.")
    else:
        print(f"  KEEP the seed weights. Best OOS lambda={best['lam']:.2f} @ "
              f"{best_lift:.2f}x vs seed {seed_lift:.2f}x -- no material, non-deadier "
              f"out-of-sample gain. Even constrained, the fit AUC-collapses OOS "
              f"({_auc(oos, fitted):.2f}); the directional seeds generalize better.")

    if args.emit_env or recommend:
        tag = f"lambda={best['lam']:.2f} blend" if recommend else "seed (unchanged)"
        print(f"\n== env for the recommended weights [{tag}] ==")
        for a in AXES:
            note = "   # LOW-SUPPORT" if flags_by_axis[a] else ""
            print(f"  export LATTICE_SCORE_W_{a.upper()}={final[a]:.3f}{note}")

    if args.json:
        with open(args.json, "w") as fh:
            json.dump({
                "n": n, "n_big": nbig, "support": support,
                "seed": seed, "fitted_nonneg": fitted, "final": final,
                "sweep": [{"lam": s["lam"], "weights": s["weights"],
                           "oos": s["oos"], "full": s["full"]} for s in sweep],
                "oos_auc": {"seed": _auc(oos, seed), "fit_lam1": _auc(oos, fitted)},
                "recommend_deploy": recommend, "best_lambda": best["lam"],
            }, fh, indent=2, default=str)
        print(f"\nwrote {args.json}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
