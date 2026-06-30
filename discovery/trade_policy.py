"""Entry policy for Lattice paper/live trading.

The scanner and conviction model decide whether a token is interesting. This
policy decides whether that alert is worth risking capital on right now.
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
TRADE_LEDGER = ROOT / "discovery" / "trades.jsonl"


def _f(row, key, default=0.0):
    try:
        value = row.get(key)
        return float(value) if value is not None else float(default)
    except (TypeError, ValueError):
        return float(default)


def _env_bool(name, default=False):
    return os.getenv(name, str(default)).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _env_float(name, default):
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return float(default)


def _env_int(name, default):
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return int(default)


def _clamp(value, lo, hi):
    return max(lo, min(hi, value))


@dataclass
class TradeDecision:
    enter: bool
    tier: str
    score: float
    size_usd: float
    live_enabled: bool
    reason: str
    reasons: list[str] = field(default_factory=list)

    def status(self):
        if self.enter:
            live = "live eligible" if self.live_enabled else "paper only"
            return f"entered policy {self.tier} {self.score:.0f}/100; {live}"
        return f"not entered; policy {self.score:.0f}/100: {self.reason}"

    def as_dict(self):
        return {
            "enter": self.enter,
            "tier": self.tier,
            "score": round(self.score, 2),
            "size_usd": self.size_usd,
            "live_enabled": self.live_enabled,
            "reason": self.reason,
            "reasons": list(self.reasons),
        }


class TradePolicy:
    def __init__(self, ledger_path=TRADE_LEDGER):
        self.enabled = _env_bool("LATTICE_TRADE_POLICY_ENABLED", True)
        self.paper_size_usd = _env_float("LATTICE_TRADE_POLICY_BASE_SIZE_USD", 20)
        self.tier_b_mult = _env_float("LATTICE_TRADE_POLICY_TIER_B_SIZE_MULT", 0.50)
        self.tier_c_mult = _env_float("LATTICE_TRADE_POLICY_TIER_C_SIZE_MULT", 0.25)
        self.min_size_usd = _env_float("LATTICE_TRADE_POLICY_MIN_SIZE_USD", 5)
        self.max_open_positions = _env_int("LATTICE_TRADE_POLICY_MAX_OPEN_POSITIONS", 4)
        self.min_score = _env_float("LATTICE_TRADE_POLICY_MIN_SCORE", 58)
        self.live_min_score = _env_float("LATTICE_TRADE_POLICY_LIVE_MIN_SCORE", 72)
        self.max_loss_streak = _env_int("LATTICE_TRADE_POLICY_MAX_LOSS_STREAK", 4)
        self.ledger_path = Path(ledger_path)

    def recent_loss_streak(self, limit=8):
        try:
            lines = self.ledger_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return 0

        streak = 0
        for line in reversed(lines[-limit:]):
            try:
                pnl = float(json.loads(line).get("pnl_usd") or 0)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if pnl < 0:
                streak += 1
                continue
            break
        return streak

    def decide(self, row, alert, detail, open_positions, cash_usd, route_depth=None):
        if not self.enabled:
            return TradeDecision(
                True,
                "legacy",
                100.0,
                self.paper_size_usd,
                True,
                "policy_disabled",
                ["policy disabled"],
            )

        score, reasons = self._score(row, alert, detail)
        live_route_ok = True
        if route_depth and route_depth.get("enabled", True):
            route_score = float(route_depth.get("score") or 0.0)
            score += route_score
            live_route_ok = bool(route_depth.get("live_ok"))
            reasons.append(
                f"route depth {route_score:+.0f}: {route_depth.get('reason', 'unknown')}"
            )
        loss_streak = self.recent_loss_streak()
        open_count = len(open_positions or {})

        if open_count >= self.max_open_positions:
            return TradeDecision(
                False,
                "blocked",
                score,
                0.0,
                False,
                f"max_open_positions:{open_count}>={self.max_open_positions}",
                reasons + [f"open positions {open_count}/{self.max_open_positions}"],
            )

        if loss_streak >= self.max_loss_streak:
            score -= 12
            reasons.append(f"loss streak {loss_streak}")

        if score < self.min_score:
            return TradeDecision(
                False,
                "blocked",
                score,
                0.0,
                False,
                f"score_below_min:{score:.1f}<{self.min_score:.1f}",
                reasons,
            )

        if score >= 82:
            tier = "A"
            size = self.paper_size_usd
        elif score >= 70:
            tier = "B"
            size = self.paper_size_usd * self.tier_b_mult
        else:
            tier = "C"
            size = self.paper_size_usd * self.tier_c_mult

        size = round(max(size, self.min_size_usd), 2)

        if cash_usd < size:
            return TradeDecision(
                False,
                "blocked",
                score,
                0.0,
                False,
                f"insufficient_paper_cash:{cash_usd:.2f}<{size:.2f}",
                reasons,
            )

        live_enabled = score >= self.live_min_score and loss_streak < 3 and live_route_ok

        return TradeDecision(
            True,
            tier,
            score,
            size,
            live_enabled,
            "ok",
            reasons,
        )

    def _score(self, row, alert, detail):
        reasons = []
        score = 0.0

        conviction = float(getattr(alert, "conviction", 0.0) or 0.0)
        conviction_score = _clamp((conviction - 0.18) / 0.22, 0.0, 1.0) * 26
        score += conviction_score
        reasons.append(f"conv {conviction:.2f}")

        lattice = float(getattr(alert, "lattice_composite", 0.0) or 0.0)
        score += _clamp(lattice, 0.0, 1.0) * 14
        reasons.append(f"lattice {lattice:.2f}")

        br = (detail or {}).get("breadth")
        buyers_sig = (detail or {}).get("buyers_sig")
        concentration = (detail or {}).get("concentration")
        if br is None:
            score += 6
            reasons.append("breadth blind")
        else:
            score += _clamp((float(br) + 0.4) / 1.4, 0.0, 1.0) * 15
            reasons.append(f"breadth {float(br):+.2f}")

        if buyers_sig is not None:
            score += _clamp((float(buyers_sig) + 0.2) / 1.2, 0.0, 1.0) * 7
        if concentration is not None:
            conc = float(concentration)
            if conc <= 0.25:
                score += 6
            elif conc <= 0.40:
                score += 3
            else:
                score -= 6
                reasons.append(f"high concentration {conc:.0%}")

        pc5 = _f(row, "price_change_5m")
        pc1h = _f(row, "price_change_1h")
        if 3 <= pc5 <= 45:
            score += 11
        elif 45 < pc5 <= 100:
            score += 7
            reasons.append(f"extended 5m {pc5:.1f}%")
        elif pc5 > 100:
            score -= 12
            reasons.append(f"overextended 5m {pc5:.1f}%")
        else:
            score -= 8
            reasons.append(f"weak 5m {pc5:.1f}%")

        if pc1h >= 0:
            score += min(8, math.log1p(pc1h) * 2.0)
        else:
            score -= 5
            reasons.append(f"negative 1h {pc1h:.1f}%")

        vlr = _f(row, "volume_liquidity_ratio")
        h1_vlr = _f(row, "h1_volume_liquidity_ratio")
        liquidity = _f(row, "liquidity")
        if 0.25 <= vlr <= 3.5:
            score += 8
        elif vlr > 5.0:
            score -= 10
            reasons.append(f"thin route VLR {vlr:.2f}")
        else:
            score -= 4
            reasons.append(f"low VLR {vlr:.2f}")

        if 0.15 <= h1_vlr <= 5.0:
            score += 4

        if liquidity > 0:
            score += _clamp(math.log10(liquidity + 1) / 5.0, 0.0, 1.0) * 5
        elif str(row.get("lifecycle") or "").lower() != "bonding_curve":
            score -= 8
            reasons.append("no liquidity")

        bsr = _f(row, "buy_sell_ratio")
        h1_bsr = _f(row, "h1_buy_sell_ratio")
        b5 = _f(row, "buy_volume_5m")
        s5 = _f(row, "sell_volume_5m")
        if bsr >= 1.25:
            score += 8
        elif bsr >= 1.05:
            score += 4
        else:
            score -= 7
            reasons.append(f"weak 5m flow {bsr:.2f}")

        if h1_bsr >= 1.10:
            score += 4

        if b5 + s5 > 0:
            asym = (b5 - s5) / max(b5 + s5, 1e-9)
            score += _clamp((asym + 0.2) / 1.2, 0.0, 1.0) * 6
            if asym < -0.1:
                reasons.append(f"sell-heavy flow {asym:.2f}")

        pressure = _f(row, "pressure")
        score += _clamp(pressure / 100.0, 0.0, 1.0) * 5

        penalty = _f(row, "penalty")
        if penalty > 0:
            score -= min(10, penalty / 4.0)
            reasons.append(f"penalty {penalty:.0f}")

        return _clamp(score, 0.0, 100.0), reasons
