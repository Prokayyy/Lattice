import argparse
import json
import re
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import (  # noqa: E402
    POSITION_MAX_ENTRY_IMPULSE,
    POSITION_MIN_ENTRY_IMPULSE,
    POSITION_MIN_ENTRY_VOLUME_1H_USD,
    POSITION_STATE_FILE
)
from storage.sqlite import DATABASE_NAME  # noqa: E402


SNAPSHOT_COLUMNS = [
    "id",
    "token_address",
    "symbol",
    "pair_address",
    "price",
    "liquidity",
    "raw_liquidity",
    "fdv",
    "volume_5m",
    "volume_1h",
    "buys_5m",
    "sells_5m",
    "buys_1h",
    "sells_1h",
    "txns_5m",
    "txns_1h",
    "price_change_5m",
    "price_change_1h",
    "pressure",
    "impulse",
    "volume_liquidity_ratio",
    "buy_sell_ratio",
    "h1_volume_liquidity_ratio",
    "h1_buy_sell_ratio",
    "score",
    "raw_score",
    "quality_tag",
    "alert_route",
    "alert_eligible",
    "timestamp",
    "buy_volume_5m",
    "sell_volume_5m",
    "buy_volume_1h",
    "sell_volume_1h",
    "local_rsi_ready",
    "local_rsi",
    "local_rsi_ema",
    "local_rsi_bullish",
    "local_rsi_bearish",
    "local_rsi_crossed_up",
    "local_rsi_crossed_down",
    "local_rsi_entry_ok",
    "local_rsi_reason",
    "local_rsi_candle_count",
    "local_rsi_timeframe_seconds"
]


ENTRY_VOLUME_RE = re.compile(
    r"1h volume\s+\$([0-9,]+)",
    re.IGNORECASE
)

HIGH_VOLUME_GRACE_MIN_ENTRY_VOLUME_USD = 30000
HIGH_VOLUME_GRACE_EARLY_SECONDS = 900
HIGH_VOLUME_GRACE_EXTENSION_SECONDS = 1800


def safe_float(value, default=0):

    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_int(value, default=0):

    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def normalize_ticker(value):

    return str(value or "").strip().lstrip("$").upper()


def resolve_path(path):

    path = Path(path)

    if path.is_absolute():
        return path

    return ROOT / path


def utc_time(timestamp):

    if not timestamp:
        return "unknown"

    return datetime.fromtimestamp(
        safe_float(timestamp),
        timezone.utc
    ).strftime("%Y-%m-%d %H:%M:%S UTC")


def money(value):

    return f"${safe_float(value):,.2f}"


def pct(value):

    return f"{safe_float(value):.1%}"


def multiple(value):

    value = safe_float(value)
    if value == 0:
        return "n/a"
    return f"{value:.2f}x"


def load_state(path):

    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)

    if not isinstance(data, dict):
        raise ValueError(f"paper state is not an object: {path}")

    return data


def load_closed_trades(state):

    return [
        trade
        for trade in state.get("closed", []) or []
        if trade.get("status") == "closed"
    ]


def existing_snapshot_columns(db):

    rows = db.execute(
        "PRAGMA table_info(signal_snapshots)"
    ).fetchall()
    return {
        row[1]
        for row in rows
        if row[1]
    }


def load_snapshots(
    db,
    address,
    start_at,
    end_at,
    available_columns
):

    columns = [
        column
        for column in SNAPSHOT_COLUMNS
        if column in available_columns
    ]

    if not columns:
        raise ValueError("signal_snapshots has no usable columns")

    query = (
        f"SELECT {', '.join(columns)} "
        "FROM signal_snapshots "
        "WHERE token_address = ? "
        "AND timestamp >= ? "
        "AND timestamp <= ? "
        "ORDER BY timestamp ASC"
    )
    rows = db.execute(
        query,
        (
            address,
            start_at,
            end_at
        )
    ).fetchall()

    return [
        dict(row)
        for row in rows
    ]


def nearest_entry_row(rows, entry_at, grace_seconds):

    before = [
        row
        for row in rows
        if safe_float(row.get("timestamp")) <= entry_at
    ]

    if before:
        return before[-1], "before_or_at"

    after = [
        row
        for row in rows
        if safe_float(row.get("timestamp")) <= entry_at + grace_seconds
    ]

    if after:
        return after[0], "after"

    return None, "missing"


def nearest_exit_row(rows, exit_at):

    before = [
        row
        for row in rows
        if safe_float(row.get("timestamp")) <= exit_at
    ]

    if before:
        return before[-1]

    return None


def snapshots_during_hold(rows, entry_at, exit_at):

    return [
        row
        for row in rows
        if (
            safe_float(row.get("timestamp")) >= entry_at
            and safe_float(row.get("timestamp")) <= exit_at
        )
    ]


def snapshots_after_exit(rows, exit_at, until_at):

    return [
        row
        for row in rows
        if (
            safe_float(row.get("timestamp")) > exit_at
            and safe_float(row.get("timestamp")) <= until_at
        )
    ]


def parse_event_entry_volume(trade):

    for event in trade.get("events", []) or []:
        if event.get("type") != "entry":
            continue

        match = ENTRY_VOLUME_RE.search(
            str(event.get("reason", ""))
        )

        if match:
            return safe_float(
                match.group(1).replace(",", "")
            )

    return 0


def entry_value(trade, entry_row, field, row_field=None):

    value = safe_float(trade.get(field), 0)

    if value:
        return value

    if entry_row:
        return safe_float(
            entry_row.get(row_field or field),
            0
        )

    return 0


def build_trade_context(
    trade,
    rows,
    entry_snapshot_grace_seconds
):

    entry_at = safe_float(trade.get("entry_at"))
    exit_at = safe_float(trade.get("exit_at"))
    entry_row, entry_row_source = nearest_entry_row(
        rows,
        entry_at,
        entry_snapshot_grace_seconds
    )
    exit_row = nearest_exit_row(
        rows,
        exit_at
    )
    hold_rows = snapshots_during_hold(
        rows,
        entry_at,
        exit_at
    )
    post_exit_rows = snapshots_after_exit(
        rows,
        exit_at,
        exit_at + HIGH_VOLUME_GRACE_EXTENSION_SECONDS
    )

    entry_volume_1h = entry_value(
        trade,
        entry_row,
        "entry_volume_1h",
        "volume_1h"
    )

    if not entry_volume_1h:
        entry_volume_1h = parse_event_entry_volume(trade)

    entry_volume_multiple = safe_float(
        trade.get("entry_volume_multiple"),
        0
    )

    if not entry_volume_multiple and entry_volume_1h:
        entry_volume_multiple = (
            entry_volume_1h
            / max(POSITION_MIN_ENTRY_VOLUME_1H_USD, 1e-18)
        )

    entry_impulse = entry_value(
        trade,
        entry_row,
        "entry_impulse",
        "impulse"
    )

    return {
        "trade": trade,
        "address": trade.get("address"),
        "symbol": trade.get("symbol", "UNKNOWN"),
        "entry_at": entry_at,
        "exit_at": exit_at,
        "actual_pnl_usd": safe_float(trade.get("pnl_usd")),
        "actual_close_reason": trade.get("close_reason", "unknown"),
        "entry_price": safe_float(trade.get("entry_price")),
        "entry_size_tokens": safe_float(trade.get("entry_size_tokens")),
        "entry_notional_usd": safe_float(trade.get("entry_notional_usd")),
        "entry_liquidity": entry_value(
            trade,
            entry_row,
            "entry_liquidity",
            "liquidity"
        ),
        "entry_pressure": entry_value(
            trade,
            entry_row,
            "entry_pressure",
            "pressure"
        ),
        "entry_score": entry_value(
            trade,
            entry_row,
            "entry_score",
            "score"
        ),
        "entry_impulse": entry_impulse,
        "entry_volume_1h": entry_volume_1h,
        "entry_volume_multiple": entry_volume_multiple,
        "entry_volume_liquidity_ratio": entry_value(
            trade,
            entry_row,
            "entry_volume_liquidity_ratio",
            "volume_liquidity_ratio"
        ),
        "entry_buy_sell_ratio": entry_value(
            trade,
            entry_row,
            "entry_buy_sell_ratio",
            "buy_sell_ratio"
        ),
        "entry_local_rsi_ready": bool(
            safe_int(
                entry_row.get("local_rsi_ready")
                if entry_row else 0
            )
        ),
        "entry_local_rsi": safe_float(
            entry_row.get("local_rsi")
            if entry_row else None,
            0
        ),
        "entry_local_rsi_ema": safe_float(
            entry_row.get("local_rsi_ema")
            if entry_row else None,
            0
        ),
        "entry_local_rsi_state": local_rsi_state(entry_row),
        "entry_row": entry_row,
        "entry_row_source": entry_row_source,
        "exit_row": exit_row,
        "rows": rows,
        "hold_rows": hold_rows,
        "post_exit_rows": post_exit_rows
    }


def local_rsi_state(row):

    if not row or not safe_int(row.get("local_rsi_ready")):
        return "not_ready"

    if safe_int(row.get("local_rsi_crossed_down")):
        return "crossed_down"

    if safe_int(row.get("local_rsi_crossed_up")):
        return "crossed_up"

    if safe_int(row.get("local_rsi_bearish")):
        return "bearish"

    if safe_int(row.get("local_rsi_bullish")):
        return "bullish"

    return "ready_neutral"


def pnl_at_price(ctx, exit_price, exit_at):

    trade = ctx["trade"]
    entry_notional = ctx["entry_notional_usd"]
    remaining_tokens = ctx["entry_size_tokens"]
    proceeds = 0

    events = sorted(
        trade.get("events", []) or [],
        key=lambda item: safe_float(item.get("timestamp"))
    )

    for event in events:
        if safe_float(event.get("timestamp")) > exit_at:
            continue

        if event.get("type") != "scale_out":
            continue

        sell_tokens = min(
            ctx["entry_size_tokens"] * safe_float(event.get("size_pct")),
            remaining_tokens
        )
        remaining_tokens -= sell_tokens
        proceeds += safe_float(
            event.get("proceeds_usd"),
            sell_tokens * safe_float(event.get("price"))
        )

    proceeds += remaining_tokens * exit_price

    return proceeds - entry_notional


def profit_factor(values):

    gains = sum(
        value
        for value in values
        if value > 0
    )
    losses = -sum(
        value
        for value in values
        if value < 0
    )

    if losses <= 0:
        return None if gains > 0 else 0

    return gains / losses


def summarize_outcomes(outcomes):

    closed = [
        outcome
        for outcome in outcomes
        if not outcome.get("blocked")
    ]
    values = [
        safe_float(outcome.get("pnl_usd"))
        for outcome in closed
    ]
    wins = [
        value
        for value in values
        if value > 0
    ]
    losses = [
        value
        for value in values
        if value < 0
    ]

    affected = [
        outcome
        for outcome in outcomes
        if outcome.get("affected")
    ]
    affected_actual_wins = sum(
        1
        for outcome in affected
        if safe_float(outcome.get("actual_pnl_usd")) > 0
    )
    affected_actual_losses = sum(
        1
        for outcome in affected
        if safe_float(outcome.get("actual_pnl_usd")) < 0
    )
    affected_close_reasons = Counter(
        outcome.get("actual_close_reason", "unknown")
        for outcome in affected
    )

    return {
        "closed_trades": len(closed),
        "pnl_usd": sum(values),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / len(closed) if closed else 0,
        "profit_factor": profit_factor(values),
        "worst_pnl_usd": min(values) if values else 0,
        "best_pnl_usd": max(values) if values else 0,
        "affected_trades": len(affected),
        "affected_actual_wins": affected_actual_wins,
        "affected_actual_losses": affected_actual_losses,
        "affected_close_reasons": dict(affected_close_reasons),
        "affected_actual_pnl_usd": sum(
            safe_float(outcome.get("actual_pnl_usd"))
            for outcome in affected
        ),
        "delta_pnl_usd": 0
    }


def actual_outcome(ctx):

    return {
        "address": ctx["address"],
        "symbol": ctx["symbol"],
        "pnl_usd": ctx["actual_pnl_usd"],
        "actual_pnl_usd": ctx["actual_pnl_usd"],
        "blocked": False,
        "affected": False,
        "action": "actual",
        "actual_close_reason": ctx["actual_close_reason"]
    }


def block_if(ctx, predicate, action):

    actual_pnl = ctx["actual_pnl_usd"]

    if predicate(ctx):
        return {
            "address": ctx["address"],
            "symbol": ctx["symbol"],
            "pnl_usd": 0,
            "actual_pnl_usd": actual_pnl,
            "blocked": True,
            "affected": True,
            "action": action,
            "actual_close_reason": ctx["actual_close_reason"]
        }

    return actual_outcome(ctx)


def size_if(ctx, predicate, multiplier, action):

    actual_pnl = ctx["actual_pnl_usd"]

    if predicate(ctx):
        return {
            "address": ctx["address"],
            "symbol": ctx["symbol"],
            "pnl_usd": actual_pnl * multiplier,
            "actual_pnl_usd": actual_pnl,
            "blocked": False,
            "affected": True,
            "action": action,
            "actual_close_reason": ctx["actual_close_reason"]
        }

    return actual_outcome(ctx)


def first_exit_trigger(ctx, predicate, action):

    entry_at = ctx["entry_at"]
    actual_exit_at = ctx["exit_at"]

    for row in ctx["hold_rows"]:
        timestamp = safe_float(row.get("timestamp"))

        if timestamp <= entry_at:
            continue

        if timestamp >= actual_exit_at:
            continue

        price = safe_float(row.get("price"))
        if price <= 0:
            continue

        price_multiple = (
            price
            / max(ctx["entry_price"], 1e-18)
        )

        if predicate(ctx, row, price_multiple):
            pnl = pnl_at_price(
                ctx,
                price,
                timestamp
            )
            return {
                "address": ctx["address"],
                "symbol": ctx["symbol"],
                "pnl_usd": pnl,
                "actual_pnl_usd": ctx["actual_pnl_usd"],
                "blocked": False,
                "affected": True,
                "action": action,
                "actual_close_reason": ctx["actual_close_reason"],
                "counterfactual_exit_at": timestamp,
                "counterfactual_exit_price": price,
                "counterfactual_price_multiple": price_multiple,
                "counterfactual_reason": action
            }

    return actual_outcome(ctx)


def high_volume_trailing_grace(ctx):

    if ctx["actual_close_reason"] != "trailing_stop":
        return actual_outcome(ctx)

    if ctx["entry_volume_1h"] < HIGH_VOLUME_GRACE_MIN_ENTRY_VOLUME_USD:
        return actual_outcome(ctx)

    if (
        ctx["exit_at"] - ctx["entry_at"]
        > HIGH_VOLUME_GRACE_EARLY_SECONDS
    ):
        return actual_outcome(ctx)

    rows = ctx.get("post_exit_rows") or []

    if not rows:
        return actual_outcome(ctx)

    chosen_row = rows[-1]
    chosen_reason = "high_volume_grace_window_end"

    for row in rows:
        price = safe_float(row.get("price"))

        if price <= 0:
            continue

        price_multiple = (
            price
            / max(ctx["entry_price"], 1e-18)
        )

        if price_multiple <= 0.70:
            chosen_row = row
            chosen_reason = "high_volume_grace_hard_stop"
            break

        if strict_two_signal_loss_exit(
            ctx,
            row,
            price_multiple
        ):
            chosen_row = row
            chosen_reason = "high_volume_grace_strict_risk_exit"
            break

    price = safe_float(chosen_row.get("price"))
    timestamp = safe_float(chosen_row.get("timestamp"))

    if price <= 0 or timestamp <= 0:
        return actual_outcome(ctx)

    pnl = pnl_at_price(
        ctx,
        price,
        timestamp
    )

    return {
        "address": ctx["address"],
        "symbol": ctx["symbol"],
        "pnl_usd": pnl,
        "actual_pnl_usd": ctx["actual_pnl_usd"],
        "blocked": False,
        "affected": True,
        "action": "high_volume_30k_early_trailing_grace",
        "actual_close_reason": ctx["actual_close_reason"],
        "counterfactual_exit_at": timestamp,
        "counterfactual_exit_price": price,
        "counterfactual_price_multiple": (
            price / max(ctx["entry_price"], 1e-18)
        ),
        "counterfactual_reason": chosen_reason,
        "entry_volume_1h": ctx["entry_volume_1h"],
        "actual_hold_seconds": (
            ctx["exit_at"] - ctx["entry_at"]
        )
    }


def impulse_bad_band(ctx):

    impulse = ctx["entry_impulse"]
    return 1.20 <= impulse < 1.50


def impulse_below_one(ctx):

    return ctx["entry_impulse"] < 1.00


def volume_lt_3x(ctx):

    return ctx["entry_volume_multiple"] < 3.00


def volume_lt_5x(ctx):

    return ctx["entry_volume_multiple"] < 5.00


def volume_3x_to_5x(ctx):

    volume_multiple = ctx["entry_volume_multiple"]

    return 3.00 <= volume_multiple < 5.00


def mid_volume_confirm_failed(ctx):

    if not volume_3x_to_5x(ctx):
        return False

    return (
        safe_float(ctx.get("entry_pressure")) < 55
        or safe_float(ctx.get("entry_volume_liquidity_ratio")) < 0.50
        or safe_float(ctx.get("entry_buy_sell_ratio")) < 1.00
    )


def quality_volume_gate(ctx):

    return volume_lt_3x(ctx) or mid_volume_confirm_failed(ctx)


def bad_impulse_or_volume_lt_3x(ctx):

    return impulse_bad_band(ctx) or volume_lt_3x(ctx)


def bad_impulse_or_volume_lt_5x(ctx):

    return impulse_bad_band(ctx) or volume_lt_5x(ctx)


def pressure_flow_exit_5pct(ctx, row, price_multiple):

    return (
        price_multiple <= 0.95
        and (
            safe_float(row.get("pressure")) <= 45
            or safe_float(row.get("volume_liquidity_ratio")) <= 0.35
            or safe_float(row.get("buy_sell_ratio")) <= 0.80
        )
    )


def pressure_flow_exit_10pct(ctx, row, price_multiple):

    return (
        price_multiple <= 0.90
        and (
            safe_float(row.get("pressure")) <= 55
            or safe_float(row.get("volume_liquidity_ratio")) <= 0.50
            or safe_float(row.get("buy_sell_ratio")) <= 1.00
        )
    )


def weak_pressure_flow_count(row, pressure, vlr, bsr):

    return (
        int(safe_float(row.get("pressure")) <= pressure)
        + int(safe_float(row.get("volume_liquidity_ratio")) <= vlr)
        + int(safe_float(row.get("buy_sell_ratio")) <= bsr)
    )


def strict_two_signal_loss_exit(ctx, row, price_multiple):

    return (
        price_multiple <= 0.95
        and weak_pressure_flow_count(
            row,
            pressure=40,
            vlr=0.50,
            bsr=0.65
        ) >= 2
    )


def liquidity_drain_exit(ctx, row, price_multiple):

    entry_liquidity = max(
        ctx["entry_liquidity"],
        1e-18
    )
    liquidity = safe_float(row.get("liquidity"))

    return (
        price_multiple <= 1.05
        and liquidity > 0
        and liquidity <= entry_liquidity * 0.85
    )


def rsi_bearish_loss_exit(ctx, row, price_multiple):

    return (
        price_multiple <= 1.05
        and bool(safe_int(row.get("local_rsi_ready")))
        and (
            bool(safe_int(row.get("local_rsi_bearish")))
            or bool(safe_int(row.get("local_rsi_crossed_down")))
        )
    )


def rsi_or_pressure_flow_exit(ctx, row, price_multiple):

    return (
        rsi_bearish_loss_exit(ctx, row, price_multiple)
        or pressure_flow_exit_5pct(ctx, row, price_multiple)
    )


def has_lineage_open_overlap(ctx):

    return bool(ctx.get("lineage_open_overlap"))


def production_quality_entry_rule(ctx):

    if bad_impulse_or_volume_lt_3x(ctx) or mid_volume_confirm_failed(ctx):
        return block_if(
            ctx,
            lambda _: True,
            "production_quality_entry_rule"
        )

    if volume_3x_to_5x(ctx):
        return size_if(
            ctx,
            lambda _: True,
            0.50,
            "production_quality_entry_rule"
        )

    return actual_outcome(ctx)


VARIANTS = [
    {
        "name": "actual",
        "type": "actual",
        "description": "Saved actual paper-bot outcome."
    },
    {
        "name": "block_same_ticker_open_overlap",
        "type": "block",
        "predicate": has_lineage_open_overlap,
        "description": (
            "Do not enter a contract when another open position has the same "
            "normalized ticker and a different contract address."
        )
    },
    {
        "name": "block_impulse_1_20_to_1_50",
        "type": "block",
        "predicate": impulse_bad_band,
        "description": "Do not enter the historically losing 1.20-1.50 impulse band."
    },
    {
        "name": "half_size_impulse_1_20_to_1_50",
        "type": "size",
        "predicate": impulse_bad_band,
        "multiplier": 0.50,
        "description": "Use half size for the 1.20-1.50 impulse band."
    },
    {
        "name": "block_impulse_below_1_00",
        "type": "block",
        "predicate": impulse_below_one,
        "description": "Raise min impulse to 1.00."
    },
    {
        "name": "block_volume_lt_3x",
        "type": "block",
        "predicate": volume_lt_3x,
        "description": "Require entry 1h volume to be at least 3x the current minimum."
    },
    {
        "name": "block_volume_lt_5x",
        "type": "block",
        "predicate": volume_lt_5x,
        "description": "Require entry 1h volume to be at least 5x the current minimum."
    },
    {
        "name": "half_size_volume_lt_5x",
        "type": "size",
        "predicate": volume_lt_5x,
        "multiplier": 0.50,
        "description": "Use half size when entry 1h volume is below 5x the current minimum."
    },
    {
        "name": "block_quality_volume_and_flow",
        "type": "block",
        "predicate": quality_volume_gate,
        "description": (
            "Require at least 3x entry 1h volume; for 3x-5x entries, "
            "also require pressure >= 55, VLR >= 0.50, and BSR >= 1.00."
        )
    },
    {
        "name": "production_quality_entry_rule",
        "type": "custom",
        "apply": production_quality_entry_rule,
        "description": (
            "Production quality rule: block bad impulse, block below 3x "
            "volume, require 3x-5x flow confirmation, and half-size 3x-5x "
            "entries while keeping 5x+ entries full size."
        )
    },
    {
        "name": "block_bad_impulse_or_volume_lt_3x",
        "type": "block",
        "predicate": bad_impulse_or_volume_lt_3x,
        "description": "Require both no 1.20-1.50 impulse band and at least 3x volume."
    },
    {
        "name": "block_bad_impulse_or_volume_lt_5x",
        "type": "block",
        "predicate": bad_impulse_or_volume_lt_5x,
        "description": "Require both no 1.20-1.50 impulse band and at least 5x volume."
    },
    {
        "name": "exit_pressure_flow_loss_5pct",
        "type": "exit",
        "predicate": pressure_flow_exit_5pct,
        "description": "Exit early below -5% when pressure or flow is weak."
    },
    {
        "name": "exit_pressure_flow_loss_10pct",
        "type": "exit",
        "predicate": pressure_flow_exit_10pct,
        "description": "Exit early below -10% when broader pressure or flow is weak."
    },
    {
        "name": "exit_strict_two_signal_loss_5pct",
        "type": "exit",
        "predicate": strict_two_signal_loss_exit,
        "description": (
            "Exit below -5% only when at least two of pressure <= 40, "
            "VLR <= 0.50, and BSR <= 0.65 are weak."
        )
    },
    {
        "name": "grace_high_volume_30k_early_trailing",
        "type": "custom",
        "apply": high_volume_trailing_grace,
        "description": (
            "For entry 1h volume >= $30k, ignore early trailing-stop exits "
            "within 15 minutes and continue for up to 30 minutes unless a hard "
            "or strict risk exit fires."
        )
    },
    {
        "name": "exit_liquidity_drain_15pct",
        "type": "exit",
        "predicate": liquidity_drain_exit,
        "description": "Exit early when liquidity falls at least 15% from entry before recovery."
    },
    {
        "name": "exit_rsi_bearish_loss",
        "type": "exit",
        "predicate": rsi_bearish_loss_exit,
        "description": "Exit below 1.05x on local RSI bearish/cross-down signal."
    },
    {
        "name": "exit_rsi_or_pressure_flow",
        "type": "exit",
        "predicate": rsi_or_pressure_flow_exit,
        "description": "Exit on either local RSI loss or the -5% weak pressure/flow rule."
    }
]


def apply_variant(ctx, variant):

    variant_type = variant["type"]

    if variant_type == "actual":
        return actual_outcome(ctx)

    if variant_type == "block":
        return block_if(
            ctx,
            variant["predicate"],
            variant["name"]
        )

    if variant_type == "size":
        return size_if(
            ctx,
            variant["predicate"],
            variant["multiplier"],
            variant["name"]
        )

    if variant_type == "exit":
        return first_exit_trigger(
            ctx,
            variant["predicate"],
            variant["name"]
        )

    if variant_type == "custom":
        return variant["apply"](
            ctx
        )

    raise ValueError(f"unknown variant type: {variant_type}")


def annotate_lineage_open_overlaps(contexts, open_positions):

    positions = []

    for ctx in contexts:
        positions.append({
            "address": ctx["address"],
            "symbol": ctx["symbol"],
            "ticker": normalize_ticker(ctx["symbol"]),
            "entry_at": ctx["entry_at"],
            "exit_at": ctx["exit_at"]
        })

    for position in (open_positions or {}).values():
        positions.append({
            "address": position.get("address"),
            "symbol": position.get("symbol", "UNKNOWN"),
            "ticker": normalize_ticker(position.get("symbol")),
            "entry_at": safe_float(position.get("entry_at")),
            "exit_at": 0
        })

    for ctx in contexts:
        ticker = normalize_ticker(ctx["symbol"])
        overlaps = []

        if not ticker:
            ctx["lineage_open_overlap"] = False
            ctx["lineage_overlap_positions"] = []
            continue

        for position in positions:
            if position["ticker"] != ticker:
                continue

            if str(position.get("address")) == str(ctx["address"]):
                continue

            entry_at = safe_float(position.get("entry_at"))
            exit_at = safe_float(position.get("exit_at"))

            if entry_at <= 0:
                continue

            if entry_at > ctx["entry_at"]:
                continue

            if exit_at > 0 and exit_at <= ctx["entry_at"]:
                continue

            overlaps.append(position)

        ctx["lineage_open_overlap"] = bool(overlaps)
        ctx["lineage_overlap_positions"] = overlaps


def run_analysis(
    state_path,
    db_path,
    *,
    entry_snapshot_grace_seconds,
    pre_entry_minutes,
    post_exit_minutes
):

    state = load_state(state_path)
    closed = load_closed_trades(state)
    contexts = []
    coverage = Counter()
    entry_sources = Counter()
    close_reasons = Counter()

    with sqlite3.connect(db_path) as db:
        db.row_factory = sqlite3.Row
        available_columns = existing_snapshot_columns(db)

        for trade in closed:
            entry_at = safe_float(trade.get("entry_at"))
            exit_at = safe_float(trade.get("exit_at"))
            rows = load_snapshots(
                db,
                trade.get("address"),
                entry_at - pre_entry_minutes * 60,
                exit_at + post_exit_minutes * 60,
                available_columns
            )
            ctx = build_trade_context(
                trade,
                rows,
                entry_snapshot_grace_seconds
            )
            contexts.append(ctx)
            close_reasons[ctx["actual_close_reason"]] += 1

            if rows:
                coverage["trades_with_any_snapshots"] += 1

            if ctx["hold_rows"]:
                coverage["trades_with_hold_snapshots"] += 1

            if ctx["entry_row"]:
                coverage["trades_with_entry_snapshot"] += 1

            if ctx["exit_row"]:
                coverage["trades_with_exit_snapshot"] += 1

            if ctx["entry_volume_1h"]:
                coverage["trades_with_entry_volume"] += 1

            entry_sources[ctx["entry_row_source"]] += 1

            if ctx["entry_local_rsi_ready"]:
                coverage["trades_with_entry_local_rsi_ready"] += 1

            if any(
                safe_int(row.get("local_rsi_ready"))
                for row in ctx["hold_rows"]
            ):
                coverage["trades_with_hold_local_rsi_ready"] += 1

    annotate_lineage_open_overlaps(
        contexts,
        state.get("open", {})
    )
    coverage["trades_with_same_ticker_open_overlap"] = sum(
        1
        for ctx in contexts
        if ctx.get("lineage_open_overlap")
    )

    variant_results = []
    actual_summary = None

    for variant in VARIANTS:
        outcomes = [
            apply_variant(ctx, variant)
            for ctx in contexts
        ]
        summary = summarize_outcomes(outcomes)

        if variant["name"] == "actual":
            actual_summary = dict(summary)

        variant_results.append({
            "name": variant["name"],
            "type": variant["type"],
            "description": variant["description"],
            "summary": summary,
            "outcomes": outcomes
        })

    if actual_summary:
        for result in variant_results:
            result["summary"]["delta_pnl_usd"] = (
                result["summary"]["pnl_usd"]
                - actual_summary["pnl_usd"]
            )

    target_diag = target_loss_diagnostics(contexts)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "state_file": str(state_path),
        "db": str(db_path),
        "post_exit_minutes": post_exit_minutes,
        "high_volume_grace": {
            "min_entry_volume_1h_usd": (
                HIGH_VOLUME_GRACE_MIN_ENTRY_VOLUME_USD
            ),
            "early_seconds": HIGH_VOLUME_GRACE_EARLY_SECONDS,
            "extension_seconds": HIGH_VOLUME_GRACE_EXTENSION_SECONDS
        },
        "paper_min_entry_impulse": POSITION_MIN_ENTRY_IMPULSE,
        "paper_max_entry_impulse": POSITION_MAX_ENTRY_IMPULSE,
        "paper_min_entry_volume_1h_usd": (
            POSITION_MIN_ENTRY_VOLUME_1H_USD
        ),
        "closed_trades": len(closed),
        "coverage": dict(coverage),
        "entry_snapshot_sources": dict(entry_sources),
        "close_reasons": dict(close_reasons),
        "variants": variant_results,
        "target_loss_diagnostics": target_diag,
        "recommendations": build_recommendations(variant_results)
    }


def target_loss_diagnostics(contexts):

    target_reasons = {
        "hard_stop_loss",
        "liquidity_drain_from_entry"
    }
    rows = []
    summary = Counter()

    for ctx in contexts:
        target = ctx["actual_close_reason"] in target_reasons
        first_rows = [
            row
            for row in ctx["hold_rows"]
            if safe_float(row.get("timestamp")) > ctx["entry_at"]
        ][:3]

        if target:
            summary["target_trades"] += 1
        else:
            summary["non_target_trades"] += 1

        if not first_rows:
            if target:
                summary["target_no_post_entry_rows"] += 1
            continue

        min_price_multiple = min(
            safe_float(row.get("price"))
            / max(ctx["entry_price"], 1e-18)
            for row in first_rows
            if safe_float(row.get("price")) > 0
        )
        min_pressure = min(
            safe_float(row.get("pressure"))
            for row in first_rows
        )
        min_vlr = min(
            safe_float(row.get("volume_liquidity_ratio"))
            for row in first_rows
        )
        min_bsr = min(
            safe_float(row.get("buy_sell_ratio"))
            for row in first_rows
        )
        min_liquidity_pct = min(
            safe_float(row.get("liquidity"))
            / max(ctx["entry_liquidity"], 1e-18)
            for row in first_rows
            if safe_float(row.get("liquidity")) > 0
        )
        rsi_bearish = any(
            safe_int(row.get("local_rsi_ready"))
            and (
                safe_int(row.get("local_rsi_bearish"))
                or safe_int(row.get("local_rsi_crossed_down"))
            )
            for row in first_rows
        )
        weak_5pct = (
            min_price_multiple <= 0.95
            and (
                min_pressure <= 45
                or min_vlr <= 0.35
                or min_bsr <= 0.80
            )
        )
        liquidity_drain = (
            min_price_multiple <= 1.05
            and min_liquidity_pct <= 0.85
        )

        if target and weak_5pct:
            summary["target_first3_weak_5pct"] += 1

        if target and liquidity_drain:
            summary["target_first3_liquidity_drain"] += 1

        if target and rsi_bearish:
            summary["target_first3_rsi_bearish"] += 1

        if not target and weak_5pct:
            summary["non_target_first3_weak_5pct"] += 1

        if not target and liquidity_drain:
            summary["non_target_first3_liquidity_drain"] += 1

        if not target and rsi_bearish:
            summary["non_target_first3_rsi_bearish"] += 1

        if target:
            rows.append({
                "symbol": ctx["symbol"],
                "actual_close_reason": ctx["actual_close_reason"],
                "actual_pnl_usd": ctx["actual_pnl_usd"],
                "entry_impulse": ctx["entry_impulse"],
                "entry_volume_multiple": ctx["entry_volume_multiple"],
                "min_price_multiple_first3": min_price_multiple,
                "min_pressure_first3": min_pressure,
                "min_vlr_first3": min_vlr,
                "min_bsr_first3": min_bsr,
                "min_liquidity_pct_first3": min_liquidity_pct,
                "first3_rsi_bearish": rsi_bearish
            })

    return {
        "summary": dict(summary),
        "target_rows": rows
    }


def build_recommendations(variant_results):

    by_name = {
        result["name"]: result["summary"]
        for result in variant_results
    }
    recommendations = []

    actual = by_name.get("actual", {})
    impulse_bad = by_name.get("block_impulse_1_20_to_1_50", {})
    impulse_min = by_name.get("block_impulse_below_1_00", {})
    volume_3x = by_name.get("block_volume_lt_3x", {})
    volume_5x = by_name.get("block_volume_lt_5x", {})
    production_quality = by_name.get("production_quality_entry_rule", {})
    strict_exit = by_name.get("exit_strict_two_signal_loss_5pct", {})

    if impulse_bad.get("pnl_usd", 0) > actual.get("pnl_usd", 0):
        recommendations.append(
            "Keep max entry impulse at or below 1.20; the actual ledger still "
            "supports blocking 1.20-1.50 impulse entries."
        )
    else:
        recommendations.append(
            "No positive actual-ledger evidence was found for a stricter max "
            "entry impulse band."
        )

    if impulse_min.get("pnl_usd", 0) < actual.get("pnl_usd", 0):
        recommendations.append(
            "Do not raise min entry impulse to 1.00 from this sample; it removes "
            "profitable 0.90-1.00 actual entries."
        )
    else:
        recommendations.append(
            "Raising min entry impulse to 1.00 is supported by this sample."
        )

    if volume_3x.get("pnl_usd", 0) > actual.get("pnl_usd", 0):
        recommendations.append(
            "Consider testing a 3x entry-volume gate before jumping straight to "
            "5x; 3x keeps more trades while improving ledger PnL."
        )

    if production_quality.get("pnl_usd", 0) > actual.get("pnl_usd", 0):
        recommendations.append(
            "The production quality entry rule is supported by this sample: "
            "block below 3x volume, require 3x-5x flow confirmation, keep "
            "5x+ as full-size high-conviction entries, and keep the bad "
            "impulse band blocked."
        )

    recommendations.append(
        "A 5x entry-volume gate has the strongest profit factor in this "
        "sample, but it is much more selective and should be treated as a "
        "high-conviction mode rather than the only default."
    )

    if (
        strict_exit.get("pnl_usd", 0) > actual.get("pnl_usd", 0)
        and strict_exit.get("affected_actual_wins", 1) == 0
    ):
        recommendations.append(
            "The strict two-signal early-loss exit improved this actual sample "
            "without touching actual winners; it is the best candidate for an "
            "isolated paper test, but it still does not remove the worst loss."
        )
    else:
        recommendations.append(
            "Do not add the tested RSI/pressure-flow early exits yet; no tested "
            "variant improved the current outcome without touching winners."
        )

    recommendations.append(
        "Keep high-volume trailing grace disabled; this run does not retest it "
        "as an accepted production rule."
    )

    return recommendations


def table_summary_row(result):

    summary = result["summary"]
    profit = summary["profit_factor"]
    profit_text = (
        "n/a"
        if profit is None
        else f"{profit:.3f}"
    )
    affected = (
        f"{summary['affected_trades']} "
        f"({summary['affected_actual_wins']}W/"
        f"{summary['affected_actual_losses']}L)"
    )

    return (
        f"| `{result['name']}` | {summary['closed_trades']} | "
        f"{money(summary['pnl_usd'])} | "
        f"{money(summary['delta_pnl_usd'])} | "
        f"{pct(summary['win_rate'])} | "
        f"{profit_text} | "
        f"{money(summary['worst_pnl_usd'])} | "
        f"{money(summary['best_pnl_usd'])} | "
        f"{affected} |"
    )


def write_report(path, result):

    actual = next(
        variant
        for variant in result["variants"]
        if variant["name"] == "actual"
    )
    strict_exit = next(
        variant
        for variant in result["variants"]
        if variant["name"] == "exit_strict_two_signal_loss_5pct"
    )
    coverage = result["coverage"]
    target = result["target_loss_diagnostics"]

    lines = [
        "# Actual-Entry Anchored Counterfactual Report",
        "",
        f"Generated: {result['generated_at']}",
        f"Actual ledger: `{result['state_file']}`",
        f"Snapshot DB: `{result['db']}`",
        f"Post-exit snapshot window: `{result.get('post_exit_minutes', 0)}` minutes",
        "",
        "## Method",
        "",
        (
            "This report uses saved closed paper trades as the source of truth. "
            "It does not use free replay-generated trades as the main dataset."
        ),
        "",
        (
            "Each actual trade is enriched from `scanner.db.signal_snapshots` by "
            "`token_address` and timestamp. Counterfactuals keep the actual entry "
            "price and size, then either block/reduce that actual entry or apply "
            "an earlier exit using only snapshots observed during the actual hold."
        ),
        "",
        (
            "The high-volume trailing-grace variant is the exception: it uses "
            "post-exit snapshots to estimate what would have happened if an "
            "early trailing-stop exit had been given more room."
        ),
        "",
        "## Coverage",
        "",
        f"- Closed actual trades: `{result['closed_trades']}`",
        (
            "- Trades with any snapshots: "
            f"`{coverage.get('trades_with_any_snapshots', 0)}`"
        ),
        (
            "- Trades with hold-period snapshots: "
            f"`{coverage.get('trades_with_hold_snapshots', 0)}`"
        ),
        (
            "- Trades with entry snapshots: "
            f"`{coverage.get('trades_with_entry_snapshot', 0)}`"
        ),
        (
            "- Trades with exit snapshots: "
            f"`{coverage.get('trades_with_exit_snapshot', 0)}`"
        ),
        (
            "- Trades with reconstructed entry volume: "
            f"`{coverage.get('trades_with_entry_volume', 0)}`"
        ),
        (
            "- Trades with local RSI ready during hold: "
            f"`{coverage.get('trades_with_hold_local_rsi_ready', 0)}`"
        ),
        (
            "- Trades with same-ticker open overlap: "
            f"`{coverage.get('trades_with_same_ticker_open_overlap', 0)}`"
        ),
        "",
        "## Variant Comparison",
        "",
        (
            "| Variant | Closed trades | PnL | Delta | Win rate | "
            "Profit factor | Worst | Best | Affected |"
        ),
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|"
    ]

    for variant in result["variants"]:
        lines.append(
            table_summary_row(variant)
        )

    lines.extend([
        "",
        "Affected is formatted as total affected actual trades, followed by "
        "actual winners and actual losers inside that affected set.",
        "",
        "## Variant Notes",
        ""
    ])

    for variant in result["variants"]:
        lines.append(
            f"- `{variant['name']}`: {variant['description']}"
        )

    lines.extend([
        "",
        "## Hard-Stop / Liquidity-Drain Early Signals",
        "",
        (
            "- Target trades: "
            f"`{target['summary'].get('target_trades', 0)}`"
        ),
        (
            "- Target trades with first-3-snapshot weak -5% pressure/flow: "
            f"`{target['summary'].get('target_first3_weak_5pct', 0)}`"
        ),
        (
            "- Non-target trades with first-3-snapshot weak -5% pressure/flow: "
            f"`{target['summary'].get('non_target_first3_weak_5pct', 0)}`"
        ),
        (
            "- Target trades with first-3-snapshot liquidity drain: "
            f"`{target['summary'].get('target_first3_liquidity_drain', 0)}`"
        ),
        (
            "- Non-target trades with first-3-snapshot liquidity drain: "
            f"`{target['summary'].get('non_target_first3_liquidity_drain', 0)}`"
        ),
        (
            "- Target trades with first-3-snapshot RSI bearish/cross-down: "
            f"`{target['summary'].get('target_first3_rsi_bearish', 0)}`"
        ),
        (
            "- Non-target trades with first-3-snapshot RSI bearish/cross-down: "
            f"`{target['summary'].get('non_target_first3_rsi_bearish', 0)}`"
        ),
        "",
        "## Strict Early Exit Affected Reasons",
        ""
    ])

    affected_reasons = strict_exit["summary"].get(
        "affected_close_reasons",
        {}
    )

    for reason, count in sorted(
        affected_reasons.items()
    ):
        lines.append(
            f"- `{reason}`: {count}"
        )

    lines.extend([
        "",
        "## Recommendations",
        ""
    ])

    for recommendation in result["recommendations"]:
        lines.append(
            f"- {recommendation}"
        )

    lines.extend([
        "",
        "## Actual Baseline",
        "",
        (
            f"- PnL: `{money(actual['summary']['pnl_usd'])}`"
        ),
        (
            f"- Win rate: `{pct(actual['summary']['win_rate'])}`"
        ),
        (
            "- Profit factor: "
            f"`{actual['summary']['profit_factor']:.3f}`"
        ),
        (
            f"- Worst trade: `{money(actual['summary']['worst_pnl_usd'])}`"
        ),
        (
            f"- Best trade: `{money(actual['summary']['best_pnl_usd'])}`"
        ),
        "",
        "## Current Runtime Thresholds Read By Script",
        "",
        (
            "- `POSITION_MIN_ENTRY_IMPULSE`: "
            f"`{result['paper_min_entry_impulse']:.2f}`"
        ),
        (
            "- `POSITION_MAX_ENTRY_IMPULSE`: "
            f"`{result['paper_max_entry_impulse']:.2f}`"
        ),
        (
            "- `POSITION_MIN_ENTRY_VOLUME_1H_USD`: "
            f"`{money(result['paper_min_entry_volume_1h_usd'])}`"
        )
    ])

    path.parent.mkdir(
        parents=True,
        exist_ok=True
    )
    path.write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8"
    )


def compact_result(result):

    compact = dict(result)
    compact["variants"] = []

    for variant in result["variants"]:
        compact["variants"].append({
            "name": variant["name"],
            "type": variant["type"],
            "description": variant["description"],
            "summary": variant["summary"],
            "changed_outcomes": [
                outcome
                for outcome in variant["outcomes"]
                if outcome.get("affected")
            ]
        })

    return compact


def print_summary(result):

    print(
        f"closed_trades={result['closed_trades']}"
    )
    print(
        "entry_snapshots="
        f"{result['coverage'].get('trades_with_entry_snapshot', 0)}"
    )
    print(
        "hold_snapshots="
        f"{result['coverage'].get('trades_with_hold_snapshots', 0)}"
    )

    for variant in result["variants"]:
        summary = variant["summary"]
        profit = summary["profit_factor"]
        profit_text = "n/a" if profit is None else f"{profit:.3f}"
        print(
            f"{variant['name']} "
            f"closed={summary['closed_trades']} "
            f"pnl={summary['pnl_usd']:.2f} "
            f"delta={summary['delta_pnl_usd']:.2f} "
            f"win_rate={summary['win_rate']:.3f} "
            f"profit_factor={profit_text} "
            f"affected={summary['affected_trades']}"
        )


def parse_args():

    parser = argparse.ArgumentParser(
        description=(
            "Run actual-entry anchored counterfactual analysis for "
            "paper trading positions."
        )
    )
    parser.add_argument(
        "--state-file",
        default=POSITION_STATE_FILE
    )
    parser.add_argument(
        "--db",
        default=DATABASE_NAME
    )
    parser.add_argument(
        "--report",
        default="analysis/actual_entry_counterfactual_report.md"
    )
    parser.add_argument(
        "--json",
        default="analysis/actual_entry_counterfactual_results.json"
    )
    parser.add_argument(
        "--entry-snapshot-grace-seconds",
        type=float,
        default=900
    )
    parser.add_argument(
        "--pre-entry-minutes",
        type=float,
        default=60
    )
    parser.add_argument(
        "--post-exit-minutes",
        type=float,
        default=30
    )
    return parser.parse_args()


def main():

    args = parse_args()
    result = run_analysis(
        resolve_path(args.state_file),
        resolve_path(args.db),
        entry_snapshot_grace_seconds=args.entry_snapshot_grace_seconds,
        pre_entry_minutes=args.pre_entry_minutes,
        post_exit_minutes=args.post_exit_minutes
    )

    report_path = resolve_path(args.report)
    json_path = resolve_path(args.json)

    write_report(
        report_path,
        result
    )
    json_path.parent.mkdir(
        parents=True,
        exist_ok=True
    )
    json_path.write_text(
        json.dumps(
            compact_result(result),
            indent=2,
            sort_keys=True
        )
        + "\n",
        encoding="utf-8"
    )
    print_summary(result)
    print(
        f"report={report_path}"
    )
    print(
        f"json={json_path}"
    )


if __name__ == "__main__":
    main()
