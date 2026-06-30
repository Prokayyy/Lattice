"""Shared volatility-scaled (downside-ATR) initial-stop sizing.

One implementation used by BOTH trading engines -- trading.position_engine
(main.py paper) and discovery.manager / live_runner (the watched paper book +
the on-chain resting stop) -- so they size the initial stop identically.

Default OFF via POSITION_ATR_STOP_ENABLED. Returns None when disabled or when
candle history is too thin, so every caller falls back to its existing flat
per-route stop %. Reads recent OHLC from token_candles read-only (the store the
scanner already builds); never writes and never raises.
"""
import os
import sqlite3

import config
from trading.ohlcv_indicators import downside_atr, safe_float

_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "scanner.db")


def _recent_candles_for_atr(address, as_of_ts=None):
    """Recent OHLC for `address`, oldest->newest. When `as_of_ts` is given, only
    candles with bucket_start <= as_of_ts are returned -- required so the shadow
    A/B replay (which drives historical timestamps) cannot read candles from
    AFTER the replayed instant (look-ahead). In live, ts is wall-clock so the
    bound is a no-op; passing None keeps the original live behaviour for the real
    engine's ATR stop, which never time-travels."""
    limit = int(config.POSITION_ATR_STOP_PERIOD) * 4 + 10
    tf = int(config.POSITION_ATR_STOP_TIMEFRAME_SECONDS)
    try:
        con = sqlite3.connect(f"file:{_DB}?mode=ro", uri=True, timeout=2.0)
        if as_of_ts is not None:
            rows = con.execute(
                "SELECT bucket_start, high, low, close FROM token_candles WHERE token_address=? "
                "AND timeframe_seconds=? AND bucket_start<=? "
                "ORDER BY bucket_start DESC LIMIT ?",
                (str(address), tf, float(as_of_ts), limit)).fetchall()
        else:
            rows = con.execute(
                "SELECT bucket_start, high, low, close FROM token_candles WHERE token_address=? "
                "AND timeframe_seconds=? ORDER BY bucket_start DESC LIMIT ?",
                (str(address), tf, limit)).fetchall()
        con.close()
    except Exception:
        return []
    rows = rows[::-1]
    return [{"bucket_start": b, "high": h, "low": l, "close": c} for b, h, l, c in rows
            if h and l and c]


def adaptive_initial_stop_pct(address, entry_price):
    """k * downside_ATR / entry_price, clamped to [MIN_PCT, MAX_PCT].

    None when POSITION_ATR_STOP_ENABLED is off, the price is unusable, or candle
    history is insufficient -- the caller then keeps its flat per-route %.
    """
    if not getattr(config, "POSITION_ATR_STOP_ENABLED", False):
        return None
    entry_price = safe_float(entry_price, 0)
    if entry_price <= 0 or not address:
        return None
    candles = _recent_candles_for_atr(address)
    if len(candles) < int(config.POSITION_ATR_STOP_MIN_CANDLES):
        return None
    atr = downside_atr(candles, period=int(config.POSITION_ATR_STOP_PERIOD))
    if not atr or atr <= 0:
        return None
    pct = float(config.POSITION_ATR_STOP_K) * atr / entry_price
    return max(float(config.POSITION_ATR_STOP_MIN_PCT),
               min(float(config.POSITION_ATR_STOP_MAX_PCT), pct))
