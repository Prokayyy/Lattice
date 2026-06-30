import asyncio
import time
from datetime import datetime, timezone

from config import (
    GECKOTERMINAL_BACKOFF_SECONDS,
    GECKOTERMINAL_FALLBACK_ENABLED,
    GECKOTERMINAL_RATE_LIMIT_COOLDOWN_SECONDS,
    GECKOTERMINAL_RATE_LIMIT_LOG_INTERVAL_SECONDS,
    GECKOTERMINAL_REQUESTS_PER_MINUTE
)

from utils.limiter import AsyncRateLimiter


GECKOTERMINAL_TOKEN_POOLS_URL = (
    "https://api.geckoterminal.com/api/v2"
    "/networks/solana/tokens/{token_address}/pools"
)


def safe_float(value, default=0):

    try:
        return float(value or default)
    except (TypeError, ValueError):
        return default


def parse_iso_ms(value):

    if not value:
        return 0

    try:
        dt = datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )
        return int(
            dt.astimezone(timezone.utc).timestamp()
            * 1000
        )
    except ValueError:
        return 0


def token_id_to_address(token_id):

    if not token_id:
        return ""

    return token_id.split("_", 1)[-1]


class GeckoTerminalClient:

    def __init__(
        self,
        session
    ):

        self.session = session
        self.limiter = AsyncRateLimiter(
            GECKOTERMINAL_REQUESTS_PER_MINUTE,
            60
        )
        self.backoff_until = 0
        self.last_rate_limit_log_at = 0

    def is_backing_off(self):

        return time.monotonic() < self.backoff_until

    def mark_rate_limited(
        self,
        retry_after=None
    ):

        wait_for = safe_float(
            retry_after,
            GECKOTERMINAL_BACKOFF_SECONDS
        )
        wait_for = max(
            wait_for,
            GECKOTERMINAL_BACKOFF_SECONDS,
            GECKOTERMINAL_RATE_LIMIT_COOLDOWN_SECONDS
        )

        self.backoff_until = max(
            self.backoff_until,
            time.monotonic() + wait_for
        )

    async def wait_for_backoff(self):

        remaining = (
            self.backoff_until
            - time.monotonic()
        )

        if remaining > 0:
            await asyncio.sleep(remaining)

    async def fetch_token_pairs(
        self,
        token_address
    ):

        if not GECKOTERMINAL_FALLBACK_ENABLED:
            return []

        try:
            if self.is_backing_off():
                return []

            await self.limiter.wait()

            url = GECKOTERMINAL_TOKEN_POOLS_URL.format(
                token_address=token_address
            )

            async with self.session.get(
                url,
                params={
                    "include": "base_token,quote_token",
                    "page": 1
                },
                headers={
                    "accept": "application/json"
                },
                timeout=20
            ) as response:

                if response.status == 429:
                    self.mark_rate_limited(
                        response.headers.get("Retry-After")
                    )
                    current = time.monotonic()

                    if (
                        current - self.last_rate_limit_log_at
                        >= GECKOTERMINAL_RATE_LIMIT_LOG_INTERVAL_SECONDS
                    ):
                        print(
                            "GeckoTerminal returned 429; "
                            "fallback cooling down."
                        )
                        self.last_rate_limit_log_at = current

                    return []

                if response.status != 200:
                    return []

                data = await response.json(
                    content_type=None
                )

            return self.convert_response(
                data,
                token_address
            )

        except Exception as e:
            print(
                f"GeckoTerminal fallback failed: {e}"
            )
            return []

    def convert_response(
        self,
        data,
        token_address
    ):

        included = {}

        for item in data.get("included", []) or []:
            item_id = item.get("id")
            attributes = item.get("attributes", {})

            if not item_id:
                continue

            included[item_id] = {
                "address": token_id_to_address(item_id),
                "name": attributes.get("name", "Unknown"),
                "symbol": attributes.get("symbol", "UNKNOWN")
            }

        pairs = []

        for pool in data.get("data", []) or []:
            pair = self.convert_pool(
                pool,
                included,
                token_address
            )

            if pair:
                pairs.append(pair)

        pairs.sort(
            key=lambda p: (
                p.get("liquidity", {})
                .get("usd", 0)
            ),
            reverse=True
        )

        return pairs

    def convert_pool(
        self,
        pool,
        included,
        token_address
    ):

        attributes = pool.get("attributes", {})
        relationships = pool.get("relationships", {})

        base_id = (
            relationships
            .get("base_token", {})
            .get("data", {})
            .get("id")
        )

        quote_id = (
            relationships
            .get("quote_token", {})
            .get("data", {})
            .get("id")
        )

        base = included.get(
            base_id,
            {
                "address": token_id_to_address(base_id),
                "name": "Unknown",
                "symbol": "UNKNOWN"
            }
        )

        quote = included.get(
            quote_id,
            {
                "address": token_id_to_address(quote_id),
                "name": "Unknown",
                "symbol": "UNKNOWN"
            }
        )

        requested = token_address.lower()
        base_is_target = (
            base.get("address", "").lower()
            == requested
        )
        quote_is_target = (
            quote.get("address", "").lower()
            == requested
        )

        if not base_is_target and not quote_is_target:
            return None

        target = base if base_is_target else quote
        other = quote if base_is_target else base

        price_usd_key = (
            "base_token_price_usd"
            if base_is_target
            else "quote_token_price_usd"
        )

        price_native_key = (
            "base_token_price_native_currency"
            if base_is_target
            else "quote_token_price_native_currency"
        )

        pair_address = (
            attributes.get("address")
            or token_id_to_address(pool.get("id"))
        )

        return {
            "chainId": "solana",
            "dexId": "geckoterminal",
            "url": (
                "https://www.geckoterminal.com"
                f"/solana/pools/{pair_address}"
            ),
            "pairAddress": pair_address,
            "baseToken": target,
            "quoteToken": other,
            "priceUsd": str(
                safe_float(
                    attributes.get(price_usd_key)
                )
            ),
            "priceNative": str(
                safe_float(
                    attributes.get(price_native_key)
                )
            ),
            "txns": (
                attributes.get("transactions")
                or {}
            ),
            "volume": (
                attributes.get("volume_usd")
                or {}
            ),
            "priceChange": (
                attributes.get("price_change_percentage")
                or {}
            ),
            "liquidity": {
                "usd": safe_float(
                    attributes.get("reserve_in_usd")
                )
            },
            "raw_base_reserve": safe_float(
                attributes.get("base_token_balance")
            ),
            "raw_quote_reserve": safe_float(
                attributes.get("quote_token_balance")
            ),
            "base_token_liquidity_usd": safe_float(
                attributes.get("base_token_liquidity_usd")
            ),
            "quote_token_liquidity_usd": safe_float(
                attributes.get("quote_token_liquidity_usd")
            ),
            "fdv": safe_float(
                attributes.get("fdv_usd")
                or attributes.get("market_cap_usd")
            ),
            "marketCap": safe_float(
                attributes.get("market_cap_usd")
            ),
            "pairCreatedAt": parse_iso_ms(
                attributes.get("pool_created_at")
            ),
            "labels": [],
            "info": {}
        }
