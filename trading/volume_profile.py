"""Volume Profile from OHLCV -- where volume actually traded = real S/R.

On an AMM there is no resting limit-order book, but TRADED volume by price still
marks the levels the market respects: the Point of Control (POC, the single
busiest price) and the Value Area (the band holding ~70% of volume). Those are
structural stop anchors -- place a stop just beyond a high-volume node, not at a
guessed %.

Built from candles by spreading each bar's volume across its high-low range (the
standard OHLCV volume-profile approximation). Same candle dict contract as
trading/ohlcv_indicators.py (open/high/low/close/volume|volume_5m, +/- ts).
Pure functions + a CLI that reads token_candles for a live look.

CLI:
  python3 trading/volume_profile.py <TOKEN_MINT>
  python3 trading/volume_profile.py <TOKEN_MINT> --bins 40 --limit 600 --tf 60
"""
import argparse
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from trading.ohlcv_indicators import safe_float        # noqa: E402

DB_PATH = os.path.join(ROOT, "scanner.db")


def _hi(c):
    return safe_float(c.get("high", c.get("h")))


def _lo(c):
    return safe_float(c.get("low", c.get("l")))


def _cl(c):
    return safe_float(c.get("close", c.get("c")))


def _vol(c):
    return safe_float(c.get("volume", c.get("volume_5m", c.get("v"))))


def volume_profile(candles, bins=50, value_area_pct=0.70):
    """Histogram of traded volume by price.

    Returns dict: {lo, hi, bin_width, bins:[(mid, volume)], total,
    poc, poc_idx, vah, val} -- or {error} if there is not enough usable data.
    """
    usable = [c for c in candles if _hi(c) > 0 and _lo(c) > 0 and _hi(c) >= _lo(c)
              and _vol(c) > 0]
    if len(usable) < 5:
        return {"error": "not_enough_candles"}
    lo = min(_lo(c) for c in usable)
    hi = max(_hi(c) for c in usable)
    if hi <= lo:
        return {"error": "degenerate_range"}
    width = (hi - lo) / bins
    vols = [0.0] * bins

    def idx(price):
        i = int((price - lo) / width)
        return min(max(i, 0), bins - 1)

    for c in usable:
        c_lo, c_hi, v = _lo(c), _hi(c), _vol(c)
        rng = c_hi - c_lo
        b0, b1 = idx(c_lo), idx(c_hi)
        if rng <= 0 or b0 == b1:
            vols[idx(_cl(c) or c_lo)] += v             # single bar -> one bin
            continue
        for b in range(b0, b1 + 1):
            b_lo = lo + b * width
            b_hi = b_lo + width
            overlap = min(c_hi, b_hi) - max(c_lo, b_lo)
            if overlap > 0:
                vols[b] += v * (overlap / rng)         # spread across H-L

    return _assemble(lo, width, bins, vols, value_area_pct)


def _assemble(lo, width, bins, vols, value_area_pct):
    """Turn a binned volume array into a profile (POC + value area)."""
    total = sum(vols)
    if total <= 0:
        return {"error": "no_volume"}
    poc_idx = max(range(bins), key=lambda b: vols[b])
    lo_i = hi_i = poc_idx
    acc = vols[poc_idx]
    target = total * value_area_pct
    while acc < target and (lo_i > 0 or hi_i < bins - 1):
        below = vols[lo_i - 1] if lo_i > 0 else -1
        above = vols[hi_i + 1] if hi_i < bins - 1 else -1
        if above >= below:
            hi_i += 1
            acc += vols[hi_i]
        else:
            lo_i -= 1
            acc += vols[lo_i]
    mid = lambda b: lo + (b + 0.5) * width             # noqa: E731
    return {
        "lo": lo, "hi": lo + bins * width, "bin_width": width, "total": total,
        "bins": [(mid(b), vols[b]) for b in range(bins)],
        "poc": mid(poc_idx), "poc_idx": poc_idx,
        "vah": lo + (hi_i + 1) * width,
        "val": lo + lo_i * width,
    }


def volume_profile_from_trades(trades, bins=40, value_area_pct=0.70,
                               outlier_band=100.0):
    """TRUE volume profile from per-trade swaps -- each trade's volume lands in
    the bin of its exact execution price (no H-L spreading approximation).

    trades: list of {price, sol_volume} (from sources.onchain_swaps).
    outlier_band: drop trades outside [median/band, median*band] -- removes
    balance-delta parse artifacts (extreme prices) while keeping legitimate
    multi-x pump ranges. Set 0 to disable.
    """
    pts = [(float(t["price"]), float(t.get("sol_volume", 1.0))) for t in trades
           if float(t.get("price", 0)) > 0 and float(t.get("sol_volume", 0)) > 0]
    if len(pts) < 5:
        return {"error": "not_enough_trades"}
    dropped = 0
    if outlier_band and outlier_band > 1:
        prices = sorted(p for p, _ in pts)
        med = prices[len(prices) // 2]
        kept = [(p, v) for p, v in pts
                if med / outlier_band <= p <= med * outlier_band]
        if len(kept) >= 5:
            dropped = len(pts) - len(kept)
            pts = kept
    lo = min(p for p, _ in pts)
    hi = max(p for p, _ in pts)
    if hi <= lo:
        return {"error": "degenerate_range"}
    width = (hi - lo) / bins
    vols = [0.0] * bins
    for price, vol in pts:
        b = min(max(int((price - lo) / width), 0), bins - 1)
        vols[b] += vol
    prof = _assemble(lo, width, bins, vols, value_area_pct)
    prof["n_trades"] = len(pts)
    prof["dropped_outliers"] = dropped
    return prof


def high_volume_nodes(profile, n=5, below=None, above=None):
    """Top-n price bins by volume, optionally restricted below/above a price."""
    bins = profile.get("bins", [])
    pool = [(p, v) for p, v in bins if v > 0
            and (below is None or p < below)
            and (above is None or p > above)]
    return sorted(pool, key=lambda pv: pv[1], reverse=True)[:n]


def nearest_support(profile, price):
    """Highest-volume node strictly below `price` -- the structural stop anchor.
    Returns (node_price, volume) or None."""
    nodes = high_volume_nodes(profile, n=1, below=price)
    return nodes[0] if nodes else None


def _load_candles(token, tf=60, limit=600):
    con = sqlite3.connect(DB_PATH)
    rows = con.execute(
        "SELECT bucket_start, open, high, low, close, volume_5m, liquidity "
        "FROM token_candles WHERE token_address=? AND timeframe_seconds=? "
        "ORDER BY bucket_start DESC LIMIT ?", (token, tf, limit)).fetchall()
    rows = rows[::-1]
    return [{"bucket_start": b, "open": o, "high": h, "low": l, "close": c,
             "volume": v, "liquidity": liq} for b, o, h, l, c, v, liq in rows]


def _print_profile(profile, price=None, width=46):
    if profile.get("error"):
        print("  " + profile["error"])
        return
    vmax = max((v for _, v in profile["bins"]), default=0) or 1
    poc, vah, val = profile["poc"], profile["vah"], profile["val"]
    print(f"  {'price':>14}  volume")
    for p, v in reversed(profile["bins"]):              # high price on top
        bar = "#" * int(round(width * v / vmax))
        tag = ""
        if abs(p - poc) < profile["bin_width"]:
            tag = " <- POC"
        elif val <= p <= vah:
            tag = " ."                                  # inside value area
        here = " *price*" if price and abs(p - price) < profile["bin_width"] else ""
        print(f"  {p:>14.3e} {bar}{tag}{here}")
    print()
    print(f"  POC (busiest price): {poc:.3e}")
    print(f"  Value Area: {val:.3e}  ..  {vah:.3e}  (70% of volume)")
    if price:
        sup = nearest_support(profile, price)
        print(f"  current price:       {price:.3e}")
        if sup:
            dist = (sup[0] / price - 1) * 100
            print(f"  nearest support node below: {sup[0]:.3e} ({dist:+.1f}% away)"
                  f"  -> stop anchor just under this")
        else:
            print("  no high-volume support below price (thin underneath -> "
                  "expect a fast drop / gap)")


def main():
    ap = argparse.ArgumentParser(description="OHLCV volume profile")
    ap.add_argument("token", help="token mint address")
    ap.add_argument("--bins", type=int, default=40)
    ap.add_argument("--limit", type=int, default=600)
    ap.add_argument("--tf", type=int, default=60)
    args = ap.parse_args()
    candles = _load_candles(args.token, tf=args.tf, limit=args.limit)
    if not candles:
        print(f"no token_candles for {args.token} (tf={args.tf})")
        return
    profile = volume_profile(candles, bins=args.bins)
    price = _cl(candles[-1])
    print(f"Token: {args.token}  |  {len(candles)} candles @ {args.tf}s\n")
    _print_profile(profile, price=price)


if __name__ == "__main__":
    main()
