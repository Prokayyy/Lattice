"""Shared Telegram message formatting helpers (HTML parse mode).

Used by discovery/notify.py and agents/telegram_agent.py so the lattice and
ignition messages render prices, money and durations the same way.

Token prices use the memecoin subscript-zero convention instead of scientific
notation: 0.00001331 -> $0.0₄1331 (the subscript is the zero count), which
reads at a glance and survives Telegram's proportional font.
"""
import os
import re
from urllib.parse import quote

_SUBSCRIPTS = "₀₁₂₃₄₅₆₇₈₉"
SOLANA_ADDRESS_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")


def solana_address(value):
    addr = str(value or "").strip()
    return addr if SOLANA_ADDRESS_RE.fullmatch(addr) else ""


def jup_url(address):
    """Jupiter token page for a mint, carrying our referral code so swaps
    opened from an alert credit the scanner.

    e.g. https://jup.ag/tokens/<MINT>?ref=0l687n5vi50j

    The ref code is read lazily from JUPITER_REFERRAL_CODE (so a .env loaded
    after import still takes effect) and falls back to the live code. Returns
    '' for a blank address."""
    addr = solana_address(address)
    if not addr:
        return ""
    ref = os.environ.get("JUPITER_REFERRAL_CODE", "0l687n5vi50j").strip()
    url = f"https://jup.ag/tokens/{quote(addr, safe='')}"
    if ref:
        url += f"?ref={quote(ref, safe='')}"
    return url


def gmgn_url(address):
    """GMGN token page for a mint, carrying our referral code so a page
    opened from an alert credits the scanner.

    e.g. https://gmgn.ai/sol/token/Venerable_<MINT>

    GMGN encodes the referral by prefixing the mint with '<code>_' (the same
    code as the https://gmgn.ai/r/<code> landing link). The ref code is read
    lazily from GMGN_REFERRAL_CODE (so a .env loaded after import still takes
    effect) and falls back to the live code. Returns '' for a blank address."""
    addr = solana_address(address)
    if not addr:
        return ""
    ref = os.environ.get("GMGN_REFERRAL_CODE", "Venerable").strip()
    prefix = f"{quote(ref, safe='')}_" if ref else ""
    return f"https://gmgn.ai/sol/token/{prefix}{quote(addr, safe='')}"


def dexscreener_solana_url(address):
    addr = solana_address(address)
    if not addr:
        return ""
    return f"https://dexscreener.com/solana/{quote(addr, safe='')}"


def _f(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def fmt_token_price(price):
    """$0.0<sub>n</sub>dddd for tiny prices, trimmed fixed otherwise."""
    p = _f(price)
    if p <= 0:
        return "$0"
    if p >= 1:
        return f"${p:,.4g}"
    if p >= 0.001:
        text = f"{p:.6f}".rstrip("0")
        return f"${text}"
    frac = f"{p:.15f}".split(".")[1]
    zeros = len(frac) - len(frac.lstrip("0"))
    digits = (frac[zeros:zeros + 4] or "0").ljust(4, "0")
    sub = "".join(_SUBSCRIPTS[int(c)] for c in str(zeros))
    return f"$0.0{sub}{digits}"


def fmt_usd(amount, signed=False):
    """Compact dollars: $18.02, +$1.5K, -$3.35, $1.2M."""
    n = _f(amount)
    if abs(n) < 0.005:  # avoid the "-$0.00" artifact on float dust
        n = 0.0
    sign = "-" if n < 0 else ("+" if signed and n > 0 else "")
    a = abs(n)
    if a >= 1_000_000:
        body = f"{a / 1_000_000:.2f}M"
    elif a >= 10_000:
        body = f"{a / 1_000:.1f}K"
    elif a >= 1_000:
        body = f"{a / 1_000:.2f}K"
    elif a >= 100:
        body = f"{a:.0f}"
    else:
        body = f"{a:.2f}"
    return f"{sign}${body}"


def fmt_pct(value, signed=True, decimals=0):
    """0.22 -> 22% ; -0.02 -> -2% (input is a fraction)."""
    n = _f(value) * 100
    sign = "+" if signed and n > 0 else ""
    return f"{sign}{n:.{decimals}f}%"


def fmt_duration(seconds):
    """90 -> 1m, 7500 -> 2h05m, 200000 -> 2d8h."""
    s = max(_f(seconds), 0)
    if s < 60:
        return f"{s:.0f}s"
    m = s / 60
    if m < 60:
        return f"{m:.0f}m"
    h = m / 60
    if h < 24:
        whole_h = int(h)
        rem_m = int(round((h - whole_h) * 60))
        if rem_m >= 60:
            whole_h, rem_m = whole_h + 1, 0
        return f"{whole_h}h{rem_m:02d}m" if rem_m else f"{whole_h}h"
    d = int(h // 24)
    rem_h = int(h - d * 24)
    return f"{d}d{rem_h}h" if rem_h else f"{d}d"


def pnl_emoji(value):
    n = _f(value)
    if n > 0.005:
        return "\U0001f7e2"  # green circle
    if n < -0.005:
        return "\U0001f534"  # red circle
    return "⚪"  # white circle
