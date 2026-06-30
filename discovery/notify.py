"""Telegram delivery for the discovery scanner — tagged [LATTICE] 💎.

Wraps the existing TelegramAlertSender. Posts to the main chat(s) the live bot
uses (TELEGRAM_CHAT_IDS in .env), but every message is prefixed with a clear
[LATTICE] tag so the experimental scanner's alerts are distinguishable from
the live bot's. Ignition-summary delivery for public ENTRY SIGNAL copies is
handled separately by tools/lattice_user_relay.py so paper trades do not land
in the summary chat.

Safety:
- LATTICE_TELEGRAM_DRY_RUN=true  -> never sends; prints the message instead.
- LATTICE_TELEGRAM_ENABLED=false -> disables sending (prints).
Default is enabled + live. Use dry-run to preview formatting.
"""
import html
import os
import time
from urllib.parse import quote

from discovery.narrative_context import format_narrative_context
from utils.tg_format import (
    fmt_duration,
    fmt_pct,
    fmt_token_price as _fmt_price,
    fmt_usd as _fmt_usd,
    dexscreener_solana_url,
    gmgn_url,
    jup_url,
    pnl_emoji,
)

TAG = "💎 <b>[LATTICE]</b>"


def _envflag(name, default):
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def _esc(s):
    return html.escape(str(s or ""))


def _ts_hm(ts):
    try:
        return time.strftime("%H:%M", time.localtime(float(ts or 0)))
    except (TypeError, ValueError, OSError):
        return "?:??"


# 🔥 message effect (private chats only; _send fail-safes if the chat rejects it)
_FIRE_EFFECT = "5104841245755180586"
_SPARK_BLOCKS = "▁▂▃▄▅▆▇█"


def _bar(frac, n=8):
    """Unicode meter, e.g. ▰▰▰▰▱▱▱▱ for a 0..1 fraction."""
    try:
        f = max(0.0, min(1.0, float(frac)))
    except (TypeError, ValueError):
        return ""
    filled = int(round(f * n))
    return "▰" * filled + "▱" * (n - filled)


def _sparkline(token, n=24):
    """Tiny price sparkline from recent token_candles. '' on any failure."""
    try:
        from trading.adaptive_stop import _recent_candles_for_atr
        closes = [c["close"] for c in _recent_candles_for_atr(token)[-n:]
                  if c.get("close", 0) > 0]
        if len(closes) < 4:
            return ""
        lo, hi = min(closes), max(closes)
        if hi <= lo:
            return ""
        spark = "".join(
            _SPARK_BLOCKS[min(int((c - lo) / (hi - lo) * 7), 7)] for c in closes)
        return f"{spark} {(closes[-1] / closes[0] - 1) * 100:+.0f}%"
    except Exception:
        return ""


def _compact_live_reason(reason):
    if isinstance(reason, dict):
        code = str(reason.get("code") or "")
        message = str(reason.get("message") or "")
        text = f"{code}: {message}" if code else message
    else:
        text = str(reason or "")

    lowered = text.lower()

    if "asset instance not found" in lowered:
        return "flash_asset_not_supported"
    if "insufficient_funder" in lowered or "insufficient funder" in lowered:
        return "insufficient_funder_balance"
    if "flash_fill_timeout_cancelled" in lowered:
        return "flash_timeout_cancelled"
    if "flash_fill_timeout" in lowered:
        return "flash_fill_timeout"
    if "invalid_argument" in lowered:
        return "flash_invalid_argument"
    if "quote route is higher than" in lowered:
        return text[:90] if len(text) > 90 else text

    return text[:140] if len(text) > 140 else text


class LatticeNotifier:
    def __init__(self, dry_run=None):
        self.enabled = _envflag("LATTICE_TELEGRAM_ENABLED", True)
        self.dry = _envflag("LATTICE_TELEGRAM_DRY_RUN", False) if dry_run is None else dry_run
        self.sender = None
        if self.enabled and not self.dry:
            from alerts.telegram import TelegramAlertSender  # lazy: only when live
            self.sender = TelegramAlertSender()

    def _lattice_chat_ids(self):
        if self.sender is None:
            return []

        chat_ids = list(getattr(self.sender, "chat_ids", None) or [])
        if not chat_ids and getattr(self.sender, "chat_id", None):
            chat_ids = [self.sender.chat_id]

        return list(dict.fromkeys(chat_ids))

    async def _send(self, text, reply_markup=None, message_effect_id=None):
        if self.dry or not self.enabled or self.sender is None:
            tag = f" (effect {message_effect_id})" if message_effect_id else ""
            print("[DRY-TG]" + tag + "\n" + text + "\n")
            return 0
        payload = {
            "chat_ids": self._lattice_chat_ids(),
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        if message_effect_id:
            payload["message_effect_id"] = message_effect_id
        sent = await self.sender.send_message(payload, "lattice alert sent")
        if sent == 0 and message_effect_id:
            # effect rejected (non-private chat) -> resend plain so the alert lands
            payload.pop("message_effect_id", None)
            sent = await self.sender.send_message(payload, "lattice alert sent")
        return sent

    async def _send_photo(self, photo_bytes, caption, reply_markup=None):
        """Send a chart PNG with an HTML caption + keyboard (multipart sendPhoto).
        Returns chats sent; 0 on any failure so the caller falls back to text."""
        if self.dry or not self.enabled or self.sender is None:
            print("[DRY-TG-PHOTO]\n" + (caption or "")[:300] + "\n")
            return 0
        base = getattr(self.sender, "base_url", "")
        if not base:
            return 0
        import json as _json

        import aiohttp
        sent = 0
        try:
            async with aiohttp.ClientSession() as session:
                for chat_id in self._lattice_chat_ids():
                    form = aiohttp.FormData()
                    form.add_field("chat_id", str(chat_id))
                    form.add_field("caption", caption or "")
                    form.add_field("parse_mode", "HTML")
                    if reply_markup:
                        form.add_field("reply_markup", _json.dumps(reply_markup))
                    form.add_field("photo", photo_bytes, filename="chart.png",
                                   content_type="image/png")
                    async with session.post(base + "/sendPhoto", data=form) as r:
                        if r.status == 200:
                            sent += 1
            if sent:
                print("lattice alert sent")
        except Exception as e:
            print(f"Telegram photo send error: {e}")
            return 0
        return sent

    # ---- formatters ----
    def _bundle_lines(self, bundle):
        """(headline_line, blockquote_line) for a Solana Tracker bundle label,
        or ('', '') when there is nothing to show. headline is always shown when
        evidence exists (incl. 'unknown' on a failed check — never hidden, so an
        absent label is never mistaken for a clean book); the wallet/cluster list
        goes in the expandable quote."""
        if not bundle:
            return "", ""
        from sources import solanatracker as st
        head = st.headline(bundle)
        headline_line = f"\n🧷 {_esc(head)}" if head else ""
        wallets = st.wallet_summary(bundle)
        if not wallets:
            return headline_line, ""
        chips = []
        for addr, pct, init in wallets:
            link = (f"<a href=\"https://solscan.io/account/{_esc(addr)}\">"
                    f"{_esc(st.short_addr(addr))}</a>")
            tag = f"{pct:.1f}%" if pct >= 0.1 else f"sold (was {init or 0:.1f}%)"
            chips.append(f"{link} {tag}")
        return headline_line, "🧷 cluster: " + " · ".join(chips)

    def fmt_signal(self, alert, entry_status="", intel=None, evidence_block=True,
                   bundle=None):
        lo, hi = alert.entry_zone
        ev = alert.evidence or {}
        br = ev.get("breadth")
        intel = intel or {}

        stop_text = _fmt_price(alert.invalidation_price)
        try:
            stop_pct = float(alert.invalidation_price) / float(lo) - 1
            stop_text += f" ({stop_pct:+.0%} from zone low)"
        except (TypeError, ValueError, ZeroDivisionError):
            pass

        scores = (
            f"revival {alert.revival_score:.2f} · "
            f"lattice {alert.lattice_composite:.2f}"
        )
        if br is None:
            scores += " · breadth blind"
        else:
            scores += f" · breadth {br:+.2f}"
        flow_bits = []
        if ev.get("buyers_sig") is not None:
            flow_bits.append(f"buyers {ev['buyers_sig']:+.2f}")
        if ev.get("concentration") is not None:
            flow_bits.append(f"top-holder conc {ev['concentration']:.0%}")
        narrative_line = format_narrative_context(
            getattr(alert, "narrative_context", {}) or {}
        )
        if narrative_line:
            narrative_line = f"\n📰 {narrative_line}"

        # smart-money holders (GMGN) — present when enrichment has landed
        smart_bits = []
        if intel.get("smart_count") is not None:
            n = int(intel.get("smart_count") or 0)
            smart_bits.append(f"{n} smart wallet{'s' if n != 1 else ''}")
            share = intel.get("smart_share_pct")
            if share is not None and share >= 0.01:
                smart_bits.append(f"{share:.1f}% of supply")
            usd = intel.get("smart_usd")
            if usd:
                smart_bits.append(_fmt_usd(usd))
            profit_n = intel.get("smart_profit_n")
            if n and profit_n is not None:
                smart_bits.append(f"{int(profit_n)} in profit")

        # twitter chatter + an instant CA search link on X
        tw_bits = []
        if intel.get("tw_mentions") is not None:
            tw_bits.append(f"{int(intel['tw_mentions'])} mentions")
            if intel.get("tw_authors"):
                tw_bits.append(f"{int(intel['tw_authors'])} authors")
            if intel.get("tw_top_followers"):
                tw_bits.append(f"top {int(intel['tw_top_followers']):,} followers")
        x_link = (
            "<a href=\"https://x.com/search?q="
            f"{_esc(alert.token_address)}&amp;f=live\">search CA on X</a>"
        )

        # OKX vibe score (X/Twitter hotness, 0-100) — present when enrichment
        # landed and the token had a score this window.
        vibe_line = ""
        if intel.get("vibe_score") is not None:
            try:
                vibe_line = f"🔥 vibe {float(intel['vibe_score']):.2f}/100"
                rate = intel.get("vibe_change_rate")
                if rate is not None:
                    vibe_line += f" ({float(rate):+.0f}% 24h)"
            except (TypeError, ValueError):
                vibe_line = ""

        # OKX smart-money/KOL/whale buy flow — present only when tracked wallets
        # are buying THIS token (independent corroboration of the entry).
        flow_line = ""
        sig = intel.get("okx_signal")
        if isinstance(sig, dict) and sig.get("signals"):
            from sources.okx_signal import okx_signal_client
            who = []
            if sig.get("smart"):
                who.append(f"{int(sig['smart'])} smart")
            if sig.get("kol"):
                who.append(f"{int(sig['kol'])} KOL")
            if sig.get("whale"):
                who.append(f"{int(sig['whale'])} whale")
            bits = [" · ".join(who)] if who else []
            amt = sig.get("amount_usd")
            if amt:
                bits.append(_fmt_usd(amt))
            label, caution = okx_signal_client.holding_label(sig.get("sold_ratio_avg"))
            if label:
                bits.append(("⚠️ " if caution else "") + label)
            flow_line = "🐋 OKX flow: " + " · ".join(b for b in bits if b)

        entry_line = ""
        if entry_status:
            status = str(entry_status)
            mark = "⛔" if status.lower().startswith(("not ", "no ", "skip")) else "✅"
            entry_line = f"\n{mark} {_esc(status)}"

        bundle_headline, bundle_quote = self._bundle_lines(bundle)

        # collapse the evidence into an expandable quote (compact feed, tap = detail)
        evidence = [f"📊 {scores}"]
        if flow_bits:
            evidence.append("💧 " + " · ".join(flow_bits))
        if smart_bits:
            evidence.append("🧠 " + " · ".join(smart_bits))
        evidence.append("🐦 " + " · ".join(tw_bits + [x_link]))
        if flow_line:
            evidence.append(flow_line)
        if bundle_quote:
            evidence.append(bundle_quote)
        if narrative_line:
            evidence.append(narrative_line.strip())
        blockquote = (
            "\n<blockquote expandable>" + "\n".join(evidence) + "</blockquote>"
            if evidence_block else ""
        )
        compact_flow_line = (
            f"\n{flow_line}" if flow_line and not evidence_block else ""
        )

        addr = _esc(alert.token_address)
        spark = _sparkline(alert.token_address)
        spark_line = f"  <code>{spark}</code>" if spark else ""

        return (
            f"{TAG} 🎯 <b>ENTRY — ${_esc(alert.symbol) or '?'}</b>\n"
            f"P(≥2x) <b>{fmt_pct(alert.conviction, signed=False)}</b>  "
            f"{_bar(alert.conviction)}{spark_line}\n"
            f"🎯 zone  {_fmt_price(lo)} → {_fmt_price(hi)}\n"
            f"🛑 stop  <tg-spoiler>{stop_text}</tg-spoiler>"
            f"{entry_line}"
            f"{bundle_headline}"
            f"{(chr(10) + vibe_line) if vibe_line else ''}"
            f"{compact_flow_line}"
            f"{blockquote}\n"
            f"📈 <a href=\"{dexscreener_solana_url(alert.token_address)}\">DexScreener</a> · "
            f"<a href=\"{gmgn_url(alert.token_address)}\">GMGN</a> · "
            f"<a href=\"https://birdeye.so/token/{quote(alert.token_address, safe='')}?chain=solana\">Birdeye</a> · "
            f"<a href=\"{jup_url(alert.token_address)}\">Jupiter</a>\n"
            f"<code>{addr}</code>"
        )

    def _build_alert_keyboard(self, alert):
        """Inline buttons for ENTRY SIGNAL alerts using rich formatting."""
        addr = alert.token_address
        return {
            "inline_keyboard": [
                [{"text": "Copy CA", "copy_text": {"text": addr}}],
                [
                    {"text": "📊 DexScreener",
                     "url": dexscreener_solana_url(addr)},
                    {"text": "💊 GMGN",
                     "url": gmgn_url(addr)}
                ],
                [
                    {"text": "🦅 Birdeye",
                     "url": f"https://birdeye.so/token/{quote(addr, safe='')}?chain=solana"},
                    {"text": "🐦 X",
                     "url": f"https://x.com/search?q={quote(addr, safe='')}&f=live"}
                ],
                [
                    {"text": "🪐 Trade on Jupiter", "url": jup_url(addr)}
                ]
            ]
        }

    def _build_paper_keyboard(self, pos):
        addr = pos.get("token") or pos.get("token_address") or ""
        if not addr:
            return None
        return {
            "inline_keyboard": [
                [{"text": "Copy CA", "copy_text": {"text": addr}}],
                [
                    {"text": "📊 DexScreener",
                     "url": dexscreener_solana_url(addr)},
                    {"text": "💊 GMGN",
                     "url": gmgn_url(addr)}
                ],
                [
                    {"text": "🦅 Birdeye",
                     "url": f"https://birdeye.so/token/{quote(addr, safe='')}?chain=solana"},
                    {"text": "🐦 X",
                     "url": f"https://x.com/search?q={quote(addr, safe='')}&f=live"}
                ],
                [
                    {"text": "🪐 Trade on Jupiter", "url": jup_url(addr)}
                ]
            ]
        }

    def fmt_paper_entry(self, pos, wallet_usd):
        fdv = pos.get("entry_fdv_usd")
        fdv_text = f" · FDV {_fmt_usd(fdv)}" if fdv else ""
        live_line = self.fmt_live_entry_status(pos)
        addr = pos.get("token") or pos.get("token_address") or ""
        ca_line = f"\n<code>{_esc(addr)}</code>" if addr else ""
        links = ""
        if addr:
            links = f"\n📈 <a href=\"{dexscreener_solana_url(addr)}\">DexScreener</a> · <a href=\"{gmgn_url(addr)}\">GMGN</a> · <a href=\"{jup_url(addr)}\">Jupiter</a>"
        spark = _sparkline(addr)
        spark_line = f"\n📉 <code>{spark}</code>" if spark else ""
        return (
            f"{TAG} 📥 <b>PAPER BUY — ${_esc(pos['symbol']) or '?'}</b>\n"
            f"{_fmt_usd(pos['cost_usd'])} @ {_fmt_price(pos['entry_price'])}"
            f"{fdv_text} · conv {fmt_pct(pos['conviction'], signed=False)}"
            f"{spark_line}\n"
            f"{live_line}\n"
            f"wallet {_fmt_usd(wallet_usd)}"
            f"{links}{ca_line}"
        )

    def fmt_live_entry_status(self, pos):
        if not pos.get("live_execution_entry_attempted"):
            return "live: not attempted"

        order_id = str(pos.get("live_execution_entry_order_id") or "")
        order_short = f" | order {order_id[:8]}" if order_id else ""
        reason = _esc(
            _compact_live_reason(
                pos.get("live_execution_entry_reason") or ""
            )
        )
        notional = pos.get("live_execution_entry_notional_usd")
        notional_text = f"${float(notional):.0f}" if notional else "?"
        filled = float(pos.get("live_execution_entry_filled_target_amount") or 0)

        if pos.get("live_execution_entry_submitted") and filled > 0:
            return f"live: FILLED {notional_text}{order_short}"

        if pos.get("live_execution_entry_order_submitted"):
            status = reason or "submitted_pending_or_unfilled"
            return f"live: NO FILL ({status}){order_short}"

        status = reason or "not_submitted"
        return f"live: NOT SUBMITTED ({status})"

    def fmt_paper_exit(self, pos, wallet_usd):
        pnl = float(pos.get("pnl_usd") or 0)
        cost = float(pos.get("cost_usd") or 0)
        proceeds = float(pos.get("proceeds") or 0)
        realized_mult = proceeds / cost if cost > 0 else 0.0
        pnl_pct_text = f" ({pnl / cost:+.0%})" if cost > 0 else ""

        held_text = ""
        entry_ts = float(pos.get("entry_ts") or 0)
        exit_ts = float(pos.get("exit_ts") or 0) or time.time()
        if entry_ts > 0:
            held_text = f" · held {fmt_duration(exit_ts - entry_ts)}"

        reason = str(pos.get("reason") or "?").replace("_", " ")
        addr = pos.get("token") or pos.get("token_address") or ""
        ca_line = f"\n<code>{_esc(addr)}</code>" if addr else ""
        links = ""
        if addr:
            links = f"\n📈 <a href=\"{dexscreener_solana_url(addr)}\">DexScreener</a> · <a href=\"{gmgn_url(addr)}\">GMGN</a> · <a href=\"{jup_url(addr)}\">Jupiter</a>"
        spark = _sparkline(addr)
        spark_line = f"\n📉 <code>{spark}</code>" if spark else ""
        return (
            f"{TAG} 📤 <b>PAPER SELL — ${_esc(pos['symbol']) or '?'}</b> "
            f"{pnl_emoji(pnl)}\n"
            f"realized {realized_mult:.2f}x · peak {pos['peak_mult']:.2f}x"
            f"{held_text}{spark_line}\n"
            f"reason: {_esc(reason)}\n"
            f"PnL <b>{_fmt_usd(pnl, signed=True)}</b>{pnl_pct_text} · "
            f"wallet {_fmt_usd(wallet_usd)}"
            f"{links}{ca_line}"
        )

    def fmt_paper_scale_out(
        self,
        pos,
        kind,
        qty,
        price,
        wallet_usd,
        sold_cum=None,
        realized_pnl=None,
    ):
        try:
            qty = float(qty)
            price = float(price)
            entry_price = float(pos.get("entry_price") or 0)
            cost_usd = float(pos.get("cost_usd") or 0)
            remaining = float(pos.get("remaining") or 0)
            peak = float(pos.get("peak") or price or 0)
        except (TypeError, ValueError):
            qty = 0.0
            price = 0.0
            entry_price = 0.0
            cost_usd = 0.0
            remaining = 0.0
            peak = 0.0

        initial_qty = cost_usd / entry_price if entry_price > 0 else 0.0
        if sold_cum is None:
            sold_cum = (
                max(0.0, min(1.0, 1.0 - remaining / initial_qty))
                if initial_qty > 0
                else 0.0
            )
        else:
            try:
                sold_cum = max(0.0, min(1.0, float(sold_cum)))
            except (TypeError, ValueError):
                sold_cum = 0.0
        multiple = price / entry_price if entry_price > 0 else 0.0
        peak_mult = peak / entry_price if entry_price > 0 else multiple
        level = str(kind or "").replace("scale_", "") or "scale"
        sold_usd = qty * price
        realized_text = ""
        if realized_pnl is not None:
            realized_text = f" · realized {_fmt_usd(realized_pnl, signed=True)}"

        return (
            f"{TAG} 💸 <b>SCALE-OUT — ${_esc(pos.get('symbol') or '?')}</b>\n"
            f"sold {_fmt_usd(sold_usd)} @ {_fmt_price(price)} "
            f"(<b>{multiple:.2f}x</b>) · {sold_cum:.0%} banked"
            f"{realized_text}\n"
            f"level {_esc(level)} · peak {peak_mult:.2f}x · "
            f"wallet {_fmt_usd(wallet_usd)}"
        )

    # ---- send helpers ----
    async def signal(self, alert, entry_status="", intel=None, bundle=None):
        text = self.fmt_signal(alert, entry_status=entry_status, intel=intel,
                               bundle=bundle)
        kb = self._build_alert_keyboard(alert)
        effect = None
        # effects are private-DM-only; default OFF since lattice alerts go to
        # a group/channel (set true if you route them to a DM).
        if _envflag("LATTICE_TG_MESSAGE_EFFECT", False):
            try:
                threshold = float(os.environ.get(
                    "LATTICE_TG_EFFECT_MIN_CONVICTION", "0.5"))
                if float(getattr(alert, "conviction", 0) or 0) >= threshold:
                    effect = _FIRE_EFFECT
            except (TypeError, ValueError):
                pass
        if _envflag("LATTICE_TG_CHART_ENABLED", False):
            ohlc = None
            try:
                import asyncio
                from sources.gmgn import gmgn_client
                from discovery.chart import gmgn_ohlc_from_kline
                if gmgn_client.enabled():
                    now = int(time.time())
                    data = await asyncio.wait_for(
                        gmgn_client.token_kline(
                            alert.token_address, "1m", now - 7200, now,
                            chain="sol"), 8.0)
                    ohlc = gmgn_ohlc_from_kline(data)
            except Exception:
                ohlc = None
            try:
                from discovery.chart import render_alert_chart
                png = render_alert_chart(alert, ohlc=ohlc)
            except Exception:
                png = None
            if png:
                caption = text
                if len(caption) > 1024:
                    caption = self.fmt_signal(
                        alert, entry_status=entry_status, intel=intel,
                        evidence_block=False, bundle=bundle)
                if len(caption) <= 1024:
                    sent = await self._send_photo(png, caption, reply_markup=kb)
                    if sent:
                        return sent
        return await self._send(text, reply_markup=kb, message_effect_id=effect)

    async def paper_entry(self, pos, wallet_usd):
        text = self.fmt_paper_entry(pos, wallet_usd)
        kb = self._build_paper_keyboard(pos)
        return await self._send(text, reply_markup=kb)

    async def paper_scale_out(
        self,
        pos,
        kind,
        qty,
        price,
        wallet_usd,
        sold_cum=None,
        realized_pnl=None,
    ):
        text = self.fmt_paper_scale_out(
            pos,
            kind,
            qty,
            price,
            wallet_usd,
            sold_cum=sold_cum,
            realized_pnl=realized_pnl,
        )
        kb = self._build_paper_keyboard(pos)
        return await self._send(text, reply_markup=kb)

    async def paper_exit(self, pos, wallet_usd):
        text = self.fmt_paper_exit(pos, wallet_usd)
        kb = self._build_paper_keyboard(pos)
        return await self._send(text, reply_markup=kb)

    def fmt_alert_list(self, alerts, since, until, max_items=30):
        alerts = list(alerts or [])
        try:
            max_items = max(1, int(max_items or 30))
        except (TypeError, ValueError):
            max_items = 30

        window = fmt_duration(float(until or 0) - float(since or 0))
        since_text = _ts_hm(since)
        until_text = _ts_hm(until)
        total = len(alerts)

        header = (
            f"{TAG} 📋 <b>ALERT LIST</b> — LAST {window.upper()}\n"
            f"window {since_text} → {until_text} · sent <b>{total}</b>"
        )

        if not alerts:
            return (
                f"{header}\n\n"
                "No Lattice ENTRY SIGNAL alerts in this window."
            )

        shown = alerts[-max_items:]
        if total > max_items:
            shown_note = f"\n<i>Showing latest {len(shown)} of {total}.</i>"
        else:
            shown_note = ""

        lines = []
        for rec in shown:
            token = str(rec.get("token") or rec.get("token_address") or "")
            symbol = _esc((rec.get("symbol") or "?")).upper()
            chain = _esc((rec.get("chain") or "solana")).lower()
            ts_text = _ts_hm(rec.get("ts"))
            price = _fmt_price(rec.get("entry_price"))
            try:
                conviction = fmt_pct(
                    float(rec.get("conviction") or 0),
                    signed=False,
                    decimals=0,
                )
            except (TypeError, ValueError):
                conviction = "0%"

            if token:
                token_html = _esc(token)
                symbol_text = f"<b>${symbol}</b>"
                links_line = (
                    f"\n📈 <a href=\"https://dexscreener.com/{quote(chain, safe='')}/{quote(token, safe='')}\">"
                    f"DexScreener</a> · "
                    f"<a href=\"{gmgn_url(token)}\">GMGN</a> · "
                    f"<a href=\"https://birdeye.so/token/{quote(token, safe='')}?chain=solana\">"
                    f"Birdeye</a> · "
                    f"<a href=\"{jup_url(token)}\">Jupiter</a>"
                )
                ca_line = f"\n<code>{token_html}</code>"
            else:
                symbol_text = f"<b>${symbol}</b>"
                links_line = ""
                ca_line = ""

            lines.append(
                f"{ts_text}  {symbol_text} · "
                f"P(≥2x) {conviction} · {price}{links_line}{ca_line}"
            )

        return (
            f"{header}\n\n"
            + "\n\n".join(lines)
            + shown_note
        )

    async def alert_list(self, alerts, since, until, max_items=30):
        return await self._send(
            self.fmt_alert_list(
                alerts,
                since,
                until,
                max_items=max_items,
            )
        )

    def fmt_methodology(self, *, poll_s=30, min_conviction=0.18, max_hold_h=None, paper=True):
        # Built from live config + the actual exit engine so the description
        # matches what the bot does (no hardcoded stops/ladder that drift).
        import config
        from discovery.manager import PositionManager
        if max_hold_h is None:
            max_hold_h = float(getattr(config, "LATTICE_MAX_HOLD_H", 48.0) or 0.0)
        m = PositionManager()
        min_sub = float(getattr(config, "LATTICE_MIN_ENTRY_LATTICE", 0.0) or 0.0)
        pc1h_cap = float(getattr(config, "LATTICE_MAX_ENTRY_PRICE_CHANGE_1H", 0.0) or 0.0)
        paper_buy_gate = bool(getattr(config, "LATTICE_PAPER_BUY_GATE_ENABLED", True))
        paper_buy_min_breadth = float(
            getattr(config, "LATTICE_PAPER_BUY_MIN_BREADTH", 0.35) or 0.0
        )
        paper_buy_min_pc5 = float(
            getattr(config, "LATTICE_PAPER_BUY_MIN_PRICE_CHANGE_5M", 4.0) or 0.0
        )
        paper_buy_max_pc5 = float(
            getattr(config, "LATTICE_PAPER_BUY_MAX_PRICE_CHANGE_5M", 20.0) or 0.0
        )
        size = float(getattr(config, "POSITION_POSITION_SIZE_USD", 20) or 20)
        atr_enabled = bool(getattr(config, "POSITION_ATR_STOP_ENABLED", True))
        atr_k = float(getattr(config, "POSITION_ATR_STOP_K", 5.0) or 5.0)
        atr_min = float(getattr(config, "POSITION_ATR_STOP_MIN_PCT", 0.12) or 0.12)
        atr_max = float(getattr(config, "POSITION_ATR_STOP_MAX_PCT", 0.70) or 0.70)
        flat_stop = float(
            getattr(config, "POSITION_INITIAL_STOP_LOSS_PCT", m.initial_stop_pct)
            or m.initial_stop_pct
        )
        initial_stop_line = (
            f"• Initial stop: ATR-scaled before any scale-out "
            f"(K={atr_k:g}, clamp {atr_min * 100:.0f}%–{atr_max * 100:.0f}%), "
            f"flat fallback −{flat_stop * 100:.0f}%.\n"
            if atr_enabled else
            f"• Initial stop: flat −{flat_stop * 100:.0f}% before any scale-out.\n"
        )
        tp_parts = []
        previous_target = 0.0
        fibs = list(getattr(m, "q3_fib_extensions", ()) or ())
        if m.tail_tp_enabled():
            tp_parts.append(
                f"{m.tail_cost_recovery_multiple:g}x→recover "
                f"{m.tail_cost_recovery_pct * 100:.0f}% cost "
                f"(cap {m.tail_cost_recovery_max_sell_pct * 100:.0f}% sold)"
            )
            tp_parts.extend(
                f"{lvl:g}x→sell {fraction * 100:.0f}% of remaining"
                for lvl, fraction in m.tail_scale_out_tiers
            )
        else:
            for idx, (lvl, target) in enumerate(m.ladder):
                sell_pct = max(target - previous_target, 0.0) * 100
                if m.tp_mode == "q3":
                    label = (
                        f"target {idx + 1} ({fibs[idx]:g} fib)"
                        if idx < len(fibs) else f"target {idx + 1}"
                    )
                    tp_parts.append(
                        f"{label}→sell {sell_pct:.0f}% (cum {target * 100:.0f}%)"
                    )
                else:
                    tp_parts.append(
                        f"{lvl:g}x→sell {sell_pct:.0f}% (cum {target * 100:.0f}%)"
                    )
                previous_target = target
        tp_plan = ", ".join(tp_parts)
        tail_line = ""
        if m.tail_tp_enabled():
            tail_line = (
                f"• Tail scale-out: cost recovery is sized from remaining "
                f"unrecovered cost at the fill price; later tiers sell a "
                f"fraction of the then-remaining bag.\n"
            )
        q3_line = ""
        if m.tp_mode == "q3":
            q3_line = (
                f"• Q3 target prices: fib extensions snapped upward to nearby "
                f"volume-profile nodes; targets below {m.q3_min_target_mult:g}x "
                f"are ignored.\n"
            )
        floor_map = ", ".join(
            f"{trigger:g}x→stop {floor:g}x"
            for trigger, floor in m.scale_stop_floors
        )
        moonbag_floor_line = ""
        if m.moonbag_step_floors_enabled:
            start = m.moonbag_step_trigger_mult
            interval = m.moonbag_step_interval_mult
            lag = m.moonbag_step_floor_lag_mult
            moonbag_floor_line = (
                f"• Moonbag step floors: at {start:g}x stop moves to "
                f"{max(start - lag, 0):g}x; then every +{interval:g}x, "
                f"the stop trails {lag:g}x behind.\n"
            )
        if m.tail_tp_enabled():
            moonbag_pct = m.tail_estimated_moonbag_pct() * 100
        else:
            moonbag_pct = (
                max(0.0, 1.0 - (m.ladder[-1][1] if m.ladder else 0.0)) * 100
            )
        sub_line = (
            f"• Lattice floor ≥ {min_sub:.2f}: buy/sell flow, liquidity health and "
            f"price structure must agree.\n" if min_sub > 0 else ""
        )
        cap_line = f" Skips entries already up &gt;{pc1h_cap:.0f}% in 1h." if pc1h_cap else ""
        paper_buy_line = (
            f"• Paper buy gate: alerts still send, but simulated buys require "
            f"breadth ≥ {paper_buy_min_breadth:.2f} and "
            f"{paper_buy_min_pc5:g}–{paper_buy_max_pc5:g}% 5m price move.\n"
            if paper and paper_buy_gate else ""
        )
        q3_floor_line = (
            f"• Q3 floor profile: break-even/scale floor stays active; "
            f"ATR trail {'on' if m.q3_atr_trail_enabled else 'off'}"
            f"{f' (K={m.q3_atr_trail_k:g})' if m.q3_atr_trail_enabled else ''}; "
            f"VP floor buffer {m.q3_vp_floor_buffer:g}.\n"
            if m.tp_mode == "q3" else ""
        )
        partial_h = m.max_hold_partial_runner_s / 3600 if m.max_hold_partial_runner_s else 0
        partial_line = (
            f" Partial runners that touched {m.max_hold_partial_runner_mult:g}x "
            f"and are still above entry get {partial_h:g}h."
            if m.max_hold_partial_runner_mult > 0 and partial_h > 0 else ""
        )
        strict = "off (it was cutting eventual runners early)" if not m.strict_enabled else "on"
        mode = "paper SIM" if paper else "live"
        tail = ("Paper SIM only — wallet and sizing are simulated, no real orders."
                if paper else "Live execution — real orders are placed.")
        return (
            f"{TAG} <b>How the Lattice 💎 bot trades</b> ({mode})\n\n"
            f"<b>DATA</b>: reads the live scanner's snapshot stream (scanner.db "
            f"<code>signal_snapshots</code>); it does not scan the chain itself. "
            f"Polls every {poll_s:.0f}s.\n\n"
            f"<b>ENTRY</b> — all must pass, per snapshot:\n"
            f"• Universe: price up &gt;2% in 5m with real 1h volume.\n"
            f"• Anti-fakeness vetoes: parabolic blow-off (&gt;150% 5m), thin-book pumps "
            f"(vol/liq&gt;4), liquidity draining while price rises, manufactured flow.\n"
            f"{sub_line}"
            f"• Conviction ≥ {min_conviction:.2f}: calibrated P(≥2x).{cap_line}\n"
            f"• Breadth gate drops clearly manufactured moves.\n\n"
            f"<b>POSITION MANAGEMENT</b> — {mode}, ${size:.0f}/entry:\n"
            f"{paper_buy_line}"
            f"• Fills up to +5% above the signal price.\n"
            f"• Take profit mode: {m.tp_mode.upper()} — {tp_plan}.\n"
            f"{tail_line}"
            f"{q3_line}"
            f"• Stop-floor ratchet after scales: {floor_map}.\n"
            f"{q3_floor_line}"
            f"{moonbag_floor_line}"
            f"• Remaining {moonbag_pct:.0f}% after the final scale is the "
            f"moonbag.\n"
            f"• Max hold {max_hold_h:.0f}h for stagnant names.{partial_line} "
            f"Runners ≥ {m.max_hold_exempt_mult:g}x are exempt.\n\n"
            f"<b>EXITS</b> — checked on every new snapshot:\n"
            f"{initial_stop_line}"
            f"• Scale stop floors: remaining bag closes if price falls back through "
            f"the active floor.\n"
            f"• Break-even floor before scale: {'on' if m.break_even_enabled else 'off'}.\n"
            f"• No-progress time stop: {'on' if m.no_progress_enabled else 'off'}.\n"
            f"• Liquidity-collapse exit: liquidity −{m.liquidity_from_entry_pct * 100:.0f}% from "
            f"entry or −{m.liquidity_from_peak_pct * 100:.0f}% from peak.\n"
            f"• Sell-only-flow exit on heavy one-sided selling near entry.\n"
            f"• Strict early exit: {strict}.\n\n"
            f"{tail}"
        )

    async def methodology(self, **kw):
        return await self._send(self.fmt_methodology(**kw))

    async def text(self, msg):
        return await self._send(f"{TAG} {msg}")
