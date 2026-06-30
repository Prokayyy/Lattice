import json
import os
import time
from pathlib import Path

from config import (
    ANCHORED_VWAP_ENTRY_ENABLED,
    ANCHORED_VWAP_ENTRY_REQUIRE_READY,
    ANCHORED_VWAP_PEAK_TRAIL_MIN_MULTIPLE,
    ANCHORED_VWAP_PEAK_TRAIL_PCT,
    ANCHORED_VWAP_STOP_CONFIRMATION_TICKS,
    POSITION_HARD_STOP_CONFIRMATION_TICKS,
    ANCHORED_VWAP_TRAILING_ACTIVATE_PROFIT_PCT,
    ANCHORED_VWAP_TRAILING_STOP_BUFFER_PCT,
    ANCHORED_VWAP_TRAILING_STOP_ENABLED,
    HYPEREVM_IGNITION_MAX_FDV_USD,
    HYPEREVM_IGNITION_MIN_LIQUIDITY_USD,
    HYPEREVM_IGNITION_MIN_PRICE_CHANGE_24H,
    HYPEREVM_IGNITION_MIN_PRICE_CHANGE_5M,
    HYPEREVM_IGNITION_MIN_VOLUME_1H_USD,
    HYPEREVM_IGNITION_SCORE,
    IGNITION_ALERT_THRESHOLD,
    LIVE_EXECUTION_REQUIRE_EXIT_QUOTE_FOR_STOPS,
    LIVE_EXECUTION_STOP_QUOTE_BUFFER_PCT,
    LIVE_EXECUTION_STOP_QUOTE_MAX_SPOT_PREMIUM_PCT,
    LIVE_EXECUTION_STOP_WATCHDOG_ENABLED,
    LIVE_EXECUTION_STOP_WATCHDOG_STALE_SECONDS,
    LIVE_EXECUTION_USE_QUOTES_FOR_STOPS,
    DEFINITIVE_INITIAL_TAKE_PROFIT_ENABLED,
    DEFINITIVE_INITIAL_TAKE_PROFIT_MIN_NOTIONAL_USD,
    DEFINITIVE_INITIAL_TAKE_PROFIT_MULTIPLE,
    DEFINITIVE_INITIAL_TAKE_PROFIT_RECOVERY_PCT,
    POSITION_CHOP_FILTER_ENABLED,
    POSITION_CHOP_LOOKBACK_SCANS,
    POSITION_CHOP_MAX_BUY_SELL_RATIO,
    POSITION_CHOP_MAX_RANGE_POSITION,
    POSITION_CHOP_MIN_DIRECTION_FLIPS,
    POSITION_CHOP_MIN_LEG_MOVE_PCT,
    POSITION_CHOP_MIN_RANGE_PCT,
    POSITION_CLOSED_POSITION_LIMIT,
    POSITION_DECAY_LOOKBACK_SCANS,
    POSITION_DECAY_MAX_BUY_SELL_RATIO,
    POSITION_DECAY_MAX_PRESSURE,
    POSITION_DECAY_MAX_VOLUME_LIQUIDITY_RATIO,
    POSITION_DECAY_SCORE_DROP,
    POSITION_ENABLED,
    POSITION_ENTRY_CONFIRMATION_ENABLED,
    POSITION_ENTRY_CONFIRMATION_MAX_VOLUME_LIQUIDITY_RATIO,
    POSITION_ENTRY_CONFIRMATION_MAX_VWAP_DISTANCE_PCT,
    POSITION_ENTRY_CONFIRMATION_MIN_BUY_SELL_RATIO,
    POSITION_ENTRY_CONFIRMATION_MIN_BUY_VOLUME_5M_USD,
    POSITION_ENTRY_CONFIRMATION_MIN_PRICE_CHANGE_1H,
    POSITION_ENTRY_CONFIRMATION_MIN_PRICE_CHANGE_5M,
    POSITION_ENTRY_CONFIRMATION_MIN_PRESSURE,
    POSITION_ENTRY_CONFIRMATION_MIN_SCORE,
    POSITION_ENTRY_CONFIRMATION_MIN_VOLUME_LIQUIDITY_RATIO,
    POSITION_ENTRY_CONFIRMATION_REQUIRED_SCANS,
    POSITION_ENTRY_CONFIRMATION_SHADOW_MODE,
    POSITION_ENTRY_CONFIRMATION_WATCH_SECONDS,
    POSITION_AVOID_MIGRATION_FDV_ZONE,
    POSITION_HIGH_VOLUME_TRAIL_GRACE_ENABLED,
    POSITION_HIGH_VOLUME_TRAIL_GRACE_MIN_BUY_SELL_RATIO,
    POSITION_HIGH_VOLUME_TRAIL_GRACE_MIN_PRESSURE,
    POSITION_HIGH_VOLUME_TRAIL_GRACE_MIN_VOLUME_LIQUIDITY_RATIO,
    POSITION_HIGH_VOLUME_TRAIL_GRACE_MIN_VOLUME_MULTIPLE,
    POSITION_HIGH_VOLUME_TRAIL_GRACE_TRAIL_PCT,
    POSITION_HIGH_VOLUME_TRAIL_GRACE_UNTIL_PEAK_MULTIPLE,
    POSITION_INITIAL_STOP_LOSS_PCT,
    POSITION_INITIAL_BALANCE_SOL,
    POSITION_LIQUIDITY_COLLAPSE_EXIT_ENABLED,
    POSITION_LIQUIDITY_COLLAPSE_FROM_ENTRY_PCT,
    POSITION_LIQUIDITY_COLLAPSE_FROM_PEAK_PCT,
    POSITION_LIQUIDITY_COLLAPSE_MIN_REFERENCE_USD,
    POSITION_LIQUIDITY_COLLAPSE_PRESSURE_CAP,
    POSITION_HYPEREVM_MAX_ENTRY_FDV_USD,
    POSITION_HYPEREVM_MAX_SCALE_OUT_PCT,
    POSITION_HYPEREVM_POSITION_SIZE_USD,
    POSITION_HYPEREVM_SCALE_OUT_LADDER,
    POSITION_HYPEREVM_TAKE_PROFIT_SELL_PCT,
    POSITION_MAX_ENTRY_IMPULSE,
    POSITION_MAX_ENTRY_FDV_USD,
    POSITION_EARLY_REVIVAL_MAX_ENTRY_FDV_USD,
    POSITION_MAX_ENTRY_PENALTY,
    POSITION_EARLY_REVIVAL_MAX_ENTRY_PENALTY,
    POSITION_MIGRATED_REVIVAL_MAX_ENTRY_PENALTY,
    POSITION_MAX_ENTRY_PRICE_CHANGE_5M,
    POSITION_EARLY_REVIVAL_MIN_ENTRY_SCORE,
    POSITION_MIGRATED_REVIVAL_MIN_ENTRY_SCORE,
    POSITION_EARLY_REVIVAL_INITIAL_STOP_LOSS_PCT,
    POSITION_MIGRATED_REVIVAL_INITIAL_STOP_LOSS_PCT,
    POSITION_HC_INITIAL_STOP_LOSS_PCT,
    POSITION_HC_MIN_ENTRY_VOLUME_1H_USD,
    POSITION_HC_MIN_ENTRY_VOLUME_MULTIPLE,
    POSITION_HC_MAX_ENTRY_PRICE_CHANGE_5M,
    POSITION_EARLY_REVIVAL_MAX_ENTRY_PRICE_CHANGE_5M,
    POSITION_HC_MAX_ENTRY_PENALTY,
    POSITION_EARLY_REVIVAL_SCALE_OUT_LADDER,
    POSITION_MIGRATED_REVIVAL_SCALE_OUT_LADDER,
    POSITION_HC_SCALE_OUT_LADDER,
    POSITION_IMMEDIATE_MIN_ENTRY_SCORE,
    POSITION_MIN_ENTRY_FDV_USD,
    POSITION_MAX_ENTRIES_PER_TOKEN_PER_HOUR,
    POSITION_MAX_OPEN_POSITIONS,
    POSITION_MAX_SCALE_OUT_PCT,
    POSITION_MISSING_PAIR_ALERT_SCANS,
    POSITION_MIN_ENTRY_IMPULSE,
    POSITION_EARLY_REVIVAL_MIN_ENTRY_PRICE_CHANGE_5M,
    POSITION_MIGRATED_REVIVAL_MIN_ENTRY_PRICE_CHANGE_5M,
    POSITION_EARLY_REVIVAL_MIN_ENTRY_PRICE_CHANGE_1H,
    POSITION_MIGRATED_REVIVAL_MIN_ENTRY_PRICE_CHANGE_1H,
    POSITION_MIN_ENTRY_PRICE_CHANGE_1H,
    POSITION_MIN_ENTRY_PRICE_CHANGE_5M,
    POSITION_MIN_ENTRY_VOLUME_1H_USD,
    POSITION_EARLY_REVIVAL_MIN_ENTRY_VOLUME_1H_USD,
    POSITION_MIGRATED_REVIVAL_MIN_ENTRY_VOLUME_1H_USD,
    POSITION_MIN_ENTRY_VOLUME_MULTIPLE,
    POSITION_EARLY_REVIVAL_MIN_ENTRY_VOLUME_MULTIPLE,
    POSITION_MIGRATED_REVIVAL_MIN_ENTRY_VOLUME_MULTIPLE,
    POSITION_MID_VOLUME_CONFIRM_ENABLED,
    POSITION_MID_VOLUME_MIN_BUY_SELL_RATIO,
    POSITION_MID_VOLUME_MIN_PRESSURE,
    POSITION_MID_VOLUME_MIN_VOLUME_LIQUIDITY_RATIO,
    POSITION_LINEAGE_EXPOSURE_BLOCK_ENABLED,
    POSITION_MIN_ENTRY_BUY_SELL_VOLUME_RATIO,
    POSITION_REQUIRE_OBSERVED_BUY_SELL_VOLUME,
    POSITION_REENTRY_COOLDOWN_SECONDS,
    POSITION_REENTRY_MIN_VOLUME_5M_USD,
    POSITION_REENTRY_BLOCK_AFTER_WIN_ENABLED,
    POSITION_REENTRY_NEW_HIGH_PCT,
    POSITION_REENTRY_POSITION_SIZE_MULTIPLIER,
    POSITION_REENTRY_RECLAIM_EXIT_PCT,
    POSITION_REENTRY_RISKY_PRIOR_CLOSE_REASONS,
    POSITION_REENTRY_STATE_FILTER_ENABLED,
    POSITION_TRAILING_REBOUND_MIN_BUY_SELL_VOLUME_RATIO,
    POSITION_TRAILING_REBOUND_MIN_BUY_VOLUME_5M_USD,
    POSITION_TRAILING_REBOUND_MIN_PRESSURE,
    POSITION_TRAILING_REBOUND_MIN_VOLUME_LIQUIDITY_RATIO,
    POSITION_TRAILING_REBOUND_RECLAIM_PCT,
    POSITION_TRAILING_REBOUND_REENTRY_ENABLED,
    POSITION_TRAILING_REBOUND_REQUIRE_VWAP_RECLAIM,
    POSITION_TRAILING_REBOUND_REQUIRE_VWAP_READY,
    POSITION_TRAILING_REBOUND_WATCH_SECONDS,
    POSITION_MIGRATION_FDV_BUFFER_USD,
    POSITION_MIGRATION_ZONE_GRACE_ENABLED,
    POSITION_MIGRATION_ZONE_GRACE_UNTIL_MULTIPLE,
    POSITION_RUNNER_HOLD_ENABLED,
    POSITION_RUNNER_HOLD_FRACTION,
    POSITION_RUNNER_HOLD_FLOOR_MULTIPLE,
    POSITION_RUNNER_HOLD_RELEASE_MULTIPLE,
    POSITION_RUNNER_HOLD_MAX_HOURS,
    POSITION_MIN_SCALE_OUT_STEP_PCT,
    POSITION_PRESSURE_LOSS_EXIT_ENABLED,
    POSITION_PRESSURE_EXIT_MAX_BUY_SELL_RATIO,
    POSITION_PRESSURE_EXIT_MAX_IMPULSE,
    POSITION_PRESSURE_EXIT_MAX_LOSS_PCT,
    POSITION_PRESSURE_EXIT_MAX_PRESSURE,
    POSITION_PRESSURE_EXIT_MAX_VOLUME_LIQUIDITY_RATIO,
    POSITION_FIXED_USD_POSITION_SIZING_ENABLED,
    POSITION_POSITION_SIZE_SOL,
    POSITION_POSITION_SIZE_USD,
    POSITION_POST_SCALE_TRAIL_ENABLED,
    POSITION_POST_SCALE_TRAIL_RULES,
    POSITION_QUALITY_VOLUME_GATE_ENABLED,
    POSITION_RUNNER_RELAXED_MIN_BUY_SELL_RATIO,
    POSITION_RUNNER_RELAXED_MIN_PRESSURE,
    POSITION_RUNNER_RELAXED_MIN_PRICE_MULTIPLE,
    POSITION_RUNNER_RELAXED_MIN_VOLUME_LIQUIDITY_RATIO,
    POSITION_RUNNER_RELAXED_TRAIL_PCT,
    POSITION_SCORE_DECAY_MAX_PRICE_MULTIPLE,
    POSITION_SCALE_OUT_LADDER,
    POSITION_SELL_ONLY_FLOW_EXIT_ENABLED,
    POSITION_SELL_ONLY_FLOW_MAX_BUY_SELL_VOLUME_RATIO,
    POSITION_SELL_ONLY_FLOW_MAX_BUY_VOLUME_5M_USD,
    POSITION_SELL_ONLY_FLOW_MAX_PRICE_MULTIPLE,
    POSITION_SELL_ONLY_FLOW_MIN_SELL_ENTRY_NOTIONAL_MULTIPLE,
    POSITION_SELL_ONLY_FLOW_MIN_SELL_VOLUME_5M_USD,
    POSITION_SCORE_DECAY_EXIT_ENABLED,
    POSITION_SOL_USD,
    POSITION_STATUS_REPORTS_ENABLED,
    POSITION_STATE_FILE,
    POSITION_FULL_SIZE_VOLUME_MULTIPLE,
    POSITION_STRICT_EARLY_EXIT_ENABLED,
    POSITION_STRICT_EARLY_EXIT_LOSS_PCT,
    POSITION_STRICT_EARLY_EXIT_MAX_BUY_SELL_RATIO,
    POSITION_STRICT_EARLY_EXIT_MAX_PRESSURE,
    POSITION_STRICT_EARLY_EXIT_MAX_VOLUME_LIQUIDITY_RATIO,
    POSITION_STRICT_EARLY_EXIT_MIN_WEAK_SIGNALS,
    POSITION_STRICT_EARLY_EXIT_CONFIRM_TICKS,
    POSITION_TAKE_PROFIT_MULTIPLE,
    POSITION_TAKE_PROFIT_SELL_PCT,
)


def safe_float(
    value,
    default=0
):

    try:
        return float(value or default)
    except (TypeError, ValueError):
        return default


def normalize_ticker(
    value
):

    return str(value or "").strip().lstrip("$").upper()


def list_from_value(
    value
):

    if not value:
        return []

    if isinstance(value, (list, tuple, set)):
        return [
            str(item).strip()
            for item in value
            if str(item).strip()
        ]

    return [
        item.strip()
        for item in str(value).replace(";", ",").split(",")
        if item.strip()
    ]


def get_state_path(
    state_file=None
):

    path = Path(
        state_file
        or POSITION_STATE_FILE
    )

    if path.is_absolute():
        return path

    return Path(__file__).resolve().parent.parent / path


class PositionEngine:

    def __init__(
        self,
        state_file=None
    ):

        self.state_file = state_file
        self.state = None
        self.state_dirty = False
        self.sol_usd = POSITION_SOL_USD

    def set_sol_usd(
        self,
        value
    ):

        value = safe_float(
            value,
            POSITION_SOL_USD
        )

        if value > 0:
            self.sol_usd = value

        return self.sol_usd

    def current_sol_usd(self):

        return safe_float(
            self.sol_usd,
            POSITION_SOL_USD
        )

    def get_state_path(self):

        return get_state_path(
            self.state_file
        )

    def load_state(self):

        if self.state is not None:
            return self.state

        path = self.get_state_path()

        if not path.exists():
            self.state = {
                "starting_balance_sol": (
                    POSITION_INITIAL_BALANCE_SOL
                ),
                "cash_sol": POSITION_INITIAL_BALANCE_SOL,
                "open": {},
                "closed": [],
                "entry_confirmation_watch": {},
                "rsi_entry_watch": {},
                "trailing_rebound_watch": {}
            }
            return self.state

        try:
            with path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        except Exception as e:
            print(
                f"Position state load failed: {e}"
            )
            data = {}

        if not isinstance(data, dict):
            data = {}

        open_positions = dict(
            data.get("open", {})
            or {}
        )
        starting_balance_sol = safe_float(
            data.get("starting_balance_sol"),
            POSITION_INITIAL_BALANCE_SOL
        )

        if "cash_sol" in data:
            cash_sol = safe_float(
                data.get("cash_sol"),
                starting_balance_sol
            )
        else:
            allocated_sol = sum(
                self.position_entry_sol(position)
                for position in open_positions.values()
            )
            cash_sol = max(
                starting_balance_sol - allocated_sol,
                0
            )

        self.state = {
            "starting_balance_sol": starting_balance_sol,
            "cash_sol": cash_sol,
            "open": dict(
                open_positions
            ),
            "closed": list(
                data.get("closed", [])
                or []
            ),
            "entry_confirmation_watch": dict(
                data.get("entry_confirmation_watch", {})
                or {}
            ),
            "rsi_entry_watch": dict(
                data.get("rsi_entry_watch", {})
                or {}
            ),
            "trailing_rebound_watch": dict(
                data.get("trailing_rebound_watch", {})
                or {}
            )
        }

        return self.state

    def position_entry_sol(
        self,
        position
    ):

        entry_size_sol = safe_float(
            position.get("entry_size_sol"),
            0
        )

        if entry_size_sol > 0:
            return entry_size_sol

        return (
            safe_float(
                position.get("entry_notional_usd"),
                0
            )
            / max(
                safe_float(
                    position.get("entry_sol_usd"),
                    self.current_sol_usd()
                ),
                1e-18
            )
        )

    def save_state(self):

        path = self.get_state_path()
        path.parent.mkdir(
            parents=True,
            exist_ok=True
        )

        temp_path = path.with_suffix(
            f"{path.suffix}.tmp"
        )

        state = self.load_state()

        with temp_path.open("w", encoding="utf-8") as handle:
            json.dump(
                state,
                handle,
                indent=2,
                sort_keys=True
            )
            handle.write("\n")

        os.replace(
            temp_path,
            path
        )
        self.state_dirty = False

    def open_addresses(self):

        return list(
            self.load_state()["open"].keys()
        )

    def open_position_refs(self):

        refs = []

        for address, position in self.load_state()["open"].items():
            refs.append(
                (
                    address,
                    position.get("chain", "solana")
                )
            )

        return refs

    def stale_live_stop_events(
        self,
        now=None
    ):
        if not LIVE_EXECUTION_STOP_WATCHDOG_ENABLED:
            return []

        now = now or time.time()
        stale_threshold = max(
            safe_float(LIVE_EXECUTION_STOP_WATCHDOG_STALE_SECONDS, 120),
            10
        )
        events = []

        for address, position in self.load_state().get("open", {}).items():
            if not position.get("live_execution_entry_submitted"):
                continue

            trailing_stop = safe_float(
                position.get("trailing_stop_price"), 0
            )
            if trailing_stop <= 0:
                continue

            last_price = safe_float(position.get("last_price"), 0)
            if last_price <= 0 or last_price > trailing_stop:
                continue

            last_update = safe_float(position.get("last_update_at"), 0)
            if last_update <= 0:
                continue

            age = now - last_update
            if age < stale_threshold:
                continue

            print(
                "LIVE WATCHDOG stale stop breach "
                f"{position.get('symbol', address)} "
                f"last_price={last_price:.8f} "
                f"stop={trailing_stop:.8f} "
                f"stale={age:.0f}s"
            )

            events.append(
                self.build_position_event(
                    "close",
                    position,
                    f"watchdog_stale_stop stale={age:.0f}s"
                )
            )

        return events

    def live_execution_event_key(
        self,
        event
    ):

        return "|".join([
            str(event.get("type", "")),
            str(event.get("address", "")),
            str(event.get("timestamp", "")),
            str(event.get("reason", "")),
            f"{safe_float(event.get('size_pct'), 0):.8f}",
            f"{safe_float(event.get('proceeds_usd'), 0):.8f}"
        ])

    def live_execution_open_summary(self):

        state = self.load_state()
        open_count = 0
        open_exposure_usd = 0

        for position in state.get("open", {}).values():
            if not position.get("live_execution_entry_submitted"):
                continue

            open_count += 1
            open_exposure_usd += safe_float(
                position.get("live_execution_entry_notional_usd"),
                position.get("entry_notional_usd")
            )

        return {
            "open_count": open_count,
            "open_exposure_usd": open_exposure_usd
        }

    def live_execution_position_for_event(
        self,
        event
    ):

        state = self.load_state()
        address = str(
            event.get("address", "")
        )
        position = state.get("open", {}).get(address)

        if position:
            return position

        timestamp = safe_float(
            event.get("timestamp"),
            0
        )

        for closed in reversed(
            state.get("closed", [])
        ):
            if str(closed.get("address", "")) != address:
                continue

            if (
                timestamp > 0
                and abs(
                    safe_float(
                        closed.get("last_update_at"),
                        closed.get("exit_at")
                    )
                    - timestamp
                )
                > 5
            ):
                continue

            return closed

        return None

    def live_execution_position_has_entry(
        self,
        event
    ):

        position = self.live_execution_position_for_event(
            event
        )

        if not position:
            return False

        return bool(
            position.get("live_execution_entry_submitted")
        )

    def live_execution_entry_event(
        self,
        position
    ):

        if not isinstance(position, dict):
            return None

        for item in position.get("events", []) or []:
            if item.get("type") == "entry":
                return item

        return None

    def live_execution_event_seen(
        self,
        event
    ):

        position = self.live_execution_position_for_event(
            event
        )

        if not position:
            return False

        event_key = self.live_execution_event_key(
            event
        )

        for order in position.get("live_execution_orders", []):
            if order.get("event_key") != event_key:
                continue

            if order.get("submitted") or order.get("dry_run"):
                return True

            if order.get("side") in ("buy", "sell"):
                continue

            return True

        return False

    def record_live_execution_result(
        self,
        event,
        result
    ):

        position = self.live_execution_position_for_event(
            event
        )

        if not position:
            return False

        result = dict(
            result or {}
        )
        event_key = self.live_execution_event_key(
            event
        )
        orders = position.setdefault(
            "live_execution_orders",
            []
        )

        for order in orders:
            if order.get("event_key") != event_key:
                continue

            if order.get("submitted"):
                return False

            if not result.get("submitted"):
                return False

        record = {
            "event_key": event_key,
            "event_type": event.get("type", ""),
            "timestamp": event.get("timestamp"),
            "provider": result.get("provider", ""),
            "side": result.get("side", ""),
            "qty": result.get("qty", ""),
            "order_qty": result.get("order_qty", ""),
            "order_value_usd": safe_float(
                result.get("order_value_usd"),
                0
            ),
            "contra_asset": result.get("contra_asset", ""),
            "contra_asset_usd": safe_float(
                result.get("contra_asset_usd")
                or result.get("contra_asset_price_usd"),
                0
            ),
            "dry_run": bool(result.get("dry_run")),
            "submitted": bool(result.get("submitted")),
            "skipped": bool(result.get("skipped")),
            "reconciled": bool(result.get("reconciled")),
            "reason": result.get("reason", ""),
            "order_id": result.get("order_id", ""),
            "filled_target_amount": safe_float(
                result.get("filled_target_amount"),
                0
            ),
            "filled_contra_amount": safe_float(
                result.get("filled_contra_amount"),
                0
            ),
            "average_fill_price": safe_float(
                result.get("average_fill_price"),
                0
            ),
            "average_notional_price": safe_float(
                result.get("average_notional_price"),
                0
            ),
            "quote_ok": bool(result.get("quote_ok")),
            "quote_price_impact": safe_float(
                result.get("quote_price_impact"),
                0
            ),
            "submit_status": result.get("submit_status"),
            "submit_error": result.get("submit_error", "")
        }
        orders.append(record)

        if len(orders) > 50:
            del orders[:-50]

        if event.get("type") == "entry":
            filled_target_amount = safe_float(
                result.get("filled_target_amount"),
                0
            )
            filled_contra_amount = safe_float(
                result.get("filled_contra_amount"),
                0
            )
            contra_asset_usd = safe_float(
                result.get("contra_asset_usd")
                or result.get("contra_asset_price_usd"),
                0
            )
            average_notional_price = safe_float(
                result.get("average_notional_price"),
                0
            )
            average_fill_price = safe_float(
                result.get("average_fill_price"),
                0
            )
            entry_notional_usd = safe_float(
                result.get("order_value_usd"),
                safe_float(
                    result.get("qty"),
                    event.get("entry_notional_usd")
                )
            )

            if (
                result.get("submitted")
                and filled_contra_amount > 0
                and contra_asset_usd > 0
            ):
                entry_notional_usd = (
                    filled_contra_amount
                    * contra_asset_usd
                )

            position["live_execution_entry_attempted"] = True
            position["live_execution_entry_submitted"] = bool(
                result.get("submitted")
            )
            position["live_execution_entry_order_id"] = result.get(
                "order_id",
                ""
            )
            position[
                "live_execution_entry_notional_usd"
            ] = entry_notional_usd
            position[
                "live_execution_entry_filled_target_amount"
            ] = filled_target_amount
            position[
                "live_execution_entry_filled_contra_amount"
            ] = filled_contra_amount
            position[
                "live_execution_entry_average_price"
            ] = average_fill_price
            position[
                "live_execution_entry_average_notional_price"
            ] = average_notional_price

            if average_notional_price > 0:
                position[
                    "live_execution_entry_fill_price_usd"
                ] = average_notional_price

            if contra_asset_usd > 0:
                position[
                    "live_execution_entry_contra_asset_usd"
                ] = contra_asset_usd
                position["entry_contra_asset_usd"] = (
                    contra_asset_usd
                )

            entry_price = safe_float(
                event.get("last_price"),
                event.get("entry_price")
            )

            if (
                result.get("submitted")
            ):
                entry_tokens = filled_target_amount

                if entry_tokens <= 0 and entry_price > 0:
                    entry_tokens = (
                        entry_notional_usd
                        / entry_price
                    )

                if entry_tokens > 0:
                    position[
                        "live_execution_entry_tokens_estimated"
                    ] = entry_tokens
                    position[
                        "live_execution_remaining_tokens_estimated"
                    ] = entry_tokens

        if (
            result.get("submitted")
            and result.get("side") == "sell"
        ):
            sold_tokens = safe_float(
                result.get("filled_target_amount"),
                0
            )

            if sold_tokens <= 0:
                sold_tokens = safe_float(
                    result.get("qty"),
                    0
                )

            remaining_tokens = safe_float(
                position.get("live_execution_remaining_tokens_estimated"),
                event.get("live_execution_remaining_tokens_estimated")
            )

            if remaining_tokens > 0:
                position[
                    "live_execution_remaining_tokens_estimated"
                ] = max(
                    remaining_tokens - sold_tokens,
                    0
                )

        if (
            event.get("type") == "live_scale_out"
        ):
            position["live_execution_initials_attempted"] = True

            if result.get("submitted"):
                position["live_execution_initials_pending"] = False
                position["live_execution_initials_taken"] = True
                position["live_execution_initials_taken_at"] = event.get(
                    "timestamp"
                )
                position["live_execution_initials_order_id"] = result.get(
                    "order_id",
                    ""
                )
                position[
                    "live_execution_initials_proceeds_usd"
                ] = safe_float(
                    event.get("proceeds_usd"),
                    0
                )
            elif not result.get("dry_run"):
                position["live_execution_initials_pending"] = True

        if event.get("type") == "close":
            position["live_execution_closed"] = bool(
                result.get("submitted")
            )

        self.state_dirty = True
        self.save_state()
        return True

    def update_live_execution_entry_fill(
        self,
        event,
        fill_amounts
    ):
        position = self.live_execution_position_for_event(event)

        if not position:
            return False

        if not position.get("live_execution_entry_submitted"):
            return False

        fill_amounts = fill_amounts or {}
        filled_target = safe_float(fill_amounts.get("target"), 0)
        filled_contra = safe_float(fill_amounts.get("contra"), 0)
        avg_price = safe_float(fill_amounts.get("average_price"), 0)
        avg_notional = safe_float(fill_amounts.get("average_notional_price"), 0)

        if filled_target > 0:
            position["live_execution_entry_filled_target_amount"] = filled_target
            position["live_execution_entry_tokens_estimated"] = filled_target
            position["live_execution_remaining_tokens_estimated"] = max(
                filled_target
                - safe_float(
                    position.get("live_execution_entry_tokens_estimated", 0), 0
                )
                + filled_target,
                filled_target,
            )
            position["live_execution_remaining_tokens_estimated"] = filled_target

        if filled_contra > 0:
            position["live_execution_entry_filled_contra_amount"] = filled_contra

        if avg_price > 0:
            position["live_execution_entry_average_price"] = avg_price

        if avg_notional > 0:
            position["live_execution_entry_average_notional_price"] = avg_notional
            position["live_execution_entry_fill_price_usd"] = avg_notional

        self.state_dirty = True
        self.save_state()
        return True

    def mark_live_execution_reconciled_entry(
        self,
        event,
        reason,
        *,
        live_balance=0,
        order_id="",
        order_value_usd=0
    ):

        position = self.live_execution_position_for_event(
            event
        )

        if not position:
            return False

        entry_event = self.live_execution_entry_event(
            position
        ) or {}
        entry_timestamp = safe_float(
            entry_event.get("timestamp"),
            position.get("entry_at")
        )
        event_key = self.live_execution_event_key({
            "type": "entry",
            "address": position.get("address", ""),
            "timestamp": entry_timestamp,
            "reason": "position entry",
            "size_pct": 0,
            "proceeds_usd": 0
        })
        orders = position.setdefault(
            "live_execution_orders",
            []
        )

        if not any(
            order.get("event_key") == event_key
            and order.get("submitted")
            for order in orders
        ):
            orders.append({
                "event_key": event_key,
                "event_type": "entry",
                "timestamp": entry_timestamp,
                "provider": "definitive",
                "side": "buy",
                "qty": str(
                    order_value_usd
                    or position.get("entry_notional_usd", 0)
                ),
                "order_qty": str(live_balance),
                "order_value_usd": safe_float(
                    order_value_usd,
                    position.get("entry_notional_usd", 0)
                ),
                "contra_asset": "",
                "contra_asset_usd": safe_float(
                    position.get("entry_contra_asset_usd") or position.get("entry_contra_asset_price_usd"),
                    0
                ),
                "dry_run": False,
                "submitted": True,
                "skipped": False,
                "reconciled": True,
                "reason": reason,
                "order_id": order_id,
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
            })

        live_balance = safe_float(
            live_balance,
            0
        )
        entry_notional_usd = safe_float(
            order_value_usd,
            position.get("live_execution_entry_notional_usd")
            or position.get("entry_notional_usd")
        )

        position["live_execution_entry_attempted"] = True
        position["live_execution_entry_submitted"] = True
        position["live_execution_entry_order_id"] = (
            order_id
            or position.get("live_execution_entry_order_id", "")
        )
        position["live_execution_entry_notional_usd"] = (
            entry_notional_usd
        )
        position["live_execution_entry_contra_asset_usd"] = (
            safe_float(
                position.get("live_execution_entry_contra_asset_usd"),
                position.get("entry_contra_asset_usd")
                or position.get("entry_contra_asset_price_usd")
                or position.get("entry_sol_usd")
            )
        )
        position["live_execution_entry_filled_target_amount"] = (
            live_balance
        )
        position["live_execution_entry_tokens_estimated"] = (
            live_balance
        )
        position["live_execution_remaining_tokens_estimated"] = (
            live_balance
        )
        position["live_execution_entry_reconciled"] = True
        position["live_execution_entry_reconciled_reason"] = reason
        position["live_execution_entry_reconciled_at"] = time.time()
        if not position.get("entry_contra_asset_usd"):
            position["entry_contra_asset_usd"] = position.get(
                "live_execution_entry_contra_asset_usd",
                position.get("entry_contra_asset_price_usd")
            ) or position.get("entry_sol_usd")

        self.state_dirty = True
        self.save_state()
        return True

    def mark_live_execution_entry_silent_fill(
        self,
        event,
        reason
    ):
        """Definitive reported the buy ORDER_STATUS_FILLED but with zero
        filled amounts, and a /positions check found no token balance —
        i.e. no tokens were actually acquired. Un-arm the live position
        so doomed exit sells are never attempted, and disable retry to
        avoid a double-buy against an order the venue believes filled.
        The user is alerted to verify manually."""

        position = self.live_execution_position_for_event(
            event
        )

        if not position:
            return False

        position["live_execution_entry_submitted"] = False
        position["live_execution_entry_silent_fill"] = True
        position["live_execution_entry_silent_fill_reason"] = reason
        position["live_execution_entry_silent_fill_at"] = time.time()
        position["live_execution_retry_disabled"] = True
        position["live_execution_entry_filled_target_amount"] = 0
        position["live_execution_entry_tokens_estimated"] = 0
        position["live_execution_remaining_tokens_estimated"] = 0

        self.state_dirty = True
        self.save_state()
        return True

    def mark_live_execution_reconciled_closed(
        self,
        event,
        reason,
        *,
        live_balance=0
    ):

        position = self.live_execution_position_for_event(
            event
        )

        if not position:
            return False

        event_key = self.live_execution_event_key(
            event
        )
        orders = position.setdefault(
            "live_execution_orders",
            []
        )

        if not any(
            order.get("event_key") == event_key
            and order.get("reconciled")
            for order in orders
        ):
            orders.append({
                "event_key": event_key,
                "event_type": event.get("type", ""),
                "timestamp": event.get("timestamp"),
                "provider": "definitive",
                "side": "sell",
                "qty": event.get("live_execution_sell_tokens", ""),
                "order_qty": event.get("live_execution_sell_tokens", ""),
                "order_value_usd": safe_float(
                    event.get("proceeds_usd"),
                    0
                ),
                "contra_asset": event.get(
                    "live_execution_contra_asset",
                    ""
                ),
                "contra_asset_usd": safe_float(
                    event.get("live_execution_contra_asset_usd")
                    or event.get("live_execution_contra_asset_price_usd"),
                    0
                ),
                "dry_run": False,
                "submitted": False,
                "skipped": True,
                "reconciled": True,
                "reason": reason,
                "order_id": "",
                "quote_ok": False,
                "quote_price_impact": 0,
                "submit_status": None,
                "submit_error": reason,
                "live_balance": safe_float(live_balance, 0)
            })

        position["live_execution_closed"] = True
        position["live_execution_remaining_tokens_estimated"] = 0
        position["live_execution_reconciled_closed"] = True
        position["live_execution_reconciled_closed_reason"] = reason
        position["live_execution_reconciled_closed_at"] = safe_float(
            event.get("timestamp"),
            time.time()
        )
        position["live_execution_reconciled_live_balance"] = safe_float(
            live_balance,
            0
        )

        self.state_dirty = True
        self.save_state()
        return True

    def live_initial_take_profit_event(
        self,
        position,
        metrics,
        pressure,
        now,
        ignition_details=None
    ):

        if not DEFINITIVE_INITIAL_TAKE_PROFIT_ENABLED:
            return None

        if not position.get("live_execution_entry_submitted"):
            return None

        if position.get("live_execution_initials_taken"):
            return None

        if position.get("live_execution_initials_pending"):
            return None

        price = safe_float(
            metrics.price,
            0
        )
        entry_price = safe_float(
            position.get("entry_price"),
            0
        )

        if price <= 0 or entry_price <= 0:
            return None

        price_multiple = price / entry_price

        if (
            price_multiple + 1e-9
            < DEFINITIVE_INITIAL_TAKE_PROFIT_MULTIPLE
        ):
            return None

        live_entry_notional = safe_float(
            position.get("live_execution_entry_notional_usd"),
            position.get("entry_notional_usd")
        )
        target_recovery = (
            live_entry_notional
            * max(
                DEFINITIVE_INITIAL_TAKE_PROFIT_RECOVERY_PCT,
                0
            )
        )

        if target_recovery <= 0:
            return None

        estimated_remaining_tokens = safe_float(
            position.get("live_execution_remaining_tokens_estimated"),
            position.get("remaining_tokens")
        )

        if estimated_remaining_tokens <= 0:
            return None

        sell_tokens = min(
            target_recovery / price,
            estimated_remaining_tokens
        )
        proceeds_usd = sell_tokens * price

        if proceeds_usd < DEFINITIVE_INITIAL_TAKE_PROFIT_MIN_NOTIONAL_USD:
            return None

        entry_size_tokens = safe_float(
            position.get("entry_size_tokens"),
            0
        )
        size_pct = (
            sell_tokens / entry_size_tokens
            if entry_size_tokens > 0
            else 0
        )
        sol_usd = self.current_sol_usd()
        reason = (
            "live_take_initials_"
            f"{DEFINITIVE_INITIAL_TAKE_PROFIT_MULTIPLE:.2f}x"
        )
        event = self.build_notification_event(
            "live_scale_out",
            position,
            metrics,
            pressure,
            reason,
            size_pct=size_pct,
            proceeds_usd=proceeds_usd,
            proceeds_sol=proceeds_usd / max(sol_usd, 1e-18),
            sol_usd=sol_usd,
            ignition_details=ignition_details or {}
        )
        event["live_only_event"] = True
        event["live_take_initials"] = True
        event["live_execution_sell_tokens"] = sell_tokens
        event[
            "live_execution_remaining_tokens_estimated"
        ] = estimated_remaining_tokens
        event["target_recovery_usd"] = target_recovery
        event["price_multiple"] = price_multiple

        return event

    def handle_missing_pair(
        self,
        token_address,
        now
    ):

        state = self.load_state()
        position = state["open"].get(
            str(token_address)
        )

        if not position:
            return None

        missing_count = (
            int(position.get("missing_pair_count", 0))
            + 1
        )
        position["missing_pair_count"] = missing_count
        position["last_update_at"] = now

        reason = (
            "pair_missing"
            if missing_count < POSITION_MISSING_PAIR_ALERT_SCANS
            else "pair_missing_repeated"
        )

        self.add_event(
            position,
            "risk",
            now,
            position.get("last_price", 0),
            position.get("last_pressure", 0),
            reason
        )

        self.save_state()

        if missing_count not in (
            1,
            POSITION_MISSING_PAIR_ALERT_SCANS
        ):
            return None

        return self.build_position_event(
            "risk",
            position,
            reason
        )

    def reset_state(self):

        self.state = {
            "starting_balance_sol": (
                POSITION_INITIAL_BALANCE_SOL
            ),
            "cash_sol": POSITION_INITIAL_BALANCE_SOL,
            "open": {},
            "closed": [],
            "entry_confirmation_watch": {},
            "rsi_entry_watch": {},
            "trailing_rebound_watch": {}
        }
        self.state_dirty = False

        return self.state

    def handle_scan(
        self,
        metrics,
        ignition_score,
        ignition_details,
        now,
        pressure=None,
        recent_snapshots=None
    ):

        if not POSITION_ENABLED:
            return []

        if metrics.price <= 0:
            return []

        state = self.load_state()
        address = str(metrics.address)
        position = state["open"].get(address)
        if pressure is None:
            pressure = self.calculate_pressure(
                metrics,
                ignition_details
            )

        recent_snapshots = recent_snapshots or []

        if position:
            return self.manage_position(
                position,
                metrics,
                ignition_details,
                pressure,
                now,
                recent_snapshots
            )

        entry_block_reason = self.entry_block_reason(
            metrics,
            ignition_score,
            ignition_details,
            now=now,
            recent_snapshots=recent_snapshots
        )

        if entry_block_reason:
            symbol = getattr(metrics, "symbol", None) or str(
                metrics.address
            )[:8]
            print(
                f"ENTRY BLOCKED {symbol} "
                f"route={ignition_details.get('alert_route','?')} "
                f"score={ignition_score} "
                f"reason={entry_block_reason}"
            )
            if self.state_dirty:
                self.save_state()
            return []

        entry_size_sol = self.entry_size_sol(
            metrics,
            ignition_details
        )

        if entry_size_sol <= 0:
            print(
                "PAPER TRADE SKIPPED "
                f"{metrics.symbol} "
                "entry size is zero"
            )
            return []

        if len(state["open"]) >= POSITION_MAX_OPEN_POSITIONS:
            print(
                "PAPER TRADE SKIPPED "
                f"{metrics.symbol} "
                "max open positions reached"
            )
            return []

        if (
            safe_float(state.get("cash_sol"), 0)
            + 1e-9
            < entry_size_sol
        ):
            print(
                "PAPER TRADE SKIPPED "
                f"{metrics.symbol} "
                "insufficient SOL balance "
                f"cash={safe_float(state.get('cash_sol'), 0):.2f}"
            )
            return []

        event = self.open_position(
            metrics,
            ignition_score,
            ignition_details,
            pressure,
            now,
            entry_size_sol
        )

        return [event]

    def entry_signal(
        self,
        metrics,
        ignition_score,
        ignition_details,
        recent_snapshots=None
    ):

        return self.entry_block_reason(
            metrics,
            ignition_score,
            ignition_details,
            recent_snapshots=recent_snapshots
        ) is None

    def entry_block_reason(
        self,
        metrics,
        ignition_score,
        ignition_details,
        now=None,
        recent_snapshots=None
    ):

        impulse = safe_float(
            ignition_details.get("price_jump"),
            0
        )
        hyperevm_entry = self.hyperevm_entry(
            metrics,
            ignition_details
        )
        min_ignition_score = (
            HYPEREVM_IGNITION_SCORE
            if hyperevm_entry
            else IGNITION_ALERT_THRESHOLD
        )

        alert_route = ignition_details.get(
            "alert_route", ""
        ) or ""

        route_min_scores = {
            "bonding_early_revival": (
                POSITION_EARLY_REVIVAL_MIN_ENTRY_SCORE
            ),
            "migrated_revival": (
                POSITION_MIGRATED_REVIVAL_MIN_ENTRY_SCORE
            ),
            "immediate": (
                POSITION_IMMEDIATE_MIN_ENTRY_SCORE
            ),
        }

        effective_min = max(
            min_ignition_score,
            route_min_scores.get(alert_route, 0)
        )

        if ignition_score < effective_min:
            return "score_below_threshold"

        if not ignition_details.get(
            "alert_eligible",
            False
        ):
            return "alert_not_eligible"

        if self.entry_count_limit_reached(
            metrics.address,
            now
        ):
            return "token_reentry_hourly_limit"

        reentry = self.is_reentry(metrics.address)

        lineage_reason = self.lineage_entry_block_reason(
            metrics,
            ignition_details
        )

        if lineage_reason:
            return lineage_reason

        if now is not None and reentry:
            if self.reentry_cooldown_active(
                metrics.address,
                now
            ):
                return "reentry_cooldown_active"

            trailing_rebound_reason = (
                self.trailing_rebound_reentry_block_reason(
                    metrics,
                    ignition_details,
                    now
                )
            )

            if trailing_rebound_reason:
                return trailing_rebound_reason

        migration_fdv_reason = (
            self.migration_fdv_entry_block_reason(metrics)
        )

        # In-migration-zone block removed for entry 2026-05-29: the danger-zone
        # study showed entries within +/-$7k of migration FDV are tail-rich (3x
        # rate 28.8%, mean peak 3.25x), so we no longer block them. The
        # above-limit ceiling (FDV well past migration) is still enforced, and
        # the zone is still used for exit/trail logic elsewhere.
        if (
            not hyperevm_entry
            and migration_fdv_reason
            and migration_fdv_reason != "migration_fdv_zone"
        ):
            return migration_fdv_reason

        if self.fdv_above_entry_max(metrics, ignition_details):
            return "fdv_above_entry_max"

        if self.fdv_below_entry_min(metrics):
            return "fdv_below_entry_min"

        entry_signal_penalty = safe_float(
            ignition_details.get("signal_penalty"), 0
        )

        max_penalty = (
            POSITION_EARLY_REVIVAL_MAX_ENTRY_PENALTY
            if alert_route == "bonding_early_revival"
            else POSITION_MIGRATED_REVIVAL_MAX_ENTRY_PENALTY
            if alert_route == "migrated_revival"
            else POSITION_HC_MAX_ENTRY_PENALTY
            if alert_route == "bonding_momentum_high_conviction"
            else POSITION_MAX_ENTRY_PENALTY
        )

        if (
            not hyperevm_entry
            and max_penalty > 0
            and entry_signal_penalty >= max_penalty
        ):
            return "penalty_too_high"

        missing_fields = (
            ignition_details.get("missing") or []
        )

        if (
            not hyperevm_entry
            and (
                "5m_buy_sell" in missing_fields
                or "h1_buy_sell" in missing_fields
            )
        ):
            return "critical_fields_missing"

        if reentry:
            reentry_state_reason = self.reentry_state_block_reason(
                metrics
            )

            if reentry_state_reason:
                return reentry_state_reason

        if hyperevm_entry:
            hyperevm_reason = self.hyperevm_entry_block_reason(
                metrics,
                ignition_details
            )

            if hyperevm_reason:
                return hyperevm_reason

            confirmation_reason = self.entry_confirmation_block_reason(
                metrics,
                ignition_details,
                now=now
            )

            if confirmation_reason:
                return confirmation_reason

            return None

        if impulse < POSITION_MIN_ENTRY_IMPULSE:
            return "impulse_below_min"

        if impulse > POSITION_MAX_ENTRY_IMPULSE:
            return "impulse_too_hot"

        price_change_1h = safe_float(
            getattr(metrics, "price_change_1h", 0), 0
        )

        min_1h = (
            POSITION_EARLY_REVIVAL_MIN_ENTRY_PRICE_CHANGE_1H
            if alert_route == "bonding_early_revival"
            else POSITION_MIGRATED_REVIVAL_MIN_ENTRY_PRICE_CHANGE_1H
            if alert_route == "migrated_revival"
            else POSITION_MIN_ENTRY_PRICE_CHANGE_1H
        )

        if price_change_1h < min_1h:
            return "1h_price_change_below_min"

        price_change_5m = safe_float(
            getattr(metrics, "price_change_5m", 0), 0
        )

        min_5m = (
            POSITION_EARLY_REVIVAL_MIN_ENTRY_PRICE_CHANGE_5M
            if alert_route == "bonding_early_revival"
            else POSITION_MIGRATED_REVIVAL_MIN_ENTRY_PRICE_CHANGE_5M
            if alert_route == "migrated_revival"
            else POSITION_MIN_ENTRY_PRICE_CHANGE_5M
        )

        if price_change_5m < min_5m:
            return "5m_price_change_below_min"

        max_5m = (
            POSITION_HC_MAX_ENTRY_PRICE_CHANGE_5M
            if alert_route == "bonding_momentum_high_conviction"
            else POSITION_EARLY_REVIVAL_MAX_ENTRY_PRICE_CHANGE_5M
            if alert_route == "bonding_early_revival"
            else POSITION_MAX_ENTRY_PRICE_CHANGE_5M
        )

        if price_change_5m > max_5m:
            return "5m_price_change_too_hot"

        chop_reason = self.chop_entry_block_reason(
            metrics,
            ignition_details,
            recent_snapshots
        )

        if chop_reason:
            return chop_reason

        if (
            safe_float(metrics.volume_1h, 0)
            <= self.min_entry_volume_1h_usd(alert_route)
        ):
            return "1h_volume_below_min"

        quality_volume_reason = (
            self.quality_volume_entry_block_reason(
                metrics,
                ignition_details
            )
        )

        if quality_volume_reason:
            return quality_volume_reason

        flow_volume_reason = (
            self.entry_buy_sell_volume_block_reason(metrics)
        )

        if flow_volume_reason:
            return flow_volume_reason

        if (
            reentry
            and safe_float(metrics.buy_volume_5m, 0)
            < POSITION_REENTRY_MIN_VOLUME_5M_USD
        ):
            return "reentry_5m_volume_below_min"

        anchored_vwap_reason = (
            self.anchored_vwap_entry_block_reason(
                metrics,
                ignition_details
            )
        )

        if anchored_vwap_reason:
            return anchored_vwap_reason

        confirmation_reason = self.entry_confirmation_block_reason(
            metrics,
            ignition_details,
            now=now
        )

        if confirmation_reason:
            return confirmation_reason

        return None

    def hyperevm_entry(
        self,
        metrics,
        ignition_details
    ):

        chain = str(
            getattr(metrics, "chain", "")
            or ""
        ).lower()

        return (
            self.hyperevm_chain(chain)
            or ignition_details.get("hyperevm_ignition")
            or ignition_details.get("alert_route")
            in (
                "hyperevm_ignition",
                "hyperevm_slow_cook"
            )
        )

    def hyperevm_chain(
        self,
        chain
    ):

        return str(
            chain or ""
        ).lower() in (
            "hyperevm",
            "hyperliquid"
        )

    def hyperevm_entry_block_reason(
        self,
        metrics,
        ignition_details
    ):

        if (
            safe_float(metrics.liquidity, 0)
            < HYPEREVM_IGNITION_MIN_LIQUIDITY_USD
        ):
            return "hyperevm_liquidity_below_min"

        fdv = safe_float(metrics.fdv, 0)

        if (
            not fdv
            or fdv > HYPEREVM_IGNITION_MAX_FDV_USD
        ):
            return "hyperevm_fdv_above_max"

        if (
            safe_float(metrics.price_change_5m, 0)
            < HYPEREVM_IGNITION_MIN_PRICE_CHANGE_5M
        ):
            return "hyperevm_5m_price_below_min"

        if (
            safe_float(metrics.price_change_24h, 0)
            < HYPEREVM_IGNITION_MIN_PRICE_CHANGE_24H
        ):
            return "hyperevm_24h_price_below_min"

        if (
            safe_float(metrics.volume_1h, 0)
            < HYPEREVM_IGNITION_MIN_VOLUME_1H_USD
        ):
            return "hyperevm_1h_volume_below_min"

        flow_reason = self.entry_buy_sell_volume_block_reason(
            metrics,
            allow_unavailable=True
        )

        if flow_reason:
            return flow_reason

        runner_vwap = safe_float(
            ignition_details.get("runner_vwap"),
            0
        )

        runner_price = safe_float(
            ignition_details.get("runner_price"),
            metrics.price
        )

        if runner_vwap > 0 and runner_price <= runner_vwap:
            return "hyperevm_price_below_15m_vwap"

        return None

    def lineage_entry_block_reason(
        self,
        metrics,
        ignition_details
    ):

        if not POSITION_LINEAGE_EXPOSURE_BLOCK_ENABLED:
            return None

        state = self.load_state()
        open_positions = state.get("open", {})

        if not open_positions:
            return None

        ticker = normalize_ticker(
            metrics.symbol
        )
        address = str(metrics.address)
        related_addresses = {
            address
        }

        for key in (
            "lineage_addresses",
            "ticker_lineage_addresses",
            "lineage_contract_addresses"
        ):
            related_addresses.update(
                list_from_value(
                    ignition_details.get(key)
                )
            )

        for position in open_positions.values():
            position_address = str(
                position.get("address", "")
            )

            if position_address == address:
                continue

            if (
                position_address in related_addresses
                or (
                    ticker
                    and normalize_ticker(
                        position.get("symbol")
                    ) == ticker
                )
            ):
                return "lineage_open_exposure"

        return None

    def min_entry_volume_1h_usd(
        self,
        alert_route=""
    ):

        if alert_route == "bonding_early_revival":
            return POSITION_EARLY_REVIVAL_MIN_ENTRY_VOLUME_1H_USD

        if alert_route == "migrated_revival":
            return POSITION_MIGRATED_REVIVAL_MIN_ENTRY_VOLUME_1H_USD

        if alert_route == "bonding_momentum_high_conviction":
            return POSITION_HC_MIN_ENTRY_VOLUME_1H_USD

        return POSITION_MIN_ENTRY_VOLUME_1H_USD

    def min_entry_volume_multiple(
        self,
        alert_route=""
    ):

        if alert_route == "bonding_early_revival":
            return POSITION_EARLY_REVIVAL_MIN_ENTRY_VOLUME_MULTIPLE

        if alert_route == "migrated_revival":
            return POSITION_MIGRATED_REVIVAL_MIN_ENTRY_VOLUME_MULTIPLE

        if alert_route == "bonding_momentum_high_conviction":
            return POSITION_HC_MIN_ENTRY_VOLUME_MULTIPLE

        return POSITION_MIN_ENTRY_VOLUME_MULTIPLE

    def entry_volume_multiple(
        self,
        metrics,
        ignition_details=None
    ):

        alert_route = ""

        if ignition_details is not None:
            alert_route = str(
                ignition_details.get("alert_route", "") or ""
            )

        threshold = self.min_entry_volume_1h_usd(alert_route)

        return (
            safe_float(metrics.volume_1h, 0)
            / max(threshold, 1e-18)
        )

    def entry_buy_sell_volume_ratio(
        self,
        metrics
    ):

        buy_volume = safe_float(
            getattr(metrics, "buy_volume_5m", 0),
            0
        )
        sell_volume = safe_float(
            getattr(metrics, "sell_volume_5m", 0),
            0
        )

        if buy_volume <= 0 and sell_volume <= 0:
            return 0

        if sell_volume <= 0:
            return 999 if buy_volume > 0 else 0

        return buy_volume / sell_volume

    def observed_buy_sell_volume_ready(
        self,
        metrics
    ):

        if not POSITION_REQUIRE_OBSERVED_BUY_SELL_VOLUME:
            return True

        return (
            getattr(metrics, "buy_sell_volume_source_5m", "")
            == "observed_flows"
        )

    def effective_buy_sell_volume_ratio(
        self,
        metrics,
        ignition_details
    ):

        if not self.observed_buy_sell_volume_ready(metrics):
            return 0

        return safe_float(
            ignition_details.get(
                "flow_buy_sell_ratio",
                ignition_details.get("buy_sell_volume_ratio")
            ),
            self.entry_buy_sell_volume_ratio(metrics)
        )

    def entry_buy_sell_volume_block_reason(
        self,
        metrics,
        allow_unavailable=False
    ):

        min_ratio = safe_float(
            POSITION_MIN_ENTRY_BUY_SELL_VOLUME_RATIO,
            0
        )

        if min_ratio <= 0:
            return None

        if not self.observed_buy_sell_volume_ready(metrics):
            if allow_unavailable:
                return None
            return "5m_buy_sell_dollar_flow_unavailable"

        if self.entry_buy_sell_volume_ratio(metrics) < min_ratio:
            return "5m_buy_sell_volume_ratio_below_min"

        return None

    def entry_confirmation_vwap_context(
        self,
        metrics,
        ignition_details
    ):

        if self.hyperevm_entry(metrics, ignition_details):
            vwap = safe_float(
                ignition_details.get("runner_vwap"),
                0
            )
            price = safe_float(
                ignition_details.get("runner_price"),
                metrics.price
            )
            return price, vwap, vwap > 0, "15m_vwap"

        vwap = safe_float(
            ignition_details.get("anchored_vwap"),
            0
        )
        price = safe_float(
            ignition_details.get("anchored_vwap_price"),
            metrics.price
        )

        return (
            price,
            vwap,
            bool(ignition_details.get("anchored_vwap_ready")) and vwap > 0,
            "1h_low_avwap"
        )

    def entry_confirmation_score(
        self,
        metrics,
        ignition_details
    ):

        score = 0
        reasons = []
        breakdown = {}
        missing = set(
            list_from_value(ignition_details.get("missing"))
            + list_from_value(ignition_details.get("data_missing"))
        )
        critical_missing = sorted(
            field
            for field in missing
            if field
            in {
                "5m_price",
                "5m_volume",
                "5m_vol_liq",
                "5m_buy_sell",
                "5m_buy_sell_dollar_flow",
                "1h_volume"
            }
        )

        data_score = 0

        if (
            safe_float(metrics.price, 0) > 0
            and safe_float(metrics.liquidity, 0) > 0
            and safe_float(metrics.volume_1h, 0) > 0
        ):
            data_score += 8
        else:
            reasons.append("incomplete_market_data")

        if not critical_missing:
            data_score += 6
        else:
            reasons.append(
                "missing_" + "_".join(critical_missing[:2])
            )

        if self.observed_buy_sell_volume_ready(metrics):
            data_score += 6
        else:
            reasons.append("flow_unavailable")

        breakdown["data"] = data_score
        score += data_score

        flow_score = 0
        buy_sell_ratio = self.effective_buy_sell_volume_ratio(
            metrics,
            ignition_details
        )
        buy_volume = safe_float(
            getattr(metrics, "buy_volume_5m", 0),
            0
        )

        if (
            buy_sell_ratio
            >= POSITION_ENTRY_CONFIRMATION_MIN_BUY_SELL_RATIO
        ):
            flow_score += 15
        else:
            reasons.append("buy_sell_flow_below_min")

        if buy_volume >= POSITION_ENTRY_CONFIRMATION_MIN_BUY_VOLUME_5M_USD:
            flow_score += 10
        else:
            reasons.append("buy_volume_below_min")

        breakdown["flow"] = flow_score
        score += flow_score

        structure_score = 0
        (
            vwap_price,
            vwap,
            vwap_ready,
            vwap_source
        ) = self.entry_confirmation_vwap_context(
            metrics,
            ignition_details
        )
        vwap_distance_pct = 0

        if vwap_ready and vwap_price > 0 and vwap > 0:
            vwap_distance_pct = (
                vwap_price / vwap
                - 1
            )
            structure_score += 5

            if vwap_price >= vwap:
                structure_score += 12

                if (
                    vwap_distance_pct
                    <= POSITION_ENTRY_CONFIRMATION_MAX_VWAP_DISTANCE_PCT
                ):
                    structure_score += 8
                else:
                    reasons.append("price_too_stretched_above_vwap")
            else:
                reasons.append("price_below_vwap")
        else:
            reasons.append("vwap_not_ready")

        breakdown["structure"] = structure_score
        score += structure_score

        persistence_score = 0
        pressure = safe_float(
            ignition_details.get("pressure"),
            0
        )

        if pressure >= POSITION_ENTRY_CONFIRMATION_MIN_PRESSURE:
            persistence_score += 10
        else:
            reasons.append("pressure_below_min")

        if (
            safe_float(metrics.price_change_5m, 0)
            >= POSITION_ENTRY_CONFIRMATION_MIN_PRICE_CHANGE_5M
        ):
            persistence_score += 5
        else:
            reasons.append("5m_price_change_below_min")

        if (
            safe_float(metrics.price_change_1h, 0)
            >= POSITION_ENTRY_CONFIRMATION_MIN_PRICE_CHANGE_1H
        ):
            persistence_score += 5
        else:
            reasons.append("1h_price_change_below_min")

        breakdown["persistence"] = persistence_score
        score += persistence_score

        liquidity_score = 0
        volume_liquidity_ratio = safe_float(
            ignition_details.get("volume_liquidity_ratio"),
            0
        )

        if (
            volume_liquidity_ratio
            >= POSITION_ENTRY_CONFIRMATION_MIN_VOLUME_LIQUIDITY_RATIO
        ):
            liquidity_score += 5
        else:
            reasons.append("volume_liquidity_below_min")

        if (
            POSITION_ENTRY_CONFIRMATION_MAX_VOLUME_LIQUIDITY_RATIO <= 0
            or volume_liquidity_ratio
            <= POSITION_ENTRY_CONFIRMATION_MAX_VOLUME_LIQUIDITY_RATIO
        ):
            liquidity_score += 5
        else:
            reasons.append("volume_liquidity_too_hot")

        breakdown["liquidity"] = liquidity_score
        score += liquidity_score

        passed = score >= POSITION_ENTRY_CONFIRMATION_MIN_SCORE

        if not reasons and not passed:
            reasons.append("score_below_min")

        return {
            "score": round(score, 2),
            "passed": passed,
            "reason": "entry_confirmation_ok" if passed else reasons[0],
            "reasons": reasons,
            "breakdown": breakdown,
            "vwap_ready": vwap_ready,
            "vwap": vwap,
            "vwap_price": vwap_price,
            "vwap_source": vwap_source,
            "vwap_distance_pct": vwap_distance_pct,
            "buy_sell_ratio": buy_sell_ratio,
            "buy_volume_5m": buy_volume,
            "volume_liquidity_ratio": volume_liquidity_ratio,
            "pressure": pressure
        }

    def entry_confirmation_block_reason(
        self,
        metrics,
        ignition_details,
        now=None
    ):

        if not POSITION_ENTRY_CONFIRMATION_ENABLED:
            ignition_details["entry_confirmation_enabled"] = False
            return None

        score_data = self.entry_confirmation_score(
            metrics,
            ignition_details
        )
        required_scans = max(
            int(POSITION_ENTRY_CONFIRMATION_REQUIRED_SCANS),
            1
        )
        now_value = safe_float(now, 0)

        if now_value <= 0:
            ready = (
                score_data["passed"]
                and required_scans <= 1
            )
            reason = (
                "entry_confirmation_waiting"
                if score_data["passed"] and not ready
                else score_data["reason"]
            )
            ignition_details.update({
                "entry_confirmation_enabled": True,
                "entry_confirmation_shadow_mode": (
                    POSITION_ENTRY_CONFIRMATION_SHADOW_MODE
                ),
                "entry_confirmation_score": score_data["score"],
                "entry_confirmation_min_score": (
                    POSITION_ENTRY_CONFIRMATION_MIN_SCORE
                ),
                "entry_confirmation_ready": ready,
                "entry_confirmation_passed_scan": score_data["passed"],
                "entry_confirmation_confirmed_scans": 1 if ready else 0,
                "entry_confirmation_required_scans": required_scans,
                "entry_confirmation_reason": reason,
                "entry_confirmation_reasons": score_data["reasons"],
                "entry_confirmation_breakdown": score_data["breakdown"],
                "entry_confirmation_would_block": not ready,
                "entry_confirmation_vwap_ready": score_data["vwap_ready"],
                "entry_confirmation_vwap": score_data["vwap"],
                "entry_confirmation_vwap_price": score_data["vwap_price"],
                "entry_confirmation_vwap_source": score_data["vwap_source"],
                "entry_confirmation_vwap_distance_pct": (
                    score_data["vwap_distance_pct"]
                ),
                "entry_confirmation_buy_sell_ratio": (
                    score_data["buy_sell_ratio"]
                ),
                "entry_confirmation_buy_volume_5m": (
                    score_data["buy_volume_5m"]
                ),
                "entry_confirmation_volume_liquidity_ratio": (
                    score_data["volume_liquidity_ratio"]
                )
            })

            if POSITION_ENTRY_CONFIRMATION_SHADOW_MODE:
                return None

            return None if ready else reason

        state = self.load_state()
        watch_book = state.setdefault(
            "entry_confirmation_watch",
            {}
        )
        address = str(metrics.address)
        watch = watch_book.get(address)

        if (
            watch
            and POSITION_ENTRY_CONFIRMATION_WATCH_SECONDS > 0
            and now_value > 0
            and now_value - safe_float(watch.get("last_seen_at"), now_value)
            > POSITION_ENTRY_CONFIRMATION_WATCH_SECONDS
        ):
            watch = None
            watch_book.pop(address, None)
            self.state_dirty = True

        if not watch:
            watch = {
                "started_at": now_value,
                "last_seen_at": now_value,
                "last_scan_at": None,
                "confirmed_scans": 0,
                "best_score": 0,
                "ready": False
            }
            watch_book[address] = watch
            self.state_dirty = True

        same_scan = (
            now_value > 0
            and safe_float(watch.get("last_scan_at"), -1) == now_value
        )

        if not same_scan:
            if score_data["passed"]:
                watch["confirmed_scans"] = int(
                    safe_float(
                        watch.get("confirmed_scans"),
                        0
                    )
                ) + 1
            else:
                watch["confirmed_scans"] = 0

            watch["last_scan_at"] = now_value
            watch["last_seen_at"] = now_value
            watch["last_score"] = score_data["score"]
            watch["last_reason"] = score_data["reason"]
            watch["best_score"] = max(
                safe_float(watch.get("best_score"), 0),
                score_data["score"]
            )
            self.state_dirty = True

        confirmed_scans = int(
            safe_float(
                watch.get("confirmed_scans"),
                0
            )
        )
        ready = (
            score_data["passed"]
            and confirmed_scans >= required_scans
        )
        watch["ready"] = ready

        if ready and not watch.get("ready_at"):
            watch["ready_at"] = now_value
            self.state_dirty = True

        would_block = not ready
        reason = (
            "entry_confirmation_waiting"
            if score_data["passed"] and not ready
            else score_data["reason"]
        )

        ignition_details.update({
            "entry_confirmation_enabled": True,
            "entry_confirmation_shadow_mode": (
                POSITION_ENTRY_CONFIRMATION_SHADOW_MODE
            ),
            "entry_confirmation_score": score_data["score"],
            "entry_confirmation_min_score": (
                POSITION_ENTRY_CONFIRMATION_MIN_SCORE
            ),
            "entry_confirmation_ready": ready,
            "entry_confirmation_passed_scan": score_data["passed"],
            "entry_confirmation_confirmed_scans": confirmed_scans,
            "entry_confirmation_required_scans": required_scans,
            "entry_confirmation_reason": reason,
            "entry_confirmation_reasons": score_data["reasons"],
            "entry_confirmation_breakdown": score_data["breakdown"],
            "entry_confirmation_would_block": would_block,
            "entry_confirmation_vwap_ready": score_data["vwap_ready"],
            "entry_confirmation_vwap": score_data["vwap"],
            "entry_confirmation_vwap_price": score_data["vwap_price"],
            "entry_confirmation_vwap_source": score_data["vwap_source"],
            "entry_confirmation_vwap_distance_pct": (
                score_data["vwap_distance_pct"]
            ),
            "entry_confirmation_buy_sell_ratio": (
                score_data["buy_sell_ratio"]
            ),
            "entry_confirmation_buy_volume_5m": (
                score_data["buy_volume_5m"]
            ),
            "entry_confirmation_volume_liquidity_ratio": (
                score_data["volume_liquidity_ratio"]
            )
        })

        if POSITION_ENTRY_CONFIRMATION_SHADOW_MODE:
            return None

        if not ready:
            return reason

        return None

    def mid_volume_confirmation_values(
        self,
        metrics,
        ignition_details
    ):

        pressure = safe_float(
            ignition_details.get("pressure"),
            self.calculate_pressure(
                metrics,
                ignition_details
            )
        )
        volume_liquidity_ratio = safe_float(
            ignition_details.get("volume_liquidity_ratio"),
            0
        )
        buy_sell_ratio = self.effective_buy_sell_volume_ratio(
            metrics,
            ignition_details
        )

        return pressure, volume_liquidity_ratio, buy_sell_ratio

    def quality_volume_tier(
        self,
        metrics,
        ignition_details
    ):

        if not POSITION_QUALITY_VOLUME_GATE_ENABLED:
            return "legacy"

        volume_multiple = self.entry_volume_multiple(
            metrics,
            ignition_details
        )

        if (
            volume_multiple
            >= POSITION_FULL_SIZE_VOLUME_MULTIPLE
        ):
            return "high_volume"

        if (
            volume_multiple
            >= POSITION_MIN_ENTRY_VOLUME_MULTIPLE
        ):
            return "confirmed_mid_volume"

        return "below_quality_volume"

    def quality_volume_entry_block_reason(
        self,
        metrics,
        ignition_details
    ):

        if not POSITION_QUALITY_VOLUME_GATE_ENABLED:
            return None

        volume_multiple = self.entry_volume_multiple(
            metrics,
            ignition_details
        )

        alert_route = str(
            ignition_details.get("alert_route", "") or ""
        )

        if (
            volume_multiple
            < self.min_entry_volume_multiple(alert_route)
        ):
            return "entry_volume_multiple_below_min"

        if (
            volume_multiple
            >= POSITION_FULL_SIZE_VOLUME_MULTIPLE
            or not POSITION_MID_VOLUME_CONFIRM_ENABLED
        ):
            return None

        (
            pressure,
            volume_liquidity_ratio,
            buy_sell_ratio
        ) = self.mid_volume_confirmation_values(
            metrics,
            ignition_details
        )

        if (
            pressure < POSITION_MID_VOLUME_MIN_PRESSURE
            or volume_liquidity_ratio
            < POSITION_MID_VOLUME_MIN_VOLUME_LIQUIDITY_RATIO
            or buy_sell_ratio
            < POSITION_MID_VOLUME_MIN_BUY_SELL_RATIO
        ):
            return "mid_volume_quality_confirm_failed"

        return None

    def anchored_vwap_entry_block_reason(
        self,
        metrics,
        ignition_details
    ):

        if not ANCHORED_VWAP_ENTRY_ENABLED:
            return None

        if self.hyperevm_entry(
            metrics,
            ignition_details
        ):
            return None

        if not ignition_details.get("anchored_vwap_ready"):
            return (
                "anchored_vwap_not_ready"
                if ANCHORED_VWAP_ENTRY_REQUIRE_READY
                else None
            )

        anchored_vwap = safe_float(
            ignition_details.get("anchored_vwap"),
            0
        )

        if anchored_vwap <= 0:
            return (
                "anchored_vwap_not_ready"
                if ANCHORED_VWAP_ENTRY_REQUIRE_READY
                else None
            )

        price = safe_float(
            ignition_details.get("anchored_vwap_price"),
            metrics.price
        )

        if price <= 0:
            price = safe_float(
                metrics.price,
                0
            )

        if price < anchored_vwap:
            return "price_below_1h_low_avwap"

        return None

    def chop_entry_block_reason(
        self,
        metrics,
        ignition_details,
        recent_snapshots=None
    ):

        if not POSITION_CHOP_FILTER_ENABLED:
            return None

        alert_route = str(
            (ignition_details or {}).get("alert_route", "") or ""
        )
        if alert_route in (
            "bonding_early_revival",
            "migrated_revival"
        ):
            return None

        snapshots = list(recent_snapshots or [])[
            -POSITION_CHOP_LOOKBACK_SCANS:
        ]
        prices = [
            safe_float(snapshot.get("price"), 0)
            for snapshot in snapshots
        ]
        prices = [
            value
            for value in prices
            if value > 0
        ]

        if len(prices) < 4:
            return None

        high = max(prices)
        low = min(prices)

        if high <= 0 or low <= 0:
            return None

        range_pct = (
            high - low
        ) / high

        if range_pct < POSITION_CHOP_MIN_RANGE_PCT:
            return None

        directions = []

        for previous_price, current_price in zip(
            prices,
            prices[1:]
        ):
            change_pct = (
                current_price
                / max(previous_price, 1e-18)
                - 1
            )

            if change_pct >= POSITION_CHOP_MIN_LEG_MOVE_PCT:
                directions.append(1)
            elif change_pct <= -POSITION_CHOP_MIN_LEG_MOVE_PCT:
                directions.append(-1)

        if len(directions) < 2:
            return None

        flips = sum(
            1
            for previous_direction, current_direction in zip(
                directions,
                directions[1:]
            )
            if previous_direction != current_direction
        )

        if flips < POSITION_CHOP_MIN_DIRECTION_FLIPS:
            return None

        current_price = safe_float(
            metrics.price,
            prices[-1]
        )
        range_position = (
            current_price - low
        ) / max(high - low, 1e-18)

        if range_position > POSITION_CHOP_MAX_RANGE_POSITION:
            return None

        buy_sell_ratio = self.effective_buy_sell_volume_ratio(
            metrics,
            ignition_details
        )
        weak_tape = (
            safe_float(metrics.price_change_5m, 0) <= 0
            or buy_sell_ratio <= POSITION_CHOP_MAX_BUY_SELL_RATIO
        )

        if not weak_tape:
            return None

        return "chop_distribution"

    def is_reentry(
        self,
        token_address
    ):

        return self.entry_count_for_token(
            token_address
        ) > 0

    def entry_count_for_token(
        self,
        token_address,
        since=None
    ):

        state = self.load_state()
        address = str(token_address)
        entries = 0

        for position in list(state["open"].values()) + list(state["closed"]):
            if str(position.get("address")) != address:
                continue

            entry_at = safe_float(
                position.get("entry_at"),
                0
            )

            if since is not None and entry_at < since:
                continue

            entries += 1

        return entries

    def last_close_at_for_token(
        self,
        token_address
    ):

        last_position = self.last_closed_position_for_token(
            token_address
        )

        if not last_position:
            return 0

        return safe_float(
            last_position.get("exit_at"),
            0
        )

    def last_closed_position_for_token(
        self,
        token_address
    ):

        state = self.load_state()
        address = str(token_address)
        last_position = None
        last_close_at = -1

        for position in state["closed"]:
            if str(position.get("address")) != address:
                continue

            exit_at = safe_float(
                position.get("exit_at"),
                0
            )

            if exit_at > last_close_at:
                last_close_at = exit_at
                last_position = position

        return last_position

    def reentry_state_block_reason(
        self,
        metrics
    ):

        if not POSITION_REENTRY_STATE_FILTER_ENABLED:
            return None

        prior = self.last_closed_position_for_token(
            metrics.address
        )

        if not prior:
            return None

        close_reason = str(
            prior.get("close_reason")
            or ""
        )
        prior_winner = (
            safe_float(
                prior.get("pnl_usd"),
                0
            ) > 0
        )
        risky_reason = close_reason in (
            POSITION_REENTRY_RISKY_PRIOR_CLOSE_REASONS
        )

        if not (
            risky_reason
            or (
                prior_winner
                and POSITION_REENTRY_BLOCK_AFTER_WIN_ENABLED
            )
        ):
            return None

        if self.reentry_price_reclaimed(
            metrics,
            prior
        ):
            return None

        if risky_reason:
            return f"reentry_prior_{close_reason}_needs_reclaim"

        return "reentry_prior_win_needs_reclaim"

    def reentry_price_reclaimed(
        self,
        metrics,
        prior
    ):

        price = safe_float(
            metrics.price,
            0
        )

        if price <= 0:
            return False

        exit_price = safe_float(
            prior.get("exit_price"),
            safe_float(
                prior.get("last_price"),
                0
            )
        )
        peak_price = safe_float(
            prior.get("peak_price"),
            0
        )
        exit_reclaim = (
            exit_price > 0
            and price
            >= exit_price * (1 + POSITION_REENTRY_RECLAIM_EXIT_PCT)
        )
        new_high = (
            peak_price > 0
            and price
            >= peak_price * (1 + POSITION_REENTRY_NEW_HIGH_PCT)
        )

        return exit_reclaim or new_high

    def trailing_rebound_close_reason(
        self,
        reason
    ):

        reason = str(reason or "")

        return (
            "trailing_stop" in reason
            or "peak_trail" in reason
        )

    def trailing_rebound_watch(
        self,
        metrics
    ):

        state = self.load_state()
        watch = state.setdefault(
            "trailing_rebound_watch",
            {}
        )

        return watch.get(str(metrics.address))

    def cleanup_trailing_rebound_watches(
        self,
        now
    ):

        state = self.load_state()
        watch = state.setdefault(
            "trailing_rebound_watch",
            {}
        )
        expired = [
            address
            for address, item in watch.items()
            if safe_float(item.get("expires_at"), 0) > 0
            and now > safe_float(item.get("expires_at"), 0)
        ]

        for address in expired:
            watch.pop(address, None)

        if expired:
            self.state_dirty = True
            self.save_state()

    def trailing_rebound_reference_price(
        self,
        watch,
        prior
    ):

        prices = []

        for source in (
            watch,
            prior
        ):
            if not isinstance(source, dict):
                continue

            for key in (
                "trailing_stop_price",
                "exit_price"
            ):
                price = safe_float(
                    source.get(key),
                    0
                )

                if price > 0:
                    prices.append(price)

        return max(prices) if prices else 0

    def update_trailing_rebound_watch(
        self,
        metrics,
        watch,
        prior,
        now
    ):

        if not isinstance(watch, dict):
            return

        price = safe_float(
            metrics.price,
            0
        )

        if price <= 0:
            return

        reference_price = self.trailing_rebound_reference_price(
            watch,
            prior
        )
        reclaim_price = reference_price * (
            1 + POSITION_TRAILING_REBOUND_RECLAIM_PCT
        )
        was_reclaimed = bool(
            watch.get("last_reclaimed")
        )
        reclaimed = (
            reference_price > 0
            and price >= reclaim_price
        )

        watch["last_seen_at"] = now
        watch["last_price"] = price
        watch["seen_count"] = int(
            safe_float(
                watch.get("seen_count"),
                0
            )
        ) + 1
        watch["high_post_exit_price"] = max(
            safe_float(
                watch.get("high_post_exit_price"),
                price
            ),
            price
        )
        watch["low_post_exit_price"] = min(
            safe_float(
                watch.get("low_post_exit_price"),
                price
            )
            or price,
            price
        )
        watch["last_reclaimed"] = reclaimed

        if reclaimed and not was_reclaimed:
            watch["reclaim_scan_count"] = int(
                safe_float(
                    watch.get("reclaim_scan_count"),
                    0
                )
            ) + 1

            if not watch.get("first_reclaim_at"):
                watch["first_reclaim_at"] = now

        self.state_dirty = True

    def trailing_rebound_vwap_reclaimed(
        self,
        metrics,
        ignition_details
    ):

        if not POSITION_TRAILING_REBOUND_REQUIRE_VWAP_RECLAIM:
            return True

        price = safe_float(
            metrics.price,
            0
        )

        if price <= 0:
            return False

        if self.hyperevm_entry(
            metrics,
            ignition_details
        ):
            runner_vwap = safe_float(
                ignition_details.get("runner_vwap"),
                0
            )

            if runner_vwap <= 0:
                return not POSITION_TRAILING_REBOUND_REQUIRE_VWAP_READY

            return price > runner_vwap

        if not ignition_details.get("anchored_vwap_ready"):
            return not POSITION_TRAILING_REBOUND_REQUIRE_VWAP_READY

        anchored_vwap = safe_float(
            ignition_details.get("anchored_vwap"),
            0
        )

        if anchored_vwap <= 0:
            return not POSITION_TRAILING_REBOUND_REQUIRE_VWAP_READY

        return price > anchored_vwap

    def trailing_rebound_reentry_block_reason(
        self,
        metrics,
        ignition_details,
        now
    ):

        if not POSITION_TRAILING_REBOUND_REENTRY_ENABLED:
            return None

        prior = self.last_closed_position_for_token(
            metrics.address
        )

        if not prior or not self.trailing_rebound_close_reason(
            prior.get("close_reason")
        ):
            return None

        watch = self.trailing_rebound_watch(metrics)

        if not watch:
            prior_exit_at = safe_float(
                prior.get("exit_at"),
                0
            )

            if (
                prior_exit_at > 0
                and now - prior_exit_at
                <= POSITION_TRAILING_REBOUND_WATCH_SECONDS
            ):
                return "trailing_rebound_watch_missing"

            return None

        expires_at = safe_float(
            watch.get("expires_at"),
            0
        )

        if expires_at > 0 and now > expires_at:
            self.load_state().setdefault(
                "trailing_rebound_watch",
                {}
            ).pop(
                str(metrics.address),
                None
            )
            self.state_dirty = True
            return "trailing_rebound_watch_expired"

        self.update_trailing_rebound_watch(
            metrics,
            watch,
            prior,
            now
        )

        price = safe_float(
            metrics.price,
            0
        )
        reference_price = self.trailing_rebound_reference_price(
            watch,
            prior
        )
        reclaim_price = reference_price * (
            1 + POSITION_TRAILING_REBOUND_RECLAIM_PCT
        )

        if reference_price > 0 and price < reclaim_price:
            return "trailing_rebound_not_reclaimed"

        if not self.trailing_rebound_vwap_reclaimed(
            metrics,
            ignition_details
        ):
            return "trailing_rebound_below_vwap"

        buy_volume_5m = safe_float(
            getattr(metrics, "buy_volume_5m", 0),
            0
        )
        sell_volume_5m = safe_float(
            getattr(metrics, "sell_volume_5m", 0),
            0
        )
        buy_sell_volume_ratio = (
            buy_volume_5m
            / max(sell_volume_5m, 1e-18)
        )

        if (
            buy_volume_5m
            < POSITION_TRAILING_REBOUND_MIN_BUY_VOLUME_5M_USD
        ):
            return "trailing_rebound_buy_volume_below_min"

        if (
            buy_sell_volume_ratio
            < POSITION_TRAILING_REBOUND_MIN_BUY_SELL_VOLUME_RATIO
        ):
            return "trailing_rebound_buy_sell_volume_below_min"

        pressure = self.calculate_pressure(
            metrics,
            ignition_details
        )

        if pressure < POSITION_TRAILING_REBOUND_MIN_PRESSURE:
            return "trailing_rebound_pressure_below_min"

        volume_liquidity_ratio = safe_float(
            ignition_details.get("volume_liquidity_ratio"),
            0
        )

        if (
            volume_liquidity_ratio
            < POSITION_TRAILING_REBOUND_MIN_VOLUME_LIQUIDITY_RATIO
        ):
            return "trailing_rebound_volume_liquidity_below_min"

        ignition_details["trailing_rebound_reentry"] = True
        ignition_details["trailing_rebound_reference_price"] = (
            reference_price
        )
        ignition_details["trailing_rebound_reclaim_price"] = (
            reclaim_price
        )

        return None

    def reentry_cooldown_active(
        self,
        token_address,
        now
    ):

        if POSITION_REENTRY_COOLDOWN_SECONDS <= 0:
            return False

        last_close_at = self.last_close_at_for_token(
            token_address
        )

        if last_close_at <= 0:
            return False

        return (
            now - last_close_at
            < POSITION_REENTRY_COOLDOWN_SECONDS
        )

    def entry_count_limit_reached(
        self,
        token_address,
        now=None
    ):

        if POSITION_MAX_ENTRIES_PER_TOKEN_PER_HOUR <= 0:
            return False

        if now is None:
            return False

        since = now - 3600
        entries = self.entry_count_for_token(
            token_address,
            since=since
        )

        return entries >= POSITION_MAX_ENTRIES_PER_TOKEN_PER_HOUR

    def entry_size_sol(
        self,
        metrics,
        ignition_details
    ):

        if self.hyperevm_entry(metrics, ignition_details):
            return (
                POSITION_HYPEREVM_POSITION_SIZE_USD
                / max(self.current_sol_usd(), 1e-18)
            )

        if POSITION_FIXED_USD_POSITION_SIZING_ENABLED:
            return (
                POSITION_POSITION_SIZE_USD
                / max(self.current_sol_usd(), 1e-18)
            )

        size_sol = POSITION_POSITION_SIZE_SOL

        if self.is_reentry(metrics.address):
            multiplier = min(
                max(
                    safe_float(
                        POSITION_REENTRY_POSITION_SIZE_MULTIPLIER,
                        1
                    ),
                    0
                ),
                1
            )
            size_sol *= multiplier

        return size_sol

    def entry_rule_status(
        self,
        metrics,
        ignition_details
    ):

        impulse = safe_float(
            ignition_details.get("price_jump"),
            0
        )
        volume_1h = safe_float(
            metrics.volume_1h,
            0
        )
        volume_multiple = self.entry_volume_multiple(
            metrics,
            ignition_details
        )
        volume_5m = safe_float(
            metrics.volume_5m,
            0
        )
        buy_volume_5m = safe_float(
            metrics.buy_volume_5m,
            0
        )
        sell_volume_5m = safe_float(
            getattr(metrics, "sell_volume_5m", 0),
            0
        )
        buy_sell_volume_ratio = self.entry_buy_sell_volume_ratio(
            metrics
        )
        fdv = safe_float(
            metrics.fdv,
            0
        )
        reentry = self.is_reentry(metrics.address)
        migration_status = self.migration_rule_status(
            metrics
        )
        migration_text = (
            f"; {migration_status}"
            if migration_status
            else ""
        )
        anchored_vwap_text = ""
        if ignition_details.get("anchored_vwap_ready"):
            anchored_vwap = safe_float(
                ignition_details.get("anchored_vwap"),
                0
            )
            anchored_price = safe_float(
                ignition_details.get("anchored_vwap_price"),
                metrics.price
            )
            if anchored_vwap > 0:
                anchored_vwap_text = (
                        f"; 1h-low AVWAP ${anchored_vwap:.12f} "
                        f"{'ok' if anchored_price >= anchored_vwap else 'below'}"
                    )
        confirmation_text = ""

        if ignition_details.get("entry_confirmation_enabled"):
            confirmation_text = (
                "; confirmation "
                f"{safe_float(ignition_details.get('entry_confirmation_score'), 0):.0f}"
                "/"
                f"{safe_float(ignition_details.get('entry_confirmation_min_score'), 0):.0f} "
                f"{int(safe_float(ignition_details.get('entry_confirmation_confirmed_scans'), 0))}"
                "/"
                f"{int(safe_float(ignition_details.get('entry_confirmation_required_scans'), 0))} "
                f"{ignition_details.get('entry_confirmation_reason', '')}"
            )
        impulse_ok = (
            POSITION_MIN_ENTRY_IMPULSE
            <= impulse
            <= POSITION_MAX_ENTRY_IMPULSE
        )
        impulse_status = "ok"

        if impulse < POSITION_MIN_ENTRY_IMPULSE:
            impulse_status = "too_low"
        elif not impulse_ok:
            impulse_status = "too_hot"

        quality_tier = self.quality_volume_tier(
            metrics,
            ignition_details
        )
        quality_reason = self.quality_volume_entry_block_reason(
            metrics,
            ignition_details
        )
        quality_status = (
            quality_reason
            if quality_reason
            else quality_tier
        )

        return (
            f"1h volume ${volume_1h:,.0f} "
            f"{'>' if volume_1h > POSITION_MIN_ENTRY_VOLUME_1H_USD else '<='} "
            f"${POSITION_MIN_ENTRY_VOLUME_1H_USD:,.0f} "
            f"({volume_multiple:.2f}x; {quality_status}); "
            f"5m flow ${buy_volume_5m:,.0f}/${sell_volume_5m:,.0f} "
            f"({buy_sell_volume_ratio:.2f}x)"
            f"{' reentry' if reentry else ''}; "
            f"fdv ${fdv:,.0f} "
            f"{'ok' if not self.fdv_above_entry_max(metrics) else 'too_high'} "
            f"(max ${self.entry_max_fdv_usd(metrics):,.0f}); "
            f"impulse {impulse:.2f}x "
            f"{impulse_status}"
            f"{migration_text}"
            f"{anchored_vwap_text}"
            f"{confirmation_text}"
        )

    def entry_max_fdv_usd(
        self,
        metrics,
        ignition_details=None
    ):

        chain = str(
            getattr(metrics, "chain", "solana")
            or "solana"
        ).lower()

        if self.hyperevm_chain(chain):
            return POSITION_HYPEREVM_MAX_ENTRY_FDV_USD

        max_fdv = POSITION_MAX_ENTRY_FDV_USD

        # Early-revival caps to a lower FDV — above it the setup isn't "early".
        route = str(
            (ignition_details or {}).get("alert_route", "") or ""
        )
        if (
            route == "bonding_early_revival"
            and POSITION_EARLY_REVIVAL_MAX_ENTRY_FDV_USD > 0
        ):
            max_fdv = min(
                max_fdv,
                POSITION_EARLY_REVIVAL_MAX_ENTRY_FDV_USD
            )

        return max_fdv

    def fdv_above_entry_max(
        self,
        metrics,
        ignition_details=None
    ):

        max_entry_fdv = self.entry_max_fdv_usd(
            metrics,
            ignition_details
        )

        if max_entry_fdv <= 0:
            return False

        return (
            safe_float(metrics.fdv, 0)
            > max_entry_fdv
        )

    def fdv_below_entry_min(
        self,
        metrics
    ):

        min_entry_fdv = POSITION_MIN_ENTRY_FDV_USD

        if min_entry_fdv <= 0:
            return False

        chain = str(
            getattr(metrics, "chain", "solana")
            or "solana"
        ).lower()

        if self.hyperevm_chain(chain):
            return False

        return (
            safe_float(metrics.fdv, 0)
            < min_entry_fdv
        )

    def migration_fdv_entry_block_reason(
        self,
        metrics
    ):

        if not POSITION_AVOID_MIGRATION_FDV_ZONE:
            return ""

        if metrics.lifecycle != "bonding_curve":
            return ""

        migration_fdv = safe_float(
            getattr(metrics, "migration_fdv", 0),
            0
        )

        if migration_fdv <= 0:
            return ""

        fdv = safe_float(
            metrics.fdv,
            0
        )

        if fdv <= 0:
            return ""

        distance = safe_float(
            getattr(metrics, "migration_distance_usd", 0),
            migration_fdv - fdv
        )

        buffer_usd = max(
            POSITION_MIGRATION_FDV_BUFFER_USD,
            0
        )

        if abs(distance) <= buffer_usd:
            return "migration_fdv_zone"

        if distance < -buffer_usd:
            return "migration_fdv_above_limit"

        return ""

    def in_migration_fdv_zone(
        self,
        metrics
    ):

        return bool(
            self.migration_fdv_entry_block_reason(metrics)
        )

    def migration_zone_grace_active(self, position):
        """True while a migration-zone entry is in its 'hard-stop-only' grace
        window (peak below GRACE_UNTIL multiple). During grace the momentum/
        signal soft exits are suppressed so post-migration shakeouts don't cut
        runners; the hard stop (via the trailing stop) and catastrophic exits
        (top of manage_position) still apply. Scoped to zone entries via the
        per-trade entry_migration_distance_usd recorded at open."""

        if not POSITION_MIGRATION_ZONE_GRACE_ENABLED:
            return False

        raw = position.get("entry_migration_distance_usd")
        if raw is None:
            return False  # not recorded (pre-change positions) -> no grace

        if abs(safe_float(raw, 0)) > max(POSITION_MIGRATION_FDV_BUFFER_USD, 0):
            return False  # not a migration-zone entry

        peak_multiple = safe_float(position.get("peak_multiple"), 1.0)
        return peak_multiple < POSITION_MIGRATION_ZONE_GRACE_UNTIL_MULTIPLE

    def runner_hold_soft_reason(self, reason):
        """Soft exits may be intercepted by the runner-hold leg; hard exits
        (rug-shaped or already at/below the hold floor) always close in full."""

        hard_prefixes = (
            "hard_stop_loss",
            "liquidity_drain",
            "sell_only_flow",
            "runner_hold",
        )
        return not str(reason or "").startswith(hard_prefixes)

    def runner_hold_holding(self, position):
        return bool(position.get("runner_hold_active"))

    def runner_hold_update(self, position, peak_multiple):
        """Release the hold leg (or disarm interception) once the position has
        printed the release multiple; normal scale/trail management resumes."""

        if not POSITION_RUNNER_HOLD_ENABLED:
            return
        if position.get("runner_hold_released"):
            return
        if peak_multiple >= POSITION_RUNNER_HOLD_RELEASE_MULTIPLE:
            position["runner_hold_released"] = True
            position["runner_hold_active"] = False
            self.state_dirty = True

    def runner_hold_exit_reason(self, position, price_multiple, now):
        """Hold-tranche exits: hard floor (confirmed over consecutive scans,
        mirroring the hard stop) and the max-hold horizon from entry."""

        max_age = POSITION_RUNNER_HOLD_MAX_HOURS * 3600
        if max_age > 0 and now - position.get("entry_at", now) >= max_age:
            return "runner_hold_timeout_exit"

        if price_multiple <= POSITION_RUNNER_HOLD_FLOOR_MULTIPLE:
            count = int(
                safe_float(position.get("runner_hold_floor_breach_count"), 0)
            ) + 1
            position["runner_hold_floor_breach_count"] = count
            self.state_dirty = True
            if count >= max(POSITION_HARD_STOP_CONFIRMATION_TICKS, 1):
                return "runner_hold_floor_exit"
        elif position.get("runner_hold_floor_breach_count"):
            position["runner_hold_floor_breach_count"] = 0
            self.state_dirty = True

        return None

    def soft_exit_close(
        self,
        position,
        metrics,
        ignition_details,
        pressure,
        now,
        reason
    ):
        """Full close, unless the runner-hold leg is armed and the reason is
        soft — then sell only the non-hold tranche and keep holding."""

        intercept = (
            POSITION_RUNNER_HOLD_ENABLED
            and not position.get("runner_hold_active")
            and not position.get("runner_hold_released")
            and self.runner_hold_soft_reason(reason)
            and safe_float(position.get("remaining_tokens"), 0) > 0
        )

        if not intercept:
            return self.close_position(
                position,
                metrics,
                pressure,
                now,
                reason
            )

        return self.runner_hold_partial_close(
            position,
            metrics,
            ignition_details,
            pressure,
            now,
            reason
        )

    def runner_hold_partial_close(
        self,
        position,
        metrics,
        ignition_details,
        pressure,
        now,
        trigger_reason
    ):

        remaining = safe_float(position.get("remaining_tokens"), 0)
        fraction = min(max(POSITION_RUNNER_HOLD_FRACTION, 0.0), 1.0)
        sell_tokens = remaining * (1.0 - fraction)

        position["runner_hold_active"] = True
        position["runner_hold_started_at"] = now
        position["runner_hold_trigger_reason"] = trigger_reason
        position["runner_hold_floor_breach_count"] = 0
        self.state_dirty = True

        reason = f"runner_hold_scale {trigger_reason}"

        if sell_tokens <= 0:
            self.add_event(
                position,
                "risk",
                now,
                metrics.price,
                pressure,
                reason
            )
            self.save_state()
            return None

        proceeds = sell_tokens * metrics.price
        sol_usd = self.current_sol_usd()
        proceeds_sol = proceeds / max(
            sol_usd,
            1e-18
        )
        entry_tokens = max(
            safe_float(position.get("entry_size_tokens"), 0),
            1e-18
        )
        sell_pct = sell_tokens / entry_tokens

        position["remaining_tokens"] = remaining - sell_tokens
        position["realized_usd"] += proceeds
        position["scaled_out_pct"] = min(
            safe_float(position.get("scaled_out_pct"), 0) + sell_pct,
            1.0
        )

        state = self.load_state()
        state["cash_sol"] = (
            safe_float(
                state.get("cash_sol"),
                0
            )
            + proceeds_sol
        )

        self.add_event(
            position,
            "scale_out",
            now,
            metrics.price,
            pressure,
            reason,
            size_pct=sell_pct,
            proceeds_usd=proceeds,
            proceeds_sol=proceeds_sol,
            sol_usd=sol_usd
        )

        self.save_state()

        print(
            "PAPER TRADE RUNNER HOLD "
            f"{metrics.symbol} "
            f"sold {sell_pct:.0%} on {trigger_reason} "
            f"Price=${metrics.price:.8f} "
            f"holding {fraction:.0%} "
            f"floor {POSITION_RUNNER_HOLD_FLOOR_MULTIPLE:.2f}x "
            f"until {POSITION_RUNNER_HOLD_RELEASE_MULTIPLE:.1f}x "
            f"or {POSITION_RUNNER_HOLD_MAX_HOURS:.0f}h"
        )

        return self.build_notification_event(
            "scale_out",
            position,
            metrics,
            pressure,
            reason,
            size_pct=sell_pct,
            proceeds_usd=proceeds,
            proceeds_sol=proceeds_sol,
            sol_usd=sol_usd,
            ignition_details=ignition_details
        )

    def migration_rule_status(
        self,
        metrics
    ):

        migration_fdv = safe_float(
            getattr(metrics, "migration_fdv", 0),
            0
        )

        if migration_fdv <= 0:
            return ""

        distance = safe_float(
            getattr(metrics, "migration_distance_usd", 0),
            migration_fdv - safe_float(metrics.fdv, 0)
        )
        zone = (
            self.migration_fdv_entry_block_reason(metrics)
            or "clear"
        )

        return (
            f"migration FDV ${migration_fdv:,.0f} "
            f"distance {distance:+,.0f} "
            f"{zone}"
        )

    def calculate_pressure(
        self,
        metrics,
        ignition_details
    ):

        volume_liquidity_ratio = safe_float(
            ignition_details.get(
                "volume_liquidity_ratio"
            ),
            0
        )

        buy_sell_ratio = self.effective_buy_sell_volume_ratio(
            metrics,
            ignition_details
        )

        volume_5m = safe_float(
            metrics.volume_5m,
            0
        )

        volume_liquidity_score = min(
            volume_liquidity_ratio / 1.00,
            1
        ) * 40

        buy_sell_score = min(
            buy_sell_ratio / 3.00,
            1
        ) * 30

        volume_score = min(
            volume_5m / 10000,
            1
        ) * 30

        return round(
            volume_liquidity_score
            + buy_sell_score
            + volume_score,
            2
        )

    def open_position(
        self,
        metrics,
        ignition_score,
        ignition_details,
        pressure,
        now,
        entry_size_sol
    ):

        state = self.load_state()
        sol_usd = self.current_sol_usd()
        notional_usd = (
            entry_size_sol
            * sol_usd
        )
        size_tokens = (
            notional_usd
            / metrics.price
        )
        entry_route = str(
            ignition_details.get("alert_route", "") or ""
        )
        initial_stop_pct = self.initial_stop_loss_pct(
            ignition_details=ignition_details
        )
        initial_stop_basis = "route"
        adaptive_pct = self.adaptive_initial_stop_pct(
            metrics.address,
            metrics.price
        )
        if adaptive_pct is not None:
            initial_stop_pct = adaptive_pct
            initial_stop_basis = "atr"
        initial_stop = (
            metrics.price
            * (1 - initial_stop_pct)
        )
        entry_count_before = self.entry_count_for_token(
            metrics.address
        )
        reentry = entry_count_before > 0
        entry_volume_multiple = self.entry_volume_multiple(
            metrics,
            ignition_details
        )
        entry_quality_tier = self.quality_volume_tier(
            metrics,
            ignition_details
        )
        entry_buy_sell_volume_ratio = self.entry_buy_sell_volume_ratio(
            metrics
        )
        trailing_rebound_watch = state.setdefault(
            "trailing_rebound_watch",
            {}
        ).get(
            str(metrics.address)
        )

        position = {
            "address": metrics.address,
            "chain": metrics.chain,
            "symbol": metrics.symbol,
            "name": getattr(
                metrics,
                "name",
                ""
            ),
            "pair_address": metrics.pair_address,
            "status": "open",
            "entry_at": now,
            "reentry": reentry,
            "entry_count_before": entry_count_before,
            "trailing_rebound_reentry": bool(
                ignition_details.get("trailing_rebound_reentry")
            ),
            "trailing_rebound_reference_price": safe_float(
                ignition_details.get("trailing_rebound_reference_price"),
                0
            ),
            "trailing_rebound_reclaim_price": safe_float(
                ignition_details.get("trailing_rebound_reclaim_price"),
                0
            ),
            "trailing_rebound_original_exit_at": safe_float(
                (
                    trailing_rebound_watch
                    or {}
                ).get("closed_at"),
                0
            ),
            "entry_size_sol": entry_size_sol,
            "entry_sol_usd": sol_usd,
            "entry_price": metrics.price,
            "entry_notional_usd": notional_usd,
            "entry_size_tokens": size_tokens,
            "remaining_tokens": size_tokens,
            "realized_usd": 0,
            "scaled_out_pct": 0,
            "take_profit_filled": False,
            "entry_pressure": pressure,
            "peak_pressure": pressure,
            "last_pressure": pressure,
            "entry_score": ignition_score,
            "entry_impulse": safe_float(
                ignition_details.get("price_jump"),
                0
            ),
            "peak_price": metrics.price,
            "peak_multiple": 1,
            "last_price": metrics.price,
            "entry_liquidity": metrics.liquidity,
            "entry_fdv": safe_float(metrics.fdv, 0),
            "entry_migration_fdv": safe_float(
                getattr(metrics, "migration_fdv", 0),
                0
            ),
            # distance = migration_fdv - fdv (+ = below migration, room to go).
            # Mirrors migration_fdv_entry_block_reason; stored per trade so the
            # migration "danger zone" study can run on real trades.
            "entry_migration_distance_usd": safe_float(
                getattr(metrics, "migration_distance_usd", None),
                safe_float(getattr(metrics, "migration_fdv", 0), 0)
                - safe_float(metrics.fdv, 0)
            ),
            "entry_migration_distance_pct": safe_float(
                getattr(metrics, "migration_distance_pct", 0),
                0
            ),
            "entry_volume_1h": metrics.volume_1h,
            "entry_volume_multiple": entry_volume_multiple,
            "entry_quality_tier": entry_quality_tier,
            "entry_volume_liquidity_ratio": safe_float(
                ignition_details.get("volume_liquidity_ratio"),
                0
            ),
            "entry_buy_sell_ratio": safe_float(
                ignition_details.get("flow_buy_sell_ratio"),
                entry_buy_sell_volume_ratio
            ),
            "entry_buy_sell_volume_ratio": entry_buy_sell_volume_ratio,
            "entry_buy_volume_5m": safe_float(
                getattr(metrics, "buy_volume_5m", 0),
                0
            ),
            "entry_sell_volume_5m": safe_float(
                getattr(metrics, "sell_volume_5m", 0),
                0
            ),
            "entry_buy_sell_volume_source_5m": getattr(
                metrics,
                "buy_sell_volume_source_5m",
                ""
            ),
            "entry_buy_sell_volume_source_1h": getattr(
                metrics,
                "buy_sell_volume_source_1h",
                ""
            ),
            "entry_confirmation_score": safe_float(
                ignition_details.get("entry_confirmation_score"),
                0
            ),
            "entry_confirmation_ready": bool(
                ignition_details.get("entry_confirmation_ready")
            ),
            "entry_confirmation_confirmed_scans": int(
                safe_float(
                    ignition_details.get(
                        "entry_confirmation_confirmed_scans"
                    ),
                    0
                )
            ),
            "entry_confirmation_shadow_mode": bool(
                ignition_details.get("entry_confirmation_shadow_mode")
            ),
            "entry_confirmation_reason": ignition_details.get(
                "entry_confirmation_reason",
                ""
            ),
            "entry_anchored_vwap": safe_float(
                ignition_details.get("anchored_vwap"),
                0
            ),
            "entry_anchored_vwap_ready": bool(
                ignition_details.get("anchored_vwap_ready")
            ),
            "entry_anchored_vwap_source": ignition_details.get(
                "anchored_vwap_source",
                ""
            ),
            "peak_liquidity": metrics.liquidity,
            "last_liquidity": metrics.liquidity,
            "trailing_stop_price": initial_stop,
            "initial_stop_pct": initial_stop_pct,
            "initial_stop_basis": initial_stop_basis,
            "entry_route": entry_route,
            "first_4x_seen_at": None,
            "missing_pair_count": 0,
            "last_update_at": now,
            "events": []
        }

        state["cash_sol"] = max(
            safe_float(
                state.get("cash_sol"),
                POSITION_INITIAL_BALANCE_SOL
            )
            - entry_size_sol,
            0
        )
        state.setdefault(
            "entry_confirmation_watch",
            {}
        ).pop(
            str(metrics.address),
            None
        )
        state.setdefault(
            "rsi_entry_watch",
            {}
        ).pop(
            str(metrics.address),
            None
        )
        state.setdefault(
            "trailing_rebound_watch",
            {}
        ).pop(
            str(metrics.address),
            None
        )

        self.add_event(
            position,
            "entry",
            now,
            metrics.price,
            pressure,
            (
                "position entry "
                f"{ignition_score}/100 "
                f"impulse {position['entry_impulse']:.2f}x "
                f"volume {entry_volume_multiple:.2f}x "
                f"tier {entry_quality_tier} "
                f"{self.entry_rule_status(metrics, ignition_details)}"
            )
        )

        state["open"][metrics.address] = position
        self.save_state()

        print(
            "PAPER TRADE ENTRY "
            f"{metrics.symbol} "
            f"CA={metrics.address} "
            f"Size={entry_size_sol:.2f} SOL "
            f"Price=${metrics.price:.8f} "
            f"Pressure={pressure:.1f} "
            f"VolumeTier={entry_quality_tier} "
            f"Cash={state['cash_sol']:.2f} SOL "
            f"Stop=${initial_stop:.8f}"
        )

        return self.build_notification_event(
            "entry",
            position,
            metrics,
            pressure,
            "position entry",
            ignition_details=ignition_details
        )

    def manage_position(
        self,
        position,
        metrics,
        ignition_details,
        pressure,
        now,
        recent_snapshots=None
        ):

        recent_snapshots = recent_snapshots or []
        ignition_details = dict(ignition_details or {})
        price = metrics.price

        if price > position["peak_price"]:
            position["peak_price"] = price

        entry_price = max(
            position.get("entry_price", 0),
            1e-18
        )
        peak_multiple = (
            position["peak_price"]
            / entry_price
        )
        price_multiple = (
            price
            / entry_price
        )
        position["peak_multiple"] = peak_multiple
        self.runner_hold_update(position, peak_multiple)

        entry_liquidity = position.get(
            "entry_liquidity",
            metrics.liquidity
        )
        peak_liquidity = max(
            position.get("peak_liquidity", entry_liquidity),
            metrics.liquidity
        )

        position["entry_liquidity"] = entry_liquidity
        position["peak_liquidity"] = peak_liquidity
        position["last_liquidity"] = metrics.liquidity

        liquidity_exit_reason = self.liquidity_collapse_reason(
            position,
            metrics
        )

        if liquidity_exit_reason:
            ignition_details[
                "liquidity_collapse_reason"
            ] = liquidity_exit_reason
            pressure = self.liquidity_adjusted_pressure(
                pressure,
                liquidity_exit_reason
            )

        if (
            peak_multiple >= POSITION_TAKE_PROFIT_MULTIPLE
            and not position.get("first_4x_seen_at")
        ):
            position["first_4x_seen_at"] = now

        if pressure > position["peak_pressure"]:
            position["peak_pressure"] = pressure

        position["last_price"] = price
        position["last_pressure"] = pressure
        position["last_update_at"] = now
        position["missing_pair_count"] = 0

        events = []

        catastrophic_exit_reason = (
            liquidity_exit_reason
            or self.sell_only_flow_exit_reason(
                position,
                metrics
            )
        )

        if catastrophic_exit_reason:
            event = self.close_position(
                position,
                metrics,
                pressure,
                now,
                catastrophic_exit_reason
            )
            events.append(event)
            return events

        # Hold-tranche mode: soft exits already fired once and were converted
        # into a partial sell. Only the hold floor, the max-hold horizon and
        # the catastrophic exits above apply until release at the release
        # multiple (handled in runner_hold_update from peak_multiple).
        if self.runner_hold_holding(position):
            hold_exit_reason = self.runner_hold_exit_reason(
                position,
                price_multiple,
                now
            )
            if hold_exit_reason:
                event = self.close_position(
                    position,
                    metrics,
                    pressure,
                    now,
                    hold_exit_reason
                )
                events.append(event)
                return events
            self.save_state()
            return events

        scale_event = self.scale_out_if_needed(
            position,
            metrics,
            ignition_details,
            pressure,
            now
        )

        # During migration-zone grace, don't let the trailing stop ratchet
        # above the initial hard stop — a post-migration spike-then-flush would
        # otherwise trip the ratcheted trail before 2x. Only the -30% hard stop
        # (held in trailing_stop_price) applies until grace ends at 2x.
        if not self.migration_zone_grace_active(position):
            self.update_trailing_stop(
                position,
                metrics,
                ignition_details,
                pressure,
                peak_multiple,
                price_multiple
            )

        if scale_event:
            scale_event["trailing_stop_price"] = position.get(
                "trailing_stop_price",
                0
            )
            scale_event["trailing_stop_mode"] = position.get(
                "trailing_stop_mode",
                "standard"
            )
            scale_event["peak_price"] = position.get(
                "peak_price",
                metrics.price
            )
            events.append(scale_event)

        pressure_exit_reason = self.pressure_exit_reason(
            position,
            metrics,
            ignition_details,
            pressure
        )

        if pressure_exit_reason:
            event = self.soft_exit_close(
                position,
                metrics,
                ignition_details,
                pressure,
                now,
                pressure_exit_reason
            )
            if event:
                events.append(event)
            return events

        decay_reason = self.signal_decay_reason(
            position,
            metrics,
            recent_snapshots
        )

        if decay_reason:
            event = self.soft_exit_close(
                position,
                metrics,
                ignition_details,
                pressure,
                now,
                decay_reason
            )
            if event:
                events.append(event)
            return events

        trailing_exit_reason = self.trailing_exit_reason(
            position,
            price,
            ignition_details
        )

        if trailing_exit_reason:
            event = self.soft_exit_close(
                position,
                metrics,
                ignition_details,
                pressure,
                now,
                trailing_exit_reason
            )
            if event:
                events.append(event)
            return events

        self.save_state()

        if events:
            return events

        return []

    def confirmed_trailing_exit_reason(
        self,
        position,
        triggered,
        reason
    ):

        if not triggered:
            if position.get("trailing_stop_breach_count"):
                position["trailing_stop_breach_count"] = 0
                self.state_dirty = True
            return None

        required_ticks = self.trailing_stop_confirmation_ticks(
            position
        )
        breach_count = (
            int(
                safe_float(
                    position.get("trailing_stop_breach_count"),
                    0
                )
            )
            + 1
        )
        position["trailing_stop_breach_count"] = breach_count
        position["trailing_stop_last_reason"] = reason
        self.state_dirty = True

        if breach_count < required_ticks:
            return None

        position["trailing_stop_breach_count"] = 0
        return reason

    def trailing_stop_confirmation_ticks(
        self,
        position
    ):

        mode = str(
            position.get("trailing_stop_mode", "")
            or ""
        )

        if "anchored_vwap" in mode:
            return max(
                int(ANCHORED_VWAP_STOP_CONFIRMATION_TICKS or 1),
                1
            )

        # Initial hard stop & standard trailing: confirm over consecutive
        # scans so a single glitch tick (reverts next scan) can't force a
        # stop. A real breach persists and still fires.
        return max(
            int(POSITION_HARD_STOP_CONFIRMATION_TICKS or 1),
            1
        )

    def trailing_exit_reason(
        self,
        position,
        price,
        ignition_details
    ):

        if position["remaining_tokens"] <= 0:
            return None

        trailing_stop = safe_float(
            position.get("trailing_stop_price"),
            0
        )

        if trailing_stop <= 0:
            return None

        quote_triggered = self.quote_stop_triggered(
            position,
            trailing_stop,
            ignition_details
        )

        if quote_triggered is True:
            return self.confirmed_trailing_exit_reason(
                position,
                True,
                "trailing_stop_quote"
            )

        if quote_triggered is False:
            self.confirmed_trailing_exit_reason(
                position,
                False,
                "trailing_stop_quote"
            )
            return None

        if (
            LIVE_EXECUTION_REQUIRE_EXIT_QUOTE_FOR_STOPS
            and LIVE_EXECUTION_USE_QUOTES_FOR_STOPS
        ):
            return None

        if price > trailing_stop:
            self.confirmed_trailing_exit_reason(
                position,
                False,
                "trailing_stop"
            )
            return None

        return self.confirmed_trailing_exit_reason(
            position,
            True,
            "trailing_stop"
        )

    def quote_stop_triggered(
        self,
        position,
        stop_price,
        ignition_details
    ):

        if not LIVE_EXECUTION_USE_QUOTES_FOR_STOPS:
            return None

        if not ignition_details.get("exit_quote_checked"):
            return None

        if not ignition_details.get("exit_quote_available"):
            return None

        quote_value_usd = safe_float(
            ignition_details.get("exit_quote_value_usd"),
            0
        )
        remaining_tokens = safe_float(
            position.get("remaining_tokens"),
            0
        )

        if quote_value_usd <= 0 or remaining_tokens <= 0:
            return None

        stop_price = safe_float(stop_price, 0)
        spot_price = safe_float(
            position.get("last_price"),
            0
        )
        max_spot_premium = safe_float(
            LIVE_EXECUTION_STOP_QUOTE_MAX_SPOT_PREMIUM_PCT,
            0
        )

        if (
            max_spot_premium >= 0
            and spot_price > 0
            and stop_price > 0
            and spot_price > stop_price * (1 + max_spot_premium)
        ):
            return False

        stop_value_usd = (
            remaining_tokens
            * stop_price
            * (1 + max(LIVE_EXECUTION_STOP_QUOTE_BUFFER_PCT, 0))
        )

        if stop_value_usd <= 0:
            return None

        return quote_value_usd <= stop_value_usd

    def update_trailing_stop(
        self,
        position,
        metrics,
        ignition_details,
        pressure,
        peak_multiple=None,
        price_multiple=None
    ):

        trail_pct = self.trail_pct(
            pressure,
            peak_multiple
        )
        relaxed_trail_pct = self.runner_relaxed_trail_pct(
            position,
            metrics,
            ignition_details,
            pressure,
            price_multiple
        )
        high_volume_grace_trail_pct = (
            self.high_volume_trail_grace_pct(
                position,
                metrics,
                ignition_details,
                pressure,
                peak_multiple
            )
        )

        trailing_mode = "standard"

        if high_volume_grace_trail_pct is not None:
            trail_pct = max(
                trail_pct,
                high_volume_grace_trail_pct
            )
            trailing_mode = "high_volume_grace"
        elif relaxed_trail_pct is not None:
            trail_pct = max(
                trail_pct,
                relaxed_trail_pct
            )
            trailing_mode = "runner_relaxed"

        peak_price = position["peak_price"]
        entry_floor = position["entry_price"]

        if not position.get("take_profit_filled"):
            entry_floor = (
                position["entry_price"]
                * (1 - self.initial_stop_loss_pct(position=position))
            )

        hybrid_stop = self.anchored_vwap_hybrid_trailing_stop(
            position,
            metrics,
            ignition_details,
            peak_multiple=peak_multiple,
            price_multiple=price_multiple
        )

        if hybrid_stop is not None:
            candidate_stop, trailing_mode = hybrid_stop
            current_stop = safe_float(
                position.get(
                    "trailing_stop_price",
                    0
                ),
                0
            )
            position["trailing_stop_price"] = max(
                current_stop,
                candidate_stop
            )
            position["trailing_stop_mode"] = trailing_mode
            return

        candidate_stop = max(
            entry_floor,
            peak_price * (1 - trail_pct)
        )
        post_scale_stop = self.post_scale_trailing_stop(
            position
        )

        if (
            post_scale_stop is not None
            and post_scale_stop > candidate_stop
        ):
            candidate_stop = post_scale_stop
            trailing_mode = "post_scale_runner"

        anchored_vwap_stop = self.anchored_vwap_trailing_stop(
            metrics,
            ignition_details
        )

        if (
            anchored_vwap_stop is not None
            and anchored_vwap_stop > candidate_stop
        ):
            candidate_stop = anchored_vwap_stop
            trailing_mode = "anchored_vwap"

        current_stop = position.get(
            "trailing_stop_price",
            0
        )

        if trailing_mode == "runner_relaxed":
            position["trailing_stop_price"] = max(
                current_stop,
                candidate_stop
            )
            position["trailing_stop_mode"] = "runner_relaxed"
            return

        position["trailing_stop_price"] = max(
            current_stop,
            candidate_stop
        )
        position["trailing_stop_mode"] = trailing_mode

    def anchored_vwap_hybrid_trailing_stop(
        self,
        position,
        metrics,
        ignition_details,
        peak_multiple=None,
        price_multiple=None
    ):

        if not ANCHORED_VWAP_TRAILING_STOP_ENABLED:
            return None

        entry_price = safe_float(
            position.get("entry_price"),
            0
        )
        price = safe_float(
            metrics.price,
            0
        )

        if entry_price <= 0 or price <= 0:
            return None

        price_multiple = safe_float(
            price_multiple,
            price / max(entry_price, 1e-18)
        )
        peak_multiple = safe_float(
            peak_multiple,
            position.get(
                "peak_multiple",
                price_multiple
            )
        )
        candidate_stop = entry_price * (
            1 - self.initial_stop_loss_pct(position=position)
        )
        trailing_mode = "initial_hard_stop"

        anchored_vwap = safe_float(
            ignition_details.get("anchored_vwap"),
            0
        )

        if (
            ignition_details.get("anchored_vwap_ready")
            and anchored_vwap > 0
            and price_multiple
            >= 1 + ANCHORED_VWAP_TRAILING_ACTIVATE_PROFIT_PCT
        ):
            vwap_stop = anchored_vwap * (
                1 - min(
                    max(
                        ANCHORED_VWAP_TRAILING_STOP_BUFFER_PCT,
                        0
                    ),
                    0.80
                )
            )
            if vwap_stop > candidate_stop:
                candidate_stop = vwap_stop
                trailing_mode = "entry_anchored_vwap"

        scaled_out_pct = safe_float(
            position.get("scaled_out_pct"),
            0
        )
        peak_trail_ready = (
            peak_multiple >= ANCHORED_VWAP_PEAK_TRAIL_MIN_MULTIPLE
            or scaled_out_pct > 0
            or position.get("take_profit_filled")
        )

        if peak_trail_ready:
            peak_price = safe_float(
                position.get("peak_price"),
                price
            )
            peak_stop = peak_price * (
                1 - min(
                    max(
                        ANCHORED_VWAP_PEAK_TRAIL_PCT,
                        0
                    ),
                    0.95
                )
            )
            if peak_stop > candidate_stop:
                candidate_stop = peak_stop
                trailing_mode = "entry_avwap_peak_trail"

        return (
            candidate_stop,
            trailing_mode
        )

    def anchored_vwap_trailing_stop(
        self,
        metrics,
        ignition_details
    ):

        if not ANCHORED_VWAP_TRAILING_STOP_ENABLED:
            return None

        if not ignition_details.get("anchored_vwap_ready"):
            return None

        anchored_vwap = safe_float(
            ignition_details.get("anchored_vwap"),
            0
        )
        price = safe_float(
            metrics.price,
            0
        )

        if anchored_vwap <= 0 or price <= anchored_vwap:
            return None

        buffer_pct = min(
            max(
                ANCHORED_VWAP_TRAILING_STOP_BUFFER_PCT,
                0
            ),
            0.50
        )
        stop_price = anchored_vwap * (1 - buffer_pct)

        if stop_price <= 0 or stop_price >= price:
            return None

        return stop_price

    def post_scale_trailing_stop(
        self,
        position
    ):

        if not POSITION_POST_SCALE_TRAIL_ENABLED:
            return None

        scaled_out_pct = safe_float(
            position.get("scaled_out_pct"),
            0
        )
        entry_price = safe_float(
            position.get("entry_price"),
            0
        )
        peak_price = safe_float(
            position.get("peak_price"),
            0
        )

        if entry_price <= 0 or peak_price <= 0:
            return None

        for scaled_threshold, trail_pct, floor_multiple in (
            POSITION_POST_SCALE_TRAIL_RULES
        ):
            scaled_threshold = safe_float(
                scaled_threshold,
                0
            )

            if scaled_out_pct + 1e-9 < scaled_threshold:
                continue

            trail_pct = safe_float(
                trail_pct,
                0
            )
            floor_multiple = safe_float(
                floor_multiple,
                1
            )

            if trail_pct <= 0 or trail_pct >= 1:
                return None

            return max(
                entry_price * floor_multiple,
                peak_price * (1 - trail_pct)
            )

        return None

    def high_volume_trail_grace_pct(
        self,
        position,
        metrics,
        ignition_details,
        pressure,
        peak_multiple=None
    ):

        if not POSITION_HIGH_VOLUME_TRAIL_GRACE_ENABLED:
            return None

        if position.get("take_profit_filled"):
            return None

        peak_multiple = safe_float(
            peak_multiple,
            position.get("peak_multiple", 1)
        )

        if (
            peak_multiple
            >= POSITION_HIGH_VOLUME_TRAIL_GRACE_UNTIL_PEAK_MULTIPLE
        ):
            return None

        entry_volume_1h = safe_float(
            position.get("entry_volume_1h"),
            metrics.volume_1h
        )
        entry_volume_multiple = (
            entry_volume_1h
            / max(POSITION_MIN_ENTRY_VOLUME_1H_USD, 1e-18)
        )

        if (
            entry_volume_multiple
            < POSITION_HIGH_VOLUME_TRAIL_GRACE_MIN_VOLUME_MULTIPLE
        ):
            return None

        if pressure < POSITION_HIGH_VOLUME_TRAIL_GRACE_MIN_PRESSURE:
            return None

        volume_liquidity_ratio = safe_float(
            ignition_details.get("volume_liquidity_ratio"),
            0
        )
        buy_sell_ratio = self.effective_buy_sell_volume_ratio(
            metrics,
            ignition_details
        )

        if (
            volume_liquidity_ratio
            < POSITION_HIGH_VOLUME_TRAIL_GRACE_MIN_VOLUME_LIQUIDITY_RATIO
        ):
            return None

        if (
            buy_sell_ratio
            < POSITION_HIGH_VOLUME_TRAIL_GRACE_MIN_BUY_SELL_RATIO
        ):
            return None

        return POSITION_HIGH_VOLUME_TRAIL_GRACE_TRAIL_PCT

    def runner_relaxed_trail_pct(
        self,
        position,
        metrics,
        ignition_details,
        pressure,
        price_multiple=None
    ):

        if not position.get("take_profit_filled"):
            return None

        if self.in_migration_fdv_zone(metrics):
            return None

        price_multiple = safe_float(
            price_multiple,
            position.get(
                "peak_multiple",
                1
            )
        )

        if (
            price_multiple
            < POSITION_RUNNER_RELAXED_MIN_PRICE_MULTIPLE
        ):
            return None

        if pressure < POSITION_RUNNER_RELAXED_MIN_PRESSURE:
            return None

        if (
            safe_float(metrics.volume_1h, 0)
            <= POSITION_MIN_ENTRY_VOLUME_1H_USD
        ):
            return None

        buy_sell_ratio = self.effective_buy_sell_volume_ratio(
            metrics,
            ignition_details
        )
        volume_liquidity_ratio = safe_float(
            ignition_details.get(
                "volume_liquidity_ratio"
            ),
            0
        )

        if buy_sell_ratio < POSITION_RUNNER_RELAXED_MIN_BUY_SELL_RATIO:
            return None

        if (
            volume_liquidity_ratio
            < POSITION_RUNNER_RELAXED_MIN_VOLUME_LIQUIDITY_RATIO
        ):
            return None

        return POSITION_RUNNER_RELAXED_TRAIL_PCT

    def trail_pct(
        self,
        pressure,
        peak_multiple=None
    ):

        pressure_trail = self.trail_pct_for_pressure(
            pressure
        )
        peak_trail = self.trail_pct_for_peak_multiple(
            peak_multiple
        )

        return max(
            pressure_trail,
            peak_trail
        )

    def trail_pct_for_peak_multiple(
        self,
        peak_multiple
    ):

        peak_multiple = safe_float(
            peak_multiple,
            1
        )

        if peak_multiple >= 4:
            return 0.50

        if peak_multiple >= 2.50:
            return 0.45

        if peak_multiple >= 2.0:
            return 0.40

        if peak_multiple >= 1.50:
            return 0.35

        return 0.30

    def trail_pct_for_pressure(
        self,
        pressure
    ):

        if pressure >= 85:
            return 0.30

        if pressure >= 70:
            return 0.24

        if pressure >= 55:
            return 0.18

        if pressure >= 40:
            return 0.13

        return 0.09

    def liquidity_adjusted_pressure(
        self,
        pressure,
        liquidity_exit_reason
    ):

        if not liquidity_exit_reason:
            return pressure

        return min(
            safe_float(pressure, 0),
            POSITION_LIQUIDITY_COLLAPSE_PRESSURE_CAP
        )

    def liquidity_collapse_reason(
        self,
        position,
        metrics
    ):

        if not POSITION_LIQUIDITY_COLLAPSE_EXIT_ENABLED:
            return None

        current_liquidity = safe_float(
            metrics.liquidity,
            0
        )
        entry_liquidity = safe_float(
            position.get("entry_liquidity"),
            current_liquidity
        )
        peak_liquidity = safe_float(
            position.get("peak_liquidity"),
            max(entry_liquidity, current_liquidity)
        )
        reference_liquidity = max(
            entry_liquidity,
            peak_liquidity
        )

        if (
            reference_liquidity
            < POSITION_LIQUIDITY_COLLAPSE_MIN_REFERENCE_USD
        ):
            return None

        entry_drain = (
            entry_liquidity > 0
            and current_liquidity
            <= entry_liquidity
            * (1 - POSITION_LIQUIDITY_COLLAPSE_FROM_ENTRY_PCT)
        )
        peak_drain = (
            peak_liquidity > 0
            and current_liquidity
            <= peak_liquidity
            * (1 - POSITION_LIQUIDITY_COLLAPSE_FROM_PEAK_PCT)
        )

        if entry_drain:
            return "liquidity_drain_from_entry"

        if peak_drain:
            return "liquidity_drain_from_peak"

        return None

    def sell_only_flow_exit_reason(
        self,
        position,
        metrics
    ):

        if not POSITION_SELL_ONLY_FLOW_EXIT_ENABLED:
            return None

        entry_price = max(
            safe_float(position.get("entry_price"), 0),
            1e-18
        )
        price_multiple = metrics.price / entry_price

        if (
            price_multiple
            > POSITION_SELL_ONLY_FLOW_MAX_PRICE_MULTIPLE
        ):
            return None

        buy_volume_5m = safe_float(
            metrics.buy_volume_5m,
            0
        )
        sell_volume_5m = safe_float(
            metrics.sell_volume_5m,
            0
        )
        entry_notional = safe_float(
            position.get("entry_notional_usd"),
            (
                safe_float(position.get("entry_size_sol"), 0)
                * safe_float(
                    position.get("entry_sol_usd"),
                    self.current_sol_usd()
                )
            )
        )
        min_sell_volume = max(
            POSITION_SELL_ONLY_FLOW_MIN_SELL_VOLUME_5M_USD,
            entry_notional
            * POSITION_SELL_ONLY_FLOW_MIN_SELL_ENTRY_NOTIONAL_MULTIPLE
        )

        if sell_volume_5m < min_sell_volume:
            return None

        buy_sell_volume_ratio = (
            buy_volume_5m
            / max(sell_volume_5m, 1e-18)
        )

        if (
            buy_volume_5m
            > POSITION_SELL_ONLY_FLOW_MAX_BUY_VOLUME_5M_USD
            and buy_sell_volume_ratio
            > POSITION_SELL_ONLY_FLOW_MAX_BUY_SELL_VOLUME_RATIO
        ):
            return None

        return "sell_only_flow_exit"

    def pressure_exit_reason(
        self,
        position,
        metrics,
        ignition_details,
        pressure
    ):

        if self.migration_zone_grace_active(position):
            return None

        price_multiple = (
            metrics.price
            / max(position["entry_price"], 1e-18)
        )
        impulse = safe_float(
            ignition_details.get("price_jump"),
            price_multiple
        )
        volume_liquidity_ratio = safe_float(
            ignition_details.get(
                "volume_liquidity_ratio"
            ),
            0
        )
        buy_sell_ratio = safe_float(
            ignition_details.get(
                "buy_sell_ratio"
            ),
            (
                metrics.buys_5m
                / max(metrics.sells_5m, 1)
            )
        )

        # liquidity-collapse / sell-only-flow are NOT re-checked here: the
        # sole caller (manage_position) evaluates both catastrophic exits
        # against the same metrics before this method runs.
        if (
            price_multiple
            <= 1 - self.initial_stop_loss_pct(position=position)
        ):
            return "hard_stop_loss"

        strict_weak_signals = (
            int(
                pressure
                <= POSITION_STRICT_EARLY_EXIT_MAX_PRESSURE
            )
            + int(
                volume_liquidity_ratio
                <= POSITION_STRICT_EARLY_EXIT_MAX_VOLUME_LIQUIDITY_RATIO
            )
            + int(
                buy_sell_ratio
                <= POSITION_STRICT_EARLY_EXIT_MAX_BUY_SELL_RATIO
            )
        )

        strict_triggered = (
            POSITION_STRICT_EARLY_EXIT_ENABLED
            and price_multiple
            <= 1 - POSITION_STRICT_EARLY_EXIT_LOSS_PCT
            and strict_weak_signals
            >= POSITION_STRICT_EARLY_EXIT_MIN_WEAK_SIGNALS
        )

        if strict_triggered:
            # Confirm over consecutive scans: a transient dip + soft tick
            # reverts and the count resets, so only a persistent failure cuts.
            count = int(
                safe_float(position.get("strict_early_breach_count"), 0)
            ) + 1
            position["strict_early_breach_count"] = count
            self.state_dirty = True
            if count >= max(POSITION_STRICT_EARLY_EXIT_CONFIRM_TICKS, 1):
                position["strict_early_breach_count"] = 0
                return "strict_early_failure_exit"
        elif position.get("strict_early_breach_count"):
            position["strict_early_breach_count"] = 0
            self.state_dirty = True

        if not POSITION_PRESSURE_LOSS_EXIT_ENABLED:
            return None

        if (
            price_multiple
            <= 1 - POSITION_PRESSURE_EXIT_MAX_LOSS_PCT
            and pressure
            <= POSITION_PRESSURE_EXIT_MAX_PRESSURE
            and (
                impulse
                <= POSITION_PRESSURE_EXIT_MAX_IMPULSE
                or volume_liquidity_ratio
                <= POSITION_PRESSURE_EXIT_MAX_VOLUME_LIQUIDITY_RATIO
                or buy_sell_ratio
                <= POSITION_PRESSURE_EXIT_MAX_BUY_SELL_RATIO
            )
        ):
            return "pressure_loss_cut"

        if (
            price_multiple < 1
            and pressure <= 25
            and impulse <= 1
            and volume_liquidity_ratio <= 0.20
        ):
            return "failed_followthrough"

        return None

    def signal_decay_reason(
        self,
        position,
        metrics,
        recent_snapshots
    ):

        if self.migration_zone_grace_active(position):
            return None

        if not POSITION_SCORE_DECAY_EXIT_ENABLED:
            return None

        if not recent_snapshots:
            return None

        latest = recent_snapshots[-1]
        price_multiple = safe_float(
            latest.get("price_multiple"),
            (
                safe_float(latest.get("price"), 0)
                / max(position.get("entry_price", 0), 1e-18)
            )
        )
        latest_score = safe_float(
            latest.get("score"),
            position.get("entry_score", 0)
        )
        entry_score = safe_float(
            position.get("entry_score"),
            latest_score
        )

        score_decayed = (
            entry_score - latest_score
            >= POSITION_DECAY_SCORE_DROP
            and price_multiple
            < POSITION_SCORE_DECAY_MAX_PRICE_MULTIPLE
        )

        lookback = recent_snapshots[
            -POSITION_DECAY_LOOKBACK_SCANS:
        ]

        if len(lookback) < 2:
            return None

        low_pressure = all(
            safe_float(snapshot.get("pressure"), 0)
            <= POSITION_DECAY_MAX_PRESSURE
            for snapshot in lookback
        )
        weak_flow = all(
            safe_float(
                snapshot.get("volume_liquidity_ratio"),
                0
            )
            <= POSITION_DECAY_MAX_VOLUME_LIQUIDITY_RATIO
            and safe_float(
                snapshot.get("buy_sell_ratio"),
                0
            )
            <= POSITION_DECAY_MAX_BUY_SELL_RATIO
            for snapshot in lookback
        )
        weak_impulse = all(
            safe_float(snapshot.get("impulse"), 0)
            <= 1.10
            for snapshot in lookback
        )

        if not score_decayed:
            return None

        if (
            low_pressure
        ):
            return "score_pressure_decay"

        if (
            weak_flow
        ):
            return "score_flow_decay"

        if (
            price_multiple < 1
            and weak_impulse
            and low_pressure
        ):
            return "score_weak_red_decay"

        return None

    def scale_out_if_needed(
        self,
        position,
        metrics,
        ignition_details,
        pressure,
        now
    ):

        price_multiple = (
            metrics.price
            / max(position["entry_price"], 1e-18)
        )

        if price_multiple <= 1:
            return None

        target_scaled, target_multiple = self.target_scale_out(
            position,
            price_multiple,
            pressure
        )

        current_scaled = position.get(
            "scaled_out_pct",
            0
        )

        sell_pct = max(
            target_scaled - current_scaled,
            0
        )

        if sell_pct < POSITION_MIN_SCALE_OUT_STEP_PCT:
            return None

        max_scale_out_pct = self.max_scale_out_pct(
            position
        )
        sell_pct = min(
            sell_pct,
            max_scale_out_pct - current_scaled
        )

        if sell_pct <= 0:
            return None

        sell_tokens = min(
            position["entry_size_tokens"] * sell_pct,
            position["remaining_tokens"]
        )

        proceeds = sell_tokens * metrics.price
        sol_usd = self.current_sol_usd()
        proceeds_sol = proceeds / max(
            sol_usd,
            1e-18
        )

        position["remaining_tokens"] -= sell_tokens
        position["realized_usd"] += proceeds
        position["scaled_out_pct"] = min(
            current_scaled + sell_pct,
            POSITION_MAX_SCALE_OUT_PCT
        )
        if (
            position["scaled_out_pct"]
            >= self.take_profit_sell_pct(position)
        ):
            position["take_profit_filled"] = True

        state = self.load_state()
        state["cash_sol"] = (
            safe_float(
                state.get("cash_sol"),
                0
            )
            + proceeds_sol
        )

        reason = (
            f"scale_out_{target_multiple:.2f}x "
            f"target {position['scaled_out_pct']:.0%}"
        )

        self.add_event(
            position,
            "scale_out",
            now,
            metrics.price,
            pressure,
            reason,
            size_pct=sell_pct,
            proceeds_usd=proceeds,
            proceeds_sol=proceeds_sol,
            sol_usd=sol_usd
        )

        print(
            "PAPER TRADE SCALE OUT "
            f"{metrics.symbol} "
            f"{sell_pct:.0%} "
            f"Price=${metrics.price:.8f} "
            f"Pressure={pressure:.1f} "
            f"Cash={state['cash_sol']:.2f} SOL "
            f"Remaining={position['remaining_tokens']:.2f}"
        )

        return self.build_notification_event(
            "scale_out",
            position,
            metrics,
            pressure,
            reason,
            size_pct=sell_pct,
            proceeds_usd=proceeds,
            proceeds_sol=proceeds_sol,
            sol_usd=sol_usd,
            ignition_details=ignition_details
        )

    def target_scale_out(
        self,
        position,
        price_multiple,
        pressure
    ):

        current_scaled = safe_float(
            position.get("scaled_out_pct"),
            0
        )
        target = current_scaled
        target_multiple = 0

        for multiple, target_pct in self.scale_out_ladder(position):
            multiple = safe_float(
                multiple,
                0
            )
            target_pct = safe_float(
                target_pct,
                0
            )

            if multiple <= 1 or target_pct <= target:
                continue

            if price_multiple < multiple:
                continue

            target = target_pct
            target_multiple = multiple

        return (
            min(
                target,
                self.max_scale_out_pct(position)
            ),
            target_multiple
        )

    def hyperevm_position(
        self,
        position
    ):

        return self.hyperevm_chain(
            position.get("chain")
        )

    def scale_out_ladder(
        self,
        position
    ):

        if self.hyperevm_position(position):
            return POSITION_HYPEREVM_SCALE_OUT_LADDER

        entry_route = str(position.get("entry_route", "") or "")

        if entry_route == "bonding_momentum_high_conviction":
            return POSITION_HC_SCALE_OUT_LADDER

        if entry_route == "bonding_early_revival":
            return POSITION_EARLY_REVIVAL_SCALE_OUT_LADDER

        if entry_route == "migrated_revival":
            return POSITION_MIGRATED_REVIVAL_SCALE_OUT_LADDER

        return POSITION_SCALE_OUT_LADDER

    def adaptive_initial_stop_pct(self, address, entry_price):
        """Volatility-scaled initial stop % (downside-ATR), or None when disabled
        / candles thin -> caller falls back to the flat per-route %. Shared with
        discovery.manager via trading.adaptive_stop so both engines match."""
        from trading.adaptive_stop import adaptive_initial_stop_pct
        return adaptive_initial_stop_pct(address, entry_price)

    def initial_stop_loss_pct(
        self,
        position=None,
        ignition_details=None
    ):

        if position is not None:
            stored = position.get("initial_stop_pct")
            if stored is not None:
                return safe_float(stored, POSITION_INITIAL_STOP_LOSS_PCT)

        route = ""

        if position is not None:
            route = str(position.get("entry_route", "") or "")

        if not route and ignition_details is not None:
            route = str(ignition_details.get("alert_route", "") or "")

        if route == "bonding_momentum_high_conviction":
            return POSITION_HC_INITIAL_STOP_LOSS_PCT

        if route == "bonding_early_revival":
            return POSITION_EARLY_REVIVAL_INITIAL_STOP_LOSS_PCT

        if route == "migrated_revival":
            return POSITION_MIGRATED_REVIVAL_INITIAL_STOP_LOSS_PCT

        return POSITION_INITIAL_STOP_LOSS_PCT

    def take_profit_sell_pct(
        self,
        position
    ):

        if self.hyperevm_position(position):
            return POSITION_HYPEREVM_TAKE_PROFIT_SELL_PCT

        return POSITION_TAKE_PROFIT_SELL_PCT

    def max_scale_out_pct(
        self,
        position
    ):

        if self.hyperevm_position(position):
            return POSITION_HYPEREVM_MAX_SCALE_OUT_PCT

        return POSITION_MAX_SCALE_OUT_PCT

    def target_scale_out_pct(
        self,
        position,
        price_multiple,
        pressure
    ):

        target, _ = self.target_scale_out(
            position,
            price_multiple,
            pressure
        )

        return target

    def next_scale_out_target(
        self,
        position,
        price_multiple=None
    ):

        current_scaled = safe_float(
            position.get("scaled_out_pct"),
            0
        )
        next_target = None

        for multiple, target_pct in self.scale_out_ladder(position):
            multiple = safe_float(
                multiple,
                0
            )
            target_pct = safe_float(
                target_pct,
                0
            )

            if target_pct <= current_scaled + 1e-9:
                continue

            next_target = {
                "multiple": multiple,
                "target_pct": min(
                    target_pct,
                    self.max_scale_out_pct(position)
                )
            }
            break

        return next_target

    def close_position(
        self,
        position,
        metrics,
        pressure,
        now,
        reason
    ):

        state = self.load_state()
        sol_usd = self.current_sol_usd()
        remaining_tokens = position.get(
            "remaining_tokens",
            0
        )
        proceeds = remaining_tokens * metrics.price
        proceeds_sol = proceeds / max(
            sol_usd,
            1e-18
        )

        position["realized_usd"] += proceeds
        position["remaining_tokens"] = 0
        position["exit_at"] = now
        position["exit_price"] = metrics.price
        position["status"] = "closed"
        position["close_reason"] = reason

        entry_notional = max(
            position.get("entry_notional_usd", 0),
            1e-18
        )
        pnl_usd = (
            position["realized_usd"]
            - entry_notional
        )
        pnl_pct = (
            pnl_usd
            / entry_notional
        )

        position["pnl_usd"] = pnl_usd
        position["pnl_pct"] = pnl_pct

        self.add_event(
            position,
            "close",
            now,
            metrics.price,
            pressure,
            reason,
            proceeds_usd=proceeds,
            proceeds_sol=proceeds_sol,
            sol_usd=sol_usd
        )

        state["cash_sol"] = (
            safe_float(
                state.get("cash_sol"),
                0
            )
            + proceeds_sol
        )

        if (
            POSITION_TRAILING_REBOUND_REENTRY_ENABLED
            and self.trailing_rebound_close_reason(reason)
        ):
            watch_expires_at = (
                now
                + POSITION_TRAILING_REBOUND_WATCH_SECONDS
            )
            position["trailing_rebound_watch_active"] = True
            position["trailing_rebound_watch_expires_at"] = watch_expires_at
            state.setdefault(
                "trailing_rebound_watch",
                {}
            )[str(position["address"])] = {
                "address": position["address"],
                "chain": position.get("chain", "solana"),
                "symbol": position.get("symbol", ""),
                "closed_at": now,
                "expires_at": watch_expires_at,
                "close_reason": reason,
                "exit_price": metrics.price,
                "last_price": metrics.price,
                "high_post_exit_price": metrics.price,
                "low_post_exit_price": metrics.price,
                "seen_count": 0,
                "reclaim_scan_count": 0,
                "last_reclaimed": False,
                "trailing_stop_price": safe_float(
                    position.get("trailing_stop_price"),
                    0
                ),
                "peak_price": safe_float(
                    position.get("peak_price"),
                    0
                ),
                "scaled_out_pct": safe_float(
                    position.get("scaled_out_pct"),
                    0
                )
            }

        state["open"].pop(
            position["address"],
            None
        )
        state["closed"].append(position)
        state["closed"] = state["closed"][
            -POSITION_CLOSED_POSITION_LIMIT:
        ]

        self.save_state()

        print(
            "PAPER TRADE CLOSE "
            f"{metrics.symbol} "
            f"Reason={reason} "
            f"Price=${metrics.price:.8f} "
            f"Cash={state['cash_sol']:.2f} SOL "
            f"PnL=${pnl_usd:.2f} "
            f"PnL={pnl_pct:.1%}"
        )

        return self.build_notification_event(
            "close",
            position,
            metrics,
            pressure,
            reason,
            proceeds_usd=proceeds,
            proceeds_sol=proceeds_sol,
            sol_usd=sol_usd
        )

    def add_event(
        self,
        position,
        event_type,
        now,
        price,
        pressure,
        reason,
        size_pct=0,
        proceeds_usd=0,
        proceeds_sol=0,
        sol_usd=0
    ):

        events = position.setdefault(
            "events",
            []
        )

        events.append({
            "type": event_type,
            "timestamp": now,
            "price": price,
            "pressure": pressure,
            "reason": reason,
            "size_pct": size_pct,
            "proceeds_usd": proceeds_usd,
            "proceeds_sol": proceeds_sol,
            "sol_usd": sol_usd or self.current_sol_usd()
        })

        if len(events) > 30:
            del events[:-30]

    def build_notification_event(
        self,
        event_type,
        position,
        metrics,
        pressure,
        reason,
        size_pct=0,
        proceeds_usd=0,
        proceeds_sol=0,
        sol_usd=0,
        ignition_details=None
    ):

        ignition_details = ignition_details or {}
        snapshot = dict(position)
        unrealized_usd = (
            snapshot.get("remaining_tokens", 0)
            * metrics.price
        )
        equity_usd = (
            snapshot.get("realized_usd", 0)
            + unrealized_usd
        )
        entry_notional = max(
            snapshot.get("entry_notional_usd", 0),
            1e-18
        )
        pnl_usd = equity_usd - entry_notional
        pnl_pct = pnl_usd / entry_notional
        price_multiple = (
            metrics.price
            / max(snapshot.get("entry_price", 0), 1e-18)
        )
        next_scale = self.next_scale_out_target(
            snapshot,
            price_multiple=price_multiple
        ) or {}

        return {
            "type": event_type,
            "timestamp": snapshot.get(
                "last_update_at",
                snapshot.get("entry_at")
            ),
            "address": snapshot.get("address"),
            "chain": snapshot.get("chain", "solana"),
            "symbol": snapshot.get("symbol"),
            "name": snapshot.get("name")
            or getattr(metrics, "name", ""),
            "pair_address": snapshot.get("pair_address"),
            "status": snapshot.get("status", "open"),
            "reason": reason,
            "entry_price": snapshot.get("entry_price", 0),
            "last_price": metrics.price,
            "peak_price": snapshot.get("peak_price", metrics.price),
            "trailing_stop_price": snapshot.get(
                "trailing_stop_price",
                0
            ),
            "trailing_stop_mode": snapshot.get(
                "trailing_stop_mode",
                "standard"
            ),
            "entry_size_sol": snapshot.get(
                "entry_size_sol",
                self.position_entry_sol(snapshot)
            ),
            "entry_notional_usd": snapshot.get(
                "entry_notional_usd",
                0
            ),
            "entry_sol_usd": snapshot.get(
                "entry_sol_usd",
                self.current_sol_usd()
            ),
            "cash_sol": safe_float(
                self.load_state().get("cash_sol"),
                0
            ),
            "realized_usd": snapshot.get("realized_usd", 0),
            "unrealized_usd": unrealized_usd,
            "equity_usd": equity_usd,
            "pnl_usd": pnl_usd,
            "pnl_pct": pnl_pct,
            "scaled_out_pct": snapshot.get("scaled_out_pct", 0),
            "size_pct": size_pct,
            "proceeds_usd": proceeds_usd,
            "proceeds_sol": proceeds_sol,
            "sol_usd": sol_usd or self.current_sol_usd(),
            "entry_pressure": snapshot.get("entry_pressure", pressure),
            "last_pressure": pressure,
            "peak_pressure": snapshot.get("peak_pressure", pressure),
            "entry_score": snapshot.get("entry_score", 0),
            "entry_quality_tier": snapshot.get(
                "entry_quality_tier",
                ""
            ),
            "entry_volume_multiple": snapshot.get(
                "entry_volume_multiple",
                0
            ),
            "entry_buy_sell_volume_ratio": snapshot.get(
                "entry_buy_sell_volume_ratio",
                0
            ),
            "entry_buy_volume_5m": snapshot.get(
                "entry_buy_volume_5m",
                0
            ),
            "entry_sell_volume_5m": snapshot.get(
                "entry_sell_volume_5m",
                0
            ),
            "entry_buy_sell_volume_source_5m": snapshot.get(
                "entry_buy_sell_volume_source_5m",
                ""
            ),
            "entry_confirmation_score": snapshot.get(
                "entry_confirmation_score",
                safe_float(
                    ignition_details.get("entry_confirmation_score"),
                    0
                )
            ),
            "entry_confirmation_ready": snapshot.get(
                "entry_confirmation_ready",
                ignition_details.get("entry_confirmation_ready", False)
            ),
            "entry_confirmation_confirmed_scans": snapshot.get(
                "entry_confirmation_confirmed_scans",
                ignition_details.get(
                    "entry_confirmation_confirmed_scans",
                    0
                )
            ),
            "entry_confirmation_required_scans": ignition_details.get(
                "entry_confirmation_required_scans",
                POSITION_ENTRY_CONFIRMATION_REQUIRED_SCANS
            ),
            "entry_confirmation_shadow_mode": snapshot.get(
                "entry_confirmation_shadow_mode",
                ignition_details.get(
                    "entry_confirmation_shadow_mode",
                    False
                )
            ),
            "entry_confirmation_reason": snapshot.get(
                "entry_confirmation_reason",
                ignition_details.get("entry_confirmation_reason", "")
            ),
            "fdv": metrics.fdv,
            "volume_1h": metrics.volume_1h,
            "migration_fdv": getattr(metrics, "migration_fdv", 0),
            "migration_distance_usd": getattr(
                metrics,
                "migration_distance_usd",
                0
            ),
            "migration_distance_pct": getattr(
                metrics,
                "migration_distance_pct",
                0
            ),
            "entry_impulse": snapshot.get(
                "entry_impulse",
                safe_float(
                    ignition_details.get("price_jump"),
                    0
                )
            ),
            "anchored_vwap_ready": ignition_details.get(
                "anchored_vwap_ready",
                False
            ),
            "anchored_vwap": ignition_details.get(
                "anchored_vwap",
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
            "anchored_vwap_source": ignition_details.get(
                "anchored_vwap_source",
                ""
            ),
            "exit_quote_checked": ignition_details.get(
                "exit_quote_checked",
                False
            ),
            "exit_quote_available": ignition_details.get(
                "exit_quote_available",
                False
            ),
            "exit_quote_provider": ignition_details.get(
                "exit_quote_provider",
                ""
            ),
            "exit_quote_value_usd": ignition_details.get(
                "exit_quote_value_usd",
                0
            ),
            "exit_quote_min_value_usd": ignition_details.get(
                "exit_quote_min_value_usd",
                0
            ),
            "exit_quote_price_impact_pct": ignition_details.get(
                "exit_quote_price_impact_pct",
                0
            ),
            "exit_quote_error": ignition_details.get(
                "exit_quote_error",
                ""
            ),
            "exit_quote_attempt_name": ignition_details.get(
                "exit_quote_attempt_name",
                ""
            ),
            "exit_quote_attempt_count": ignition_details.get(
                "exit_quote_attempt_count",
                0
            ),
            "exit_quote_fallback_used": ignition_details.get(
                "exit_quote_fallback_used",
                False
            ),
            "exit_quote_attempts": ignition_details.get(
                "exit_quote_attempts",
                []
            ),
            "next_scale_multiple": next_scale.get("multiple", 0),
            "next_scale_target_pct": next_scale.get("target_pct", 0),
            "trailing_rebound_watch_active": bool(
                snapshot.get("trailing_rebound_watch_active")
            ),
            "trailing_rebound_watch_expires_at": snapshot.get(
                "trailing_rebound_watch_expires_at",
                0
            ),
            "trailing_rebound_reentry": bool(
                snapshot.get("trailing_rebound_reentry")
            ),
            "price_multiple": price_multiple
        }

    def build_position_event(
        self,
        event_type,
        position,
        reason
    ):

        last_price = safe_float(
            position.get("last_price"),
            position.get("entry_price", 0)
        )
        remaining_tokens = safe_float(
            position.get("remaining_tokens"),
            0
        )
        unrealized_usd = (
            remaining_tokens
            * last_price
        )
        equity_usd = (
            safe_float(position.get("realized_usd"), 0)
            + unrealized_usd
        )
        entry_notional = max(
            safe_float(position.get("entry_notional_usd"), 0),
            1e-18
        )
        pnl_usd = equity_usd - entry_notional
        pnl_pct = pnl_usd / entry_notional

        return {
            "type": event_type,
            "timestamp": position.get("last_update_at"),
            "address": position.get("address"),
            "chain": position.get("chain", "solana"),
            "symbol": position.get("symbol"),
            "pair_address": position.get("pair_address"),
            "status": position.get("status", "open"),
            "reason": reason,
            "entry_price": position.get("entry_price", 0),
            "last_price": last_price,
            "peak_price": position.get("peak_price", last_price),
            "trailing_stop_price": position.get(
                "trailing_stop_price",
                0
            ),
            "entry_size_sol": position.get(
                "entry_size_sol",
                self.position_entry_sol(position)
            ),
            "entry_notional_usd": entry_notional,
            "cash_sol": safe_float(
                self.load_state().get("cash_sol"),
                0
            ),
            "realized_usd": position.get("realized_usd", 0),
            "unrealized_usd": unrealized_usd,
            "equity_usd": equity_usd,
            "pnl_usd": pnl_usd,
            "pnl_pct": pnl_pct,
            "scaled_out_pct": position.get("scaled_out_pct", 0),
            "size_pct": 0,
            "proceeds_usd": 0,
            "proceeds_sol": 0,
            "sol_usd": self.current_sol_usd(),
            "entry_pressure": position.get("entry_pressure", 0),
            "last_pressure": position.get("last_pressure", 0),
            "peak_pressure": position.get("peak_pressure", 0),
            "entry_score": position.get("entry_score", 0),
            "entry_impulse": position.get("entry_impulse", 0),
            "price_multiple": (
                last_price
                / max(position.get("entry_price", 0), 1e-18)
            )
        }

    def build_status_report(
        self,
        now,
        live_prices=None,
        live_refresh=None
    ):

        if (
            not POSITION_ENABLED
            or not POSITION_STATUS_REPORTS_ENABLED
        ):
            return None

        state = self.load_state()
        self.cleanup_trailing_rebound_watches(now)
        state = self.load_state()
        live_prices = live_prices or {}
        positions = []
        total_pnl = 0
        total_equity = 0
        rebound_watch = state.get("trailing_rebound_watch", {})
        active_rebound_watch_count = sum(
            1
            for watch in rebound_watch.values()
            if safe_float(watch.get("expires_at"), 0) > now
        )
        cash_sol = safe_float(
            state.get("cash_sol"),
            0
        )
        sol_usd = self.current_sol_usd()

        for key, position in state["open"].items():
            address = (
                position.get("address")
                or key
            )
            live_price = live_prices.get(
                address
            )
            last_price = safe_float(
                position.get("last_price"),
                position.get("entry_price", 0)
            )

            if live_price:
                last_price = safe_float(
                    live_price.get("price_usd"),
                    last_price
                )

            unrealized_usd = (
                position.get("remaining_tokens", 0)
                * last_price
            )
            equity_usd = (
                position.get("realized_usd", 0)
                + unrealized_usd
            )
            entry_notional = max(
                position.get("entry_notional_usd", 0),
                1e-18
            )
            pnl_usd = equity_usd - entry_notional
            pnl_pct = pnl_usd / entry_notional
            price_multiple = (
                last_price
                / max(position.get("entry_price", 0), 1e-18)
            )
            next_scale = self.next_scale_out_target(
                position,
                price_multiple=price_multiple
            ) or {}
            total_pnl += pnl_usd
            total_equity += equity_usd

            positions.append({
                "address": address,
                "chain": position.get("chain", "solana"),
                "symbol": position.get("symbol"),
                "pair_address": position.get("pair_address"),
                "entry_price": position.get("entry_price", 0),
                "last_price": last_price,
                "live_refreshed": bool(live_price),
                "live_pair_address": (
                    live_price.get("pair_address")
                    if live_price
                    else ""
                ),
                "live_liquidity_usd": (
                    live_price.get("liquidity_usd")
                    if live_price
                    else 0
                ),
                "live_volume_1h_usd": (
                    live_price.get("volume_1h_usd")
                    if live_price
                    else 0
                ),
                "peak_price": position.get("peak_price", last_price),
                "trailing_stop_price": position.get(
                    "trailing_stop_price",
                    0
                ),
                "entry_size_sol": position.get(
                    "entry_size_sol",
                    self.position_entry_sol(position)
                ),
                "entry_notional_usd": entry_notional,
                "realized_usd": position.get("realized_usd", 0),
                "equity_usd": equity_usd,
                "pnl_usd": pnl_usd,
                "pnl_pct": pnl_pct,
                "scaled_out_pct": position.get("scaled_out_pct", 0),
                "entry_pressure": position.get("entry_pressure", 0),
                "last_pressure": position.get("last_pressure", 0),
                "entry_impulse": position.get("entry_impulse", 0),
                "entry_confirmation_score": position.get(
                    "entry_confirmation_score",
                    0
                ),
                "entry_confirmation_ready": bool(
                    position.get("entry_confirmation_ready")
                ),
                "next_scale_multiple": next_scale.get("multiple", 0),
                "next_scale_target_pct": next_scale.get("target_pct", 0),
                "trailing_stop_mode": position.get(
                    "trailing_stop_mode",
                    "standard"
                ),
                "trailing_rebound_reentry": bool(
                    position.get("trailing_rebound_reentry")
                ),
                "price_multiple": price_multiple
            })

        if not positions:
            return None

        positions.sort(
            key=lambda item: item["pnl_usd"],
            reverse=True
        )

        return {
            "timestamp": now,
            "open_count": len(positions),
            "cash_sol": cash_sol,
            "cash_usd": cash_sol * sol_usd,
            "sol_usd": sol_usd,
            "total_equity_usd": total_equity,
            "total_account_equity_usd": (
                total_equity
                + cash_sol * sol_usd
            ),
            "total_pnl_usd": total_pnl,
            "trailing_rebound_watch_count": active_rebound_watch_count,
            "positions": positions,
            "live_refresh": live_refresh or {
                "enabled": False
            }
        }
