QUALITY_PLAYBOOK = {
    # ── route-canonical keys (alert_route == quality_tag for live alerts) ──

    "bonding_momentum_high_conviction": {
        "criteria": (
            "Bonding: 5m Vol/Liq >= 100%, "
            "1h txns >= 300, 1h volume >= $10k."
        ),
        "play": (
            "High-risk / high-reward bonding bucket. "
            "DB: 20.7% hit 2x but 43.7% close below entry at 1h. "
            "Fast-exit mode active — first partial fires at 1.5x. "
            "Take profits quickly; do not hold for 4x+ unless momentum confirms."
        )
    },
    "bonding_early_revival": {
        "criteria": (
            "Bonding: 5m Vol/Liq >= 40%, 5m txns >= 20, "
            "5m buy/sell >= 1.2x, 5m volume >= band minimum."
        ),
        "play": (
            "Early pressure bucket. Enter smaller, add only if fresh "
            "5m volume expands and price holds above call zone."
        )
    },
    "bonding_scalp": {
        "criteria": (
            "Bonding: 5m Vol/Liq >= 200% and 1h txns < 150."
        ),
        "play": (
            "Fast scalp bucket. Expect volatility; take partials quickly "
            "and avoid giving a full candle back. "
            "Spike impulse (>=1.5x) on this route is a lottery — "
            "size accordingly."
        )
    },
    "bonding_momentum_scalp": {
        "criteria": (
            "Bonding: momentum criteria met but no high-conviction or "
            "early-revival quality qualifier."
        ),
        "play": (
            "Momentum scalp. Treat like bonding_scalp — "
            "take partials quickly, no full holds."
        )
    },
    "immediate": {
        "criteria": (
            "Passed the active FDV band route gates; migrated lifecycle."
        ),
        "play": (
            "Migrated alert. Use the raw flow: prefer higher 5m Vol/Liq, "
            "more 5m txns, and fresh 5m volume share."
        )
    },
    "low_fdv_accumulation": {
        "criteria": (
            "FDV < $10k, liquidity >= $1k, "
            "5m Vol/Liq >= 150%, 1h Vol/Liq >= 10x, "
            "red or flat 5m, green 1h and 6h."
        ),
        "play": (
            "HENRY-style accumulation bucket. This is early flow into "
            "a low-liquidity pullback; prioritize tracking and avoid "
            "treating weak impulse as an automatic reject."
        )
    },
    "migrated_revival": {
        "criteria": (
            "Migrated: price dumped 50-90% from post-graduation peak, "
            "now showing 5m Vol/Liq >= 30%, 5m txns >= 15, "
            "5m buy/sell >= 1.2x, 5m volume >= $500."
        ),
        "play": (
            "Post-graduation washout recovery. The prior dump is the edge — "
            "deeper washouts (70-85%) have the best reward. "
            "Manage fast: take first partial at 1.3x, tighten trail if "
            "volume fades. The dump trend can resume quickly."
        )
    },
    "hyperevm_ignition": {
        "criteria": (
            "HyperEVM: 1h price change >= 100%, "
            "1h volume >= $3k. 5m impulse and participant count ignored."
        ),
        "play": (
            "HyperEVM continuation. Treat it as a chain-specific grind "
            "setup; manage with 15m RSI and avoid forcing Solana-style "
            "5m participation rules onto it."
        )
    },
    "cto_metadata_change": {
        "criteria": (
            "Website, social, banner, image, or description changed "
            "after the scanner had already seen the token, with live "
            "flow confirmation."
        ),
        "play": (
            "Possible CTO or team refresh. Treat metadata alone as a "
            "watch signal; prefer entries only when volume and buys confirm."
        )
    },

    # ── state-only tags (never become alert_route, kept for snapshot tracking) ──

    "migrated_high_quality": {
        "criteria": (
            "Migrated: impulse >= 2.0x, 5m Vol/Liq >= 40%, "
            "5m txns >= 50, 5m volume >= 50% of 1h volume."
        ),
        "play": (
            "Strong migrated ignition. Best migrated bucket; favor "
            "momentum entries while 5m volume remains dominant."
        )
    },
    "migrated_stale": {
        "criteria": (
            "Migrated: 5m volume is <35% of 1h volume."
        ),
        "play": (
            "Continuation may be late. Be selective; prefer reclaim "
            "or fresh 5m expansion before sizing."
        )
    },
    "migrated_fragile": {
        "criteria": (
            "Migrated reject: FDV >= $40k and 5m Vol/Liq < 25%."
        ),
        "play": (
            "Usually skip. High FDV with weak live pressure often fades "
            "unless new volume arrives."
        )
    },
    "extended_cooling_reject": {
        "criteria": (
            "Bonding reject: max 1h/6h move >= 300% and "
            "5m Vol/Liq < 40%."
        ),
        "play": (
            "Usually skip. It already moved hard and current pressure "
            "is not confirming continuation."
        )
    },
    "standard": {
        "criteria": (
            "Passed the active FDV band route but no special quality tag."
        ),
        "play": (
            "Normal alert. Use the raw flow: prefer higher 5m Vol/Liq, "
            "more 5m txns, and fresh 5m volume share."
        )
    }
}

# Maps legacy quality_tag values (pre-unification) to canonical route names.
_LEGACY_TAG_MAP = {
    "high_conviction": "bonding_momentum_high_conviction",
    "early_revival": "bonding_early_revival",
    "speculative_scalp": "bonding_scalp",
    "migrated_early_revival": "migrated_revival",
    "hyperevm_slow_cook": "hyperevm_ignition",
}

# Short human-readable display labels used in alert headers and Telegram cards.
ROUTE_DISPLAY_NAMES = {
    "bonding_momentum_high_conviction": "HC Bonding",
    "bonding_early_revival": "Early Revival",
    "bonding_scalp": "Scalp",
    "bonding_momentum_scalp": "Momentum Scalp",
    "immediate": "Migrated",
    "low_fdv_accumulation": "Low-FDV Accum",
    "migrated_revival": "Migrated Revival",
    "hyperevm_ignition": "HyperEVM",
    "cto_metadata_change": "CTO Metadata",
}


def route_display_name(route):
    return ROUTE_DISPLAY_NAMES.get(
        str(route or ""),
        str(route or "unknown")
    )


def get_quality_playbook(
    quality_tag
):

    normalized_tag = str(quality_tag or "standard")
    normalized_tag = _LEGACY_TAG_MAP.get(
        normalized_tag,
        normalized_tag
    )

    return QUALITY_PLAYBOOK.get(
        normalized_tag,
        QUALITY_PLAYBOOK["standard"]
    )


def format_quality_note(
    quality_tag
):

    entry = get_quality_playbook(quality_tag)

    return (
        f"Tag Criteria: {entry['criteria']}\n"
        f"Play: {entry['play']}"
    )
