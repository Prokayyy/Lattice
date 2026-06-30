"""One-off: compose the live Lattice entry criteria from config and push it
to Telegram via the existing TelegramAlertSender."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config as c
from alerts.telegram import TelegramAlertSender


def onoff(v):
    return "on" if v else "off"


def g(name, default=None):
    return getattr(c, name, default)


sub_floor = g("LATTICE_MIN_ENTRY_LATTICE", 0.0)
cap1h = g("LATTICE_MAX_ENTRY_PRICE_CHANGE_1H", 0.0)
cap24h = g("LATTICE_MAX_ENTRY_PRICE_CHANGE_24H", 0.0)
pb_breadth = g("LATTICE_PAPER_BUY_MIN_BREADTH", 0.35)
pb_min5 = g("LATTICE_PAPER_BUY_MIN_PRICE_CHANGE_5M", 4.0)
pb_max5 = g("LATTICE_PAPER_BUY_MAX_PRICE_CHANGE_5M", 20.0)
sec_on = g("GMGN_SECURITY_GATE_ENABLED", False)
sec_tax = g("GMGN_SECURITY_MAX_SELL_TAX", 0.10)
sec_top10 = g("GMGN_SECURITY_MAX_TOP10_RATE", 0.0)
sec_renounce = g("GMGN_SECURITY_REQUIRE_RENOUNCED", False)
kl_on = g("LATTICE_GMGN_KLINE_FADE_FILTER_ENABLED", False)
kl_wick = g("LATTICE_GMGN_KLINE_MAX_UPPER_WICK_RATIO", 0.5)
kl_dd = g("LATTICE_GMGN_KLINE_MAX_DRAWDOWN_FROM_HIGH_PCT", -25.0)
liq_ovr = g("GMGN_LIQUIDITY_OVERRIDE_ENABLED", False)
backfill = g("GMGN_SCAN_BACKFILL_ENABLED", False)
stop_pct = g("LATTICE_EXIT_INITIAL_STOP_PCT", 0.30)
ladder = g("LATTICE_EXIT_SCALE_OUT_LADDER", ())
maxhold = g("LATTICE_MAX_HOLD_H", 12.0)
monitor = g("LATTICE_OPEN_POSITION_MONITOR_INTERVAL_SECONDS", 5.0)

ladder_str = ", ".join(f"{int(t*100)}%@{m:g}x" for m, t in ladder) or "—"
cap1h_str = f"{cap1h:.0f}%" if cap1h else "off"
sec_extra = []
if sec_top10:
    sec_extra.append(f"top10≤{sec_top10*100:.0f}%")
if sec_renounce:
    sec_extra.append("renounced mint+freeze")
sec_extra_str = (" + " + ", ".join(sec_extra)) if sec_extra else ""

text = (
    "<b>[ LATTICE ENTRY CRITERIA ]</b> (live, paper)\n"
    "A token must clear ALL of these, in order:\n\n"
    "<b>1. Universe</b> — 5m change &gt; 2% AND 1h volume &gt; 0\n"
    "<b>2. Lattice vetoes</b> — reject if: liquidity draining while price up · "
    "5m blow-off &gt;150% · thin book (VLR&gt;4) · flow without breadth (wash)\n"
    f"<b>3. Lattice score</b> ≥ <b>{sub_floor:.2f}</b> "
    "(blend of flow / liquidity / structure / participation)\n"
    f"<b>4. Not overheated</b> — 24h change ≤ <b>{cap24h:.0f}%</b> "
    f"(1h cap: {cap1h_str})\n"
    "<b>5. Conviction</b> ≥ <b>0.18</b> (calibrated P(≥2x))\n"
    "<b>6. Breadth</b> ≥ <b>-0.4</b> when known — holder concentration "
    "(top-10 share) + unique-buyer breadth/asymmetry; needs Helius, else blind→allowed\n"
    "<b>7. Liquidity lock</b> — must pass lock/safety check\n"
    f"<b>8. Paper-buy gate</b> — breadth ≥ <b>{pb_breadth:.2f}</b> AND "
    f"5m change in <b>[{pb_min5:.0f}, {pb_max5:.0f}]%</b>\n"
    f"<b>9. GMGN security</b> ({onoff(sec_on)}) — block honeypot / unsellable / "
    f"blacklist / sell-tax &gt;{sec_tax*100:.0f}%{sec_extra_str}\n"
    f"<b>10. GMGN kline fade</b> ({onoff(kl_on)}) — block blow-off wick "
    f"&gt;{kl_wick:.2f} of range OR &gt;{abs(kl_dd):.0f}% below 1h high\n"
    "<b>11. Brakes</b> — entry cooldown · entries/hr cap · zone discipline\n\n"
    "<b>Data augmentation</b>: GMGN liquidity override "
    f"({onoff(liq_ovr)}) · scan-time backfill ({onoff(backfill)})\n"
    f"<b>Exit</b>: stop -{stop_pct*100:.0f}% · scale {ladder_str} · "
    f"max hold {maxhold:.0f}h · position monitor {monitor:g}s"
)

chat_ids = list(c.TELEGRAM_CHAT_IDS or ([c.TELEGRAM_CHAT_ID] if c.TELEGRAM_CHAT_ID else []))


async def main():
    sender = TelegramAlertSender()
    n = await sender.send_message(
        {
            "chat_ids": chat_ids,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        "entry criteria sent",
    )
    print("sent to", chat_ids, "->", n)


asyncio.run(main())
