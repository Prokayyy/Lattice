#!/usr/bin/env python3
"""
Targeted rollback for the 2026-05-22 entry-anchored VWAP trailing experiment.

Run from the repo root:
    python3 tools/revert_vwap_hybrid_trailing_2026_05_22.py

This avoids using a broad git diff because this worktree already had many
unrelated local changes before the experiment was added.
"""

from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]


def path_for(relative_path):
    return ROOT / relative_path


def read(relative_path):
    return path_for(relative_path).read_text()


def write(relative_path, content):
    path_for(relative_path).write_text(content)


def replace_once(relative_path, current, previous, optional=False):
    content = read(relative_path)

    if current not in content:
        if previous in content:
            print(f"{relative_path}: already reverted")
            return False

        if optional:
            print(f"{relative_path}: block not present, skipped")
            return False

        raise RuntimeError(f"{relative_path}: expected block was not found")

    write(relative_path, content.replace(current, previous, 1))
    print(f"{relative_path}: reverted block")
    return True


def function_span(content, name, indent=""):
    pattern = re.compile(
        rf"(?m)^{re.escape(indent)}(?:async\s+)?def {re.escape(name)}\("
    )
    match = pattern.search(content)

    if not match:
        return None

    next_pattern = re.compile(
        rf"(?m)^{re.escape(indent)}(?:async\s+)?def [A-Za-z_][A-Za-z0-9_]*\("
    )
    next_match = next_pattern.search(
        content,
        match.end()
    )
    end = next_match.start() if next_match else len(content)
    return match.start(), end


def replace_function(relative_path, name, previous_source, indent=""):
    content = read(relative_path)
    span = function_span(
        content,
        name,
        indent=indent
    )

    if not span:
        if previous_source.strip() in content:
            print(f"{relative_path}:{name}: already reverted")
            return False

        raise RuntimeError(f"{relative_path}:{name}: function was not found")

    start, end = span
    replacement = previous_source.rstrip() + "\n\n"
    write(
        relative_path,
        content[:start] + replacement + content[end:]
    )
    print(f"{relative_path}:{name}: restored previous function")
    return True


def remove_function(relative_path, name, indent=""):
    content = read(relative_path)
    span = function_span(
        content,
        name,
        indent=indent
    )

    if not span:
        print(f"{relative_path}:{name}: not present, skipped")
        return False

    start, end = span
    write(
        relative_path,
        content[:start] + content[end:]
    )
    print(f"{relative_path}:{name}: removed")
    return True


PREVIOUS_SHOULD_REFRESH = """def should_refresh_anchored_vwap_provider(
    metrics,
    now
):

    if not ANCHORED_VWAP_PROVIDER_REFRESH_ENABLED:
        return False

    if not getattr(metrics, "pair_address", ""):
        return False

    key = (
        str(getattr(metrics, "chain", "") or "solana").lower(),
        str(getattr(metrics, "address", "")),
        str(getattr(metrics, "pair_address", ""))
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
"""


PREVIOUS_FETCH_PROVIDER_AVWAP = """async def fetch_provider_anchored_vwap_candles(
    metrics,
    now
):

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
"""


PREVIOUS_UPDATE_AVWAP = """async def update_anchored_vwap(
    metrics,
    ignition_details,
    now,
    source_label,
    provider_refresh_allowed=False
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

        local_candles = await scanner_storage.load_token_candles(
            metrics.address,
            timeframe_seconds=ANCHORED_VWAP_TIMEFRAME_SECONDS,
            limit=ANCHORED_VWAP_CANDLE_LIMIT,
            until=now
        )
        signal = anchored_vwap_from_low(
            local_candles,
            lookback_seconds=ANCHORED_VWAP_LOOKBACK_SECONDS,
            until=now,
            min_candles=ANCHORED_VWAP_MIN_CANDLES
        )
        source = "scanner_candles"

        if (
            provider_refresh_allowed
            and should_refresh_anchored_vwap_provider(metrics, now)
        ):
            provider_candles = await fetch_provider_anchored_vwap_candles(
                metrics,
                now
            )

            if provider_candles:
                signal = anchored_vwap_from_low(
                    provider_candles,
                    lookback_seconds=ANCHORED_VWAP_LOOKBACK_SECONDS,
                    until=now,
                    min_candles=ANCHORED_VWAP_MIN_CANDLES
                )
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
"""


PREVIOUS_TRAILING_EXIT = """    def trailing_exit_reason(
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

        if (
            self.runner_rsi_trail_controls_position(
                position,
                ignition_details
            )
        ):
            runner_stop = self.active_runner_rsi_stop_price(
                position,
                trailing_stop
            )

            quote_triggered = self.quote_stop_triggered(
                position,
                runner_stop,
                ignition_details
            )

            if quote_triggered is True:
                return "runner_rsi_peak_trail_quote"

            if quote_triggered is False:
                return None

            if (
                LIVE_EXECUTION_REQUIRE_EXIT_QUOTE_FOR_STOPS
                and LIVE_EXECUTION_USE_QUOTES_FOR_STOPS
            ):
                return None

            if runner_stop > 0 and price <= runner_stop:
                return "runner_rsi_peak_trail"

            return None

        quote_triggered = self.quote_stop_triggered(
            position,
            trailing_stop,
            ignition_details
        )

        if quote_triggered is True:
            return "trailing_stop_quote"

        if quote_triggered is False:
            return None

        if (
            LIVE_EXECUTION_REQUIRE_EXIT_QUOTE_FOR_STOPS
            and LIVE_EXECUTION_USE_QUOTES_FOR_STOPS
        ):
            return None

        if price > trailing_stop:
            return None

        return "trailing_stop"
"""


PREVIOUS_UPDATE_TRAILING = """    def update_trailing_stop(
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
                * (1 - POSITION_INITIAL_STOP_LOSS_PCT)
            )

        if (
            self.runner_rsi_trail_controls_position(
                position,
                ignition_details
            )
        ):
            current_stop = safe_float(
                position.get("trailing_stop_price"),
                0
            )
            runner_stop = self.active_runner_rsi_stop_price(
                position,
                current_stop
            )
            if runner_stop > 0:
                position["trailing_stop_price"] = runner_stop
                position[
                    "trailing_stop_mode"
                ] = "runner_rsi_peak_trail"
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
"""


def main():
    replace_once(
        "config.py",
        """ANCHORED_VWAP_TRAILING_STOP_BUFFER_PCT = _env_float(
    "ANCHORED_VWAP_TRAILING_STOP_BUFFER_PCT",
    0.10
)

ANCHORED_VWAP_TRAILING_ACTIVATE_PROFIT_PCT = _env_float(
    "ANCHORED_VWAP_TRAILING_ACTIVATE_PROFIT_PCT",
    0.10
)

ANCHORED_VWAP_PEAK_TRAIL_PCT = _env_float(
    "ANCHORED_VWAP_PEAK_TRAIL_PCT",
    0.18
)

ANCHORED_VWAP_PEAK_TRAIL_MIN_MULTIPLE = _env_float(
    "ANCHORED_VWAP_PEAK_TRAIL_MIN_MULTIPLE",
    4.00
)

ANCHORED_VWAP_STOP_CONFIRMATION_TICKS = _env_int(
    "ANCHORED_VWAP_STOP_CONFIRMATION_TICKS",
    2
)
""",
        """ANCHORED_VWAP_TRAILING_STOP_BUFFER_PCT = _env_float(
    "ANCHORED_VWAP_TRAILING_STOP_BUFFER_PCT",
    0.01
)
"""
    )

    env_current = """ANCHORED_VWAP_TRAILING_STOP_BUFFER_PCT=0.10
ANCHORED_VWAP_TRAILING_ACTIVATE_PROFIT_PCT=0.10
ANCHORED_VWAP_PEAK_TRAIL_PCT=0.18
ANCHORED_VWAP_PEAK_TRAIL_MIN_MULTIPLE=4.00
ANCHORED_VWAP_STOP_CONFIRMATION_TICKS=2
ANCHORED_VWAP_LOOKBACK_SECONDS=3600"""
    env_previous = """ANCHORED_VWAP_TRAILING_STOP_BUFFER_PCT=0.01
ANCHORED_VWAP_LOOKBACK_SECONDS=3600"""

    replace_once(
        ".env",
        env_current,
        env_previous,
        optional=True
    )
    replace_once(
        ".env.example",
        env_current,
        env_previous
    )

    replace_once(
        "main.py",
        """from trading.local_rsi import (
    anchored_vwap_from_low,
    anchored_vwap_from_time,
    local_rsi_signal_from_candles
)
""",
        """from trading.local_rsi import (
    anchored_vwap_from_low,
    local_rsi_signal_from_candles
)
"""
    )
    replace_function(
        "main.py",
        "should_refresh_anchored_vwap_provider",
        PREVIOUS_SHOULD_REFRESH
    )
    replace_function(
        "main.py",
        "fetch_provider_anchored_vwap_candles",
        PREVIOUS_FETCH_PROVIDER_AVWAP
    )
    replace_function(
        "main.py",
        "update_anchored_vwap",
        PREVIOUS_UPDATE_AVWAP
    )
    replace_once(
        "main.py",
        """    await update_anchored_vwap(
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
""",
        """    await update_anchored_vwap(
        metrics,
        ignition_details,
        now,
        "open_position_monitor",
        provider_refresh_allowed=True
    )
"""
    )

    remove_function(
        "trading/local_rsi.py",
        "anchored_vwap_from_time"
    )

    replace_once(
        "trading/paper.py",
        """    ANCHORED_VWAP_PEAK_TRAIL_MIN_MULTIPLE,
    ANCHORED_VWAP_PEAK_TRAIL_PCT,
    ANCHORED_VWAP_STOP_CONFIRMATION_TICKS,
    ANCHORED_VWAP_TRAILING_ACTIVATE_PROFIT_PCT,
""",
        ""
    )
    remove_function(
        "trading/paper.py",
        "confirmed_trailing_exit_reason",
        indent="    "
    )
    replace_function(
        "trading/paper.py",
        "trailing_exit_reason",
        PREVIOUS_TRAILING_EXIT,
        indent="    "
    )
    replace_function(
        "trading/paper.py",
        "update_trailing_stop",
        PREVIOUS_UPDATE_TRAILING,
        indent="    "
    )
    remove_function(
        "trading/paper.py",
        "anchored_vwap_hybrid_trailing_stop",
        indent="    "
    )

    print(
        "\nRevert complete. Run: "
        "python3 -m py_compile config.py trading/local_rsi.py main.py trading/paper.py"
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"revert failed: {exc}", file=sys.stderr)
        sys.exit(1)
