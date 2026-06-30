import asyncio
import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta
from html import escape
from pathlib import Path

import aiohttp

from config import (
    TELEGRAM_AGENT_ADMIN_USER_IDS,
    TELEGRAM_AGENT_ALERT_OHLCV_MAX_PAGES,
    TELEGRAM_AGENT_ALERT_OHLCV_REFRESH_ENABLED,
    TELEGRAM_AGENT_ALERT_REFRESH_ENABLED,
    TELEGRAM_AGENT_ALERT_REFRESH_MAX_TOKENS,
    TELEGRAM_AGENT_ALLOWED_CHAT_IDS,
    TELEGRAM_AGENT_PUBLIC_CHAT_IDS,
    TELEGRAM_AGENT_PUBLIC_COMMANDS,
    TELEGRAM_AGENT_ENABLED,
    TELEGRAM_AGENT_LIVE_ACTIONS_ENABLED,
    TELEGRAM_AGENT_MAX_REPORT_LINES,
    TELEGRAM_AGENT_POLL_INTERVAL_SECONDS,
    TELEGRAM_AGENT_POLL_TIMEOUT_SECONDS,
    TELEGRAM_AGENT_RESTART_ENABLED,
    TELEGRAM_AGENT_RESTART_STATUS_PATH,
    TELEGRAM_AGENT_WRITE_ACTIONS_ENABLED,
    TELEGRAM_BOT_TOKEN
)
from trading.alert_report import refresh_alerts_with_ohlcv
from trading.live_prices import fetch_live_prices
from trading.execution import LiveExecutionManager
from trading.position_report import refresh_trade_prices
from utils.tg_format import fmt_token_price, fmt_usd, jup_url, pnl_emoji
from trading.post_alert_outcomes import (
    group_by_route,
    load_outcomes,
    parse_window,
    summarize_rows,
    window_label
)


ROOT = Path(__file__).resolve().parent.parent


def html(value, quote=True):

    return escape(
        str(value or ""),
        quote=quote
    )


def safe_float(value, default=0):

    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def money(value):

    return f"${safe_float(value):,.2f}"


def pct(value):

    return f"{safe_float(value):.1%}"


def pct_points(value):

    return f"{safe_float(value):.2f}%"


def token_amount(value):

    number = safe_float(value)
    abs_number = abs(number)

    if abs_number >= 100000:
        places = 2
    elif abs_number >= 100:
        places = 4
    else:
        places = 9

    text = f"{number:,.{places}f}"
    return text.rstrip("0").rstrip(".") or "0"


def message_chunks(lines, max_chars=3600):

    chunks = []
    current = []
    current_len = 0

    for line in lines:
        line = str(line)
        line_len = len(line) + (1 if current else 0)

        if current and current_len + line_len > max_chars:
            chunks.append("\n".join(current))
            current = [line]
            current_len = len(line)
        else:
            current.append(line)
            current_len += line_len

    if current:
        chunks.append("\n".join(current))

    return chunks or [""]


def short_address(value):

    text = str(value or "")

    if len(text) <= 12:
        return text

    return f"{text[:6]}...{text[-6:]}"


def local_day_window(now=None):

    current = datetime.fromtimestamp(
        now or time.time()
    ).astimezone()
    start = current.replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0
    )
    end = start + timedelta(days=1)
    return start.timestamp(), end.timestamp()


class TelegramCommandAgent:

    # Commands usable by any member of a privileged OR public chat (no admin).
    PUBLIC_COMMANDS = (
        "/og", "/help", "/agent",
    )
    # Admin-only commands (require authorized()).
    ADMIN_COMMANDS = (
        "/status", "/paper", "/holdings", "/upnl", "/positions", "/outcomes",
        "/routes", "/discovery", "/performance", "/pnl", "/restart", "/trades",
        "/history", "/ai", "/regime", "/tune", "/why", "/bundle",
        "/badwallets", "/badactors", "/alerts",
    )
    # Everything the agent actually answers to. Anything else is ignored
    # silently — the bot must never reply to a command it does not implement.
    KNOWN_COMMANDS = frozenset(("/whoami",) + PUBLIC_COMMANDS + ADMIN_COMMANDS)

    def __init__(
        self,
        *,
        telegram,
        position_engine,
        scanner_storage,
        refresh_position_sol_usd,
        live_execution=None
    ):

        self.telegram = telegram
        self.position_engine = position_engine
        self.scanner_storage = scanner_storage
        self.refresh_position_sol_usd = refresh_position_sol_usd
        self.live_execution = live_execution or LiveExecutionManager()
        self.offset = None

    def enabled(self):

        return bool(
            TELEGRAM_AGENT_ENABLED
            and TELEGRAM_BOT_TOKEN
        )

    def allowed_chat_ids(self):

        return {
            str(chat_id).strip()
            for chat_id in TELEGRAM_AGENT_ALLOWED_CHAT_IDS
            if str(chat_id).strip()
        }

    def admin_user_ids(self):

        return {
            str(user_id).strip()
            for user_id in TELEGRAM_AGENT_ADMIN_USER_IDS
            if str(user_id).strip()
        }

    def public_chat_ids(self):

        return {
            str(chat_id).strip()
            for chat_id in TELEGRAM_AGENT_PUBLIC_CHAT_IDS
            if str(chat_id).strip()
        }

    def public_commands(self):
        configured = {
            str(command).strip().lower()
            for command in TELEGRAM_AGENT_PUBLIC_COMMANDS
            if str(command).strip()
        }
        allowed = set(self.PUBLIC_COMMANDS)
        return configured & allowed if configured else allowed

    def public_command_allowed(self, message):
        """True if a PUBLIC command (e.g. /og) may run here: any member of a
        privileged (allowlisted) OR public chat, no admin required. Other
        commands still go through authorized()."""
        chat_id = str((message.get("chat") or {}).get("id", ""))
        if not chat_id:
            return False
        return chat_id in (self.allowed_chat_ids() | self.public_chat_ids())

    def authorized(
        self,
        message
    ):

        chat_id = str(
            (message.get("chat") or {}).get("id", "")
        )
        user_id = str(
            (message.get("from") or {}).get("id", "")
        )
        sender_chat_id = str(
            (message.get("sender_chat") or {}).get("id", "")
        )
        allowed_chats = self.allowed_chat_ids()
        allowed_users = self.admin_user_ids()

        # An admin user is authorized in ANY chat the bot is in — not only the
        # allowlisted main chat. Telegram authenticates `from.id` and it is
        # globally unique, so identifying the admin by user id is not spoofable;
        # the chat allowlist is not needed to protect a known admin. This lets
        # privileged commands run from any group the admin is a member of.
        if user_id and allowed_users and user_id in allowed_users:
            return True

        # Anonymous admin posts (sent "as the group/channel") carry no
        # from-user (sender_chat == chat), so the specific user can't be
        # verified — fall back to trusting posting rights, but ONLY inside an
        # allowlisted chat.
        if (not user_id and sender_chat_id and sender_chat_id == chat_id
                and chat_id in allowed_chats):
            return True

        return False

    async def run(self):

        if not self.enabled():
            return

        print("Telegram command agent started")
        await self.send_restart_complete_if_pending()

        timeout = aiohttp.ClientTimeout(
            total=TELEGRAM_AGENT_POLL_TIMEOUT_SECONDS + 10
        )

        async with aiohttp.ClientSession(timeout=timeout) as session:
            while True:
                try:
                    updates = await self.fetch_updates(session)

                    for update in updates:
                        self.offset = update.get("update_id", 0) + 1
                        try:
                            await self.handle_update(update)
                        except Exception as update_exc:
                            print(f"Error handling update {update.get('update_id')}: {update_exc!r}")

                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    # repr: a bare TimeoutError stringifies to "" which made
                    # 210 historical log lines undiagnosable.
                    print(f"Telegram command agent error: {exc!r}")

                await asyncio.sleep(
                    max(TELEGRAM_AGENT_POLL_INTERVAL_SECONDS, 0.25)
                )

    async def fetch_updates(
        self,
        session
    ):

        params = {
            "timeout": TELEGRAM_AGENT_POLL_TIMEOUT_SECONDS,
            # channel_post variants included: commands typed in an allowlisted
            # CHANNEL arrive as channel_post, not message — without these the
            # agent never even receives them (no reply, no error).
            "allowed_updates": json.dumps(
                [
                    "message",
                    "edited_message",
                    "channel_post",
                    "edited_channel_post"
                ]
            )
        }

        if self.offset is not None:
            params["offset"] = self.offset

        async with session.get(
            f"{self.telegram.base_url}/getUpdates",
            params=params
        ) as response:
            data = await response.json(content_type=None)

        if not data.get("ok"):
            raise RuntimeError(
                data.get("description", "getUpdates_failed")
            )

        return data.get("result") or []

    async def handle_update(
        self,
        update
    ):

        message = (
            update.get("message")
            or update.get("edited_message")
            or update.get("channel_post")
            or update.get("edited_channel_post")
            or {}
        )
        text = str(message.get("text") or "").strip()

        if not text.startswith("/"):
            return

        command = text.split()[0].split("@", 1)[0].lower()

        # Ignore anything that isn't a command we implement — no reply at all
        # (so bogus slash-text like /th in a public group gets silence, not a
        # "BLOCKED"/"Unknown command" nag). Checked before the auth gate.
        if command not in self.KNOWN_COMMANDS:
            return

        if command == "/whoami":
            await self.reply(
                message,
                self.whoami_message(message)
            )
            return

        # Public commands — usable by ANY member of a privileged OR public chat,
        # no admin required (read-only scanner/analysis + help). Handled before
        # the admin gate; every other command stays admin-only.
        if command in self.public_commands():
            if not self.public_command_allowed(message):
                await self.reply(message, self.unauthorized_message(message))
                return
            body = text[len(text.split()[0]):].strip()
            if command in ("/help", "/agent"):
                # admins get the full syntax list; public gets the summary
                await self.reply(
                    message,
                    self.help_message() if self.authorized(message)
                    else self.public_help_message()
                )
            elif command == "/og":
                await self.reply(message, await self.og_token_message(body))
            elif command in ("/badwallets", "/badactors"):
                await self.reply(message, await self.bad_wallets_message(body))
            elif command == "/alerts":
                await self.reply(message, await self.alert_report_message(body))
            return

        if not self.authorized(message):
            await self.reply(
                message,
                self.unauthorized_message(message)
            )
            return

        body = text[len(text.split()[0]):].strip()

        if command in ("/status", "/paper", "/holdings", "/upnl"):
            # one ignition-book view: summary + per-position detail
            await self.reply(message, await self.paper_holdings_message())
        elif command == "/positions":
            for part in await self.live_positions_messages():
                await self.reply(message, part)
        elif command in ("/outcomes", "/routes"):
            await self.reply(message, self.alert_outcomes_message(body))
        elif command == "/alerts":
            await self.reply(message, await self.alert_report_message(body))
        elif command in ("/badwallets", "/badactors"):
            await self.reply(message, await self.bad_wallets_message(body))
        elif command == "/discovery":
            await self.reply(message, await self.discovery_quality_message(body))
        elif command in ("/performance", "/pnl"):
            await self.reply(
                message,
                await self.performance_message(body)
            )
        elif command == "/restart":
            await self.restart_main(message, body)
        elif command in ("/trades", "/history"):
            await self.reply(message, await self.trade_history_message(body))
        elif command in ("/ai", "/regime", "/tune", "/why"):
            await self.reply(
                message,
                await self.lattice_ai_message(command, body)
            )
        elif command == "/bundle":
            await self.reply(message, await self.bundle_message(body))
        else:
            await self.reply(
                message,
                "Unknown command. Use /help."
            )

    async def reply(
        self,
        message,
        text
    ):

        chat_id = (message.get("chat") or {}).get("id")

        if not chat_id:
            return 0

        return await self.telegram.send_message(
            {
                "chat_ids": [str(chat_id)],
                "chat_id": str(chat_id),
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
                "allow_sending_without_reply": True,
                "reply_to_message_id": message.get("message_id")
            },
            "Telegram agent reply sent"
        )

    async def bundle_message(self, body):

        addr = (body or "").strip().split()[0] if (body or "").strip() else ""

        if not addr:
            return "Usage: <code>/bundle &lt;token_address&gt;</code>"

        from sources.gmgn import gmgn_client
        from filters import bundle

        holders = []
        gmgn_status = "disabled"
        if gmgn_client.enabled():
            try:
                holders = await gmgn_client.top_holders(
                    addr,
                    chain="sol",
                    limit=100
                )
                gmgn_status = "ok" if holders else "no_data"
            except Exception as exc:
                gmgn_status = f"error:{type(exc).__name__}"

        if holders:
            s = bundle.analyze(holders)
            lines = [
                f"<b>[ BUNDLE SCAN · GMGN ]</b> <code>{html(addr[:14])}</code>",
                (
                    f"verdict <b>{s['verdict']}</b> · holders {s['holders_seen']} "
                    f"(pools excl {s['pools_excluded']})"
                ),
                (
                    f"naive top1 <b>{s['naive_top1']:.1f}%</b> · "
                    f"top10 <b>{s['naive_top10']:.1f}%</b>"
                ),
                (
                    f"effective top holder <b>{s['effective_top']:.1f}%</b> "
                    f"(+{s['obfuscation_gap']:.1f}pp hidden via splitting)"
                ),
                (
                    f"time clusters {s['n_time_clusters']} · "
                    f"bundler-tagged {s['bundler_tagged']} · "
                    f"transfer/dev supply {s['nonbuy_pct']:.1f}%"
                ),
            ]
            for c in s["clusters"][:3]:
                lines.append(
                    f"• {c['n']} wallets/{c['span_s']:.0f}s → "
                    f"<b>{c['combined_pct']:.1f}%</b> "
                    f"(amt-sim {c['similar_n']}/{c['n']})"
                )
            return "\n".join(lines)

        from sources import solanatracker

        if not solanatracker.enabled():
            return (
                f"Bundle data unavailable for <code>{html(addr[:16])}</code>: "
                f"GMGN {html(gmgn_status)}; Solana Tracker disabled."
            )

        evidence = await solanatracker.fetch_risk(addr)
        tracker_status = str(evidence.get("status") or "error")
        if tracker_status != "ok":
            return (
                f"Bundle data unavailable for <code>{html(addr[:16])}</code>: "
                f"GMGN {html(gmgn_status)}; Solana Tracker "
                f"{html(tracker_status)}."
            )

        level = str(evidence.get("risk_level") or "unknown").upper()
        score = evidence.get("provider_score")
        score_text = f" · score {safe_float(score):.0f}" if score is not None else ""
        lines = [
            (
                "<b>[ BUNDLE SCAN · SOLANA TRACKER FALLBACK ]</b> "
                f"<code>{html(addr[:14])}</code>"
            ),
            f"verdict <b>{html(level)}</b>{score_text}",
            (
                f"current bundle <b>{safe_float(evidence.get('current_bundle_pct')):.1f}%</b> "
                f"· wallets {int(safe_float(evidence.get('bundle_wallet_count')))}"
            ),
            (
                f"insiders <b>{safe_float(evidence.get('insider_pct')):.1f}%</b> · "
                f"snipers <b>{safe_float(evidence.get('sniper_pct')):.1f}%</b> · "
                f"dev <b>{safe_float(evidence.get('dev_pct')):.1f}%</b>"
            ),
            (
                f"top10 <b>{safe_float(evidence.get('top10_pct')):.1f}%</b> · "
                f"rugged <b>{'YES' if evidence.get('rugged') else 'no'}</b>"
            ),
            f"fallback reason: GMGN {html(gmgn_status)}",
        ]
        for wallet in (evidence.get("bundle_wallets") or [])[:3]:
            initial = wallet.get("initial_percentage")
            initial_text = (
                f" · initial {safe_float(initial):.1f}%"
                if initial is not None else ""
            )
            lines.append(
                f"• <code>{html(short_address(wallet.get('address')))}</code> "
                f"{safe_float(wallet.get('percentage')):.1f}%{initial_text}"
            )
        return "\n".join(lines)

    @staticmethod
    def _og_age(seconds):
        """Compact age label from an age in seconds."""
        days = max(0.0, seconds) / 86400.0
        if days < 1:
            return f"{days * 24:.0f}h"
        if days < 60:
            return f"{days:.0f}d"
        if days < 365:
            return f"{days / 30:.0f}mo"
        return f"{days / 365:.1f}y"

    async def og_token_message(self, body):
        """`/og <token_address> [N]` — the N OLDEST tokens that share the SAME
        ticker as the queried token (the 'OG' of that ticker). On Solana this
        ranks by on-chain mint-transaction time via the ticker-lineage engine
        (multi-source discovery + genesis walk), which is far more reliable than
        DEX pool age; other chains fall back to DexScreener pair-creation time.
        Default N=10. Public command."""
        parts = (body or "").strip().split()
        addr = parts[0] if parts else ""

        if not addr:
            return ("Usage: <code>/og &lt;token_address&gt; [N]</code> "
                    "(N oldest tokens sharing this ticker, default 10)")

        n = 10
        if len(parts) > 1:
            try:
                n = max(1, min(50, int(parts[1])))
            except ValueError:
                return ("N must be a number. Usage: "
                        "<code>/og &lt;token_address&gt; [N]</code>")

        from sources.dexscreener import DexScreenerClient

        client = DexScreenerClient()
        try:
            await client.start()
            # Resolve the queried token's ticker + chain from DexScreener.
            seed = await client.fetch_search_pairs(addr)
            symbol = ""
            chain = ""
            for pair in seed:
                base = pair.get("baseToken") or {}
                if str(base.get("address", "")).lower() == addr.lower():
                    symbol = str(base.get("symbol") or "").strip()
                    chain = str(pair.get("chainId") or "").strip().lower()
                    break
            if not symbol and seed:
                top = seed[0]
                symbol = str(
                    (top.get("baseToken") or {}).get("symbol") or ""
                ).strip()
                chain = str(top.get("chainId") or "").strip().lower()
            if not symbol:
                return (f"Couldn't resolve a ticker for "
                        f"<code>{html(addr[:16])}</code> on DexScreener.")

            # Solana: rank by on-chain mint time via the lineage engine. Fall
            # back to the DexScreener pair-age scan for other chains, or if the
            # engine surfaces no mint-dated candidates.
            if chain in ("", "solana"):
                text = await self._og_lineage_message(client, addr, symbol, n)
                if text:
                    return text
            return await self._og_dexscreener_message(client, addr, symbol, n)
        except Exception as exc:
            return f"OG scan error: {html(str(exc))}"
        finally:
            await client.close()

    async def _og_lineage_message(self, client, addr, symbol, n):
        """OG ranking via the on-chain ticker-lineage engine (Solana). Returns
        an empty string when no mint-dated candidates are found so the caller
        can fall back to the DexScreener scan."""
        from sources.token_lineage import (
            get_cached_ticker_lineage,
            resolve_focus_lineage_record,
            merge_ranked_record,
            find_record_index,
            lineage_confidence,
            normalize_ticker,
        )

        ticker = normalize_ticker(symbol)
        ranked, total_found = await get_cached_ticker_lineage(client, ticker)

        # Make sure the queried token itself is represented and markable, even
        # if discovery missed it (e.g. dead/illiquid mint).
        if find_record_index(ranked, addr) is None:
            focus = await resolve_focus_lineage_record(client, ticker, addr)
            if focus:
                ranked, _ = merge_ranked_record(ranked, focus)

        ranked = [r for r in ranked if safe_float(r.get("mint_time")) > 0]
        if not ranked:
            return ""

        ranked = ranked[:n]
        now = time.time()

        lines = [
            f"💎 <b>OG of ${html(symbol)}</b> — {len(ranked)} oldest "
            f"(of {total_found} found, by on-chain mint time)",
            "",
        ]
        for i, rec in enumerate(ranked, 1):
            token_addr = str(rec.get("address") or "")
            mint_time = safe_float(rec.get("mint_time"))
            # Full CA in a <code> span: tap-to-copy on Telegram.
            link = f"<code>{html(token_addr)}</code>"
            conf = html(lineage_confidence(rec).replace("_", " "))
            mc = safe_float(rec.get("market_cap"))
            mc_text = f" · mc {fmt_usd(mc)}" if mc > 0 else ""
            mark = " ⬅️ queried" if token_addr.lower() == addr.lower() else ""
            crown = " 👑" if i == 1 else ""
            jup = f' · <a href="{jup_url(token_addr)}">🪐 Jup</a>'
            lines.append(
                f"{i}.{crown} {link} · {self._og_age(now - mint_time)} · "
                f"{conf}{mc_text}{mark}{jup}"
            )
        return "\n".join(lines)

    async def _og_dexscreener_message(self, client, addr, symbol, n):
        """Fallback OG ranking by DexScreener pair-creation time. Used for
        non-Solana chains and when the lineage engine finds nothing."""
        matches = await client.fetch_search_pairs(symbol)

        oldest = {}
        for pair in matches:
            base = pair.get("baseToken") or {}
            if str(base.get("symbol") or "").strip().lower() != symbol.lower():
                continue
            token_addr = str(base.get("address") or "")
            created = safe_float(pair.get("pairCreatedAt"))   # ms
            if not token_addr or created <= 0:
                continue
            liq = safe_float((pair.get("liquidity") or {}).get("usd"))
            fdv = safe_float(pair.get("fdv") or pair.get("marketCap"))
            rec = oldest.get(token_addr)
            if rec is None:
                oldest[token_addr] = {
                    "created": created,
                    "chain": pair.get("chainId", ""),
                    "liq": liq,
                    "fdv": fdv,
                    "best_liq": liq,
                    "name": str(base.get("name") or ""),
                }
                continue
            # earliest pair sets the token's age; the most-liquid pair gives the
            # representative liq / fdv (the oldest pair can be a dead pool).
            if created < rec["created"]:
                rec["created"] = created
            if liq >= rec["best_liq"]:
                rec["best_liq"] = liq
                rec["liq"] = liq
                rec["fdv"] = fdv
                rec["chain"] = pair.get("chainId", rec["chain"])

        if not oldest:
            return (f"No other tokens found sharing ticker "
                    f"<b>${html(symbol)}</b>.")

        ranked = sorted(oldest.items(), key=lambda kv: kv[1]["created"])[:n]
        now_ms = time.time() * 1000.0

        lines = [
            f"💎 <b>OG of ${html(symbol)}</b> — {len(ranked)} oldest "
            f"(of {len(oldest)} found, by DEX pair age)",
            "",
        ]
        for i, (token_addr, info) in enumerate(ranked, 1):
            chain = info["chain"] or "solana"
            # Full CA in a <code> span: tap-to-copy on Telegram.
            link = f"<code>{html(token_addr)}</code>"
            liq = f" · liq {fmt_usd(info['liq'])}" if info["liq"] else ""
            fdv = f" · fdv {fmt_usd(info['fdv'])}" if info.get("fdv") else ""
            mark = " ⬅️ queried" if token_addr.lower() == addr.lower() else ""
            og = " 👑" if i == 1 else ""
            # Jupiter (Solana-only) trade link carrying our referral code
            jup = (f' · <a href="{jup_url(token_addr)}">🪐 Jup</a>'
                   if chain == "solana" else "")
            lines.append(
                f"{i}.{og} {link} · {self._og_age((now_ms - info['created']) / 1000.0)} · "
                f"{chain}{liq}{fdv}{mark}{jup}"
            )
        return "\n".join(lines)

    def whoami_message(
        self,
        message
    ):

        chat = message.get("chat") or {}
        user = message.get("from") or {}

        return (
            "<b>[ TELEGRAM AGENT ID CHECK ]</b>\n"
            f"Chat ID: <code>{html(chat.get('id'))}</code>\n"
            f"User ID: <code>{html(user.get('id'))}</code>\n"
            f"Username: <code>{html(user.get('username'))}</code>\n"
            f"Authorized: <b>{self.authorized(message)}</b>"
        )

    def unauthorized_message(
        self,
        message
    ):

        chat = message.get("chat") or {}
        user = message.get("from") or {}

        return (
            "<b>[ TELEGRAM AGENT BLOCKED ]</b>\n"
            "Set both allowlists before using commands.\n"
            f"Chat ID: <code>{html(chat.get('id'))}</code>\n"
            f"User ID: <code>{html(user.get('id'))}</code>"
        )

    def public_help_message(self):
        # Shown to non-admins / in public groups. Lists the public commands in
        # full, the privileged ones by name only, and points to admin chats.
        return (
            "<b>📋 [ LATTICE TELEGRAM AGENT ]</b>\n"
            "<b>Public commands</b> (work in public groups):\n"
            "/og &lt;addr&gt; [N] — oldest same-ticker (the OG, top N default 10)\n"
            "/badwallets [days] [tokens] — repeat bad wallets\n"
            "/alerts 📊 [today|7d|open] — alert performance\n"
            "/help, /whoami\n"
            "\n"
            "<b>Privileged</b> (admin only):\n"
            "/positions /trades /status /performance\n"
            "/bundle 👥 &lt;addr&gt; — de-obf bundle risk (top % + clusters)\n"
            "/discovery [days] — source/evidence quality report\n"
            "/outcomes /regime /tune /ai /why\n"
            "/restart\n"
            "\n"
            "<i>Full list &amp; syntax in private admin chats.</i>"
        )

    def help_message(self):
        # Full list with syntax — shown to admins in privileged chats.
        return (
            "<b>💎 [ LATTICE TELEGRAM AGENT ] — admin</b>\n"
            "<b>Public</b> (work in public groups):\n"
            "/og &lt;addr&gt; [N] - N oldest tokens sharing this ticker (default 10)\n"
            "/badwallets [days] [tokens] - repeat wallets across bad outcomes\n"
            "/alerts today|7d|open - alert performance\n"
            "/help, /whoami\n"
            "\n"
            "<b>Privileged</b> (admin only):\n"
            "/positions - open positions + fill status\n"
            "/trades [N] - trade history (last N, default 10)\n"
            "/status - paper book summary (aliases /paper /holdings /upnl)\n"
            "/performance 7|30|all - closed paper PnL\n"
            "/bundle &lt;addr&gt; - split-wallet cluster / hidden-concentration scan\n"
            "/discovery [days] - source quality + entry block attribution\n"
            "/outcomes 7d 1h - post-alert route outcomes\n"
            "/regime - meta/regime read\n"
            "/ai - AI advisor summary\n"
            "/tune - read-only tuning suggestions\n"
            "/why &lt;symbol|address&gt; - explain a token/position\n"
            "/restart - restart main.py"
        )

    async def discovery_quality_message(self, body):
        text = str(body or "").strip().lower()
        parts = text.split()
        days = 3.0

        if parts:
            raw = parts[0].rstrip("d")
            try:
                days = max(min(float(raw), 30.0), 0.25)
            except ValueError:
                days = 3.0

        try:
            from analysis.discovery_quality_report import (
                build_report,
                render_telegram,
            )

            loop = asyncio.get_running_loop()
            report = await loop.run_in_executor(
                None,
                lambda: build_report(days=days)
            )
            return render_telegram(
                report,
                limit=max(min(TELEGRAM_AGENT_MAX_REPORT_LINES, 12), 1),
            )
        except Exception as exc:
            return (
                "<b>[ DISCOVERY QUALITY ]</b>\n"
                "Report unavailable: "
                f"<code>{html(exc)}</code>"
            )

    async def bad_wallets_message(self, body):
        text = str(body or "").strip().lower()
        parts = text.split()
        days = 7.0
        max_tokens = 10

        if parts:
            try:
                days = max(min(float(parts[0].rstrip("d")), 30.0), 0.25)
            except ValueError:
                days = 7.0

        if len(parts) > 1:
            try:
                max_tokens = max(min(int(parts[1]), 30), 1)
            except ValueError:
                max_tokens = 10

        try:
            from analysis.bad_wallet_cluster_report import (
                build_report,
                render_telegram,
            )

            loop = asyncio.get_running_loop()
            report = await loop.run_in_executor(
                None,
                lambda: build_report(days=days, max_tokens=max_tokens)
            )
            return render_telegram(
                report,
                limit=max(min(TELEGRAM_AGENT_MAX_REPORT_LINES, 12), 1),
            )
        except Exception as exc:
            return (
                "<b>[ BAD WALLET CLUSTERS ]</b>\n"
                "Report unavailable: "
                f"<code>{html(exc)}</code>"
            )

    async def lattice_ai_message(self, command, body):
        try:
            from discovery.ai_advisor import LatticeAIAdvisor

            mode = {
                "/ai": "advisor",
                "/regime": "regime",
                "/tune": "tune",
                "/why": "why"
            }.get(command, "advisor")

            subject = body.strip() if command == "/why" else ""

            if command == "/why" and not subject:
                return (
                    "<b>[ LATTICE AI | WHY ]</b>\n"
                    "Usage: <code>/why SYMBOL</code> or "
                    "<code>/why token_address</code>"
                )

            return await LatticeAIAdvisor().telegram_report(
                mode=mode,
                subject=subject
            )
        except Exception as exc:
            return (
                "<b>[ LATTICE AI ]</b>\n"
                "Advisor unavailable: "
                f"<code>{html(exc)}</code>"
            )

    async def paper_holdings_message(self):

        await self.refresh_position_sol_usd()
        state = self.position_engine.load_state()
        closed = list(state.get("closed", []) or [])
        closed_pnl = sum(
            safe_float(trade.get("pnl_usd"))
            for trade in closed
        )
        open_refs = self.position_engine.open_position_refs()
        live_prices = {}
        live_refresh = None

        if open_refs:
            open_addresses = [
                address
                for address, _chain in open_refs
            ]
            chain_by_address = {
                address: chain
                for address, chain in open_refs
            }
            try:
                live_prices, live_refresh = await fetch_live_prices(
                    open_addresses,
                    chain_by_address=chain_by_address
                )
            except Exception as price_exc:
                print(f"Failed to fetch live prices in command: {price_exc!r}")
                live_prices = {}
                live_refresh = {
                    "enabled": True,
                    "attempted": len(open_addresses),
                    "refreshed": 0,
                    "error": f"Live prices unavailable: {type(price_exc).__name__}"
                }

        report = self.position_engine.build_status_report(
            time.time(),
            live_prices=live_prices,
            live_refresh=live_refresh
        )
        cash_sol = safe_float(state.get("cash_sol"), 0)
        sol_usd = self.position_engine.current_sol_usd()

        if not report:
            return (
                "<b>[ PAPER HOLDINGS ]</b>\n"
                "Held positions: <b>0</b>\n"
                f"Cash: {cash_sol:.2f} SOL "
                f"({money(cash_sol * sol_usd)})\n"
                f"Realized PnL: <b>{money(closed_pnl)}</b>"
            )

        positions = report.get("positions", [])
        open_realized_pnl = 0
        open_upnl = 0

        for position in positions:
            entry_notional = safe_float(
                position.get("entry_notional_usd")
            )
            realized_usd = safe_float(
                position.get("realized_usd")
            )
            scaled_out_pct = safe_float(
                position.get("scaled_out_pct")
            )
            equity_usd = safe_float(
                position.get("equity_usd")
            )
            remaining_value = max(
                equity_usd - realized_usd,
                0
            )
            remaining_cost = entry_notional * max(
                1 - scaled_out_pct,
                0
            )
            open_realized_pnl += (
                realized_usd
                - entry_notional * scaled_out_pct
            )
            open_upnl += remaining_value - remaining_cost

        open_pnl = safe_float(
            report.get("total_pnl_usd")
        )
        total_pnl = closed_pnl + open_pnl
        lines = [
            "<b>[ PAPER HOLDINGS ]</b>",
            (
                f"Held: <b>{report.get('open_count', 0)}</b> | "
                f"Cash: <b>{cash_sol:.2f} SOL</b> "
                f"({money(report.get('cash_usd'))})"
            ),
            (
                "Open equity: "
                f"<b>{money(report.get('total_equity_usd'))}</b> | "
                "Account: "
                f"<b>{money(report.get('total_account_equity_usd'))}</b>"
            ),
            (
                f"uPnL: <b>{money(open_upnl)}</b> | "
                f"Open PnL: <b>{money(open_pnl)}</b>"
            ),
            (
                f"Realized PnL: <b>{money(closed_pnl + open_realized_pnl)}</b> | "
                f"Total PnL: <b>{money(total_pnl)}</b>"
            )
        ]

        live_refresh = report.get("live_refresh") or {}

        if live_refresh.get("enabled"):
            lines.append(
                "Live prices: "
                f"<b>{live_refresh.get('refreshed', 0)}</b>/"
                f"{live_refresh.get('attempted', 0)}"
            )

            if live_refresh.get("error"):
                lines.append(
                    "Live error: "
                    f"{html(live_refresh.get('error'))}"
                )

        shown = positions

        if shown:
            lines.append("")
            lines.append("<b>Held positions</b>")

        for position in shown:
            entry_notional = safe_float(
                position.get("entry_notional_usd")
            )
            realized_usd = safe_float(
                position.get("realized_usd")
            )
            scaled_out_pct = safe_float(
                position.get("scaled_out_pct")
            )
            equity_usd = safe_float(
                position.get("equity_usd")
            )
            remaining_value = max(
                equity_usd - realized_usd,
                0
            )
            remaining_cost = entry_notional * max(
                1 - scaled_out_pct,
                0
            )
            position_upnl = remaining_value - remaining_cost
            live_marker = (
                " live"
                if position.get("live_refreshed")
                else ""
            )

            lines.extend([
                "",
                (
                    f"<b>${html(position.get('symbol', 'UNKNOWN'))}</b> "
                    f"{pnl_emoji(position_upnl)} "
                    f"<b>{safe_float(position.get('price_multiple')):.2f}x</b>"
                    f"{live_marker}"
                ),
                (
                    f"uPnL <b>{fmt_usd(position_upnl, signed=True)}</b> · "
                    f"PnL <b>{fmt_usd(position.get('pnl_usd'), signed=True)}</b> "
                    f"({pct(position.get('pnl_pct'))}) · "
                    f"{safe_float(position.get('entry_size_sol')):.2f} SOL"
                ),
                (
                    f"{fmt_token_price(safe_float(position.get('entry_price')))} → "
                    f"{fmt_token_price(safe_float(position.get('last_price')))} · "
                    f"<code>{html(short_address(position.get('address')))}</code>"
                )
            ])

        return "\n".join(lines)

    def load_live_runner_state(self):

        # Live trading runs in the separate discovery/live_runner process; read
        # its serialized state file for the live position book.
        path = ROOT / "discovery" / "live_state.json"

        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def load_live_runner_trades(self):

        path = ROOT / "discovery" / "trades.jsonl"
        trades = []

        try:
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()

                    if not line:
                        continue

                    try:
                        trades.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except OSError:
            return []

        return trades

    def live_position_last_price(self, position):

        last_price = safe_float(position.get("last_price"))

        if last_price > 0:
            return last_price

        recent = position.get("recent") or []

        if recent and isinstance(recent[-1], dict):
            return safe_float(recent[-1].get("price"))

        return safe_float(position.get("entry_price"))

    async def live_positions_messages(self):

        state = self.load_live_runner_state()

        if not state:
            return [(
                "<b>[ LIVE POSITIONS | LATTICE ]</b>\n"
                "No live_runner state "
                "(<code>discovery/live_state.json</code>)."
            )]

        open_pos = state.get("open_pos") or {}
        positions = (
            list(open_pos.values())
            if isinstance(open_pos, dict)
            else list(open_pos or [])
        )
        cash = safe_float(state.get("cash"))
        realized = safe_float(state.get("realized"))
        sol_usd = safe_float(state.get("sol_usd"))
        last_seen = safe_float(state.get("last_seen"))
        age = (
            f"{max(time.time() - last_seen, 0):.0f}s ago"
            if last_seen
            else "unknown"
        )

        # Real on-chain funder balance (NOT state['balance_sol'], which is the
        # live_runner's PAPER/sim balance BALANCE_SOL).
        try:
            from config import DEFINITIVE_FLASH_FUNDER_ADDRESS
            funder_sol = safe_float(
                await self.live_execution.solana_sol_balance(
                    DEFINITIVE_FLASH_FUNDER_ADDRESS
                )
            )
        except Exception:
            funder_sol = None

        open_value = 0.0
        open_pnl = 0.0
        for position in positions:
            last = self.live_position_last_price(position)
            remaining = safe_float(position.get("remaining"))
            open_value += last * remaining
            open_pnl += (
                last * remaining
                + safe_float(position.get("proceeds"))
                - safe_float(position.get("cost_usd"))
            )

        # Daily realized PnL: realized events since local midnight. The live
        # runner writes scale-out PnL here before the final close lands.
        lt = time.localtime()
        midnight = time.mktime(
            (lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1)
        )
        today_events = []
        for item in state.get("recent_realized") or []:
            try:
                event_ts = safe_float(item[0])
                event_pnl = safe_float(item[1])
            except (TypeError, IndexError):
                continue
            if event_ts >= midnight:
                today_events.append(event_pnl)

        if today_events:
            daily_realized = sum(today_events)
            daily_n = len(today_events)
            daily_wins = sum(1 for value in today_events if value > 0)
            daily_summary = f"{daily_wins}/{daily_n} positive events"
        else:
            today_trades = [
                t for t in self.load_live_runner_trades()
                if safe_float(t.get("exit_ts")) >= midnight
            ]
            daily_realized = sum(
                safe_float(t.get("pnl_usd")) for t in today_trades
            )
            daily_n = len(today_trades)
            daily_wins = sum(
                1 for t in today_trades if safe_float(t.get("pnl_usd")) > 0
            )
            daily_summary = (
                f"{daily_wins}/{daily_n} closes" if daily_n else "no closes"
            )

        lines = [
            "<b>[ LATTICE POSITIONS ]</b>",
            (
                f"open <b>{len(positions)}</b> "
                f"(val {money(open_value)}, uPnL {fmt_usd(open_pnl, signed=True)}) · "
                f"cash <b>{money(cash)}</b> · "
                f"realized <b>{fmt_usd(realized, signed=True)}</b>"
            ),
            (
                f"today <b>{fmt_usd(daily_realized, signed=True)}</b> "
                f"realized ({daily_summary})"
            ),
        ]

        if funder_sol is not None:
            lines.append(
                f"live wallet <b>{funder_sol:.3f} SOL</b> "
                f"({money(funder_sol * sol_usd)}) · updated {age}"
            )
        else:
            lines.append(f"updated {age}")

        positions = sorted(
            positions,
            key=lambda position: (
                self.live_position_last_price(position)
                * safe_float(position.get("remaining"))
            ),
            reverse=True
        )

        for index, position in enumerate(positions, start=1):
            entry = safe_float(position.get("entry_price"))
            last = self.live_position_last_price(position)
            multiple = last / entry if entry else 0
            remaining = safe_float(position.get("remaining"))
            value = last * remaining
            proceeds = safe_float(position.get("proceeds"))
            cost = safe_float(position.get("cost_usd"))
            pnl = value + proceeds - cost
            # split: remaining tranche carries cost remaining*entry; whatever
            # cost is not still on the book belongs to the scaled-out tranche.
            remaining_cost = remaining * entry
            upnl = value - remaining_cost
            realized_pnl = proceeds - max(cost - remaining_cost, 0)
            conviction = safe_float(position.get("conviction"))
            filled = safe_float(
                position.get("live_execution_entry_filled_target_amount")
            )

            if position.get("live_execution_entry_submitted") and filled > 0:
                live_status = f"live <b>FILLED</b> {token_amount(filled)} tok"
            elif position.get("live_execution_entry_reason"):
                reason = str(position.get("live_execution_entry_reason"))
                if len(reason) > 60:
                    reason = reason[:57] + "..."
                live_status = f"live: {html(reason)}"
            else:
                live_status = "paper"

            held = ""
            entry_ts = safe_float(position.get("entry_ts"))
            if entry_ts > 0:
                hours = max(time.time() - entry_ts, 0) / 3600
                # &lt; — a literal "<1h" reads as an HTML tag and makes
                # Telegram reject the whole message (can't parse entities).
                held = f" · {hours:.0f}h" if hours >= 1 else " · &lt;1h"

            stop_pct = safe_float(position.get("initial_stop_pct"))
            peak = safe_float(position.get("peak"))
            peak_mult = peak / entry if entry else multiple
            stop_line = f"peak <b>{peak_mult:.2f}x</b>"
            # The live engine ratchets a stop floor up as the position scales
            # (break-even arm, per-scale step floor, moonbag step floor -- all in
            # manager.py, stored as stop_floor_price). Once armed, that floor is
            # the stop that actually governs the exit: post-scale the entry-time
            # initial stop is no longer checked at all. Show the higher of the
            # two so a runner doesn't misread as "no protection" off a frozen
            # initial stop that sits far below price.
            initial_stop = entry * (1 - stop_pct) if stop_pct > 0 else 0
            floor_price = safe_float(position.get("stop_floor_price"))
            stop_price = max(initial_stop, floor_price)
            if stop_price > 0 and entry > 0 and last > 0:
                cushion = (last / stop_price - 1) * 100
                stop_mult = stop_price / entry
                if floor_price > 0 and floor_price >= initial_stop:
                    basis = str(
                        position.get("stop_floor_source")
                        or ("break_even" if position.get("break_even_armed")
                            else "floor")
                    )
                else:
                    basis = str(position.get("initial_stop_basis") or "flat")
                stop_line = (
                    f"🛑 {html(basis)} {fmt_token_price(stop_price)} "
                    f"({stop_mult:.2f}x · {cushion:+.0f}% cushion) · "
                    + stop_line
                )

            lines.extend([
                "",
                (
                    f"<b>{index}. ${html(position.get('symbol', 'UNKNOWN'))}</b> "
                    f"{pnl_emoji(pnl)} <b>{fmt_usd(pnl, signed=True)}</b> "
                    f"({multiple:.2f}x)"
                ),
                (
                    f"uPnL {fmt_usd(upnl, signed=True)} · "
                    f"realized {fmt_usd(realized_pnl, signed=True)} · "
                    f"val {money(value)}"
                ),
                (
                    f"conv {conviction * 100:.0f}%"
                    f" · {live_status}{held}"
                ),
                stop_line,
                (
                    f"{fmt_token_price(entry)} → {fmt_token_price(last)} · "
                    f"<code>{html(short_address(position.get('token')))}</code>"
                )
            ])

        return message_chunks(lines)

    async def live_positions_message(self):

        return "\n\n".join(await self.live_positions_messages())

    async def trade_history_message(self, body):

        trades = self.load_live_runner_trades()

        if not trades:
            return (
                "<b>[ TRADE HISTORY | LATTICE ]</b>\n"
                "No trades "
                "(<code>discovery/trades.jsonl</code>)."
            )

        try:
            limit = int(str(body or "").strip() or "10")
        except ValueError:
            limit = 10

        limit = min(
            max(limit, 1),
            max(TELEGRAM_AGENT_MAX_REPORT_LINES, 1)
        )
        total = len(trades)
        pnl_values = [safe_float(trade.get("pnl_usd")) for trade in trades]
        total_pnl = sum(pnl_values)
        wins = sum(1 for value in pnl_values if value > 0)
        losses = sum(1 for value in pnl_values if value < 0)
        win_rate = wins / total if total else 0
        recent = trades[-limit:]

        lines = [
            "<b>[ TRADE HISTORY | LATTICE ]</b>",
            (
                f"Trades: <b>{total}</b> | "
                f"W/L: {wins}/{losses} ({pct(win_rate)}) | "
                f"PnL: <b>{money(total_pnl)}</b>"
            )
        ]

        if pnl_values:
            lines.append(
                "Avg/Best/Worst: "
                f"{money(total_pnl / total)} / "
                f"{money(max(pnl_values))} / "
                f"{money(min(pnl_values))}"
            )

        # adaptive-stop A/B (initial_stop_basis) — surfaces the ATR-vs-flat test
        atr_t = [t for t in trades if t.get("initial_stop_basis") == "atr"]
        flat_t = [t for t in trades if t.get("initial_stop_basis") == "flat"]
        if atr_t or flat_t:
            def _avg(ts):
                vals = [safe_float(t.get("pnl_usd")) for t in ts]
                return sum(vals) / len(vals) if vals else 0.0
            lines.append(
                f"stop A/B — atr <b>{len(atr_t)}</b> avg {money(_avg(atr_t))} · "
                f"flat <b>{len(flat_t)}</b> avg {money(_avg(flat_t))}"
            )

        # exit-reason breakdown
        reason_pnl, reason_n = {}, {}
        for t in trades:
            r = str(t.get("reason") or "?")
            reason_pnl[r] = reason_pnl.get(r, 0.0) + safe_float(t.get("pnl_usd"))
            reason_n[r] = reason_n.get(r, 0) + 1
        top = sorted(reason_n.items(), key=lambda kv: kv[1], reverse=True)[:4]
        if top:
            lines.append("by reason: " + " · ".join(
                f"{html(r.replace('_', ' '))} {n} ({money(reason_pnl[r])})"
                for r, n in top))

        lines.append("")
        lines.append(f"<b>Last {len(recent)}</b>")

        for trade in reversed(recent):
            entry = safe_float(trade.get("entry_price"))
            exit_price = safe_float(trade.get("exit_price"))
            multiple = exit_price / entry if entry else 0
            exit_ts = safe_float(trade.get("exit_ts"))
            when = (
                datetime.fromtimestamp(exit_ts)
                .astimezone()
                .strftime("%m-%d %H:%M")
                if exit_ts
                else "?"
            )
            reason = str(trade.get("reason", "closed")).replace("_", " ")
            lines.append(
                f"{pnl_emoji(trade.get('pnl_usd'))} "
                f"${html(trade.get('symbol', 'UNKNOWN'))} "
                f"<b>{fmt_usd(trade.get('pnl_usd'), signed=True)}</b> "
                f"({multiple:.2f}x) {html(reason)} · {html(when)}"
            )

        return "\n".join(lines)

    async def alert_report_message(
        self,
        body
    ):

        now = time.time()
        body = (body or "today").strip().lower()
        open_only = body == "open"
        since = None
        until = None

        if body in ("", "today", "day"):
            since, until = local_day_window(now)
            label = "today"
        elif body in ("7", "7d", "week"):
            since = now - 7 * 86400
            label = "7d"
        elif body in ("30", "30d", "month"):
            since = now - 30 * 86400
            label = "30d"
        elif open_only:
            label = "open"
        else:
            since = now - 7 * 86400
            label = "7d"

        refresh_stats = await self.refresh_alert_report_prices(
            since=since,
            until=until,
            open_only=open_only
        )
        ohlcv_stats = await self.refresh_alert_ohlcv_peaks(
            since=since,
            until=until,
            open_only=open_only,
            now=now
        )
        report = await self.scanner_storage.build_ignition_alert_report(
            now,
            since=since,
            until=until,
            open_only=open_only
        )
        report["window"]["label"] = label
        report["live_refresh"] = refresh_stats
        report["ohlcv_refresh"] = ohlcv_stats
        return self.telegram.build_alert_performance_summary_message(
            report
        )

    async def refresh_alert_report_prices(
        self,
        since=None,
        until=None,
        open_only=False
    ):

        stats = {
            "enabled": TELEGRAM_AGENT_ALERT_REFRESH_ENABLED,
            "attempted": 0,
            "refreshed": 0,
            "updated_alerts": 0,
            "limited": False,
            "error": ""
        }

        if not TELEGRAM_AGENT_ALERT_REFRESH_ENABLED:
            return stats

        try:
            alerts = await self.scanner_storage.load_ignition_alerts(
                since=since,
                until=until,
                open_only=open_only
            )
        except Exception as exc:
            stats["error"] = str(exc)
            return stats

        alerts = sorted(
            alerts,
            key=lambda alert: safe_float(
                alert.get("alert_timestamp"),
                0
            ),
            reverse=True
        )
        addresses = []
        chain_by_address = {}

        for alert in alerts:
            address = alert.get("token_address")

            if not address or address in chain_by_address:
                continue

            addresses.append(address)
            chain_by_address[address] = alert.get(
                "chain_name",
                "solana"
            )

            if (
                len(addresses)
                >= TELEGRAM_AGENT_ALERT_REFRESH_MAX_TOKENS
            ):
                stats["limited"] = True
                break

        stats["attempted"] = len(addresses)

        if not addresses:
            return stats

        try:
            live_prices, live_stats = await fetch_live_prices(
                addresses,
                chain_by_address=chain_by_address
            )
        except Exception as exc:
            stats["error"] = str(exc)
            return stats

        stats.update({
            "refreshed": live_stats.get("refreshed", 0),
            "missing": live_stats.get("missing", []),
            "as_of": live_stats.get("as_of"),
            "source_error": live_stats.get("error", "")
        })

        updated = 0

        for address, live_price in live_prices.items():
            updated += await self.scanner_storage.update_ignition_alert_live_price(
                address,
                live_price.get("price_usd"),
                fdv=live_price.get("fdv_usd"),
                liquidity=live_price.get("liquidity_usd"),
                timestamp=live_stats.get("as_of")
            )

        stats["updated_alerts"] = updated

        return stats

    async def refresh_alert_ohlcv_peaks(
        self,
        since=None,
        until=None,
        open_only=False,
        now=None
    ):

        stats = {
            "enabled": TELEGRAM_AGENT_ALERT_OHLCV_REFRESH_ENABLED,
            "attempted": 0,
            "updated": 0,
            "error": ""
        }

        if not TELEGRAM_AGENT_ALERT_OHLCV_REFRESH_ENABLED:
            return stats

        try:
            alerts = await self.scanner_storage.load_ignition_alerts(
                since=since,
                until=until,
                open_only=open_only
            )

            if not alerts:
                return stats

            _until = until or now or time.time()
            max_pages = TELEGRAM_AGENT_ALERT_OHLCV_MAX_PAGES
            loop = asyncio.get_event_loop()

            ohlcv_stats = await loop.run_in_executor(
                None,
                lambda: refresh_alerts_with_ohlcv(
                    alerts,
                    until=_until,
                    save=True,
                    max_pages=max_pages
                )
            )
            stats["attempted"] = ohlcv_stats.get("attempted", 0)
            stats["updated"] = ohlcv_stats.get("updated", 0)

            if ohlcv_stats.get("auth_required"):
                stats["error"] = "OHLCV API key missing or invalid"

        except Exception as exc:
            stats["error"] = str(exc)

        return stats

    def alert_outcomes_message(
        self,
        body
    ):

        now = time.time()
        parts = str(
            body or ""
        ).strip().lower().split()
        period = parts[0] if parts else "7d"
        window_text = parts[1] if len(parts) > 1 else "1h"
        since = None
        until = None

        if period in ("", "today", "day"):
            since, until = local_day_window(now)
            label = "today"
        elif period in ("all", "inception"):
            label = "all"
        else:
            try:
                days = max(
                    float(period.rstrip("d")),
                    1
                )
            except ValueError:
                days = 7

            since = now - days * 86400
            label = f"{days:g}d"

        try:
            outcome_window = parse_window(window_text)
        except argparse.ArgumentTypeError:
            outcome_window = 3600

        rows = load_outcomes(
            since=since,
            until=until,
            window_seconds=outcome_window
        )
        summary = summarize_rows(rows)
        routes = [
            route
            for route in group_by_route(rows)
            if route["alerts"] >= 3
        ][:TELEGRAM_AGENT_MAX_REPORT_LINES]

        lines = [
            "<b>[ POST-ALERT OUTCOMES ]</b>",
            f"Window: <code>{html(label)} +{html(window_label(outcome_window))}</code>",
            f"Alerts with coverage: <b>{summary['alerts']}</b>"
        ]

        if summary["alerts"]:
            lines.extend([
                "Peak hits: "
                f"1.5x <b>{summary['hit_1_5x']}</b> "
                f"({pct(summary['hit_1_5x'] / summary['alerts'])}) | "
                f"2x <b>{summary['hit_2x']}</b> "
                f"({pct(summary['hit_2x'] / summary['alerts'])})",
                "Avg peak/close: "
                f"<b>{summary['avg_peak_multiple']:.2f}x</b> / "
                f"<b>{summary['avg_close_multiple']:.2f}x</b>",
                "False positives: "
                f"<b>{summary['false_positive']}</b> "
                f"({pct(summary['false_positive'] / summary['alerts'])})"
            ])

        if routes:
            lines.append("")
            lines.append("<b>Routes</b>")

            for route in routes:
                lines.append(
                    f"<code>{html(route['route'])}</code> "
                    f"n={route['alerts']} | "
                    f"2x {pct(route['hit_2x_rate'])} | "
                    f"peak {route['avg_peak_multiple']:.2f}x | "
                    f"false+ {pct(route['false_positive_rate'])}"
                )

        return "\n".join(lines)

    async def performance_message(
        self,
        body
    ):

        state = self.position_engine.load_state()
        closed = list(state.get("closed", []) or [])
        body = (body or "7").strip().lower()
        now = time.time()
        label = body

        if body in ("all", "inception"):
            selected = closed
            label = "all"
        else:
            try:
                days = max(float(body.rstrip("d")), 1)
            except ValueError:
                days = 7
                label = "7"

            since = now - days * 86400
            selected = [
                trade
                for trade in closed
                if safe_float(
                    trade.get("exit_at")
                    or trade.get("entry_at")
                ) >= since
            ]
            label = f"{days:g}d"

        live_refresh = {
            "enabled": False,
            "attempted": 0,
            "refreshed": 0,
            "error": ""
        }

        if selected:
            try:
                _open_positions, selected, live_refresh = (
                    await refresh_trade_prices(
                        {},
                        selected,
                        refresh_open=False,
                        refresh_closed=True
                    )
                )
            except Exception as exc:
                live_refresh = {
                    "enabled": True,
                    "attempted": len(selected),
                    "refreshed": 0,
                    "error": str(exc)
                }

        total = len(selected)
        pnl_values = [
            safe_float(trade.get("pnl_usd"))
            for trade in selected
        ]
        total_pnl = sum(pnl_values)
        wins = sum(1 for value in pnl_values if value > 0)
        losses = sum(1 for value in pnl_values if value < 0)
        win_rate = wins / total if total else 0
        recent = selected[-TELEGRAM_AGENT_MAX_REPORT_LINES:]
        lines = [
            "<b>[ PAPER PERFORMANCE ]</b>",
            f"Window: <code>{html(label)}</code>",
            f"Closed trades: <b>{total}</b>",
            f"Wins/Losses: {wins}/{losses} ({pct(win_rate)})",
            f"PnL: <b>{money(total_pnl)}</b>"
        ]

        if live_refresh.get("enabled"):
            live_line = (
                "Live prices: "
                f"<b>{live_refresh.get('refreshed', 0)}</b>/"
                f"{live_refresh.get('attempted', 0)} "
                "tokens"
            )

            if live_refresh.get("error"):
                live_line += (
                    " | "
                    f"{html(live_refresh.get('error'))}"
                )

            lines.append(live_line)

        if pnl_values:
            lines.append(
                "Avg/Best/Worst: "
                f"{money(total_pnl / total)} / "
                f"{money(max(pnl_values))} / "
                f"{money(min(pnl_values))}"
            )

        if recent:
            lines.append("")
            lines.append("<b>Recent closed</b>")

            for trade in recent:
                live_text = ""

                if trade.get("live_refreshed"):
                    live_text = (
                        " now "
                        f"{safe_float(trade.get('live_entry_multiple')):.2f}x"
                    )

                lines.append(
                    f"${html(trade.get('symbol', 'UNKNOWN'))} "
                    f"<code>{html(short_address(trade.get('address')))}</code> "
                    f"{money(trade.get('pnl_usd'))} "
                    f"({pct(trade.get('pnl_pct'))})"
                    f"{live_text} "
                    f"<code>{html(trade.get('close_reason', 'closed'))}</code>"
                )

        return "\n".join(lines)

    def restart_status_path(self):

        path = Path(TELEGRAM_AGENT_RESTART_STATUS_PATH)

        if not path.is_absolute():
            path = ROOT / path

        return path

    def write_restart_pending_status(
        self,
        message,
        argv
    ):

        chat = message.get("chat") or {}
        user = message.get("from") or {}
        now = time.time()
        path = self.restart_status_path()
        path.parent.mkdir(
            parents=True,
            exist_ok=True
        )
        payload = {
            "status": "pending",
            "chat_id": str(chat.get("id", "")),
            "chat_title": chat.get("title", ""),
            "user_id": str(user.get("id", "")),
            "username": user.get("username", ""),
            "message_id": message.get("message_id"),
            "requested_at": now,
            "requested_at_iso": datetime.fromtimestamp(now)
            .astimezone()
            .isoformat(timespec="seconds"),
            "old_pid": os.getpid(),
            "argv": argv
        }
        tmp_path = path.with_suffix(
            path.suffix + ".tmp"
        )
        tmp_path.write_text(
            json.dumps(
                payload,
                ensure_ascii=True,
                sort_keys=True
            ),
            encoding="utf-8"
        )
        tmp_path.replace(path)

    async def send_restart_complete_if_pending(self):

        path = self.restart_status_path()

        if not path.exists():
            return

        try:
            payload = json.loads(
                path.read_text(encoding="utf-8")
            )
        except (
            OSError,
            json.JSONDecodeError
        ) as exc:
            print(
                "Telegram restart status read failed: "
                f"{exc}"
            )
            return

        if payload.get("status") != "pending":
            return

        chat_id = str(payload.get("chat_id", "")).strip()

        if not chat_id:
            return

        message = {
            "chat": {
                "id": chat_id
            },
            "message_id": payload.get("message_id")
        }
        now = time.time()
        requested_at = safe_float(
            payload.get("requested_at"),
            0
        )
        elapsed = (
            f"{max(now - requested_at, 0):.1f}s"
            if requested_at
            else "unknown"
        )
        text = (
            "<b>[ RESTART COMPLETE ]</b>\n"
            "main.py restarted successfully.\n"
            f"PID: <code>{html(os.getpid())}</code>\n"
            f"Elapsed: <code>{html(elapsed)}</code>"
        )

        sent = await self.reply(
            message,
            text
        )

        if sent:
            try:
                path.unlink()
            except OSError as exc:
                print(
                    "Telegram restart status cleanup failed: "
                    f"{exc}"
                )

    async def restart_main(
        self,
        message,
        body
    ):

        if not TELEGRAM_AGENT_RESTART_ENABLED:
            await self.reply(
                message,
                "<b>[ RESTART BLOCKED ]</b>\n"
                "Set "
                "<code>TELEGRAM_AGENT_RESTART_ENABLED=true</code> "
                "to allow /restart."
            )
            return

        body = str(body or "").strip().lower()

        if body not in ("", "main", "main.py", "now"):
            await self.reply(
                message,
                "<b>[ RESTART ]</b>\n"
                "Usage: <code>/restart</code>"
            )
            return

        script_name = Path(sys.argv[0] or "").name

        if script_name != "main.py":
            await self.reply(
                message,
                "<b>[ RESTART BLOCKED ]</b>\n"
                "This command only restarts a process launched as "
                "<code>main.py</code>."
            )
            return

        argv = [
            sys.executable
        ] + sys.argv

        await self.reply(
            message,
            "<b>[ RESTARTING MAIN.PY ]</b>\n"
            "Telegram update acknowledged. Replacing the current "
            "process now."
        )

        try:
            self.write_restart_pending_status(
                message,
                argv
            )
        except Exception as exc:
            await self.reply(
                message,
                "<b>[ RESTART BLOCKED ]</b>\n"
                "Could not write restart status file:\n"
                f"<code>{html(exc)}</code>"
            )
            return

        try:
            await self.acknowledge_update_offset()
        except Exception as exc:
            print(
                "Telegram restart offset acknowledge failed: "
                f"{exc}"
            )

        await asyncio.sleep(0.5)

        print(
            "Telegram command agent restarting process: "
            f"{' '.join(argv)}"
        )

        try:
            os.execv(
                sys.executable,
                argv
            )
        except Exception as exc:
            try:
                self.restart_status_path().unlink()
            except OSError:
                pass

            await self.reply(
                message,
                "<b>[ RESTART FAILED ]</b>\n"
                f"<code>{html(exc)}</code>"
            )

    def live_action_message(
        self,
        command,
        body
    ):

        if not TELEGRAM_AGENT_WRITE_ACTIONS_ENABLED:
            return (
                "<b>[ ACTION BLOCKED ]</b>\n"
                "Write actions are disabled. Set "
                "<code>TELEGRAM_AGENT_WRITE_ACTIONS_ENABLED=true</code> "
                "only after command review."
            )

        if not TELEGRAM_AGENT_LIVE_ACTIONS_ENABLED:
            return (
                "<b>[ LIVE ACTION BLOCKED ]</b>\n"
                "Live actions are disabled. Set "
                "<code>TELEGRAM_AGENT_LIVE_ACTIONS_ENABLED=true</code> "
                "only when execution is ready."
            )

        return (
            "<b>[ LIVE ACTION FRAMEWORK ]</b>\n"
            f"Command: <code>{html(command)} {html(body)}</code>\n"
            "The live execution handler is intentionally not wired yet."
        )
