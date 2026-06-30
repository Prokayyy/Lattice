"""Feature extraction for the conviction ranker.

Design notes (grounded in this scanner's own outcome data):
- 5m momentum is non-monotonic: 100-150% is the sweet spot, >150% is an
  exhaustion trap (0% hit 5x historically). We give the model both the level
  and its square so it can learn the inverted-U instead of a hard 35% cap.
- "Lattice" axes are normalized to the token, not to global dollar floors:
  volume/liquidity ratio (VLR) and buy/sell asymmetry are already
  token-relative; raw volume and liquidity enter as logs.
- Participation breadth (unique buyers / holder growth) is the missing axis in
  the current schema. It is declared here so the vector shape is stable; until
  a data source is wired (see lattice.ParticipationProvider) it is filled
  with a neutral sentinel and carries near-zero learned weight, so the model
  degrades gracefully rather than pretending.

A feature vector is built from a single snapshot dict (a signal_snapshots row
at/just before alert/decision time). Everything is plain floats; no deps.
"""

import math


def _f(row, key, default=0.0):
    try:
        v = row.get(key)
        return float(v) if v is not None else float(default)
    except (TypeError, ValueError):
        return float(default)


def _log1p_signed(x):
    # log scale that tolerates 0 and small values without blowing up.
    return math.log1p(max(x, 0.0))


# Order is the contract between training and inference. Append only.
FEATURE_NAMES = [
    "price_change_5m",        # level of the 5m move
    "price_change_5m_sq",     # inverted-U: lets the model penalise blow-offs
    "price_change_1h",
    "impulse",
    "pressure",
    "vlr",                    # volume / liquidity (token-relative intensity)
    "h1_vlr",
    "buy_sell_ratio",
    "h1_buy_sell_ratio",
    "buy_sell_asym_5m",       # (buyUSD-sellUSD)/(buyUSD+sellUSD) in [-1,1]
    "log_volume_1h",
    "log_liquidity",
    "score",                  # the existing score, so we can measure lift over it
    "participation_breadth",  # MISSING-DATA axis; neutral until provider wired
]

PARTICIPATION_IDX = FEATURE_NAMES.index("participation_breadth")
PARTICIPATION_NEUTRAL = 0.0


def buy_sell_asymmetry(buy_usd, sell_usd):
    total = buy_usd + sell_usd
    if total <= 0:
        return 0.0
    return (buy_usd - sell_usd) / total


def extract(row, participation=None):
    """row: a signal_snapshots-shaped dict. participation: optional float in
    roughly [-1, 1] from a ParticipationProvider (broad buying positive,
    concentrated/wash negative). Returns list[float] aligned to FEATURE_NAMES."""
    pc5 = _f(row, "price_change_5m")
    pc1h = _f(row, "price_change_1h")
    b5 = _f(row, "buy_volume_5m")
    s5 = _f(row, "sell_volume_5m")

    return [
        pc5,
        pc5 * pc5,
        pc1h,
        _f(row, "impulse"),
        _f(row, "pressure"),
        _f(row, "volume_liquidity_ratio"),
        _f(row, "h1_volume_liquidity_ratio"),
        _f(row, "buy_sell_ratio"),
        _f(row, "h1_buy_sell_ratio"),
        buy_sell_asymmetry(b5, s5),
        _log1p_signed(_f(row, "volume_1h")),
        _log1p_signed(_f(row, "liquidity")),
        _f(row, "score"),
        PARTICIPATION_NEUTRAL if participation is None else float(participation),
    ]
