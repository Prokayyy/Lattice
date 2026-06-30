#!/usr/bin/env python3
"""Create/check the human Telegram session used by lattice_user_relay.py."""
import argparse
import asyncio
import getpass
import json
import os
import sys
from pathlib import Path

try:
    from telethon import TelegramClient
    from telethon.errors import SessionPasswordNeededError
except ImportError as exc:
    raise SystemExit(
        "Telethon is not installed. Run: env/bin/pip install -r requirements.txt"
    ) from exc


ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "data" / "lattice_relay_login.json"


def load_dotenv(path):
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()

        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        os.environ.setdefault(
            key.strip(),
            value.strip().strip('"').strip("'")
        )


def env_text(name, default=""):
    return os.getenv(name, default).strip()


def env_int(name, default=0):
    value = env_text(name)

    if not value:
        return default

    return int(value)


def load_state():
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(state):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(
        json.dumps(state, indent=2, sort_keys=True),
        encoding="utf-8"
    )


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--phone", help="Phone number for the normal user account.")
    parser.add_argument("--code", help="Telegram login code for the normal user account.")
    parser.add_argument("--password", help="2FA password, only if Telegram requires it.")
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")

    api_id = env_int("LATTICE_RELAY_API_ID")
    api_hash = env_text("LATTICE_RELAY_API_HASH")
    session_name = env_text(
        "LATTICE_RELAY_SESSION",
        "data/lattice_relay_human"
    )

    if not api_id or not api_hash:
        raise SystemExit("Missing LATTICE_RELAY_API_ID/API_HASH.")

    client = TelegramClient(str(ROOT / session_name), api_id, api_hash)
    await client.connect()

    try:
        if await client.is_user_authorized():
            me = await client.get_me()
            print(
                "authorized: "
                f"id={getattr(me, 'id', '')} "
                f"username={getattr(me, 'username', '') or ''} "
                f"bot={getattr(me, 'bot', None)}"
            )

            if getattr(me, "bot", False):
                raise SystemExit(
                    "This session is a bot. Change LATTICE_RELAY_SESSION "
                    "or delete the session file, then login with a normal user."
                )

            return

        if args.status:
            print("not authorized")
            return

        if args.phone:
            sent = await client.send_code_request(args.phone)
            save_state(
                {
                    "phone": args.phone,
                    "phone_code_hash": sent.phone_code_hash
                }
            )
            print("code sent")
            return

        if args.code:
            state = load_state()
            phone = state.get("phone")
            phone_code_hash = state.get("phone_code_hash")

            if not phone or not phone_code_hash:
                raise SystemExit("Run with --phone first.")

            try:
                await client.sign_in(
                    phone=phone,
                    code=args.code,
                    phone_code_hash=phone_code_hash
                )
            except SessionPasswordNeededError:
                password = args.password or getpass.getpass("Telegram 2FA password: ")
                await client.sign_in(password=password)

            me = await client.get_me()

            if getattr(me, "bot", False):
                raise SystemExit("Logged in session is a bot, not a normal user.")

            print(
                "login complete: "
                f"id={getattr(me, 'id', '')} "
                f"username={getattr(me, 'username', '') or ''}"
            )
            return

        raise SystemExit("Use --status, --phone, or --code.")
    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
