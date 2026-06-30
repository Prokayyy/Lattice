import asyncio
import time

import aiohttp

from config import (
    ALCHEMY_RPC_URLS,
    MINT_AGE_CACHE_TTL_SECONDS,
    MINT_AGE_EARLY_EXIT_HOURS,
    MINT_AGE_RPC_GENESIS_MAX_PAGES,
    MINT_AGE_RPC_MAX_PAGES,
    MINT_AGE_RPC_PAGE_LIMIT,
    MIN_TOKEN_AGE_HOURS,
    REQUIRE_MINT_AGE
)


MINT_AGE_CACHE = {}


def normalize_chain(chain):

    return str(chain or "").strip().lower()


def age_unlimited(chain):

    return normalize_chain(chain) == "hyperevm"


def unlimited_age_result(chain):

    if not age_unlimited(chain):
        return None

    return {
        "age_hours": 0,
        "source": "age_unlimited_hyperevm",
        "block_time": None
    }


def fallback_pair_age(pair_created_at):

    if not pair_created_at:
        return None

    try:
        block_time = float(pair_created_at) / 1000
    except (TypeError, ValueError):
        return None

    age_hours = (
        time.time()
        - block_time
    ) / 3600

    return {
        "age_hours": age_hours,
        "source": "dexscreener_pair_created_at",
        "block_time": block_time
    }


async def resolve_mint_age(
    session,
    mint_address,
    pair_created_at=None,
    chain="solana",
    walk_to_genesis=False
):

    now = time.time()

    cached = MINT_AGE_CACHE.get(
        mint_address
    )

    if cached:

        fetched_at, result = cached

        if (
            now - fetched_at
            < MINT_AGE_CACHE_TTL_SECONDS
        ):
            # A genesis walk needs a genesis-confirmed block time. A gate-cache
            # result may only prove "old enough", so re-resolve for lineage.
            if not walk_to_genesis or result.get("reached_genesis"):
                return build_age_result(result)

    if age_unlimited(chain) and not pair_created_at:
        return unlimited_age_result(chain)

    if chain != "solana":
        fallback = fallback_pair_age(
            pair_created_at
        )

        if fallback:
            MINT_AGE_CACHE[mint_address] = (
                now,
                fallback
            )
            return build_age_result(fallback)

        unlimited = unlimited_age_result(chain)

        if unlimited:
            return unlimited

        return None

    fallback = fallback_pair_age(
        pair_created_at
    )

    if (
        fallback
        and not walk_to_genesis
        and fallback["age_hours"] >= MIN_TOKEN_AGE_HOURS
    ):
        MINT_AGE_CACHE[mint_address] = (
            now,
            fallback
        )
        return build_age_result(fallback)

    rpc = ALCHEMY_RPC_URLS.get(
        "solana"
    )

    result = None

    if rpc and mint_address:
        result = await fetch_mint_creation_time(
            session,
            rpc,
            mint_address,
            walk_to_genesis=walk_to_genesis
        )

    if result:
        MINT_AGE_CACHE[mint_address] = (
            now,
            result
        )
        return build_age_result(result)

    if fallback:
        MINT_AGE_CACHE[mint_address] = (
            now,
            fallback
        )
        return build_age_result(fallback)

    if REQUIRE_MINT_AGE:
        return None

    return None


async def fetch_mint_creation_time(
    session,
    rpc,
    mint_address,
    timeout_seconds=15,
    walk_to_genesis=False
):

    before = None
    oldest_block_time = None
    oldest_signature = None
    reached_genesis = False
    deadline = time.monotonic() + timeout_seconds

    # Once the oldest signature we've seen is already older than this cutoff the
    # mint is provably "old enough" for the entry gate, so there's no point
    # walking the rest of its history back to genesis. <= 0 disables it.
    # Lineage/OG ranking instead needs the true InitializeMint time, so callers
    # pass walk_to_genesis=True to page all the way to the first signature.
    configured_early_exit_hours = float(
        MINT_AGE_EARLY_EXIT_HOURS or 0
    )
    proof_age_hours = (
        max(configured_early_exit_hours, float(MIN_TOKEN_AGE_HOURS))
        if configured_early_exit_hours > 0
        else 0
    )
    early_exit_cutoff = (
        time.time() - proof_age_hours * 3600
        if (proof_age_hours > 0 and not walk_to_genesis)
        else None
    )
    max_pages = (
        MINT_AGE_RPC_GENESIS_MAX_PAGES
        if walk_to_genesis
        else MINT_AGE_RPC_MAX_PAGES
    )

    for _ in range(max_pages):

        if time.monotonic() >= deadline:
            break

        options = {
            "limit": MINT_AGE_RPC_PAGE_LIMIT
        }

        if before:
            options["before"] = before

        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getSignaturesForAddress",
            "params": [
                mint_address,
                options
            ]
        }

        try:
            async with session.post(
                rpc,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as response:

                if response.status != 200:
                    return None

                data = await response.json()

        except (
            aiohttp.ClientError,
            TimeoutError,
            asyncio.TimeoutError
        ):
            return None

        signatures = data.get("result")

        if not signatures:
            # An empty page after we already hold history means we've paged past
            # the first signature — the oldest we have is the mint's genesis.
            if oldest_block_time is not None:
                reached_genesis = True
            break

        for entry in signatures:
            block_time = entry.get("blockTime")

            if block_time is None:
                continue

            if (
                oldest_block_time is None
                or block_time < oldest_block_time
            ):
                oldest_block_time = block_time
                oldest_signature = entry.get("signature")

        if (
            early_exit_cutoff is not None
            and oldest_block_time is not None
            and oldest_block_time <= early_exit_cutoff
        ):
            # Confirmed old enough — stop before walking back to genesis.
            break

        if len(signatures) < MINT_AGE_RPC_PAGE_LIMIT:
            # Reached the natural end — this IS the creation tx
            reached_genesis = True
            break

        before = signatures[-1].get("signature")

        if not before:
            break

    if oldest_block_time is None:
        return None

    return {
        "block_time": oldest_block_time,
        "signature": oldest_signature,
        "source": "mint_tx",
        "reached_genesis": reached_genesis
    }


def build_age_result(result):

    block_time = result.get(
        "block_time"
    )

    if block_time is None:
        return result

    age_hours = (
        time.time()
        - block_time
    ) / 3600

    built = {
        "age_hours": age_hours,
        "source": result.get(
            "source",
            "mint_tx"
        ),
        "block_time": block_time,
        "signature": result.get(
            "signature"
        )
    }

    if "reached_genesis" in result:
        built["reached_genesis"] = result["reached_genesis"]

    return built


def passes_min_mint_age(age, chain="solana"):

    if age_unlimited(chain):
        return True

    if not age:
        return False

    return (
        age["age_hours"]
        >= MIN_TOKEN_AGE_HOURS
    )
