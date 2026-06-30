import aiohttp

from config import (
    LIQD_API_BASE_URL,
    LIQD_LIQUIDCORE_DISCOVERY_ENABLED,
    LIQD_LIQUIDCORE_SKIP_ADDRESSES,
    LIQD_LIQUIDCORE_SKIP_SYMBOLS,
    LIQD_LIQUIDCORE_TIMEOUT_SECONDS,
    LIQD_TOKEN_DISCOVERY_ENABLED,
    LIQD_TOKEN_DISCOVERY_LIMIT
)


def safe_float(value, default=0):

    try:
        return float(value or default)
    except (TypeError, ValueError):
        return default


class LiquidCoreDiscovery:

    def __init__(
        self,
        session
    ):

        self.session = session

    def base_url(self):

        return LIQD_API_BASE_URL.rstrip("/")

    def skipped_addresses(self):

        return {
            str(address).lower()
            for address in LIQD_LIQUIDCORE_SKIP_ADDRESSES
        }

    def skipped_symbols(self):

        return {
            str(symbol).upper()
            for symbol in LIQD_LIQUIDCORE_SKIP_SYMBOLS
        }

    def should_skip_token(
        self,
        token
    ):

        address = str(
            token.get("address", "")
        ).lower()
        symbol = str(
            token.get("symbol", "")
        ).upper()

        return (
            not address
            or address in self.skipped_addresses()
            or symbol in self.skipped_symbols()
        )

    async def fetch_pools(self):

        if not LIQD_LIQUIDCORE_DISCOVERY_ENABLED:
            return []

        url = f"{self.base_url()}/liquidcore/pools"
        timeout = aiohttp.ClientTimeout(
            total=LIQD_LIQUIDCORE_TIMEOUT_SECONDS
        )

        async with self.session.get(
            url,
            timeout=timeout
        ) as response:

            if response.status != 200:
                print(
                    "LiquidCore pools returned "
                    f"{response.status}"
                )
                return []

            data = await response.json(
                content_type=None
            )

        pools = data.get("data") if isinstance(data, dict) else []

        if not isinstance(pools, list):
            return []

        return pools

    def pool_volume_usd(
        self,
        pool
    ):

        volume = pool.get("volume24h")

        if isinstance(volume, dict):
            return safe_float(
                volume.get("usd"),
                0
            )

        return safe_float(volume, 0)

    def normalize_pool_token(
        self,
        pool,
        token,
        token_role
    ):

        if not isinstance(token, dict):
            return None

        if self.should_skip_token(token):
            return None

        address = str(
            token.get("address", "")
        ).lower()

        return {
            "address": address,
            "chain": "hyperevm",
            "symbol": token.get("symbol", "UNKNOWN"),
            "source": "liqd_liquidcore",
            "liqd_pool_address": pool.get("poolAddress"),
            "liqd_pool_pair": (
                f"{pool.get('token0', {}).get('symbol', '')}/"
                f"{pool.get('token1', {}).get('symbol', '')}"
            ).strip("/"),
            "liqd_token_role": token_role,
            "liqd_tvl_usd": safe_float(
                pool.get("tvlUSD"),
                0
            ),
            "liqd_volume_24h_usd": self.pool_volume_usd(pool)
        }

    def normalize_pools(
        self,
        pools
    ):

        candidates = {}

        for pool in pools:
            if not isinstance(pool, dict):
                continue

            for token_role in ("token0", "token1"):
                candidate = self.normalize_pool_token(
                    pool,
                    pool.get(token_role),
                    token_role
                )

                if not candidate:
                    continue

                candidates[
                    candidate["address"]
                ] = candidate

        return list(candidates.values())

    async def fetch_candidates(self):

        try:
            pools = await self.fetch_pools()
        except Exception as e:
            print(
                "LiquidCore discovery failed: "
                f"{e}"
            )
            return []

        return self.normalize_pools(pools)


class LiquidTokenDiscovery(LiquidCoreDiscovery):

    async def fetch_tokens(self):

        if not LIQD_TOKEN_DISCOVERY_ENABLED:
            return []

        url = f"{self.base_url()}/tokens"
        timeout = aiohttp.ClientTimeout(
            total=LIQD_LIQUIDCORE_TIMEOUT_SECONDS
        )

        async with self.session.get(
            url,
            params={
                "limit": LIQD_TOKEN_DISCOVERY_LIMIT,
                "metadata": "true"
            },
            timeout=timeout
        ) as response:

            if response.status != 200:
                print(
                    "Liquid token list returned "
                    f"{response.status}"
                )
                return []

            data = await response.json(
                content_type=None
            )

        payload = data.get("data") if isinstance(data, dict) else {}
        tokens = (
            payload.get("tokens")
            if isinstance(payload, dict)
            else None
        )

        if tokens is None and isinstance(data, dict):
            tokens = data.get("tokens")

        if not isinstance(tokens, list):
            return []

        return tokens

    def normalize_tokens(
        self,
        tokens
    ):

        candidates = {}

        for token in tokens:
            if not isinstance(token, dict):
                continue

            if self.should_skip_token(token):
                continue

            address = str(
                token.get("address", "")
            ).lower()

            if not address:
                continue

            candidates[address] = {
                "address": address,
                "chain": "hyperevm",
                "symbol": token.get("symbol", "UNKNOWN"),
                "name": token.get("name", ""),
                "source": "liqd_tokens",
                "liqd_transfers_24h": safe_float(
                    token.get("transfers24h"),
                    0
                )
            }

        return list(candidates.values())

    async def fetch_candidates(self):

        try:
            tokens = await self.fetch_tokens()
        except Exception as e:
            print(
                "Liquid token discovery failed: "
                f"{e}"
            )
            return []

        return self.normalize_tokens(tokens)
