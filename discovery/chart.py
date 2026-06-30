"""Alert chart image (Pillow) — dark-theme OHLC candlesticks + volume + overlays.

Renders a compact PNG for an ENTRY SIGNAL. Preferred source is real OHLC from
GMGN klines (true candlesticks); falls back to the local token_candles close
series (area line) when GMGN is unavailable. Overlays the entry zone, the stop,
and the volume-profile POC / value area. Pure stdlib + Pillow.

  gmgn_ohlc_from_kline(data) -> [(o,h,l,c,v), ...] sorted ascending, or []
  render_alert_chart(alert, ohlc=None) -> PNG bytes, or None (fail-safe).
"""
import io
import os
import sqlite3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DB = os.path.join(ROOT, "scanner.db")

BG = (13, 17, 23)
GRID = (30, 36, 44)
UP = (61, 214, 140)
DOWN = (240, 95, 95)
VOL = (54, 64, 76)
TEXT = (150, 162, 176)
TITLE = (236, 241, 246)
STOPC = (240, 95, 95)
POCC = (242, 201, 92)
ZONE = (34, 78, 60)
VA = (26, 38, 50)


def _ff(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _candle_ts(c):
    for k in ("time", "timestamp", "t", "ts", "open_time", "unixTime"):
        if k in c:
            v = _ff(c[k])
            if v:
                return v / 1000.0 if v > 1e12 else v
    return 0.0


def gmgn_ohlc_from_kline(data):
    """Parse a GMGN `market kline` response into [(o,h,l,c,v)] ascending by time."""
    rows = (data or {}).get("list") or (data or {}).get("candles") or []
    out = []
    for c in rows:
        o, h, l, cl = (_ff(c.get("open")), _ff(c.get("high")),
                       _ff(c.get("low")), _ff(c.get("close")))
        v = _ff(c.get("volume")) or 0.0
        if None in (o, h, l, cl) or cl <= 0:
            continue
        out.append((_candle_ts(c), o, h, l, cl, v))
    out.sort(key=lambda r: r[0])
    return [(o, h, l, cl, v) for _, o, h, l, cl, v in out]


def _load_close(token, tf=60, limit=150):
    try:
        con = sqlite3.connect(f"file:{_DB}?mode=ro", uri=True, timeout=2.0)
        rows = con.execute(
            "SELECT close, volume_5m FROM token_candles WHERE token_address=? "
            "AND timeframe_seconds=? ORDER BY bucket_start DESC LIMIT ?",
            (str(token), tf, limit)).fetchall()
        con.close()
    except Exception:
        return []
    rows = rows[::-1]
    # represent as flat OHLC so one renderer handles both modes
    return [(float(c), float(c), float(c), float(c), float(v or 0))
            for c, v in rows if (c or 0) > 0]


def _font(name, size):
    from PIL import ImageFont
    try:
        return ImageFont.truetype(
            f"/usr/share/fonts/truetype/dejavu/{name}.ttf", size)
    except Exception:
        return ImageFont.load_default()


def _pfmt(p):
    if p <= 0:
        return "0"
    if p >= 1:
        return f"{p:.4f}"
    return f"{p:.3g}"


def render_alert_chart(alert, ohlc=None):
    try:
        from PIL import Image, ImageDraw
        token = getattr(alert, "token_address", "")
        candles = ohlc if (ohlc and len(ohlc) >= 8) else _load_close(token)
        candlestick = bool(ohlc and len(ohlc) >= 8)
        if len(candles) < 8:
            return None
        opens = [c[0] for c in candles]
        highs = [c[1] for c in candles]
        lows = [c[2] for c in candles]
        closes = [c[3] for c in candles]
        vols = [c[4] for c in candles]
        n = len(candles)

        W, H = 900, 470
        ML, MR, MT, MB = 10, 100, 50, 12
        gap = 10
        ph = int((H - MT - MB) * 0.72)
        vt = MT + ph + gap
        vh = H - MB - vt
        pl, pr = ML, W - MR
        pw = pr - pl

        extra = []
        try:
            lo, hi = float(alert.entry_zone[0]), float(alert.entry_zone[1])
            extra += [lo, hi]
        except Exception:
            lo = hi = None
        try:
            stop = float(alert.invalidation_price)
            extra.append(stop)
        except Exception:
            stop = None
        poc = val = vah = None
        try:
            from trading.volume_profile import volume_profile
            from trading.adaptive_stop import _recent_candles_for_atr
            prof = volume_profile(_recent_candles_for_atr(token), bins=24)
            if not prof.get("error"):
                poc, val, vah = prof["poc"], prof["val"], prof["vah"]
                extra += [poc, val, vah]
        except Exception:
            pass

        allp = highs + lows + [p for p in extra if p and p > 0]
        pmin, pmax = min(allp), max(allp)
        if pmax <= pmin:
            return None
        pad = (pmax - pmin) * 0.06
        pmin, pmax = pmin - pad, pmax + pad

        def yx(p):
            return MT + (1 - (p - pmin) / (pmax - pmin)) * ph

        def xx(i):
            return pl + (i / (n - 1)) * pw if n > 1 else pl + pw / 2

        img = Image.new("RGB", (W, H), BG)
        d = ImageDraw.Draw(img)
        f_title = _font("DejaVuSans-Bold", 22)
        f_lbl = _font("DejaVuSans", 13)
        f_mono = _font("DejaVuSansMono-Bold", 13)

        for k in range(1, 4):
            gy = MT + ph * k / 4
            d.line([(pl, gy), (pr, gy)], fill=GRID, width=1)
        if val and vah and vah > val:
            d.rectangle([pl, yx(vah), pr, yx(val)], fill=VA)
        if lo and hi and hi > lo:
            d.rectangle([pl, yx(hi), pr, yx(lo)], fill=ZONE)

        net_up = closes[-1] >= closes[0]
        net_col = UP if net_up else DOWN
        if candlestick:
            bw = max(1.0, pw / n * 0.62)
            for i in range(n):
                xc = xx(i)
                col = UP if closes[i] >= opens[i] else DOWN
                d.line([(xc, yx(highs[i])), (xc, yx(lows[i]))], fill=col, width=1)
                yo, yc = yx(opens[i]), yx(closes[i])
                top, bot = min(yo, yc), max(yo, yc)
                if bot - top < 1:
                    bot = top + 1
                d.rectangle([xc - bw / 2, top, xc + bw / 2, bot], fill=col)
        else:
            pts = [(xx(i), yx(closes[i])) for i in range(n)]
            d.polygon([(pl, MT + ph)] + pts + [(pr, MT + ph)],
                      fill=(net_col[0] // 5, net_col[1] // 5, net_col[2] // 5))
            d.line(pts, fill=net_col, width=2)
            d.ellipse([pts[-1][0] - 3, pts[-1][1] - 3,
                       pts[-1][0] + 3, pts[-1][1] + 3], fill=net_col)

        def hline(p, color, label):
            y = yx(p)
            x = pl
            while x < pr:
                d.line([(x, y), (min(x + 7, pr), y)], fill=color, width=1)
                x += 13
            d.text((pr + 4, y - 7), label, font=f_lbl, fill=color)

        if stop:
            hline(stop, STOPC, "stop")
        if poc:
            hline(poc, POCC, "POC")
        d.text((pr + 4, yx(closes[-1]) - 7), _pfmt(closes[-1]),
               font=f_mono, fill=net_col)

        vmax = max(vols) or 1
        bw = max(1.0, pw / n - 1)
        for i in range(n):
            vhh = (vols[i] / vmax) * vh
            x0 = xx(i) - bw / 2
            col = (UP if closes[i] >= opens[i] else DOWN) if candlestick else (
                net_col if i == n - 1 else VOL)
            d.rectangle([x0, vt + vh - vhh, x0 + bw, vt + vh], fill=col)

        sym = str(getattr(alert, "symbol", "") or "?")[:12]
        conv = float(getattr(alert, "conviction", 0) or 0)
        chg = (closes[-1] / closes[0] - 1) * 100 if closes[0] else 0
        d.text((ML, 13), f"${sym}", font=f_title, fill=TITLE)
        tx = ML + d.textlength(f"${sym}", font=f_title) + 16
        d.text((tx, 19), f"P(≥2x) {conv * 100:.0f}%   {chg:+.1f}%",
               font=f_lbl, fill=(UP if chg >= 0 else DOWN))
        src = "1m OHLC" if candlestick else "1m close"
        d.text((pr - 96, 19), src, font=f_lbl, fill=TEXT)

        out = io.BytesIO()
        img.save(out, format="PNG")
        return out.getvalue()
    except Exception:
        return None
