import asyncio
import time
from datetime import datetime, timezone

from sources.dexscreener import DexScreenerClient

from config import (
    POSITION_SOL_MINT_ADDRESS,
    POSITION_SOL_PRICE_REFRESH_SECONDS,
    POSITION_SOL_USD
)


def safe_float(value, default=0):

    try:
        return float(value or default)
    except (TypeError, ValueError):
        return default


def unique_addresses(addresses):

    unique = []
    seen = set()

    for address in addresses:
        if not address or address in seen:
            continue

        seen.add(address)
        unique.append(address)

    return unique


def best_live_pair(pairs, token_address=None):

    requested = (
        token_address.lower()
        if token_address
        else ""
    )
    valid_pairs = [
        pair
        for pair in pairs or []
        if safe_float(pair.get("priceUsd")) > 0
    ]

    if not valid_pairs:
        return None

    preferred_pairs = [
        pair
        for pair in valid_pairs
        if (
            not requested
            or (
                pair.get("baseToken", {})
                .get("address", "")
                .lower()
                == requested
            )
        )
    ]

    candidates = preferred_pairs or valid_pairs

    candidates.sort(
        key=lambda pair: safe_float(
            (pair.get("liquidity") or {}).get("usd")
        ),
        reverse=True
    )

    return candidates[0]


async def fetch_live_prices(addresses, chain_by_address=None):

    addresses = unique_addresses(addresses)
    chain_by_address = chain_by_address or {}
    stats = {
        "enabled": True,
        "attempted": len(addresses),
        "refreshed": 0,
        "missing": [],
        "error": "",
        "as_of": datetime.now(timezone.utc).timestamp(),
        "mutated_state": False
    }
    live_prices = {}

    if not addresses:
        return live_prices, stats

    client = DexScreenerClient()

    try:
        await client.start()
        pair_map = {}
        addresses_by_chain = {}

        for address in addresses:
            chain = chain_by_address.get(
                address,
                "solana"
            )
            addresses_by_chain.setdefault(
                chain,
                []
            ).append(address)

        for chain, chain_addresses in addresses_by_chain.items():
            pair_map.update(
                await client.fetch_token_pairs_batch(
                    chain_addresses,
                    allow_fallback=True,
                    chain_id=chain
                )
            )
    except Exception as exc:
        stats["error"] = str(exc)
        return live_prices, stats
    finally:
        await client.close()

    for address in addresses:
        pair = best_live_pair(
            pair_map.get(address, []),
            token_address=address
        )

        if not pair:
            stats["missing"].append(address)
            continue

        live_price = safe_float(
            pair.get("priceUsd")
        )

        if live_price <= 0:
            stats["missing"].append(address)
            continue

        live_prices[address] = {
            "price_usd": live_price,
            "pair_address": pair.get("pairAddress", ""),
            "liquidity_usd": safe_float(
                (pair.get("liquidity") or {}).get("usd")
            ),
            "volume_1h_usd": safe_float(
                (pair.get("volume") or {}).get("h1")
            ),
            "fdv_usd": safe_float(
                pair.get("fdv")
                or pair.get("marketCap")
            ),
            "source": pair.get("dexId", "dexscreener")
        }
        stats["refreshed"] += 1

    return live_prices, stats


async def fetch_sol_usd_price():

    live_prices, stats = await fetch_live_prices([
        POSITION_SOL_MINT_ADDRESS
    ])
    live_price = live_prices.get(
        POSITION_SOL_MINT_ADDRESS,
        {}
    )
    price = safe_float(
        live_price.get("price_usd"),
        0
    )

    if price <= 0:
        return 0, stats

    return price, stats


class SolUsdPriceFeed:

    def __init__(
        self,
        fallback_price=POSITION_SOL_USD,
        refresh_seconds=POSITION_SOL_PRICE_REFRESH_SECONDS
    ):

        self.fallback_price = safe_float(
            fallback_price,
            POSITION_SOL_USD
        )
        self.refresh_seconds = max(
            safe_float(refresh_seconds, 60),
            1
        )
        self.price = 0
        self.updated_at = 0
        self.last_stats = {
            "enabled": True,
            "refreshed": 0,
            "attempted": 0,
            "error": "",
            "as_of": 0,
            "fallback": True
        }
        self.lock = asyncio.Lock()

    def current_price(self):

        return safe_float(
            self.price,
            self.fallback_price
        )

    def current_stats(self):

        return dict(
            self.last_stats,
            sol_usd=self.current_price()
        )

    async def get_price(
        self,
        force=False
    ):

        now = time.monotonic()

        if (
            not force
            and self.price > 0
            and now - self.updated_at < self.refresh_seconds
        ):
            return self.price

        async with self.lock:
            now = time.monotonic()

            if (
                not force
                and self.price > 0
                and now - self.updated_at < self.refresh_seconds
            ):
                return self.price

            try:
                price, stats = await fetch_sol_usd_price()
            except Exception as exc:
                stats = {
                    "enabled": True,
                    "refreshed": 0,
                    "attempted": 1,
                    "error": str(exc),
                    "as_of": datetime.now(timezone.utc).timestamp(),
                    "fallback": True
                }
                price = 0

            if price > 0:
                self.price = price
                self.updated_at = time.monotonic()
                self.last_stats = dict(
                    stats,
                    fallback=False,
                    sol_usd=price
                )
                return price

            fallback = self.current_price()
            self.last_stats = dict(
                stats,
                fallback=True,
                sol_usd=fallback
            )

            return fallback
