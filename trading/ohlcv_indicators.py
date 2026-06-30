"""Liquidity- and flow-aware OHLCV indicators for memecoin microstructure.

Design thesis (see why these and not another oscillator):
  The classic momentum/trend indicators (RSI, RSI-EMA cross, Kaufman ER) were
  validated to death on this book and proved to have no edge -- they are pure
  *price* measures aimed at *prediction*, and entry-prediction on these tokens
  is a coin flip. The realised edge lives in EXITS and EXECUTION: cutting losers
  before the stop slips, and not surrendering runners.

  So these five indicators deliberately mine a different axis -- LIQUIDITY and
  ORDER-FLOW -- and most are *state measurements* (how illiquid is this right
  now / how much profit have I given back) rather than forecasts. State
  measurements are causal: they describe a cost that exists this instant, so
  they survive as hand-coded rules. Predictive ones (flow, climax) are flagged
  as such and are intended as model features, not hand-tuned gates.

Indicators
  1. price_impact / Amihud illiquidity   -- slippage / thin-book risk  [STATE]
  2. chaikin_money_flow / signed flow     -- accumulation vs distribution [PRED]
  3. downside_atr_chandelier              -- runner-capture trailing exit [STATE]
  4. corwin_schultz_spread                -- effective bid-ask spread     [STATE]
  5. volume_climax                        -- blow-off / capitulation      [PRED]

Candle contract
  Every function takes candle dicts and is tolerant of missing keys via
  safe_float. Recognised keys (first present wins):
    open/o, high/h, low/l, close/c,
    volume/volume_5m/v, liquidity/liq,
    ts/bucket_start/timestamp/time
  This matches both the live token_candles rows (storage.sqlite) and the GMGN
  kline dicts, so the same code runs live and in backtests.

Nothing here imports repo trading state -- it is a pure, importable library.
"""

import math


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #
def safe_float(value, default=0.0):
    try:
        out = float(value)
        if math.isnan(out) or math.isinf(out):
            return default
        return out
    except (TypeError, ValueError):
        return default


def _o(c):
    return safe_float(c.get("open", c.get("o")))


def _h(c):
    return safe_float(c.get("high", c.get("h")))


def _l(c):
    return safe_float(c.get("low", c.get("l")))


def _c(c):
    return safe_float(c.get("close", c.get("c")))


def _v(c):
    # live rows carry rolling volume_5m; gmgn klines carry volume/v
    return safe_float(c.get("volume", c.get("volume_5m", c.get("v"))))


def _liq(c):
    return safe_float(c.get("liquidity", c.get("liq")))


def _ts(c):
    for key in ("ts", "bucket_start", "timestamp", "time", "open_time"):
        if key in c:
            v = safe_float(c.get(key), None if False else 0.0)
            if v:
                return v / 1000.0 if v > 1e12 else v
    return 0.0


def _typical(c):
    """HLC3 typical price, falling back to close."""
    high, low, close = _h(c), _l(c), _c(c)
    if high > 0 and low > 0 and close > 0:
        return (high + low + close) / 3.0
    return close


def _mid(c):
    high, low = _h(c), _l(c)
    if high > 0 and low > 0:
        return (high + low) / 2.0
    return _c(c)


def _mean(values):
    vals = [safe_float(v) for v in values]
    return sum(vals) / len(vals) if vals else 0.0


def _std(values):
    vals = [safe_float(v) for v in values]
    if len(vals) < 2:
        return 0.0
    mu = _mean(vals)
    var = sum((v - mu) ** 2 for v in vals) / (len(vals) - 1)
    return math.sqrt(max(var, 0.0))


def _ema(values, period):
    """EMA series; leading entries before the seed are None."""
    if period <= 0:
        raise ValueError("period must be positive")
    out = []
    alpha = 2.0 / (period + 1.0)
    current = None
    for i, value in enumerate(values):
        value = safe_float(value)
        if current is None:
            if i + 1 < period:
                out.append(None)
                continue
            current = _mean(values[i + 1 - period:i + 1])
            out.append(current)
            continue
        current = value * alpha + current * (1 - alpha)
        out.append(current)
    return out


def _percentile_rank(value, history):
    """Fraction of `history` <= value, in [0, 1]. 1.0 = highest on record."""
    hist = [safe_float(v) for v in history]
    if not hist:
        return 0.5
    below = sum(1 for v in hist if v <= value)
    return below / len(hist)


# --------------------------------------------------------------------------- #
# 1. Amihud price-impact / illiquidity                                  [STATE]
# --------------------------------------------------------------------------- #
#   The single most relevant number to the documented #1 leak (stop slippage).
#   Amihud's illiquidity = |return| / dollar-volume: how far price moves per
#   dollar traded. Here we also expose the liquidity form, which uses the real
#   per-candle resting liquidity as the denominator -- a true depth measure
#   rather than a volume proxy. Higher impact == thinner book == your slip.
def price_impact(candle, mode="liquidity", eps=1e-9):
    """Per-candle price impact. Higher = more illiquid = more slippage.

    mode="liquidity": relative range / resting liquidity (preferred; needs the
                      `liquidity` field). Depth-like.
    mode="volume":    |close-open|/open / dollar-volume (classic Amihud).
                      Falls back here automatically when liquidity is absent.
    """
    high, low, close, open_ = _h(candle), _l(candle), _c(candle), _o(candle)
    mid = _mid(candle)
    if mid <= 0:
        return 0.0
    rel_range = (high - low) / mid if high > 0 and low > 0 else 0.0

    if mode == "liquidity":
        liq = _liq(candle)
        if liq > 0:
            # scale by 1e6 so values land in a human range (impact per $1M depth)
            return rel_range / liq * 1e6
        mode = "volume"  # graceful fallback

    # volume / classic Amihud form
    ret = abs(close - open_) / open_ if open_ > 0 else 0.0
    dollar_vol = _v(candle) * _typical(candle)
    return ret / max(dollar_vol, eps) * 1e6


def price_impact_series(candles, mode="liquidity"):
    return [price_impact(c, mode=mode) for c in candles]


def illiquidity_spike(candles, window=20, mode="liquidity"):
    """Where current impact sits in its own trailing distribution, in [0,1].

    The actionable signal: a rising percentile (e.g. > 0.85) means liquidity is
    evaporating *right now* -> the next stop will slip -> exit into liquidity
    pre-emptively or size the exit down. Self-normalising, so it is comparable
    across tokens of wildly different absolute depth.
    """
    if len(candles) < 3:
        return {"ready": False, "reason": "not_enough_candles"}
    series = price_impact_series(candles, mode=mode)
    current = series[-1]
    history = series[max(0, len(series) - window - 1):-1]
    history = [v for v in history if v > 0]
    if len(history) < 3:
        return {"ready": False, "reason": "not_enough_history"}
    pct = _percentile_rank(current, history)
    med = sorted(history)[len(history) // 2]
    return {
        "ready": True,
        "impact": current,
        "impact_percentile": pct,
        "impact_vs_median": (current / med) if med > 0 else 0.0,
        "spiking": pct >= 0.85,
        "mode": mode,
    }


# --------------------------------------------------------------------------- #
# 2. Money-flow pressure (CLV-weighted volume)                          [PRED]
# --------------------------------------------------------------------------- #
#   With no trade-level buy/sell, the close's position inside the candle range
#   is a clean proxy for who won the bar. The building block is old (A/D / CMF);
#   the *use* is the fresh part -- a distribution detector for EXITS, not an
#   entry oscillator. Price flat/up while flow rolls negative == being
#   distributed into -> exit before price breaks.
def close_location_value(candle):
    """((C-L)-(H-C))/(H-L) in [-1,1]; +1 close on high, -1 on low. 0 on doji."""
    high, low, close = _h(candle), _l(candle), _c(candle)
    rng = high - low
    if rng <= 0:
        return 0.0
    return max(-1.0, min(1.0, ((close - low) - (high - close)) / rng))


def signed_volume(candle):
    return close_location_value(candle) * _v(candle)


def chaikin_money_flow(candles, period=20):
    """Sum(CLV*vol)/Sum(vol) over the last `period` bars, in [-1,1].

    > 0 net accumulation, < 0 net distribution. None if no volume.
    """
    window = candles[-period:] if period else candles
    num = sum(signed_volume(c) for c in window)
    den = sum(abs(_v(c)) for c in window)
    if den <= 0:
        return None
    return max(-1.0, min(1.0, num / den))


def money_flow_divergence(candles, price_lookback=10, period=20):
    """Bearish divergence: price higher over the lookback but money-flow negative.

    Returns a dict with the raw pieces + a `bearish_divergence` flag intended as
    an early-exit signal (distribution into strength).
    """
    if len(candles) < max(price_lookback + 1, period):
        return {"ready": False, "reason": "not_enough_candles"}
    cmf = chaikin_money_flow(candles, period=period)
    if cmf is None:
        return {"ready": False, "reason": "no_volume"}
    close_now = _c(candles[-1])
    close_then = _c(candles[-1 - price_lookback])
    price_chg = (close_now / close_then - 1.0) if close_then > 0 else 0.0
    return {
        "ready": True,
        "cmf": cmf,
        "price_change": price_chg,
        # up >= 2% on the lookback but net distribution underneath it
        "bearish_divergence": price_chg >= 0.02 and cmf < 0.0,
        # mirror: down but net accumulation -> possible capitulation bottom
        "bullish_divergence": price_chg <= -0.02 and cmf > 0.0,
    }


# --------------------------------------------------------------------------- #
# 3. Downside-ATR chandelier trailing exit                              [STATE]
# --------------------------------------------------------------------------- #
#   Runner capture. A fixed-% trail either chokes runners early or gives back
#   too much. Make the trail distance react to *selling* pressure only, using
#   the true ranges of down-closing candles. Normal up-leg pullbacks don't trip
#   it; a genuine reversal (downside ranges expanding) does.
def true_range(candle, prev_close):
    high, low = _h(candle), _l(candle)
    pc = safe_float(prev_close)
    if pc <= 0:
        return max(high - low, 0.0)
    return max(high - low, abs(high - pc), abs(low - pc))


def downside_atr(candles, period=14):
    """Mean true range over DOWN-closing candles only (semivariance-style).

    A down candle is close < prior close. Returns None without enough history,
    and 0.0 when there are bars but none closed down (a clean up-leg) -- callers
    that need a positive distance on such calm runners should fall back to
    full_atr (see take_profit_levels / the shadow trail).
    """
    if len(candles) < 2:
        return None
    trs = []
    for i in range(1, len(candles)):
        prev_c = _c(candles[i - 1])
        if _c(candles[i]) < prev_c:                 # only downside bars
            trs.append(true_range(candles[i], prev_c))
    if not trs:
        return 0.0
    return _mean(trs[-period:])


def full_atr(candles, period=14):
    """Mean true range over ALL bars (classic ATR). Companion to downside_atr for
    the calm-uptrend case: a strong early up-leg can have zero down-closing bars,
    so downside_atr returns 0.0 even though real volatility exists. full_atr gives
    those runners a usable distance for the TP ladder / trail. None without
    enough history."""
    if len(candles) < 2:
        return None
    trs = [true_range(candles[i], _c(candles[i - 1]))
           for i in range(1, len(candles))]
    if not trs:
        return None
    return _mean(trs[-period:])


def simulate_chandelier(candles, entry_idx=0, k=3.0, period=14,
                        giveback_cap=0.5, min_atr_bars=6):
    """Walk a trade forward from entry_idx; exit when close drops below the
    downside-ATR trail off the peak, or when too much open profit is surrendered.

    Returns: {exit_idx, exit_price, peak_price, reason, bars_held}. reason is
    one of "chandelier", "giveback_cap", "end_of_data".
    """
    n = len(candles)
    if n == 0 or entry_idx >= n:
        return {"exit_idx": None, "reason": "no_data"}
    entry_price = _c(candles[entry_idx]) or _o(candles[entry_idx])
    peak = entry_price
    for i in range(entry_idx + 1, n):
        peak = max(peak, _h(candles[i]) or _c(candles[i]))
        close = _c(candles[i])
        window = candles[max(entry_idx, i - period * 3):i + 1]
        d_atr = downside_atr(window, period=period)
        # peak giveback guard (works before ATR has enough downside bars)
        if peak > entry_price:
            given = (peak - close) / (peak - entry_price)
            if given >= giveback_cap:
                return {"exit_idx": i, "exit_price": close, "peak_price": peak,
                        "reason": "giveback_cap", "bars_held": i - entry_idx}
        if d_atr and i - entry_idx >= min_atr_bars:
            trail = peak - k * d_atr
            if close < trail:
                return {"exit_idx": i, "exit_price": close, "peak_price": peak,
                        "reason": "chandelier", "bars_held": i - entry_idx}
    return {"exit_idx": n - 1, "exit_price": _c(candles[-1]), "peak_price": peak,
            "reason": "end_of_data", "bars_held": n - 1 - entry_idx}


# --------------------------------------------------------------------------- #
# Stop placement: volatility-scaled + structure-anchored
# --------------------------------------------------------------------------- #
#   Replaces a guessed fixed-% stop. The DISTANCE comes from downside-ATR (so
#   calm tokens get a tight stop, violent ones get room); the ANCHOR comes from
#   a volume-profile support node when one sits outside the noise band.
def atr_stop_level(candles, ref_price, k=2.5, period=14):
    """Volatility-scaled stop: ref_price - k * downside_ATR.

    ref_price = entry (fixed stop) or running peak (trailing). Downside ATR ties
    the distance to selling pressure, not upside whips. None without history.
    """
    atr = downside_atr(candles, period=period)
    if not atr:
        return None
    return {"stop": ref_price - k * atr, "atr": atr, "k": k, "basis": "atr"}


def structure_anchored_stop(candles, ref_price, support_price=None,
                            k=2.5, period=14, node_buffer=0.01):
    """Combine ATR distance with a volume-profile support node.

    If a high-volume support node sits at least k*ATR below price, anchor the
    stop just under it (only trips if real structure breaks). Otherwise the
    nearest node is inside the noise band -> fall back to the pure ATR stop.
    Pass support_price from volume_profile.nearest_support(profile, ref_price).
    Returns {stop, basis: 'structure'|'atr', atr, k, support_price} or None.
    """
    atr = downside_atr(candles, period=period)
    if not atr:
        return None
    out = {"atr": atr, "k": k, "support_price": support_price}
    if support_price and 0 < support_price < ref_price:
        structural = support_price * (1 - node_buffer)
        if (ref_price - structural) >= k * atr:
            out.update(stop=structural, basis="structure")
            return out
    out.update(stop=ref_price - k * atr, basis="atr")
    return out


def take_profit_levels(candles, entry_price, k=2.5, period=14,
                       r_multiples=(1.0, 2.0, 3.0), nodes=None,
                       node_snap_band=0.15):
    """Volatility-scaled take-profit ladder -- the upside twin of atr_stop_level.

    R (one risk unit) = k * downside_ATR, the SAME absolute price distance the
    ATR stop sits below entry. So target_i = entry + r_i * R is a clean r_i:1
    reward:risk level: at the 1R target you have made back exactly what you were
    risking. Tying TP distance to the same volatility unit as the stop keeps the
    ladder symmetric -- calm tokens get tight targets, violent ones get room --
    instead of guessing fixed multiples.

    nodes: optional iterable of high-volume node PRICES above entry
    (volume_profile.high_volume_nodes). When supplied, each raw R-target snaps UP
    to the nearest node within +node_snap_band, so a take-profit sits at real
    traded resistance rather than a round number when structure is close by.

    NOTE: R is measured from the candles passed in -- typically an ENTRY-TIME
    snapshot. It is not re-derived as the trade evolves, so the stop/TP symmetry
    is exact at entry and approximate thereafter. On a clean up-leg with no
    down-closing bars downside_ATR is 0, so full_atr is used as the risk unit.

    Returns ascending target prices strictly above entry (deduped), or None when
    there is not enough history to measure ATR.
    """
    entry_price = safe_float(entry_price)
    atr = downside_atr(candles, period=period)
    if not atr or atr <= 0:                     # calm up-leg: no down bars yet
        atr = full_atr(candles, period=period)
    if not atr or atr <= 0 or entry_price <= 0:
        return None
    risk_unit = k * atr
    node_prices = sorted(safe_float(n) for n in (nodes or []) if safe_float(n) > 0)
    levels = []
    for r in r_multiples:
        target = entry_price + safe_float(r) * risk_unit
        if node_prices:
            band = [n for n in node_prices
                    if target <= n <= target * (1.0 + node_snap_band)]
            if band:
                target = band[0]
        levels.append(target)
    out = []
    for target in sorted(levels):
        if target > entry_price and (not out or target > out[-1]):
            out.append(target)
    return out or None


# --------------------------------------------------------------------------- #
# Q3 unified runner-exit building blocks (fib + swing helpers)
# --------------------------------------------------------------------------- #
# Added per HANDOFF_runner_exit_and_telegram_stop_fix.md Part B.
# fibonacci_extension_levels for TP rungs (from swing_low to swing_high).
# entry_swing_low for base of fib (min low post-entry to peak or now).
# Keep minimal; callers (shadow) handle snapping to VP nodes via existing
# take_profit_levels logic or direct band filter.

def fibonacci_extension_levels(
    swing_low,
    swing_high,
    exts=(1.272, 1.618, 2.0, 2.618, 4.236),
):
    """Fibonacci extension levels.

    Level_i = swing_low + ext_i * (swing_high - swing_low).
    Returns ascending list of levels (typically > swing_high).
    """
    sl = safe_float(swing_low)
    sh = safe_float(swing_high)
    if sl <= 0 or sh <= sl:
        return []
    diff = sh - sl
    out = []
    for e in exts:
        lvl = sl + float(e) * diff
        if lvl > sh and (not out or lvl > out[-1]):
            out.append(lvl)
    return out


def entry_swing_low(candles, entry_ts, to_ts=None):
    """Min low among candles with ts >= entry_ts (and <= to_ts if given).

    Candles should be time-ascending. Used as fib swing_low base.
    Returns None if no usable lows.
    """
    if not candles:
        return None
    has_timestamps = any(
        safe_float(c.get("ts") or c.get("bucket_start") or c.get("timestamp") or 0) > 0
        for c in candles
    )
    lo = None
    for c in candles:
        cts = safe_float(c.get("ts") or c.get("bucket_start") or c.get("timestamp") or 0)
        if has_timestamps and entry_ts and cts < entry_ts:
            continue
        if has_timestamps and to_ts is not None and cts > to_ts:
            break
        cl = safe_float(c.get("low") or c.get("l"))
        if cl > 0:
            lo = cl if lo is None else min(lo, cl)
    return lo


# --------------------------------------------------------------------------- #
# 4. Corwin-Schultz effective spread                                   [STATE]
# --------------------------------------------------------------------------- #
#   An under-used, OHLC-only liquidity estimator (Corwin & Schultz 2012) that
#   recovers the bid-ask spread from two consecutive candles' high/low ranges.
#   On memecoins the spread IS the round-trip cost; a rising estimate is an
#   independent liquidity vote alongside #1.
_CS_K = 3.0 - 2.0 * math.sqrt(2.0)        # 0.17157...


def corwin_schultz_spread(c1, c2):
    """Estimated proportional effective spread from two consecutive candles.

    Negative estimates are floored to 0 per the paper. Returns None if the
    high/low inputs are unusable.
    """
    h1, l1 = _h(c1), _l(c1)
    h2, l2 = _h(c2), _l(c2)
    if min(h1, l1, h2, l2) <= 0:
        return None
    try:
        beta = math.log(h1 / l1) ** 2 + math.log(h2 / l2) ** 2
        hi = max(h1, h2)
        lo = min(l1, l2)
        gamma = math.log(hi / lo) ** 2
        alpha = (math.sqrt(2.0 * beta) - math.sqrt(beta)) / _CS_K \
            - math.sqrt(gamma / _CS_K)
        spread = 2.0 * (math.exp(alpha) - 1.0) / (1.0 + math.exp(alpha))
    except (ValueError, ZeroDivisionError):
        return None
    return max(spread, 0.0)


def corwin_schultz_series(candles, smooth=1):
    """Rolling spread over consecutive candle pairs (optionally mean-smoothed)."""
    raw = []
    for i in range(1, len(candles)):
        raw.append(corwin_schultz_spread(candles[i - 1], candles[i]))
    if smooth <= 1:
        return raw
    out = []
    for i in range(len(raw)):
        window = [v for v in raw[max(0, i - smooth + 1):i + 1] if v is not None]
        out.append(_mean(window) if window else None)
    return out


def spread_state(candles, window=20, smooth=2):
    """Current estimated spread + its percentile vs trailing history.

    `rising` (percentile high) == round-trip cost climbing == avoid / exit."""
    series = [v for v in corwin_schultz_series(candles, smooth=smooth)
              if v is not None]
    if len(series) < 4:
        return {"ready": False, "reason": "not_enough_spread"}
    current = series[-1]
    history = series[max(0, len(series) - window - 1):-1]
    return {
        "ready": True,
        "spread": current,
        "spread_pct_of_price": current,
        "spread_percentile": _percentile_rank(current, history),
        "rising": _percentile_rank(current, history) >= 0.80,
    }


# --------------------------------------------------------------------------- #
# 5. Volume-climax exhaustion z-score                                   [PRED]
# --------------------------------------------------------------------------- #
#   Climax volume + a rejection wick marks a local extreme. Blow-off top
#   (exit) vs capitulation bottom (possible reversal), disambiguated by which
#   wick dominates and where the bar closes.
def upper_wick_fraction(candle):
    high, low, close, open_ = _h(candle), _l(candle), _c(candle), _o(candle)
    rng = high - low
    if rng <= 0:
        return 0.0
    return (high - max(open_, close)) / rng


def lower_wick_fraction(candle):
    high, low, close, open_ = _h(candle), _l(candle), _c(candle), _o(candle)
    rng = high - low
    if rng <= 0:
        return 0.0
    return (min(open_, close) - low) / rng


def close_position(candle):
    """Where the bar closed within its range, in [0,1]. 1 = top, 0 = bottom."""
    high, low, close = _h(candle), _l(candle), _c(candle)
    rng = high - low
    if rng <= 0:
        return 0.5
    return max(0.0, min(1.0, (close - low) / rng))


def volume_zscore(candles, window=30):
    if len(candles) < 5:
        return None
    vols = [_v(c) for c in candles[-(window + 1):-1]]
    vols = [v for v in vols if v > 0]
    if len(vols) < 4:
        return None
    sd = _std(vols)
    if sd <= 0:
        return 0.0
    return (_v(candles[-1]) - _mean(vols)) / sd


def volume_climax(candles, window=30, z_thresh=2.5):
    """Detect a volume-climax exhaustion bar.

    blow_off_top:  z high + dominant upper wick + close in lower third  (EXIT)
    capitulation:  z high + dominant lower wick + close in upper third  (REVERSAL)
    """
    if len(candles) < 5:
        return {"ready": False, "reason": "not_enough_candles"}
    z = volume_zscore(candles, window=window)
    if z is None:
        return {"ready": False, "reason": "no_volume"}
    last = candles[-1]
    uw, lw, cp = (upper_wick_fraction(last), lower_wick_fraction(last),
                  close_position(last))
    return {
        "ready": True,
        "volume_z": z,
        "upper_wick": uw,
        "lower_wick": lw,
        "close_position": cp,
        "blow_off_top": z >= z_thresh and uw >= 0.5 and cp <= 0.34,
        "capitulation": z >= z_thresh and lw >= 0.5 and cp >= 0.66,
    }


# --------------------------------------------------------------------------- #
# self-test
# --------------------------------------------------------------------------- #
def _demo():
    import random
    rng = random.Random(7)
    candles = []
    price = 1.0
    liq = 50_000.0
    for i in range(120):
        drift = 0.01 if i < 70 else -0.012      # pump then fade
        o = price
        c = max(1e-6, o * (1 + drift + rng.uniform(-0.03, 0.03)))
        hi = max(o, c) * (1 + abs(rng.uniform(0, 0.02)))
        lo = min(o, c) * (1 - abs(rng.uniform(0, 0.02)))
        liq *= 1 + rng.uniform(-0.05, 0.05) - (0.01 if i > 70 else 0)
        candles.append({
            "open": o, "high": hi, "low": lo, "close": c,
            "volume": rng.uniform(500, 5000) * (3 if i in (40, 71) else 1),
            "liquidity": max(1000.0, liq),
            "bucket_start": 1_780_000_000 + i * 60,
        })
        price = c

    print("price_impact (last):      ", round(price_impact(candles[-1]), 4))
    print("illiquidity_spike:        ", illiquidity_spike(candles))
    print("chaikin_money_flow(20):   ", chaikin_money_flow(candles))
    print("money_flow_divergence:    ", money_flow_divergence(candles))
    print("downside_atr(14):         ", downside_atr(candles))
    print("full_atr(14):             ", full_atr(candles))
    print("take_profit_levels:       ", take_profit_levels(candles, _c(candles[0])))
    print("simulate_chandelier:      ", simulate_chandelier(candles, entry_idx=60))
    print("corwin_schultz (last pair):", corwin_schultz_spread(candles[-2], candles[-1]))
    print("spread_state:             ", spread_state(candles))
    print("volume_climax:            ", volume_climax(candles))


if __name__ == "__main__":
    _demo()
