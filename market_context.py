import math

from config import (
    HYPEREVM_SCANNER_MAX_FDV_USD,
    HYPEREVM_SCANNER_MIN_LIQUIDITY_USD,
    MAX_FDV_USD,
    MAX_LIQUIDITY_USD,
    MIN_BONDING_CURVE_FDV_USD,
    MIN_LIQUIDITY_USD,
    PUMPFUN_BONDING_CURVE_REAL_TOKEN_RESERVES,
    PUMPFUN_INITIAL_VIRTUAL_SOL_RESERVES,
    PUMPFUN_INITIAL_VIRTUAL_TOKEN_RESERVES,
    PUMPFUN_SOL_USD_FALLBACK,
    PUMPFUN_TOTAL_SUPPLY
)


_LIVE_SOL_USD = 0


LAUNCHPAD_MARKERS = (
    "launch",
    "launchpad",
    "pump.fun",
    "pumpfun",
    "bonk.fun",
    "letsbonk",
    "bonding",
    "curve",
    "moonshot",
    "bags",
    "boop",
)


def safe_float(value, default=0):

    try:
        return float(value or default)
    except (TypeError, ValueError):
        return default


def set_live_sol_usd(sol_usd):
    global _LIVE_SOL_USD
    _LIVE_SOL_USD = max(safe_float(sol_usd, 0), 0)


def infer_sol_usd(pair):

    price_usd = safe_float(
        pair.get("priceUsd")
    )

    price_native = safe_float(
        pair.get("priceNative")
    )

    if price_usd > 0 and price_native > 0:
        return price_usd / price_native

    if _LIVE_SOL_USD > 0:
        return _LIVE_SOL_USD

    return PUMPFUN_SOL_USD_FALLBACK


def estimate_pumpfun_curve_liquidity(
    fdv,
    sol_usd
):

    if fdv <= 0 or sol_usd <= 0:
        return 0

    market_cap_sol = fdv / sol_usd

    k = (
        PUMPFUN_INITIAL_VIRTUAL_TOKEN_RESERVES
        * PUMPFUN_INITIAL_VIRTUAL_SOL_RESERVES
    )

    virtual_sol_reserves = math.sqrt(
        market_cap_sol
        * k
        / PUMPFUN_TOTAL_SUPPLY
    )

    real_sol_reserves = max(
        virtual_sol_reserves
        - PUMPFUN_INITIAL_VIRTUAL_SOL_RESERVES,
        0
    )

    return real_sol_reserves * sol_usd


def estimate_pumpfun_migration_fdv(
    sol_usd
):

    if sol_usd <= 0:
        return 0

    complete_virtual_token_reserves = (
        PUMPFUN_INITIAL_VIRTUAL_TOKEN_RESERVES
        - PUMPFUN_BONDING_CURVE_REAL_TOKEN_RESERVES
    )

    if complete_virtual_token_reserves <= 0:
        return 0

    k = (
        PUMPFUN_INITIAL_VIRTUAL_TOKEN_RESERVES
        * PUMPFUN_INITIAL_VIRTUAL_SOL_RESERVES
    )

    complete_virtual_sol_reserves = (
        k
        / complete_virtual_token_reserves
    )

    migration_market_cap_sol = (
        complete_virtual_sol_reserves
        / complete_virtual_token_reserves
        * PUMPFUN_TOTAL_SUPPLY
    )

    return migration_market_cap_sol * sol_usd


def build_migration_context(
    fdv,
    sol_usd,
    lifecycle
):

    if lifecycle != "bonding_curve":
        return {
            "migration_fdv": 0,
            "migration_distance_usd": 0,
            "migration_distance_pct": 0,
            "migration_fdv_source": ""
        }

    migration_fdv = estimate_pumpfun_migration_fdv(
        sol_usd
    )

    if migration_fdv <= 0:
        return {
            "migration_fdv": 0,
            "migration_distance_usd": 0,
            "migration_distance_pct": 0,
            "migration_fdv_source": ""
        }

    migration_distance_usd = migration_fdv - fdv

    return {
        "migration_fdv": migration_fdv,
        "migration_distance_usd": migration_distance_usd,
        "migration_distance_pct": (
            migration_distance_usd
            / migration_fdv
        ),
        "migration_fdv_source": "pumpfun_curve_estimate"
    }


def launchpad_text(pair):

    dex_id = str(
        pair.get("dexId")
        or ""
    ).lower()

    url = str(
        pair.get("url")
        or ""
    ).lower()

    labels = " ".join(
        str(label).lower()
        for label in pair.get("labels", [])
    )

    return " ".join(
        (
            dex_id,
            url,
            labels
        )
    )


def detect_launchpad_marker(pair):

    text = launchpad_text(pair)

    for marker in LAUNCHPAD_MARKERS:
        if marker in text:
            return marker

    return None


def is_pumpfun_curve_pair(
    pair
):

    text = launchpad_text(pair)
    base_token = pair.get(
        "baseToken",
        {}
    )
    address = str(
        base_token.get("address", "")
    ).lower()

    return (
        "pump.fun" in text
        or "pumpfun" in text
        or address.endswith("pump")
    )


def is_launchpad_curve_pair(
    pair,
    raw_liquidity,
    lifecycle_hint=None
):

    if pair.get("chainId", "solana") != "solana":
        return False

    return (
        raw_liquidity <= 0
        or bool(detect_launchpad_marker(pair))
        or (
            lifecycle_hint == "bonding_curve"
            and raw_liquidity <= 0
        )
    )


def build_market_context(
    pair,
    lifecycle_hint=None
):

    chain = str(
        pair.get("chainId", "solana")
    ).lower()
    liquidity_data = pair.get(
        "liquidity",
        {}
    )
    raw_base_reserve = safe_float(
        pair.get("raw_base_reserve"),
        0
    )
    raw_quote_reserve = safe_float(
        pair.get("raw_quote_reserve"),
        0
    )

    raw_liquidity = safe_float(
        liquidity_data.get("usd")
        if isinstance(liquidity_data, dict)
        else 0
    )

    fdv = safe_float(
        pair.get("fdv")
    )

    sol_usd = infer_sol_usd(pair)

    lifecycle = "migrated"
    liquidity = raw_liquidity
    liquidity_source = "dexscreener_pool"

    if is_launchpad_curve_pair(
        pair,
        raw_liquidity,
        lifecycle_hint=lifecycle_hint
    ):
        lifecycle = "bonding_curve"
        liquidity = estimate_pumpfun_curve_liquidity(
            fdv,
            sol_usd
        )
        liquidity_source = "launchpad_curve_estimate"

    migration_context = build_migration_context(
        fdv,
        sol_usd,
        (
            lifecycle
            if is_pumpfun_curve_pair(pair)
            else ""
        )
    )

    return {
        "fdv": fdv,
        "liquidity": liquidity,
        "raw_liquidity": raw_liquidity,
        "raw_base_reserve": raw_base_reserve,
        "raw_quote_reserve": raw_quote_reserve,
        "chain": chain,
        "sol_usd": sol_usd,
        "lifecycle": lifecycle,
        "liquidity_source": liquidity_source,
        **migration_context
    }


def is_scannable_market(context):

    fdv = context["fdv"]
    chain = str(
        context.get("chain", "solana")
    ).lower()

    if not fdv:
        return False

    if chain == "hyperevm":
        return (
            fdv <= HYPEREVM_SCANNER_MAX_FDV_USD
            and context["liquidity"]
            >= HYPEREVM_SCANNER_MIN_LIQUIDITY_USD
        )

    if fdv > MAX_FDV_USD:
        return False

    if context["lifecycle"] == "bonding_curve":
        return fdv >= MIN_BONDING_CURVE_FDV_USD

    liquidity = context["liquidity"]

    return (
        liquidity >= MIN_LIQUIDITY_USD
        and liquidity <= MAX_LIQUIDITY_USD
    )
