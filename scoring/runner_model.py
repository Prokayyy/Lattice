"""Shared feature definition + shadow scorer for the runner entry model.

Single source of truth for the model's feature vector: tools/train_runner_model.py
imports NUMERIC/vector() at train time, record_candidate_event scores through
score_candidate() at serve time, so train and serve can never drift.

Serve side is fully degradable: returns None when models/runner_model.pkl is
absent, not blessed by the deployment bar, or scikit-learn is not installed in
the scanner venv (install it only once a model is blessed). The scanner itself
never imports sklearn at module import time.
"""

import math
import os
import pickle
import threading

# Numeric feature columns; each contributes (value, missing-indicator) pairs
# to the vector, followed by route and quality-tag one-hots from the artifact.
NUMERIC = [
    "score", "raw_score", "penalty", "pressure", "impulse",
    "log_fdv", "log_liq", "volume_5m", "volume_1h",
    "volume_liquidity_ratio", "buy_sell_ratio",
    "h1_volume_liquidity_ratio", "h1_buy_sell_ratio",
    "price_change_5m", "price_change_1h",
    "momentum_score",
    "log_token_age", "breadth_eligible_30m",
    "gmgn_smart_money", "gmgn_smart_share_pct",
    "log_gmgn_smart_usd", "gmgn_smart_profit_n",
    "gmgn_smart_fresh_n", "gmgn_smart_suspicious_n",
]

_NAN = float("nan")


def _log1p(value):
    try:
        return math.log1p(max(float(value), 0.0))
    except (TypeError, ValueError):
        return None


def base_features(row):
    """Map a raw candidate/alert row (column-name keys) to the NUMERIC space."""
    out = {k: row.get(k) for k in NUMERIC}
    out["log_fdv"] = _log1p(row.get("fdv")) if row.get("fdv") is not None else None
    out["log_liq"] = _log1p(row.get("liquidity")) if row.get("liquidity") is not None else None
    age = row.get("token_age_seconds")
    out["log_token_age"] = _log1p(age) if age is not None else None
    smart_usd = row.get("gmgn_smart_usd")
    out["log_gmgn_smart_usd"] = (
        _log1p(smart_usd)
        if smart_usd is not None
        else None
    )
    return out


def vector(row, routes, qtags):
    """Assemble the feature vector (NaN for missing) in the canonical order.
    Returns (values, names)."""
    feats = base_features(row)
    values = []
    names = []
    for c in NUMERIC:
        v = feats.get(c)
        try:
            v = float(v) if v is not None else None
        except (TypeError, ValueError):
            v = None
        values.append(v if v is not None else _NAN)
        values.append(1.0 if v is None else 0.0)
        names.extend([c, f"m_{c}"])
    rt = row.get("alert_route") or "unknown"
    qt = row.get("quality_tag") or "unknown"
    for x in routes:
        values.append(1.0 if rt == x else 0.0)
        names.append(f"r_{x}")
    for x in qtags:
        values.append(1.0 if qt == x else 0.0)
        names.append(f"q_{x}")
    return values, names


_lock = threading.Lock()
_cache = {"path": None, "mtime": None, "artifact": None}


def _load_artifact(model_path):
    try:
        mtime = os.path.getmtime(model_path)
    except OSError:
        return None

    with _lock:
        if (
            _cache["path"] == model_path
            and _cache["mtime"] == mtime
        ):
            return _cache["artifact"]

        try:
            # sklearn import happens implicitly during unpickle; a venv
            # without it raises ImportError/ModuleNotFoundError here.
            with open(model_path, "rb") as fh:
                artifact = pickle.load(fh)
        except Exception:
            artifact = None

        if artifact is not None and not artifact.get("blessed"):
            # Never serve probabilities from a model that failed the
            # deployment bar (a --force artifact).
            artifact = None

        _cache.update(
            {"path": model_path, "mtime": mtime, "artifact": artifact}
        )
        return artifact


def score_candidate(row, model_path="models/runner_model.pkl"):
    """Runner probability for a candidate row, or None when no blessed model
    (or its dependencies) are available. Never raises."""
    try:
        artifact = _load_artifact(model_path)
        if artifact is None:
            return None

        encoders = artifact.get("encoders") or {}
        routes = encoders.get("routes") or []
        qtags = encoders.get("qtags") or []
        median = encoders.get("median") or []

        values, names = vector(row, routes, qtags)
        if artifact.get("feature_names") and artifact["feature_names"] != names:
            return None

        if len(median) == len(values):
            values = [
                median[i] if v != v else v  # NaN check
                for i, v in enumerate(values)
            ]
        else:
            values = [0.0 if v != v else v for v in values]

        prob = artifact["model"].predict_proba([values])[0][1]
        return float(prob)
    except Exception:
        return None
