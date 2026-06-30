"""Discovery-layer position management.

The old engine is preserved for A/B replay and quick rollback. The new engine
keeps the same fill shape as discovery.paper_trade.manage:
    (kind, tokens_sold, price)
"""
import config

from trading.adaptive_stop import adaptive_initial_stop_pct, _recent_candles_for_atr
from trading.ohlcv_indicators import (
    downside_atr,
    entry_swing_low,
    fibonacci_extension_levels,
    full_atr,
)
from trading.volume_profile import high_volume_nodes, nearest_support, volume_profile


def _f(row, key, default=0.0):
    try:
        if row is None:
            return default
        value = row.get(key)
        return float(value) if value is not None else default
    except (TypeError, ValueError, AttributeError):
        return default


def _b(name, default=False):
    return bool(getattr(config, name, default))


def _min_low(candles):
    lows = []
    for c in candles or []:
        low = _f(c, "low") or _f(c, "l")
        if low > 0:
            lows.append(low)
    return min(lows) if lows else None


def _max_hold_close_due(
    pos,
    ts,
    max_hold_s,
    mult,
    peak_mult,
    exempt_mult,
    partial_runner_mult,
    partial_runner_hold_s,
    partial_runner_require_profit,
):
    if not max_hold_s or pos.get("remaining", 0) <= 0 or pos.get("closed"):
        return False

    age_s = ts - _f(pos, "entry_ts", ts)
    if age_s < max_hold_s:
        return False

    if peak_mult >= exempt_mult:
        return False

    if (
        partial_runner_mult > 0
        and peak_mult >= partial_runner_mult
        and partial_runner_hold_s > max_hold_s
    ):
        still_profitable = mult > 1.0 if partial_runner_require_profit else True
        if still_profitable and age_s < partial_runner_hold_s:
            return False

    return True


def _snap_up_to_node(target, nodes, band):
    for node in sorted(nodes or []):
        if target <= node <= target * (1.0 + band):
            return node
    return target


def old_manage(pos, price, ts, max_hold_s=None, features=None):
    fills = []
    ep = pos["entry_price"]
    pos["peak"] = max(pos["peak"], price)
    mult = price / ep if ep > 0 else 0.0
    peak_mult = pos["peak"] / ep if ep > 0 else 0.0
    trail_pct = float(
        getattr(config, "POSITION_RUNNER_RELAXED_TRAIL_PCT", 0.25) or 0.25
    )
    high_trigger = float(
        getattr(config, "POSITION_HIGH_MULT_TRAIL_TRIGGER", 4.0) or 4.0
    )
    high_trail = float(
        getattr(config, "POSITION_HIGH_MULT_TRAIL_PCT", 0.50) or 0.50
    )
    max_hold_exempt = float(
        getattr(config, "LATTICE_MAX_HOLD_EXEMPT_MULTIPLE", 2.0) or 2.0
    )
    partial_runner_mult = float(
        getattr(config, "LATTICE_MAX_HOLD_PARTIAL_RUNNER_MULTIPLE", 1.5)
        or 0.0
    )
    partial_runner_hold_s = (
        float(getattr(config, "LATTICE_MAX_HOLD_PARTIAL_RUNNER_H", 24.0) or 0.0)
        * 3600
    )
    partial_runner_require_profit = _b(
        "LATTICE_MAX_HOLD_PARTIAL_RUNNER_REQUIRE_PROFIT",
        True
    )
    default_stop = float(
        getattr(
            config,
            "LATTICE_EXIT_INITIAL_STOP_PCT",
            getattr(config, "POSITION_INITIAL_STOP_LOSS_PCT", 0.30),
        )
        or 0.30
    )
    ladder = sorted([
        (float(m), float(t))
        for m, t in getattr(
            config,
            "POSITION_SCALE_OUT_LADDER",
            ((2.0, 0.50), (4.0, 0.60)),
        )
    ])

    if not pos["scaled"] and price <= ep * (1 - default_stop):
        q = pos["remaining"]
        pos["remaining"] = 0.0
        pos["proceeds"] += q * price
        fills.append(("initial_stop", q, price))
        pos["closed"] = True
        pos["reason"] = "initial_stop"
        return fills

    initial_tokens = pos["cost_usd"] / ep if ep > 0 else 0.0
    for lvl, target in ladder:
        if mult >= lvl and lvl not in pos["levels_done"] and pos["remaining"] > 0:
            current_sold = (
                1.0 - pos["remaining"] / initial_tokens
                if initial_tokens > 0
                else 0.0
            )
            q = initial_tokens * max(target - current_sold, 0.0)
            q = min(q, pos["remaining"])
            if q <= 0:
                pos["levels_done"].add(lvl)
                continue
            pos["remaining"] -= q
            pos["proceeds"] += q * price
            pos["levels_done"].add(lvl)
            pos["scaled"] = True
            fills.append((f"scale_{lvl:g}x", q, price))

    active_trail = high_trail if peak_mult >= high_trigger else trail_pct
    if pos["scaled"] and pos["remaining"] > 0 and price <= pos["peak"] * (1 - active_trail):
        q = pos["remaining"]
        pos["remaining"] = 0.0
        pos["proceeds"] += q * price
        fills.append(("trailing_stop", q, price))
        pos["closed"] = True
        pos["reason"] = "trailing_stop"

    if (
        _max_hold_close_due(
            pos,
            ts,
            max_hold_s,
            mult,
            peak_mult,
            max_hold_exempt,
            partial_runner_mult,
            partial_runner_hold_s,
            partial_runner_require_profit,
        )
    ):
        q = pos["remaining"]
        pos["remaining"] = 0.0
        pos["proceeds"] += q * price
        fills.append(("max_hold", q, price))
        pos["closed"] = True
        pos["reason"] = "max_hold"

    if pos["remaining"] <= 1e-12 and not pos.get("closed"):
        pos["closed"] = True
        pos["reason"] = pos.get("reason") or "scaled_out"
    return fills


class PositionManager:
    def __init__(self):
        self.initial_stop_pct = float(
            getattr(
                config,
                "LATTICE_EXIT_INITIAL_STOP_PCT",
                getattr(config, "POSITION_INITIAL_STOP_LOSS_PCT", 0.30),
            )
            or 0.30
        )
        self.strict_enabled = _b("LATTICE_STRICT_EARLY_EXIT_ENABLED", True)
        self.strict_loss_pct = float(
            getattr(config, "LATTICE_STRICT_EARLY_EXIT_LOSS_PCT", 0.12) or 0.12
        )
        self.strict_min_weak = int(
            getattr(config, "LATTICE_STRICT_EARLY_EXIT_MIN_WEAK_SIGNALS", 2)
            or 2
        )
        self.strict_confirm_ticks = int(
            getattr(config, "LATTICE_STRICT_EARLY_EXIT_CONFIRM_TICKS", 2) or 2
        )
        self.strict_max_pressure = float(
            getattr(config, "LATTICE_STRICT_EARLY_EXIT_MAX_PRESSURE", 40) or 40
        )
        self.strict_max_vlr = float(
            getattr(
                config,
                "LATTICE_STRICT_EARLY_EXIT_MAX_VOLUME_LIQUIDITY_RATIO",
                0.50,
            )
            or 0.50
        )
        self.strict_max_bsr = float(
            getattr(config, "LATTICE_STRICT_EARLY_EXIT_MAX_BUY_SELL_RATIO", 0.65)
            or 0.65
        )
        self.liquidity_enabled = _b("LATTICE_LIQUIDITY_COLLAPSE_EXIT_ENABLED", True)
        self.liquidity_from_entry_pct = float(
            getattr(config, "LATTICE_LIQUIDITY_COLLAPSE_FROM_ENTRY_PCT", 0.45)
            or 0.45
        )
        self.liquidity_from_peak_pct = float(
            getattr(config, "LATTICE_LIQUIDITY_COLLAPSE_FROM_PEAK_PCT", 0.50)
            or 0.50
        )
        self.liquidity_min_reference_usd = float(
            getattr(config, "LATTICE_LIQUIDITY_COLLAPSE_MIN_REFERENCE_USD", 1000)
            or 1000
        )
        self.sell_only_enabled = _b("LATTICE_SELL_ONLY_FLOW_EXIT_ENABLED", True)
        self.sell_only_max_buy_volume = float(
            getattr(config, "LATTICE_SELL_ONLY_FLOW_MAX_BUY_VOLUME_5M_USD", 25)
            or 25
        )
        self.sell_only_max_buy_sell_ratio = float(
            getattr(config, "LATTICE_SELL_ONLY_FLOW_MAX_BUY_SELL_VOLUME_RATIO", 0.05)
            or 0.05
        )
        self.sell_only_min_sell_volume = float(
            getattr(config, "LATTICE_SELL_ONLY_FLOW_MIN_SELL_VOLUME_5M_USD", 5000)
            or 5000
        )
        self.sell_only_min_entry_multiple = float(
            getattr(
                config,
                "LATTICE_SELL_ONLY_FLOW_MIN_SELL_ENTRY_NOTIONAL_MULTIPLE",
                5,
            )
            or 5
        )
        self.sell_only_max_multiple = float(
            getattr(config, "LATTICE_SELL_ONLY_FLOW_MAX_PRICE_MULTIPLE", 1.20)
            or 1.20
        )
        self.no_progress_enabled = _b("LATTICE_NO_PROGRESS_EXIT_ENABLED", True)
        self.no_progress_min_s = float(
            getattr(config, "LATTICE_NO_PROGRESS_EXIT_MIN_SECONDS", 45 * 60)
            or 45 * 60
        )
        self.no_progress_peak_mult = float(
            getattr(config, "LATTICE_NO_PROGRESS_EXIT_MAX_PEAK_MULTIPLE", 1.20)
            or 1.20
        )
        self.no_progress_max_pressure = float(
            getattr(config, "LATTICE_NO_PROGRESS_EXIT_MAX_PRESSURE", 35) or 35
        )
        self.no_progress_max_bsr = float(
            getattr(config, "LATTICE_NO_PROGRESS_EXIT_MAX_BUY_SELL_RATIO", 0.90)
            or 0.90
        )
        self.break_even_enabled = _b("LATTICE_BREAK_EVEN_EXIT_ENABLED", True)
        self.arm_mult = float(
            getattr(config, "LATTICE_BREAK_EVEN_ARM_MULTIPLE", 1.30) or 1.30
        )
        self.break_even_floor_mult = float(
            getattr(config, "LATTICE_BREAK_EVEN_FLOOR_MULTIPLE", 1.02) or 1.02
        )
        self.ladder = sorted([
            (float(m), float(t))
            for m, t in getattr(
                config,
                "LATTICE_EXIT_SCALE_OUT_LADDER",
                ((3.00, 0.50), (6.00, 0.95)),
            )
        ])
        self.tp_mode = str(
            getattr(config, "LATTICE_EXIT_TP_MODE", "tail") or "tail"
        ).lower()
        self.tail_modes = {"tail", "staged", "staged_tail"}
        self.tail_cost_recovery_multiple = max(
            float(
                getattr(config, "LATTICE_TAIL_COST_RECOVERY_MULTIPLE", 2.0)
                or 2.0
            ),
            1.0,
        )
        self.tail_cost_recovery_pct = min(
            max(
                float(getattr(config, "LATTICE_TAIL_COST_RECOVERY_PCT", 1.0) or 0),
                0.0,
            ),
            1.0,
        )
        self.tail_cost_recovery_max_sell_pct = min(
            max(
                float(
                    getattr(
                        config,
                        "LATTICE_TAIL_COST_RECOVERY_MAX_SELL_PCT",
                        0.55,
                    )
                    or 0
                ),
                0.0,
            ),
            1.0,
        )
        self.tail_scale_out_tiers = sorted([
            (float(m), float(f))
            for m, f in getattr(
                config,
                "LATTICE_TAIL_SCALE_OUT_TIERS",
                ((5.0, 0.10), (10.0, 0.10)),
            )
            if float(m) > 1.0 and 0.0 < float(f) <= 1.0
        ])
        self.q3_fib_extensions = tuple(
            float(x)
            for x in getattr(
                config,
                "LATTICE_Q3_FIB_EXTENSIONS",
                (2.618, 4.236),
            )
            if float(x) > 1.0
        )
        self.q3_node_snap_band = float(
            getattr(config, "LATTICE_Q3_TP_NODE_SNAP_BAND", 0.15) or 0.15
        )
        self.q3_min_target_mult = float(
            getattr(config, "LATTICE_Q3_MIN_TARGET_MULTIPLE", 2.0) or 2.0
        )
        self.q3_atr_trail_enabled = _b("LATTICE_Q3_ATR_TRAIL_ENABLED", False)
        self.q3_atr_trail_k = float(
            getattr(
                config,
                "LATTICE_Q3_ATR_TRAIL_K",
                getattr(config, "POSITION_ATR_STOP_K", 5.0),
            )
            or 5.0
        )
        self.q3_vp_floor_buffer = float(
            getattr(config, "LATTICE_Q3_VP_FLOOR_BUFFER_PCT", 1.0) or 1.0
        )
        self.scale_stop_floors = sorted([
            (float(m), float(t))
            for m, t in getattr(
                config,
                "LATTICE_EXIT_SCALE_STOP_FLOORS",
                ((3.00, 1.50), (6.00, 3.00)),
            )
        ])
        self.moonbag_step_floors_enabled = _b(
            "LATTICE_MOONBAG_STEP_FLOORS_ENABLED",
            True,
        )
        self.moonbag_step_trigger_mult = float(
            getattr(config, "LATTICE_MOONBAG_STEP_TRIGGER_MULT", 20.0)
            or 20.0
        )
        self.moonbag_step_interval_mult = float(
            getattr(config, "LATTICE_MOONBAG_STEP_INTERVAL_MULT", 10.0)
            or 10.0
        )
        self.moonbag_step_floor_lag_mult = float(
            getattr(config, "LATTICE_MOONBAG_STEP_FLOOR_LAG_MULT", 10.0)
            or 10.0
        )
        self.trail_pct = float(
            getattr(config, "LATTICE_POST_SCALE_TRAIL_PCT", 0.0) or 0.0
        )
        self.trailing_enabled = self.trail_pct > 0
        self.high_mult_trigger = float(
            getattr(config, "LATTICE_HIGH_MULT_TRAIL_TRIGGER", 4.0) or 4.0
        )
        self.high_mult_trail_pct = float(
            getattr(config, "LATTICE_HIGH_MULT_TRAIL_PCT", 0.0) or 0.0
        )
        self.max_hold_exempt_mult = float(
            getattr(config, "LATTICE_MAX_HOLD_EXEMPT_MULTIPLE", 2.0) or 2.0
        )
        self.max_hold_partial_runner_mult = float(
            getattr(config, "LATTICE_MAX_HOLD_PARTIAL_RUNNER_MULTIPLE", 1.5)
            or 0.0
        )
        self.max_hold_partial_runner_s = (
            float(
                getattr(config, "LATTICE_MAX_HOLD_PARTIAL_RUNNER_H", 24.0)
                or 0.0
            )
            * 3600
        )
        self.max_hold_partial_runner_require_profit = _b(
            "LATTICE_MAX_HOLD_PARTIAL_RUNNER_REQUIRE_PROFIT",
            True
        )

    def append_features(self, pos, features):
        if not features:
            return False
        feature_ts = _f(features, "timestamp", 0)
        if feature_ts <= 0:
            return False
        if feature_ts <= _f(pos, "last_feature_ts", 0):
            return False
        pos["last_feature_ts"] = feature_ts
        recent = pos.setdefault("recent", [])
        recent.append({
            "timestamp": feature_ts,
            "pressure": _f(features, "pressure", 0),
            "volume_liquidity_ratio": _f(features, "volume_liquidity_ratio", 0),
            "buy_sell_ratio": _f(features, "buy_sell_ratio", 0),
            "liquidity": self.current_liquidity(features),
            "price": _f(features, "price", 0),
        })
        pos["recent"] = recent[-12:]
        return True

    def current_liquidity(self, features):
        return _f(features, "liquidity", 0) or _f(features, "raw_liquidity", 0)

    def close_all(self, pos, reason, price, fills):
        q = pos["remaining"]
        pos["remaining"] = 0.0
        pos["proceeds"] += q * price
        fills.append((reason, q, price))
        pos["closed"] = True
        pos["reason"] = reason
        return fills

    def scale_stop_floor_multiple(self, level):
        floor_mult = 0.0
        for trigger, floor in self.scale_stop_floors:
            if level + 1e-9 >= trigger:
                floor_mult = max(floor_mult, floor)
        return floor_mult

    def arm_scale_stop_floor(self, pos, level, entry_price):
        floor_mult = self.scale_stop_floor_multiple(level)
        if floor_mult <= 0:
            return

        floor_price = entry_price * floor_mult
        if floor_price <= _f(pos, "stop_floor_price", 0):
            return

        pos["stop_floor_price"] = floor_price
        pos["stop_floor_multiple"] = floor_mult
        pos["stop_floor_source"] = f"scale_{level:g}x"

    def completed_scale_level(self, level_key):
        if isinstance(level_key, (int, float)):
            return float(level_key)

        raw = str(level_key or "").strip().lower()
        if not raw:
            return 0.0
        try:
            return float(raw)
        except ValueError:
            pass

        for prefix in (
            "tail_cost_recovery_",
            "tail_scale_",
            "scale_cost_recovery_",
            "scale_tail_",
            "scale_",
        ):
            if not raw.startswith(prefix):
                continue
            value = raw[len(prefix):]
            if value.endswith("x"):
                value = value[:-1]
            try:
                return float(value)
            except ValueError:
                return 0.0
        return 0.0

    def arm_completed_scale_floor(self, pos, entry_price):
        level = 0.0
        for level_key in pos.get("levels_done") or ():
            level = max(level, self.completed_scale_level(level_key))
        if level > 0:
            self.arm_scale_stop_floor(pos, level, entry_price)

    def moonbag_step_floor_multiple(self, peak_mult):
        if not self.moonbag_step_floors_enabled:
            return 0.0, 0.0
        start = self.moonbag_step_trigger_mult
        interval = self.moonbag_step_interval_mult
        lag = self.moonbag_step_floor_lag_mult
        if start <= 0 or interval <= 0 or lag <= 0:
            return 0.0, 0.0
        if peak_mult + 1e-9 < start:
            return 0.0, 0.0

        steps = int(((peak_mult + 1e-9) - start) // interval)
        trigger_mult = start + max(steps, 0) * interval
        floor_mult = max(trigger_mult - lag, 0.0)
        return floor_mult, trigger_mult

    def arm_moonbag_step_floor(self, pos, peak_mult, entry_price):
        floor_mult, trigger_mult = self.moonbag_step_floor_multiple(peak_mult)
        if floor_mult <= 0:
            return

        floor_price = entry_price * floor_mult
        if floor_price <= _f(pos, "stop_floor_price", 0):
            return

        pos["stop_floor_price"] = floor_price
        pos["stop_floor_multiple"] = floor_mult
        pos["stop_floor_source"] = (
            f"moonbag_{trigger_mult:g}x_to_{floor_mult:g}x"
        )

    def raise_stop_floor(self, pos, floor_price, source, entry_price):
        if floor_price <= _f(pos, "stop_floor_price", 0):
            return False
        pos["stop_floor_price"] = floor_price
        pos["stop_floor_multiple"] = (
            floor_price / entry_price if entry_price > 0 else 0
        )
        pos["stop_floor_source"] = source
        return True

    def tail_tp_enabled(self, tp_mode=None):
        return (tp_mode or self.tp_mode) in self.tail_modes

    def position_tp_mode(self, pos):
        levels_done = {
            str(level)
            for level in (pos.get("levels_done") or set())
        }
        if any(level.startswith("q3_") for level in levels_done):
            return "q3"
        return self.tp_mode

    def tail_estimated_moonbag_pct(self):
        remaining = 1.0
        if self.tail_cost_recovery_multiple > 1.0:
            recovery_sell = (
                self.tail_cost_recovery_pct
                / max(self.tail_cost_recovery_multiple, 1e-18)
            )
            recovery_sell = min(
                recovery_sell,
                self.tail_cost_recovery_max_sell_pct,
                remaining,
            )
            remaining -= max(recovery_sell, 0.0)

        for _, fraction in self.tail_scale_out_tiers:
            remaining -= remaining * min(max(fraction, 0.0), 1.0)

        return max(remaining, 0.0)

    def tail_cost_recovery_qty(self, pos, price, initial_tokens):
        if price <= 0 or initial_tokens <= 0:
            return 0.0

        remaining = max(_f(pos, "remaining", 0), 0.0)
        if remaining <= 0:
            return 0.0

        target_usd = max(_f(pos, "cost_usd", 0), 0.0) * self.tail_cost_recovery_pct
        unrecovered_usd = max(target_usd - _f(pos, "proceeds", 0), 0.0)
        if unrecovered_usd <= 0:
            return 0.0

        cap_qty = initial_tokens * self.tail_cost_recovery_max_sell_pct
        already_sold = max(initial_tokens - remaining, 0.0)
        cap_qty_left = max(cap_qty - already_sold, 0.0)
        return min(unrecovered_usd / price, remaining, cap_qty_left)

    def tail_scale_plan(self, entry_price):
        plan = []
        if (
            self.tail_cost_recovery_multiple > 1.0
            and self.tail_cost_recovery_pct > 0
            and self.tail_cost_recovery_max_sell_pct > 0
        ):
            level = self.tail_cost_recovery_multiple
            plan.append({
                "key": f"tail_cost_recovery_{level:g}x",
                "fill_kind": f"scale_cost_recovery_{level:g}x",
                "level_mult": level,
                "target_price": entry_price * level,
                "mode": "cost_recovery",
                "fraction": 0.0,
            })

        for level, fraction in self.tail_scale_out_tiers:
            plan.append({
                "key": f"tail_scale_{level:g}x",
                "fill_kind": f"scale_tail_{level:g}x",
                "level_mult": level,
                "target_price": entry_price * level,
                "mode": "remaining_fraction",
                "fraction": fraction,
            })

        return sorted(plan, key=lambda step: step["level_mult"])

    def q3_targets(self, pos, entry_price, ts):
        n_rungs = max(len(self.ladder), 1)
        existing = pos.get("q3_targets")
        if existing and len(existing) >= n_rungs:
            return [float(t) for t in existing if float(t) > entry_price]

        token = pos.get("token")
        candles = _recent_candles_for_atr(token, as_of_ts=ts) if token else []
        nodes = []
        if candles:
            prof = volume_profile(candles, bins=24)
            if not prof.get("error"):
                nodes = sorted(
                    p for p, _ in high_volume_nodes(prof, n=8, above=entry_price)
                )

        swing_low = entry_swing_low(candles, _f(pos, "entry_ts"), ts)
        swing_low = swing_low or _min_low(candles) or entry_price * 0.50
        if swing_low >= entry_price:
            swing_low = entry_price * 0.50
        swing_high = max(entry_price, _f(pos, "peak", entry_price))
        raw = fibonacci_extension_levels(
            swing_low,
            swing_high,
            exts=self.q3_fib_extensions,
        )
        targets = []
        for target in raw:
            target = _snap_up_to_node(target, nodes, self.q3_node_snap_band)
            if (
                target >= entry_price * self.q3_min_target_mult
                and (not targets or target > targets[-1])
            ):
                targets.append(target)
        for ext in self.q3_fib_extensions:
            if len(targets) >= n_rungs:
                break
            if ext < self.q3_min_target_mult:
                continue
            fallback = entry_price * ext
            if fallback > entry_price and all(
                abs(fallback - target) / max(target, 1e-18) > 1e-6
                for target in targets
            ):
                targets.append(fallback)
        targets = sorted(set(targets))[:n_rungs]
        pos["q3_targets"] = targets
        pos["q3_swing_low"] = swing_low
        pos["q3_swing_high"] = swing_high
        pos["q3_tp_mode"] = "fib"
        return targets

    def arm_unified_floor(self, pos, peak_mult, entry_price, ts):
        if not pos.get("scaled"):
            return
        self.arm_completed_scale_floor(pos, entry_price)
        self.raise_stop_floor(pos, entry_price, "break_even", entry_price)
        self.arm_moonbag_step_floor(pos, peak_mult, entry_price)

        if self.q3_atr_trail_enabled:
            try:
                period = int(getattr(config, "POSITION_ATR_STOP_PERIOD", 14))
                candles = _recent_candles_for_atr(pos.get("token"), as_of_ts=ts)
                atr = downside_atr(candles, period=period)
                if not atr or atr <= 0:
                    atr = full_atr(candles, period=period)
                if atr and atr > 0:
                    floor = _f(pos, "peak", entry_price) - self.q3_atr_trail_k * atr
                    self.raise_stop_floor(pos, floor, "q3_atr_trail", entry_price)
            except Exception:
                pass

        try:
            candles = _recent_candles_for_atr(pos.get("token"), as_of_ts=ts)
            prof = volume_profile(candles, bins=12)
            if not prof.get("error"):
                sup = nearest_support(prof, _f(pos, "peak", entry_price))
                if sup and sup[0] > 0:
                    floor = sup[0] * (1.0 - self.q3_vp_floor_buffer)
                    self.raise_stop_floor(pos, floor, "q3_vp_support", entry_price)
        except Exception:
            pass

    def liquidity_collapse_reason(self, pos, features):
        if not self.liquidity_enabled or not features:
            return ""
        current = self.current_liquidity(features)
        if current <= 0:
            return ""
        entry = _f(pos, "entry_liquidity", current)
        peak = max(_f(pos, "peak_liquidity", current), current)
        pos["peak_liquidity"] = peak
        reference = max(entry, peak)
        if reference < self.liquidity_min_reference_usd:
            return ""
        if entry > 0 and current <= entry * (1 - self.liquidity_from_entry_pct):
            return "liquidity_drain_from_entry"
        if peak > 0 and current <= peak * (1 - self.liquidity_from_peak_pct):
            return "liquidity_drain_from_peak"
        return ""

    def sell_only_flow_reason(self, pos, price, features):
        if not self.sell_only_enabled or not features:
            return ""
        entry_price = max(_f(pos, "entry_price", 0), 1e-18)
        if price / entry_price > self.sell_only_max_multiple:
            return ""
        sell_volume = _f(features, "sell_volume_5m", 0)
        buy_volume = _f(features, "buy_volume_5m", 0)
        entry_notional = _f(pos, "cost_usd", 0)
        min_sell = max(
            self.sell_only_min_sell_volume,
            entry_notional * self.sell_only_min_entry_multiple,
        )
        if sell_volume < min_sell:
            return ""
        buy_sell_ratio = buy_volume / max(sell_volume, 1e-18)
        if (
            buy_volume > self.sell_only_max_buy_volume
            and buy_sell_ratio > self.sell_only_max_buy_sell_ratio
        ):
            return ""
        return "sell_only_flow_exit"

    def strict_early_reason(self, pos, mult, features, new_feature_tick):
        if not self.strict_enabled or not features:
            return ""
        weak_signals = (
            int(_f(features, "pressure", 0) <= self.strict_max_pressure)
            + int(_f(features, "volume_liquidity_ratio", 0) <= self.strict_max_vlr)
            + int(_f(features, "buy_sell_ratio", 0) <= self.strict_max_bsr)
        )
        triggered = (
            mult <= 1 - self.strict_loss_pct
            and weak_signals >= self.strict_min_weak
        )
        if triggered and new_feature_tick:
            count = int(_f(pos, "strict_early_breach_count", 0)) + 1
            pos["strict_early_breach_count"] = count
            if count >= max(self.strict_confirm_ticks, 1):
                pos["strict_early_breach_count"] = 0
                return "strict_early_failure_exit"
        elif not triggered and new_feature_tick and pos.get("strict_early_breach_count"):
            pos["strict_early_breach_count"] = 0
        return ""

    def no_progress_reason(self, pos, ts, peak_mult, features):
        if not self.no_progress_enabled or not features:
            return ""
        if ts - _f(pos, "entry_ts", ts) < self.no_progress_min_s:
            return ""
        if peak_mult >= self.no_progress_peak_mult:
            return ""
        weak = (
            _f(features, "pressure", 0) <= self.no_progress_max_pressure
            or _f(features, "buy_sell_ratio", 0) <= self.no_progress_max_bsr
        )
        return "no_progress_time_stop" if weak else ""

    def _position_initial_stop_pct(self, pos):
        """Per-position initial stop %: adaptive (downside-ATR) when enabled,
        computed once and cached on the position; else the global flat %."""
        cached = pos.get("initial_stop_pct")
        if cached is not None:
            return float(cached)
        adaptive = adaptive_initial_stop_pct(pos.get("token"),
                                             _f(pos, "entry_price", 0))
        pct = adaptive if adaptive is not None else self.initial_stop_pct
        basis = "atr" if adaptive is not None else "flat"
        # Layer 3 (tier -> stop coupling, default OFF): a reduced-size Tier-B
        # entry can tolerate a wider initial invalidation. Capped at the ATR-stop
        # max so it never exceeds the configured worst-case stop width.
        if _b("LATTICE_TIER_STOP_COUPLING_ENABLED", False) and pos.get("entry_tier") == "B":
            pct = min(
                pct * float(getattr(config, "LATTICE_TIER_B_STOP_WIDEN_MULT", 1.3) or 1.0),
                float(getattr(config, "POSITION_ATR_STOP_MAX_PCT", 0.70) or 0.70),
            )
            basis += "+tierB"
        pos["initial_stop_pct"] = pct
        pos["initial_stop_basis"] = basis
        return pct

    def manage(self, pos, price, ts, max_hold_s=None, features=None):
        fills = []
        ep = max(_f(pos, "entry_price", 0), 1e-18)
        pos["peak"] = max(_f(pos, "peak", price), price)
        mult = price / ep
        peak_mult = pos["peak"] / ep
        new_feature_tick = self.append_features(pos, features)

        if pos.get("remaining", 0) <= 0:
            return fills

        reason = self.liquidity_collapse_reason(pos, features)
        if reason:
            return self.close_all(pos, reason, price, fills)

        reason = self.sell_only_flow_reason(pos, price, features)
        if reason:
            return self.close_all(pos, reason, price, fills)

        if (not pos.get("scaled")
                and price <= ep * (1 - self._position_initial_stop_pct(pos))):
            return self.close_all(pos, "initial_stop", price, fills)

        if not pos.get("scaled"):
            reason = self.strict_early_reason(pos, mult, features, new_feature_tick)
            if reason:
                return self.close_all(pos, reason, price, fills)

            reason = self.no_progress_reason(pos, ts, peak_mult, features)
            if reason:
                return self.close_all(pos, reason, price, fills)

            if self.break_even_enabled:
                if peak_mult >= self.arm_mult:
                    pos["break_even_armed"] = True
                    pos["stop_floor_price"] = max(
                        _f(pos, "stop_floor_price", 0),
                        ep * self.break_even_floor_mult,
                    )

                floor = _f(pos, "stop_floor_price", 0)
                if pos.get("break_even_armed") and floor > 0 and price <= floor:
                    return self.close_all(pos, "break_even_floor", price, fills)
        else:
            self.arm_unified_floor(pos, peak_mult, ep, ts)
            floor = _f(pos, "stop_floor_price", 0)
            if floor > 0 and price <= floor:
                return self.close_all(pos, "scale_stop_floor", price, fills)

        initial_tokens = _f(pos, "cost_usd", 0) / ep
        pos.setdefault("levels_done", set())
        tp_mode = self.position_tp_mode(pos)
        if self.tail_tp_enabled(tp_mode):
            scale_plan = self.tail_scale_plan(ep)
        elif tp_mode == "q3":
            scale_plan = [
                {
                    "key": f"q3_{i + 1}",
                    "fill_kind": f"q3_scale_{target_price / ep:.3g}x",
                    "level_mult": target_price / ep,
                    "target_price": target_price,
                    "mode": "cumulative_target",
                    "fraction": target_frac,
                }
                for i, (target_price, (_, target_frac)) in enumerate(
                    zip(self.q3_targets(pos, ep, ts), self.ladder)
                )
            ]
        else:
            scale_plan = [
                {
                    "key": lvl,
                    "fill_kind": f"scale_{lvl:g}x",
                    "level_mult": lvl,
                    "target_price": ep * lvl,
                    "mode": "cumulative_target",
                    "fraction": target_frac,
                }
                for lvl, target_frac in self.ladder
            ]

        for step in scale_plan:
            level_key = step["key"]
            level_mult = step["level_mult"]
            target_price = step["target_price"]
            if (
                price >= target_price
                and level_key not in pos["levels_done"]
                and pos["remaining"] > 0
            ):
                if step["mode"] == "cost_recovery":
                    q = self.tail_cost_recovery_qty(pos, price, initial_tokens)
                elif step["mode"] == "remaining_fraction":
                    q = pos["remaining"] * step["fraction"]
                else:
                    current_sold = (
                        1.0 - pos["remaining"] / initial_tokens
                        if initial_tokens > 0
                        else 0.0
                    )
                    target = step["fraction"]
                    q = initial_tokens * max(target - current_sold, 0.0)
                    q = min(q, pos["remaining"])
                if q <= 0:
                    pos["levels_done"].add(level_key)
                    self.arm_scale_stop_floor(pos, level_mult, ep)
                    continue
                pos["remaining"] -= q
                pos["proceeds"] += q * price
                pos["levels_done"].add(level_key)
                pos["scaled"] = True
                self.arm_scale_stop_floor(pos, level_mult, ep)
                self.arm_unified_floor(pos, peak_mult, ep, ts)
                fills.append((step["fill_kind"], q, price))

        if pos.get("scaled") and pos["remaining"] > 0:
            self.arm_unified_floor(pos, peak_mult, ep, ts)

        active_trail = (
            self.high_mult_trail_pct
            if peak_mult >= self.high_mult_trigger
            else self.trail_pct
        )
        if (
            self.trailing_enabled
            and active_trail > 0
            and pos.get("scaled")
            and pos["remaining"] > 0
            and price <= pos["peak"] * (1 - active_trail)
        ):
            return self.close_all(pos, "trailing_stop", price, fills)

        if (
            _max_hold_close_due(
                pos,
                ts,
                max_hold_s,
                mult,
                peak_mult,
                self.max_hold_exempt_mult,
                self.max_hold_partial_runner_mult,
                self.max_hold_partial_runner_s,
                self.max_hold_partial_runner_require_profit,
            )
        ):
            return self.close_all(pos, "max_hold", price, fills)

        if pos["remaining"] <= 1e-12 and not pos.get("closed"):
            pos["closed"] = True
            pos["reason"] = pos.get("reason") or "scaled_out"
        return fills


_NEW_MANAGER = PositionManager()


def manage(pos, price, ts, max_hold_s=None, features=None, engine=None):
    selected = str(
        engine
        or getattr(config, "LATTICE_EXIT_ENGINE", "new")
        or "new"
    ).lower()
    if selected == "old":
        return old_manage(pos, price, ts, max_hold_s=max_hold_s, features=features)
    return _NEW_MANAGER.manage(pos, price, ts, max_hold_s=max_hold_s, features=features)
