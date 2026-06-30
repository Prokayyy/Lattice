import aiohttp

from config import (
    HYPERSWAP_SUBGRAPH_DISCOVERY_ENABLED,
    HYPERSWAP_SUBGRAPH_PAIR_LIMIT,
    HYPERSWAP_SUBGRAPH_TIMEOUT_SECONDS,
    HYPERSWAP_V2_SUBGRAPH_URL,
    HYPERSWAP_V3_SUBGRAPH_URL,
    LIQD_LIQUIDCORE_SKIP_ADDRESSES,
    LIQD_LIQUIDCORE_SKIP_SYMBOLS
)


def safe_float(value, default=0):

    try:
        return float(value or default)
    except (TypeError, ValueError):
        return default


class HyperSwapSubgraphDiscovery:

    def __init__(
        self,
        session
    ):

        self.session = session

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
            token.get("id", "")
            or token.get("address", "")
        ).lower()
        symbol = str(
            token.get("symbol", "")
        ).upper()

        return (
            not address
            or address in self.skipped_addresses()
            or symbol in self.skipped_symbols()
        )

    async def query(
        self,
        url,
        query
    ):

        timeout = aiohttp.ClientTimeout(
            total=HYPERSWAP_SUBGRAPH_TIMEOUT_SECONDS
        )

        async with self.session.post(
            url,
            json={
                "query": query
            },
            timeout=timeout
        ) as response:

            if response.status != 200:
                print(
                    "HyperSwap subgraph returned "
                    f"{response.status}"
                )
                return {}

            data = await response.json(
                content_type=None
            )

        if not isinstance(data, dict):
            return {}

        if data.get("errors"):
            print("HyperSwap subgraph returned errors")
            return {}

        payload = data.get("data")

        return payload if isinstance(payload, dict) else {}

    async def fetch_v2_pairs(self):

        limit = max(
            HYPERSWAP_SUBGRAPH_PAIR_LIMIT,
            1
        )
        query = """
        {
          pairs(
            first: %d
            orderBy: reserveUSD
            orderDirection: desc
          ) {
            id
            reserveUSD
            volumeUSD
            token0 { id symbol name }
            token1 { id symbol name }
          }
        }
        """ % limit

        data = await self.query(
            HYPERSWAP_V2_SUBGRAPH_URL,
            query
        )

        pairs = data.get("pairs") or []

        return pairs if isinstance(pairs, list) else []

    async def fetch_v3_pools(self):

        limit = max(
            HYPERSWAP_SUBGRAPH_PAIR_LIMIT,
            1
        )
        query = """
        {
          pools(
            first: %d
            orderBy: totalValueLockedUSD
            orderDirection: desc
          ) {
            id
            totalValueLockedUSD
            volumeUSD
            token0 { id symbol name }
            token1 { id symbol name }
          }
        }
        """ % limit

        data = await self.query(
            HYPERSWAP_V3_SUBGRAPH_URL,
            query
        )

        pools = data.get("pools") or []

        return pools if isinstance(pools, list) else []

    def add_pair_candidates(
        self,
        candidates,
        item,
        source,
        liquidity_key
    ):

        liquidity = safe_float(
            item.get(liquidity_key),
            0
        )
        volume = safe_float(
            item.get("volumeUSD"),
            0
        )

        for role in ("token0", "token1"):
            token = item.get(role) or {}

            if self.should_skip_token(token):
                continue

            address = str(
                token.get("id", "")
            ).lower()

            candidates[address] = {
                "address": address,
                "chain": "hyperevm",
                "symbol": token.get("symbol", "UNKNOWN"),
                "name": token.get("name", ""),
                "source": source,
                "hyperswap_pair_address": item.get("id"),
                "hyperswap_liquidity_usd": liquidity,
                "hyperswap_volume_usd": volume
            }

    async def fetch_candidates(self):

        if not HYPERSWAP_SUBGRAPH_DISCOVERY_ENABLED:
            return []

        candidates = {}

        try:
            for pair in await self.fetch_v2_pairs():
                self.add_pair_candidates(
                    candidates,
                    pair,
                    "hyperswap_v2_subgraph",
                    "reserveUSD"
                )

            for pool in await self.fetch_v3_pools():
                self.add_pair_candidates(
                    candidates,
                    pool,
                    "hyperswap_v3_subgraph",
                    "totalValueLockedUSD"
                )

        except Exception as e:
            print(
                "HyperSwap subgraph discovery failed: "
                f"{e}"
            )
            return []

        return list(candidates.values())
