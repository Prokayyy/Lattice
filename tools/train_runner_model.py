#!/usr/bin/env python3
"""Train the >=2x runner entry model against candle labels, with a deployment bar.

Implements rec #4 of analysis/runner_trainability_report.md:
  - labels: alert_candle_labels (fixed-horizon candle max multiples; NEVER the
    variable-horizon ignition_alerts.max_multiple)
  - features: candidate_events (uniform alert-time surface incl. token age and
    market breadth) plus ignition_alerts for the pre-control-arm era
  - eval: walk-forward (expanding window) with group hygiene — a token seen in
    a fold's train rows is dropped from its test rows
  - deployment bar: >= MIN_ROWS labeled rows, mean walk-forward ROC-AUC >= 0.65
    AND mean PR-AUC >= 2x base rate. Below the bar the tool refuses to bless a
    model (writes metrics, exits 1). With --force it saves an artifact anyway,
    clearly marked not blessed.

Run from the repo root with a python that has scikit-learn (e.g. ~/ml-venv):
    ~/ml-venv/bin/python tools/train_runner_model.py
    ~/ml-venv/bin/python tools/train_runner_model.py --label h24 --min-multiple 2.0
"""

import argparse
import json
import os
import pickle
import sqlite3
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
os.chdir(REPO)
sys.path.insert(0, str(REPO))

try:
    import numpy as np
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import average_precision_score, roc_auc_score
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
except ImportError as exc:
    print(
        f"missing dependency ({exc}); run with a sklearn-capable venv, "
        "e.g. ~/ml-venv/bin/python"
    )
    sys.exit(2)

from scoring.runner_model import vector  # noqa: E402  (shared train/serve features)

DB = "scanner.db"
METRICS_OUT = REPO / "analysis" / "runner_model_metrics.json"
MODEL_OUT = REPO / "models" / "runner_model.pkl"

BAR_MIN_ROWS = 3000
BAR_MIN_AUC = 0.65
BAR_PR_AUC_LIFT = 2.0


def load_rows(label_col, min_multiple):
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = []

    q = f"""
        SELECT 'candidate' AS src, c.token_address, c.timestamp AS ts,
            c.score, c.raw_score, c.penalty, c.pressure, c.impulse,
            c.fdv, c.liquidity, c.volume_5m, c.volume_1h,
            c.volume_liquidity_ratio, c.buy_sell_ratio,
            c.h1_volume_liquidity_ratio, c.h1_buy_sell_ratio,
            c.price_change_5m, c.price_change_1h,
            c.momentum_score,
            c.alert_route, c.quality_tag,
            c.token_age_seconds, c.breadth_eligible_30m,
            c.gmgn_smart_money, c.gmgn_smart_share_pct,
            c.gmgn_smart_usd, c.gmgn_smart_profit_n,
            c.gmgn_smart_fresh_n, c.gmgn_smart_suspicious_n,
            l.{label_col} AS outcome
        FROM candidate_events c
        JOIN alert_candle_labels l
            ON l.subject_type = 'candidate' AND l.subject_id = c.id
        WHERE l.{label_col} IS NOT NULL
        UNION ALL
        SELECT 'alert', a.token_address, a.alert_timestamp,
            a.score, a.raw_score, a.penalty, a.alert_pressure, a.alert_impulse,
            a.alert_fdv, a.alert_liquidity, NULL, NULL,
            NULL, NULL, NULL, NULL, NULL, NULL, NULL,
            a.alert_route, a.quality_tag, NULL, NULL,
            NULL, NULL, NULL, NULL, NULL, NULL,
            l.{label_col}
        FROM ignition_alerts a
        JOIN alert_candle_labels l
            ON l.subject_type = 'alert' AND l.subject_id = a.id
        WHERE l.{label_col} IS NOT NULL
            AND a.token_address NOT IN (
                SELECT token_address FROM candidate_events
            )
    """
    for r in con.execute(q):
        rows.append(dict(r))
    con.close()

    rows.sort(key=lambda r: r["ts"] or 0)

    # first event per token per 24h (candidates already enforce this at write
    # time; alerts need it, and it also guards cross-source duplicates)
    last_seen = {}
    deduped = []
    for r in rows:
        prev = last_seen.get(r["token_address"])
        if prev is not None and (r["ts"] or 0) - prev < 24 * 3600:
            continue
        last_seen[r["token_address"]] = r["ts"] or 0
        r["y"] = 1 if (r["outcome"] or 0) >= min_multiple else 0
        deduped.append(r)
    return deduped


def featurize(rows):
    """Build the matrix through scoring.runner_model.vector() — the same code
    path record_candidate_event scores through at serve time."""
    routes = sorted({r["alert_route"] or "unknown" for r in rows})
    qtags = sorted({r["quality_tag"] or "unknown" for r in rows})

    names = None
    vecs = []
    for r in rows:
        v, names = vector(r, routes, qtags)
        vecs.append(v)

    X = np.array(vecs, dtype=float)
    # median impute (the HGB handles NaN natively; logistic needs values)
    med = np.nanmedian(X, axis=0)
    med = np.where(np.isnan(med), 0.0, med)
    X_imp = np.where(np.isnan(X), med, X)
    y = np.array([r["y"] for r in rows], dtype=int)
    groups = [r["token_address"] for r in rows]
    return X_imp, y, groups, names, {"routes": routes, "qtags": qtags, "median": med.tolist()}


def make_models():
    return {
        "logreg": lambda: make_pipeline(
            StandardScaler(),
            LogisticRegression(class_weight="balanced", max_iter=2000, C=0.1),
        ),
        "hgb": lambda: HistGradientBoostingClassifier(
            max_depth=3, learning_rate=0.05, max_iter=300,
            min_samples_leaf=25, l2_regularization=1.0, random_state=42,
        ),
    }


def walk_forward(X, y, groups, n_folds=5):
    """Expanding-window folds with group hygiene: test rows whose token already
    appears in the training window are dropped (repeat events straddling the
    boundary would leak the same pump into both sides)."""
    n = len(y)
    edges = [int(n * (i + 1) / (n_folds + 1)) for i in range(n_folds + 1)]
    for k in range(n_folds):
        tr = np.arange(0, edges[k])
        te = np.arange(edges[k], edges[k + 1])
        if not len(tr) or not len(te):
            continue
        train_tokens = {groups[i] for i in tr}
        te = np.array([i for i in te if groups[i] not in train_tokens])
        if not len(te) or y[te].sum() in (0, len(te)) or y[tr].sum() in (0, len(tr)):
            continue
        yield tr, te


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--label", choices=("h6", "h24"), default="h6")
    ap.add_argument("--min-multiple", type=float, default=2.0)
    ap.add_argument("--min-rows", type=int, default=BAR_MIN_ROWS)
    ap.add_argument("--min-auc", type=float, default=BAR_MIN_AUC)
    ap.add_argument("--pr-lift", type=float, default=BAR_PR_AUC_LIFT)
    ap.add_argument("--force", action="store_true",
                    help="save an artifact even below the bar (marked not blessed)")
    args = ap.parse_args()

    label_col = "h6_max_multiple" if args.label == "h6" else "h24_max_multiple"
    rows = load_rows(label_col, args.min_multiple)
    n = len(rows)
    base = (sum(r["y"] for r in rows) / n) if n else 0.0
    n_cand = sum(1 for r in rows if r["src"] == "candidate")
    print(f"rows: {n} (candidates {n_cand}, alerts {n - n_cand}), "
          f"base rate {base:.1%} [{args.label} >= {args.min_multiple}x]")

    report = {
        "generated_at": time.time(),
        "label": args.label,
        "min_multiple": args.min_multiple,
        "rows": n,
        "candidate_rows": n_cand,
        "base_rate": base,
        "bar": {
            "min_rows": args.min_rows,
            "min_auc": args.min_auc,
            "pr_auc_lift": args.pr_lift,
        },
        "models": {},
        "blessed": False,
        "blessed_model": None,
    }

    if n < max(args.min_rows, 50):
        report["verdict"] = (
            f"NOT BLESSED: only {n} labeled rows "
            f"(bar: {args.min_rows}). Keep collecting; candidate_events + "
            "alert_candle_labels accrue automatically."
        )
        METRICS_OUT.write_text(json.dumps(report, indent=1))
        print(report["verdict"])
        print(f"metrics -> {METRICS_OUT}")
        sys.exit(0 if n < 50 else 1)

    X, y, groups, names, encoders = featurize(rows)
    best_name, best_auc = None, -1.0

    for mname, mk in make_models().items():
        aucs, prs = [], []
        for tr, te in walk_forward(X, y, groups):
            m = mk()
            m.fit(X[tr], y[tr])
            p = m.predict_proba(X[te])[:, 1]
            aucs.append(roc_auc_score(y[te], p))
            prs.append(average_precision_score(y[te], p))
        if not aucs:
            print(f"{mname}: no valid folds")
            continue
        mean_auc = float(np.mean(aucs))
        mean_pr = float(np.mean(prs))
        report["models"][mname] = {
            "wf_auc_mean": mean_auc,
            "wf_auc_std": float(np.std(aucs)),
            "wf_pr_auc_mean": mean_pr,
            "wf_pr_auc_std": float(np.std(prs)),
            "folds": len(aucs),
        }
        print(f"{mname}: walk-forward AUC {mean_auc:.3f}±{np.std(aucs):.3f}  "
              f"PR-AUC {mean_pr:.3f}±{np.std(prs):.3f} ({len(aucs)} folds)")
        if mean_auc > best_auc:
            best_name, best_auc = mname, mean_auc

    passed = (
        best_name is not None
        and n >= args.min_rows
        and report["models"][best_name]["wf_auc_mean"] >= args.min_auc
        and report["models"][best_name]["wf_pr_auc_mean"] >= args.pr_lift * base
    )
    report["blessed"] = bool(passed)

    if passed or (args.force and best_name):
        m = make_models()[best_name]()
        m.fit(X, y)
        MODEL_OUT.parent.mkdir(exist_ok=True)
        with open(MODEL_OUT, "wb") as fh:
            pickle.dump({
                "model": m,
                "model_name": best_name,
                "feature_names": names,
                "encoders": encoders,
                "label": args.label,
                "min_multiple": args.min_multiple,
                "blessed": bool(passed),
                "metrics": report["models"].get(best_name),
                "trained_at": time.time(),
                "rows": n,
            }, fh)
        report["blessed_model"] = best_name if passed else None
        report["artifact"] = str(MODEL_OUT)
        state = "BLESSED" if passed else "NOT blessed (forced artifact)"
        report["verdict"] = f"{state}: {best_name} -> {MODEL_OUT}"
    else:
        report["verdict"] = (
            "NOT BLESSED: best model "
            f"{best_name} AUC {best_auc:.3f} vs bar {args.min_auc} "
            f"(PR-AUC bar {args.pr_lift}x base = {args.pr_lift * base:.3f}). "
            "No artifact written; the entry gates stay rule-based."
        )

    METRICS_OUT.write_text(json.dumps(report, indent=1))
    print(report["verdict"])
    print(f"metrics -> {METRICS_OUT}")
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
