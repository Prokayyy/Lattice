#!/usr/bin/env python3
"""Relay Lattice entry and late-moon alerts into target Telegram groups.

This is intentionally separate from the bot sender. It uses a normal Telegram
user session via Telethon so third-party call loggers that ignore bot-origin
messages can see the reposted call.

Default mode is "forward" because some call loggers key off Telegram forwarded
metadata. Set LATTICE_RELAY_MODE=copy to repost fresh plain text instead.

Run:
    env/bin/python tools/lattice_user_relay.py

First run will ask for the relay account phone/login code unless an existing
session file is already present.
"""
import asyncio
import html
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import quote

sys.path.insert(
    0,
    str(Path(__file__).resolve().parent.parent)
)

import config

try:
    from telethon import TelegramClient, events
except ImportError as exc:
    raise SystemExit(
        "Telethon is not installed. Run: env/bin/pip install -r requirements.txt"
    ) from exc


ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "data" / "lattice_relay_state.json"
PUBLIC_MEDIA_CAPTION_MAX_CHARS = 1024
SOLANA_ADDRESS_RE = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b")
# Both alert generations are matched: the pre-2026-06-10 format
# ("entry zone: 1.3e-05 – 1.4e-05" / "invalidation: 9.3e-06" / "$SYM" line)
# and the redesigned one ("🎯 zone $0.0₄1331 → ..." / "🛑 stop $0.0₅9317 (...)"
# / "ENTRY — $SYM" or "ENTRY SIGNAL — $SYM" header).
SYMBOL_RE = re.compile(r"^\$([^\s]+)")
# Capture the full token label after "$" — symbols can contain spaces
# (e.g. "$MERRY CAT"). The header line ends at the newline; "<" guards the
# HTML-bearing case (e.g. "$SYM</b>"). clean_public_symbol collapses whitespace.
HEADER_SYMBOL_RE = re.compile(
    r"\bENTRY(?:\s+SIGNAL)?\s*[—–-]\s*\$([^<\n]+)",
    re.IGNORECASE
)
LATE_MOON_RE = re.compile(
    r"\$([^\s<]+)\s+late[- ]moon:\s*(\d+)x\b",
    re.IGNORECASE
)
PROBABILITY_CHART_RE = re.compile(
    r"^P\((?P<label>[^)]+)\)\s+"
    r"(?P<value>[+-]?\d+(?:\.\d+)?%)"
    r"(?:\s+(?P<bars>.*))?$",
    re.IGNORECASE
)
PROBABILITY_BAR_CHARS = set("▰▱▬▭■□")
ENTRY_ZONE_RE = re.compile(
    r"(?:entry\s+zone:|🎯\s*zone)\s*([^\n]+)", re.IGNORECASE
)
INVALIDATION_RE = re.compile(
    r"(?:invalidation:|🛑\s*stop)\s*([^\n]+)", re.IGNORECASE
)
_SUBSCRIPTS = "₀₁₂₃₄₅₆₇₈₉"


def expand_subscript_prices(text):
    """$0.0₄1331 -> 0.00001331 so third-party call loggers can parse values."""

    def _expand(match):
        zeros = "".join(
            str(_SUBSCRIPTS.index(ch)) for ch in match.group(1)
        )
        return f"0.{'0' * int(zeros)}{match.group(2)}"

    return re.sub(
        rf"\$?0\.0([{_SUBSCRIPTS}]+)(\d+)",
        _expand,
        str(text or "")
    ).replace("$", "")


def env_flag(name, default=False):
    return os.getenv(name, str(default)).strip().lower() in {
        "1",
        "true",
        "yes",
        "on"
    }


def env_text(name, default=""):
    return os.getenv(name, default).strip()


def env_int(name, default=0):
    value = env_text(name)

    if not value:
        return default

    return int(value)


def parse_chat_ref(value):
    text = str(value or "").strip()

    if not text:
        return None

    if text.startswith("@"):
        return text

    try:
        return int(text)
    except ValueError:
        return text


def parse_chat_refs(value):
    refs = []

    for raw in str(value or "").replace(";", ",").split(","):
        ref = parse_chat_ref(raw)

        if ref is not None and ref not in refs:
            refs.append(ref)

    return refs


def load_state():
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {
            "relayed": []
        }


def save_state(state):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state["relayed"] = list(dict.fromkeys(state.get("relayed", [])))[-1000:]
    STATE_PATH.write_text(
        json.dumps(state, indent=2, sort_keys=True),
        encoding="utf-8"
    )


def normalize_alert(text):
    """Preserve the visible call content for copy mode."""
    lines = [
        line.rstrip()
        for line in str(text or "").splitlines()
    ]
    cleaned = []

    for line in lines:
        line = line.strip()

        if line:
            cleaned.append(line)

    return "\n".join(cleaned).strip()


def relay_kind(text, require_lattice_tag=True):
    if not text:
        return ""

    if LATE_MOON_RE.search(text) and SOLANA_ADDRESS_RE.search(text):
        return "late_moon"

    if require_lattice_tag and "[LATTICE]" not in text:
        return ""

    if "ENTRY SIGNAL" not in text and not HEADER_SYMBOL_RE.search(text):
        return ""

    if SOLANA_ADDRESS_RE.search(text):
        return "entry"

    return ""


def should_relay(text, require_lattice_tag=True):
    return bool(relay_kind(text, require_lattice_tag=require_lattice_tag))


def extract_contract_address(text):
    match = SOLANA_ADDRESS_RE.search(text or "")

    if not match:
        return ""

    return match.group(0)


def clean_public_value(text):
    cleaned = expand_subscript_prices(text)
    cleaned = (
        cleaned
        .replace("→", " to ")
        .replace("–", " to ")
        .replace("—", " to ")
        .strip()
    )
    # strip trailing annotations like "(-30% from zone low)"
    cleaned = re.sub(r"\([^)]*\)\s*$", "", cleaned)
    cleaned = re.sub(r"\s+\?\s+", " to ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def html_public_value(text):
    return html.escape(str(text or ""), quote=False)


def clean_public_symbol(text):
    cleaned = re.sub(r"<[^>]+>", "", str(text or ""))
    cleaned = cleaned.strip().strip("$")
    return re.sub(r"\s+", " ", cleaned).strip()


def clean_public_text(text):
    cleaned = re.sub(r"<[^>]+>", "", str(text or ""))
    return re.sub(r"\s+", " ", cleaned).strip()


def clean_probability_label(text):
    cleaned = clean_public_text(text)
    cleaned = cleaned.replace("≥", ">=").replace("≤", "<=")
    return cleaned.replace(" ", "")


def extract_probability_bars(text):
    parts = []

    for token in clean_public_text(text).split():
        if token and all(ch in PROBABILITY_BAR_CHARS for ch in token):
            parts.append(token)
        else:
            break

    return " ".join(parts)


def find_context_line(text, predicate):
    for line in normalize_alert(text).splitlines():
        line = line.strip()

        if line and predicate(line, line.lower()):
            return line

    return ""


def strip_context_label(line, *labels):
    cleaned = str(line or "").strip()

    for prefix in ("🧠", "🐦", "📰", "𝕏", "🧷", "🔥"):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):].strip()

    lowered = cleaned.lower()
    for label in labels:
        if lowered.startswith(label.lower()):
            cleaned = cleaned[len(label):].strip()
            break

    cleaned = cleaned.strip(" :|-")
    return re.sub(r"\s+", " ", cleaned).strip()


def extract_smart_context(text):
    line = find_context_line(
        text,
        lambda line, lower: line.startswith("🧠") or "smart wallet" in lower
    )
    return strip_context_label(line)


def extract_x_context(text):
    line = find_context_line(
        text,
        lambda line, lower: (
            line.startswith(("🐦", "𝕏"))
            or "search ca on x" in lower
            or "tw_mentions" in lower
        )
    )
    cleaned = strip_context_label(line)
    cleaned = re.sub(
        r"(?:[·|]\s*)?search\s+ca\s+on\s+x\b",
        "",
        cleaned,
        flags=re.IGNORECASE
    )
    return cleaned.strip(" ·|")


def extract_bundle_context(text):
    """Solana Tracker bundle headline (🧷 risk line), or '' when absent.

    Matches the top-level headline (held %, wallet count, insiders/snipers/dev,
    RUGGED, or an 'unknown' failed-check label) and skips the 🧷 cluster line,
    whose wallet links are dead once HTML is stripped for the public copy."""
    line = find_context_line(
        text,
        lambda line, lower: line.startswith("🧷") and "cluster:" not in lower
    )
    return strip_context_label(line, "bundle")


def extract_vibe_context(text):
    """OKX vibe (X/Twitter hotness) headline (🔥 line), or '' when absent."""
    line = find_context_line(
        text,
        lambda line, lower: line.startswith("🔥") or lower.startswith("vibe ")
    )
    return strip_context_label(line, "vibe")


def extract_narrative_context(text):
    line = find_context_line(
        text,
        lambda line, lower: (
            line.startswith("📰")
            or lower.startswith("narrative:")
        )
    )
    return strip_context_label(line, "narrative")


def extract_probability_chart(text):
    for line in normalize_alert(text).splitlines():
        cleaned = clean_public_text(line)
        match = PROBABILITY_CHART_RE.match(cleaned)

        if match:
            return {
                "label": f"P({clean_probability_label(match.group('label'))})",
                "value": match.group("value").strip(),
                "bars": extract_probability_bars(match.group("bars") or "")
            }

    return {}


def build_x_search_url(symbol, ca):
    terms = []

    if symbol:
        terms.append(f'"${symbol}"')

    if ca:
        terms.append(f'"{ca}"')

    query = " OR ".join(terms) or ca
    return f"https://x.com/search?q={quote(query, safe='')}&f=live"


def public_entry_message(text, include_context=True):
    text = str(text or "")
    symbol = ""

    header_match = HEADER_SYMBOL_RE.search(text)
    if header_match:
        symbol = clean_public_symbol(header_match.group(1))
    else:
        for line in text.splitlines():
            match = SYMBOL_RE.search(line.strip())

            if match:
                symbol = clean_public_symbol(match.group(1))
                break

    ca = extract_contract_address(text)
    entry_match = ENTRY_ZONE_RE.search(text)
    invalidation_match = INVALIDATION_RE.search(text)
    entry_zone = clean_public_value(entry_match.group(1)) if entry_match else ""
    invalidation = (
        clean_public_value(invalidation_match.group(1))
        if invalidation_match
        else ""
    )

    if not (symbol and ca and entry_zone and invalidation):
        return ""

    smart_context = extract_smart_context(text)
    x_context = extract_x_context(text)
    narrative_context = extract_narrative_context(text)
    probability_chart = extract_probability_chart(text)
    x_url = html.escape(build_x_search_url(symbol, ca), quote=True)

    lines = [
        (
            f"<b>Ticker:</b> <code>${html_public_value(symbol)}</code> "
            f"<b>Name:</b> {html_public_value(symbol)} "
            f"<b>CA:</b> <code>{html_public_value(ca)}</code>"
        ),
        "💎 <b>LATTICE ENTRY</b>",
    ]

    if probability_chart.get("value"):
        probability_parts = [
            probability_chart["value"]
        ]

        if probability_chart.get("bars"):
            probability_parts.append(probability_chart["bars"])

        lines.append(
            f"<b>{html_public_value(probability_chart['label'])}:</b> "
            f"<code>{html_public_value(' '.join(probability_parts))}</code>"
        )

    lines.extend([
        f"<b>Entry:</b> <code>{html_public_value(entry_zone)}</code>",
        f"<b>Invalidation:</b> <code>{html_public_value(invalidation)}</code>",
    ])

    bundle_context = extract_bundle_context(text)
    if bundle_context:
        lines.append(f"🧷 <b>Bundle:</b> {html_public_value(bundle_context)}")

    vibe_context = extract_vibe_context(text)
    if vibe_context:
        lines.append(f"🔥 <b>Vibe:</b> {html_public_value(vibe_context)}")

    if not include_context:
        return "\n".join(lines)

    if smart_context or x_context or narrative_context:
        lines.append("")

    if smart_context:
        lines.append(
            f"🧠 <b>Smart wallets:</b> {html_public_value(smart_context)}"
        )

    x_parts = []
    if x_context:
        x_parts.append(html_public_value(x_context))
    x_parts.append(f'<a href="{x_url}">search CA on X</a>')
    lines.append(f"𝕏 <b>X:</b> {' · '.join(x_parts)}")

    if narrative_context:
        lines.append(
            f"📰 <b>Narrative:</b> {html_public_value(narrative_context)}"
        )

    # The current Lattice alert only carries one token label. Use it for both
    # ticker and name until the scanner adds a distinct project name field.
    return "\n".join(lines)


def public_late_moon_message(text):
    text = str(text or "")
    match = LATE_MOON_RE.search(text)
    ca = extract_contract_address(text)

    if not match or not ca:
        return ""

    symbol = clean_public_symbol(match.group(1))
    milestone = match.group(2)
    detail = find_context_line(
        text,
        lambda _line, lower: lower.startswith("alerted ")
    )
    liquidity = find_context_line(
        text,
        lambda _line, lower: lower.startswith("liq ")
    )
    liquidity = re.sub(
        r"\s*[·|]\s*chart\s*$",
        "",
        clean_public_text(liquidity),
        flags=re.IGNORECASE
    )
    chart_url = html.escape(
        f"https://dexscreener.com/solana/{ca}",
        quote=True
    )
    lines = [
        "🌙 <b>LATTICE LATE-MOON</b>",
        (
            f"<b>${html_public_value(symbol)}</b> reached "
            f"<b>{html_public_value(milestone)}x</b>"
        ),
    ]

    if detail:
        lines.append(html_public_value(clean_public_text(detail)))

    if liquidity:
        lines.append(html_public_value(liquidity))

    lines.extend([
        f'<a href="{chart_url}">chart</a>',
        f"<code>{html_public_value(ca)}</code>",
    ])
    return "\n".join(lines)


async def send_public_copy(client, target_chat, source_message, public_message):
    media = getattr(source_message, "media", None)

    if media:
        caption = public_message

        if len(caption) > PUBLIC_MEDIA_CAPTION_MAX_CHARS:
            caption = public_entry_message(
                getattr(source_message, "raw_text", "") or "",
                include_context=False
            )

        if caption and len(caption) <= PUBLIC_MEDIA_CAPTION_MAX_CHARS:
            await client.send_file(
                target_chat,
                media,
                caption=caption,
                parse_mode="html"
            )
            return True

    await client.send_message(
        target_chat,
        public_message,
        parse_mode="html",
        link_preview=False
    )
    return True


async def main():
    enabled = env_flag("LATTICE_RELAY_ENABLED", False)

    if not enabled:
        raise SystemExit(
            "LATTICE_RELAY_ENABLED=false. Set it true after filling "
            "LATTICE_RELAY_API_ID/API_HASH."
        )

    api_id = env_int("LATTICE_RELAY_API_ID")
    api_hash = env_text("LATTICE_RELAY_API_HASH")
    session_name = env_text(
        "LATTICE_RELAY_SESSION",
        "data/lattice_relay_user"
    )
    source_chat = env_int("LATTICE_RELAY_SOURCE_CHAT_ID")
    target_chat = parse_chat_ref(env_text("LATTICE_RELAY_TARGET_CHAT_ID"))
    public_enabled = env_flag("LATTICE_PUBLIC_RELAY_ENABLED", False)
    public_target_chats = parse_chat_refs(
        env_text("LATTICE_PUBLIC_RELAY_TARGET_CHAT", "")
    )
    public_relay_mode = env_text(
        "LATTICE_PUBLIC_RELAY_MODE",
        "copy"
    ).lower()
    scanner_username = env_text("LATTICE_RELAY_SCANNER_BOT_USERNAME").lower()
    scanner_sender_id = env_int("LATTICE_RELAY_SCANNER_BOT_ID", 0)
    relay_mode = env_text("LATTICE_RELAY_MODE", "forward").lower()
    require_lattice_tag = env_flag(
        "LATTICE_RELAY_REQUIRE_LATTICE_TAG",
        True
    )
    scan_command_enabled = env_flag(
        "LATTICE_RELAY_SCAN_COMMAND_ENABLED",
        False
    )
    scan_command = env_text(
        "LATTICE_RELAY_SCAN_COMMAND",
        "/s@tokenscan"
    )

    if not api_id or not api_hash:
        raise SystemExit("Missing LATTICE_RELAY_API_ID/API_HASH.")

    if not source_chat:
        raise SystemExit("Missing LATTICE_RELAY_SOURCE_CHAT_ID.")

    if public_enabled and not public_target_chats:
        raise SystemExit(
            "LATTICE_PUBLIC_RELAY_ENABLED=true but "
            "LATTICE_PUBLIC_RELAY_TARGET_CHAT is empty."
        )

    if not target_chat and not public_enabled:
        raise SystemExit(
            "LATTICE_RELAY_TARGET_CHAT_ID is empty and public relay is disabled."
        )

    if not scanner_username and not scanner_sender_id:
        raise SystemExit(
            "Configure LATTICE_RELAY_SCANNER_BOT_USERNAME or "
            "LATTICE_RELAY_SCANNER_BOT_ID so the relay can verify message "
            "origin before forwarding or sending scan commands."
        )

    client = TelegramClient(
        str(ROOT / session_name),
        api_id,
        api_hash
    )
    state = load_state()
    relayed = set(state.get("relayed", []))

    @client.on(events.NewMessage(chats=source_chat))
    async def relay(event):
        try:
            relay_key = f"{event.chat_id}:{event.id}"

            if relay_key in relayed:
                return

            sender = await event.get_sender()
            sender_username = str(getattr(sender, "username", "") or "").lower()
            sender_id = int(getattr(sender, "id", 0) or event.sender_id or 0)

            if scanner_username and sender_username != scanner_username:
                return
            if scanner_sender_id and sender_id != scanner_sender_id:
                return

            text = event.raw_text or ""
            kind = relay_kind(
                text,
                require_lattice_tag=require_lattice_tag
            )

            if not kind:
                return

            contract_address = extract_contract_address(text)

            primary_sent = False

            if target_chat:
                if relay_mode == "forward":
                    await client.forward_messages(target_chat, event.message)
                    primary_sent = True
                else:
                    message = normalize_alert(text)

                    if not message:
                        return

                    await client.send_message(target_chat, message)
                    primary_sent = True

            if (
                kind == "entry"
                and target_chat
                and scan_command_enabled
                and scan_command
                and contract_address
            ):
                await client.send_message(
                    target_chat,
                    f"{scan_command} {contract_address}"
                )
                state["last_scan_command_sent_for"] = contract_address

            public_sent = False

            public_sent_count = 0

            if public_enabled and public_target_chats:
                if public_relay_mode == "forward":
                    for public_target_chat in public_target_chats:
                        await client.forward_messages(
                            public_target_chat,
                            event.message
                        )
                        public_sent_count += 1
                else:
                    public_message = (
                        public_late_moon_message(text)
                        if kind == "late_moon"
                        else public_entry_message(text)
                    )

                    if public_message:
                        for public_target_chat in public_target_chats:
                            await send_public_copy(
                                client,
                                public_target_chat,
                                event.message,
                                public_message
                            )
                            public_sent_count += 1

                public_sent = public_sent_count > 0

            relayed.add(relay_key)
            state["last_relayed_at"] = time.time()
            state["last_relayed_message_id"] = relay_key
            state["last_relay_mode"] = relay_mode
            state["last_public_relay_mode"] = public_relay_mode
            state["last_public_relay_sent"] = public_sent
            state["last_relay_kind"] = kind
            state["last_public_relay_targets"] = [
                str(chat)
                for chat in public_target_chats
            ]
            save_state({
                **state,
                "relayed": list(relayed)
            })
            print(
                (
                    f"relayed {relay_key} -> {target_chat} ({relay_mode})"
                    if primary_sent
                    else f"relayed {relay_key}"
                )
                + (
                    f" public->{','.join(str(chat) for chat in public_target_chats)}"
                    if public_sent
                    else ""
                ),
                flush=True
            )
        except Exception as handler_exc:
            print(f"Error handling relay event {event.id}: {handler_exc!r}", flush=True)

    print(
        "Lattice user relay starting: "
        + (
            f"{source_chat} -> {target_chat} ({relay_mode})"
            if target_chat
            else f"{source_chat} -> no primary target"
        )
        + (
            " | public -> "
            f"{','.join(str(chat) for chat in public_target_chats)} "
            f"({public_relay_mode})"
            if public_enabled and public_target_chats
            else ""
        ),
        flush=True
    )
    await client.start()
    me = await client.get_me()

    if getattr(me, "bot", False):
        await client.disconnect()
        raise SystemExit(
            "LATTICE_RELAY_SESSION is authorized as a bot account. "
            "Rick/TokenScan will still ignore this. Use a normal Telegram "
            "user account session instead."
        )

    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
