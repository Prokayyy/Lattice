#!/usr/bin/env python3
"""Resolve Telegram chat IDs using the existing Lattice relay user session."""
import argparse
import asyncio
from pathlib import Path

from telethon import TelegramClient


ROOT = Path(__file__).resolve().parent.parent


def load_env():
    values = {}
    env_path = ROOT / ".env"

    if not env_path.exists():
        return values

    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()

        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")

    return values


def full_chat_id(entity):
    raw_id = getattr(entity, "id", None)

    if raw_id is None:
        return ""

    if getattr(entity, "broadcast", False) or getattr(entity, "megagroup", False):
        text = str(raw_id)

        if text.startswith("-100"):
            return text

        return f"-100{text}"

    return str(raw_id)


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "chat",
        nargs="?",
        help="Chat username/link/id to resolve, e.g. @mychannel"
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List recent dialogs visible to the relay user."
    )
    parser.add_argument("--limit", type=int, default=30)
    args = parser.parse_args()

    env = load_env()
    api_id = int(env.get("LATTICE_RELAY_API_ID") or 0)
    api_hash = env.get("LATTICE_RELAY_API_HASH") or ""
    session = env.get("LATTICE_RELAY_SESSION") or "data/lattice_relay_user"

    if not api_id or not api_hash:
        raise SystemExit("Missing LATTICE_RELAY_API_ID/API_HASH in .env")

    client = TelegramClient(str(ROOT / session), api_id, api_hash)
    await client.start()

    if args.list:
        async for dialog in client.iter_dialogs(limit=args.limit):
            entity = dialog.entity
            username = getattr(entity, "username", "") or ""
            print(
                f"{full_chat_id(entity)}\t{dialog.name}\t"
                f"{('@' + username) if username else ''}"
            )

    if args.chat:
        entity = await client.get_entity(args.chat)
        username = getattr(entity, "username", "") or ""
        print(f"id={full_chat_id(entity)}")
        print(f"title={getattr(entity, 'title', '') or getattr(entity, 'first_name', '')}")
        print(f"username={('@' + username) if username else ''}")

    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
