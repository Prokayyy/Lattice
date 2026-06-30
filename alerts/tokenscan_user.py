from config import (
    TELEGRAM_CHAT_ID,
    TOKENSCAN_COMMAND,
    TOKENSCAN_USER_API_HASH,
    TOKENSCAN_USER_API_ID,
    TOKENSCAN_USER_CHAT_ID,
    TOKENSCAN_USER_SESSION_FILE,
    TOKENSCAN_USER_SESSION_STRING,
    TOKENSCAN_USER_TRIGGER_ENABLED
)


try:
    from telethon import TelegramClient
    from telethon.sessions import StringSession
except ImportError:
    TelegramClient = None
    StringSession = None


class TokenScanUserTrigger:

    def __init__(self):

        self.client = None
        self.warned = set()

    def warn_once(
        self,
        key,
        message
    ):

        if key in self.warned:
            return

        self.warned.add(key)
        print(message)

    def get_api_id(self):

        try:
            return int(TOKENSCAN_USER_API_ID)
        except (TypeError, ValueError):
            return 0

    def get_chat_id(self):

        chat_id = (
            TOKENSCAN_USER_CHAT_ID
            or TELEGRAM_CHAT_ID
        )

        if isinstance(chat_id, str):
            chat_id = chat_id.strip()

            if chat_id.lstrip("-").isdigit():
                return int(chat_id)

        return chat_id

    def get_session(self):

        if TOKENSCAN_USER_SESSION_STRING:
            return StringSession(
                TOKENSCAN_USER_SESSION_STRING
            )

        return TOKENSCAN_USER_SESSION_FILE

    def is_configured(self):

        return (
            TOKENSCAN_USER_TRIGGER_ENABLED
            and self.get_api_id() > 0
            and bool(TOKENSCAN_USER_API_HASH)
            and bool(
                TOKENSCAN_USER_SESSION_STRING
                or TOKENSCAN_USER_SESSION_FILE
            )
            and bool(self.get_chat_id())
        )

    async def ensure_client(self):

        if not TOKENSCAN_USER_TRIGGER_ENABLED:
            return None

        if TelegramClient is None:
            self.warn_once(
                "telethon_missing",
                (
                    "TokenScan user trigger is enabled, "
                    "but telethon is not installed."
                )
            )
            return None

        if not self.is_configured():
            self.warn_once(
                "config_missing",
                (
                    "TokenScan user trigger is enabled, "
                    "but API id/hash/session/chat config is incomplete."
                )
            )
            return None

        if self.client and self.client.is_connected():
            return self.client

        self.client = TelegramClient(
            self.get_session(),
            self.get_api_id(),
            TOKENSCAN_USER_API_HASH
        )

        await self.client.connect()

        if not await self.client.is_user_authorized():
            await self.client.disconnect()
            self.client = None
            self.warn_once(
                "not_authorized",
                (
                    "TokenScan user trigger session is not authorized. "
                    "Run tokenscan_user_login.py once, then restart "
                    "the scanner."
                )
            )
            return None

        return self.client

    async def send_contract_scan(
        self,
        contract_address
    ):

        client = await self.ensure_client()

        if not client:
            return False

        message = (
            f"{TOKENSCAN_COMMAND} "
            f"{contract_address}"
        )

        try:
            await client.send_message(
                self.get_chat_id(),
                message
            )
            print(
                "TokenScan user trigger sent "
                f"for {contract_address}"
            )
            return True
        except Exception as e:
            print(
                f"TokenScan user trigger failed: {e}"
            )
            return False
