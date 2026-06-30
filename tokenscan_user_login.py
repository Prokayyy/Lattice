import argparse
import asyncio

from config import (
    TOKENSCAN_USER_API_HASH,
    TOKENSCAN_USER_API_ID,
    TOKENSCAN_USER_SESSION_FILE
)


try:
    from telethon import TelegramClient
    from telethon.sessions import StringSession
except ImportError:
    TelegramClient = None
    StringSession = None


async def login(
    api_id,
    api_hash,
    session_file,
    print_string
):

    if TelegramClient is None:
        raise RuntimeError(
            "telethon is not installed. Run: pip install telethon"
        )

    session = (
        StringSession()
        if print_string
        else session_file
    )

    client = TelegramClient(
        session,
        int(api_id),
        api_hash
    )

    await client.start()

    if print_string:
        print(
            "TOKENSCAN_USER_SESSION_STRING="
            f"{client.session.save()}"
        )
    else:
        print(
            "Authorized Telegram user session saved to "
            f"{session_file}.session"
        )

    await client.disconnect()


def main():

    parser = argparse.ArgumentParser(
        description=(
            "Authorize the Telegram user session used to trigger "
            "@tokenscan from scanner alerts."
        )
    )
    parser.add_argument(
        "--api-id",
        default=TOKENSCAN_USER_API_ID,
        help="Telegram API id from https://my.telegram.org"
    )
    parser.add_argument(
        "--api-hash",
        default=TOKENSCAN_USER_API_HASH,
        help="Telegram API hash from https://my.telegram.org"
    )
    parser.add_argument(
        "--session-file",
        default=TOKENSCAN_USER_SESSION_FILE,
        help="Telethon session file base name"
    )
    parser.add_argument(
        "--string",
        action="store_true",
        help="Print a StringSession instead of writing a session file"
    )
    args = parser.parse_args()

    if not args.api_id or int(args.api_id) <= 0:
        raise SystemExit(
            "Set TOKENSCAN_USER_API_ID in config.py or pass --api-id."
        )

    if not args.api_hash:
        raise SystemExit(
            "Set TOKENSCAN_USER_API_HASH in config.py or pass --api-hash."
        )

    asyncio.run(
        login(
            args.api_id,
            args.api_hash,
            args.session_file,
            args.string
        )
    )


if __name__ == "__main__":
    main()
