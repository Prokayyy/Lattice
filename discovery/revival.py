"""Revival-shape detection from token_candles.

"Revival" is a trajectory, not a route label: a token with PRIOR life that went
dormant and is now re-accelerating off a low base — distinct from a fresh launch.
The key reframe vs the old scanner: stop using global dollar floors ($20k 1h vol
etc). Normalise to the token's OWN history — "volume is 10x this token's median,
off a quiet base" is the real revival signal and scales across token sizes.

Uses the token_candles table (OHLC + volume_1h + liquidity history). Returns an
interpretable shape dict + a [0,1] revival_score + the token-relative volume
z-score (the normalised intensity used downstream).
"""

import sqlite3
import statistics as st


def _median(xs):
    xs = [x for x in xs if x is not None]
    return st.median(xs) if xs else 0.0


def revival_shape(db_path, token_address, now, lookback_seconds=86400 * 3):
    """db_path: path to scanner.db. now: decision timestamp. Looks back over
    `lookback_seconds` of candle history for the token."""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT bucket_start, close, volume_1h, liquidity FROM token_candles "
        "WHERE token_address=? AND bucket_start BETWEEN ? AND ? "
        "ORDER BY bucket_start ASC",
        (token_address, now - lookback_seconds, now),
    ).fetchall()
    con.close()

    out = {
        "candles": len(rows),
        "age_seconds": 0.0,
        "baseline_volume": 0.0,
        "volume_z": 0.0,
        "prior_peak": 0.0,
        "trough": 0.0,
        "drawdown_from_peak": 0.0,
        "reawakening": False,
        "revival_score": 0.0,
        "reason": "insufficient_history",
    }
    if len(rows) < 12:
        return out

    closes = [float(r["close"] or 0) for r in rows]
    vols = [float(r["volume_1h"] or 0) for r in rows]
    ts = [float(r["bucket_start"]) for r in rows]
    out["age_seconds"] = ts[-1] - ts[0]

    # split history: an older "base" period and the recent re-acceleration window
    recent_n = max(3, len(rows) // 6)
    base_vols = vols[:-recent_n]
    base_closes = closes[:-recent_n]
    recent_vols = vols[-recent_n:]
    recent_closes = closes[-recent_n:]

    baseline_vol = _median(base_vols)
    out["baseline_volume"] = baseline_vol
    # token-relative volume z: how far the recent volume is above its own quiet base
    base_sd = (st.pstdev([v for v in base_vols if v is not None])
               if len([v for v in base_vols if v is not None]) > 1 else 0.0)
    recent_vol = _median(recent_vols)
    if base_sd > 1e-9:
        out["volume_z"] = (recent_vol - baseline_vol) / base_sd
    elif baseline_vol > 0:
        out["volume_z"] = (recent_vol / baseline_vol) - 1.0

    peak = max(base_closes) if base_closes else 0.0
    trough = min(base_closes) if base_closes else 0.0
    cur = recent_closes[-1] if recent_closes else 0.0
    out["prior_peak"] = peak
    out["trough"] = trough
    out["drawdown_from_peak"] = (1.0 - (trough / peak)) if peak > 0 else 0.0

    # reawakening: was dormant (deep drawdown off a prior peak) AND now
    # re-accelerating in both price and its own-relative volume
    was_dormant = out["drawdown_from_peak"] >= 0.5 and peak > 0
    price_reaccel = cur > (trough * 1.3) if trough > 0 else False
    vol_reaccel = out["volume_z"] >= 2.0
    out["reawakening"] = bool(was_dormant and price_reaccel and vol_reaccel)

    score = 0.0
    score += 0.4 * min(max(out["volume_z"] / 5.0, 0.0), 1.0)   # own-relative volume surge
    score += 0.3 * (1.0 if was_dormant else 0.0)               # had a base + trough
    score += 0.3 * (1.0 if price_reaccel else 0.0)             # turning up off the base
    out["revival_score"] = round(score, 4)
    out["reason"] = "ok"
    return out
