import json
import os
from collections import defaultdict, deque
from pathlib import Path

from config import (
    IGNITION_STATE_FILE
)


TRACKED_CANDIDATES = {}

PRIORITY_SCAN_QUEUE = deque()

PRIORITY_SCAN_SET = set()

# gRPC-derived real-time prices for open positions
# token_address → {price_sol, updated_at, direction}
GRPC_POSITION_PRICES = {}

# pair_address → token_address, maintained by position_monitor_loop
# so the yellowstone listener can subscribe to open position pools
POSITION_WATCH_ACCOUNTS = {}

IGNITION_CALLS = None


def get_ignition_state_path():

    path = Path(IGNITION_STATE_FILE)

    if path.is_absolute():
        return path

    return Path(__file__).resolve().parent / path


def load_ignition_calls():

    global IGNITION_CALLS

    if IGNITION_CALLS is not None:
        return IGNITION_CALLS

    path = get_ignition_state_path()

    if not path.exists():
        IGNITION_CALLS = {}
        return IGNITION_CALLS

    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception as e:
        print(
            f"Ignition state load failed: {e}"
        )
        IGNITION_CALLS = {}
        return IGNITION_CALLS

    if not isinstance(data, dict):
        IGNITION_CALLS = {}
        return IGNITION_CALLS

    IGNITION_CALLS = {
        str(address): snapshot
        for address, snapshot in data.items()
        if isinstance(snapshot, dict)
    }

    return IGNITION_CALLS


def save_ignition_calls():

    path = get_ignition_state_path()
    path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    temp_path = path.with_suffix(
        f"{path.suffix}.tmp"
    )

    with temp_path.open("w", encoding="utf-8") as handle:
        json.dump(
            load_ignition_calls(),
            handle,
            indent=2,
            sort_keys=True
        )
        handle.write("\n")

    os.replace(
        temp_path,
        path
    )


def safe_float(
    value,
    default=None
):

    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_timestamp(
    value,
    default=0
):

    try:
        return float(value or default)
    except (TypeError, ValueError):
        return default


def hydrate_ignition_memory(
    token_address,
    memory
):

    snapshot = load_ignition_calls().get(
        str(token_address)
    )

    if not snapshot:
        return False

    first_at = safe_timestamp(
        snapshot.get("first_ignition_at")
    )

    last_at = safe_timestamp(
        snapshot.get("last_ignition_alert")
    )

    if first_at or last_at:
        memory["ignition_detected"] = True

    memory["last_ignition_alert"] = max(
        memory["last_ignition_alert"],
        last_at
    )

    memory["last_ignition_score"] = max(
        memory["last_ignition_score"],
        safe_float(
            snapshot.get("last_ignition_score"),
            0
        )
    )

    for key in (
        "first_ignition_fdv",
        "first_ignition_liquidity",
        "first_ignition_price",
        "first_ignition_at",
        "first_ignition_quality_tag",
        "first_ignition_alert_route",
        "last_ignition_quality_tag",
        "last_ignition_alert_route"
    ):
        if memory.get(key) is None and snapshot.get(key) is not None:
            memory[key] = snapshot[key]

    for key in (
        "last_ignition_recall_override_at",
        "last_ignition_recall_volume_multiple",
        "last_ignition_recall_price_multiple"
    ):
        memory[key] = max(
            safe_float(memory.get(key), 0),
            safe_float(snapshot.get(key), 0)
        )

    if (
        not memory.get("last_ignition_recall_override_reason")
        and snapshot.get("last_ignition_recall_override_reason")
    ):
        memory["last_ignition_recall_override_reason"] = snapshot[
            "last_ignition_recall_override_reason"
        ]

    return True


def persist_ignition_call(
    metrics,
    memory,
    score,
    now,
    details=None
):

    details = details or {}
    calls = load_ignition_calls()
    address = str(metrics.address)
    existing = calls.get(address, {})

    first_fdv = existing.get(
        "first_ignition_fdv"
    )

    if first_fdv is None:
        first_fdv = memory.get(
            "first_ignition_fdv",
            metrics.fdv
        )

    first_liquidity = existing.get(
        "first_ignition_liquidity"
    )

    if first_liquidity is None:
        first_liquidity = memory.get(
            "first_ignition_liquidity",
            metrics.liquidity
        )

    first_price = existing.get(
        "first_ignition_price"
    )

    if first_price is None:
        first_price = memory.get(
            "first_ignition_price",
            metrics.price
        )

    first_at = existing.get(
        "first_ignition_at"
    )

    if first_at is None:
        first_at = memory.get(
            "first_ignition_at",
            now
        )

    first_quality_tag = existing.get(
        "first_ignition_quality_tag"
    )

    if first_quality_tag is None:
        first_quality_tag = memory.get(
            "first_ignition_quality_tag"
        )

        if (
            first_quality_tag is None
            and not details.get("prior_ignition_call", False)
        ):
            first_quality_tag = details.get("quality_tag")

    first_alert_route = existing.get(
        "first_ignition_alert_route"
    )

    if first_alert_route is None:
        first_alert_route = memory.get(
            "first_ignition_alert_route"
        )

        if (
            first_alert_route is None
            and not details.get("prior_ignition_call", False)
        ):
            first_alert_route = details.get("alert_route")

    migration_carryover = {
        key: existing.get(key)
        for key in (
            "migration_first_seen_at",
            "migration_first_fdv",
            "migration_first_price",
            "migration_peak_at",
            "migration_peak_fdv",
            "migration_peak_price"
        )
        if existing.get(key) is not None
    }

    calls[address] = {
        "address": address,
        "symbol": metrics.symbol,
        **migration_carryover,
        "first_ignition_fdv": first_fdv,
        "first_ignition_liquidity": first_liquidity,
        "first_ignition_price": first_price,
        "first_ignition_at": first_at,
        "first_ignition_quality_tag": first_quality_tag,
        "first_ignition_alert_route": first_alert_route,
        "last_ignition_alert": now,
        "last_ignition_fdv": metrics.fdv,
        "last_ignition_liquidity": metrics.liquidity,
        "last_ignition_price": metrics.price,
        "last_ignition_score": score,
        "last_ignition_quality_tag": details.get(
            "quality_tag"
        ),
        "last_ignition_alert_route": details.get(
            "alert_route"
        ),
        "last_ignition_recall_override_at": (
            memory.get("last_ignition_recall_override_at")
            or existing.get("last_ignition_recall_override_at", 0)
        ),
        "last_ignition_recall_override_reason": (
            memory.get("last_ignition_recall_override_reason")
            or existing.get("last_ignition_recall_override_reason", "")
        ),
        "last_ignition_recall_volume_multiple": max(
            safe_float(
                existing.get("last_ignition_recall_volume_multiple"),
                0
            ),
            safe_float(
                memory.get("last_ignition_recall_volume_multiple"),
                0
            )
        ),
        "last_ignition_recall_price_multiple": max(
            safe_float(
                existing.get("last_ignition_recall_price_multiple"),
                0
            ),
            safe_float(
                memory.get("last_ignition_recall_price_multiple"),
                0
            )
        ),
        "last_age_hours": metrics.age_hours,
        "last_lifecycle": metrics.lifecycle
    }

    try:
        save_ignition_calls()
    except Exception as e:
        print(
            f"Ignition state save failed: {e}"
        )


def update_migration_tracking(metrics, now):

    if str(getattr(metrics, "lifecycle", "")) != "migrated":
        return

    address = str(getattr(metrics, "address", ""))

    if not address:
        return

    fdv = safe_float(getattr(metrics, "fdv", 0), 0) or 0
    price = safe_float(getattr(metrics, "price", 0), 0) or 0

    if fdv <= 0 and price <= 0:
        return

    calls = load_ignition_calls()
    entry = calls.get(address)

    if entry is None:
        entry = {
            "address": address,
            "symbol": getattr(metrics, "symbol", "")
        }
        calls[address] = entry

    changed = False

    if entry.get("migration_first_seen_at") is None:
        entry["migration_first_seen_at"] = now
        entry["migration_first_fdv"] = fdv
        entry["migration_first_price"] = price
        entry["migration_peak_at"] = now
        entry["migration_peak_fdv"] = fdv
        entry["migration_peak_price"] = price
        changed = True
    else:
        peak_fdv = safe_float(entry.get("migration_peak_fdv"), 0) or 0
        if fdv > peak_fdv:
            entry["migration_peak_at"] = now
            entry["migration_peak_fdv"] = fdv
            entry["migration_peak_price"] = price
            changed = True

    if changed:
        try:
            save_ignition_calls()
        except Exception as e:
            print(f"Migration tracking save failed: {e}")


def get_migration_reference(address):

    entry = load_ignition_calls().get(str(address))

    if not entry:
        return None

    first_fdv = safe_float(entry.get("migration_first_fdv"), 0) or 0
    peak_fdv = safe_float(entry.get("migration_peak_fdv"), 0) or 0
    reference_fdv = max(first_fdv, peak_fdv)

    if reference_fdv <= 0:
        return None

    first_price = safe_float(entry.get("migration_first_price"), 0) or 0
    peak_price = safe_float(entry.get("migration_peak_price"), 0) or 0

    if peak_fdv >= first_fdv:
        reference_price = peak_price
        reference_at = entry.get("migration_peak_at")
    else:
        reference_price = first_price
        reference_at = entry.get("migration_first_seen_at")

    return {
        "reference_fdv": reference_fdv,
        "reference_price": reference_price,
        "reference_at": reference_at,
        "first_fdv": first_fdv,
        "first_price": first_price,
        "first_seen_at": entry.get("migration_first_seen_at"),
        "peak_fdv": peak_fdv,
        "peak_price": peak_price,
        "peak_at": entry.get("migration_peak_at")
    }


def migration_drawdown_pct(metrics):

    reference = get_migration_reference(
        getattr(metrics, "address", "")
    )

    if not reference:
        return None

    reference_fdv = reference["reference_fdv"]
    current_fdv = safe_float(getattr(metrics, "fdv", 0), 0) or 0

    if reference_fdv <= 0:
        return None

    drawdown = (reference_fdv - current_fdv) / reference_fdv

    return max(0, drawdown)


def default_memory():

    return {

        # ─────────────────────────────
        # Activity memory
        # ─────────────────────────────

        "history": [],

        "signal_snapshots": [],

        "last_score": 0,

        "last_alert": 0,

        "last_ignition_alert": 0,

        "last_scan": 0,

        "last_grpc_scan": 0,

        "last_grpc_activity": 0,

        "recent_trade_flows": [],

        "last_trade_flow_at": 0,

        "discovery_count": 0,

        "last_discovery_at": 0,

        "last_discovery_source": "",

        "last_validated_at": 0,

        "last_validated_source": "",

        "discovery_source_counts": {},

        "bad_evidence_count": 0,

        "last_bad_evidence_at": 0,

        "last_bad_evidence_reason": "",

        # ─────────────────────────────
        # Tiering
        # ─────────────────────────────

        "tier": 2,

        "tier1_consecutive_failures": 0,

        "last_priority_scan_queued_at": 0,

        "last_priority_scan_reason": "",

        # ─────────────────────────────
        # Dormancy detection
        # ─────────────────────────────

        "low_activity_count": 0,

        "low_volatility_count": 0,

        "quiet_period_detected": False,

        # ─────────────────────────────
        # Rolling baselines
        # ─────────────────────────────

        "rolling_avg_volume": 0,

        "rolling_avg_txns": 0,

        "rolling_avg_volatility": 0,

        # ─────────────────────────────
        # Revival state
        # ─────────────────────────────

        "revival_detected": False,

        "ignition_detected": False,

        "last_ignition_score": 0,

        "first_ignition_fdv": None,

        "first_ignition_liquidity": None,

        "first_ignition_price": None,

        "first_ignition_at": None,

        "first_ignition_quality_tag": None,

        "first_ignition_alert_route": None,

        "last_ignition_quality_tag": None,

        "last_ignition_alert_route": None,

        "last_ignition_recall_override_at": 0,

        "last_ignition_recall_override_reason": "",

        "last_ignition_recall_volume_multiple": 0,

        "last_ignition_recall_price_multiple": 0,

        # ─────────────────────────────
        # Metadata snapshot memory
        # ─────────────────────────────

        "metadata_snapshot": {

            "website": None,

            "twitter": None,

            "telegram": None,

            "banner": None,

            "image_url": None,

            "description": None,

            "boosted": False
        },

        # ─────────────────────────────
        # Metadata mutation counters
        # ─────────────────────────────

        "metadata_mutations": 0,

        "recent_metadata_change": False,

        "metadata_initialized": False,

        "last_metadata_change_fields": [],

        "last_metadata_alert_at": 0
    }


TOKEN_MEMORY = defaultdict(
    default_memory
)
