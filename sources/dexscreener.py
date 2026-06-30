import asyncio
import time
from collections import defaultdict

import aiohttp

from config import (
    DEXSCREENER_BACKOFF_SECONDS,
    DEXSCREENER_BATCH_SIZE,
    DEXSCREENER_PAIR_CACHE_SECONDS,
    DEXSCREENER_REQUESTS_PER_MINUTE,
    GECKOTERMINAL_5M_ENRICHMENT_ENABLED,
    GECKOTERMINAL_FALLBACK_MAX_PER_BATCH,
    SCANNER_ENABLED_CHAINS
)

from sources.geckoterminal import GeckoTerminalClient

from utils.limiter import AsyncRateLimiter


DEX_TOKEN_BATCH_URL = (
    "https://api.dexscreener.com/"
    "tokens/v1/{chain_id}/{token_addresses}"
)

DEX_SEARCH_URL = (
    "https://api.dexscreener.com/"
    "latest/dex/search"
)


def safe_float(value, default=0):

    try:
        return float(value or default)
    except (TypeError, ValueError):
        return default


class DexScreenerClient:

    def __init__(self):

        self.session = None
        self.limiter = AsyncRateLimiter(
            DEXSCREENER_REQUESTS_PER_MINUTE,
            60
        )
        self.backoff_until = 0
        self.last_rate_limit_at = 0
        self.pair_cache = {}
        self.geckoterminal = None

    async def start(self):

        if not self.session:

            self.session = aiohttp.ClientSession()
            self.geckoterminal = GeckoTerminalClient(
                self.session
            )

    async def close(self):

        if self.session:

            await self.session.close()
            self.session = None
            self.geckoterminal = None

    def is_backing_off(self):

        return time.monotonic() < self.backoff_until

    def recently_rate_limited(
        self,
        within_seconds=120
    ):

        return (
            self.last_rate_limit_at > 0
            and time.monotonic() - self.last_rate_limit_at
            <= within_seconds
        )

    def mark_rate_limited(
        self,
        retry_after=None
    ):

        wait_for = safe_float(
            retry_after,
            DEXSCREENER_BACKOFF_SECONDS
        )

        wait_for = max(
            wait_for,
            DEXSCREENER_BACKOFF_SECONDS
        )

        self.last_rate_limit_at = time.monotonic()
        self.backoff_until = max(
            self.backoff_until,
            time.monotonic() + wait_for
        )

    async def wait_for_turn(self):

        remaining = (
            self.backoff_until
            - time.monotonic()
        )

        if remaining > 0:
            await asyncio.sleep(remaining)

        await self.limiter.wait()

    def chunk_addresses(
        self,
        addresses
    ):

        for index in range(
            0,
            len(addresses),
            DEXSCREENER_BATCH_SIZE
        ):
            yield addresses[
                index:
                index + DEXSCREENER_BATCH_SIZE
            ]

    def cache_get(
        self,
        token_address,
        chain_id="solana"
    ):

        cached = self.pair_cache.get(
            (chain_id, token_address)
        )

        if not cached:
            return None

        cached_at, pairs = cached

        if (
            time.monotonic() - cached_at
            > DEXSCREENER_PAIR_CACHE_SECONDS
        ):
            self.pair_cache.pop(
                (chain_id, token_address),
                None
            )
            return None

        return pairs

    def cache_set(
        self,
        token_address,
        pairs,
        chain_id="solana"
    ):

        self.pair_cache[(chain_id, token_address)] = (
            time.monotonic(),
            pairs
        )

    def pair_missing_5m_fields(
        self,
        pair
    ):

        volume = pair.get("volume") or {}
        txns = pair.get("txns") or {}
        price_change = pair.get("priceChange") or {}
        txns_5m = txns.get("m5") or {}

        return (
            "m5" not in volume
            or "m5" not in price_change
            or "m5" not in txns
            or (
                "buys" not in txns_5m
                and "sells" not in txns_5m
            )
        )

    def pairs_need_5m_fallback(
        self,
        pairs
    ):

        if not pairs:
            return False

        return self.pair_missing_5m_fields(
            pairs[0]
        )

    def fallback_pair_for_merge(
        self,
        primary_pair,
        fallback_pairs
    ):

        primary_address = str(
            primary_pair.get("pairAddress")
            or ""
        ).lower()

        if primary_address:
            for pair in fallback_pairs:
                if (
                    str(pair.get("pairAddress") or "").lower()
                    == primary_address
                ):
                    return pair

        return fallback_pairs[0] if fallback_pairs else None

    def merge_5m_fallback_pair(
        self,
        primary_pair,
        fallback_pair
    ):

        if not fallback_pair:
            return False

        changed = False

        for field in (
            "volume",
            "txns",
            "priceChange"
        ):
            primary = primary_pair.setdefault(field, {})
            fallback = fallback_pair.get(field) or {}

            if (
                "m5" not in primary
                and "m5" in fallback
            ):
                primary["m5"] = fallback["m5"]
                changed = True

        if changed:
            primary_pair[
                "geckoterminal5mFallback"
            ] = True

        return changed

    async def enrich_missing_5m_fields(
        self,
        pair_map,
        token_addresses,
        chain_id="solana"
    ):

        if (
            chain_id != "solana"
            or not self.geckoterminal
            or not GECKOTERMINAL_5M_ENRICHMENT_ENABLED
            or GECKOTERMINAL_FALLBACK_MAX_PER_BATCH <= 0
        ):
            return pair_map

        attempts = 0

        for address in token_addresses:
            if attempts >= GECKOTERMINAL_FALLBACK_MAX_PER_BATCH:
                break

            if self.geckoterminal.is_backing_off():
                break

            pairs = pair_map.get(address) or []

            if not self.pairs_need_5m_fallback(pairs):
                continue

            attempts += 1
            fallback_pairs = (
                await self.geckoterminal
                .fetch_token_pairs(address)
            )

            if not fallback_pairs:
                continue

            fallback_pair = self.fallback_pair_for_merge(
                pairs[0],
                fallback_pairs
            )

            if self.merge_5m_fallback_pair(
                pairs[0],
                fallback_pair
            ):
                self.cache_set(
                    address,
                    pairs,
                    chain_id=chain_id
                )

        return pair_map

    async def fetch_json(
        self,
        url,
        params=None,
        timeout=15
    ):

        await self.wait_for_turn()

        async with self.session.get(
            url,
            params=params,
            timeout=timeout
        ) as response:

            if response.status == 429:
                self.mark_rate_limited(
                    response.headers.get("Retry-After")
                )
                print(
                    "DexScreener returned 429; "
                    "cooling down before more Dex calls."
                )
                return None

            if response.status != 200:
                return None

            return await response.json(
                content_type=None
            )

    async def fetch_search_pairs(
        self,
        query,
        chains=None
    ):

        try:
            data = await self.fetch_json(
                DEX_SEARCH_URL,
                params={
                    "q": query
                },
                timeout=20
            )

            if not data:
                return []

            pairs = data.get("pairs") or []

            if not isinstance(pairs, list):
                return []

            allowed_chains = set(
                chains or SCANNER_ENABLED_CHAINS
            )

            return [
                pair
                for pair in pairs
                if pair.get("chainId") in allowed_chains
            ]

        except Exception as e:
            print(
                f"DexScreener search failed "
                f"({query}): {e}"
            )
            return []

    async def fetch_token_pairs(
        self,
        token_address,
        allow_fallback=True,
        chain_id="solana"
    ):

        pair_map = await self.fetch_token_pairs_batch(
            [token_address],
            allow_fallback=allow_fallback,
            chain_id=chain_id
        )

        return pair_map.get(
            token_address,
            []
        )

    async def fetch_token_pairs_batch(
        self,
        token_addresses,
        allow_fallback=False,
        force_refresh=False,
        chain_id="solana"
    ):

        unique_addresses = []
        seen = set()

        for address in token_addresses:

            if not address or address in seen:
                continue

            seen.add(address)
            unique_addresses.append(address)

        pair_map = {}
        uncached = []

        for address in unique_addresses:

            cached = None

            if not force_refresh:
                cached = self.cache_get(
                    address,
                    chain_id=chain_id
                )

            if cached is None:
                uncached.append(address)
            else:
                pair_map[address] = cached

        if uncached and self.is_backing_off():

            if not allow_fallback:
                return pair_map

            uncached = []

        for chunk in self.chunk_addresses(uncached):

            chunk_map = await self.fetch_token_pairs_chunk(
                chunk,
                chain_id=chain_id,
                force_refresh=force_refresh
            )

            pair_map.update(chunk_map)

            if self.is_backing_off():
                break

        if (
            chain_id == "solana"
            and allow_fallback
            and self.recently_rate_limited()
        ):

            fallback_attempts = 0

            for address in unique_addresses:
                if (
                    fallback_attempts
                    >= GECKOTERMINAL_FALLBACK_MAX_PER_BATCH
                    or not self.geckoterminal
                    or self.geckoterminal.is_backing_off()
                ):
                    break

                if pair_map.get(address):
                    continue

                fallback_attempts += 1
                fallback_pairs = (
                    await self.geckoterminal
                    .fetch_token_pairs(address)
                )

                if fallback_pairs:
                    pair_map[address] = fallback_pairs

        if allow_fallback:
            pair_map = await self.enrich_missing_5m_fields(
                pair_map,
                unique_addresses,
                chain_id=chain_id
            )

        return pair_map

    async def fetch_token_pairs_chunk(
        self,
        token_addresses,
        chain_id="solana",
        force_refresh=False
    ):

        if not token_addresses:
            return {}

        empty_map = {
            address: []
            for address in token_addresses
        }

        try:
            url = DEX_TOKEN_BATCH_URL.format(
                chain_id=chain_id,
                token_addresses=",".join(
                    token_addresses
                )
            )

            data = await self.fetch_json(url)

            if data is None:
                return empty_map

            if isinstance(data, dict):
                pairs = data.get("pairs") or []
            elif isinstance(data, list):
                pairs = data
            else:
                pairs = []

            mapped = self.map_pairs_to_addresses(
                token_addresses,
                pairs,
                chain_id=chain_id
            )

            for address in token_addresses:
                self.cache_set(
                    address,
                    mapped.get(address, []),
                    chain_id=chain_id
                )

            return mapped

        except Exception as e:
            print(
                f"Dex batch fetch error: {e}"
            )
            return empty_map

    def map_pairs_to_addresses(
        self,
        token_addresses,
        pairs,
        chain_id="solana"
    ):

        requested = {
            address.lower(): address
            for address in token_addresses
        }

        mapped = defaultdict(list)

        for pair in pairs:

            if pair.get("chainId") != chain_id:
                continue

            base = pair.get("baseToken", {})
            quote = pair.get("quoteToken", {})

            pair_addresses = [
                base.get("address", "").lower(),
                quote.get("address", "").lower()
            ]

            for pair_address in pair_addresses:

                original = requested.get(
                    pair_address
                )

                if original:
                    mapped[original].append(pair)

        result = {}

        for address in token_addresses:

            address_pairs = mapped.get(
                address,
                []
            )

            address_pairs.sort(
                key=lambda p: (
                    p.get("liquidity", {})
                    .get("usd", 0)
                ),
                reverse=True
            )

            result[address] = address_pairs

        return result
