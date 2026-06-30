"""Additive capital-lane scorecard (scanner gate redesign, Layer 2).

Replaces the conviction float as the capital SELECTOR. The float does not rank
outcomes (discovery/redesign_validate.py: the top conviction deciles are the
deadest and have the fewest winners), so capital tiering is driven instead by a
small, transparent, ADDITIVE scorecard over independent axes — each a bounded
contribution, summed into one score. The score's ABSOLUTE value is meaningless;
only the ORDER matters (live tiers are assigned by trailing percentile, with
separate absolute floors for Tier A).

Pure stdlib, in the style of discovery/features.py. Inputs:
  row       : signal_snapshots-shaped dict (entry-time)
  detail    : participation breadth_detail (buyers_sig/breadth/concentration) | None
  st_bundle : SolanaTracker evidence dict | None (degrade-neutral when not ok)
  regime    : "warming"|"risk_on"|"caution"|"risk_off"|None (scales nothing here;
              the caller uses it to pick the percentile cutoff)

Conviction enters as a WEAK axis only. Weights are seeded by direction (aligned
with the forward-outcome data) and are env-tunable; the offline harness reports
Tier-A win% vs base so they can be refreshed on the retrain cron.
"""
from __future__ import annotations

import os


def _f(row, key, default=0.0):
    try:
        v = row.get(key)
        return float(v) if v is not None else float(default)
    except (TypeError, ValueError):
        return float(default)


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def _w(name, default):
    try:
        return float(os.getenv(f"LATTICE_SCORE_W_{name.upper()}", str(default)))
    except (TypeError, ValueError):
        return default


# Seed weights (env-overridable as LATTICE_SCORE_W_<AXIS>). Each axis returns a
# contribution already on a comparable [-1,1]-ish scale; the weight sets its pull.
WEIGHTS = {
    "momentum_shape": 1.0,
    "pc1h_shape": 0.8,
    "buyers": 1.2,
    "order_flow": 1.0,
    "risk_flag_load": 1.0,
    "lifecycle": 0.3,
    "liquidity_band": 0.6,
    "actor": 1.0,
    "overextension": 0.6,      # pc24 overheat penalty (replaces the hard pc24 cap)
    "conviction_weak": 0.25,   # deliberately weak: the float does not rank
}


def _risk_flags(row):
    rf = row.get("risk_flags")
    if isinstance(rf, str):
        import json
        try:
            rf = json.loads(rf)
        except (ValueError, TypeError):
            rf = []
    return rf or []


def _momentum_shape(row):
    """Inverted-U on 5m move: a healthy revival impulse sits ~12% (data sweet
    spot); flat is weak, blow-off is an exhaustion trap."""
    pc5 = _f(row, "price_change_5m")
    if pc5 <= 0:
        return -0.4
    return _clamp(1.0 - abs(pc5 - 12.0) / 30.0, -0.6, 1.0)


def _pc1h_shape(row):
    """Moderate 1h continuation is good; a deep fade is bad; an already-huge 1h
    move is an overheated chase (penalty, not a hard cap)."""
    pc1h = _f(row, "price_change_1h")
    if pc1h <= -40.0:
        return -0.1                      # capitulation: ambiguous, near-neutral
    if -40.0 < pc1h < -15.0:
        return -1.0                      # deep-fader band
    if pc1h >= 300.0:
        return -0.6                      # overheated chase
    if pc1h >= 100.0:
        return -0.2
    return _clamp(pc1h / 60.0, -0.3, 0.8)


def _buyers(detail):
    bs = (detail or {}).get("buyers_sig")
    try:
        return _clamp(float(bs), -1.0, 1.0) if bs is not None else 0.0
    except (ValueError, TypeError):
        return 0.0


def _order_flow(row):
    """Pressure + 5m buy/sell DOLLAR asymmetry (not transaction BSR)."""
    pressure = _f(row, "pressure")
    p_term = _clamp((pressure - 15.0) / 60.0, -0.6, 0.8)
    b5 = _f(row, "buy_volume_5m")
    s5 = _f(row, "sell_volume_5m")
    tot = b5 + s5
    asym = (b5 - s5) / tot if tot > 0 else 0.0
    return _clamp(0.5 * p_term + 0.5 * asym, -1.0, 1.0)


def _risk_flag_load(row):
    n = len(_risk_flags(row))
    return _clamp(-0.33 * n, -1.0, 0.0)


def _lifecycle(row):
    lc = str(row.get("lifecycle") or "").lower()
    if lc == "bonding_curve":
        return 0.1
    if lc in ("graduated", "migrated", "amm"):
        return 0.2
    return 0.0


def _liquidity_band(row):
    """Healthy VLR band (real volume, not a hollow thin-book pump)."""
    vlr = _f(row, "volume_liquidity_ratio")
    if vlr <= 0:
        return -0.2
    return _clamp(1.0 - abs(min(vlr, 4.0) - 0.5) / 2.0, -0.6, 1.0)


def _actor(st_bundle):
    """SolanaTracker actor health. Degrade-neutral when evidence is not ok."""
    if not st_bundle or st_bundle.get("status") != "ok":
        return 0.0
    lvl = str(st_bundle.get("risk_level") or "").lower()
    base = {"low": 0.3, "review": -0.3, "high": -1.0}.get(lvl, 0.0)
    cur = _f(st_bundle, "current_bundle_pct")
    if cur >= 25.0:
        base -= 0.4
    return _clamp(base, -1.0, 0.4)


def _overextension(row):
    """24h overheat: a token already up multiples on 24h is a late chase. A soft
    penalty (Tier-B pull), NOT a hard reject — the data shows >300% tokens still
    beat base on dead-rate, so the old hard cap rejected better-than-base names."""
    pc24 = _f(row, "price_change_24h")
    if pc24 >= 600.0:
        return -1.0
    if pc24 >= 300.0:
        return -0.5
    return 0.0


def _conviction_weak(row):
    conv = _f(row, "conviction") or _f(row, "_conviction")
    return _clamp((conv - 0.25) * 0.8, -0.2, 0.3)


def raw_axes(row, detail=None, st_bundle=None, conviction=None):
    """Unweighted per-axis contributions (each already on a comparable ~[-1,1]
    scale). Exposed so the offline weight-fitter
    (discovery/fit_scorecard_weights.py) can regress forward outcomes on the SAME
    axes the live gate sums; score() just folds in WEIGHTS on top of these."""
    if conviction is not None:
        row = dict(row)
        row["_conviction"] = conviction
    return {
        "momentum_shape": _momentum_shape(row),
        "pc1h_shape": _pc1h_shape(row),
        "buyers": _buyers(detail),
        "order_flow": _order_flow(row),
        "risk_flag_load": _risk_flag_load(row),
        "lifecycle": _lifecycle(row),
        "liquidity_band": _liquidity_band(row),
        "actor": _actor(st_bundle),
        "overextension": _overextension(row),
        "conviction_weak": _conviction_weak(row),
    }


def score(row, detail=None, st_bundle=None, regime=None, conviction=None):
    """Return {"score": float, "axes": {axis: weighted_contribution}, "raw": {...}}.

    conviction may be passed explicitly (the live pipeline carries it on the
    EntryAlert, not the row); it is folded in as the weak axis."""
    raw = raw_axes(row, detail=detail, st_bundle=st_bundle, conviction=conviction)
    axes = {k: _w(k, WEIGHTS[k]) * v for k, v in raw.items()}
    return {"score": sum(axes.values()), "axes": axes, "raw": raw}


# --- absolute Tier-A floors (a high score is necessary but not sufficient) ---

def passes_tier_a_floors(row, detail=None, st_bundle=None):
    """Hard guards a Tier-A (full-size) entry must clear regardless of score:
    real impulse, real pressure, no high-bundle/high-risk actor, no flag stack,
    dollar-flow not clearly negative. Returns (ok: bool, reason: str)."""
    if _f(row, "price_change_5m") < float(
            os.getenv("LATTICE_TIER_A_MIN_PC5", "3")):
        return False, "pc5<min"
    if _f(row, "pressure") < float(os.getenv("LATTICE_TIER_A_MIN_PRESSURE", "15")):
        return False, "pressure<min"
    if len(_risk_flags(row)) >= int(os.getenv("LATTICE_TIER_A_MAX_FLAGS", "3")):
        return False, "flag_stack"
    if st_bundle and st_bundle.get("status") == "ok":
        if str(st_bundle.get("risk_level") or "").lower() == "high":
            return False, "actor_high"
        if _f(st_bundle, "current_bundle_pct") >= 25.0:
            return False, "actor_bundled"
    b5 = _f(row, "buy_volume_5m")
    s5 = _f(row, "sell_volume_5m")
    tot = b5 + s5
    if tot > 0 and (b5 - s5) / tot < float(
            os.getenv("LATTICE_TIER_A_MIN_DOLLARFLOW", "-0.2")):
        return False, "dollarflow_negative"
    return True, ""
