import time

import aiohttp

from config import (
    PUMPFUN_DISCOVERY_CANDIDATE_TTL_SECONDS,
    PUMPFUN_DISCOVERY_ENABLED,
    PUMPFUN_DISCOVERY_ENDPOINTS,
    PUMPFUN_DISCOVERY_LIMIT
)

from filters.contracts import (
    is_excluded_contract_address
)


BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://pump.fun",
    "Referer": "https://pump.fun/"
}


def safe_float(value, default=0):

    try:
        return float(value or default)
    except (TypeError, ValueError):
        return default


def parse_timestamp(value):

    timestamp = safe_float(value, 0)

    if timestamp <= 0:
        return 0

    if timestamp > 1_000_000_000_000:
        timestamp = timestamp / 1000

    return timestamp


def response_items(data):

    if isinstance(data, list):
        return data

    if not isinstance(data, dict):
        return []

    for key in (
        "coins",
        "data",
        "results",
        "items"
    ):
        value = data.get(key)

        if isinstance(value, list):
            return value

    return []


class PumpFunDiscovery:

    def __init__(
        self,
        session
    ):

        self.session = session
        self.candidates = {}

    def enabled(self):

        return (
            PUMPFUN_DISCOVERY_ENABLED
            and PUMPFUN_DISCOVERY_ENDPOINTS
        )

    async def fetch_candidates(self):

        self.prune_candidates()

        if not self.enabled():
            return list(self.candidates.values())

        for template in PUMPFUN_DISCOVERY_ENDPOINTS:
            await self.fetch_endpoint(template)

        return list(self.candidates.values())

    async def fetch_endpoint(
        self,
        template
    ):

        url = str(template).format(
            limit=PUMPFUN_DISCOVERY_LIMIT
        )

        try:
            async with self.session.get(
                url,
                headers=BROWSER_HEADERS,
                timeout=20
            ) as response:

                if response.status != 200:
                    print(
                        "Pump.fun discovery returned "
                        f"{response.status}."
                    )
                    return

                data = await response.json(
                    content_type=None
                )

        except (
            aiohttp.ClientError,
            TimeoutError
        ) as e:
            print(
                "Pump.fun discovery failed: "
                f"{e}"
            )
            return

        found = 0

        for item in response_items(data):
            candidate = self.normalize_coin(item)

            if not candidate:
                continue

            self.candidates[candidate["address"]] = candidate
            found += 1

        if found:
            print(
                "Pump.fun discovery found "
                f"{found} recent candidates."
            )

    def normalize_coin(
        self,
        item
    ):

        if not isinstance(item, dict):
            return None

        address = (
            item.get("mint")
            or item.get("address")
            or item.get("id")
        )

        if (
            not address
            or is_excluded_contract_address(address)
        ):
            return None

        now = time.time()
        created_at = parse_timestamp(
            item.get("created_timestamp")
            or item.get("createdAt")
            or item.get("created_at")
            or item.get("timestamp")
        )
        first_seen_at = created_at or now

        if (
            PUMPFUN_DISCOVERY_CANDIDATE_TTL_SECONDS > 0
            and first_seen_at
            < now - PUMPFUN_DISCOVERY_CANDIDATE_TTL_SECONDS
        ):
            return None

        return {
            "address": address,
            "chain": "solana",
            "symbol": item.get("symbol") or "UNKNOWN",
            "name": item.get("name") or "",
            "source": "pumpfun_recent",
            "launchpad": "pump.fun",
            "lifecycle_hint": "bonding_curve",
            "provider_pair_pending": True,
            "first_seen_at": first_seen_at,
            "pumpfun_created_at": created_at,
            "pumpfun_complete": bool(item.get("complete")),
            "pumpfun_raydium_pool": item.get("raydium_pool") or "",
            "source_score": 2
        }

    def prune_candidates(self):

        if not PUMPFUN_DISCOVERY_CANDIDATE_TTL_SECONDS:
            return

        cutoff = (
            time.time()
            - PUMPFUN_DISCOVERY_CANDIDATE_TTL_SECONDS
        )

        expired = [
            address
            for address, candidate in self.candidates.items()
            if candidate.get("first_seen_at", 0) < cutoff
        ]

        for address in expired:
            self.candidates.pop(address, None)
