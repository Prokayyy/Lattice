import asyncio
import signal
import statistics
import time
import traceback
from collections import Counter
from datetime import datetime

from analysis.pattern_analyzer import (
    LLMPatternAnalyzer
)

from alerts.telegram import (
    TelegramAlertSender
)

from agents.telegram_agent import (
    TelegramCommandAgent
)

from config import (
    ALERT_WINDOW_ENTRY_ENABLED,
    ALERT_WINDOW_ENTRY_SHADOW_MODE,
    ALERT_WINDOW_ENTRY_SECONDS,
    ALERT_WINDOW_MAX_RUN_PCT,
    ALERT_WINDOW_MAX_DROP_PCT,
    ALERT_WINDOW_MIN_SCORE,
    ALERT_WINDOW_ROUTES,
    ANCHORED_VWAP_CANDLE_LIMIT,
    ANCHORED_VWAP_ENABLED,
    ANCHORED_VWAP_LOOKBACK_SECONDS,
    ANCHORED_VWAP_MIN_CANDLES,
    ANCHORED_VWAP_PROVIDER_MAX_PAGES,
    ANCHORED_VWAP_PROVIDER_PADDING_SECONDS,
    ANCHORED_VWAP_PROVIDER_REFRESH_ENABLED,
    ANCHORED_VWAP_PROVIDER_REFRESH_SECONDS,
    ANCHORED_VWAP_TIMEFRAME_SECONDS,
    CANDIDATE_REFRESH_INTERVAL,
    CTO_METADATA_ALERT_COOLDOWN_SECONDS,
    CTO_METADATA_ALERTS_ENABLED,
    CTO_METADATA_MIN_BASE_SCORE,
    CTO_METADATA_MIN_BUY_SELL_VOLUME_RATIO,
    CTO_METADATA_MIN_PRESSURE,
    CTO_METADATA_MIN_VOLUME_LIQUIDITY_RATIO,
    CTO_METADATA_SCORE_BONUS,
    IGNITION_BONDING_EARLY_REVIVAL_MIN_BUY_SELL_RATIO_5M,
    IGNITION_BONDING_EARLY_REVIVAL_MIN_TXNS_5M,
    IGNITION_BONDING_EARLY_REVIVAL_MIN_VOLUME_LIQUIDITY_RATIO_5M,
    IGNITION_BONDING_EXTENDED_COOLING_MAX_VOLUME_LIQUIDITY_RATIO_5M,
    IGNITION_BONDING_EXTENDED_COOLING_MIN_PRICE_CHANGE,
    IGNITION_BONDING_HIGH_CONVICTION_MIN_TXNS_1H,
    IGNITION_BONDING_HIGH_CONVICTION_MIN_VOLUME_1H,
    IGNITION_BONDING_HIGH_CONVICTION_MIN_VOLUME_LIQUIDITY_RATIO_5M,
    IGNITION_BONDING_MOMENTUM_MIN_BUY_SELL_RATIO_1H,
    IGNITION_BONDING_MOMENTUM_MIN_PRICE_CHANGE_1H,
    IGNITION_BONDING_MOMENTUM_MIN_PRICE_CHANGE_6H,
    IGNITION_BONDING_MOMENTUM_MIN_TXNS_1H,
    IGNITION_BONDING_MOMENTUM_MIN_VOLUME_LIQUIDITY_RATIO_1H,
    IGNITION_BONDING_MOMENTUM_MIN_VOLUME_MULTIPLE_1H,
    IGNITION_BONDING_SCALP_MAX_TXNS_1H,
    IGNITION_BONDING_SCALP_MIN_VOLUME_LIQUIDITY_RATIO_5M,
    IGNITION_BONDING_CURVE_BANDS,
    IGNITION_ALERT_COOLDOWN_SECONDS,
    IGNITION_ALERT_THRESHOLD,
    IGNITION_RECALL_OVERRIDE_ENABLED,
    IGNITION_RECALL_OVERRIDE_MIN_SECONDS,
    IGNITION_RECALL_OVERRIDE_PRICE_MULTIPLE,
    IGNITION_RECALL_OVERRIDE_PRICE_STEP,
    IGNITION_RECALL_OVERRIDE_VOLUME_MULTIPLE,
    IGNITION_LOW_FDV_ACCUMULATION_MAX_FDV,
    IGNITION_LOW_FDV_ACCUMULATION_MAX_PRICE_CHANGE_5M,
    IGNITION_LOW_FDV_ACCUMULATION_MIN_BUY_SELL_RATIO_1H,
    IGNITION_LOW_FDV_ACCUMULATION_MIN_BUY_SELL_RATIO_5M,
    IGNITION_LOW_FDV_ACCUMULATION_MIN_LIQUIDITY,
    IGNITION_LOW_FDV_ACCUMULATION_MIN_PRICE_CHANGE_1H,
    IGNITION_LOW_FDV_ACCUMULATION_MIN_PRICE_CHANGE_6H,
    IGNITION_LOW_FDV_ACCUMULATION_MIN_VOLUME_LIQUIDITY_RATIO_1H,
    IGNITION_LOW_FDV_ACCUMULATION_MIN_VOLUME_LIQUIDITY_RATIO_5M,
    IGNITION_MIGRATED_BUY_SELL_SCORE_CAP_POINTS,
    IGNITION_MIGRATED_BUY_SELL_SCORE_CAP_TXNS_5M,
    IGNITION_MIGRATED_FRAGILE_MAX_VOLUME_LIQUIDITY_RATIO_5M,
    IGNITION_MIGRATED_FRAGILE_MIN_FDV,
    IGNITION_MIGRATED_HIGH_QUALITY_MIN_PRICE_JUMP,
    IGNITION_MIGRATED_HIGH_QUALITY_MIN_TXNS_5M,
    IGNITION_MIGRATED_HIGH_QUALITY_MIN_VOLUME_LIQUIDITY_RATIO_5M,
    IGNITION_MIGRATED_HIGH_QUALITY_MIN_VOLUME_SHARE_5M_1H,
    IGNITION_MIGRATED_BANDS,
    IGNITION_MIGRATED_REVIVAL_MIN_DRAWDOWN_PCT,
    IGNITION_MIGRATED_REVIVAL_MAX_DRAWDOWN_PCT,
    IGNITION_MIGRATED_REVIVAL_MIN_VOLUME_LIQUIDITY_RATIO_5M,
    IGNITION_MIGRATED_REVIVAL_MIN_TXNS_5M,
    IGNITION_MIGRATED_REVIVAL_MIN_BUY_SELL_RATIO_5M,
    IGNITION_MIGRATED_REVIVAL_MIN_VOLUME_5M_USD,
    IGNITION_MIGRATED_STALE_MAX_VOLUME_SHARE_5M_1H,
    IGNITION_EXTENDED_6H_MOVE_PENALTY,
    IGNITION_MIGRATED_STALE_VOLUME_SHARE_PENALTY,
    GRPC_IMMEDIATE_SCAN_COOLDOWN_SECONDS,
    HYPEREVM_IGNITION_MAX_FDV_USD,
    HYPEREVM_IGNITION_MIN_LIQUIDITY_USD,
    HYPEREVM_IGNITION_MIN_PRICE_CHANGE_24H,
    HYPEREVM_IGNITION_MIN_PRICE_CHANGE_5M,
    HYPEREVM_IGNITION_MIN_VOLUME_1H_USD,
    HYPEREVM_IGNITION_SCORE,
    MAX_CANDIDATES,
    ALERT_PERFORMANCE_SUMMARY_INTERVAL_SECONDS,
    LLM_PATTERN_REPORTS_ENABLED,
    LLM_PATTERN_REPORT_INTERVAL_SECONDS,
    LLM_PATTERN_REPORT_LOOKBACK_HOURS,
    LLM_PATTERN_REPORT_MIN_ALERTS,
    LOCAL_RSI_ENABLED,
    LOCAL_RSI_TIMEFRAME_SECONDS,
    MOBULA_SAFETY_CHAINS,
    DEFINITIVE_ENTRY_CONFIRM_FILL_SECONDS,
    LIVE_EXECUTION_RETRY_ENABLED,
    LIVE_EXECUTION_RETRY_INITIAL_DELAY_SECONDS,
    LIVE_EXECUTION_RETRY_MAX_DELAY_SECONDS,
    LIVE_EXECUTION_ENTRY_RETRY_MAX_PRICE_RUN_PCT,
    REQUIRE_LIQUIDITY_LOCK,
    PRIORITY_SCANNER_COOLDOWN_SECONDS,
    PRIORITY_SCANNER_ENABLED,
    PRIORITY_SCANNER_MAX_QUEUE,
    PRIORITY_SCANNER_MIN_BUY_SELL_VOLUME_RATIO,
    PRIORITY_SCANNER_MIN_PRESSURE,
    PRIORITY_SCANNER_MIN_SCORE,
    PRIORITY_SCANNER_MIN_VOLUME_LIQUIDITY_RATIO,
    POSITION_MAX_OPEN_POSITIONS,
    POSITION_MIN_ENTRY_BUY_SELL_VOLUME_RATIO,
    POSITION_MIN_ENTRY_VOLUME_1H_USD,
    POSITION_MIN_ENTRY_VOLUME_MULTIPLE,
    GRPC_PRICE_MAX_DEX_DEVIATION_PCT,
    POSITION_OPEN_POSITION_SCAN_INTERVAL_SECONDS,
    POSITION_PRICE_QUOTE_SANITY_ENABLED,
    POSITION_PRICE_QUOTE_SANITY_MAX_DEVIATION_PCT,
    POSITION_PRICE_QUOTE_SANITY_MIN_QUOTE_VALUE_USD,
    POSITION_STATUS_REPORT_INTERVAL_SECONDS,
    SCANNER_TELEMETRY_ARCHIVE_DATABASE,
    SCANNER_TELEMETRY_ARCHIVE_ENABLED,
    SCANNER_TELEMETRY_PRUNE_INTERVAL_SECONDS,
    SCANNER_TELEMETRY_RETENTION_DAYS,
    SCANNER_TELEMETRY_RETENTION_BY_TABLE,
    ROUTE_OUTCOME_CACHE_SECONDS,
    ROUTE_OUTCOME_APPLY_MIN_ALERTS,
    ROUTE_OUTCOME_FALSE_POSITIVE_PENALTY_SCALE,
    ROUTE_OUTCOME_LOOKBACK_DAYS,
    ROUTE_OUTCOME_MAX_BONUS,
    ROUTE_OUTCOME_MAX_PENALTY,
    ROUTE_OUTCOME_MIN_ALERTS,
    ROUTE_OUTCOME_SCORING_ENABLED,
    ROUTE_OUTCOME_WINDOW_SECONDS,
    SCANNER_BAD_EVIDENCE_MEMORY_ENABLED,
    SCANNER_BAD_EVIDENCE_MEMORY_WINDOW_SECONDS,
    SCANNER_ENABLED_CHAINS,
    SCAN_GATE_ATTRITION_REPORT_ENABLED,
    SCAN_GATE_ATTRITION_REPORT_INTERVAL_SECONDS,
    SCAN_GATE_ATTRITION_REPORT_TOP_N,
)

from filters.safety import (
    SafetyChecker
)

from filters.contracts import (
    is_excluded_contract_address
)

from models import (
    TokenMetrics
)

from market_context import (
    build_market_context,
    is_scannable_market,
    set_live_sol_usd
)

from config import (
    PUMPFUN_TOTAL_SUPPLY as _PUMPFUN_TOTAL_SUPPLY
)

from sources.dexscreener import (
    DexScreenerClient
)

from sources.mint_age import (
    passes_min_mint_age,
    resolve_mint_age
)

from sources.token_lineage import (
    build_ticker_lineage_section
)

from sources.discovery import (
    CandidateDiscovery
)

from sources.trending_cache import (
    cache_loaded as trending_cache_loaded,
    find_trending_match,
    refresh_trending_cache
)

from sources.yellowstone import (
    YellowstoneImpulseListener
)

from storage.sqlite import (
    ScannerStorage
)

from trading.position_engine import (
    PositionEngine
)

from trading.candles import (
    anchored_vwap_from_low,
    anchored_vwap_from_time
)

from trading.alert_report import (
    fetch_ohlcv_window
)

from trading.execution import (
    LiveExecutionManager
)

from trading.live_prices import (
    best_live_pair,
    fetch_live_prices,
    SolUsdPriceFeed
)

from state import (
    GRPC_POSITION_PRICES,
    hydrate_ignition_memory,
    migration_drawdown_pct,
    persist_ignition_call,
    POSITION_WATCH_ACCOUNTS,
    PRIORITY_SCAN_QUEUE,
    PRIORITY_SCAN_SET,
    TOKEN_MEMORY,
    TRACKED_CANDIDATES,
    update_migration_tracking
)


telegram = TelegramAlertSender()

safety = SafetyChecker()

position_engine = PositionEngine()

live_execution = LiveExecutionManager()

sol_usd_price_feed = SolUsdPriceFeed()

scanner_storage = ScannerStorage()

llm_pattern_analyzer = LLMPatternAnalyzer()

RUNNER_RSI_LAST_OBSERVED_AT = {}
ANCHORED_VWAP_PROVIDER_FETCHED_AT = {}
LIVE_EXECUTION_RETRY_TASKS = {}
LIVE_ORDER_LOCK = asyncio.Lock()

TOKEN_HISTORY_MAX_POINTS = 30


async def refresh_position_sol_usd(
    force=False
):

    sol_usd = await sol_usd_price_feed.get_price(
        force=force
    )
    position_engine.set_sol_usd(sol_usd)
    set_live_sol_usd(sol_usd)

    return sol_usd


TIER_INTERVALS = {
    1: 60,
    2: 180
}


ROTATION_BATCH_SIZE = 50
MARKET_BENCHMARK_CACHE = {
    "cached_at": 0,
    "value": {}
}
MARKET_BENCHMARK_CACHE_SECONDS = 120
ROUTE_OUTCOME_SCORE_CACHE = {
    "cached_at": 0,
    "value": {}
}
SCAN_GATE_ATTRITION = Counter()
SCAN_GATE_ATTRITION_LAST_REPORT_AT = 0


def local_day_window(now=None):

    current = now or time.time()
    local_now = datetime.fromtimestamp(
        current
    ).astimezone()
    start = local_now.replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0
    )

    return start.timestamp(), current


def safe_float(
    value,
    default=0
):

    try:
        return float(value or default)
    except (TypeError, ValueError):
        return default


def normalize_alert_route(
    route
):

    route = str(route or "none")

    if route == "hyperevm_slow_cook":
        return "hyperevm_ignition"

    return route


def route_outcome_scores(
    now=None
):

    if not ROUTE_OUTCOME_SCORING_ENABLED:
        return {}

    current = now or time.time()

    if (
        ROUTE_OUTCOME_SCORE_CACHE["value"]
        and current - ROUTE_OUTCOME_SCORE_CACHE["cached_at"]
        < ROUTE_OUTCOME_CACHE_SECONDS
    ):
        return ROUTE_OUTCOME_SCORE_CACHE["value"]

    since = None

    if ROUTE_OUTCOME_LOOKBACK_DAYS > 0:
        since = current - ROUTE_OUTCOME_LOOKBACK_DAYS * 86400

    try:
        scores = scanner_storage.load_route_outcome_scores(
            since=since,
            until=current,
            window_seconds=ROUTE_OUTCOME_WINDOW_SECONDS,
            min_alerts=ROUTE_OUTCOME_MIN_ALERTS,
            max_bonus=ROUTE_OUTCOME_MAX_BONUS,
            max_penalty=ROUTE_OUTCOME_MAX_PENALTY,
            false_positive_penalty_scale=(
                ROUTE_OUTCOME_FALSE_POSITIVE_PENALTY_SCALE
            )
        )
    except Exception as e:
        print(
            f"Route outcome score load error: {e}"
        )
        scores = {}

    ROUTE_OUTCOME_SCORE_CACHE["cached_at"] = current
    ROUTE_OUTCOME_SCORE_CACHE["value"] = scores

    return scores


def apply_route_outcome_score(
    ignition_score,
    ignition_details,
    ignition_breakdown
):

    route = normalize_alert_route(
        ignition_details.get("alert_route")
    )

    if route == "none":
        return ignition_score

    score_rows = route_outcome_scores()
    route_score = score_rows.get(route)

    if not route_score:
        return ignition_score

    adjustment = safe_float(
        route_score.get("score_adjustment"),
        0
    )
    alert_count = int(
        safe_float(
            route_score.get("alerts"),
            0
        )
    )
    apply_adjustment = (
        alert_count >= ROUTE_OUTCOME_APPLY_MIN_ALERTS
    )
    adjusted_score = max(
        0,
        min(
            100,
            int(
                round(
                    ignition_score
                    + (
                        adjustment
                        if apply_adjustment
                        else 0
                    )
                )
            )
        )
    )

    ignition_details["alert_route"] = route
    ignition_details["route_outcome_base_score"] = ignition_score
    ignition_details["route_outcome_adjustment"] = adjustment
    ignition_details["route_outcome_adjustment_applied"] = (
        apply_adjustment
    )
    ignition_details["route_outcome_shadowed"] = (
        not apply_adjustment
    )
    ignition_details["route_outcome_apply_min_alerts"] = (
        ROUTE_OUTCOME_APPLY_MIN_ALERTS
    )
    ignition_details["route_confidence_tier"] = route_score.get(
        "confidence_tier",
        "unproven"
    )
    ignition_details["route_outcome_alerts"] = alert_count
    ignition_details["route_outcome_hit_2x_rate"] = route_score.get(
        "hit_2x_rate",
        0
    )
    ignition_details[
        "route_outcome_false_positive_rate"
    ] = route_score.get(
        "false_positive_rate",
        0
    )
    ignition_details[
        "route_outcome_avg_peak_multiple"
    ] = route_score.get(
        "avg_peak_multiple",
        0
    )

    if adjustment and apply_adjustment:
        ignition_breakdown.append(
            "Route outcome "
            f"{route} {adjustment:+.1f} "
            f"({route_score.get('confidence_tier', 'unproven')})"
        )
    elif adjustment:
        ignition_breakdown.append(
            "Route outcome shadow "
            f"{route} {adjustment:+.1f} "
            f"({alert_count}/"
            f"{ROUTE_OUTCOME_APPLY_MIN_ALERTS} alerts)"
        )

    return adjusted_score


def pair_missing_data_fields(
    pair,
    trade_volumes=None
):

    missing = []
    volume = pair.get("volume") or {}
    txns = pair.get("txns") or {}
    price_change = pair.get("priceChange") or {}
    txns_5m = txns.get("m5") or {}

    if "m5" not in volume:
        missing.append("5m_volume")

    if "m5" not in price_change:
        missing.append("5m_price")

    if "h24" not in price_change:
        missing.append("24h_price")

    if "m5" not in txns:
        missing.append("5m_txns")

    if "buys" not in txns_5m or "sells" not in txns_5m:
        missing.append("5m_buy_sell_txns")

    if (
        trade_volumes
        and trade_volumes.get("source_5m") != "observed_flows"
    ):
        missing.append("5m_buy_sell_dollar_flow")

    return missing


BAD_EVIDENCE_MISSING_FIELDS = {
    "5m_price",
    "5m_vol_liq",
    "5m_volume",
    "5m_buy_sell",
    "5m_buy_sell_dollar_flow",
    "flow_unconfirmed_5m"
}


def update_discovery_bad_evidence_memory(
    memory,
    snapshot,
    now
):

    if not SCANNER_BAD_EVIDENCE_MEMORY_ENABLED:
        return

    missing = set(snapshot.get("missing") or [])
    data_missing = set(snapshot.get("data_missing") or [])
    evidence_missing = bool(
        (missing | data_missing) & BAD_EVIDENCE_MISSING_FIELDS
    )
    route = normalize_alert_route(snapshot.get("alert_route"))
    no_5m_market = (
        safe_float(snapshot.get("volume_5m"), 0) <= 0
        and abs(safe_float(snapshot.get("price_change_5m"), 0)) <= 1e-12
    )
    bad = evidence_missing or (route == "none" and no_5m_market)
    last_bad_at = safe_float(memory.get("last_bad_evidence_at"), 0)

    if bad:
        if (
            last_bad_at <= 0
            or now - last_bad_at > SCANNER_BAD_EVIDENCE_MEMORY_WINDOW_SECONDS
        ):
            memory["bad_evidence_count"] = 1
        else:
            memory["bad_evidence_count"] = (
                int(safe_float(memory.get("bad_evidence_count"), 0)) + 1
            )
        memory["last_bad_evidence_at"] = now
        memory["last_bad_evidence_reason"] = ",".join(
            sorted((missing | data_missing) & BAD_EVIDENCE_MISSING_FIELDS)
        ) or "route_none_no_5m_market"
    else:
        memory["bad_evidence_count"] = max(
            0,
            int(safe_float(memory.get("bad_evidence_count"), 0)) - 1
        )


def record_scan_gate_attrition(
    metrics,
    pair,
    ignition_score,
    ignition_details,
    ignition_triggered,
    entry_precheck_reason=None,
    trade_volumes=None
):

    if not SCAN_GATE_ATTRITION_REPORT_ENABLED:
        return

    chain = str(
        getattr(metrics, "chain", "")
        or "unknown"
    ).lower()
    route = normalize_alert_route(
        ignition_details.get("alert_route")
    )
    missing_data = pair_missing_data_fields(
        pair,
        trade_volumes=trade_volumes
    )

    ignition_details["data_missing"] = missing_data
    SCAN_GATE_ATTRITION["scans"] += 1
    SCAN_GATE_ATTRITION[f"chain:{chain}"] += 1

    for field in missing_data:
        SCAN_GATE_ATTRITION[f"missing:{field}"] += 1

    if not ignition_details.get("alert_eligible", False):
        reason = ignition_details.get("reason", "alert_not_eligible")
        SCAN_GATE_ATTRITION[f"alert_block:{reason}"] += 1

        for field in ignition_details.get("missing", []) or []:
            SCAN_GATE_ATTRITION[f"alert_missing:{field}"] += 1

        return

    if not ignition_triggered:
        SCAN_GATE_ATTRITION["alert_block:score_below_threshold"] += 1
        SCAN_GATE_ATTRITION[f"route_seen:{route}"] += 1
        return

    SCAN_GATE_ATTRITION[f"alert_ready:{route}"] += 1

    if entry_precheck_reason:
        SCAN_GATE_ATTRITION[
            f"entry_block:{entry_precheck_reason}"
        ] += 1
    elif ignition_details.get("entry_confirmation_would_block"):
        SCAN_GATE_ATTRITION[
            "entry_shadow_block:entry_confirmation"
        ] += 1
    else:
        SCAN_GATE_ATTRITION["entry_ready"] += 1


def maybe_print_scan_gate_attrition(
    now=None
):

    global SCAN_GATE_ATTRITION_LAST_REPORT_AT

    if not SCAN_GATE_ATTRITION_REPORT_ENABLED:
        return

    current = now or time.time()
    interval = max(
        SCAN_GATE_ATTRITION_REPORT_INTERVAL_SECONDS,
        1
    )

    if (
        SCAN_GATE_ATTRITION_LAST_REPORT_AT
        and current - SCAN_GATE_ATTRITION_LAST_REPORT_AT
        < interval
    ):
        return

    SCAN_GATE_ATTRITION_LAST_REPORT_AT = current

    total = SCAN_GATE_ATTRITION.get("scans", 0)

    if total <= 0:
        return

    top_n = max(
        SCAN_GATE_ATTRITION_REPORT_TOP_N,
        1
    )
    top_items = [
        (key, count)
        for key, count in SCAN_GATE_ATTRITION.most_common()
        if key != "scans"
    ][:top_n]

    print(
        "Scan gate attrition "
        f"({total} scans): "
        + " | ".join(
            f"{key}={count}"
            for key, count in top_items
        )
    )


def enqueue_priority_scan(
    token_address,
    memory,
    reason,
    now=None
):

    if not PRIORITY_SCANNER_ENABLED:
        return False

    if token_address in PRIORITY_SCAN_SET:
        return False

    if len(PRIORITY_SCAN_QUEUE) >= PRIORITY_SCANNER_MAX_QUEUE:
        return False

    current = now or time.time()
    last_queued = safe_float(
        memory.get("last_priority_scan_queued_at"),
        0
    )

    if (
        current - last_queued
        < PRIORITY_SCANNER_COOLDOWN_SECONDS
    ):
        return False

    PRIORITY_SCAN_QUEUE.append(
        token_address
    )
    PRIORITY_SCAN_SET.add(
        token_address
    )
    memory["last_priority_scan_queued_at"] = current
    memory["last_priority_scan_reason"] = reason

    return True


def priority_rescan_reason(
    metrics,
    ignition_score,
    ignition_details,
    pressure
):

    if not PRIORITY_SCANNER_ENABLED:
        return None

    route = normalize_alert_route(
        ignition_details.get("alert_route")
    )
    volume_liquidity_ratio = safe_float(
        ignition_details.get("volume_liquidity_ratio"),
        0
    )
    buy_sell_volume_ratio = (
        safe_float(metrics.buy_volume_5m, 0)
        / max(
            safe_float(metrics.sell_volume_5m, 0),
            1
        )
    )

    if ignition_score >= PRIORITY_SCANNER_MIN_SCORE:
        return f"score_{ignition_score}"

    if (
        route != "none"
        and pressure >= PRIORITY_SCANNER_MIN_PRESSURE
    ):
        return "route_pressure"

    if (
        pressure >= PRIORITY_SCANNER_MIN_PRESSURE
        and volume_liquidity_ratio
        >= PRIORITY_SCANNER_MIN_VOLUME_LIQUIDITY_RATIO
        and buy_sell_volume_ratio
        >= PRIORITY_SCANNER_MIN_BUY_SELL_VOLUME_RATIO
    ):
        return "flow_strength"

    return None


def ignition_recall_override_reason(
    metrics,
    ignition_details,
    memory,
    now,
    entry_precheck_reason
):

    if not IGNITION_RECALL_OVERRIDE_ENABLED:
        return None

    last_alert = safe_float(
        memory.get("last_ignition_alert"),
        0
    )

    if last_alert <= 0:
        return None

    if now - last_alert >= IGNITION_ALERT_COOLDOWN_SECONDS:
        return None

    last_override = safe_float(
        memory.get("last_ignition_recall_override_at"),
        0
    )

    if (
        last_override > 0
        and now - last_override
        < IGNITION_RECALL_OVERRIDE_MIN_SECONDS
    ):
        return None

    volume_multiple = position_engine.entry_volume_multiple(
        metrics
    )
    first_price = safe_float(
        memory.get("first_ignition_price"),
        0
    )
    current_price = safe_float(
        metrics.price,
        0
    )
    price_multiple = (
        current_price / first_price
        if first_price > 0
        and current_price > 0
        else 0
    )

    ignition_details[
        "recall_override_volume_multiple"
    ] = volume_multiple
    ignition_details[
        "recall_override_price_multiple"
    ] = price_multiple

    if entry_precheck_reason is None:
        return "entry_ready"

    if ignition_details.get("upgraded_high_conviction_after_call"):
        return "high_conviction_upgrade"

    last_volume_multiple = safe_float(
        memory.get("last_ignition_recall_volume_multiple"),
        0
    )

    if (
        volume_multiple
        >= IGNITION_RECALL_OVERRIDE_VOLUME_MULTIPLE
        and last_volume_multiple
        < IGNITION_RECALL_OVERRIDE_VOLUME_MULTIPLE
    ):
        return f"volume_multiple_{volume_multiple:.2f}x"

    last_price_multiple = max(
        safe_float(
            memory.get("last_ignition_recall_price_multiple"),
            0
        ),
        1
    )

    if (
        price_multiple
        >= IGNITION_RECALL_OVERRIDE_PRICE_MULTIPLE
        and price_multiple
        >= last_price_multiple
        + IGNITION_RECALL_OVERRIDE_PRICE_STEP
    ):
        return f"new_high_{price_multiple:.2f}x"

    return None


def metadata_payload_from_pair_info(
    website,
    twitter,
    telegram_link,
    banner,
    image_url,
    description
):

    return {
        "website": website,
        "twitter": twitter,
        "telegram": telegram_link,
        "banner": banner,
        "image_url": image_url,
        "description": description
    }


def update_metadata_memory(
    memory,
    current_metadata
):

    snapshot = memory["metadata_snapshot"]
    fields = (
        "website",
        "twitter",
        "telegram",
        "banner",
        "image_url",
        "description"
    )

    if not memory.get("metadata_initialized"):
        for field in fields:
            snapshot[field] = current_metadata.get(field)

        memory["metadata_initialized"] = True
        memory["recent_metadata_change"] = False
        memory["last_metadata_change_fields"] = []
        return []

    changed_fields = []

    for field in fields:
        previous = snapshot.get(field)
        current = current_metadata.get(field)

        if previous == current:
            continue

        snapshot[field] = current
        changed_fields.append(field)

    if changed_fields:
        memory["metadata_mutations"] += 1
        memory["recent_metadata_change"] = True
        memory["last_metadata_change_fields"] = changed_fields
    else:
        memory["recent_metadata_change"] = False
        memory["last_metadata_change_fields"] = []

    return changed_fields


def apply_cto_metadata_signal(
    metrics,
    memory,
    ignition_score,
    ignition_details,
    ignition_breakdown,
    pressure,
    now
):

    if not CTO_METADATA_ALERTS_ENABLED:
        return ignition_score

    changed_fields = list(
        memory.get("last_metadata_change_fields") or []
    )

    if not changed_fields:
        return ignition_score

    if (
        now
        - safe_float(memory.get("last_metadata_alert_at"), 0)
        < CTO_METADATA_ALERT_COOLDOWN_SECONDS
    ):
        ignition_details["metadata_change_suppressed"] = (
            "metadata_alert_cooldown"
        )
        return ignition_score

    volume_liquidity_ratio = safe_float(
        ignition_details.get("volume_liquidity_ratio"),
        0
    )
    buy_sell_volume_ratio = (
        safe_float(metrics.buy_volume_5m, 0)
        / max(
            safe_float(metrics.sell_volume_5m, 0),
            1
        )
    )

    flow_confirmed = (
        pressure >= CTO_METADATA_MIN_PRESSURE
        and volume_liquidity_ratio
        >= CTO_METADATA_MIN_VOLUME_LIQUIDITY_RATIO
        and buy_sell_volume_ratio
        >= CTO_METADATA_MIN_BUY_SELL_VOLUME_RATIO
    )
    base_score_ok = (
        ignition_score >= CTO_METADATA_MIN_BASE_SCORE
    )

    ignition_details["metadata_change_detected"] = True
    ignition_details["metadata_changed_fields"] = changed_fields
    ignition_details["metadata_change_flow_confirmed"] = flow_confirmed
    ignition_details[
        "metadata_change_buy_sell_volume_ratio"
    ] = buy_sell_volume_ratio

    if not flow_confirmed or not base_score_ok:
        return ignition_score

    adjusted_score = min(
        100,
        max(
            ignition_score,
            ignition_score + CTO_METADATA_SCORE_BONUS,
            IGNITION_ALERT_THRESHOLD
        )
    )

    ignition_details["metadata_special_alert"] = True
    ignition_details["metadata_score_bonus"] = (
        adjusted_score - ignition_score
    )
    ignition_details["alert_eligible"] = True

    if normalize_alert_route(
        ignition_details.get("alert_route")
    ) == "none":
        ignition_details["alert_route"] = "cto_metadata_change"
        ignition_details["quality_tag"] = "cto_metadata_change"
        ignition_details["reason"] = "passed_cto_metadata_change"

    ignition_breakdown.append(
        "CTO metadata change "
        f"{','.join(changed_fields)} "
        f"(+{adjusted_score - ignition_score})"
    )

    return adjusted_score


def trim_token_history(
    history
):

    if len(history) > TOKEN_HISTORY_MAX_POINTS:
        del history[:-TOKEN_HISTORY_MAX_POINTS]


def calculate_pressure(
    metrics,
    ignition_details
):

    return position_engine.calculate_pressure(
        metrics,
        ignition_details
    )


def uses_mobula_safety(
    chain
):

    return (
        str(chain or "").lower()
        in {
            str(item).lower()
            for item in MOBULA_SAFETY_CHAINS
        }
    )


def position_entry_precheck_reason(
    metrics,
    ignition_score,
    ignition_details,
    now,
    recent_snapshots
):

    state = position_engine.load_state()
    address = str(metrics.address)

    if address in state.get("open", {}):
        return "position_already_open"

    reason = position_engine.entry_block_reason(
        metrics,
        ignition_score,
        ignition_details,
        now=now,
        recent_snapshots=recent_snapshots
    )

    if reason:
        return reason

    if (
        len(state.get("open", {}))
        >= POSITION_MAX_OPEN_POSITIONS
    ):
        return "max_open_positions_reached"

    entry_size_sol = position_engine.entry_size_sol(
        metrics,
        ignition_details
    )

    if entry_size_sol <= 0:
        return "entry_size_zero"

    if (
        safe_float(state.get("cash_sol"), 0)
        + 1e-9
        < entry_size_sol
    ):
        return "insufficient_position_cash"

    return None


def build_signal_snapshot(
    metrics,
    ignition_score,
    ignition_details,
    pressure,
    now,
    momentum_features=None
):

    buy_sell_ratio = safe_float(
        ignition_details.get("buy_sell_ratio"),
        metrics.buys_5m / max(metrics.sells_5m, 1)
    )
    h1_buy_sell_ratio = safe_float(
        ignition_details.get("h1_buy_sell_ratio"),
        metrics.buys_1h / max(metrics.sells_1h, 1)
    )
    buy_sell_volume_ratio = safe_float(
        ignition_details.get("buy_sell_volume_ratio"),
        (
            metrics.buy_volume_5m
            / max(metrics.sell_volume_5m, 1e-18)
            if metrics.sell_volume_5m > 0
            else 999 if metrics.buy_volume_5m > 0 else 0
        )
    )
    flow_buy_sell_ratio = safe_float(
        ignition_details.get("flow_buy_sell_ratio"),
        buy_sell_volume_ratio
    )
    volume_liquidity_ratio = safe_float(
        ignition_details.get("volume_liquidity_ratio"),
        0
    )
    impulse = safe_float(
        ignition_details.get("price_jump"),
        0
    )

    risk_flags = []

    if pressure <= 35:
        risk_flags.append("low_pressure")

    if volume_liquidity_ratio <= 0.35:
        risk_flags.append("weak_volume_liquidity")

    if flow_buy_sell_ratio <= 0.80:
        risk_flags.append("sell_pressure")

    if impulse <= 1.10:
        risk_flags.append("weak_impulse")

    experimental_features = dict(momentum_features or {})

    if ignition_details.get("entry_confirmation_enabled"):
        experimental_features["entry_confirmation"] = {
            "enabled": ignition_details.get(
                "entry_confirmation_enabled",
                False
            ),
            "shadow_mode": ignition_details.get(
                "entry_confirmation_shadow_mode",
                False
            ),
            "score": ignition_details.get(
                "entry_confirmation_score",
                0
            ),
            "min_score": ignition_details.get(
                "entry_confirmation_min_score",
                0
            ),
            "ready": ignition_details.get(
                "entry_confirmation_ready",
                False
            ),
            "passed_scan": ignition_details.get(
                "entry_confirmation_passed_scan",
                False
            ),
            "confirmed_scans": ignition_details.get(
                "entry_confirmation_confirmed_scans",
                0
            ),
            "required_scans": ignition_details.get(
                "entry_confirmation_required_scans",
                0
            ),
            "reason": ignition_details.get(
                "entry_confirmation_reason",
                ""
            ),
            "would_block": ignition_details.get(
                "entry_confirmation_would_block",
                False
            ),
            "vwap_ready": ignition_details.get(
                "entry_confirmation_vwap_ready",
                False
            ),
            "vwap_distance_pct": ignition_details.get(
                "entry_confirmation_vwap_distance_pct",
                0
            )
        }

    return {
        "token_address": metrics.address,
        "symbol": metrics.symbol,
        "pair_address": metrics.pair_address,
        "chain_name": metrics.chain,
        "lifecycle": metrics.lifecycle,
        "price": metrics.price,
        "liquidity": metrics.liquidity,
        "raw_liquidity": metrics.raw_liquidity,
        "fdv": metrics.fdv,
        "volume_5m": metrics.volume_5m,
        "volume_1h": metrics.volume_1h,
        "buy_volume_5m": metrics.buy_volume_5m,
        "sell_volume_5m": metrics.sell_volume_5m,
        "buy_volume_1h": metrics.buy_volume_1h,
        "sell_volume_1h": metrics.sell_volume_1h,
        "buys_5m": metrics.buys_5m,
        "sells_5m": metrics.sells_5m,
        "buys_1h": metrics.buys_1h,
        "sells_1h": metrics.sells_1h,
        "txns_5m": metrics.buys_5m + metrics.sells_5m,
        "txns_1h": metrics.buys_1h + metrics.sells_1h,
        "price_change_5m": metrics.price_change_5m,
        "price_change_1h": metrics.price_change_1h,
        "price_change_6h": metrics.price_change_6h,
        "price_change_24h": metrics.price_change_24h,
        "pressure": pressure,
        "impulse": impulse,
        "volume_liquidity_ratio": volume_liquidity_ratio,
        "buy_sell_ratio": buy_sell_ratio,
        "buy_sell_volume_ratio": buy_sell_volume_ratio,
        "buy_sell_volume_source_5m": ignition_details.get(
            "buy_sell_volume_source_5m",
            getattr(metrics, "buy_sell_volume_source_5m", "")
        ),
        "flow_buy_sell_ratio": flow_buy_sell_ratio,
        "h1_volume_liquidity_ratio": safe_float(
            ignition_details.get("h1_volume_liquidity_ratio"),
            0
        ),
        "h1_buy_sell_ratio": h1_buy_sell_ratio,
        "h1_buy_sell_volume_ratio": ignition_details.get(
            "h1_buy_sell_volume_ratio",
            0
        ),
        "buy_sell_volume_source_1h": ignition_details.get(
            "buy_sell_volume_source_1h",
            getattr(metrics, "buy_sell_volume_source_1h", "")
        ),
        "h1_flow_buy_sell_ratio": ignition_details.get(
            "h1_flow_buy_sell_ratio",
            0
        ),
        "score": ignition_score,
        "raw_score": ignition_details.get(
            "raw_score",
            ignition_score
        ),
        "penalty": ignition_details.get("penalty", 0),
        "quality_tag": ignition_details.get(
            "quality_tag",
            "standard"
        ),
        "alert_route": ignition_details.get(
            "alert_route",
            "none"
        ),
        "alert_eligible": ignition_details.get(
            "alert_eligible",
            False
        ),
        "liquidity_lock_checked": ignition_details.get(
            "liquidity_lock_checked",
            False
        ),
        "liquidity_lock_required": ignition_details.get(
            "liquidity_lock_required",
            False
        ),
        "liquidity_lock_locked": ignition_details.get(
            "liquidity_lock_locked",
            True
        ),
        "liquidity_lock_locked_percent": ignition_details.get(
            "liquidity_lock_locked_percent"
        ),
        "liquidity_lock_source": ignition_details.get(
            "liquidity_lock_source",
            ""
        ),
        "liquidity_lock_reason": ignition_details.get(
            "liquidity_lock_reason",
            ""
        ),
        "anchored_vwap_ready": ignition_details.get(
            "anchored_vwap_ready",
            False
        ),
        "anchored_vwap": ignition_details.get(
            "anchored_vwap",
            0
        ),
        "anchored_vwap_anchor_timestamp": ignition_details.get(
            "anchored_vwap_anchor_timestamp",
            0
        ),
        "anchored_vwap_anchor_low": ignition_details.get(
            "anchored_vwap_anchor_low",
            0
        ),
        "anchored_vwap_candle_count": ignition_details.get(
            "anchored_vwap_candle_count",
            0
        ),
        "anchored_price_above_vwap": ignition_details.get(
            "anchored_price_above_vwap",
            False
        ),
        "anchored_vwap_reclaimed": ignition_details.get(
            "anchored_vwap_reclaimed",
            False
        ),
        "anchored_vwap_distance_pct": ignition_details.get(
            "anchored_vwap_distance_pct",
            0
        ),
        "anchored_vwap_source": ignition_details.get(
            "anchored_vwap_source",
            ""
        ),
        "anchored_vwap_reason": ignition_details.get(
            "anchored_vwap_reason",
            ""
        ),
        "trade_quality_label": ignition_details.get(
            "trade_quality_label",
            "neutral"
        ),
        "trade_quality_score": ignition_details.get(
            "trade_quality_score",
            0
        ),
        "trade_quality_reason": ignition_details.get(
            "trade_quality_reason",
            "within_market_band"
        ),
        "entry_confirmation_enabled": ignition_details.get(
            "entry_confirmation_enabled",
            False
        ),
        "entry_confirmation_shadow_mode": ignition_details.get(
            "entry_confirmation_shadow_mode",
            False
        ),
        "entry_confirmation_score": ignition_details.get(
            "entry_confirmation_score",
            0
        ),
        "entry_confirmation_min_score": ignition_details.get(
            "entry_confirmation_min_score",
            0
        ),
        "entry_confirmation_ready": ignition_details.get(
            "entry_confirmation_ready",
            False
        ),
        "entry_confirmation_passed_scan": ignition_details.get(
            "entry_confirmation_passed_scan",
            False
        ),
        "entry_confirmation_confirmed_scans": ignition_details.get(
            "entry_confirmation_confirmed_scans",
            0
        ),
        "entry_confirmation_required_scans": ignition_details.get(
            "entry_confirmation_required_scans",
            0
        ),
        "entry_confirmation_reason": ignition_details.get(
            "entry_confirmation_reason",
            ""
        ),
        "entry_confirmation_reasons": ignition_details.get(
            "entry_confirmation_reasons",
            []
        ),
        "entry_confirmation_would_block": ignition_details.get(
            "entry_confirmation_would_block",
            False
        ),
        "entry_confirmation_vwap_ready": ignition_details.get(
            "entry_confirmation_vwap_ready",
            False
        ),
        "entry_confirmation_vwap_distance_pct": ignition_details.get(
            "entry_confirmation_vwap_distance_pct",
            0
        ),
        "relative_strength_pct": ignition_details.get(
            "relative_strength_pct",
            0
        ),
        "missing": ignition_details.get("missing", []),
        "risk_flags": risk_flags,
        "migration_fdv": metrics.migration_fdv,
        "migration_distance_usd": metrics.migration_distance_usd,
        "migration_distance_pct": metrics.migration_distance_pct,
        "migration_fdv_source": metrics.migration_fdv_source,
        "momentum_features": experimental_features,
        "timestamp": now
    }


def append_signal_snapshot(
    memory,
    snapshot
):

    snapshots = memory["signal_snapshots"]
    snapshots.append(snapshot)

    if len(snapshots) > 60:
        del snapshots[:-60]

    return snapshots


def confidence_history(
    snapshots
):

    if not snapshots:
        return {}

    recent = snapshots[-5:]

    return {
        "scores": [
            int(safe_float(snapshot.get("score"), 0))
            for snapshot in recent
        ],
        "pressures": [
            round(
                safe_float(snapshot.get("pressure"), 0),
                1
            )
            for snapshot in recent
        ],
        "quality_tags": [
            snapshot.get("quality_tag", "standard")
            for snapshot in recent
        ]
    }


def momentum_features(
    snapshots,
    metrics
):

    recent = snapshots[-6:]
    prices = [
        safe_float(snapshot.get("price"), 0)
        for snapshot in recent
        if safe_float(snapshot.get("price"), 0) > 0
    ]
    volumes = [
        safe_float(snapshot.get("volume_5m"), 0)
        for snapshot in recent
    ]
    liquidities = [
        safe_float(snapshot.get("liquidity"), 0)
        for snapshot in recent
    ]

    if not prices:
        return {}

    current_price = safe_float(metrics.price, prices[-1])
    previous_price = prices[-2] if len(prices) >= 2 else current_price
    prior_prices = prices[:-1]
    recent_high = max(prior_prices) if prior_prices else current_price
    recent_low = min(prior_prices) if prior_prices else current_price

    current_return = (
        current_price / max(previous_price, 1e-18)
        - 1
    )
    previous_return = 0

    if len(prices) >= 3:
        previous_return = (
            prices[-2] / max(prices[-3], 1e-18)
            - 1
        )

    price_acceleration = current_return - previous_return

    avg_volume = (
        sum(volumes[:-1]) / max(len(volumes[:-1]), 1)
        if len(volumes) >= 2
        else safe_float(metrics.volume_5m, 0)
    )
    current_volume = safe_float(metrics.volume_5m, 0)
    volume_expansion = (
        current_volume / max(avg_volume, 1e-18)
        if avg_volume > 0
        else 0
    )

    vwap_numerator = 0
    vwap_denominator = 0

    for snapshot in recent:
        price = safe_float(snapshot.get("price"), 0)
        volume = safe_float(snapshot.get("volume_5m"), 0)

        if price > 0 and volume > 0:
            vwap_numerator += price * volume
            vwap_denominator += volume

    vwap_proxy = (
        vwap_numerator / vwap_denominator
        if vwap_denominator > 0
        else current_price
    )

    vwap_reclaim = (
        current_price >= vwap_proxy
        and previous_price < vwap_proxy
    )

    higher_high = current_price > recent_high
    higher_low_proxy = current_price > recent_low
    breakout_strength = (
        current_price / max(recent_high, 1e-18)
        - 1
    )
    liquidity_peak = max(liquidities) if liquidities else 0
    liquidity_drain = 0

    if liquidity_peak > 0:
        liquidity_drain = (
            1 - safe_float(metrics.liquidity, 0) / liquidity_peak
        )

    volume_persistence = 0

    if len(volumes) >= 3:
        above_baseline = [
            volume > avg_volume * 1.1
            for volume in volumes[-3:]
        ]
        volume_persistence = sum(
            1
            for item in above_baseline
            if item
        )

    momentum_score = 0

    if current_return > 0.15:
        momentum_score += 20
    if price_acceleration > 0.05:
        momentum_score += 15
    if volume_expansion >= 1.5:
        momentum_score += 20
    if higher_high:
        momentum_score += 15
    if vwap_reclaim:
        momentum_score += 15
    if liquidity_drain < 0.25:
        momentum_score += 10
    if volume_persistence >= 2:
        momentum_score += 5

    return {
        "current_return": round(current_return, 4),
        "previous_return": round(previous_return, 4),
        "price_acceleration": round(price_acceleration, 4),
        "volume_expansion": round(volume_expansion, 4),
        "current_volume": round(current_volume, 2),
        "avg_volume_lookback": round(avg_volume, 2),
        "higher_high": higher_high,
        "higher_low_proxy": higher_low_proxy,
        "breakout_strength": round(breakout_strength, 4),
        "vwap_proxy": round(vwap_proxy, 12),
        "vwap_reclaim": vwap_reclaim,
        "liquidity_drain": round(liquidity_drain, 4),
        "volume_persistence": volume_persistence,
        "momentum_score": min(momentum_score, 100)
    }


def trade_volume_from_flows(
    flows,
    current_price,
    now,
    window_seconds
):

    buy_units = 0
    sell_units = 0
    used = 0

    for flow in flows or []:

        timestamp = safe_float(
            flow.get("timestamp"),
            0
        )

        if now - timestamp > window_seconds:
            continue

        delta = safe_float(
            flow.get("base_delta"),
            0
        )

        if delta > 0:
            buy_units += delta
            used += 1
        elif delta < 0:
            sell_units += abs(delta)
            used += 1

    return {
        "buy_volume": buy_units * current_price,
        "sell_volume": sell_units * current_price,
        "buy_units": buy_units,
        "sell_units": sell_units,
        "used": used
    }


def summarize_trade_volumes(
    memory,
    current_price,
    now,
    volume_5m,
    volume_1h,
    buys_5m,
    sells_5m,
    buys_1h,
    sells_1h
):

    flows = memory.get("recent_trade_flows") or []
    source_5m = "observed_flows"
    source_1h = "observed_flows"

    five_minute = trade_volume_from_flows(
        flows,
        current_price,
        now,
        300
    )
    one_hour = trade_volume_from_flows(
        flows,
        current_price,
        now,
        3600
    )

    if five_minute["used"] <= 0:
        source_5m = "unavailable"
        five_minute["buy_volume"] = 0
        five_minute["sell_volume"] = 0

    if one_hour["used"] <= 0:
        source_1h = "unavailable"
        one_hour["buy_volume"] = 0
        one_hour["sell_volume"] = 0

    return {
        "buy_volume_5m": round(five_minute["buy_volume"], 2),
        "sell_volume_5m": round(five_minute["sell_volume"], 2),
        "buy_volume_1h": round(one_hour["buy_volume"], 2),
        "sell_volume_1h": round(one_hour["sell_volume"], 2),
        "source_5m": source_5m,
        "source_1h": source_1h
    }


def latest_snapshot_features(snapshot):

    features = snapshot.get("momentum_features")

    if isinstance(features, dict) and features:
        return features

    experimental = snapshot.get("experimental_features")

    if isinstance(experimental, dict) and experimental:
        return experimental

    return {}


def build_market_benchmark(
    now,
    exclude_address=None
):

    cached_at = MARKET_BENCHMARK_CACHE.get("cached_at", 0)
    cached_value = MARKET_BENCHMARK_CACHE.get("value", {})

    if (
        cached_value
        and now - cached_at < MARKET_BENCHMARK_CACHE_SECONDS
    ):
        return cached_value

    rows = []

    for token_address, memory in TOKEN_MEMORY.items():

        if exclude_address and token_address == exclude_address:
            continue

        snapshots = memory.get("signal_snapshots") or []

        if not snapshots:
            continue

        features = latest_snapshot_features(
            snapshots[-1]
        )

        if not features:
            continue

        rows.append(features)

    if not rows:
        benchmark = {
            "token_count": 0,
            "feature_count": 0,
            "median_current_return": 0,
            "median_price_acceleration": 0,
            "median_volume_expansion": 0,
            "median_liquidity_drain": 0,
            "median_momentum_score": 0
        }
        MARKET_BENCHMARK_CACHE["cached_at"] = now
        MARKET_BENCHMARK_CACHE["value"] = benchmark
        return benchmark

    benchmark = {
        "token_count": len(rows),
        "feature_count": len(rows),
        "median_current_return": statistics.median(
            safe_float(row.get("current_return"), 0)
            for row in rows
        ),
        "median_price_acceleration": statistics.median(
            safe_float(row.get("price_acceleration"), 0)
            for row in rows
        ),
        "median_volume_expansion": statistics.median(
            safe_float(row.get("volume_expansion"), 0)
            for row in rows
        ),
        "median_liquidity_drain": statistics.median(
            safe_float(row.get("liquidity_drain"), 0)
            for row in rows
        ),
        "median_momentum_score": statistics.median(
            safe_float(row.get("momentum_score"), 0)
            for row in rows
        )
    }

    MARKET_BENCHMARK_CACHE["cached_at"] = now
    MARKET_BENCHMARK_CACHE["value"] = benchmark
    return benchmark


def build_trade_quality_label(
    momentum,
    benchmark
):

    if not benchmark or benchmark.get("feature_count", 0) < 5:
        return {
            "trade_quality_label": "neutral",
            "trade_quality_score": 0,
            "trade_quality_reason": "benchmark_unavailable",
            "relative_strength_pct": 0
        }

    score = 0
    reasons = []

    current_return = safe_float(
        momentum.get("current_return"),
        0
    )
    benchmark_return = safe_float(
        benchmark.get("median_current_return"),
        0
    )
    return_edge = current_return - benchmark_return

    if return_edge >= 0.08:
        score += 2
        reasons.append("return_leading")
    elif return_edge <= -0.05:
        score -= 2
        reasons.append("return_lagging")

    volume_expansion = safe_float(
        momentum.get("volume_expansion"),
        0
    )
    benchmark_volume = safe_float(
        benchmark.get("median_volume_expansion"),
        0
    )
    volume_edge = volume_expansion - benchmark_volume

    if volume_edge >= 0.50:
        score += 2
        reasons.append("volume_leading")
    elif volume_edge <= -0.25:
        score -= 1
        reasons.append("volume_lagging")

    price_acceleration = safe_float(
        momentum.get("price_acceleration"),
        0
    )
    benchmark_acceleration = safe_float(
        benchmark.get("median_price_acceleration"),
        0
    )
    acceleration_edge = (
        price_acceleration
        - benchmark_acceleration
    )

    if acceleration_edge >= 0.04:
        score += 1
        reasons.append("acceleration_leading")
    elif acceleration_edge <= -0.04:
        score -= 1
        reasons.append("acceleration_lagging")

    liquidity_drain = safe_float(
        momentum.get("liquidity_drain"),
        0
    )
    benchmark_liquidity_drain = safe_float(
        benchmark.get("median_liquidity_drain"),
        0
    )

    if liquidity_drain <= benchmark_liquidity_drain - 0.10:
        score += 1
        reasons.append("liquidity_holding")
    elif liquidity_drain >= benchmark_liquidity_drain + 0.15:
        score -= 1
        reasons.append("liquidity_bleeding")

    momentum_score = safe_float(
        momentum.get("momentum_score"),
        0
    )
    benchmark_momentum = safe_float(
        benchmark.get("median_momentum_score"),
        0
    )

    if momentum_score >= benchmark_momentum + 15:
        score += 1
        reasons.append("momentum_leading")
    elif momentum_score <= benchmark_momentum - 15:
        score -= 1
        reasons.append("momentum_lagging")

    if score >= 3:
        label = "leading"
    elif score <= -2:
        label = "lagging"
    else:
        label = "neutral"

    return {
        "trade_quality_label": label,
        "trade_quality_score": score,
        "trade_quality_reason": ",".join(reasons) or "within_market_band",
        "relative_strength_pct": round(return_edge * 100, 2)
    }


def get_ignition_criteria(metrics):

    bands = (
        IGNITION_BONDING_CURVE_BANDS
        if metrics.lifecycle == "bonding_curve"
        else IGNITION_MIGRATED_BANDS
    )

    for band in bands:

        if (
            metrics.fdv >= band["min_fdv"]
            and metrics.fdv < band["max_fdv"]
        ):
            return band

    return None


def recalibrate_ignition_score(
    metrics,
    details,
    legacy_score,
    breakdown
):

    route = normalize_alert_route(
        details.get("alert_route")
    )

    if (
        route == "none"
        or str(metrics.chain or "").lower() == "hyperevm"
    ):
        return legacy_score

    route_base = {
        "bonding_momentum_high_conviction": 50,
        "bonding_early_revival": 42,
        "bonding_scalp": 38,
        "bonding_momentum_scalp": 33,
        "migrated_revival": 38,
        "low_fdv_accumulation": 35,
        "immediate": 30
    }.get(route, 30)

    score = route_base
    adjustments = [
        f"route {route} base {route_base}"
    ]

    if route == "migrated_revival":
        drawdown = safe_float(
            details.get("migration_drawdown_pct"),
            0
        )
        if 0.70 <= drawdown <= 0.85:
            score += 12
            adjustments.append(
                f"deep washout {drawdown:.0%} +12"
            )
        elif drawdown > 0.85:
            score += 4
            adjustments.append(
                f"very deep dump {drawdown:.0%} +4"
            )
        elif drawdown >= 0.50:
            score += 6
            adjustments.append(
                f"moderate washout {drawdown:.0%} +6"
            )
    volume_multiple = (
        safe_float(metrics.volume_1h, 0)
        / max(POSITION_MIN_ENTRY_VOLUME_1H_USD, 1e-18)
    )
    buy_volume_5m = safe_float(
        getattr(metrics, "buy_volume_5m", 0),
        0
    )
    sell_volume_5m = safe_float(
        getattr(metrics, "sell_volume_5m", 0),
        0
    )
    buy_sell_volume_ratio = (
        buy_volume_5m / max(sell_volume_5m, 1e-18)
        if sell_volume_5m > 0
        else 999 if buy_volume_5m > 0 else 0
    )
    pressure = position_engine.calculate_pressure(
        metrics,
        details
    )
    impulse = safe_float(
        details.get("price_jump"),
        0
    )

    if volume_multiple >= 5:
        score += 22
        adjustments.append(
            f"1h volume {volume_multiple:.2f}x +22"
        )
    elif (
        volume_multiple
        >= POSITION_MIN_ENTRY_VOLUME_MULTIPLE
    ):
        score += 14
        adjustments.append(
            f"1h volume {volume_multiple:.2f}x +14"
        )
    elif volume_multiple >= 1:
        score += 5
        adjustments.append(
            f"1h volume {volume_multiple:.2f}x +5"
        )
    else:
        score -= 6
        adjustments.append(
            f"1h volume {volume_multiple:.2f}x -6"
        )

    if pressure >= 70:
        score += 8
        adjustments.append(
            f"pressure {pressure:.1f} +8"
        )
    elif pressure >= 55:
        adjustments.append(
            f"pressure {pressure:.1f} +0"
        )
    elif pressure >= 40:
        score += 10
        adjustments.append(
            f"pressure {pressure:.1f} +10"
        )
    else:
        score -= 8
        adjustments.append(
            f"pressure {pressure:.1f} -8"
        )

    price_change_1h = safe_float(
        getattr(metrics, "price_change_1h", 0),
        0
    )

    if impulse >= 2.0:
        score -= 4
        adjustments.append(
            f"spike impulse {impulse:.2f} "
            f"(47% blowup rate) -4"
        )
    elif impulse >= 1.50:
        if price_change_1h <= 15:
            score += 8
            adjustments.append(
                f"breakout impulse {impulse:.2f} "
                f"from consolidation ({price_change_1h:.0f}% 1h) +8"
            )
        else:
            score -= 10
            adjustments.append(
                f"top-of-candle impulse {impulse:.2f} "
                f"on {price_change_1h:.0f}% 1h -10"
            )
    elif 1.20 <= impulse < 1.50:
        score -= 12
        adjustments.append(
            f"hot impulse {impulse:.2f} "
            f"(worst outcome band: 7.9% 2x rate) -12"
        )
    elif 1.0 <= impulse < 1.20:
        score += 8
        adjustments.append(
            f"continuation impulse {impulse:.2f} +8"
        )
    elif 0.90 <= impulse < 1.0:
        score += 14
        adjustments.append(
            f"cool-off entry impulse {impulse:.2f} +14"
        )
    else:
        score -= 6
        adjustments.append(
            f"weak impulse {impulse:.2f} -6"
        )

    fdv = safe_float(
        metrics.fdv,
        0
    )

    if 10000 <= fdv < 20000:
        score += 8
        adjustments.append(
            f"FDV ${fdv:,.0f} +8"
        )
    elif 20000 <= fdv < 50000:
        score += 5
        adjustments.append(
            f"FDV ${fdv:,.0f} +5"
        )
    elif fdv >= 50000:
        score -= 12
        adjustments.append(
            f"FDV ${fdv:,.0f} -12"
        )

    if (
        buy_sell_volume_ratio
        >= POSITION_MIN_ENTRY_BUY_SELL_VOLUME_RATIO
        and buy_sell_volume_ratio < 2
    ):
        score += 6
        adjustments.append(
            f"5m dollar flow {buy_sell_volume_ratio:.2f}x +6"
        )
    elif buy_sell_volume_ratio >= 2:
        score += 2
        adjustments.append(
            f"5m dollar flow {buy_sell_volume_ratio:.2f}x +2"
        )
    else:
        score -= 4
        adjustments.append(
            f"5m dollar flow {buy_sell_volume_ratio:.2f}x -4"
        )

    # ── Pool reserve scoring ──────────────────────────────────────────────
    # base_reserve_trend: fraction change in pool token count since last scan.
    #   Positive  = tokens flowing INTO pool = net selling → penalty.
    #   Negative  = tokens leaving pool = net buying → bonus.
    # curve_token_pct_delta: same idea but expressed as fraction of pumpfun
    #   total supply (1B), giving a scale-independent, human-readable number.
    # reserve_price_confirmed: price rising AND base leaving pool simultaneously
    #   → the strongest confirmation of genuine demand.
    base_reserve_trend = details.get("base_reserve_trend")
    reserve_price_confirmed = details.get("reserve_price_confirmed", False)
    curve_token_pct_delta = details.get("curve_token_pct_delta")

    if reserve_price_confirmed:
        score += 10
        adjustments.append(
            "reserve+price confirmed buy +10"
        )

    if base_reserve_trend is not None:
        if base_reserve_trend > 0.08:
            score -= 14
            adjustments.append(
                f"pool base +{base_reserve_trend:.1%} "
                f"(heavy net sell) -14"
            )
        elif base_reserve_trend > 0.03:
            score -= 7
            adjustments.append(
                f"pool base +{base_reserve_trend:.1%} "
                f"(net sell) -7"
            )
        elif base_reserve_trend < -0.05:
            score += 8
            adjustments.append(
                f"pool base {base_reserve_trend:.1%} "
                f"(strong net buy) +8"
            )
        elif base_reserve_trend < -0.02:
            score += 4
            adjustments.append(
                f"pool base {base_reserve_trend:.1%} "
                f"(net buy) +4"
            )

    # Bonding-curve token-in-curve delta: if % of total supply flowing
    # back into the curve is large it means holders are dumping early.
    if curve_token_pct_delta is not None:
        if curve_token_pct_delta > 0.05:
            score -= 16
            adjustments.append(
                f"curve dump: {curve_token_pct_delta:.1%} "
                f"of supply returned -16"
            )
        elif curve_token_pct_delta > 0.02:
            score -= 8
            adjustments.append(
                f"curve sell pressure: {curve_token_pct_delta:.1%} "
                f"of supply -8"
            )
        elif curve_token_pct_delta < -0.02:
            score += 6
            adjustments.append(
                f"curve buy: {abs(curve_token_pct_delta):.1%} "
                f"of supply bought out +6"
            )

    recalibrated_score = max(
        min(
            int(round(score)),
            150
        ),
        0
    )
    details["score_model"] = "route_quality_v3"
    details["legacy_score"] = legacy_score
    details["recalibrated_score"] = recalibrated_score
    details["recalibration_route_base"] = route_base
    details["recalibration_adjustments"] = adjustments
    details["recalibration_volume_multiple"] = volume_multiple
    details[
        "recalibration_buy_sell_volume_ratio"
    ] = buy_sell_volume_ratio
    details["recalibration_pressure"] = pressure
    details["recalibration_impulse"] = impulse

    breakdown.append(
        "Route-quality score "
        f"{legacy_score}->{recalibrated_score}: "
        + "; ".join(adjustments)
    )

    return recalibrated_score


def calculate_ignition_signal(
    metrics,
    memory
):

    history = memory["history"]
    chain = str(
        metrics.chain or ""
    ).lower()

    previous = (
        history[-1]
        if history
        else {}
    )

    previous_price = previous.get(
        "price",
        metrics.price
    )

    previous_volume = previous.get(
        "volume_5m",
        0
    )

    if previous_price <= 0:
        return 0, [], {}

    last_scan_price_jump = (
        metrics.price
        / previous_price
    )

    price_change_5m_jump = (
        1
        + metrics.price_change_5m
        / 100
    )

    price_jump = max(
        last_scan_price_jump,
        price_change_5m_jump
    )

    volume_liquidity_ratio = (
        metrics.volume_5m
        / max(metrics.liquidity, 1)
    )

    buy_sell_ratio = (
        metrics.buys_5m
        / max(metrics.sells_5m, 1)
    )
    buy_sell_volume_ratio = (
        metrics.buy_volume_5m
        / max(metrics.sell_volume_5m, 1e-18)
        if metrics.sell_volume_5m > 0
        else 999 if metrics.buy_volume_5m > 0 else 0
    )
    buy_sell_volume_source_5m = getattr(
        metrics,
        "buy_sell_volume_source_5m",
        ""
    )
    flow_observed_5m = buy_sell_volume_source_5m == "observed_flows"
    flow_buy_sell_ratio = (
        buy_sell_volume_ratio
        if flow_observed_5m
        else buy_sell_ratio * 0.60
    )

    txns_5m = (
        metrics.buys_5m
        + metrics.sells_5m
    )

    h1_volume_liquidity_ratio = (
        metrics.volume_1h
        / max(metrics.liquidity, 1)
    )

    h1_buy_sell_ratio = (
        metrics.buys_1h
        / max(metrics.sells_1h, 1)
    )
    h1_buy_sell_volume_ratio = (
        metrics.buy_volume_1h
        / max(metrics.sell_volume_1h, 1e-18)
        if metrics.sell_volume_1h > 0
        else 999 if metrics.buy_volume_1h > 0 else 0
    )
    buy_sell_volume_source_1h = getattr(
        metrics,
        "buy_sell_volume_source_1h",
        ""
    )
    flow_observed_1h = buy_sell_volume_source_1h == "observed_flows"
    h1_flow_buy_sell_ratio = (
        h1_buy_sell_volume_ratio
        if flow_observed_1h
        else h1_buy_sell_ratio * 0.60
    )

    h1_txns = (
        metrics.buys_1h
        + metrics.sells_1h
    )

    volume_share_5m_1h = (
        metrics.volume_5m
        / max(metrics.volume_1h, 1)
    )

    volume_jump = 0

    if previous_volume > 0:
        volume_jump = (
            metrics.volume_5m
            / previous_volume
        )

    # Pool reserve trend — derived from history entries that now carry
    # raw_base_reserve/raw_quote_reserve. Older history entries before this
    # field was added will just produce None (handled gracefully below).
    _hist_bases = [
        h.get("raw_base_reserve", 0)
        for h in history[-5:]
        if h.get("raw_base_reserve", 0) > 0
    ]
    _cur_base = metrics.raw_base_reserve

    base_reserve_trend = None    # fractional change: + = selling, − = buying
    reserve_price_confirmed = False  # price up + base down = confirmed buy
    curve_token_pct = None       # bonding: tokens still in curve / total supply
    curve_token_pct_delta = None  # bonding: delta vs most recent history

    if _hist_bases and _cur_base > 0:
        _ref_base = _hist_bases[-1]
        if _ref_base > 0:
            base_reserve_trend = (
                (_cur_base - _ref_base) / _ref_base
            )
            # Cross-validate: price rising + tokens leaving pool = genuine buy
            if price_jump >= 1.05 and base_reserve_trend < -0.02:
                reserve_price_confirmed = True

    # Bonding curve: express as fraction of fixed total supply so the delta
    # is human-interpretable (e.g. 0.03 = 3% of supply dumped back in).
    if (
        metrics.lifecycle == "bonding_curve"
        and _cur_base > 0
        and _PUMPFUN_TOTAL_SUPPLY > 0
    ):
        curve_token_pct = _cur_base / _PUMPFUN_TOTAL_SUPPLY
        if _hist_bases and _hist_bases[-1] > 0:
            curve_token_pct_delta = (
                curve_token_pct
                - _hist_bases[-1] / _PUMPFUN_TOTAL_SUPPLY
            )

    low_fdv_accumulation = (
        chain != "hyperevm"
        and
        metrics.fdv
        < IGNITION_LOW_FDV_ACCUMULATION_MAX_FDV
        and metrics.liquidity
        >= IGNITION_LOW_FDV_ACCUMULATION_MIN_LIQUIDITY
        and volume_liquidity_ratio
        >= (
            IGNITION_LOW_FDV_ACCUMULATION_MIN_VOLUME_LIQUIDITY_RATIO_5M
        )
        and h1_volume_liquidity_ratio
        >= (
            IGNITION_LOW_FDV_ACCUMULATION_MIN_VOLUME_LIQUIDITY_RATIO_1H
        )
        and metrics.price_change_5m
        <= IGNITION_LOW_FDV_ACCUMULATION_MAX_PRICE_CHANGE_5M
        and metrics.price_change_1h
        >= IGNITION_LOW_FDV_ACCUMULATION_MIN_PRICE_CHANGE_1H
        and metrics.price_change_6h
        >= IGNITION_LOW_FDV_ACCUMULATION_MIN_PRICE_CHANGE_6H
        and flow_buy_sell_ratio
        >= IGNITION_LOW_FDV_ACCUMULATION_MIN_BUY_SELL_RATIO_5M
        and h1_flow_buy_sell_ratio
        >= IGNITION_LOW_FDV_ACCUMULATION_MIN_BUY_SELL_RATIO_1H
    )

    extended_price_change = max(
        metrics.price_change_1h,
        metrics.price_change_6h
    )

    bonding_extended_cooling = (
        metrics.lifecycle == "bonding_curve"
        and extended_price_change
        >= IGNITION_BONDING_EXTENDED_COOLING_MIN_PRICE_CHANGE
        and volume_liquidity_ratio
        < IGNITION_BONDING_EXTENDED_COOLING_MAX_VOLUME_LIQUIDITY_RATIO_5M
    )

    bonding_high_conviction = (
        metrics.lifecycle == "bonding_curve"
        and volume_liquidity_ratio
        >= IGNITION_BONDING_HIGH_CONVICTION_MIN_VOLUME_LIQUIDITY_RATIO_5M
        and h1_txns
        >= IGNITION_BONDING_HIGH_CONVICTION_MIN_TXNS_1H
        and metrics.volume_1h
        >= IGNITION_BONDING_HIGH_CONVICTION_MIN_VOLUME_1H
    )

    bonding_scalp_candidate = (
        metrics.lifecycle == "bonding_curve"
        and volume_liquidity_ratio
        >= IGNITION_BONDING_SCALP_MIN_VOLUME_LIQUIDITY_RATIO_5M
        and h1_txns
        < IGNITION_BONDING_SCALP_MAX_TXNS_1H
    )

    migrated_fragile = (
        metrics.lifecycle == "migrated"
        and metrics.fdv
        >= IGNITION_MIGRATED_FRAGILE_MIN_FDV
        and volume_liquidity_ratio
        < IGNITION_MIGRATED_FRAGILE_MAX_VOLUME_LIQUIDITY_RATIO_5M
    )

    migrated_high_quality = (
        metrics.lifecycle == "migrated"
        and price_jump
        >= IGNITION_MIGRATED_HIGH_QUALITY_MIN_PRICE_JUMP
        and volume_liquidity_ratio
        >= IGNITION_MIGRATED_HIGH_QUALITY_MIN_VOLUME_LIQUIDITY_RATIO_5M
        and txns_5m
        >= IGNITION_MIGRATED_HIGH_QUALITY_MIN_TXNS_5M
        and volume_share_5m_1h
        >= IGNITION_MIGRATED_HIGH_QUALITY_MIN_VOLUME_SHARE_5M_1H
    )

    migrated_stale_volume = (
        metrics.lifecycle == "migrated"
        and metrics.volume_1h > 0
        and volume_share_5m_1h
        < IGNITION_MIGRATED_STALE_MAX_VOLUME_SHARE_5M_1H
    )

    bonding_early_revival = False

    bonding_quality_tag = "standard"

    if bonding_extended_cooling:
        bonding_quality_tag = "extended_cooling_reject"
    elif bonding_high_conviction:
        bonding_quality_tag = "high_conviction"
    elif bonding_scalp_candidate:
        bonding_quality_tag = "speculative_scalp"

    migrated_quality_tag = "standard"

    if migrated_fragile:
        migrated_quality_tag = "migrated_fragile"
    elif migrated_high_quality:
        migrated_quality_tag = "migrated_high_quality"
    elif migrated_stale_volume:
        migrated_quality_tag = "migrated_stale"

    quality_tag = (
        bonding_quality_tag
        if metrics.lifecycle == "bonding_curve"
        else migrated_quality_tag
    )

    if low_fdv_accumulation:
        quality_tag = "low_fdv_accumulation"

    details = {
        "criteria": {},
        "lifecycle": metrics.lifecycle,
        "liquidity": metrics.liquidity,
        "raw_liquidity": metrics.raw_liquidity,
        "raw_base_reserve": metrics.raw_base_reserve,
        "raw_quote_reserve": metrics.raw_quote_reserve,
        "liquidity_source": metrics.liquidity_source,
        "alert_eligible": False,
        "alert_route": "none",
        "immediate_pass": False,
        "momentum_pass": False,
        "momentum_base_pass": False,
        "low_fdv_accumulation": low_fdv_accumulation,
        "low_fdv_accumulation_pass": False,
        "bonding_extended_cooling": bonding_extended_cooling,
        "bonding_high_conviction": bonding_high_conviction,
        "bonding_early_revival": bonding_early_revival,
        "bonding_scalp_candidate": bonding_scalp_candidate,
        "migrated_fragile": migrated_fragile,
        "migrated_high_quality": migrated_high_quality,
        "migrated_stale_volume": migrated_stale_volume,
        "quality_tag": quality_tag,
        "raw_score": 0,
        "penalty": 0,
        "reason": "unscored",
        "missing": [],
        "immediate_missing": [],
        "momentum_missing": [],
        "last_scan_price_jump": last_scan_price_jump,
        "price_jump": price_jump,
        "price_change_5m": metrics.price_change_5m,
        "price_change_1h": metrics.price_change_1h,
        "price_change_6h": metrics.price_change_6h,
        "price_change_24h": metrics.price_change_24h,
        "volume_liquidity_ratio": volume_liquidity_ratio,
        "txns_5m": txns_5m,
        "h1_volume_liquidity_ratio": h1_volume_liquidity_ratio,
        "buy_sell_ratio": buy_sell_ratio,
        "buy_sell_volume_ratio": buy_sell_volume_ratio,
        "buy_sell_volume_source_5m": buy_sell_volume_source_5m,
        "flow_buy_sell_ratio": flow_buy_sell_ratio,
        "h1_buy_sell_ratio": h1_buy_sell_ratio,
        "h1_buy_sell_volume_ratio": h1_buy_sell_volume_ratio,
        "buy_sell_volume_source_1h": buy_sell_volume_source_1h,
        "h1_flow_buy_sell_ratio": h1_flow_buy_sell_ratio,
        "h1_txns": h1_txns,
        "volume_share_5m_1h": volume_share_5m_1h,
        "volume_jump": volume_jump,
        "base_reserve_trend": base_reserve_trend,
        "reserve_price_confirmed": reserve_price_confirmed,
        "curve_token_pct": curve_token_pct,
        "curve_token_pct_delta": curve_token_pct_delta,
    }

    if chain == "hyperevm":
        price_change_5m = safe_float(
            metrics.price_change_5m,
            0
        )
        price_change_24h = safe_float(
            metrics.price_change_24h,
            0
        )
        liquidity = safe_float(metrics.liquidity, 0)
        fdv = safe_float(metrics.fdv, 0)
        hyperevm_missing = []

        if (
            liquidity
            < HYPEREVM_IGNITION_MIN_LIQUIDITY_USD
        ):
            hyperevm_missing.append("liquidity")

        if (
            not fdv
            or fdv > HYPEREVM_IGNITION_MAX_FDV_USD
        ):
            hyperevm_missing.append("fdv")

        if (
            price_change_5m
            < HYPEREVM_IGNITION_MIN_PRICE_CHANGE_5M
        ):
            hyperevm_missing.append("5m_price")

        if (
            price_change_24h
            < HYPEREVM_IGNITION_MIN_PRICE_CHANGE_24H
        ):
            hyperevm_missing.append("24h_price")

        if (
            metrics.volume_1h
            < HYPEREVM_IGNITION_MIN_VOLUME_1H_USD
        ):
            hyperevm_missing.append("1h_volume")

        hyperevm_pass = not hyperevm_missing
        score = HYPEREVM_IGNITION_SCORE if hyperevm_pass else 0
        breakdown = []

        if hyperevm_pass:
            breakdown.extend([
                (
                    "HyperEVM ignition 5m price "
                    f"+{price_change_5m:.1f}%"
                ),
                (
                    "HyperEVM 24h price "
                    f"+{price_change_24h:.1f}%"
                ),
                (
                    "HyperEVM liquidity/FDV "
                    f"${liquidity:,.0f}/${fdv:,.0f}"
                ),
                (
                    "HyperEVM 1h volume "
                    f"${metrics.volume_1h:,.0f}"
                )
            ])

        details.update({
            "criteria": {
                "name": "hyperevm ignition",
                "min_price_change_5m": (
                    HYPEREVM_IGNITION_MIN_PRICE_CHANGE_5M
                ),
                "min_price_change_24h": (
                    HYPEREVM_IGNITION_MIN_PRICE_CHANGE_24H
                ),
                "min_liquidity_usd": (
                    HYPEREVM_IGNITION_MIN_LIQUIDITY_USD
                ),
                "max_fdv_usd": (
                    HYPEREVM_IGNITION_MAX_FDV_USD
                ),
                "min_volume_1h_usd": (
                    HYPEREVM_IGNITION_MIN_VOLUME_1H_USD
                )
            },
            "hyperevm_ignition": True,
            "hyperevm_ignition_pass": hyperevm_pass,
            "raw_score": score,
            "penalty": 0,
            "penalties": [],
            "alert_eligible": hyperevm_pass,
            "alert_route": (
                "hyperevm_ignition"
                if hyperevm_pass
                else "none"
            ),
            "quality_tag": (
                "hyperevm_ignition"
                if hyperevm_pass
                else "hyperevm_watch"
            ),
            "reason": (
                "passed_hyperevm_ignition"
                if hyperevm_pass
                else "missing_hyperevm_ignition"
            ),
            "missing": hyperevm_missing
        })

        return score, breakdown, details

    criteria = get_ignition_criteria(metrics)

    if not criteria and low_fdv_accumulation:
        criteria = {
            "name": "low-FDV accumulation",
            "min_fdv": 0,
            "max_fdv": IGNITION_LOW_FDV_ACCUMULATION_MAX_FDV,
            "min_price_jump": 1.0,
            "min_volume_liquidity_ratio": (
                IGNITION_LOW_FDV_ACCUMULATION_MIN_VOLUME_LIQUIDITY_RATIO_5M
            ),
            "min_buy_sell_ratio": (
                IGNITION_LOW_FDV_ACCUMULATION_MIN_BUY_SELL_RATIO_5M
            ),
            "min_volume_usd": (
                IGNITION_LOW_FDV_ACCUMULATION_MIN_LIQUIDITY
                * IGNITION_LOW_FDV_ACCUMULATION_MIN_VOLUME_LIQUIDITY_RATIO_5M
            )
        }

    if not criteria:
        details["reason"] = "no_fdv_band"
        details["missing"] = [
            "no_fdv_band"
        ]
        return 0, [], details

    details["criteria"] = criteria

    score = 0
    breakdown = []

    min_price_jump = criteria["min_price_jump"]
    min_volume_liquidity_ratio = (
        criteria["min_volume_liquidity_ratio"]
    )
    min_buy_sell_ratio = criteria["min_buy_sell_ratio"]
    min_volume_usd = criteria["min_volume_usd"]
    min_momentum_volume_usd = (
        min_volume_usd
        * IGNITION_BONDING_MOMENTUM_MIN_VOLUME_MULTIPLE_1H
    )

    bonding_early_revival = (
        metrics.lifecycle == "bonding_curve"
        and volume_liquidity_ratio
        >= IGNITION_BONDING_EARLY_REVIVAL_MIN_VOLUME_LIQUIDITY_RATIO_5M
        and txns_5m
        >= IGNITION_BONDING_EARLY_REVIVAL_MIN_TXNS_5M
        and flow_buy_sell_ratio
        >= IGNITION_BONDING_EARLY_REVIVAL_MIN_BUY_SELL_RATIO_5M
        and metrics.volume_5m
        >= min_volume_usd
    )

    migration_drawdown = (
        migration_drawdown_pct(metrics)
        if metrics.lifecycle == "migrated"
        else None
    )

    migrated_early_revival = (
        metrics.lifecycle == "migrated"
        and not migrated_fragile
        and migration_drawdown is not None
        and migration_drawdown >= IGNITION_MIGRATED_REVIVAL_MIN_DRAWDOWN_PCT
        and migration_drawdown <= IGNITION_MIGRATED_REVIVAL_MAX_DRAWDOWN_PCT
        and volume_liquidity_ratio
        >= IGNITION_MIGRATED_REVIVAL_MIN_VOLUME_LIQUIDITY_RATIO_5M
        and txns_5m
        >= IGNITION_MIGRATED_REVIVAL_MIN_TXNS_5M
        and flow_buy_sell_ratio
        >= IGNITION_MIGRATED_REVIVAL_MIN_BUY_SELL_RATIO_5M
        and metrics.volume_5m
        >= IGNITION_MIGRATED_REVIVAL_MIN_VOLUME_5M_USD
    )

    if (
        not low_fdv_accumulation
        and
        not bonding_extended_cooling
        and bonding_early_revival
        and not bonding_high_conviction
        and not bonding_scalp_candidate
    ):
        bonding_quality_tag = "early_revival"

    quality_tag = (
        bonding_quality_tag
        if metrics.lifecycle == "bonding_curve"
        else migrated_quality_tag
    )

    if low_fdv_accumulation:
        quality_tag = "low_fdv_accumulation"

    details["bonding_early_revival"] = bonding_early_revival
    details["migrated_early_revival"] = migrated_early_revival
    details["migration_drawdown_pct"] = migration_drawdown
    details["quality_tag"] = quality_tag

    if (
        migrated_early_revival
        and quality_tag in ("standard", "migrated_high_quality")
    ):
        quality_tag = "migrated_early_revival"
        details["quality_tag"] = quality_tag

    if price_jump >= 2:
        score += 30
        breakdown.append(
            f"Price impulse {price_jump:.2f}x (+30)"
        )
    elif price_jump >= 1.5:
        score += 22
        breakdown.append(
            f"Price impulse {price_jump:.2f}x (+22)"
        )
    elif price_jump >= min_price_jump:
        score += 15
        breakdown.append(
            f"Price impulse {price_jump:.2f}x (+15)"
        )

    if (
        volume_liquidity_ratio
        >= min_volume_liquidity_ratio * 2.5
    ):
        score += 25
        breakdown.append(
            "Volume/liquidity shock "
            f"{volume_liquidity_ratio:.1%} (+25)"
        )
    elif (
        volume_liquidity_ratio
        >= min_volume_liquidity_ratio * 1.5
    ):
        score += 20
        breakdown.append(
            "Volume/liquidity shock "
            f"{volume_liquidity_ratio:.1%} (+20)"
        )
    elif (
        volume_liquidity_ratio
        >= min_volume_liquidity_ratio
    ):
        score += 15
        breakdown.append(
            "Volume/liquidity shock "
            f"{volume_liquidity_ratio:.1%} (+15)"
        )

    buy_sell_points = 0
    buy_sell_text = ""

    if flow_buy_sell_ratio >= min_buy_sell_ratio * 2:
        buy_sell_points = 20
        buy_sell_text = (
            f"Buy dollar flow {flow_buy_sell_ratio:.1f}x sells"
        )
    elif flow_buy_sell_ratio >= min_buy_sell_ratio:
        buy_sell_points = 10
        buy_sell_text = (
            f"Buy dollar flow {flow_buy_sell_ratio:.1f}x sells"
        )

    if (
        metrics.lifecycle == "migrated"
        and txns_5m
        < IGNITION_MIGRATED_BUY_SELL_SCORE_CAP_TXNS_5M
        and buy_sell_points
        > IGNITION_MIGRATED_BUY_SELL_SCORE_CAP_POINTS
    ):
        buy_sell_points = (
            IGNITION_MIGRATED_BUY_SELL_SCORE_CAP_POINTS
        )
        buy_sell_text = (
            f"Thin 5m dollar flow {flow_buy_sell_ratio:.1f}x "
            f"on {txns_5m} txns"
        )

    if buy_sell_points:
        score += buy_sell_points
        breakdown.append(
            f"{buy_sell_text} (+{buy_sell_points})"
        )

    if metrics.volume_5m >= min_volume_usd * 3:
        score += 15
        breakdown.append(
            f"5m volume ${metrics.volume_5m:,.0f} (+15)"
        )
    elif metrics.volume_5m >= min_volume_usd * 2:
        score += 10
        breakdown.append(
            f"5m volume ${metrics.volume_5m:,.0f} (+10)"
        )
    elif metrics.volume_5m >= min_volume_usd:
        score += 5
        breakdown.append(
            f"5m volume ${metrics.volume_5m:,.0f} (+5)"
        )

    if volume_jump >= 3:
        score += 10
        breakdown.append(
            f"Volume jump {volume_jump:.1f}x (+10)"
        )

    immediate_base_pass = (
        price_jump >= min_price_jump
        and volume_liquidity_ratio >= min_volume_liquidity_ratio
        and flow_buy_sell_ratio >= min_buy_sell_ratio
        and metrics.volume_5m >= min_volume_usd
    )

    immediate_pass = (
        immediate_base_pass
        and not bonding_extended_cooling
    )

    immediate_missing = []

    if price_jump < min_price_jump:
        immediate_missing.append("5m_price")

    if volume_liquidity_ratio < min_volume_liquidity_ratio:
        immediate_missing.append("5m_vol_liq")

    if flow_buy_sell_ratio < min_buy_sell_ratio:
        immediate_missing.append(
            "5m_buy_sell"
            if flow_observed_5m
            else "flow_unconfirmed_5m"
        )

    if metrics.volume_5m < min_volume_usd:
        immediate_missing.append("5m_volume")

    momentum_base_pass = (
        metrics.lifecycle == "bonding_curve"
        and (
            metrics.price_change_1h
            >= IGNITION_BONDING_MOMENTUM_MIN_PRICE_CHANGE_1H
            or metrics.price_change_6h
            >= IGNITION_BONDING_MOMENTUM_MIN_PRICE_CHANGE_6H
        )
        and h1_volume_liquidity_ratio
        >= IGNITION_BONDING_MOMENTUM_MIN_VOLUME_LIQUIDITY_RATIO_1H
        and h1_flow_buy_sell_ratio
        >= IGNITION_BONDING_MOMENTUM_MIN_BUY_SELL_RATIO_1H
        and h1_txns >= IGNITION_BONDING_MOMENTUM_MIN_TXNS_1H
        and metrics.volume_1h >= min_momentum_volume_usd
    )

    bonding_momentum_quality_pass = (
        bonding_high_conviction
        or bonding_early_revival
        or bonding_scalp_candidate
    )

    momentum_pass = (
        momentum_base_pass
        and bonding_momentum_quality_pass
        and not bonding_extended_cooling
    )

    early_revival_pass = (
        bonding_early_revival
        and not bonding_extended_cooling
    )

    momentum_missing = []

    if metrics.lifecycle != "bonding_curve":
        momentum_missing.append("not_bonding")

    if not (
        metrics.price_change_1h
        >= IGNITION_BONDING_MOMENTUM_MIN_PRICE_CHANGE_1H
        or metrics.price_change_6h
        >= IGNITION_BONDING_MOMENTUM_MIN_PRICE_CHANGE_6H
    ):
        momentum_missing.append("h1_h6_price")

    if (
        h1_volume_liquidity_ratio
        < IGNITION_BONDING_MOMENTUM_MIN_VOLUME_LIQUIDITY_RATIO_1H
    ):
        momentum_missing.append("h1_vol_liq")

    if (
        h1_flow_buy_sell_ratio
        < IGNITION_BONDING_MOMENTUM_MIN_BUY_SELL_RATIO_1H
    ):
        momentum_missing.append(
            "h1_buy_sell"
            if flow_observed_1h
            else "flow_unconfirmed_1h"
        )

    if h1_txns < IGNITION_BONDING_MOMENTUM_MIN_TXNS_1H:
        momentum_missing.append("h1_txns")

    if metrics.volume_1h < min_momentum_volume_usd:
        momentum_missing.append("h1_volume")

    if bonding_extended_cooling:
        momentum_missing.append("extended_cooling")

    if (
        metrics.lifecycle == "bonding_curve"
        and momentum_base_pass
        and not bonding_momentum_quality_pass
    ):
        momentum_missing.append("bonding_quality")

    if metrics.lifecycle == "bonding_curve":

        if metrics.price_change_6h >= 80:
            score += 20
            breakdown.append(
                "Bonding momentum "
                f"6h price +{metrics.price_change_6h:.1f}% (+20)"
            )
        elif (
            metrics.price_change_6h
            >= IGNITION_BONDING_MOMENTUM_MIN_PRICE_CHANGE_6H
        ):
            score += 15
            breakdown.append(
                "Bonding momentum "
                f"6h price +{metrics.price_change_6h:.1f}% (+15)"
            )

        if metrics.price_change_1h >= 20:
            score += 15
            breakdown.append(
                "Bonding momentum "
                f"1h price +{metrics.price_change_1h:.1f}% (+15)"
            )
        elif (
            metrics.price_change_1h
            >= IGNITION_BONDING_MOMENTUM_MIN_PRICE_CHANGE_1H
        ):
            score += 10
            breakdown.append(
                "Bonding momentum "
                f"1h price +{metrics.price_change_1h:.1f}% (+10)"
            )

        if h1_volume_liquidity_ratio >= 1:
            score += 20
            breakdown.append(
                "1h volume/liquidity expansion "
                f"{h1_volume_liquidity_ratio:.1%} (+20)"
            )
        elif (
            h1_volume_liquidity_ratio
            >= IGNITION_BONDING_MOMENTUM_MIN_VOLUME_LIQUIDITY_RATIO_1H
        ):
            score += 15
            breakdown.append(
                "1h volume/liquidity expansion "
                f"{h1_volume_liquidity_ratio:.1%} (+15)"
            )

        if metrics.volume_1h >= min_momentum_volume_usd * 2:
            score += 15
            breakdown.append(
                f"1h volume ${metrics.volume_1h:,.0f} (+15)"
            )
        elif metrics.volume_1h >= min_momentum_volume_usd:
            score += 10
            breakdown.append(
                f"1h volume ${metrics.volume_1h:,.0f} (+10)"
            )

        if h1_flow_buy_sell_ratio >= 1.2:
            score += 10
            breakdown.append(
                f"1h dollar flow {h1_flow_buy_sell_ratio:.1f}x sells (+10)"
            )
        elif (
            h1_flow_buy_sell_ratio
            >= IGNITION_BONDING_MOMENTUM_MIN_BUY_SELL_RATIO_1H
        ):
            score += 5
            breakdown.append(
                f"1h neutral-to-buy dollar flow {h1_flow_buy_sell_ratio:.1f}x sells (+5)"
            )

        if h1_txns >= 50:
            score += 5
            breakdown.append(
                f"1h activity {h1_txns} txns (+5)"
            )
        elif h1_txns >= IGNITION_BONDING_MOMENTUM_MIN_TXNS_1H:
            score += 3
            breakdown.append(
                f"1h activity {h1_txns} txns (+3)"
            )

    if low_fdv_accumulation:
        score += 30
        breakdown.append(
            "Low-FDV accumulation: "
            f"FDV ${metrics.fdv:,.0f}, "
            f"liquidity ${metrics.liquidity:,.0f}, "
            f"5m Vol/Liq {volume_liquidity_ratio:.1%}, "
            f"1h Vol/Liq {h1_volume_liquidity_ratio:.1%}, "
            f"5m pullback {metrics.price_change_5m:+.1f}% (+30)"
        )

    raw_score = score
    penalty = 0
    signal_penalty = 0
    penalties = []
    flow_unconfirmed = not flow_observed_5m or not flow_observed_1h

    if flow_unconfirmed:
        penalty += 10
        # data gap — not counted as signal_penalty
        penalties.append(
            "Unconfirmed dollar flow "
            f"(txn proxy 5m={buy_sell_ratio:.1f}x "
            f"1h={h1_buy_sell_ratio:.1f}x) (-10)"
        )

    if h1_flow_buy_sell_ratio < 0.75:
        penalty += 15
        signal_penalty += 15
        penalties.append(
            f"1h sell dollar flow {h1_flow_buy_sell_ratio:.1f}x buys/sells (-15)"
        )
    elif h1_flow_buy_sell_ratio < 0.90:
        penalty += 8
        signal_penalty += 8
        penalties.append(
            f"Soft 1h sell dollar flow {h1_flow_buy_sell_ratio:.1f}x (-8)"
        )

    if h1_txns < 20 and not bonding_scalp_candidate:
        penalty += 10
        signal_penalty += 10
        penalties.append(
            f"Thin 1h activity {h1_txns} txns (-10)"
        )

    if (
        volume_liquidity_ratio >= 1.50
        and h1_flow_buy_sell_ratio < 1.20
    ):
        penalty += 10
        # only a signal problem when 1h flow was actually observed;
        # when gRPC is down the discounted proxy triggers this unfairly
        if flow_observed_1h:
            signal_penalty += 10
        penalties.append(
            "Extreme 5m volume/liquidity without strong 1h dollar flow (-10)"
        )

    if (
        volume_liquidity_ratio >= 1.50
        and price_jump < 1.05
        and flow_buy_sell_ratio < 1.20
    ):
        penalty += 10
        signal_penalty += 10
        penalties.append(
            f"Volume without price/flow confirmation: "
            f"vol/liq {volume_liquidity_ratio:.1%}, "
            f"price {price_jump:.2f}x, "
            f"flow {flow_buy_sell_ratio:.1f}x (-10)"
        )

    if (
        metrics.price_change_1h < 0
        and not immediate_pass
    ):
        penalty += 10
        signal_penalty += 10
        penalties.append(
            f"Negative 1h price change {metrics.price_change_1h:.1f}% (-10)"
        )

    if metrics.lifecycle == "migrated":

        if migrated_fragile:
            penalty += 25
            signal_penalty += 25
            penalties.append(
                "Migrated fragile setup: "
                f"FDV ${metrics.fdv:,.0f} with 5m Vol/Liq "
                f"{volume_liquidity_ratio:.1%} (-25)"
            )
        elif (
            migrated_stale_volume
            and not migrated_high_quality
        ):
            penalty += (
                IGNITION_MIGRATED_STALE_VOLUME_SHARE_PENALTY
            )
            signal_penalty += (
                IGNITION_MIGRATED_STALE_VOLUME_SHARE_PENALTY
            )
            penalties.append(
                "Migrated stale continuation: "
                "5m volume share "
                f"{volume_share_5m_1h:.1%} of 1h "
                f"(-{IGNITION_MIGRATED_STALE_VOLUME_SHARE_PENALTY})"
            )

    if metrics.lifecycle == "bonding_curve":

        if bonding_extended_cooling:
            penalty += 35
            signal_penalty += 35
            penalties.append(
                "Extended move cooling: "
                f"+{extended_price_change:.1f}% max 1h/6h "
                "with 5m Vol/Liq "
                f"{volume_liquidity_ratio:.1%} (-35)"
            )
        if metrics.age_hours < 6:
            penalty += 5
            signal_penalty += 5
            penalties.append(
                f"Fresh mint age {metrics.age_hours:.1f}h (-5)"
            )

        # Demoted to 0 by default after the 2026-06-10 penalty audit — this
        # fired on the best-performing cohort (momentum continuation). The
        # config docstring has the numbers; restore via env to re-enable.
        if (
            metrics.price_change_6h >= 150
            and IGNITION_EXTENDED_6H_MOVE_PENALTY > 0
        ):
            penalty += IGNITION_EXTENDED_6H_MOVE_PENALTY
            signal_penalty += IGNITION_EXTENDED_6H_MOVE_PENALTY
            penalties.append(
                f"Extended 6h move +{metrics.price_change_6h:.1f}% "
                f"(-{IGNITION_EXTENDED_6H_MOVE_PENALTY:.0f})"
            )

    for penalty_text in penalties:
        breakdown.append(penalty_text)

    score = max(
        score - penalty,
        0
    )

    score = min(
        score,
        150
    )

    details["raw_score"] = raw_score
    details["penalty"] = penalty
    details["signal_penalty"] = signal_penalty
    details["penalties"] = penalties

    details["immediate_pass"] = immediate_pass
    details["immediate_base_pass"] = immediate_base_pass
    details["momentum_pass"] = momentum_pass
    details["momentum_base_pass"] = momentum_base_pass
    details["early_revival_pass"] = early_revival_pass
    details["low_fdv_accumulation_pass"] = low_fdv_accumulation
    details["immediate_missing"] = immediate_missing
    details["momentum_missing"] = momentum_missing
    details["alert_eligible"] = (
        immediate_pass
        or momentum_pass
        or early_revival_pass
        or low_fdv_accumulation
        or migrated_early_revival
    )

    if bonding_extended_cooling:
        details["alert_eligible"] = False
        details["alert_route"] = "none"
        details["reason"] = "extended_cooling_reject"
        details["missing"] = [
            "extended_cooling",
            "5m_vol_liq_confirmation"
        ]
    elif migrated_fragile:
        details["alert_eligible"] = False
        details["alert_route"] = "none"
        details["reason"] = "migrated_fragile_reject"
        details["missing"] = [
            "high_fdv_low_5m_vol_liq",
            "broad_5m_participation"
        ]
    elif migrated_stale_volume and not migrated_early_revival:
        details["alert_eligible"] = False
        details["alert_route"] = "none"
        details["reason"] = "migrated_stale_reject"
        details["missing"] = [
            "migrated_stale_volume",
            "fresh_5m_volume_share"
        ]
    elif migrated_early_revival:
        details["alert_route"] = "migrated_revival"
        details["reason"] = "passed_migrated_revival"
    elif low_fdv_accumulation:
        details["alert_route"] = "low_fdv_accumulation"
        details["reason"] = "passed_low_fdv_accumulation"
    elif immediate_pass:
        if bonding_scalp_candidate:
            details["alert_route"] = "bonding_scalp"
            details["reason"] = "passed_bonding_scalp"
        elif bonding_early_revival:
            details["alert_route"] = "bonding_early_revival"
            details["reason"] = "passed_bonding_early_revival"
        else:
            details["alert_route"] = "immediate"
            details["reason"] = "passed_immediate"
    elif momentum_pass:
        if bonding_high_conviction:
            details[
                "alert_route"
            ] = "bonding_momentum_high_conviction"
            details[
                "reason"
            ] = "passed_bonding_high_conviction"
        elif bonding_early_revival:
            details["alert_route"] = "bonding_early_revival"
            details["reason"] = "passed_bonding_early_revival"
        else:
            details["alert_route"] = "bonding_momentum_scalp"
            details["reason"] = "passed_bonding_momentum_scalp"
    elif early_revival_pass:
        details["alert_route"] = "bonding_early_revival"
        details["reason"] = "passed_bonding_early_revival"
    elif score < IGNITION_ALERT_THRESHOLD:
        details["reason"] = "score_below_threshold"
        details["missing"] = immediate_missing
    elif metrics.lifecycle == "bonding_curve":
        details["reason"] = "missing_route_gates"
        details["missing"] = list(
            dict.fromkeys(
                immediate_missing
                + momentum_missing
            )
        )
    else:
        details["reason"] = "missing_immediate_gates"
        details["missing"] = immediate_missing

    # Unify quality_tag with alert_route for alerted tokens so both fields
    # carry the same canonical name. State-only tags (extended_cooling_reject,
    # migrated_fragile, etc.) are preserved on non-alerted snapshots.
    if (
        details.get("alert_eligible", False)
        and details.get("alert_route", "none") != "none"
    ):
        details["quality_tag"] = details["alert_route"]

    score = recalibrate_ignition_score(
        metrics,
        details,
        score,
        breakdown
    )

    if (
        details.get("alert_eligible", False)
        and score < IGNITION_ALERT_THRESHOLD
    ):
        details["reason"] = "recalibrated_score_below_threshold"
        details["missing"] = list(
            dict.fromkeys(
                details.get("missing", [])
                + ["recalibrated_score"]
            )
        )

    return score, breakdown, details


async def refresh_candidates_loop(
    discovery
):

    while True:

        try:

            await discovery.refresh_candidates()

        except Exception as e:

            print(
                f"Refresh error: {e}"
            )

        await asyncio.sleep(
            CANDIDATE_REFRESH_INTERVAL
        )


async def priority_scan_loop(
    client
):

    while True:

        try:

            if not PRIORITY_SCAN_QUEUE:
                await asyncio.sleep(1)
                continue

            token_address = (
                PRIORITY_SCAN_QUEUE.popleft()
            )

            PRIORITY_SCAN_SET.discard(
                token_address
            )

            if is_excluded_contract_address(
                token_address
            ):
                continue

            if token_address not in TRACKED_CANDIDATES:
                continue

            memory = TOKEN_MEMORY[
                token_address
            ]

            now = time.time()

            if (
                now
                - memory["last_grpc_scan"]
                < GRPC_IMMEDIATE_SCAN_COOLDOWN_SECONDS
            ):
                continue

            memory[
                "last_grpc_scan"
            ] = now

            memory[
                "last_scan"
            ] = now

            await process_token(
                client,
                token_address
            )

        except Exception as e:

            print(
                f"Priority scan error: {e}"
            )


async def position_status_loop():

    while True:

        await asyncio.sleep(
            POSITION_STATUS_REPORT_INTERVAL_SECONDS
        )

        try:
            await refresh_position_sol_usd()
            live_prices = {}
            live_refresh = None
            open_refs = position_engine.open_position_refs()
            open_addresses = [
                address
                for address, _chain in open_refs
            ]
            chain_by_address = {
                address: chain
                for address, chain in open_refs
            }

            if open_addresses:
                live_prices, live_refresh = await fetch_live_prices(
                    open_addresses,
                    chain_by_address=chain_by_address
                )

            report = position_engine.build_status_report(
                time.time(),
                live_prices=live_prices,
                live_refresh=live_refresh
            )

            if report:
                await telegram.send_position_status(
                    report
                )

        except Exception as e:
            print(
                f"Position status error: {e}"
            )


async def alert_performance_summary_loop():

    while True:

        await asyncio.sleep(
            ALERT_PERFORMANCE_SUMMARY_INTERVAL_SECONDS
        )

        try:
            now = time.time()
            since, until = local_day_window(now)
            report = await scanner_storage.build_ignition_alert_report(
                now,
                since=since,
                until=until
            )
            report["window"]["label"] = "today"

            if report["summary"]["alerts"] > 0:
                await telegram.send_alert_performance_summary(
                    report
                )

        except Exception as e:
            print(
                f"Alert performance summary error: {e}"
            )


async def llm_pattern_report_loop():

    while True:

        await asyncio.sleep(
            LLM_PATTERN_REPORT_INTERVAL_SECONDS
        )

        if not LLM_PATTERN_REPORTS_ENABLED:
            continue

        if not llm_pattern_analyzer.ready():
            continue

        try:
            now = time.time()
            since = (
                now
                - LLM_PATTERN_REPORT_LOOKBACK_HOURS
                * 3600
            )
            report = await scanner_storage.build_ignition_alert_report(
                now,
                since=since
            )

            alert_count = report["summary"].get(
                "alerts",
                0
            )

            if alert_count < LLM_PATTERN_REPORT_MIN_ALERTS:
                continue

            llm_report = await llm_pattern_analyzer.analyze(
                report.get("alerts", []),
                report.get("summary", {}),
                LLM_PATTERN_REPORT_LOOKBACK_HOURS
            )

            if not llm_report:
                continue

            delivered = await telegram.send_llm_pattern_report(
                llm_report.get("html")
                or llm_report.get("text")
            )

            await scanner_storage.record_llm_pattern_report(
                llm_report.get("provider"),
                llm_report.get("model"),
                LLM_PATTERN_REPORT_LOOKBACK_HOURS,
                alert_count,
                llm_report.get("text"),
                raw_payload={
                    "parsed": llm_report.get("parsed"),
                    "html": llm_report.get("html"),
                    "delivered": delivered
                },
                created_at=now
            )

        except Exception as e:
            print(
                f"LLM pattern report error: {e}"
            )


async def trending_cache_loop():

    while True:

        try:
            await refresh_trending_cache()
        except Exception as e:
            print(
                f"Trending cache error: {e}"
            )

        await asyncio.sleep(300)


def build_open_position_metrics(
    address,
    position,
    pair
):

    base_token = pair.get(
        "baseToken",
        {}
    )
    base_address = str(
        base_token.get("address", "")
    )

    if base_address.lower() != str(address).lower():
        return None

    price = safe_float(
        pair.get("priceUsd"),
        0
    )

    if price <= 0:
        return None

    market = build_market_context(
        pair,
        lifecycle_hint=position.get("lifecycle")
    )

    volume = pair.get("volume") or {}
    txns = pair.get("txns") or {}
    txns_5m = txns.get("m5") or {}
    txns_1h = txns.get("h1") or {}
    price_change = pair.get("priceChange") or {}

    buys_5m = int(
        safe_float(
            txns_5m.get("buys"),
            0
        )
    )
    sells_5m = int(
        safe_float(
            txns_5m.get("sells"),
            0
        )
    )
    buys_1h = int(
        safe_float(
            txns_1h.get("buys"),
            0
        )
    )
    sells_1h = int(
        safe_float(
            txns_1h.get("sells"),
            0
        )
    )
    volume_5m = safe_float(
        volume.get("m5"),
        0
    )
    volume_1h = safe_float(
        volume.get("h1"),
        0
    )
    memory = TOKEN_MEMORY[address]
    trade_volumes = summarize_trade_volumes(
        memory,
        price,
        time.time(),
        volume_5m,
        volume_1h,
        buys_5m,
        sells_5m,
        buys_1h,
        sells_1h
    )

    return TokenMetrics(
        address=address,
        symbol=base_token.get(
            "symbol",
            position.get("symbol", "")
        ),
        name=base_token.get(
            "name",
            position.get("name", "")
        ),
        pair_address=pair.get(
            "pairAddress",
            position.get("pair_address", "")
        ),
        liquidity=market["liquidity"],
        fdv=market["fdv"],
        price=price,
        volume_5m=volume_5m,
        volume_1h=volume_1h,
        buys_5m=buys_5m,
        sells_5m=sells_5m,
        buys_1h=buys_1h,
        sells_1h=sells_1h,
        price_change_5m=safe_float(
            price_change.get("m5"),
            0
        ),
        price_change_1h=safe_float(
            price_change.get("h1"),
            0
        ),
        price_change_6h=safe_float(
            price_change.get("h6"),
            0
        ),
        price_change_24h=safe_float(
            price_change.get("h24"),
            0
        ),
        age_hours=safe_float(
            position.get("age_hours"),
            0
        ),
        buy_volume_5m=trade_volumes["buy_volume_5m"],
        sell_volume_5m=trade_volumes["sell_volume_5m"],
        buy_volume_1h=trade_volumes["buy_volume_1h"],
        sell_volume_1h=trade_volumes["sell_volume_1h"],
        buy_sell_volume_source_5m=trade_volumes["source_5m"],
        buy_sell_volume_source_1h=trade_volumes["source_1h"],
        age_source="open_position_live_refresh",
        chain=pair.get("chainId", "solana"),
        source=pair.get("dexId", "dexscreener"),
        lifecycle=market["lifecycle"],
        raw_liquidity=market["raw_liquidity"],
        raw_base_reserve=market["raw_base_reserve"],
        raw_quote_reserve=market["raw_quote_reserve"],
        liquidity_source=market["liquidity_source"],
        migration_fdv=market.get("migration_fdv", 0),
        migration_distance_usd=market.get(
            "migration_distance_usd",
            0
        ),
        migration_distance_pct=market.get(
            "migration_distance_pct",
            0
        ),
        migration_fdv_source=market.get(
            "migration_fdv_source",
            ""
        )
    )


def open_position_ignition_details(
    metrics,
    position
):

    previous_price = safe_float(
        position.get("last_price"),
        metrics.price
    )
    last_scan_price_jump = (
        metrics.price
        / previous_price
        if previous_price > 0
        else 1
    )
    price_change_5m_jump = (
        1
        + metrics.price_change_5m
        / 100
    )
    volume_liquidity_ratio = (
        metrics.volume_5m
        / max(metrics.liquidity, 1)
    )
    h1_volume_liquidity_ratio = (
        metrics.volume_1h
        / max(metrics.liquidity, 1)
    )
    buy_sell_ratio = (
        metrics.buys_5m
        / max(metrics.sells_5m, 1)
    )
    h1_buy_sell_ratio = (
        metrics.buys_1h
        / max(metrics.sells_1h, 1)
    )
    buy_sell_volume_ratio = (
        metrics.buy_volume_5m
        / max(metrics.sell_volume_5m, 1e-18)
        if metrics.sell_volume_5m > 0
        else 999 if metrics.buy_volume_5m > 0 else 0
    )

    return {
        "price_jump": max(
            last_scan_price_jump,
            price_change_5m_jump
        ),
        "volume_liquidity_ratio": volume_liquidity_ratio,
        "buy_sell_ratio": buy_sell_ratio,
        "buy_sell_volume_ratio": buy_sell_volume_ratio,
        "buy_sell_volume_source_5m": (
            metrics.buy_sell_volume_source_5m
        ),
        "h1_volume_liquidity_ratio": h1_volume_liquidity_ratio,
        "h1_buy_sell_ratio": h1_buy_sell_ratio,
        "txns_5m": metrics.buys_5m + metrics.sells_5m,
        "txns_1h": metrics.buys_1h + metrics.sells_1h,
        "alert_eligible": True,
        "quality_tag": "open_position_live",
        "alert_route": "open_position_monitor",
        "reason": "open_position_live_refresh"
    }


async def update_open_position_candles(
    metrics,
    ignition_details,
    now
):

    if not LOCAL_RSI_ENABLED:
        return

    observation = {
        "token_address": metrics.address,
        "symbol": metrics.symbol,
        "pair_address": metrics.pair_address,
        "chain_name": metrics.chain,
        "price": metrics.price,
        "volume_5m": metrics.volume_5m,
        "liquidity": metrics.liquidity,
        "timestamp": now
    }

    try:
        await scanner_storage.save_token_candle_observation(
            observation,
            timeframe_seconds=LOCAL_RSI_TIMEFRAME_SECONDS
        )
    except Exception as e:
        print(
            f"Open position candle update error: {e}"
        )


def anchored_vwap_fields(
    signal,
    source
):

    fields = {
        key: value
        for key, value in dict(signal or {}).items()
        if key.startswith("anchored_")
    }
    fields["anchored_vwap_source"] = source

    return fields


def should_refresh_anchored_vwap_provider(
    metrics,
    now,
    cache_scope="default"
):

    if not ANCHORED_VWAP_PROVIDER_REFRESH_ENABLED:
        return False

    if not getattr(metrics, "pair_address", ""):
        return False

    key = (
        str(getattr(metrics, "chain", "") or "solana").lower(),
        str(getattr(metrics, "address", "")),
        str(getattr(metrics, "pair_address", "")),
        str(cache_scope or "default")
    )
    last_fetched_at = ANCHORED_VWAP_PROVIDER_FETCHED_AT.get(
        key,
        0
    )

    if (
        now - last_fetched_at
        < max(ANCHORED_VWAP_PROVIDER_REFRESH_SECONDS, 1)
    ):
        return False

    ANCHORED_VWAP_PROVIDER_FETCHED_AT[key] = now
    return True


async def fetch_provider_anchored_vwap_candles(
    metrics,
    now,
    since=None
):

    if since is None:
        since = (
            now
            - max(ANCHORED_VWAP_LOOKBACK_SECONDS, 1)
            - max(ANCHORED_VWAP_PROVIDER_PADDING_SECONDS, 0)
        )

    candles = await asyncio.to_thread(
        fetch_ohlcv_window,
        metrics.pair_address,
        metrics.address,
        since,
        now,
        ANCHORED_VWAP_PROVIDER_MAX_PAGES,
        chain_id=metrics.chain
    )

    for candle in candles:
        timestamp = safe_float(
            candle.get("timestamp"),
            0
        )
        close = safe_float(
            candle.get("close"),
            0
        )

        if timestamp <= 0 or close <= 0:
            continue

        await scanner_storage.save_token_candle_observation(
            {
                "token_address": metrics.address,
                "symbol": metrics.symbol,
                "pair_address": metrics.pair_address,
                "chain_name": metrics.chain,
                "timestamp": timestamp,
                "open": candle.get("open"),
                "high": candle.get("high"),
                "low": candle.get("low"),
                "close": close,
                "price": close,
                "volume": candle.get("volume"),
                "liquidity": metrics.liquidity,
                "fdv": metrics.fdv,
                "market_cap": metrics.fdv,
                "source": candle.get(
                    "source",
                    "provider_ohlcv"
                )
            },
            timeframe_seconds=ANCHORED_VWAP_TIMEFRAME_SECONDS
        )

    return candles


async def update_anchored_vwap(
    metrics,
    ignition_details,
    now,
    source_label,
    provider_refresh_allowed=False,
    anchor_timestamp=None,
    anchor_name="1h_low"
):

    if not ANCHORED_VWAP_ENABLED:
        ignition_details["anchored_vwap_enabled"] = False
        return

    ignition_details["anchored_vwap_enabled"] = True

    try:
        await scanner_storage.save_token_candle_observation(
            token_candle_observation(
                metrics,
                now,
                source_label
            ),
            timeframe_seconds=ANCHORED_VWAP_TIMEFRAME_SECONDS
        )

        anchor_timestamp = safe_float(
            anchor_timestamp,
            0
        )
        candle_limit = ANCHORED_VWAP_CANDLE_LIMIT
        if anchor_timestamp > 0:
            needed_candles = int(
                max(
                    now - anchor_timestamp,
                    0
                )
                / max(ANCHORED_VWAP_TIMEFRAME_SECONDS, 1)
            ) + ANCHORED_VWAP_MIN_CANDLES + 2
            candle_limit = min(
                max(
                    candle_limit,
                    needed_candles
                ),
                1440
            )

        local_candles = await scanner_storage.load_token_candles(
            metrics.address,
            timeframe_seconds=ANCHORED_VWAP_TIMEFRAME_SECONDS,
            limit=candle_limit,
            until=now
        )
        if anchor_timestamp > 0:
            signal = anchored_vwap_from_time(
                local_candles,
                anchor_timestamp=anchor_timestamp,
                until=now,
                min_candles=ANCHORED_VWAP_MIN_CANDLES,
                anchor_name=anchor_name
            )
        else:
            signal = anchored_vwap_from_low(
                local_candles,
                lookback_seconds=ANCHORED_VWAP_LOOKBACK_SECONDS,
                until=now,
                min_candles=ANCHORED_VWAP_MIN_CANDLES
            )
        source = "scanner_candles"

        if (
            provider_refresh_allowed
            and should_refresh_anchored_vwap_provider(
                metrics,
                now,
                cache_scope=anchor_name
            )
        ):
            provider_since = None
            if anchor_timestamp > 0:
                provider_since = max(
                    anchor_timestamp
                    - max(
                        ANCHORED_VWAP_PROVIDER_PADDING_SECONDS,
                        0
                    ),
                    0
                )
            provider_candles = await fetch_provider_anchored_vwap_candles(
                metrics,
                now,
                since=provider_since
            )

            if provider_candles:
                if anchor_timestamp > 0:
                    provider_signal = anchored_vwap_from_time(
                        provider_candles,
                        anchor_timestamp=anchor_timestamp,
                        until=now,
                        min_candles=ANCHORED_VWAP_MIN_CANDLES,
                        anchor_name=anchor_name
                    )
                else:
                    provider_signal = anchored_vwap_from_low(
                        provider_candles,
                        lookback_seconds=ANCHORED_VWAP_LOOKBACK_SECONDS,
                        until=now,
                        min_candles=ANCHORED_VWAP_MIN_CANDLES
                    )
                if (
                    provider_signal.get("anchored_vwap_ready")
                    or not signal.get("anchored_vwap_ready")
                ):
                    signal = provider_signal
                    source = provider_candles[-1].get(
                        "source",
                        "provider_ohlcv"
                    )

        ignition_details.update(
            anchored_vwap_fields(
                signal,
                source
            )
        )
    except Exception as e:
        ignition_details.update({
            "anchored_vwap_ready": False,
            "anchored_vwap_reason": "update_error",
            "anchored_vwap_error": str(e)[:120]
        })
        print(
            f"Anchored VWAP update error: {e}"
        )


def token_candle_observation(
    metrics,
    now,
    source_label
):

    price_native = 0
    if metrics.chain == "solana":
        sol_usd = sol_usd_price_feed.current_price()
        if sol_usd > 0:
            price_native = metrics.price / sol_usd

    return {
        "token_address": metrics.address,
        "symbol": metrics.symbol,
        "pair_address": metrics.pair_address,
        "chain_name": metrics.chain,
        "price": metrics.price,
        "price_native": price_native,
        "volume_5m": metrics.volume_5m,
        "volume_1h": metrics.volume_1h,
        "liquidity": metrics.liquidity,
        "fdv": metrics.fdv,
        "market_cap": metrics.fdv,
        "source": (
            metrics.source
            or source_label
        ),
        "timestamp": now
    }


def attach_exit_quote_details(
    ignition_details,
    quote
):

    if not quote:
        return

    ignition_details.update({
        "exit_quote_checked": True,
        "exit_quote_available": quote.get("quote_available", False),
        "exit_quote_provider": quote.get("provider", ""),
        "exit_quote_value_usd": quote.get("quote_value_usd", 0),
        "exit_quote_min_value_usd": quote.get(
            "min_quote_value_usd",
            0
        ),
        "exit_quote_output_amount": quote.get("output_amount", 0),
        "exit_quote_output_mint": quote.get("output_mint", ""),
        "exit_quote_price_impact_pct": quote.get(
            "price_impact_pct",
            0
        ),
        "exit_quote_route": str(
            quote.get("route", "")
        )[:240],
        "exit_quote_error": quote.get("error", ""),
        "exit_quote_cached": quote.get("cached", False),
        "exit_quote_attempt_name": quote.get("attempt_name", ""),
        "exit_quote_attempt_count": quote.get("attempt_count", 0),
        "exit_quote_fallback_used": quote.get("fallback_used", False),
        "exit_quote_attempts": quote.get("attempts", [])
    })


async def update_exit_quote(
    position,
    metrics,
    ignition_details
):

    try:
        quote = await live_execution.quote_exit_value(
            position=position,
            metrics=metrics,
            output_price_usd=sol_usd_price_feed.current_price()
        )
        attach_exit_quote_details(
            ignition_details,
            quote
        )
    except Exception as e:
        ignition_details.update({
            "exit_quote_checked": True,
            "exit_quote_available": False,
            "exit_quote_error": str(e)
        })


def apply_quote_price_sanity(
    position,
    metrics,
    ignition_details
):
    """Cross-check the spot price (gRPC/DexScreener) against the
    executable Definitive exit quote already fetched this scan, and
    replace the spot price with the quote-implied price when the two
    diverge beyond the configured threshold. This is a price oracle
    cross-check only — it never executes. It guards against spurious
    near-zero on-chain ticks that otherwise corrupt RSI/VWAP/peak and
    force-close positions at fabricated losses. Must run before the
    price feeds RSI/VWAP/stops."""

    if not POSITION_PRICE_QUOTE_SANITY_ENABLED:
        return

    if not ignition_details.get("exit_quote_available"):
        return

    quote_value_usd = safe_float(
        ignition_details.get("exit_quote_value_usd"),
        0
    )

    if quote_value_usd < POSITION_PRICE_QUOTE_SANITY_MIN_QUOTE_VALUE_USD:
        return

    remaining_tokens = safe_float(
        position.get("remaining_tokens"),
        0
    )

    if remaining_tokens <= 0:
        return

    quote_implied_price = quote_value_usd / remaining_tokens

    if quote_implied_price <= 0:
        return

    spot_price = safe_float(metrics.price, 0)

    deviation = (
        abs(spot_price - quote_implied_price)
        / quote_implied_price
    )

    if deviation <= POSITION_PRICE_QUOTE_SANITY_MAX_DEVIATION_PCT:
        return

    print(
        "PRICE SANITY override "
        f"{metrics.symbol or metrics.address} "
        f"spot={spot_price:.8g} "
        f"quote_implied={quote_implied_price:.8g} "
        f"deviation={deviation * 100:.1f}% "
        f"provider={ignition_details.get('exit_quote_provider', '')}"
    )

    ignition_details["price_sanity_corrected"] = True
    ignition_details["price_sanity_spot_price"] = spot_price
    ignition_details["price_sanity_quote_price"] = quote_implied_price
    ignition_details["price_sanity_deviation_pct"] = deviation

    metrics.price = quote_implied_price


def attach_live_execution_details(
    event,
    result
):

    result = dict(
        result or {}
    )

    if not result:
        return event

    event["live_execution_enabled"] = bool(
        result.get("enabled")
    )
    event["live_execution_provider"] = result.get(
        "provider",
        ""
    )
    event["live_execution_submitted"] = bool(
        result.get("submitted")
    )
    event["live_execution_skipped"] = bool(
        result.get("skipped")
    )
    event["live_execution_dry_run"] = bool(
        result.get("dry_run")
    )
    event["live_execution_reason"] = result.get(
        "reason",
        ""
    )
    event["live_execution_order_id"] = result.get(
        "order_id",
        ""
    )
    event["live_execution_side"] = result.get(
        "side",
        ""
    )
    event["live_execution_qty"] = result.get(
        "qty",
        ""
    )
    event["live_execution_order_qty"] = result.get(
        "order_qty",
        ""
    )
    event["live_execution_order_value_usd"] = safe_float(
        result.get("order_value_usd"),
        0
    )
    event["live_execution_contra_asset"] = result.get(
        "contra_asset",
        ""
    )
    contra_asset_usd = safe_float(
        result.get("contra_asset_usd")
        or result.get("contra_asset_price_usd"),
        0
    )
    event["live_execution_contra_asset_usd"] = contra_asset_usd
    event["contra_asset_usd"] = contra_asset_usd
    event["live_execution_quote_ok"] = bool(
        result.get("quote_ok")
    )
    event["live_execution_quote_price_impact"] = safe_float(
        result.get("quote_price_impact"),
        0
    )
    event["live_execution_error"] = (
        result.get("submit_error")
        or result.get("quote_error")
        or ""
    )
    event["live_execution_filled_target_amount"] = safe_float(
        result.get("filled_target_amount"),
        0
    )
    event["live_execution_filled_contra_amount"] = safe_float(
        result.get("filled_contra_amount"),
        0
    )
    event["live_execution_average_fill_price"] = safe_float(
        result.get("average_fill_price"),
        0
    )
    # USD-denominated fill price (contra/notional). average_fill_price is in
    # SOL per token; this is the USD value used for human-readable alerts.
    event["live_execution_average_notional_price"] = safe_float(
        result.get("average_notional_price"),
        0
    )
    return event


async def notify_live_execution_event(
    event
):

    try:
        await telegram.send_live_execution_event(event)
    except Exception as exc:
        print(
            "LIVE EXECUTION notification failed: "
            f"{exc}"
        )


def live_execution_retryable_failure(
    event,
    result
):

    if not LIVE_EXECUTION_RETRY_ENABLED:
        return False

    if not event or not result:
        return False

    if event.get("type") not in (
        "entry",
        "scale_out",
        "live_scale_out",
        "close"
    ):
        return False

    if result.get("submitted") or result.get("dry_run"):
        return False

    expected_side = (
        "buy"
        if event.get("type") == "entry"
        else "sell"
    )

    if (
        result.get("side")
        and result.get("side") != expected_side
    ):
        return False

    reason = str(
        result.get("reason")
        or result.get("submit_error")
        or result.get("quote_error")
        or ""
    )

    non_retryable = (
        "definitive_execution_disabled",
        "definitive_credentials_missing",
        "live_submit_not_armed",
        "chain_not_allowed",
        "contra_asset_missing",
        "entry_notional_below_min_or_exposure_full",
        "definitive_max_open_positions",
        "no_live_entry_for_position",
        "zero_exit_quantity",
        "definitive_order_not_filled_before_timeout"
    )

    if any(item in reason for item in non_retryable):
        return False

    return bool(
        result.get("enabled", True)
    )


async def retry_live_execution_until_submitted(
    event
):

    event = dict(event or {})
    event_key = position_engine.live_execution_event_key(
        event
    )
    delay = max(
        safe_float(
            LIVE_EXECUTION_RETRY_INITIAL_DELAY_SECONDS,
            2.0
        ),
        0
    )
    max_delay = max(
        safe_float(
            LIVE_EXECUTION_RETRY_MAX_DELAY_SECONDS,
            30.0
        ),
        delay
    )
    attempt = 0

    while True:
        event_type = event.get("type")
        position = position_engine.live_execution_position_for_event(
            event
        )

        if not position:
            print(
                "LIVE EXECUTION RETRY stopped: position missing "
                f"{event.get('symbol', '')}"
            )
            return

        if (
            event_type == "entry"
            and position.get("live_execution_entry_submitted")
        ):
            return

        if event_type == "entry":
            # Probe Definitive up to 3 times before re-submitting.
            # The first buy may have landed even if we got a timeout —
            # give the exchange time to index it.
            reconciled_result = None
            for _probe in range(3):
                reconciled_result = await reconcile_live_entry_if_present(
                    event,
                    "live_entry_present_on_definitive"
                )
                if reconciled_result:
                    break
                if _probe < 2:
                    print(
                        "LIVE EXECUTION RETRY probe "
                        f"{_probe + 1}/3 no balance yet "
                        f"{event.get('symbol', '')} — waiting 6s"
                    )
                    await asyncio.sleep(6)

            if reconciled_result:
                attach_live_execution_details(
                    event,
                    reconciled_result
                )
                event["live_execution_retrying"] = False
                print(
                    "LIVE EXECUTION RETRY reconciled entry "
                    f"{event.get('symbol', '')} "
                    "reason=live_entry_present_on_definitive"
                )
                await notify_live_execution_event(event)
                return

        if event_type != "entry":
            if position.get("live_execution_closed"):
                return

            remaining_tokens = safe_float(
                position.get("live_execution_remaining_tokens_estimated"),
                event.get("live_execution_remaining_tokens_estimated")
            )

            if remaining_tokens <= 0:
                print(
                    "LIVE EXECUTION RETRY stopped: zero remaining tokens "
                    f"{event.get('symbol', '')}"
                )
                return

            event["live_execution_remaining_tokens_estimated"] = (
                remaining_tokens
            )
            event["live_execution_sell_tokens"] = remaining_tokens

            live_balance, balance_error = (
                await definitive_live_token_balance(
                    event.get("address", "")
                )
            )

            if live_balance is not None and live_balance <= 0:
                position_engine.mark_live_execution_reconciled_closed(
                    event,
                    "live_position_absent_on_definitive",
                    live_balance=live_balance
                )
                event["live_execution_retrying"] = False
                event["live_execution_reconciled_closed"] = True
                event["live_execution_reason"] = (
                    "live_position_absent_on_definitive"
                )
                print(
                    "LIVE EXECUTION RETRY reconciled closed "
                    f"{event.get('symbol', '')} "
                    "reason=live_position_absent_on_definitive"
                )
                await notify_live_execution_event(event)
                return

            if (
                live_balance is not None
                and live_balance > 0
                and not position.get("live_execution_entry_submitted")
            ):
                position_engine.mark_live_execution_reconciled_entry(
                    event,
                    "live_entry_reconciled_on_retry",
                    live_balance=live_balance,
                    order_id=position.get(
                        "live_execution_entry_order_id", ""
                    ),
                    order_value_usd=safe_float(
                        position.get("live_execution_entry_notional_usd")
                        or position.get("entry_notional_usd"),
                        0
                    )
                )
                print(
                    "LIVE EXECUTION RETRY reconciled entry "
                    f"{event.get('symbol', '')} "
                    f"balance={live_balance:.4f} "
                    "reason=live_entry_reconciled_on_retry"
                )

            if balance_error:
                print(
                    "LIVE EXECUTION RETRY balance check failed "
                    f"{event.get('symbol', '')} "
                    f"error={balance_error}"
                )

        # Entry-retry chase guard: a buy failed (e.g. Definitive API error) and
        # we're retrying — but don't fill if the token has already run far past
        # the intended entry. A brief error leaves price near entry (retry fills
        # and catches it, e.g. GRND); a longer one that let it run aborts here.
        if event_type == "entry":
            intended_price = safe_float(
                event.get("entry_price"),
                position.get("entry_price")
            )
            current_price = safe_float(
                position.get("last_price"),
                0
            )
            max_run = safe_float(
                LIVE_EXECUTION_ENTRY_RETRY_MAX_PRICE_RUN_PCT,
                0
            )
            if (
                max_run > 0
                and intended_price > 0
                and current_price > 0
                and current_price > intended_price * (1 + max_run)
            ):
                print(
                    "LIVE EXECUTION RETRY aborted (entry chased) "
                    f"{event.get('symbol', '')} "
                    f"intended={intended_price:.8f} now={current_price:.8f} "
                    f"run={current_price / intended_price:.2f}x "
                    f"> {1 + max_run:.2f}x"
                )
                position["live_execution_retry_disabled"] = True
                position["live_execution_entry_retry_aborted"] = "chased"
                position_engine.save_state()
                event["live_execution_retrying"] = False
                LIVE_EXECUTION_RETRY_TASKS.pop(event_key, None)
                return

        if attempt > 0 or delay > 0:
            await asyncio.sleep(delay)

        attempt += 1

        try:
            if not event.get("contra_asset_usd"):
                event["contra_asset_usd"] = (
                    live_execution.definitive_contra_asset_price_usd(
                        event,
                        event.get("chain", "solana")
                    )
                )

            result = await live_execution.execute_position_event(
                event,
                open_summary=position_engine.live_execution_open_summary(),
                has_live_position=(
                    event_type == "entry"
                    or position_engine.live_execution_position_has_entry(event)
                )
            )
        except Exception as exc:
            result = {
                "enabled": True,
                "provider": "definitive",
                "submitted": False,
                "skipped": True,
                "side": (
                    "buy"
                    if event_type == "entry"
                    else "sell"
                ),
                "reason": "definitive_execution_error",
                "submit_error": str(exc)
            }

        attach_live_execution_details(
            event,
            result
        )

        if result.get("submitted"):
            position_engine.record_live_execution_result(
                event,
                result
            )
            await live_execution.manage_flash_onchain_stop(
                position_engine,
                event,
                result
            )
            await live_execution.manage_flash_resting_exits(
                position_engine,
                event,
                result
            )
            event["live_execution_retrying"] = False
            print(
                "LIVE EXECUTION RETRY submitted "
                f"{event.get('symbol', '')} "
                f"type={event_type} "
                f"attempt={attempt} "
                f"order={result.get('order_id', '')}"
            )

            await notify_live_execution_event(event)

            return

        print(
            "LIVE EXECUTION RETRY failed "
            f"{event.get('symbol', '')} "
            f"type={event_type} "
            f"attempt={attempt} "
            f"reason={result.get('reason', '')} "
            f"error={result.get('submit_error', '')}"
        )

        if not live_execution_retryable_failure(event, result):
            position_engine.record_live_execution_result(
                event,
                result
            )
            event["live_execution_retrying"] = False
            print(
                "LIVE EXECUTION RETRY stopped as non-retryable "
                f"{event.get('symbol', '')}"
            )
            await notify_live_execution_event(event)
            return

        delay = min(
            max(delay * 2, 1),
            max_delay
        )


def schedule_live_execution_retry(
    event
):

    if not LIVE_EXECUTION_RETRY_ENABLED:
        return

    event_key = position_engine.live_execution_event_key(
        event
    )
    task = LIVE_EXECUTION_RETRY_TASKS.get(event_key)

    if task and not task.done():
        return

    LIVE_EXECUTION_RETRY_TASKS[event_key] = asyncio.create_task(
        retry_live_execution_until_submitted(event)
    )


def definitive_position_asset_address(
    position
):

    if not isinstance(position, dict):
        return ""

    for key in (
        "assetAddress",
        "tokenAddress",
        "address"
    ):
        value = position.get(key)

        if value:
            return str(value)

    for key in (
        "asset",
        "targetAsset"
    ):
        asset = position.get(key)

        if isinstance(asset, dict) and asset.get("address"):
            return str(asset.get("address"))

    return ""


def definitive_position_balance(
    position
):

    if not isinstance(position, dict):
        return 0

    for key in (
        "balance",
        "quantity",
        "amount",
        "tokenAmount"
    ):
        if key in position:
            return safe_float(
                position.get(key),
                0
            )

    size = position.get("size")

    if isinstance(size, dict):
        return safe_float(
            size.get("amount"),
            0
        )

    return 0


def definitive_positions_list(
    raw
):

    if isinstance(raw, list):
        return raw

    if not isinstance(raw, dict):
        return []

    for key in (
        "positions",
        "data",
        "items",
        "results"
    ):
        value = raw.get(key)

        if isinstance(value, list):
            return value

        if isinstance(value, dict):
            nested = definitive_positions_list(value)

            if nested:
                return nested

    return []


async def definitive_live_token_balance(
    address
):

    address = str(address or "").lower()

    if not address:
        return (
            None,
            "missing_address"
        )

    try:
        result = await live_execution.definitive.request(
            "GET",
            "/positions"
        )
    except Exception as exc:
        return (
            None,
            str(exc)
        )

    if not result.get("ok"):
        return (
            None,
            str(
                result.get("error")
                or "definitive_positions_failed"
            )
        )

    for position in definitive_positions_list(
        result.get("raw_response")
    ):
        position_address = definitive_position_asset_address(
            position
        ).lower()

        if position_address != address:
            continue

        return (
            definitive_position_balance(position),
            ""
        )

    return (
        0,
        ""
    )


def reconciled_live_entry_result(
    event,
    position,
    live_balance,
    reason
):

    chain = str(
        event.get("chain")
        or position.get("chain")
        or "solana"
    ).lower()
    order_value_usd = safe_float(
        position.get("live_execution_entry_notional_usd"),
        position.get("entry_notional_usd")
    )

    return {
        "enabled": True,
        "provider": "definitive",
        "event_type": "entry",
        "chain": chain,
        "side": "buy",
        "qty": str(order_value_usd),
        "order_qty": str(live_balance),
        "order_value_usd": order_value_usd,
        "contra_asset": live_execution.definitive_contra_asset(
            chain
        ),
        "contra_asset_usd": safe_float(
            live_execution.definitive_contra_asset_price_usd(
                event,
                chain
            ),
            safe_float(
                position.get("entry_contra_asset_usd")
                or position.get("entry_contra_asset_price_usd")
                or position.get("entry_sol_usd"),
                0
            )
        ),
        "dry_run": False,
        "accepted": True,
        "submitted": True,
        "skipped": False,
        "reconciled": True,
        "already_recorded": True,
        "reason": reason,
        "order_id": position.get(
            "live_execution_entry_order_id",
            ""
        ),
        "filled_target_amount": safe_float(
            live_balance,
            0
        ),
        "filled_contra_amount": 0,
        "average_fill_price": 0,
        "average_notional_price": 0,
        "quote_ok": False,
        "quote_price_impact": 0,
        "submit_status": 200,
        "submit_error": ""
    }


async def startup_reconcile_open_positions():
    if not live_execution.definitive_ordering_enabled():
        return

    state = position_engine.load_state()
    unreconciled = [
        pos for pos in state.get("open", {}).values()
        if not pos.get("live_execution_entry_submitted")
        and str(pos.get("chain", "solana")).lower() == "solana"
    ]

    if not unreconciled:
        return

    try:
        result = await live_execution.definitive.request("GET", "/positions")
    except Exception as exc:
        print(f"STARTUP RECONCILE portfolio fetch failed: {exc}")
        return

    if not result.get("ok"):
        print(
            "STARTUP RECONCILE portfolio fetch error: "
            + str(result.get("error") or "unknown")
        )
        return

    live_positions = {
        definitive_position_asset_address(p).lower(): p
        for p in definitive_positions_list(result.get("raw_response"))
    }

    for position in unreconciled:
        address = str(position.get("address", ""))
        live = live_positions.get(address.lower())
        if not live:
            continue

        balance = definitive_position_balance(live)
        if balance <= 0:
            continue

        entry_event = position_engine.live_execution_entry_event(position) or {}
        event = {
            "type": "entry",
            "address": address,
            "chain": position.get("chain", "solana"),
            "symbol": position.get("symbol", ""),
            "timestamp": entry_event.get(
                "timestamp",
                position.get("entry_at", 0)
            )
        }

        position_engine.mark_live_execution_reconciled_entry(
            event,
            "startup_reconcile_open_position",
            live_balance=balance,
            order_id=position.get("live_execution_entry_order_id", ""),
            order_value_usd=safe_float(
                position.get("live_execution_entry_notional_usd")
                or position.get("entry_notional_usd"),
                0
            )
        )

        print(
            "STARTUP RECONCILE recovered live entry "
            f"{position.get('symbol', address)} "
            f"balance={balance:.4f}"
        )


async def reconcile_live_entry_if_present(
    event,
    reason
):

    position = position_engine.live_execution_position_for_event(
        event
    )

    if not position or position.get("live_execution_entry_submitted"):
        return None

    live_balance, _balance_error = await definitive_live_token_balance(
        event.get("address", "")
    )

    if live_balance is None:
        # API failure — caller should treat as retryable, not permanent skip
        return {"reconcile_api_failed": True}

    if live_balance <= 0:
        return None

    position_engine.mark_live_execution_reconciled_entry(
        event,
        reason,
        live_balance=live_balance,
        order_id=position.get(
            "live_execution_entry_order_id",
            ""
        ),
        order_value_usd=safe_float(
            position.get("live_execution_entry_notional_usd"),
            position.get("entry_notional_usd")
        )
    )

    return reconciled_live_entry_result(
        event,
        position,
        live_balance,
        reason
    )


def failed_live_retry_events():

    state = position_engine.load_state()
    events = []

    for position in state.get("open", {}).values():
        if position.get("live_execution_retry_disabled"):
            continue

        orders = position.get("live_execution_orders", []) or []

        if not position.get("live_execution_entry_submitted"):
            has_failed_buy = any(
                order.get("side") == "buy"
                and not order.get("submitted")
                and not order.get("dry_run")
                for order in orders
            )

            if not has_failed_buy:
                continue

            entry_event = None

            for item in position.get("events", []) or []:
                if item.get("type") == "entry":
                    entry_event = item
                    break

            if not entry_event:
                continue

            events.append({
                "type": "entry",
                "timestamp": entry_event.get(
                    "timestamp",
                    position.get("entry_at")
                ),
                "address": position.get("address"),
                "chain": position.get("chain", "solana"),
                "symbol": position.get("symbol"),
                "name": position.get("name", ""),
                "pair_address": position.get("pair_address"),
                "status": "open",
                "reason": "position entry",
                "last_price": entry_event.get(
                    "price",
                    position.get("entry_price")
                ),
                "entry_price": position.get("entry_price"),
                "entry_notional_usd": position.get(
                    "entry_notional_usd",
                    0
                ),
                "entry_size_sol": position.get("entry_size_sol", 0),
                "entry_size_tokens": position.get("entry_size_tokens", 0)
            })
            continue

        remaining_tokens = safe_float(
            position.get("live_execution_remaining_tokens_estimated"),
            0
        )

        if remaining_tokens <= 0:
            continue

        submitted_keys = {
            order.get("event_key")
            for order in orders
            if order.get("submitted")
        }
        queued_keys = set()

        for order in orders:
            event_key = order.get("event_key", "")

            if (
                not event_key
                or event_key in submitted_keys
                or event_key in queued_keys
                or order.get("side") != "sell"
                or order.get("submitted")
                or order.get("dry_run")
            ):
                continue

            event_type = order.get("event_type", "")

            if event_type not in (
                "scale_out",
                "live_scale_out"
            ):
                continue

            parts = event_key.split("|")
            timestamp = (
                parts[2]
                if len(parts) > 2
                else position.get("last_update_at")
            )
            reason = (
                parts[3]
                if len(parts) > 3
                else order.get("reason", "")
            )
            size_pct = (
                safe_float(parts[4], 0)
                if len(parts) > 4
                else 0
            )
            proceeds_usd = (
                safe_float(parts[5], 0)
                if len(parts) > 5
                else 0
            )
            sell_tokens = safe_float(
                order.get("qty"),
                0
            )
            last_price = safe_float(
                position.get("last_price"),
                position.get("entry_price")
            )

            if sell_tokens <= 0 and proceeds_usd > 0:
                sell_tokens = proceeds_usd / max(
                    last_price,
                    1e-18
                )

            if sell_tokens <= 0:
                continue

            queued_keys.add(event_key)
            events.append({
                "type": event_type,
                "timestamp": timestamp,
                "address": position.get("address"),
                "chain": position.get("chain", "solana"),
                "symbol": position.get("symbol"),
                "name": position.get("name", ""),
                "pair_address": position.get("pair_address"),
                "status": "open",
                "reason": reason,
                "last_price": last_price,
                "entry_price": position.get("entry_price"),
                "entry_notional_usd": position.get(
                    "entry_notional_usd",
                    0
                ),
                "proceeds_usd": proceeds_usd,
                "proceeds_sol": 0,
                "size_pct": size_pct,
                "live_execution_sell_tokens": sell_tokens,
                "live_execution_remaining_tokens_estimated": (
                    remaining_tokens
                )
            })

    for position in state.get("closed", []) or []:
        if position.get("live_execution_retry_disabled"):
            continue

        if not position.get("live_execution_entry_submitted"):
            continue

        if position.get("live_execution_closed"):
            continue

        remaining_tokens = safe_float(
            position.get("live_execution_remaining_tokens_estimated"),
            0
        )

        if remaining_tokens <= 0:
            continue

        close_event = None

        for item in reversed(position.get("events", []) or []):
            if item.get("type") == "close":
                close_event = item
                break

        if not close_event:
            continue

        events.append({
            "type": "close",
            "timestamp": close_event.get(
                "timestamp",
                position.get("exit_at")
            ),
            "address": position.get("address"),
            "chain": position.get("chain", "solana"),
            "symbol": position.get("symbol"),
            "name": position.get("name", ""),
            "pair_address": position.get("pair_address"),
            "status": "closed",
            "reason": close_event.get(
                "reason",
                position.get("close_reason", "")
            ),
            "last_price": close_event.get(
                "price",
                position.get("exit_price")
            ),
            "entry_price": position.get("entry_price"),
            "entry_notional_usd": position.get(
                "entry_notional_usd",
                0
            ),
            "proceeds_usd": close_event.get("proceeds_usd", 0),
            "proceeds_sol": close_event.get("proceeds_sol", 0),
            "size_pct": close_event.get("size_pct", 0),
            "live_execution_sell_tokens": remaining_tokens,
            "live_execution_remaining_tokens_estimated": remaining_tokens
        })

    return events


async def live_execution_retry_watcher():

    while True:
        try:
            for event in failed_live_retry_events():
                schedule_live_execution_retry(event)
        except Exception as exc:
            print(
                "LIVE EXECUTION RETRY watcher error: "
                f"{exc}"
            )

        await asyncio.sleep(5)


# Candidate intelligence enrichment — exactly two external skills, both
# data-only, never fired on ordinary scans. Per-skill trigger scope
# (GMGN_ENRICH_SCOPE / OPENTWITTER_ENRICH_SCOPE):
#   "eligible" -> when a fresh alert-eligible candidate row is recorded
#                 (one per token/24h)
#   "alerted"  -> only when the candidate actually alerts (ignition delivery
#                 here; lattice ENTRY SIGNALs fire the same hook from
#                 discovery/live_runner)
# Background tasks so the scan loop never waits; strong refs held until done.
_intel_enrich_tasks = set()


def schedule_candidate_intel(token_address, stage):
    if not token_address:
        return

    async def _enrich():
        from config import GMGN_ENRICH_SCOPE, OPENTWITTER_ENRICH_SCOPE

        if GMGN_ENRICH_SCOPE == stage:
            try:
                from sources.gmgn import gmgn_client

                if gmgn_client.enabled():
                    features = await gmgn_client.candidate_features(
                        token_address
                    )

                    if features:
                        await scanner_storage.update_candidate_gmgn(
                            token_address,
                            features
                        )
                        print(
                            "Smart-money intel attached "
                            f"{token_address[:8]}... "
                            f"count={features.get('smart_count')} "
                            f"share={features.get('smart_share_pct'):.2f}% "
                            f"usd=${features.get('smart_usd'):.0f}"
                        )
            except Exception as e:
                print(f"GMGN smart-money intel error: {e}")

        if OPENTWITTER_ENRICH_SCOPE == stage:
            try:
                from sources.opentwitter import opentwitter_client

                if opentwitter_client.enabled():
                    features = await opentwitter_client.ca_mention_features(
                        token_address
                    )

                    if features:
                        await scanner_storage.update_candidate_twitter(
                            token_address,
                            features
                        )
                        print(
                            "Twitter intel attached "
                            f"{token_address[:8]}... "
                            f"mentions={features.get('mentions')} "
                            f"authors={features.get('authors')} "
                            f"top_followers={features.get('top_followers')}"
                        )
            except Exception as e:
                print(f"OpenTwitter intel error: {e}")

    task = asyncio.create_task(_enrich())
    _intel_enrich_tasks.add(task)
    task.add_done_callback(_intel_enrich_tasks.discard)


async def telemetry_prune_loop():
    """Archive/prune old telemetry rows at startup and recurring intervals.
    The blocking SQLite work runs in an executor so the event loop is never
    stalled; under WAL the per-batch locks are short."""

    while True:
        try:
            if SCANNER_TELEMETRY_ARCHIVE_ENABLED:
                archived = await asyncio.get_event_loop().run_in_executor(
                    None,
                    scanner_storage.archive_telemetry,
                    SCANNER_TELEMETRY_RETENTION_BY_TABLE,
                    SCANNER_TELEMETRY_ARCHIVE_DATABASE or None,
                )
                if any(
                    stats.get("deleted", 0)
                    for stats in archived.values()
                ):
                    print(
                        "Telemetry archive: "
                        + ", ".join(
                            f"{table} +{stats.get('archived', 0)} "
                            f"archived, -{stats.get('deleted', 0)} hot"
                            for table, stats in archived.items()
                        )
                    )
            else:
                deleted = await asyncio.get_event_loop().run_in_executor(
                    None,
                    scanner_storage.prune_telemetry,
                    SCANNER_TELEMETRY_RETENTION_BY_TABLE,
                )
                if any(deleted.values()):
                    print(
                        "Telemetry prune: "
                        + ", ".join(
                            f"{k} -{v}" for k, v in deleted.items()
                        )
                    )
        except Exception as exc:
            print(f"Telemetry archive/prune error: {exc}")

        # Two-stage confirmation (shadow): evaluate candidate events whose
        # 15-min window has elapsed while their snapshots are still hot.
        try:
            evaluated = await asyncio.get_event_loop().run_in_executor(
                None,
                scanner_storage.evaluate_due_confirmations,
            )
            if evaluated:
                print(
                    f"Candidate confirmations evaluated: {evaluated}"
                )
        except Exception as exc:
            print(f"Candidate confirmation error: {exc}")

        await asyncio.sleep(
            max(SCANNER_TELEMETRY_PRUNE_INTERVAL_SECONDS, 300)
        )


async def _confirm_entry_fill_background(
    event,
    order_id
):
    try:
        detail = await live_execution.definitive_wait_for_terminal_order(
            order_id,
            DEFINITIVE_ENTRY_CONFIRM_FILL_SECONDS
        )

        if not detail:
            return

        status = live_execution.definitive_order_status(detail)

        if status == "ORDER_STATUS_FILLED":
            fill_amounts = live_execution.definitive_order_filled_amounts(
                detail
            )
            filled_target = safe_float(fill_amounts.get("target"), 0)

            if filled_target > 0:
                position_engine.update_live_execution_entry_fill(
                    event,
                    fill_amounts
                )
                print(
                    "LIVE ENTRY CONFIRMED "
                    f"{event.get('symbol', '')} "
                    f"order={order_id} "
                    f"tokens={filled_target:.4f}"
                )
            else:
                # Silent fill: Definitive reports FILLED but zero filled
                # amounts. Do not arm a phantom position — reconcile first.
                await _handle_silent_entry_fill(event, order_id)
        elif status in (
            "ORDER_STATUS_CANCELLED",
            "ORDER_STATUS_REJECTED",
            "ORDER_STATUS_TERMINATED"
        ):
            reason = (
                live_execution.definitive_order_close_reason(detail)
                or status
            )
            print(
                "LIVE ENTRY FILL FAILED "
                f"{event.get('symbol', '')} "
                f"order={order_id} "
                f"reason={reason}"
            )
    except Exception as exc:
        print(
            "LIVE ENTRY CONFIRM ERROR "
            f"{event.get('symbol', '')} "
            f"order={order_id} "
            f"error={exc}"
        )


async def _handle_silent_entry_fill(
    event,
    order_id
):
    """Definitive reported the buy ORDER_STATUS_FILLED but with zero
    filled amounts. Reconcile against /positions: if a token balance is
    actually present the order detail merely under-reported, so arm the
    position with the real balance. If the balance is confirmed zero, no
    tokens were acquired — un-arm the phantom position so doomed exit
    sells are never attempted, and alert. If the balance check fails,
    leave the exit-side reconciliation as backstop and alert."""

    symbol = event.get("symbol", "")
    address = event.get("address", "")

    live_balance, balance_error = await definitive_live_token_balance(
        address
    )

    if live_balance and live_balance > 0:
        position_engine.mark_live_execution_reconciled_entry(
            event,
            "silent_fill_reconciled_from_positions",
            live_balance=live_balance,
            order_id=order_id,
            order_value_usd=safe_float(
                event.get("entry_notional_usd"),
                0
            )
        )
        print(
            "LIVE ENTRY SILENT-FILL RECONCILED "
            f"{symbol} order={order_id} "
            f"balance={live_balance:.4f}"
        )
        return

    if live_balance is None:
        # Could not confirm the balance (API error). Do not un-arm — the
        # exit-side /positions reconciliation remains the backstop — but
        # surface it so it is not silent.
        print(
            "LIVE ENTRY SILENT FILL UNVERIFIED "
            f"{symbol} order={order_id} "
            f"error={balance_error}"
        )
        await _alert_silent_entry_fill(
            event,
            order_id,
            "FILLED but 0 tokens; balance check failed "
            f"({balance_error}). Verify manually."
        )
        return

    # Confirmed zero balance — no tokens acquired. Un-arm the phantom.
    position_engine.mark_live_execution_entry_silent_fill(
        event,
        "silent_fill_zero_amount"
    )
    print(
        "LIVE ENTRY SILENT FILL "
        f"{symbol} order={order_id} "
        "FILLED status but 0 tokens and no live balance; entry un-armed"
    )
    await _alert_silent_entry_fill(
        event,
        order_id,
        "Definitive reported FILLED but 0 tokens and /positions shows no "
        "balance — entry un-armed, no live position. Verify manually."
    )


async def _alert_silent_entry_fill(
    event,
    order_id,
    message
):

    try:
        alert_event = dict(event)
        alert_event["live_execution_provider"] = "definitive"
        alert_event["live_execution_submitted"] = False
        alert_event["live_execution_skipped"] = True
        alert_event["live_execution_reason"] = "silent_fill"
        alert_event["live_execution_error"] = message
        alert_event["live_execution_order_id"] = order_id
        await telegram.send_live_execution_event(alert_event)
    except Exception as exc:
        print(
            "LIVE ENTRY SILENT FILL alert error "
            f"{event.get('symbol', '')} {exc}"
        )


def apply_alert_window_entry(
    metrics,
    ignition_score,
    ignition_details,
    memory,
    now
):
    """Catch explosive runners that alert but get dropped at entry because
    the per-scan route/score flickers to "none".

    Part A: cache the most recent VALID alert per token.
    Part B: when the current scan does NOT qualify, fall back to that cached
    alert's route+score for the entry decision — gated by anti-chase guards.
    Shadow mode logs would-enters and changes nothing. Returns the (possibly
    bumped) ignition_score; mutates ignition_details only on a live override.
    """

    if not ALERT_WINDOW_ENTRY_ENABLED:
        return ignition_score

    route = str(ignition_details.get("alert_route", "none") or "none")
    eligible = bool(ignition_details.get("alert_eligible", False))
    current_qualifies = eligible and route != "none"

    # Part A — cache a valid, entry-eligible alert.
    if (
        current_qualifies
        and route in ALERT_WINDOW_ROUTES
        and ignition_score >= ALERT_WINDOW_MIN_SCORE
    ):
        memory["last_valid_alert"] = {
            "at": now,
            "route": route,
            "score": safe_float(ignition_score, 0),
            "price": safe_float(metrics.price, 0),
            "fdv": safe_float(metrics.fdv, 0),
        }
        return ignition_score

    # Part B — only a fallback when the current scan does NOT qualify.
    if current_qualifies:
        return ignition_score

    cached = memory.get("last_valid_alert")
    if not cached:
        return ignition_score

    age = now - safe_float(cached.get("at"), 0)
    alert_price = safe_float(cached.get("price"), 0)
    price = safe_float(metrics.price, 0)

    # Guards (any failure -> no override).
    if (
        age > ALERT_WINDOW_ENTRY_SECONDS
        or alert_price <= 0
        or price <= 0
        or price > alert_price * (1 + ALERT_WINDOW_MAX_RUN_PCT)
        or price < alert_price * (1 - ALERT_WINDOW_MAX_DROP_PCT)
        or str(metrics.address)
        in position_engine.load_state().get("open", {})
    ):
        return ignition_score

    run = price / alert_price

    if ALERT_WINDOW_ENTRY_SHADOW_MODE:
        ignition_details["alert_window_shadow_would_enter"] = True
        ignition_details["alert_window_cached_route"] = cached.get("route")
        ignition_details["alert_window_age_seconds"] = age
        print(
            "ALERT-WINDOW WOULD ENTER (shadow) "
            f"{getattr(metrics, 'symbol', '')} "
            f"route={cached.get('route')} "
            f"score={safe_float(cached.get('score'), 0):.0f} "
            f"age={age:.0f}s run={run:.2f}x"
        )
        return ignition_score

    # Live override: enter as the cached alert's route/score.
    ignition_details["alert_route"] = cached.get("route")
    ignition_details["alert_window_entry"] = True
    ignition_details["alert_window_age_seconds"] = age
    print(
        "ALERT-WINDOW ENTRY "
        f"{getattr(metrics, 'symbol', '')} "
        f"route={cached.get('route')} "
        f"score={safe_float(cached.get('score'), 0):.0f} "
        f"age={age:.0f}s run={run:.2f}x"
    )
    return max(ignition_score, safe_float(cached.get("score"), 0))


async def maybe_execute_live_trade_event(
    event
):

    if not event:
        return event

    from config import SCANNER_LIVE_EXECUTION_ENABLED
    if not SCANNER_LIVE_EXECUTION_ENABLED:
        # Scanner is paper/alerts-only; discovery/live_runner is the sole live
        # trader (one bot per wallet). Set SCANNER_LIVE_EXECUTION_ENABLED=true
        # to let the scanner trade live too.
        return event

    if not event.get("contra_asset_usd"):
        event["contra_asset_usd"] = (
            live_execution.definitive_contra_asset_price_usd(
                event,
                event.get("chain", "solana")
            )
        )

    if position_engine.live_execution_event_seen(event):
        return attach_live_execution_details(
            event,
            {
                "enabled": True,
                "provider": "definitive",
                "skipped": True,
                "reason": "duplicate_live_execution_event"
            }
        )

    position = position_engine.live_execution_position_for_event(
        event
    )

    if position:
        event[
            "live_execution_remaining_tokens_estimated"
        ] = position.get(
            "live_execution_remaining_tokens_estimated",
            0
        )

    has_live_position = (
        event.get("type") == "entry"
        or position_engine.live_execution_position_has_entry(event)
    )

    if (
        event.get("type") != "entry"
        and not has_live_position
    ):
        reconciled_result = await reconcile_live_entry_if_present(
            event,
            "live_entry_present_on_definitive_before_exit"
        )

        if reconciled_result and reconciled_result.get("reconcile_api_failed"):
            return attach_live_execution_details(
                event,
                {
                    "enabled": True,
                    "provider": "definitive",
                    "submitted": False,
                    "skipped": True,
                    "reason": "reconcile_api_failed_before_exit",
                    "side": "sell"
                }
            )

        if reconciled_result:
            attach_live_execution_details(
                event,
                reconciled_result
            )
            has_live_position = True

    async with LIVE_ORDER_LOCK:
        # Re-check inside the lock — a concurrent call may have submitted
        # between our outer check and acquiring the lock (TOCTOU).
        if position_engine.live_execution_event_seen(event):
            return attach_live_execution_details(
                event,
                {
                    "enabled": True,
                    "provider": "definitive",
                    "skipped": True,
                    "reason": "duplicate_live_execution_event"
                }
            )

        try:
            result = await live_execution.execute_position_event(
                event,
                open_summary=position_engine.live_execution_open_summary(),
                has_live_position=has_live_position
            )
        except Exception as exc:
            result = {
                "enabled": True,
                "provider": "definitive",
                "submitted": False,
                "skipped": True,
                "side": (
                    "buy"
                    if event.get("type") == "entry"
                    else "sell"
                ),
                "reason": "definitive_execution_error",
                "submit_error": str(exc)
            }

        attach_live_execution_details(
            event,
            result
        )

        if (
            event.get("type") == "entry"
            and not result.get("submitted")
        ):
            reconciled_result = await reconcile_live_entry_if_present(
                event,
                "live_entry_present_on_definitive_after_submit"
            )

            if reconciled_result and not reconciled_result.get("reconcile_api_failed"):
                result = reconciled_result
                attach_live_execution_details(
                    event,
                    result
                )

        retryable_live_failure = live_execution_retryable_failure(
            event,
            result
        )

        if (
            result.get("enabled")
            and not result.get("dry_run")
            and not retryable_live_failure
            and not result.get("already_recorded")
        ):
            position_engine.record_live_execution_result(
                event,
                result
            )
            await live_execution.manage_flash_onchain_stop(
                position_engine,
                event,
                result
            )
            await live_execution.manage_flash_resting_exits(
                position_engine,
                event,
                result
            )

        if retryable_live_failure:
            event["live_execution_retrying"] = True
            if not result.get("already_recorded"):
                position_engine.record_live_execution_result(
                    event,
                    result
                )

    if retryable_live_failure:
        schedule_live_execution_retry(event)
        await notify_live_execution_event(event)

    if (
        event.get("type") == "entry"
        and result.get("submitted")
        and result.get("order_id")
    ):
        asyncio.create_task(
            _confirm_entry_fill_background(
                event,
                result["order_id"]
            )
        )

    if (
        result.get("enabled")
        and (
            result.get("submitted")
            or (
                not result.get("dry_run")
                and result.get("skipped")
            )
        )
    ):
        print(
            "LIVE EXECUTION "
            f"{event.get('type', '')} "
            f"{event.get('symbol', '')} "
            f"submitted={result.get('submitted', False)} "
            f"skipped={result.get('skipped', False)} "
            f"reason={result.get('reason', '')}"
        )

        if not retryable_live_failure:
            await notify_live_execution_event(event)

    return event


async def maybe_execute_live_trade_events(
    events
):

    executed = []

    for event in events or []:
        executed.append(
            await maybe_execute_live_trade_event(event)
        )

    return executed


async def process_open_position_pair(
    address,
    pair
):

    state = position_engine.load_state()
    position = state.get("open", {}).get(address)

    if not position:
        return []

    # Sync the live token estimate from on-chain resting-exit fills (gated;
    # dormant unless the resting take-profit ladder is armed for this position).
    await live_execution.reconcile_flash_resting_exits(
        position_engine,
        position,
        address
    )

    metrics = build_open_position_metrics(
        address,
        position,
        pair
    )

    if not metrics:
        return []

    now = time.time()
    ignition_details = open_position_ignition_details(
        metrics,
        position
    )

    # Fetch the executable exit quote first and use it to sanity-check
    # the spot price BEFORE it feeds RSI/VWAP/stops/snapshot. A spurious
    # near-zero on-chain tick would otherwise corrupt all of them.
    await update_exit_quote(
        position,
        metrics,
        ignition_details
    )
    apply_quote_price_sanity(
        position,
        metrics,
        ignition_details
    )

    await update_open_position_candles(
        metrics,
        ignition_details,
        now
    )
    await update_anchored_vwap(
        metrics,
        ignition_details,
        now,
        "open_position_monitor",
        provider_refresh_allowed=True,
        anchor_timestamp=safe_float(
            position.get("entry_at"),
            0
        ),
        anchor_name="entry"
    )
    pressure = calculate_pressure(
        metrics,
        ignition_details
    )
    ignition_details["pressure"] = pressure

    ignition_score = safe_float(
        position.get("entry_score"),
        0
    )
    signal_snapshot = build_signal_snapshot(
        metrics,
        ignition_score,
        ignition_details,
        pressure,
        now
    )
    memory = TOKEN_MEMORY[address]
    recent_snapshots = append_signal_snapshot(
        memory,
        signal_snapshot
    )

    try:
        await scanner_storage.save_signal_snapshot(
            signal_snapshot
        )
    except Exception as e:
        print(
            f"Open position snapshot save error: {e}"
        )

    history = memory["history"]
    history.append({
        "timestamp": now,
        "volume_5m": metrics.volume_5m,
        "liquidity": metrics.liquidity,
        "buys": metrics.buys_5m,
        "sells": metrics.sells_5m,
        "txns": metrics.buys_5m + metrics.sells_5m,
        "price": metrics.price,
        "raw_base_reserve": metrics.raw_base_reserve,
        "raw_quote_reserve": metrics.raw_quote_reserve,
    })

    trim_token_history(history)

    live_initial_event = position_engine.live_initial_take_profit_event(
        position,
        metrics,
        pressure,
        now,
        ignition_details=ignition_details
    )
    live_initial_events = []

    if live_initial_event:
        live_initial_events = [
            await maybe_execute_live_trade_event(live_initial_event)
        ]

    position_events = position_engine.handle_scan(
        metrics,
        ignition_score,
        ignition_details,
        now,
        pressure=pressure,
        recent_snapshots=recent_snapshots
    )

    position_events = await maybe_execute_live_trade_events(
        position_events
    )

    return live_initial_events + position_events


async def position_monitor_loop(
    client
):

    while True:

        try:
            open_refs = position_engine.open_position_refs()
            open_addresses = [
                address
                for address, _chain in open_refs
            ]

            if not open_addresses:
                await asyncio.sleep(
                    POSITION_OPEN_POSITION_SCAN_INTERVAL_SECONDS
                )
                continue

            await refresh_position_sol_usd()

            pair_map = {}
            open_by_chain = {}

            for address, chain in open_refs:
                open_by_chain.setdefault(
                    chain or "solana",
                    []
                ).append(address)

            for chain, chain_addresses in open_by_chain.items():
                pair_map.update(
                    await client.fetch_token_pairs_batch(
                        chain_addresses,
                        allow_fallback=True,
                        force_refresh=True,
                        chain_id=chain
                    )
                )

            # Keep POSITION_WATCH_ACCOUNTS in sync so the gRPC listener
            # subscribes to the right pool accounts for price streaming.
            POSITION_WATCH_ACCOUNTS.clear()
            for address in open_addresses:
                best = best_live_pair(
                    pair_map.get(address, []),
                    token_address=address
                )
                if best:
                    pair_addr = best.get("pairAddress")
                    if pair_addr:
                        POSITION_WATCH_ACCOUNTS[pair_addr] = address

            tasks = []
            now = time.time()
            sol_usd = sol_usd_price_feed.current_price()

            for address in open_addresses:
                pair = best_live_pair(
                    pair_map.get(address, []),
                    token_address=address
                )

                # If gRPC has a fresher price for this position, override
                # the DexScreener cached priceUsd with the on-chain price.
                if pair and sol_usd:
                    grpc_data = GRPC_POSITION_PRICES.get(address)
                    if (
                        grpc_data
                        and now - grpc_data.get("updated_at", 0) < 60
                    ):
                        price_sol = grpc_data.get("price_sol", 0)
                        if price_sol > 0:
                            grpc_price_usd = price_sol * sol_usd
                            dex_price = safe_float(pair.get("priceUsd"), 0)
                            # Reject a gRPC per-swap price that diverges wildly
                            # from DexScreener — a single dust/routed/MEV swap
                            # yields a glitch ratio that false-fires stops.
                            if (
                                dex_price <= 0
                                or abs(grpc_price_usd - dex_price) / dex_price
                                <= GRPC_PRICE_MAX_DEX_DEVIATION_PCT
                            ):
                                pair = dict(pair)
                                pair["priceUsd"] = str(grpc_price_usd)
                            else:
                                print(
                                    "GRPC PRICE REJECTED "
                                    f"{address[:10]} "
                                    f"grpc=${grpc_price_usd:.8f} "
                                    f"dex=${dex_price:.8f} "
                                    f"dev={abs(grpc_price_usd - dex_price) / dex_price * 100:.0f}%"
                                )

                if pair:
                    tasks.append(
                        process_open_position_pair(
                            address,
                            pair
                        )
                    )
                    continue

                risk_event = position_engine.handle_missing_pair(
                    address,
                    now
                )

                if risk_event:
                    risk_event = await maybe_execute_live_trade_event(
                        risk_event
                    )
                    await telegram.send_position_event(
                        risk_event
                    )

            if tasks:
                results = await asyncio.gather(
                    *tasks,
                    return_exceptions=True
                )

                for result in results:
                    if isinstance(result, Exception):
                        print(
                            f"Position refresh error: {result}"
                        )
                        continue

                    for position_event in result or []:
                        await telegram.send_position_event(
                            position_event
                        )

            for watchdog_event in position_engine.stale_live_stop_events(now):
                watchdog_event = await maybe_execute_live_trade_event(
                    watchdog_event
                )
                await telegram.send_position_event(watchdog_event)

        except Exception as e:
            print(
                f"Position monitor error: {e}"
            )

        await asyncio.sleep(
            POSITION_OPEN_POSITION_SCAN_INTERVAL_SECONDS
        )


async def monitor_candidates(
    client
):

    rotation_index = 0

    while True:

        try:

            candidate_addresses = list(
                TRACKED_CANDIDATES.keys()
            )

            if not candidate_addresses:

                print(
                    "No candidates currently tracked..."
                )

                await asyncio.sleep(10)

                continue

            priority_candidates = [
                address
                for address in candidate_addresses
                if TOKEN_MEMORY[address]["tier"] == 1
            ]
            priority_candidates.sort(
                key=lambda address: TOKEN_MEMORY[address][
                    "last_scan"
                ]
            )

            priority_batch = priority_candidates[
                :ROTATION_BATCH_SIZE
            ]

            batch = candidate_addresses[
                rotation_index:
                rotation_index
                + ROTATION_BATCH_SIZE
            ]

            batch = list(
                dict.fromkeys(
                    priority_batch
                    + batch
                )
            )

            rotation_index += (
                ROTATION_BATCH_SIZE
            )

            if (
                rotation_index
                >= len(candidate_addresses)
            ):

                rotation_index = 0

            due_addresses = []

            now = time.time()

            for address in batch:

                if is_excluded_contract_address(
                    address
                ):
                    continue

                memory = TOKEN_MEMORY[
                    address
                ]

                tier = memory["tier"]

                interval = (
                    TIER_INTERVALS.get(
                        tier,
                        120
                    )
                )

                last_scan = memory[
                    "last_scan"
                ]

                if (
                    now - last_scan
                    < interval
                ):
                    continue

                memory[
                    "last_scan"
                ] = now

                due_addresses.append(
                    address
                )

            if due_addresses:

                pair_map = {}
                due_by_chain = {}

                for address in due_addresses:
                    chain = (
                        TRACKED_CANDIDATES
                        .get(address, {})
                        .get("chain", "solana")
                    )
                    due_by_chain.setdefault(
                        chain,
                        []
                    ).append(address)

                if due_by_chain:
                    print(
                        "Scanning due candidates by chain: "
                        + ", ".join(
                            f"{chain}={len(chain_addresses)}"
                            for chain, chain_addresses in sorted(
                                due_by_chain.items()
                            )
                        )
                    )

                for chain, chain_addresses in due_by_chain.items():
                    pair_map.update(
                        await client.fetch_token_pairs_batch(
                            chain_addresses,
                            allow_fallback=True,
                            chain_id=chain
                        )
                    )

                tasks = [
                    process_token(
                        client,
                        address,
                        pair_map.get(address, [])
                    )
                    for address in due_addresses
                    if pair_map.get(address)
                ]

                if not tasks:
                    await asyncio.sleep(5)
                    continue

                await asyncio.gather(
                    *tasks,
                    return_exceptions=True
                )

        except Exception as e:

            print(
                f"Monitor loop error: {e}"
            )

        await asyncio.sleep(5)


async def _gmgn_scan_backfill(token_address, chain, market, candidate_metadata):
    """Targeted scan-time GMGN backfill (default OFF, GMGN_SCAN_BACKFILL_ENABLED).
    When a TRACKED candidate would be discarded by is_scannable_market because
    DexScreener FDV/liquidity are absent or wrong (common for pre-migration
    tokens), fetch real GMGN token-info (cached 900s + semaphore-bounded in the
    client) and patch market['fdv'] / market['liquidity'] in place so the caller
    can re-check the gate. Returns the features (for volume/price_change
    backfill) or None. Only fires for already-tracked candidates whose data is
    missing — a small subset — never on the untracked discovery firehose."""
    import config
    if not getattr(config, "GMGN_SCAN_BACKFILL_ENABLED", False):
        return None
    if not candidate_metadata:
        return None
    try:
        from sources.gmgn import gmgn_client
        if not gmgn_client.enabled():
            return None
        gchain = ("sol" if str(chain).lower() in ("solana", "sol")
                  else str(chain).lower())
        feats = await asyncio.wait_for(
            gmgn_client.token_info_features(token_address, chain=gchain), 8.0)
    except Exception:
        return None
    if not feats:
        return None
    if (not market.get("fdv")) and feats.get("gmgn_fdv_usd"):
        market["fdv"] = feats["gmgn_fdv_usd"]
    if (not market.get("liquidity")) and feats.get("gmgn_liquidity_usd"):
        market["liquidity"] = feats["gmgn_liquidity_usd"]
    return feats


async def process_token(
    client,
    token_address,
    pairs=None
):

    try:

        if is_excluded_contract_address(
            token_address
        ):
            return

        candidate_metadata = TRACKED_CANDIDATES.get(
            token_address,
            {}
        )
        chain = candidate_metadata.get(
            "chain",
            "solana"
        )

        if pairs is None:
            pairs = (
                await client
                .fetch_token_pairs(
                    token_address,
                    chain_id=chain
                )
            )

        if not pairs:
            return

        pair = pairs[0]
        chain = pair.get("chainId", chain)

        market = build_market_context(
            pair,
            lifecycle_hint=candidate_metadata.get(
                "lifecycle_hint"
            )
        )

        gmgn_bf = None
        if not is_scannable_market(market):
            # Targeted GMGN backfill of fdv/liquidity for tracked candidates
            # whose DexScreener data is missing; re-check the gate after.
            gmgn_bf = await _gmgn_scan_backfill(
                token_address, chain, market, candidate_metadata
            )
            if not gmgn_bf or not is_scannable_market(market):
                return

        liquidity = market["liquidity"]
        fdv = market["fdv"]

        price = float(
            pair.get(
                "priceUsd",
                0
            )
        )

        volume_5m = pair.get(
            "volume",
            {}
        ).get(
            "m5",
            0
        )

        volume_1h = pair.get(
            "volume",
            {}
        ).get(
            "h1",
            0
        )

        txns = pair.get(
            "txns",
            {}
        ).get(
            "m5",
            {}
        )

        buys = txns.get(
            "buys",
            0
        )

        sells = txns.get(
            "sells",
            0
        )

        txns_1h = pair.get(
            "txns",
            {}
        ).get(
            "h1",
            {}
        )

        buys_1h = txns_1h.get(
            "buys",
            0
        )

        sells_1h = txns_1h.get(
            "sells",
            0
        )

        price_change = pair.get(
            "priceChange",
            {}
        )

        price_change_5m = float(
            price_change.get(
                "m5",
                0
            )
            or 0
        )

        price_change_1h = float(
            price_change.get(
                "h1",
                0
            )
            or 0
        )

        price_change_6h = float(
            price_change.get(
                "h6",
                0
            )
            or 0
        )

        price_change_24h = float(
            price_change.get(
                "h24",
                0
            )
            or 0
        )

        # Scan-time backfill (#4) also recovers tracked candidates that PASS the
        # scannable gate but have zero 1h volume (DexScreener data absent) — the
        # common scoring-stage attrition. Reuses any fetch from the gate above;
        # _gmgn_scan_backfill no-ops instantly when the flag is off or the token
        # is untracked, so this stays bounded to tracked, dataless tokens.
        if gmgn_bf is None and candidate_metadata and not volume_1h:
            gmgn_bf = await _gmgn_scan_backfill(
                token_address, chain, market, candidate_metadata
            )

        # Backfill zero/missing DexScreener fields from the GMGN token-info
        # fetched above (only set when the scan-time backfill actually ran).
        if gmgn_bf:
            if not volume_5m and gmgn_bf.get("gmgn_volume_5m"):
                volume_5m = gmgn_bf["gmgn_volume_5m"]
            if not volume_1h and gmgn_bf.get("gmgn_volume_1h"):
                volume_1h = gmgn_bf["gmgn_volume_1h"]
            if (not price_change_5m
                    and gmgn_bf.get("gmgn_price_change_5m") is not None):
                price_change_5m = gmgn_bf["gmgn_price_change_5m"]
            if (not price_change_1h
                    and gmgn_bf.get("gmgn_price_change_1h") is not None):
                price_change_1h = gmgn_bf["gmgn_price_change_1h"]

        total_txns = (
            buys + sells
        )

        pair_created_at = pair.get(
            "pairCreatedAt",
            0
        )

        base_token = pair.get(
            "baseToken",
            {}
        )

        token_mint = base_token.get(
            "address",
            token_address
        )

        if is_excluded_contract_address(
            token_mint
        ):
            return

        mint_age = await resolve_mint_age(
            client.session,
            token_mint,
            pair_created_at,
            chain=chain
        )

        if not passes_min_mint_age(
            mint_age,
            chain=chain
        ):
            return

        age_hours = mint_age[
            "age_hours"
        ]

        age_source = mint_age[
            "source"
        ]

        info = pair.get(
            "info",
            {}
        )

        websites = info.get(
            "websites",
            []
        )

        socials = info.get(
            "socials",
            []
        )

        banner = info.get(
            "header"
        )

        image_url = (
            info.get("imageUrl")
            or info.get("image")
            or info.get("icon")
        )

        description = info.get(
            "description"
        )

        twitter = None
        telegram_link = None
        website = None

        for site in websites:

            if site.get("url"):

                website = site[
                    "url"
                ]

        for social in socials:

            social_type = social.get(
                "type",
                ""
            ).lower()

            if social_type == "twitter":

                twitter = social.get(
                    "url"
                )

            elif social_type == "telegram":

                telegram_link = social.get(
                    "url"
                )

        memory = TOKEN_MEMORY[
            token_address
        ]

        hydrate_ignition_memory(
            pair[
                "baseToken"
            ][
                "address"
            ],
            memory
        )

        trade_volumes = summarize_trade_volumes(
            memory,
            price,
            time.time(),
            volume_5m,
            volume_1h,
            buys,
            sells,
            buys_1h,
            sells_1h
        )

        metrics = TokenMetrics(
            address=pair[
                "baseToken"
            ][
                "address"
            ],
            symbol=pair[
                "baseToken"
            ][
                "symbol"
            ],
            name=pair[
                "baseToken"
            ].get(
                "name",
                ""
            ),
            pair_address=pair[
                "pairAddress"
            ],
            liquidity=liquidity,
            fdv=fdv,
            price=price,
            volume_5m=volume_5m,
            volume_1h=volume_1h,
            buys_5m=buys,
            sells_5m=sells,
            buys_1h=buys_1h,
            sells_1h=sells_1h,
            price_change_5m=price_change_5m,
            price_change_1h=price_change_1h,
            price_change_6h=price_change_6h,
            price_change_24h=price_change_24h,
            age_hours=age_hours,
            buy_volume_5m=trade_volumes["buy_volume_5m"],
            sell_volume_5m=trade_volumes["sell_volume_5m"],
            buy_volume_1h=trade_volumes["buy_volume_1h"],
            sell_volume_1h=trade_volumes["sell_volume_1h"],
            buy_sell_volume_source_5m=trade_volumes["source_5m"],
            buy_sell_volume_source_1h=trade_volumes["source_1h"],
            age_source=age_source,
            chain=chain,
            source=pair.get(
                "dexId",
                "dexscreener"
            ),
            lifecycle=market["lifecycle"],
            raw_liquidity=market["raw_liquidity"],
            raw_base_reserve=market["raw_base_reserve"],
            raw_quote_reserve=market["raw_quote_reserve"],
            liquidity_source=market["liquidity_source"],
            migration_fdv=market.get("migration_fdv", 0),
            migration_distance_usd=market.get(
                "migration_distance_usd",
                0
            ),
            migration_distance_pct=market.get(
                "migration_distance_pct",
                0
            ),
            migration_fdv_source=market.get(
                "migration_fdv_source",
                ""
            )
        )
        token_metadata = {
            "name": pair.get(
                "baseToken",
                {}
            ).get("name", ""),
            "symbol": metrics.symbol,
            "description": description or "",
            "website": website or "",
            "twitter": twitter or "",
            "telegram": telegram_link or "",
            "banner": banner or "",
            "image_url": image_url or "",
            "pair_url": pair.get("url") or (
                "https://dexscreener.com/"
                f"{metrics.chain}/{metrics.pair_address}"
            )
        }
        safe = await safety.check_token(
            metrics.chain,
            metrics.address
        )

        if not safe:

            return

        if metrics.lifecycle == "migrated":
            update_migration_tracking(metrics, time.time())

        history = memory["history"]

        historical_volumes = [
            h["volume_5m"]
            for h in history[-10:]
        ]

        historical_txns = [
            h["txns"]
            for h in history[-10:]
        ]

        historical_prices = [
            h["price"]
            for h in history[-10:]
            if h["price"] > 0
        ]

        rolling_avg_volume = (
            sum(historical_volumes)
            / max(
                len(
                    historical_volumes
                ),
                1
            )
        )

        rolling_avg_txns = (
            sum(historical_txns)
            / max(
                len(
                    historical_txns
                ),
                1
            )
        )

        volatility = 0

        if (
            len(
                historical_prices
            ) >= 2
        ):

            volatility = (
                statistics.pstdev(
                    historical_prices
                )
            )

        memory[
            "rolling_avg_volume"
        ] = rolling_avg_volume

        memory[
            "rolling_avg_txns"
        ] = rolling_avg_txns

        memory[
            "rolling_avg_volatility"
        ] = volatility

        if (
            volume_5m
            < max(
                rolling_avg_volume
                * 1.2,
                2000
            )
            and
            total_txns
            < max(
                rolling_avg_txns
                * 1.2,
                15
            )
        ):

            memory[
                "low_activity_count"
            ] += 1

        else:

            memory[
                "low_activity_count"
            ] = 0

        if volatility < max(
            price * 0.015,
            0.000001
        ):

            memory[
                "low_volatility_count"
            ] += 1

        else:

            memory[
                "low_volatility_count"
            ] = 0

        if (
            memory[
                "low_activity_count"
            ] >= 3
            and
            memory[
                "low_volatility_count"
            ] >= 3
        ):

            memory[
                "quiet_period_detected"
            ] = True

        metadata_changed_fields = update_metadata_memory(
            memory,
            metadata_payload_from_pair_info(
                website,
                twitter,
                telegram_link,
                banner,
                image_url,
                description
            )
        )

        volume_ratio = 0

        if rolling_avg_volume > 0:

            volume_ratio = (
                volume_5m
                / rolling_avg_volume
            )

        buy_sell_ratio = (
            buys
            / max(sells, 1)
        )

        ignition_score, ignition_breakdown, ignition_details = (
            calculate_ignition_signal(
                metrics,
                memory
            )
        )
        ignition_score = apply_route_outcome_score(
            ignition_score,
            ignition_details,
            ignition_breakdown
        )

        pressure = calculate_pressure(
            metrics,
            ignition_details
        )
        ignition_details["pressure"] = pressure
        ignition_score = apply_cto_metadata_signal(
            metrics,
            memory,
            ignition_score,
            ignition_details,
            ignition_breakdown,
            pressure,
            time.time()
        )

        liquidity_lock = {
            "checked": False,
            "required": False,
            "locked": True,
            "locked_percent": None,
            "source": "not_checked",
            "reason": "not_checked"
        }
        liquidity_check_allowed = True
        liquidity_precheck_reason = None

        if (
            REQUIRE_LIQUIDITY_LOCK
            and metrics.lifecycle != "bonding_curve"
            and ignition_details.get(
                "alert_eligible",
                False
            )
            and uses_mobula_safety(metrics.chain)
        ):
            liquidity_precheck_reason = position_entry_precheck_reason(
                metrics,
                ignition_score,
                ignition_details,
                now=time.time(),
                recent_snapshots=memory.get(
                    "signal_snapshots",
                    []
                )
            )
            ignition_details[
                "mobula_entry_precheck_reason"
            ] = liquidity_precheck_reason or ""

            if liquidity_precheck_reason:
                liquidity_check_allowed = False
                ignition_details["alert_eligible"] = False
                ignition_details["alert_route"] = "none"
                ignition_details["reason"] = (
                    "mobula_entry_precheck_failed"
                )
                ignition_details["missing"] = list(
                    dict.fromkeys(
                        ignition_details.get("missing", [])
                        + [liquidity_precheck_reason]
                    )
                )
                ignition_breakdown.append(
                    "Mobula skipped until position entry "
                    f"precheck passes ({liquidity_precheck_reason})"
                )
                liquidity_lock = {
                    "checked": False,
                    "required": True,
                    "locked": False,
                    "locked_percent": None,
                    "source": "mobula_skipped_entry_precheck",
                    "reason": liquidity_precheck_reason
                }
                ignition_details[
                    "liquidity_lock_checked"
                ] = liquidity_lock["checked"]
                ignition_details[
                    "liquidity_lock_required"
                ] = liquidity_lock["required"]
                ignition_details[
                    "liquidity_lock_locked"
                ] = liquidity_lock["locked"]
                ignition_details[
                    "liquidity_lock_locked_percent"
                ] = liquidity_lock["locked_percent"]
                ignition_details[
                    "liquidity_lock_source"
                ] = liquidity_lock["source"]
                ignition_details[
                    "liquidity_lock_reason"
                ] = liquidity_lock["reason"]

        if (
            REQUIRE_LIQUIDITY_LOCK
            and metrics.lifecycle != "bonding_curve"
            and ignition_details.get(
                "alert_eligible",
                False
            )
            and liquidity_check_allowed
        ):
            liquidity_lock = await safety.check_liquidity_lock(
                metrics.chain,
                metrics.address,
                lifecycle=metrics.lifecycle,
                pair_address=metrics.pair_address
            )

            ignition_details[
                "liquidity_lock_checked"
            ] = liquidity_lock.get("checked", False)
            ignition_details[
                "liquidity_lock_required"
            ] = liquidity_lock.get("required", False)
            ignition_details[
                "liquidity_lock_locked"
            ] = liquidity_lock.get("locked", False)
            ignition_details[
                "liquidity_lock_locked_percent"
            ] = liquidity_lock.get("locked_percent")
            ignition_details[
                "liquidity_lock_source"
            ] = liquidity_lock.get("source", "")
            ignition_details[
                "liquidity_lock_reason"
            ] = liquidity_lock.get("reason", "")
            ignition_details[
                "liquidity_burned_percent"
            ] = liquidity_lock.get("burned_percent")
            ignition_details[
                "liquidity_unlocked_percent"
            ] = liquidity_lock.get("unlocked_percent")
            ignition_details[
                "liquidity_lock_pool_count"
            ] = liquidity_lock.get("pool_count")

            if not liquidity_lock.get("locked", False):
                print(
                    "Liquidity lock blocked alert for "
                    f"{metrics.symbol} "
                    f"({metrics.address})"
                )
                ignition_details["alert_eligible"] = False
                ignition_details["alert_route"] = "none"
                ignition_details["reason"] = "liquidity_not_locked"
                ignition_details["missing"] = list(
                    dict.fromkeys(
                        ignition_details.get("missing", [])
                        + ["liquidity_lock"]
                    )
                )
                ignition_breakdown.append(
                    "Liquidity lock check failed (alert blocked)"
                )

        pressure = calculate_pressure(
            metrics,
            ignition_details
        )
        ignition_details["pressure"] = pressure

        momentum = momentum_features(
            memory["signal_snapshots"],
            metrics
        )

        benchmark = build_market_benchmark(
            now=time.time(),
            exclude_address=metrics.address
        )
        trade_quality = build_trade_quality_label(
            momentum,
            benchmark
        )

        momentum.update(
            {
                "benchmark_token_count": benchmark.get(
                    "token_count",
                    0
                ),
                "benchmark_median_current_return": benchmark.get(
                    "median_current_return",
                    0
                ),
                "benchmark_median_price_acceleration": benchmark.get(
                    "median_price_acceleration",
                    0
                ),
                "benchmark_median_volume_expansion": benchmark.get(
                    "median_volume_expansion",
                    0
                ),
                "benchmark_median_liquidity_drain": benchmark.get(
                    "median_liquidity_drain",
                    0
                ),
                "benchmark_median_momentum_score": benchmark.get(
                    "median_momentum_score",
                    0
                ),
                "trade_quality_label": trade_quality.get(
                    "trade_quality_label",
                    "neutral"
                ),
                "trade_quality_score": trade_quality.get(
                    "trade_quality_score",
                    0
                ),
                "trade_quality_reason": trade_quality.get(
                    "trade_quality_reason",
                    "within_market_band"
                ),
                "relative_strength_pct": trade_quality.get(
                    "relative_strength_pct",
                    0
                )
            }
        )
        ignition_details["trade_quality_label"] = trade_quality.get(
            "trade_quality_label",
            "neutral"
        )
        ignition_details["trade_quality_score"] = trade_quality.get(
            "trade_quality_score",
            0
        )
        ignition_details["trade_quality_reason"] = trade_quality.get(
            "trade_quality_reason",
            "within_market_band"
        )
        ignition_details["relative_strength_pct"] = trade_quality.get(
            "relative_strength_pct",
            0
        )
        ignition_details["benchmark_token_count"] = benchmark.get(
            "token_count",
            0
        )

        if LOCAL_RSI_ENABLED:
            try:
                await scanner_storage.save_token_candle_observation(
                    {
                        "token_address": metrics.address,
                        "symbol": metrics.symbol,
                        "pair_address": metrics.pair_address,
                        "chain_name": metrics.chain,
                        "price": metrics.price,
                        "volume_5m": metrics.volume_5m,
                        "liquidity": metrics.liquidity,
                        "timestamp": time.time()
                    },
                    timeframe_seconds=LOCAL_RSI_TIMEFRAME_SECONDS
                )
            except Exception as e:
                print(
                    f"Local candle update error: {e}"
                )

        await update_anchored_vwap(
            metrics,
            ignition_details,
            time.time(),
            "candidate_scan",
            provider_refresh_allowed=bool(
                ignition_details.get(
                    "alert_eligible",
                    False
                )
            )
        )

        now = time.time()
        ignition_min_score = (
            HYPEREVM_IGNITION_SCORE
            if str(metrics.chain or "").lower() == "hyperevm"
            else IGNITION_ALERT_THRESHOLD
        )
        ignition_triggered = (
            ignition_score
            >= ignition_min_score
            and ignition_details.get(
                "alert_eligible",
                False
            )
        )
        entry_precheck_reason = None
        recall_override_reason = None

        if ignition_triggered:
            entry_precheck_reason = position_entry_precheck_reason(
                metrics,
                ignition_score,
                ignition_details,
                now,
                memory.get("signal_snapshots", [])
            )
            ignition_details[
                "position_entry_precheck_reason"
            ] = (
                entry_precheck_reason
                or "entry_ready"
            )

        record_scan_gate_attrition(
            metrics,
            pair,
            ignition_score,
            ignition_details,
            ignition_triggered,
            entry_precheck_reason=entry_precheck_reason,
            trade_volumes=trade_volumes
        )
        maybe_print_scan_gate_attrition(now)

        signal_snapshot = build_signal_snapshot(
            metrics,
            ignition_score,
            ignition_details,
            pressure,
            now,
            momentum_features=momentum
        )
        signal_snapshot.update(
            {
                "source": candidate_metadata.get("source", ""),
                "source_family": candidate_metadata.get("source_family", ""),
                "novelty_factor": candidate_metadata.get("novelty_factor"),
                "adjusted_score": candidate_metadata.get("adjusted_score"),
                "data_completeness_score": candidate_metadata.get(
                    "data_completeness_score"
                ),
                "evidence_bucket": candidate_metadata.get(
                    "evidence_bucket"
                ),
                "evidence_factor": candidate_metadata.get("evidence_factor"),
                "bad_evidence_penalty": candidate_metadata.get(
                    "bad_evidence_penalty"
                ),
                "data_missing": ignition_details.get("data_missing", [])
            }
        )
        update_discovery_bad_evidence_memory(
            memory,
            signal_snapshot,
            now
        )

        recent_snapshots = append_signal_snapshot(
            memory,
            signal_snapshot
        )

        ignition_details[
            "confidence_history"
        ] = confidence_history(
            recent_snapshots
        )

        try:
            await scanner_storage.save_signal_snapshot(
                signal_snapshot
            )
        except Exception as e:
            print(
                f"Signal snapshot save error: {e}"
            )
        else:
            try:
                await scanner_storage.update_ignition_alerts_for_snapshot(
                    metrics,
                    signal_snapshot,
                    signal_snapshot.get("timestamp", time.time())
                )
            except Exception as e:
                print(
                    f"Alert performance update error: {e}"
                )

            try:
                await scanner_storage.update_alert_outcomes_for_snapshot(
                    metrics,
                    signal_snapshot.get("timestamp", time.time())
                )
            except Exception as e:
                print(
                    f"Post-alert outcome update error: {e}"
                )

            # Control arm for future entry models: every alert-eligible
            # candidate gets one row per 24h whether or not it alerts
            # (analysis/runner_trainability_report.md rec #2c).
            try:
                recorded = await scanner_storage.record_candidate_event(
                    signal_snapshot
                )
            except Exception as e:
                recorded = False
                print(
                    f"Candidate event record error: {e}"
                )

            if recorded:
                # eligible-stage intel (GMGN by default), one lookup per
                # fresh eligible candidate row (token/24h)
                schedule_candidate_intel(
                    signal_snapshot.get("token_address"),
                    "eligible"
                )

        memory[
            "last_ignition_score"
        ] = ignition_score

        if (
            volume_ratio > 3
            or
            safe_float(
                ignition_details.get("flow_buy_sell_ratio"),
                0
            ) > 2
            or
            ignition_score >= 30
        ):

            memory[
                "tier"
            ] = 1

            memory[
                "tier1_consecutive_failures"
            ] = 0

        else:

            if (
                memory["tier"]
                == 1
            ):

                memory[
                    "tier1_consecutive_failures"
                ] += 1

                if (
                    memory[
                        "tier1_consecutive_failures"
                    ] >= 2
                ):

                    memory[
                        "tier"
                    ] = 2

        priority_reason = priority_rescan_reason(
            metrics,
            ignition_score,
            ignition_details,
            pressure
        )

        if priority_reason:
            memory["tier"] = 1
            memory["tier1_consecutive_failures"] = 0

            if enqueue_priority_scan(
                token_address,
                memory,
                priority_reason,
                now=now
            ):
                ignition_details[
                    "priority_scan_queued"
                ] = True
                ignition_details[
                    "priority_scan_reason"
                ] = priority_reason

        # TOKEN_MEMORY is keyed by contract address, so duplicate
        # tickers/names do not share an ignition cooldown.
        ignition_cooldown_passed = (
            now
            - memory["last_ignition_alert"]
            >= IGNITION_ALERT_COOLDOWN_SECONDS
        )

        prior_ignition_call = (
            memory["ignition_detected"]
            or memory["last_ignition_alert"] > 0
        )
        current_quality_tag = ignition_details.get(
            "quality_tag",
            "standard"
        )
        current_alert_route = ignition_details.get(
            "alert_route",
            "none"
        )
        first_ignition_quality_tag = memory.get(
            "first_ignition_quality_tag"
        )
        first_ignition_alert_route = memory.get(
            "first_ignition_alert_route"
        )
        current_high_conviction = (
            current_quality_tag == "high_conviction"
            or current_alert_route
            == "bonding_momentum_high_conviction"
        )
        first_call_high_conviction = (
            first_ignition_quality_tag == "high_conviction"
            or first_ignition_alert_route
            == "bonding_momentum_high_conviction"
        )
        upgraded_high_conviction_after_call = (
            prior_ignition_call
            and current_high_conviction
            and not first_call_high_conviction
        )

        ignition_details[
            "prior_ignition_call"
        ] = prior_ignition_call
        ignition_details[
            "first_ignition_quality_tag"
        ] = first_ignition_quality_tag
        ignition_details[
            "first_ignition_alert_route"
        ] = first_ignition_alert_route
        ignition_details[
            "upgraded_high_conviction_after_call"
        ] = upgraded_high_conviction_after_call

        if (
            ignition_triggered
            and prior_ignition_call
            and not ignition_cooldown_passed
        ):
            entry_precheck_reason = position_entry_precheck_reason(
                metrics,
                ignition_score,
                ignition_details,
                now,
                recent_snapshots
            )
            ignition_details[
                "position_entry_precheck_reason"
            ] = (
                entry_precheck_reason
                or "entry_ready"
            )
            recall_override_reason = (
                ignition_recall_override_reason(
                    metrics,
                    ignition_details,
                    memory,
                    now,
                    entry_precheck_reason
                )
            )

            if recall_override_reason:
                ignition_details[
                    "recall_override_reason"
                ] = recall_override_reason
                ignition_details[
                    "recall_override_cooldown_bypass"
                ] = True

        try:
            await refresh_position_sol_usd()
            ignition_score = apply_alert_window_entry(
                metrics,
                ignition_score,
                ignition_details,
                memory,
                now
            )
            position_events = position_engine.handle_scan(
                metrics,
                ignition_score,
                ignition_details,
                now,
                pressure=pressure,
                recent_snapshots=recent_snapshots
            )
            position_events = await maybe_execute_live_trade_events(
                position_events
            )

            for position_event in position_events:
                event_type = position_event.get("type", "")
                symbol = position_event.get("symbol", "?")
                print(
                    f"POSITION EVENT {event_type.upper()} "
                    f"{symbol} sending telegram..."
                )
                try:
                    await telegram.send_position_event(
                        position_event
                    )
                except Exception as send_exc:
                    print(
                        f"POSITION EVENT TELEGRAM ERROR "
                        f"{event_type} {symbol}: {send_exc}"
                    )
                    traceback.print_exc()

        except Exception as e:
            print(
                f"Position error: {e}"
            )
            traceback.print_exc()

        if (
            ignition_triggered
            and (
                ignition_cooldown_passed
                or recall_override_reason
            )
        ):

            is_recall = (
                memory["ignition_detected"]
                or memory["last_ignition_alert"] > 0
            )

            ignition_details[
                "is_recall"
            ] = is_recall

            if recall_override_reason:
                memory[
                    "last_ignition_recall_override_at"
                ] = now
                memory[
                    "last_ignition_recall_override_reason"
                ] = recall_override_reason

            if memory.get("first_ignition_fdv") is None:
                memory[
                    "first_ignition_fdv"
                ] = metrics.fdv

                memory[
                    "first_ignition_liquidity"
                ] = metrics.liquidity

                memory[
                    "first_ignition_price"
                ] = metrics.price

                memory[
                    "first_ignition_at"
                ] = now

                memory[
                    "first_ignition_quality_tag"
                ] = ignition_details.get(
                    "quality_tag"
                )

                memory[
                    "first_ignition_alert_route"
                ] = ignition_details.get(
                    "alert_route"
                )

            ignition_details[
                "initial_ignition_fdv"
            ] = memory.get(
                "first_ignition_fdv"
            )

            # Claim the slot immediately (before awaiting the network call)
            # to prevent concurrent process_token calls from double-firing.
            memory[
                "last_ignition_alert"
            ] = now

            memory[
                "last_ignition_quality_tag"
            ] = ignition_details.get(
                "quality_tag"
            )

            memory[
                "last_ignition_alert_route"
            ] = ignition_details.get(
                "alert_route"
            )

            memory[
                "last_ignition_recall_volume_multiple"
            ] = max(
                safe_float(
                    memory.get(
                        "last_ignition_recall_volume_multiple"
                    ),
                    0
                ),
                safe_float(
                    ignition_details.get(
                        "recall_override_volume_multiple"
                    ),
                    position_engine.entry_volume_multiple(
                        metrics
                    )
                )
            )

            memory[
                "last_ignition_recall_price_multiple"
            ] = max(
                safe_float(
                    memory.get(
                        "last_ignition_recall_price_multiple"
                    ),
                    0
                ),
                safe_float(
                    ignition_details.get(
                        "recall_override_price_multiple"
                    ),
                    (
                        safe_float(metrics.price, 0)
                        / max(
                            safe_float(
                                memory.get(
                                    "first_ignition_price"
                                ),
                                safe_float(metrics.price, 0)
                            ),
                            1e-18
                        )
                    )
                )
            )

            if ignition_details.get("metadata_special_alert"):
                memory[
                    "last_metadata_alert_at"
                ] = now

            persist_ignition_call(
                metrics,
                memory,
                ignition_score,
                now,
                ignition_details
            )

            lineage_text = ""

            try:
                lineage_text = (
                    await build_ticker_lineage_section(
                        client,
                        metrics.symbol,
                        metrics.address
                    )
                )

            except Exception as e:
                print(
                    f"Ticker lineage error: {e}"
                )

            ignition_details[
                "trending_checked"
            ] = trending_cache_loaded()

            ignition_details[
                "trending_match"
            ] = find_trending_match(
                metrics.symbol,
                getattr(metrics, "name", "") or "",
                metrics.address
            )

            delivery_count = await telegram.send_ignition_alert(
                metrics,
                ignition_score,
                ignition_breakdown,
                ignition_details,
                lineage_text=lineage_text
            )

            if delivery_count:
                try:
                    await scanner_storage.record_ignition_alert(
                        metrics,
                        ignition_score,
                        ignition_details,
                        now,
                        snapshot=signal_snapshot,
                        delivered_chat_ids=telegram.chat_ids,
                        delivery_count=delivery_count,
                        note=ignition_details.get("reason", "")
                    )
                except Exception as e:
                    print(
                        f"Ignition alert ledger error: {e}"
                    )

                # alerted-stage intel (paid twitter spend by default) fires
                # only for candidates that actually alerted
                schedule_candidate_intel(
                    metrics.address,
                    "alerted"
                )

            await telegram.send_ignition_summary(
                metrics,
                ignition_score,
                ignition_details,
                lineage_text=lineage_text
            )

            memory[
                "ignition_detected"
            ] = True

        history.append({
            "timestamp": now,
            "volume_5m": volume_5m,
            "liquidity": liquidity,
            "buys": buys,
            "sells": sells,
            "txns": total_txns,
            "price": price,
            "raw_base_reserve": metrics.raw_base_reserve,
            "raw_quote_reserve": metrics.raw_quote_reserve,
        })

        trim_token_history(history)

        print(
            f"[{metrics.chain.upper()}][Tier {memory['tier']}] "
            f"{metrics.symbol} "
            f"| CA={metrics.address} "
            f"| Ignition={ignition_score} "
            f"| Raw={ignition_details.get('raw_score', ignition_score)} "
            f"| Penalty={ignition_details.get('penalty', 0)} "
            f"| Route={ignition_details.get('alert_route', 'none')} "
            f"| Quality={ignition_details.get('quality_tag', 'standard')} "
            f"| Reason={ignition_details.get('reason', 'unknown')} "
            f"| Missing="
            f"{','.join(ignition_details.get('missing', [])) or 'none'} "
            f"| MetaChanges="
            f"{memory['metadata_mutations']} "
            f"| Vol={volume_5m:.0f}"
        )

    except Exception as e:

        print(
            f"Process token error: {e}"
        )


async def main():

    client = DexScreenerClient()

    await client.start()

    await scanner_storage.initialize()

    discovery = CandidateDiscovery(
        client
    )

    telegram_agent = TelegramCommandAgent(
        telegram=telegram,
        position_engine=position_engine,
        scanner_storage=scanner_storage,
        refresh_position_sol_usd=refresh_position_sol_usd,
        live_execution=live_execution
    )

    yellowstone = YellowstoneImpulseListener()

    await startup_reconcile_open_positions()

    print(
        "\nIgnition "
        "Scanner v5 Started\n"
    )

    print(
        f"Tracking up to "
        f"{MAX_CANDIDATES} "
        f"candidates across "
        f"{', '.join(SCANNER_ENABLED_CHAINS)}\n"
    )

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def request_shutdown(signame):

        if not stop_event.is_set():
            print(f"\nShutdown requested ({signame}). Stopping scanner...")
            stop_event.set()

    for signame in ("SIGINT", "SIGTERM"):
        signum = getattr(signal, signame, None)

        if signum is None:
            continue

        try:
            loop.add_signal_handler(
                signum,
                request_shutdown,
                signame
            )
        except NotImplementedError:
            signal.signal(
                signum,
                lambda _signum, _frame, name=signame: request_shutdown(name)
            )

    tasks = [
        asyncio.create_task(
            refresh_candidates_loop(
                discovery
            )
        ),
        asyncio.create_task(
            priority_scan_loop(
                client
            )
        ),
        asyncio.create_task(
            position_status_loop()
        ),
        asyncio.create_task(
            alert_performance_summary_loop()
        ),
        asyncio.create_task(
            llm_pattern_report_loop()
        ),
        asyncio.create_task(
            trending_cache_loop()
        ),
        asyncio.create_task(
            telegram_agent.run()
        ),
        asyncio.create_task(
            live_execution_retry_watcher()
        ),
        asyncio.create_task(
            telemetry_prune_loop()
        ),
        asyncio.create_task(
            position_monitor_loop(
                client
            )
        ),
        asyncio.create_task(
            yellowstone.run()
        ),
        asyncio.create_task(
            monitor_candidates(
                client
            )
        )
    ]
    stop_task = asyncio.create_task(
        stop_event.wait()
    )

    try:

        done, pending = await asyncio.wait(
            [
                stop_task,
                *tasks
            ],
            return_when=asyncio.FIRST_COMPLETED
        )

        if stop_task in done:
            pending = [
                task
                for task in tasks
                if not task.done()
            ]
        else:
            for task in done:
                task.result()

        for task in pending:
            task.cancel()

        if pending:
            await asyncio.gather(
                *pending,
                return_exceptions=True
            )

    finally:

        stop_task.cancel()
        await asyncio.gather(
            stop_task,
            return_exceptions=True
        )
        await discovery.close()
        await client.close()

    print("Scanner stopped cleanly.")


if __name__ == "__main__":

    try:

        asyncio.run(main())

    except KeyboardInterrupt:

        print("\nScanner stopped cleanly.")
