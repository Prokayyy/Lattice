import time
from html import escape
from urllib.parse import quote, urlparse

import aiohttp

from config import (
    build_defined_token_url,
    DEFINITIVE_APP_BASE_URL,
    DEFINITIVE_REFERRAL_CODE,
    IGNITION_BONDING_EARLY_REVIVAL_MIN_BUY_SELL_RATIO_5M,
    IGNITION_BONDING_EARLY_REVIVAL_MIN_TXNS_5M,
    IGNITION_BONDING_EARLY_REVIVAL_MIN_VOLUME_LIQUIDITY_RATIO_5M,
    IGNITION_MIGRATED_REVIVAL_MIN_BUY_SELL_RATIO_5M,
    IGNITION_MIGRATED_REVIVAL_MIN_DRAWDOWN_PCT,
    IGNITION_MIGRATED_REVIVAL_MAX_DRAWDOWN_PCT,
    IGNITION_MIGRATED_REVIVAL_MIN_TXNS_5M,
    IGNITION_MIGRATED_REVIVAL_MIN_VOLUME_5M_USD,
    IGNITION_MIGRATED_REVIVAL_MIN_VOLUME_LIQUIDITY_RATIO_5M,
    IGNITION_LOW_FDV_ACCUMULATION_MAX_FDV,
    IGNITION_LOW_FDV_ACCUMULATION_MAX_PRICE_CHANGE_5M,
    IGNITION_LOW_FDV_ACCUMULATION_MIN_BUY_SELL_RATIO_1H,
    IGNITION_LOW_FDV_ACCUMULATION_MIN_BUY_SELL_RATIO_5M,
    IGNITION_LOW_FDV_ACCUMULATION_MIN_LIQUIDITY,
    IGNITION_LOW_FDV_ACCUMULATION_MIN_PRICE_CHANGE_1H,
    IGNITION_LOW_FDV_ACCUMULATION_MIN_PRICE_CHANGE_6H,
    IGNITION_LOW_FDV_ACCUMULATION_MIN_VOLUME_LIQUIDITY_RATIO_1H,
    IGNITION_LOW_FDV_ACCUMULATION_MIN_VOLUME_LIQUIDITY_RATIO_5M,
    IGNITION_SUMMARY_CHAT_ENABLED,
    IGNITION_SUMMARY_CHAT_ID,
    IGNITION_SUMMARY_CHAT_IDS,
    LIVE_EXECUTION_TELEGRAM_CHAT_IDS,
    LIVE_EXECUTION_TELEGRAM_ENABLED,
    ORGANIC_TELEGRAM_ALERTS_ENABLED,
    HYPEREVM_IGNITION_MAX_FDV_USD,
    HYPEREVM_IGNITION_MIN_LIQUIDITY_USD,
    HYPEREVM_IGNITION_MIN_PRICE_CHANGE_24H,
    HYPEREVM_IGNITION_MIN_PRICE_CHANGE_5M,
    HYPEREVM_IGNITION_MIN_VOLUME_1H_USD,
    POSITION_TELEGRAM_ENABLED,
    POSITION_AVOID_MIGRATION_FDV_ZONE,
    POSITION_MIGRATION_FDV_BUFFER_USD,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    TELEGRAM_CHAT_IDS,
    TOKENSCAN_COMMAND
)

from alerts.quality_playbook import (
    get_quality_playbook,
    route_display_name,
)

from alerts.tokenscan_user import (
    TokenScanUserTrigger
)

from utils.tg_format import jup_url


class TelegramAlertSender:

    def __init__(self):

        self.base_url = (
            f"https://api.telegram.org/bot"
            f"{TELEGRAM_BOT_TOKEN}"
        )

        self.chat_id = TELEGRAM_CHAT_ID
        self.chat_ids = list(
            TELEGRAM_CHAT_IDS
            or (
                [TELEGRAM_CHAT_ID]
                if TELEGRAM_CHAT_ID
                else []
            )
        )
        if IGNITION_SUMMARY_CHAT_ENABLED:
            self.summary_chat_ids = list(
                IGNITION_SUMMARY_CHAT_IDS
                or (
                    [IGNITION_SUMMARY_CHAT_ID]
                    if IGNITION_SUMMARY_CHAT_ID
                    else []
                )
            )
        else:
            self.summary_chat_ids = []

        self.broadcast_chat_ids = list(
            dict.fromkeys(
                self.chat_ids + self.summary_chat_ids
            )
        )
        self.live_execution_chat_ids = list(
            LIVE_EXECUTION_TELEGRAM_CHAT_IDS
            or self.chat_ids
        )
        # True only when a dedicated live-execution channel is configured
        # AND it differs from the main chat.  When False, the position
        # message already carries the live execution summary via
        # live_execution_line, so a second message to the same chat would
        # trigger Telegram's per-chat rate limit and silently drop the
        # position entry alert.
        self.has_dedicated_live_execution_chat = bool(
            LIVE_EXECUTION_TELEGRAM_CHAT_IDS
            and set(LIVE_EXECUTION_TELEGRAM_CHAT_IDS) != set(self.chat_ids)
        )
        self.tokenscan_user_trigger = (
            TokenScanUserTrigger()
        )

    def get_grade(self, score):

        if score >= 90:
            return "🟢 S", "Extremely High"

        if score >= 80:
            return "🟢 A", "Very High"

        if score >= 70:
            return "🔵 B", "High"

        if score >= 60:
            return "🟡 C", "Moderate"

        return "⚪ D", "Low"

    def build_trade_link(
        self,
        contract_address,
        chain="solana"
    ):

        base_url = str(
            DEFINITIVE_APP_BASE_URL
            or "https://app.definitive.fi"
        ).rstrip("/")
        referral = str(
            DEFINITIVE_REFERRAL_CODE
            or ""
        ).strip().lstrip("@")
        referral_suffix = (
            f"@{quote(referral, safe='')}"
            if referral
            else ""
        )

        return (
            f"{base_url}/"
            f"{quote(str(contract_address or ''), safe='')}/"
            f"{quote(str(chain or 'solana').lower(), safe='')}"
            f"{referral_suffix}"
        )

    def html(
        self,
        value,
        quote=True
    ):

        return escape(
            str(value or ""),
            quote=quote
        )

    def safe_external_url(
        self,
        value
    ):

        url = str(value or "").strip()

        if not url or any(ord(ch) < 32 for ch in url):
            return ""

        parsed = urlparse(url)

        if parsed.scheme != "https" or not parsed.netloc:
            return ""

        return escape(
            url,
            quote=True
        )

    def safe_float(
        self,
        value,
        default=0
    ):

        if value is None or value == "":
            return default
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def build_defined_url(
        self,
        metrics
    ):

        return build_defined_token_url(
            address=metrics.address,
            chain=metrics.chain,
            pair_address=metrics.pair_address
        )

    def build_x_search_link(
        self,
        metrics
    ):

        return self.build_x_search_url(
            metrics.symbol,
            metrics.address
        )

    def build_x_search_url(
        self,
        symbol,
        address
    ):

        address = str(address or "").strip()
        symbol = str(symbol or "").strip().lstrip("$")
        terms = []

        if symbol:
            terms.append(f"\"${symbol}\"")

        if address:
            terms.append(f"\"{address}\"")
            terms.append(f"url:{address}")

        query = (
            "("
            + " OR ".join(terms)
            + ")"
        )

        return (
            "https://x.com/search?q="
            f"{quote(query, safe='')}"
            "&f=live"
        )

    def token_name(
        self,
        metrics
    ):

        return str(
            getattr(metrics, "name", "")
            or ""
        ).strip()

    def token_identity_lines(
        self,
        metrics
    ):

        name = self.token_name(metrics)
        symbol = str(
            getattr(metrics, "symbol", "")
            or "UNKNOWN"
        ).strip()
        lines = [
            f"Ticker: <b>${self.html(symbol)}</b>"
        ]

        if (
            name
            and name.upper() != symbol.upper()
        ):
            lines.insert(
                0,
                f"Name: <b>{self.html(name)}</b>"
            )

        return "\n".join(lines)

    def build_initial_fdv_line(
        self,
        details
    ):

        if not details.get("is_recall"):
            return ""

        initial_fdv = details.get(
            "initial_ignition_fdv"
        )

        if initial_fdv is None:
            return ""

        try:
            initial_fdv = float(initial_fdv)
        except (TypeError, ValueError):
            return ""

        return (
            "Initial call FDV: "
            f"<b>${initial_fdv:,.0f}</b>\n"
        )

    def divider(self):

        return (
            "<code>"
            "--------------------------------"
            "</code>"
        )

    def section_title(
        self,
        title
    ):

        return (
            f"<b>[ {self.html(title).upper()} ]</b>"
        )

    def compact_section_title(
        self,
        title
    ):

        return (
            f"<b>{self.html(title).upper()}</b>"
        )

    def telegram_section_title(
        self,
        title
    ):

        return (
            f"━━━━━━━━━━ {self.html(title).upper()} ━━━━━━━━━━"
        )

    def format_penalty(
        self,
        penalty
    ):

        penalty = self.safe_float(
            penalty,
            0
        )

        if penalty == 0:
            return "0"

        if penalty > 0:
            return f"-{penalty:g}"

        return f"{penalty:g}"

    def format_trade_score(
        self,
        score
    ):

        score = int(
            self.safe_float(
                score,
                0
            )
        )

        if score == 0:
            return "0"

        if 0 < score <= 3:
            return "+" * score

        if -3 <= score < 0:
            return "-" * abs(score)

        return f"{score:+d}"

    def format_signed_usd(
        self,
        value
    ):

        value = self.safe_float(
            value,
            0
        )
        sign = "+" if value >= 0 else "-"

        return (
            f"{sign}${abs(value):,.0f}"
        )

    def migration_fdv_risk_zone(
        self,
        metrics
    ):

        migration_fdv = self.safe_float(
            getattr(metrics, "migration_fdv", 0),
            0
        )

        if (
            not POSITION_AVOID_MIGRATION_FDV_ZONE
            or migration_fdv <= 0
            or getattr(metrics, "lifecycle", "") != "bonding_curve"
        ):
            return False

        distance = self.safe_float(
            getattr(metrics, "migration_distance_usd", 0),
            migration_fdv - self.safe_float(metrics.fdv, 0)
        )

        return (
            abs(distance)
            <= POSITION_MIGRATION_FDV_BUFFER_USD
        )

    def build_migration_fdv_lines(
        self,
        metrics
    ):

        migration_fdv = self.safe_float(
            getattr(metrics, "migration_fdv", 0),
            0
        )

        if migration_fdv <= 0:
            return ""

        distance = self.safe_float(
            getattr(metrics, "migration_distance_usd", 0),
            migration_fdv - self.safe_float(metrics.fdv, 0)
        )
        risk_zone = (
            "YES"
            if self.migration_fdv_risk_zone(metrics)
            else "NO"
        )

        return (
            f"Migration FDV: <b>${migration_fdv:,.0f}</b>\n"
            "Migration Distance: "
            f"<b>{self.format_signed_usd(distance)}</b> "
            f"| Risk Zone: <b>{risk_zone}</b>"
        )

    def build_migration_fdv_inline(
        self,
        metrics
    ):

        lines = self.build_migration_fdv_lines(
            metrics
        )

        if not lines:
            return ""

        return (
            lines.replace(
                "\n",
                " | "
            )
        )

    def build_tag_criteria_block(
        self,
        quality_tag,
        alert_route
    ):

        # After unification quality_tag == alert_route for live alerts.
        # Use alert_route as canonical; fall back to quality_tag for legacy
        # records that pre-date the unification.
        normalized_route = str(alert_route or quality_tag or "")

        if normalized_route in (
            "hyperevm_slow_cook",
            "hyperevm_ignition"
        ):
            normalized_route = "hyperevm_ignition"

        if normalized_route in (
            "low_fdv_accumulation",
            "bonding_low_fdv_accumulation"
        ):
            content = (
                "Low-FDV accumulation:\n"
                "• FDV &lt; "
                f"${IGNITION_LOW_FDV_ACCUMULATION_MAX_FDV:,.0f}\n"
                "• Liquidity ≥ "
                f"${IGNITION_LOW_FDV_ACCUMULATION_MIN_LIQUIDITY:,.0f}\n"
                "• 5m Vol/Liq ≥ "
                f"{IGNITION_LOW_FDV_ACCUMULATION_MIN_VOLUME_LIQUIDITY_RATIO_5M:.0%}\n"
                "• 1h Vol/Liq ≥ "
                f"{IGNITION_LOW_FDV_ACCUMULATION_MIN_VOLUME_LIQUIDITY_RATIO_1H:.1f}x\n"
                "• 5m change ≤ "
                f"{IGNITION_LOW_FDV_ACCUMULATION_MAX_PRICE_CHANGE_5M:+.0f}%\n"
                "• 1h/6h ≥ "
                f"{IGNITION_LOW_FDV_ACCUMULATION_MIN_PRICE_CHANGE_1H:+.0f}% / "
                f"{IGNITION_LOW_FDV_ACCUMULATION_MIN_PRICE_CHANGE_6H:+.0f}%\n"
                "• 5m/1h Buy-Sell ≥ "
                f"{IGNITION_LOW_FDV_ACCUMULATION_MIN_BUY_SELL_RATIO_5M:.1f}x / "
                f"{IGNITION_LOW_FDV_ACCUMULATION_MIN_BUY_SELL_RATIO_1H:.1f}x"
            )
            return (
                f"{self.telegram_section_title('tag criteria')}\n"
                f"<blockquote>{content}</blockquote>"
            )

        if normalized_route in (
            "migrated_revival",
            "migrated_early_revival"
        ):
            content = (
                "Post-graduation dump recovery:\n"
                "• Dump from peak: "
                f"{IGNITION_MIGRATED_REVIVAL_MIN_DRAWDOWN_PCT:.0%}"
                " – "
                f"{IGNITION_MIGRATED_REVIVAL_MAX_DRAWDOWN_PCT:.0%}\n"
                "• 5m Vol/Liq ≥ "
                f"{IGNITION_MIGRATED_REVIVAL_MIN_VOLUME_LIQUIDITY_RATIO_5M:.0%}\n"
                "• 5m txns ≥ "
                f"{IGNITION_MIGRATED_REVIVAL_MIN_TXNS_5M}\n"
                "• 5m Buy/Sell ≥ "
                f"{IGNITION_MIGRATED_REVIVAL_MIN_BUY_SELL_RATIO_5M:.1f}x\n"
                "• 5m volume ≥ "
                f"${IGNITION_MIGRATED_REVIVAL_MIN_VOLUME_5M_USD:,.0f}"
            )
            return (
                f"{self.telegram_section_title('tag criteria')}\n"
                f"<blockquote>{content}</blockquote>"
            )

        if normalized_route in (
            "bonding_early_revival",
            "early_revival"
        ):
            content = (
                "Bonding:\n"
                "• 5m Vol/Liq ≥ "
                f"{IGNITION_BONDING_EARLY_REVIVAL_MIN_VOLUME_LIQUIDITY_RATIO_5M:.0%}\n"
                "• 5m txns ≥ "
                f"{IGNITION_BONDING_EARLY_REVIVAL_MIN_TXNS_5M}\n"
                "• 5m Buy/Sell ≥ "
                f"{IGNITION_BONDING_EARLY_REVIVAL_MIN_BUY_SELL_RATIO_5M:.1f}x\n"
                "• 5m volume ≥ band minimum"
            )
            return (
                f"{self.telegram_section_title('tag criteria')}\n"
                f"<blockquote>{content}</blockquote>"
            )

        if normalized_route == "hyperevm_ignition":
            content = (
                "HyperEVM ignition:\n"
                "• 5m price change ≥ "
                f"{HYPEREVM_IGNITION_MIN_PRICE_CHANGE_5M:+.0f}%\n"
                "• 24h price change ≥ "
                f"{HYPEREVM_IGNITION_MIN_PRICE_CHANGE_24H:+.0f}%\n"
                "• Liquidity ≥ "
                f"${HYPEREVM_IGNITION_MIN_LIQUIDITY_USD:,.0f}\n"
                "• FDV ≤ "
                f"${HYPEREVM_IGNITION_MAX_FDV_USD:,.0f}\n"
                "• 1h volume ≥ "
                f"${HYPEREVM_IGNITION_MIN_VOLUME_1H_USD:,.0f}\n"
                "• 5m impulse and participant count ignored"
            )
            return (
                f"{self.telegram_section_title('tag criteria')}\n"
                f"<blockquote>{content}</blockquote>"
            )

        entry = get_quality_playbook(normalized_route)

        return (
            f"{self.telegram_section_title('tag criteria')}\n"
            f"<blockquote>{self.html(entry['criteria'])}</blockquote>"
        )

    def build_play_text(
        self,
        quality_tag
    ):

        entry = get_quality_playbook(
            quality_tag
        )
        play = str(
            entry["play"]
        ).strip()

        if ". " in play:
            first_sentence, remaining = play.split(
                ". ",
                1
            )
            play = (
                f"{first_sentence}.\n"
                f"{remaining}"
            )

        return self.html(
            play
        )

    def build_quality_block(
        self,
        quality_tag
    ):

        entry = get_quality_playbook(
            quality_tag
        )

        criteria = self.html(
            entry["criteria"]
        )

        play = self.html(
            entry["play"]
        )

        return (
            f"<b>Tag Criteria</b>\n"
            f"<code>{criteria}</code>\n"
            f"<b>Play</b>\n"
            f"<code>{play}</code>"
        )

    def build_card_header(
        self,
        alert_title,
        quality,
        symbol,
        chain
    ):

        return (
            f"<b>[ {alert_title} | {quality} ]</b>\n"
            f"<b>${symbol}</b> on <b>{chain}</b>\n"
            f"{self.divider()}"
        )

    def build_alert_keyboard(
        self,
        metrics,
        defined_url=None
    ):

        buttons = [
            [
                {
                    "text": "Copy CA",
                    "copy_text": {
                        "text": metrics.address
                    }
                }
            ],
            [
                {
                    "text": "Definitive",
                    "url": self.build_trade_link(
                        metrics.address,
                        metrics.chain
                    )
                }
            ]
        ]

        if defined_url:
            buttons[1].append(
                {
                    "text": "Defined",
                    "url": defined_url
                }
            )

        # Jupiter is Solana-only; carry our referral code on the swap link
        if str(metrics.chain or "").lower() == "solana":
            buttons.append(
                [
                    {
                        "text": "🪐 Trade on Jupiter",
                        "url": jup_url(metrics.address)
                    }
                ]
            )

        buttons.append(
            [
                {
                    "text": "Search X",
                    "url": self.build_x_search_link(
                        metrics
                    )
                }
            ]
        )

        return {
            "inline_keyboard": buttons
        }

    def short_address(
        self,
        address
    ):

        address = str(address or "")

        if len(address) <= 12:
            return address

        return (
            f"{address[:6]}..."
            f"{address[-6:]}"
        )

    def asset_label(
        self,
        address
    ):

        address = str(address or "")

        if address == "So11111111111111111111111111111111111111112":
            return "SOL"

        if address == "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v":
            return "USDC"

        return self.short_address(address)

    def build_position_event_keyboard(
        self,
        address,
        symbol,
        chain="solana"
    ):

        defined_url = build_defined_token_url(
            address=address,
            chain=chain,
            pair_address=""
        )

        rows = [
            [
                {
                    "text": "Copy CA",
                    "copy_text": {
                        "text": address
                    }
                }
            ],
            [
                {
                    "text": "Definitive",
                    "url": self.build_trade_link(
                        address,
                        chain
                    )
                },
                {
                    "text": "Defined",
                    "url": defined_url
                }
            ]
        ]

        # Jupiter is Solana-only; carry our referral code on the swap link
        if str(chain or "").lower() == "solana":
            rows.append(
                [
                    {
                        "text": "🪐 Trade on Jupiter",
                        "url": jup_url(address)
                    }
                ]
            )

        rows.append(
            [
                {
                    "text": "Search X",
                    "url": self.build_x_search_url(
                        symbol,
                        address
                    )
                }
            ]
        )

        return {
            "inline_keyboard": rows
        }

    def build_position_event_message(
        self,
        event
    ):

        event_type = str(
            event.get("type", "update")
        ).upper()
        symbol_raw = str(
            event.get("symbol", "UNKNOWN")
            or "UNKNOWN"
        ).strip()
        symbol = self.html(symbol_raw)
        name_raw = str(
            event.get("name", "")
            or ""
        ).strip()
        identity_line = f"<b>${symbol}</b>"

        if (
            name_raw
            and name_raw.upper() != symbol_raw.upper()
        ):
            identity_line = (
                f"<b>{self.html(name_raw)}</b> / {identity_line}"
            )

        address = self.html(
            event.get("address", "")
        )
        short_address = self.html(
            self.short_address(
                event.get("address", "")
            )
        )
        reason = self.html(
            event.get("reason", "")
        )
        entry_route_raw = str(
            event.get("entry_route", "") or ""
        )
        entry_score = int(
            self.safe_float(event.get("entry_score"), 0)
        )
        trailing_mode = self.html(
            event.get("trailing_stop_mode", "standard")
        )
        entry_impulse = self.safe_float(
            event.get("entry_impulse"),
            0
        )
        entry_volume_multiple = self.safe_float(
            event.get("entry_volume_multiple"),
            0
        )
        entry_buy_sell_volume_ratio = self.safe_float(
            event.get("entry_buy_sell_volume_ratio"),
            0
        )
        entry_quality_tier = self.html(
            event.get("entry_quality_tier", "")
        )
        route_score_line = ""
        if entry_route_raw:
            route_score_line = (
                f"Route: <b>{self.html(entry_route_raw)}</b> | "
                f"Score: <b>{entry_score}</b>\n"
            )
        entry_confirmation_line = ""

        if event.get("entry_confirmation_score"):
            confirmation_note = (
                " shadow"
                if event.get("entry_confirmation_shadow_mode")
                else ""
            )
            entry_confirmation_line = (
                "Entry Confirm: "
                f"<b>{event.get('entry_confirmation_score', 0):.0f}</b> "
                f"({event.get('entry_confirmation_confirmed_scans', 0)}/"
                f"{event.get('entry_confirmation_required_scans', 0)}"
                f"{confirmation_note})\n"
            )
        fdv = self.safe_float(
            event.get("fdv"),
            0
        )
        volume_1h = self.safe_float(
            event.get("volume_1h"),
            0
        )
        migration_fdv = self.safe_float(
            event.get("migration_fdv"),
            0
        )
        migration_distance = self.safe_float(
            event.get("migration_distance_usd"),
            0
        )
        migration_line = ""

        if migration_fdv > 0:
            migration_risk = (
                "YES"
                if abs(migration_distance)
                <= POSITION_MIGRATION_FDV_BUFFER_USD
                else "NO"
            )
            migration_line = (
                "Migration FDV: "
                f"<b>${migration_fdv:,.0f}</b> | "
                "Distance: "
                f"<b>{self.format_signed_usd(migration_distance)}</b> | "
                f"Risk: <b>{migration_risk}</b>\n"
            )

        size_line = ""

        if event.get("type") in ("scale_out", "live_scale_out"):
            size_line = (
                "Sold: "
                f"<b>{event.get('size_pct', 0):.0%}</b> | "
                f"Proceeds: ${event.get('proceeds_usd', 0):,.2f} "
                f"({event.get('proceeds_sol', 0):.2f} SOL)\n"
            )

        if event.get("type") == "close":
            size_line = (
                "Closed runner | "
                f"Proceeds: ${event.get('proceeds_usd', 0):,.2f} "
                f"({event.get('proceeds_sol', 0):.2f} SOL)\n"
            )


        exit_quote_line = ""
        if event.get("exit_quote_checked"):
            attempt_count = int(
                event.get("exit_quote_attempt_count", 0) or 0
            )
            attempt_name = self.html(
                event.get("exit_quote_attempt_name", "")
            )
            attempt_note = ""

            if attempt_count:
                attempt_note = f" | Attempts {attempt_count}"

            if event.get("exit_quote_fallback_used") and attempt_name:
                attempt_note = (
                    f"{attempt_note} | Fallback {attempt_name}"
                )

            if event.get("exit_quote_available"):
                exit_quote_line = (
                    "Exit quote: "
                    f"<b>${event.get('exit_quote_value_usd', 0):,.2f}</b> "
                    f"via {self.html(event.get('exit_quote_provider', ''))} "
                    f"| Impact {event.get('exit_quote_price_impact_pct', 0):.2f}%"
                    f"{attempt_note}\n"
                )
            else:
                exit_quote_line = (
                    "Exit quote: unavailable "
                    f"({self.html(event.get('exit_quote_error', ''))})"
                    f"{attempt_note}\n"
	                )

        next_scale_line = ""
        if event.get("next_scale_multiple"):
            next_scale_line = (
                "Next scale: "
                f"<b>{event.get('next_scale_multiple', 0):.2f}x</b> "
                f"to <b>{event.get('next_scale_target_pct', 0):.0%}</b>\n"
            )

        live_execution_line = ""
        if event.get("live_execution_enabled"):
            exec_side = self.html(
                (event.get("live_execution_side") or "").upper()
            )
            exec_error = self.html(
                event.get("live_execution_error") or ""
            )
            exec_order_id = self.html(
                event.get("live_execution_order_id") or ""
            )
            exec_provider = self.html(
                event.get("live_execution_provider") or ""
            )
            filled_tokens = self.safe_float(
                event.get("live_execution_filled_target_amount"), 0
            )
            filled_contra = self.safe_float(
                event.get("live_execution_filled_contra_amount"), 0
            )
            avg_fill = self.safe_float(
                event.get("live_execution_average_fill_price"), 0
            )
            fill_usd = self.safe_float(
                event.get("live_execution_average_notional_price"), 0
            )
            order_suffix = (
                f" | Order <code>{exec_order_id}</code>"
                if exec_order_id
                else ""
            )

            if event.get("live_execution_dry_run"):
                live_execution_line = (
                    f"Live execution: <b>dry-run</b> {exec_side}\n"
                )
            elif event.get("live_execution_skipped"):
                skip_reason = self.html(
                    event.get("live_execution_reason") or ""
                )
                live_execution_line = (
                    "Live execution: <b>skipped</b>"
                    + (f" — {skip_reason}" if skip_reason else "")
                    + "\n"
                )
            elif exec_error:
                live_execution_line = (
                    f"Live execution: <b>FAILED</b> {exec_side}"
                    f" — <code>{exec_error}</code>"
                    f"{order_suffix}\n"
                )
            elif event.get("live_execution_submitted"):
                if filled_tokens > 0 and (fill_usd > 0 or avg_fill > 0):
                    fill_price_str = (
                        f"${fill_usd:.8f}"
                        if fill_usd > 0
                        else f"{avg_fill:.8f} SOL"
                    )
                    live_execution_line = (
                        f"Live execution: <b>FILLED</b> {exec_side} "
                        f"<b>{filled_tokens:,.0f}</b> tokens"
                        f" @ <code>{fill_price_str}</code>"
                        + (
                            f" | {filled_contra:.4f} SOL"
                            if filled_contra > 0
                            else ""
                        )
                        + order_suffix
                        + "\n"
                    )
                else:
                    live_execution_line = (
                        f"Live execution: <b>submitted</b> {exec_side}"
                        f" via {exec_provider}"
                        f" — fill pending"
                        f"{order_suffix}\n"
                    )
            else:
                live_execution_line = (
                    "Live execution: <b>not submitted</b>"
                    + (f" — <code>{exec_error}</code>" if exec_error else "")
                    + "\n"
                )

        vwap_line = ""
        if event.get("anchored_vwap_ready"):
            vwap_line = (
                "VWAP: "
                f"<code>${event.get('anchored_vwap', 0):.8f}</code> "
                f"({event.get('anchored_vwap_source', '')})\n"
            )

        rebound_line = ""
        if event.get("trailing_rebound_watch_active"):
            rebound_line = (
                "Rebound watch: <b>active</b>\n"
            )
        elif event.get("trailing_rebound_reentry"):
            rebound_line = (
                "Rebound reentry: <b>yes</b>\n"
            )

        chain = event.get("chain", "solana")
        defined_url = build_defined_token_url(
            address=event.get("address", ""),
            chain=chain,
            pair_address=""
        )
        defined_line = (
            f'Defined: <a href="{self.html(defined_url)}">'
            "Defined.fi"
            "</a>\n"
        )

        trade_label = (
            "LIVE TRADE"
            if event.get("live_execution_submitted")
            or event.get("live_execution_entry_submitted")
            else "PAPER TRADE"
        )
        if event_type == "ENTRY" and entry_route_raw:
            header_tag = (
                " | "
                + self.html(
                    entry_route_raw.replace("_", " ").upper()
                )
            )
        elif event_type in ("STOP", "CLOSE") and reason:
            header_tag = f" | {reason.upper()}"
        else:
            header_tag = ""

        # --- live-derived figures (delivery is live-only, so show the real
        # trade — not the paper $40 accounting that confused the old format) ---
        le_tokens = self.safe_float(
            event.get("live_execution_filled_target_amount"), 0
        )
        le_contra = self.safe_float(
            event.get("live_execution_filled_contra_amount"), 0
        )
        le_contra_usd = self.safe_float(
            event.get("live_execution_contra_asset_usd"), 0
        )
        le_fill_usd = self.safe_float(
            event.get("live_execution_average_notional_price"), 0
        )
        entry_price_v = self.safe_float(event.get("entry_price"), 0)
        le_value_usd = (
            le_contra * le_contra_usd
            if le_contra > 0 and le_contra_usd > 0
            else self.safe_float(event.get("live_execution_order_value_usd"), 0)
        )
        live_notional_usd = self.safe_float(
            event.get("live_execution_entry_notional_usd"),
            self.safe_float(event.get("live_execution_order_value_usd"), 0),
        )
        # Multiple from the executable USD fill vs entry (real), not the
        # tracked last price (which can be a glitch tick).
        le_multiple = (
            le_fill_usd / entry_price_v
            if le_fill_usd > 0 and entry_price_v > 0
            else self.safe_float(event.get("price_multiple"), 0)
        )
        live_pnl_pct = (le_multiple - 1) * 100 if le_multiple > 0 else 0

        if le_fill_usd > 0:
            live_fill_line = (
                f"Fill: <code>${le_fill_usd:.8f}</code> "
                f"(<b>{le_multiple:.2f}x</b>)"
            )
        else:
            live_fill_line = (
                f"Last: <code>${self.safe_float(event.get('last_price'), 0):.8f}</code> "
                f"({self.safe_float(event.get('price_multiple'), 0):.2f}x)"
            )

        if le_tokens > 0:
            live_size_line = (
                f"Live size: <b>${le_value_usd:,.2f}</b> · "
                f"{le_tokens:,.0f} tokens"
                + (f" · {le_contra:.4f} SOL" if le_contra > 0 else "")
            )
        else:
            live_size_line = f"Live size: <b>${live_notional_usd:,.2f}</b>"

        # Live proceeds for scale/close (real SOL moved × contra price)
        if event.get("type") in ("scale_out", "live_scale_out"):
            size_line = (
                f"Sold: <b>{event.get('size_pct', 0):.0%}</b> | "
                f"Live proceeds: ${le_value_usd:,.2f} ({le_contra:.4f} SOL)\n"
            )
        elif event.get("type") in ("close", "live_close"):
            size_line = (
                "Closed runner | "
                f"Live proceeds: ${le_value_usd:,.2f} ({le_contra:.4f} SOL)\n"
            )

        return f"""<b>[ {trade_label} | {event_type}{header_tag} ]</b>
{identity_line} <code>{short_address}</code>
{self.divider()}

{self.section_title("position")}
{route_score_line}\
Entry: <code>${event.get('entry_price', 0):.8f}</code>
{live_fill_line}
{live_size_line}
FDV: <b>${fdv:,.0f}</b> | 1h Vol: <b>${volume_1h:,.0f}</b>
{migration_line}\
{defined_line}\
	Impulse: <b>{entry_impulse:.2f}x</b>
	Quality: <code>{entry_quality_tier or "n/a"}</code> | Vol Multiple: <b>{entry_volume_multiple:.2f}x</b>
	Dollar Flow: <b>{entry_buy_sell_volume_ratio:.2f}x</b> buy/sell 5m ({self.html(event.get('entry_buy_sell_volume_source_5m', ''))})
	{entry_confirmation_line}\
	Cash: {event.get('cash_sol', 0):.2f} SOL
Scaled: {event.get('scaled_out_pct', 0):.0%}
{size_line}Result: <b>{live_pnl_pct:+.1f}%</b> (fill vs entry)
{self.divider()}

{self.section_title("pressure")}
Now: {event.get('last_pressure', 0):.1f}/100
Entry: {event.get('entry_pressure', 0):.1f}/100
	Peak: {event.get('peak_pressure', 0):.1f}/100
	Trail: <code>${event.get('trailing_stop_price', 0):.8f}</code> ({trailing_mode})
	{next_scale_line}\
	{vwap_line}\
	{rebound_line}\
	{exit_quote_line}\
	{live_execution_line}\
	Reason: <code>{reason}</code>
{self.divider()}

{self.section_title("contract")}
<code>{address}</code>
"""

    def build_trade_brief_message(self, event):
        """Short live-trade notice for the ignition summary group chat."""

        event_type = str(
            event.get("type") or "update"
        ).lower().replace("live_", "")

        symbol = self.html(
            str(event.get("symbol") or "?").upper()
        )
        address = str(event.get("address") or "")
        short_addr = (
            address[:8] + "…" + address[-4:]
            if len(address) > 14
            else address
        )

        price_multiple = self.safe_float(
            event.get("price_multiple"), 0
        )
        pnl_pct = (price_multiple - 1) * 100 if price_multiple > 0 else 0
        pnl_sign = "+" if pnl_pct >= 0 else ""

        chain = str(event.get("chain") or "solana")
        defined_url = (
            self.build_defined_url(address, chain)
            if address
            else ""
        )

        if event_type == "entry":
            route_raw = (
                event.get("entry_route")
                or event.get("alert_route")
                or ""
            )
            route_label = (
                self.html(route_raw.replace("_", " "))
                if route_raw
                else ""
            )
            header = f"🟢 Bought <b>${symbol}</b>"
            if route_label:
                header += f" — {route_label}"

        elif event_type == "scale_out":
            scaled_pct = int(
                self.safe_float(event.get("scaled_out_pct"), 0) * 100
            )
            header = (
                f"📤 Scaled <b>${symbol}</b>"
                f" {pnl_sign}{price_multiple:.2f}x"
            )
            if scaled_pct:
                header += f" ({scaled_pct}% out)"

        elif event_type in ("stop", "close"):
            reason = str(event.get("reason") or "").replace("_", " ")
            label = "Stop" if event_type == "stop" else "Closed"
            header = (
                f"🔴 {label} <b>${symbol}</b>"
                f" {pnl_sign}{price_multiple:.2f}x"
            )
            if reason:
                header += f" — {reason}"

        else:
            header = f"📌 <b>${symbol}</b> {event_type.upper()}"
            if price_multiple > 0:
                header += f" {pnl_sign}{price_multiple:.2f}x"

        lines = [header, f"<code>{address}</code>"]

        if defined_url:
            lines.append(
                f'<a href="{self.html(defined_url)}">{short_addr} ↗ Definitive</a>'
            )

        return "\n".join(lines)

    async def send_position_event(
        self,
        event
    ):

        if not ORGANIC_TELEGRAM_ALERTS_ENABLED:
            return 0

        if not POSITION_TELEGRAM_ENABLED:
            return 0

        if not event:
            return 0

        address = event.get("address", "")
        symbol = event.get("symbol", "UNKNOWN")
        is_live = (
            event.get("live_execution_submitted")
            or event.get("live_execution_entry_submitted")
        )

        # Live-only delivery: suppress paper-only position events (entries,
        # scale-outs, closes, risk notices) from the chat. Only trades that
        # actually executed live are alerted, so the message reflects real
        # money — not paper accounting.
        if not is_live:
            return 0

        # Full detailed message → main chat only (always)
        payload = {
            "chat_ids": self.chat_ids or [self.chat_id],
            "text": self.build_position_event_message(event),
            "parse_mode": "HTML",
            "reply_markup": self.build_position_event_keyboard(
                address,
                symbol,
                chain=event.get("chain", "solana")
            ),
            "disable_web_page_preview": True
        }

        await self.send_message(
            payload,
            f"Position {event.get('type', 'update')} sent for {symbol}"
        )

        # Brief notice → summary group for live trades only
        if is_live and self.summary_chat_ids:
            brief_payload = {
                "chat_ids": self.summary_chat_ids,
                "text": self.build_trade_brief_message(event),
                "parse_mode": "HTML",
                "disable_web_page_preview": True
            }
            await self.send_message(
                brief_payload,
                f"Trade brief sent for {symbol}"
            )

        return 1

    def build_live_execution_event_message(
        self,
        event
    ):

        event_type = str(
            event.get("type", "update")
            or "update"
        ).upper()
        symbol_raw = str(
            event.get("symbol", "UNKNOWN")
            or "UNKNOWN"
        ).strip()
        symbol = self.html(symbol_raw)
        name_raw = str(
            event.get("name", "")
            or ""
        ).strip()
        identity_line = f"<b>${symbol}</b>"

        if (
            name_raw
            and name_raw.upper() != symbol_raw.upper()
        ):
            identity_line = (
                f"<b>{self.html(name_raw)}</b> / {identity_line}"
            )

        if event.get("live_execution_submitted"):
            status = "SUBMITTED"
        elif event.get("live_execution_dry_run"):
            status = "DRY RUN"
        elif event.get("live_execution_retrying"):
            status = "RETRYING"
        elif event.get("live_execution_skipped"):
            status = "FAILED"
        else:
            status = "UPDATE"

        side = str(
            event.get("live_execution_side")
            or (
                "buy"
                if event.get("type") == "entry"
                else "sell"
            )
        ).upper()
        address_value = str(
            event.get("address", "")
            or ""
        )
        address = self.html(address_value)
        short_address = self.html(
            self.short_address(address_value)
        )
        provider = self.html(
            event.get("live_execution_provider", "")
        )
        reason = self.html(
            event.get("live_execution_reason")
            or event.get("reason", "")
        )
        error = self.html(
            event.get("live_execution_error", "")
        )
        qty = self.html(
            event.get("live_execution_qty")
            or event.get("live_execution_sell_tokens")
            or event.get("entry_size_tokens")
            or ""
        )
        order_qty = self.html(
            event.get("live_execution_order_qty", "")
        )
        contra_asset = str(
            event.get("live_execution_contra_asset", "")
            or ""
        )
        contra_label = self.html(
            self.asset_label(contra_asset)
        )
        order_value_usd = self.safe_float(
            event.get("live_execution_order_value_usd"),
            0
        )
        order_id = self.html(
            event.get("live_execution_order_id", "")
        )
        notional_usd = self.safe_float(
            event.get("entry_notional_usd"),
            0
        )
        last_price = self.safe_float(
            event.get("last_price"),
            0
        )
        price_multiple = self.safe_float(
            event.get("price_multiple"),
            0
        )
        impact = self.safe_float(
            event.get("live_execution_quote_price_impact"),
            0
        )
        filled_tokens = self.safe_float(
            event.get("live_execution_filled_target_amount"), 0
        )
        filled_contra = self.safe_float(
            event.get("live_execution_filled_contra_amount"), 0
        )
        avg_fill = self.safe_float(
            event.get("live_execution_average_fill_price"), 0
        )
        entry_route_raw = str(
            event.get("entry_route", "") or ""
        )
        entry_score = int(
            self.safe_float(event.get("entry_score"), 0)
        )

        order_line = ""
        qty_line = ""
        order_qty_line = ""
        value_line = ""
        impact_line = ""
        retry_line = ""
        error_line = ""
        fill_line = ""
        route_line = ""

        if entry_route_raw:
            route_line = (
                f"Route: <b>{self.html(entry_route_raw)}</b> | "
                f"Score: <b>{entry_score}</b>\n"
            )

        if filled_tokens > 0 and avg_fill > 0:
            fill_line = (
                f"Filled: <b>{filled_tokens:,.0f}</b> tokens"
                f" @ <code>${avg_fill:.8f}</code>"
                + (
                    f" | <b>{filled_contra:.4f} SOL</b>"
                    if filled_contra > 0
                    else ""
                )
                + "\n"
            )

        if qty:
            qty_line = f"Qty: <code>{qty}</code>\n"

        if order_qty:
            order_qty_line = (
                f"Order qty: <code>{order_qty}</code> "
                f"{contra_label}\n"
            )

        if order_value_usd:
            value_line = (
                f"Order value: <b>${order_value_usd:,.2f}</b>\n"
            )

        if order_id:
            order_line = f"Order: <code>{order_id}</code>\n"

        if impact:
            impact_line = f"Quote impact: <b>{impact:.2%}</b>\n"

        if event.get("live_execution_retrying"):
            retry_line = (
                "Retry: <b>queued</b> with backoff until submitted\n"
            )

        if error:
            error_line = f"Error: <code>{error}</code>\n"

        return f"""<b>[ LIVE TRADE | {status} ]</b>
{identity_line} <code>{short_address}</code>
{self.divider()}

{self.section_title("execution")}
Event: <b>{event_type}</b>
Side: <b>{side}</b>
Provider: <b>{provider or "n/a"}</b>
{route_line}{fill_line}{qty_line}Notional: <b>${notional_usd:,.2f}</b>
{order_qty_line}{value_line}{order_line}{impact_line}{retry_line}{error_line}Reason: <code>{reason}</code>
{self.divider()}

{self.section_title("market")}
Last: <code>${last_price:.8f}</code> ({price_multiple:.2f}x)
{self.divider()}

{self.section_title("contract")}
<code>{address}</code>
"""

    async def send_live_execution_event(
        self,
        event
    ):

        if not ORGANIC_TELEGRAM_ALERTS_ENABLED:
            return 0

        if (
            not POSITION_TELEGRAM_ENABLED
            or not LIVE_EXECUTION_TELEGRAM_ENABLED
        ):
            return 0

        # When no dedicated live-execution chat is configured the live
        # execution summary is already embedded in the position message.
        # Sending a second message to the same chat in quick succession
        # triggers Telegram's per-chat rate limit and silently drops one
        # of the two messages — usually the position alert.
        if not self.has_dedicated_live_execution_chat:
            return 0

        if not event:
            return 0

        address = event.get("address", "")
        symbol = event.get("symbol", "UNKNOWN")

        payload = {
            "chat_ids": self.live_execution_chat_ids,
            "text": self.build_live_execution_event_message(
                event
            ),
            "parse_mode": "HTML",
            "reply_markup": self.build_position_event_keyboard(
                address,
                symbol,
                chain=event.get("chain", "solana")
            ),
            "disable_web_page_preview": True
        }

        return await self.send_message(
            payload,
            (
                "Live trade "
                f"{event.get('type', 'update')} sent for {symbol}"
            )
        )

    def build_position_status_message(
        self,
        report
    ):

        lines = [
            "<b>[ PAPER TRADING STATUS ]</b>",
            self.divider(),
            "",
            self.section_title("book"),
            (
                f"Open: <b>{report.get('open_count', 0)}</b> | "
                f"Open Equity: ${report.get('total_equity_usd', 0):,.2f}"
            ),
            (
                f"Cash: {report.get('cash_sol', 0):.2f} SOL | "
                "Account Equity: "
                f"${report.get('total_account_equity_usd', 0):,.2f}"
            ),
            (
                f"Open PnL: <b>${report.get('total_pnl_usd', 0):,.2f}</b>"
            ),
            (
                "Rebound watches: "
                f"<b>{report.get('trailing_rebound_watch_count', 0)}</b>"
            ),
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
                    f"{self.html(live_refresh.get('error'))}"
                )

        lines.extend([
            self.divider(),
            "",
            self.section_title("positions")
        ])

        for position in report.get("positions", [])[:8]:
            symbol = self.html(
                position.get("symbol", "UNKNOWN")
            )
            short_address = self.html(
                self.short_address(
                    position.get("address", "")
                )
            )
            live_marker = (
                " live"
                if position.get("live_refreshed")
                else ""
            )
            next_scale = ""

            if position.get("next_scale_multiple"):
                next_scale = (
                    " | Next "
                    f"{position.get('next_scale_multiple', 0):.2f}x/"
                    f"{position.get('next_scale_target_pct', 0):.0%}"
                )

            confirmation = ""

            if position.get("entry_confirmation_score"):
                confirmation = (
                    " | Confirm "
                    f"{position.get('entry_confirmation_score', 0):.0f}"
                )

            lines.extend([
                (
                    f"<b>${symbol}</b> "
                    f"<code>{short_address}</code>"
                ),
                (
                    f"Px {position.get('price_multiple', 0):.2f}x"
                    f"{live_marker} | "
                    f"PnL ${position.get('pnl_usd', 0):,.2f} "
                    f"({position.get('pnl_pct', 0):.1%})"
                ),
                (
                    f"Pressure {position.get('last_pressure', 0):.1f} | "
                    f"Scaled {position.get('scaled_out_pct', 0):.0%} | "
                    f"Stop ${position.get('trailing_stop_price', 0):.8f}"
                    f"{confirmation}"
                    f"{next_scale}"
                )
            ])

        return "\n".join(lines)

    async def send_position_status(
        self,
        report
    ):

        if not ORGANIC_TELEGRAM_ALERTS_ENABLED:
            return 0

        if not POSITION_TELEGRAM_ENABLED:
            return 0

        if not report:
            return 0

        payload = {
            "chat_id": self.chat_id,
            "text": self.build_position_status_message(
                report
            ),
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }

        return await self.send_message(
            payload,
            "Position status sent"
        )

    def build_trending_block(self, details):

        trending_match = details.get("trending_match")
        trending_checked = details.get(
            "trending_checked",
            False
        )

        if trending_match:
            t_sym = self.html(
                trending_match.get("symbol", "")
            )
            t_name = self.html(
                trending_match.get("name", "")
            )
            t_addr = self.html(
                trending_match.get("address", "")
            )
            name_part = (
                f" ({t_name})"
                if t_name and t_name.upper() != t_sym.upper()
                else ""
            )
            return (
                f"\n⚠️ <b>Trending Match: "
                f"${t_sym}{name_part}</b>"
                f"\nMatch CA: <code>{t_addr}</code>"
            )

        if trending_checked:
            return "\n<b>Trending Match: None found</b>"

        return "\n<b>Trending Match: Cache loading...</b>"

    @staticmethod
    def _fmt_abbrev(v, prefix="$"):
        try:
            v = float(v)
        except (TypeError, ValueError):
            return "—"
        if v == 0:
            return "—"
        if v >= 1_000_000_000:
            return f"{prefix}{v / 1_000_000_000:.1f}B"
        if v >= 1_000_000:
            return f"{prefix}{v / 1_000_000:.1f}M"
        if v >= 1_000:
            return f"{prefix}{v / 1_000:.1f}K"
        return f"{prefix}{v:,.0f}"

    def _build_signal_pre_block(
        self,
        score,
        raw_score,
        penalty,
        metrics
    ):
        L1, V1, gap, L2, V2 = 6, 7, 4, 4, 7
        sep = "─" * (L1 + V1 + gap + L2 + V2)

        def row(l1, v1, l2="", v2=""):
            return (
                f"{l1:<{L1}}{v1:>{V1}}"
                f"{' ' * gap}"
                f"{l2:<{L2}}{v2:>{V2}}"
            )

        lines = [
            row(
                "Score", f"{score}/150",
                "Age", f"{metrics.age_hours:.1f}h"
            ),
            row(
                "Raw", str(raw_score),
                "FDV", self._fmt_abbrev(metrics.fdv)
            ),
            sep,
            row(
                "Pen", self.format_penalty(penalty),
                "Liq", self._fmt_abbrev(
                    getattr(metrics, "liquidity", 0)
                )
            ),
        ]

        return "<pre>" + "\n".join(lines) + "</pre>"

    def _build_flow_pre_block(
        self,
        metrics,
        buy_sell_ratio,
        h1_buy_sell_ratio,
        volume_liquidity_ratio,
        h1_volume_liquidity_ratio,
        price_jump,
        h1_txns,
        price_change_1h,
        price_change_6h
    ):
        L, V = 4, 7
        sep = "─" * (L + V + 2 + V)

        def row(label, v5="", v1=""):
            return f"{label:<{L}}{v5:>{V}}  {v1:>{V}}"

        lines = [
            row("", "5m", "1h"),
            sep,
            row(
                "Vol",
                self._fmt_abbrev(metrics.volume_5m),
                self._fmt_abbrev(metrics.volume_1h)
            ),
            sep,
            row(
                "B/S",
                f"{metrics.buys_5m}/{metrics.sells_5m}",
                f"{metrics.buys_1h}/{metrics.sells_1h}"
            ),
            row(
                "x",
                f"{buy_sell_ratio:.1f}x",
                f"{h1_buy_sell_ratio:.1f}x"
            ),
            sep,
            row(
                "V/L",
                f"{volume_liquidity_ratio:.1%}",
                f"{h1_volume_liquidity_ratio:.1%}"
            ),
            row("Imp", f"{price_jump:.2f}x"),
            sep,
            row("Txn", v1=str(int(h1_txns)) if h1_txns else "—"),
            row("Δ1h", v1=f"{price_change_1h:+.1f}%"),
            row("Δ6h", v1=f"{price_change_6h:+.1f}%"),
        ]

        return "<pre>" + "\n".join(lines) + "</pre>"

    def build_compact_ignition_message(
        self,
        metrics,
        score,
        details,
        tokenscan_command,
        defined_url=None,
        lineage_text=""
    ):

        raw_score = details.get(
            "raw_score",
            score
        )
        penalty = details.get(
            "penalty",
            0
        )
        alert_route = details.get(
            "alert_route",
            "none"
        )
        price_jump = details.get(
            "price_jump",
            0
        )
        volume_liquidity_ratio = details.get(
            "volume_liquidity_ratio",
            0
        )
        buys_5m = self.safe_float(getattr(metrics, "buys_5m", 0), 0)
        sells_5m = self.safe_float(getattr(metrics, "sells_5m", 0), 0)
        buys_1h = self.safe_float(getattr(metrics, "buys_1h", 0), 0)
        sells_1h = self.safe_float(getattr(metrics, "sells_1h", 0), 0)

        buy_sell_ratio = details.get(
            "buy_sell_ratio",
            (
                buys_5m
                / max(sells_5m, 1.0)
            )
        )
        h1_buy_sell_ratio = details.get(
            "h1_buy_sell_ratio",
            (
                buys_1h
                / max(sells_1h, 1.0)
            )
        )
        h1_volume_liquidity_ratio = details.get(
            "h1_volume_liquidity_ratio",
            0
        )
        h1_txns = details.get(
            "h1_txns",
            (
                buys_1h
                + sells_1h
            )
        )
        price_change_1h = details.get(
            "price_change_1h",
            0
        )
        price_change_6h = details.get(
            "price_change_6h",
            0
        )
        quality_tag = details.get(
            "quality_tag",
            "standard"
        )
        news_items = details.get("news_items") or []
        narrative_items = details.get("narrative_items") or []
        narrative_special = details.get(
            "narrative_special_alert",
            False
        )
        is_recall = details.get(
            "is_recall",
            False
        )

        alert_title = "IGNITION ALERT"

        if quality_tag == "hyperevm_slow_cook":
            quality_tag = "hyperevm_ignition"

        if alert_route == "hyperevm_slow_cook":
            alert_route = "hyperevm_ignition"

        if (
            quality_tag == "low_fdv_accumulation"
            or alert_route
            in (
                "low_fdv_accumulation",
                "bonding_low_fdv_accumulation"
            )
        ):
            alert_title = "LOW-FDV ACCUMULATION"

        if alert_route == "hyperevm_ignition":
            alert_title = "HYPEREVM IGNITION"

        if details.get("metadata_special_alert"):
            alert_title = "CTO METADATA CHANGE"
        elif narrative_special:
            alert_title = "REAL-WORLD NARRATIVE"
        elif news_items:
            alert_title = "NEWS CATALYST"

        address = self.html(metrics.address)
        chain = self.html(metrics.chain.upper())
        lifecycle = self.html(metrics.lifecycle)

        name_raw = self.token_name(metrics)
        symbol_raw = str(
            getattr(metrics, "symbol", "") or "UNKNOWN"
        ).strip()
        has_name = (
            name_raw
            and name_raw.upper() != symbol_raw.upper()
        )
        name_line = (
            f"Name: <b>{self.html(name_raw)}</b>\n"
            if has_name
            else ""
        )
        ticker_chain_line = (
            f"<b>${self.html(symbol_raw)}</b> · {chain}"
        )
        route = self.html(alert_route)
        quality = self.html(route_display_name(alert_route))
        trade_quality = self.html(
            details.get("trade_quality_label", "neutral")
        )
        trade_quality_score = int(
            details.get("trade_quality_score", 0)
        )
        relative_strength_pct = float(
            details.get("relative_strength_pct", 0)
        )

        confidence_history = details.get(
            "confidence_history",
            {}
        )
        confidence_lines = []

        if confidence_history:
            score_history = " -> ".join(
                str(score_item)
                for score_item in confidence_history.get(
                    "scores",
                    []
                )
            )
            pressure_history = " -> ".join(
                f"{pressure_item:.1f}"
                for pressure_item in confidence_history.get(
                    "pressures",
                    []
                )
            )

            if score_history or pressure_history:
                confidence_lines.extend([
                    f"Score Trend: {self.html(score_history)}",
                    f"Pressure Trend: {self.html(pressure_history)}"
                ])

        route_confidence_tier = details.get(
            "route_confidence_tier"
        )

        if route_confidence_tier:
            route_adjustment = self.safe_float(
                details.get("route_outcome_adjustment"),
                0
            )
            route_alerts = int(
                self.safe_float(
                    details.get("route_outcome_alerts"),
                    0
                )
            )
            route_hit_rate = self.safe_float(
                details.get("route_outcome_hit_2x_rate"),
                0
            )
            route_false_rate = self.safe_float(
                details.get(
                    "route_outcome_false_positive_rate"
                ),
                0
            )
            route_shadow = (
                " shadow"
                if details.get("route_outcome_shadowed")
                else ""
            )

            confidence_lines.append(
                "Route Outcome: "
                f"{self.html(route_confidence_tier)}{route_shadow} "
                f"({route_adjustment:+.1f}) | "
                f"n={route_alerts} | "
                f"2x {route_hit_rate:.1%} | "
                f"false+ {route_false_rate:.1%}"
            )

        if details.get("entry_confirmation_enabled"):
            confirmation_score = self.safe_float(
                details.get("entry_confirmation_score"),
                0
            )
            confirmation_min = self.safe_float(
                details.get("entry_confirmation_min_score"),
                0
            )
            confirmation_scans = int(
                self.safe_float(
                    details.get("entry_confirmation_confirmed_scans"),
                    0
                )
            )
            confirmation_required = int(
                self.safe_float(
                    details.get("entry_confirmation_required_scans"),
                    0
                )
            )
            confirmation_note = (
                "shadow "
                if details.get("entry_confirmation_shadow_mode")
                else ""
            )
            confirmation_state = (
                "ready"
                if details.get("entry_confirmation_ready")
                else self.html(
                    details.get(
                        "entry_confirmation_reason",
                        "waiting"
                    )
                )
            )
            confidence_lines.append(
                "Entry Confirm: "
                f"{confirmation_note}{confirmation_score:.0f}/"
                f"{confirmation_min:.0f} | "
                f"{confirmation_scans}/{confirmation_required} | "
                f"{confirmation_state}"
            )

        recall_note = ""
        if is_recall:
            recall_reason = details.get("recall_override_reason")
            if recall_reason:
                recall_note = (
                    "\n↩ <b>Recall</b> — "
                    f"cooldown override ({self.html(recall_reason)})"
                )
            else:
                recall_note = (
                    "\n↩ <b>Recall</b> — after cooldown"
                )

        if defined_url is None:
            defined_url = self.build_defined_url(metrics)

        criteria_block = self.build_tag_criteria_block(
            quality_tag,
            alert_route
        )
        play_text = self.build_play_text(quality_tag)
        news_block = self.build_news_context_block(news_items)
        narrative_block = self.build_narrative_context_block(
            details,
            narrative_items
        )
        metadata_block = self.build_metadata_context_block(details)

        confidence_block = ""
        if confidence_lines:
            confidence_block = "\n" + "\n".join(confidence_lines)

        migration_block = self.build_migration_fdv_lines(metrics)
        migration_block = (
            f"\n{migration_block}"
            if migration_block
            else ""
        )

        lineage_block = ""
        if lineage_text:
            lineage_block = "\n\n" + lineage_text.strip()

        trending_block = self.build_trending_block(details)

        hc_flag = (
            "\n⚡ <b>FAST-EXIT MODE</b> — partials at 1.5x, "
            "DB: 43.7% close below entry at 1h."
            if alert_route == "bonding_momentum_high_conviction"
            else ""
        )

        _entry_reason_labels = {
            "entry_ready":                   ("✅", "entry ready"),
            "position_already_open":         ("📌", "position already open"),
            "max_open_positions_reached":    ("🚫", "max positions open"),
            "score_below_threshold":         ("📉", "score below threshold"),
            "penalty_too_high":              ("⚠️", "penalty too high"),
            "5m_price_change_too_hot":       ("🌡", "5m price too hot"),
            "5m_price_change_below_min":     ("❄️", "5m price too weak"),
            "1h_price_change_below_min":     ("📉", "1h price too weak"),
            "1h_volume_below_min":           ("💧", "1h volume too low"),
            "impulse_below_min":             ("💤", "impulse too low"),
            "impulse_too_hot":               ("🌡", "impulse too hot"),
            "critical_fields_missing":       ("❓", "flow data missing"),
            "fdv_above_entry_max":           ("💰", "FDV too high"),
            "fdv_below_entry_min":           ("💰", "FDV too low"),
            "token_reentry_hourly_limit":    ("🔁", "reentry limit"),
            "alert_not_eligible":            ("⛔", "alert not eligible"),
        }

        entry_precheck = self.html(
            details.get("position_entry_precheck_reason") or ""
        )
        entry_status_line = ""

        if entry_precheck:
            icon, label = _entry_reason_labels.get(
                entry_precheck,
                ("⛔", entry_precheck.replace("_", " "))
            )
            if entry_precheck == "entry_ready":
                entry_status_line = f"\nEntry: {icon} <b>{label}</b>"
            else:
                entry_status_line = (
                    f"\nEntry: {icon} <b>blocked</b> — {label}"
                )

        flow_pre_block = self._build_flow_pre_block(
            metrics,
            buy_sell_ratio,
            h1_buy_sell_ratio,
            volume_liquidity_ratio,
            h1_volume_liquidity_ratio,
            price_jump,
            h1_txns,
            price_change_1h,
            price_change_6h
        )

        # Pool reserve line (GeckoTerminal only — absent for DexScreener pairs).
        _base_trend = details.get("base_reserve_trend")
        _rpc = details.get("reserve_price_confirmed", False)
        _cpd = details.get("curve_token_pct_delta")

        if _base_trend is not None:
            _trend_sign = "▼" if _base_trend < 0 else "▲"
            _trend_label = (
                "net buy" if _base_trend < -0.02
                else "net sell" if _base_trend > 0.02
                else "neutral"
            )
            _confirmed = " ✓" if _rpc else ""
            _curve_part = (
                f" | curve dump {_cpd:.1%}"
                if _cpd is not None and _cpd > 0.02
                else ""
            )
            reserve_line = (
                f"\nPool: {_trend_sign}{abs(_base_trend):.1%} "
                f"({_trend_label}){_confirmed}{_curve_part}"
            )
        else:
            reserve_line = ""

        signal_pre = self._build_signal_pre_block(
            score, raw_score, penalty, metrics
        )

        return f"""🔥 <b>{alert_title}</b> — {quality}{recall_note}

{name_line}{ticker_chain_line}
CA: <code>{address}</code>{trending_block}

{self.telegram_section_title("signal")}
{signal_pre}
Route: <code>{route}</code> · Life: <code>{lifecycle}</code>
Trade: {trade_quality} ({self.format_trade_score(trade_quality_score)}) · RS: {relative_strength_pct:+.1f}pp{confidence_block}{hc_flag}{migration_block}{entry_status_line}

{self.telegram_section_title("flow")}
{flow_pre_block}{reserve_line}

{self.telegram_section_title("play")}
<blockquote expandable>{play_text}</blockquote>

{criteria_block}{metadata_block}{narrative_block}{news_block}{lineage_block}
"""

    def build_metadata_context_block(
        self,
        details
    ):

        if not details.get("metadata_change_detected"):
            return ""

        fields = details.get("metadata_changed_fields") or []

        if not fields:
            return ""

        field_text = ", ".join(
            str(field).replace("_", " ")
            for field in fields[:8]
        )
        flow_confirmed = (
            "YES"
            if details.get("metadata_change_flow_confirmed")
            else "NO"
        )
        buy_sell_volume_ratio = self.safe_float(
            details.get("metadata_change_buy_sell_volume_ratio"),
            0
        )

        content = (
            f"Changed: <b>{self.html(field_text)}</b>\n"
            f"Flow confirmed: <b>{flow_confirmed}</b>\n"
            f"5m buy/sell ratio: <b>{buy_sell_volume_ratio:.2f}x</b>"
        )

        return (
            "\n\n"
            f"{self.telegram_section_title('metadata change')}\n"
            f"<blockquote expandable>{content}</blockquote>"
        )

    def build_news_context_block(
        self,
        news_items
    ):

        if not news_items:
            return ""

        lines = []

        for item in news_items[:3]:
            title = self.html(
                item.get("title", "")
            )
            source = self.html(
                item.get("source", "news")
            )
            url = self.safe_external_url(item.get("url"))
            terms = ", ".join(
                item.get("matched_terms", [])
            )
            terms = self.html(terms)

            if url:
                title_text = (
                    f'<a href="{url}">{title}</a>'
                )
            else:
                title_text = title

            if terms:
                lines.append(
                    f"• {source}: {title_text} ({terms})"
                )
            else:
                lines.append(
                    f"• {source}: {title_text}"
                )

        content = "\n".join(lines)
        return (
            "\n\n"
            f"{self.telegram_section_title('news catalyst')}\n"
            f"<blockquote expandable>{content}</blockquote>"
        )

    def build_narrative_context_block(
        self,
        details,
        narrative_items
    ):

        if not details.get("narrative_special_alert"):
            return ""

        terms = ", ".join(
            details.get("narrative_terms", [])[:8]
        )
        terms = self.html(terms)
        narrative_type = self.html(
            details.get(
                "narrative_type",
                "real_world_narrative"
            )
        )
        narrative_score = int(
            details.get("narrative_score", 0) or 0
        )
        lines = [
            (
                f"Type: <code>{narrative_type}</code> "
                f"| Score: <b>{narrative_score}</b>"
            )
        ]

        if terms:
            lines.append(
                f"Terms: {terms}"
            )

        for item in narrative_items[:3]:
            title = self.html(
                item.get("title", "")
            )
            source = self.html(
                item.get("source", "narrative")
            )
            url = self.safe_external_url(item.get("url"))
            matched_terms = ", ".join(
                item.get("matched_terms", [])[:5]
            )
            matched_terms = self.html(matched_terms)

            if url:
                title_text = (
                    f'<a href="{url}">{title}</a>'
                )
            else:
                title_text = title

            if matched_terms:
                lines.append(
                    f"• {source}: {title_text} ({matched_terms})"
                )
            else:
                lines.append(
                    f"• {source}: {title_text}"
                )

        content = "\n".join(lines)
        return (
            "\n\n"
            f"{self.telegram_section_title('real-world narrative')}\n"
            f"<blockquote expandable>{content}</blockquote>"
        )

    def entry_status_line(self, details):
        """Render the entry-precheck status as a '\\nEntry: ...' suffix
        (empty when no precheck reason). Used by the ignition summary alert
        so the chat shows whether an alert would actually be entered."""

        reason = str(details.get("position_entry_precheck_reason") or "")
        if not reason:
            return ""

        labels = {
            "entry_ready":                ("✅", "entry ready"),
            "position_already_open":      ("📌", "position already open"),
            "max_open_positions_reached": ("🚫", "max positions open"),
            "score_below_threshold":      ("📉", "score below threshold"),
            "penalty_too_high":           ("⚠️", "penalty too high"),
            "5m_price_change_too_hot":    ("🌡", "5m price too hot"),
            "5m_price_change_below_min":  ("❄️", "5m price too weak"),
            "1h_price_change_below_min":  ("📉", "1h price too weak"),
            "1h_volume_below_min":        ("💧", "1h volume too low"),
            "impulse_below_min":          ("💤", "impulse too low"),
            "impulse_too_hot":            ("🌡", "impulse too hot"),
            "critical_fields_missing":    ("❓", "flow data missing"),
            "fdv_above_entry_max":        ("💰", "FDV too high"),
            "fdv_below_entry_min":        ("💰", "FDV too low"),
            "migration_fdv_zone":         ("🌀", "in migration zone"),
            "migration_fdv_above_limit":  ("💰", "migration FDV too high"),
            "token_reentry_hourly_limit": ("🔁", "reentry limit"),
            "alert_not_eligible":         ("⛔", "alert not eligible"),
        }
        icon, label = labels.get(
            reason,
            ("⛔", self.html(reason.replace("_", " ")))
        )
        if reason == "entry_ready":
            return f"\nEntry: {icon} <b>{label}</b>"
        return f"\nEntry: {icon} <b>blocked</b> — {label}"

    def build_ignition_summary_message(
        self,
        metrics,
        score,
        details,
        tokenscan_command
    ):

        alert_route = details.get("alert_route", "none")
        quality_tag = details.get("quality_tag", "standard")
        is_recall = details.get("is_recall", False)
        news_items = details.get("news_items") or []
        narrative_special = details.get("narrative_special_alert", False)

        if quality_tag == "hyperevm_slow_cook":
            quality_tag = "hyperevm_ignition"
        if alert_route == "hyperevm_slow_cook":
            alert_route = "hyperevm_ignition"

        title = "IGNITION"
        if (
            quality_tag == "low_fdv_accumulation"
            or alert_route in (
                "low_fdv_accumulation",
                "bonding_low_fdv_accumulation"
            )
        ):
            title = "LOW-FDV ACCUMULATION"
        if alert_route == "hyperevm_ignition":
            title = "HYPEREVM IGNITION"
        if details.get("metadata_special_alert"):
            title = "CTO METADATA CHANGE"
        elif narrative_special:
            title = "REAL-WORLD NARRATIVE"
        elif news_items:
            title = "NEWS CATALYST"

        quality = self.html(route_display_name(alert_route))
        chain = self.html(metrics.chain.upper())

        name_raw = self.token_name(metrics)
        symbol_raw = str(
            getattr(metrics, "symbol", "") or "UNKNOWN"
        ).strip()
        has_name = (
            name_raw
            and name_raw.upper() != symbol_raw.upper()
        )
        name_line = (
            f"Name: <b>{self.html(name_raw)}</b>\n"
            if has_name
            else ""
        )
        ticker_chain_line = f"<b>${self.html(symbol_raw)}</b> · {chain}"
        address = self.html(metrics.address)

        recall_note = ""
        if is_recall:
            recall_reason = details.get("recall_override_reason")
            recall_note = (
                f"\n↩ <b>Recall</b> — "
                f"cooldown override ({self.html(recall_reason)})"
                if recall_reason
                else "\n↩ <b>Recall</b> — after cooldown"
            )

        trending_block = self.build_trending_block(details)

        # Market grid: score + key market data only (no internal scoring)
        L1, V1, gap, L2, V2 = 6, 7, 4, 4, 7
        sep = "─" * (L1 + V1 + gap + L2 + V2)

        def mrow(l1, v1, l2="", v2=""):
            return (
                f"{l1:<{L1}}{v1:>{V1}}"
                f"{' ' * gap}"
                f"{l2:<{L2}}{v2:>{V2}}"
            )

        market_pre = "<pre>" + "\n".join([
            mrow("Score", str(score), "Age", f"{metrics.age_hours:.1f}h"),
            sep,
            mrow(
                "FDV", self._fmt_abbrev(metrics.fdv),
                "Liq", self._fmt_abbrev(
                    getattr(metrics, "liquidity", 0)
                )
            ),
        ]) + "</pre>"

        # Flow grid (all public market data)
        volume_liquidity_ratio = details.get("volume_liquidity_ratio", 0)
        h1_volume_liquidity_ratio = details.get(
            "h1_volume_liquidity_ratio", 0
        )
        buys_5m = self.safe_float(getattr(metrics, "buys_5m", 0), 0)
        sells_5m = self.safe_float(getattr(metrics, "sells_5m", 0), 0)
        buys_1h = self.safe_float(getattr(metrics, "buys_1h", 0), 0)
        sells_1h = self.safe_float(getattr(metrics, "sells_1h", 0), 0)

        buy_sell_ratio = details.get(
            "buy_sell_ratio",
            buys_5m / max(sells_5m, 1.0)
        )
        h1_buy_sell_ratio = details.get(
            "h1_buy_sell_ratio",
            buys_1h / max(sells_1h, 1.0)
        )
        h1_txns = details.get(
            "h1_txns", buys_1h + sells_1h
        )
        price_change_1h = details.get("price_change_1h", 0)
        price_change_6h = details.get("price_change_6h", 0)
        price_jump = details.get("price_jump", 0)

        flow_pre_block = self._build_flow_pre_block(
            metrics,
            buy_sell_ratio,
            h1_buy_sell_ratio,
            volume_liquidity_ratio,
            h1_volume_liquidity_ratio,
            price_jump,
            h1_txns,
            price_change_1h,
            price_change_6h
        )

        _base_trend = details.get("base_reserve_trend")
        _rpc = details.get("reserve_price_confirmed", False)
        _cpd = details.get("curve_token_pct_delta")

        if _base_trend is not None:
            _trend_sign = "▼" if _base_trend < 0 else "▲"
            _trend_label = (
                "net buy" if _base_trend < -0.02
                else "net sell" if _base_trend > 0.02
                else "neutral"
            )
            _confirmed = " ✓" if _rpc else ""
            _curve_part = (
                f" | curve dump {_cpd:.1%}"
                if _cpd is not None and _cpd > 0.02
                else ""
            )
            reserve_line = (
                f"\nPool: {_trend_sign}{abs(_base_trend):.1%} "
                f"({_trend_label}){_confirmed}{_curve_part}"
            )
        else:
            reserve_line = ""

        defined_url = self.build_defined_url(metrics)
        trade_link = self.build_trade_link(
            metrics.address, metrics.chain
        )
        x_search_link = self.build_x_search_link(metrics)
        links_line = (
            f'<a href="{self.html(defined_url)}">Defined</a>'
            f' · <a href="{self.html(trade_link)}">Definitive</a>'
            f' · <a href="{self.html(x_search_link)}">X</a>'
        )

        return f"""📊 <b>{title}</b> — {quality}{recall_note}

{name_line}{ticker_chain_line}
CA: <code>{address}</code>{trending_block}

{self.telegram_section_title("market")}
{market_pre}

{self.telegram_section_title("flow")}
{flow_pre_block}{reserve_line}{self.entry_status_line(details)}

{self.telegram_section_title("links")}
{links_line}
"""

    async def send_ignition_summary(
        self,
        metrics,
        score,
        details,
        tokenscan_command=None,
        lineage_text=""
    ):

        if not ORGANIC_TELEGRAM_ALERTS_ENABLED:
            return 0

        if not IGNITION_SUMMARY_CHAT_ENABLED:
            return 0

        summary_chat_ids = list(self.summary_chat_ids)

        if not summary_chat_ids:
            return 0

        if tokenscan_command is None:
            tokenscan_command = (
                f"{TOKENSCAN_COMMAND} {metrics.address}"
            )

        defined_url = self.build_defined_url(
            metrics
        )
        message = self.build_compact_ignition_message(
            metrics,
            score,
            details,
            tokenscan_command,
            defined_url=defined_url,
            lineage_text=lineage_text
        )

        payload = {
            "chat_ids": summary_chat_ids,
            "text": message,
            "parse_mode": "HTML",
            "reply_markup": self.build_alert_keyboard(
                metrics,
                defined_url
            ),
            "disable_web_page_preview": True
        }

        return await self.send_message(
            payload,
            f"Ignition summary sent for {metrics.symbol}"
        )

    def _alert_time_ago(self, alert_timestamp, now):

        elapsed = now - float(alert_timestamp or 0)

        if elapsed < 60:
            return "just now"

        if elapsed < 3600:
            return f"{int(elapsed / 60)}m ago"

        hours = int(elapsed / 3600)
        mins = int((elapsed % 3600) / 60)

        if mins:
            return f"{hours}h {mins}m ago"

        return f"{hours}h ago"

    def _route_breakdown_lines(self, alerts):

        route_map = {}

        for alert in alerts:
            if self.safe_float(alert.get("alert_price"), 0) <= 0:
                continue

            route = (
                alert.get("alert_route") or "none"
            )
            route_map.setdefault(route, []).append(alert)

        summaries = []

        for route, rows in route_map.items():
            n = len(rows)
            hit2 = sum(
                1 for a in rows
                if self.safe_float(a.get("max_multiple"), 0) >= 2
            )
            peaks = [
                self.safe_float(a.get("max_multiple"), 0)
                for a in rows
            ]
            avg_peak = sum(peaks) / len(peaks) if peaks else 0
            summaries.append((n, route, hit2, avg_peak))

        summaries.sort(key=lambda t: -t[0])

        lines = []

        for n, route, hit2, avg_peak in summaries:
            if n < 1:
                continue

            rate = hit2 / n
            label = self.html(route.replace("_", " "))
            lines.append(
                f"<code>{label:<22}</code>"
                f" n={n}"
                f"  2x {rate:.0%}"
                f"  avg {avg_peak:.2f}x"
            )

        return lines

    def build_alert_performance_summary_message(
        self,
        report
    ):

        summary = report.get("summary", {})
        window = report.get("window", {})
        alerts = report.get("alerts", [])
        live_refresh = report.get("live_refresh", {})
        now = self.safe_float(window.get("now"), 0) or time.time()

        window_label = (
            self.html(window.get("label") or "all time").upper()
        )

        # ── Summary stats ───────────────────────────────────
        total = summary.get("alerts", 0)
        open_count = summary.get("open_alerts", 0)
        positive_now = summary.get("current_positive", 0)
        win_rate = summary.get("win_rate", 0)
        hit_2x = summary.get("hit_2x", 0)
        hit_4x = summary.get("hit_4x", 0)
        peak_avg = summary.get("peak_multiple_avg", 0)
        curr_avg = summary.get("current_multiple_avg", 0)
        best_peak = summary.get("best_peak_multiple", 0)
        sum_peak = summary.get("sum_peak_multiple", 0)

        # ── Route breakdown ──────────────────────────────────
        route_lines = self._route_breakdown_lines(alerts)
        routes_block = (
            "\n".join(route_lines) if route_lines else "—"
        )

        # ── Top calls ranked by peak then current ────────────
        valid = [
            a for a in alerts
            if self.safe_float(a.get("alert_price"), 0) > 0
        ]
        ranked = sorted(
            valid,
            key=lambda a: (
                self.safe_float(a.get("max_multiple"), 0),
                self.safe_float(a.get("last_price"), 0)
                / max(self.safe_float(a.get("alert_price"), 0), 1e-18)
            ),
            reverse=True
        )
        seen_syms = set()
        deduped = []

        for alert in ranked:
            sym = str(alert.get("symbol", "")).upper()
            if sym not in seen_syms:
                seen_syms.add(sym)
                deduped.append(alert)

        ohlcv_refresh = report.get("ohlcv_refresh", {})

        runner_cards = []

        for alert in deduped[:6]:
            symbol = self.html(
                (alert.get("symbol") or "???").upper()
            )
            alert_price = max(
                self.safe_float(alert.get("alert_price"), 0),
                1e-18
            )
            last_price = self.safe_float(
                alert.get("last_price"), 0
            )
            peak_x = self.safe_float(
                alert.get("max_multiple"), 0
            )
            curr_x = last_price / alert_price if last_price > 0 else 0
            trend = "▲" if curr_x >= 1.0 else "▼"
            route = self.html(
                (alert.get("alert_route") or "none")
                .replace("_", " ")
            )
            ago = (
                self._alert_time_ago(
                    alert.get("alert_timestamp"), now
                )
                if now > 0
                else ""
            )

            last_ts = self.safe_float(
                alert.get("last_timestamp"), 0
            )
            price_stale = (
                now > 0
                and last_ts > 0
                and now - last_ts > 1800
            )
            curr_label = (
                f"~{curr_x:.2f}x⚠"
                if price_stale
                else f"{curr_x:.2f}x"
            )

            if peak_x >= 2.0:
                card_icon = "🔥"
            elif peak_x >= 1.5:
                card_icon = "⚡"
            else:
                card_icon = "📌"

            ago_part = f"  <i>{ago}</i>" if ago else ""
            card = (
                f"{card_icon} <b>${symbol}</b> · <i>{route}</i>{ago_part}\n"
                f"Peak <b>{peak_x:.2f}x</b>  ·  Now {curr_label} {trend}"
            )
            runner_cards.append(card)

        runner_block = (
            "\n\n".join(runner_cards) if runner_cards else "No calls yet."
        )

        # ── Data source footnotes ────────────────────────────
        footnotes = []

        if live_refresh and live_refresh.get("enabled"):
            refreshed = int(
                self.safe_float(live_refresh.get("refreshed"), 0)
            )
            attempted = int(
                self.safe_float(live_refresh.get("attempted"), 0)
            )

            if attempted > 0:
                note = f"Live prices: {refreshed}/{attempted}"

                if live_refresh.get("limited"):
                    note += " (capped)"

                footnotes.append(note)

        if ohlcv_refresh and ohlcv_refresh.get("enabled"):
            updated = int(
                self.safe_float(ohlcv_refresh.get("updated"), 0)
            )
            attempted_o = int(
                self.safe_float(ohlcv_refresh.get("attempted"), 0)
            )

            if attempted_o > 0:
                note = f"OHLCV peaks: {updated}/{attempted_o} updated"
                footnotes.append(note)
            elif ohlcv_refresh.get("error"):
                footnotes.append(
                    f"OHLCV: {ohlcv_refresh['error']}"
                )

        footnote_line = (
            "\n<i>" + "  ·  ".join(footnotes) + "</i>"
            if footnotes
            else ""
        )

        return (
            f"📊 <b>ALERT PERFORMANCE</b> — {window_label}\n"
            f"\n"
            f"{self.telegram_section_title('stats')}\n"
            f"Sent <b>{total}</b>  ·  Open <b>{open_count}</b>  ·  "
            f"Up now <b>{positive_now}</b>  ·  Win rate <b>{win_rate:.0%}</b>\n"
            f"Hits  2x <b>{hit_2x}</b>  ·  4x <b>{hit_4x}</b>\n"
            f"Peak avg <b>{peak_avg:.2f}x</b>  ·  Best <b>{best_peak:.2f}x</b>  ·  "
            f"Now avg {curr_avg:.2f}x\n"
            f"Sum of Xs <b>{sum_peak:.1f}x</b>\n"
            f"\n"
            f"{self.telegram_section_title('by route')}\n"
            f"{routes_block}\n"
            f"\n"
            f"{self.telegram_section_title('top calls')}\n"
            f"{runner_block}"
            f"{footnote_line}"
        )

    async def send_alert_performance_summary(
        self,
        report
    ):

        if not ORGANIC_TELEGRAM_ALERTS_ENABLED:
            return 0

        payload = {
            "chat_ids": self.broadcast_chat_ids,
            "text": self.build_alert_performance_summary_message(
                report
            ),
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }

        return await self.send_message(
            payload,
            "Alert performance summary sent"
        )

    async def send_llm_pattern_report(
        self,
        report_text,
        parse_mode="HTML"
    ):

        if not ORGANIC_TELEGRAM_ALERTS_ENABLED:
            return 0

        if not report_text:
            return 0

        message = report_text

        if not parse_mode:
            message = self.html(report_text)

        payload = {
            "chat_ids": self.broadcast_chat_ids,
            "text": message,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True
        }

        return await self.send_message(
            payload,
            "LLM pattern report sent"
        )

    async def send_message(
        self,
        payload,
        success_message
    ):

        payload = dict(payload)
        requested_chat_ids = payload.pop(
            "chat_ids",
            None
        )

        if requested_chat_ids is None:
            payload_chat_id = str(
                payload.get("chat_id", "")
            ).strip()

            if payload_chat_id:
                if (
                    self.chat_id
                    and payload_chat_id == str(self.chat_id)
                    and self.chat_ids
                ):
                    requested_chat_ids = self.chat_ids
                else:
                    requested_chat_ids = [payload_chat_id]
            else:
                requested_chat_ids = self.chat_ids

        chat_ids = [
            str(chat_id).strip()
            for chat_id in requested_chat_ids
            if str(chat_id).strip()
        ]

        if not TELEGRAM_BOT_TOKEN or not chat_ids:
            return 0

        sent_count = 0

        try:
            async with aiohttp.ClientSession() as session:

                for chat_id in chat_ids:

                    send_payload = dict(payload)
                    send_payload["chat_id"] = chat_id

                    try:
                        if await self.send_message_to_chat(
                            session,
                            send_payload
                        ):
                            sent_count += 1
                    except Exception as send_exc:
                        print(
                            f"Telegram send to chat {chat_id} error: {send_exc}"
                        )

            if sent_count:

                if len(chat_ids) == 1:
                    print(success_message)
                else:
                    print(
                        f"{success_message} "
                        f"({sent_count}/{len(chat_ids)} chats)"
                    )

        except Exception as e:

            print(
                f"Telegram send error: {e}"
            )

            return 0

        return sent_count

    async def send_message_to_chat(
        self,
        session,
        payload
    ):

        payload_chat_id = str(
            payload.get("chat_id", "")
        )

        async with session.post(
            f"{self.base_url}/sendMessage",
            json=payload
        ) as response:

            if response.status == 200:
                return True

            try:
                data = await response.json(
                    content_type=None
                )
            except Exception:
                data = {}

            migrate_to_chat_id = (
                data.get("parameters", {})
                .get("migrate_to_chat_id")
            )

            if migrate_to_chat_id:

                migrated_chat_id = str(
                    migrate_to_chat_id
                )

                if payload_chat_id == str(self.chat_id):
                    self.chat_id = migrated_chat_id

                self.chat_ids = [
                    migrated_chat_id
                    if str(chat_id) == payload_chat_id
                    else chat_id
                    for chat_id in self.chat_ids
                ]

                payload[
                    "chat_id"
                ] = migrated_chat_id

                print(
                    "Telegram group migrated "
                    "to supergroup; retrying "
                    "with the new chat id."
                )

                async with session.post(
                    f"{self.base_url}/sendMessage",
                    json=payload
                ) as retry_response:

                    if retry_response.status == 200:
                        return True

                    print(
                        "Telegram retry failed "
                        f"with status "
                        f"{retry_response.status}"
                    )

                return False

            description = data.get(
                "description",
                "unknown Telegram error"
            )

            print(
                f"Telegram error: {description}"
            )

            return False

    async def send_ignition_alert(
        self,
        metrics,
        score,
        breakdown,
        details,
        lineage_text=""
    ):

        if not ORGANIC_TELEGRAM_ALERTS_ENABLED:
            return 0

        defined_url = self.build_defined_url(
            metrics
        )
        tokenscan_command = (
            f"{TOKENSCAN_COMMAND} {metrics.address}"
        )
        message = self.build_compact_ignition_message(
            metrics,
            score,
            details,
            tokenscan_command,
            defined_url=defined_url,
            lineage_text=lineage_text
        )

        payload = {
            "chat_id": self.chat_id,
            "text": message,
            "parse_mode": "HTML",
            "reply_markup": self.build_alert_keyboard(
                metrics,
                defined_url
            ),
            "disable_web_page_preview": True
        }

        sent_count = await self.send_message(
            payload,
            f"Ignition alert sent for {metrics.symbol}"
        )

        if sent_count:
            await self.tokenscan_user_trigger.send_contract_scan(
                metrics.address
            )

        return sent_count

    async def send_ticker_lineage(
        self,
        metrics,
        lineage_text
    ):

        if not ORGANIC_TELEGRAM_ALERTS_ENABLED:
            return 0

        payload = {
            "chat_id": self.chat_id,
            "text": lineage_text,
            "parse_mode": "HTML",
            "reply_markup": self.build_alert_keyboard(
                metrics,
                self.build_defined_url(metrics)
            ),
            "disable_web_page_preview": True
        }

        return await self.send_message(
            payload,
            f"Ticker lineage sent for {metrics.symbol}"
        )
