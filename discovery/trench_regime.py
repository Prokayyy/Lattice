"""Shadow-only trench activity regime scoring.

The scorer is intentionally local and deterministic. It does not gate entries;
it emits telemetry so later reports can compare HOT/COLD periods against the
paper ledger before any capital behavior changes.
"""

from __future__ import annotations

import re
from collections import deque


_WORD_RE = re.compile(r"[a-z0-9]+")


def _f(value, default=0.0):
    try:
        return float(value if value is not None else default)
    except (TypeError, ValueError):
        return float(default)


def _norm(value):
    return " ".join(_WORD_RE.findall(str(value or "").lower()))


def _tokens(value):
    return _WORD_RE.findall(str(value or "").lower())


def _has_term(text, token_set, term):
    raw = str(term or "").strip().lower()
    if not raw:
        return False

    clean = _norm(raw)
    if not clean:
        return False

    term_tokens = clean.split()
    if len(term_tokens) == 1:
        token = term_tokens[0]
        if len(token) <= 4:
            return token in token_set
        return token in token_set or token in text

    return f" {clean} " in f" {text} "


def _proximity_hit(tokens, names, triggers, max_gap):
    if not tokens:
        return False

    name_positions = [
        idx for idx, token in enumerate(tokens) if token in names
    ]
    trigger_positions = [
        idx for idx, token in enumerate(tokens) if token in triggers
    ]
    return any(
        abs(name_idx - trigger_idx) <= max_gap
        for name_idx in name_positions
        for trigger_idx in trigger_positions
    )


class TrenchRegimeShadow:
    def __init__(
        self,
        *,
        enabled=True,
        window_seconds=3600.0,
        hot_score=65.0,
        euphoria_score=85.0,
        cold_score=25.0,
        hot_candidates_per_hour=140.0,
        hot_alerts_per_hour=24.0,
        hot_entries_per_hour=8.0,
        hot_open_upnl_usd=250.0,
        hot_pc5=12.0,
        hot_volume_5m=750.0,
        watch_terms=(),
        kol_terms=(),
        trigger_terms=(),
        proximity_tokens=5,
    ):
        self.enabled = bool(enabled)
        self.window_seconds = max(float(window_seconds or 0.0), 60.0)
        self.hot_score = float(hot_score)
        self.euphoria_score = float(euphoria_score)
        self.cold_score = float(cold_score)
        self.hot_candidates_per_hour = max(float(hot_candidates_per_hour), 1.0)
        self.hot_alerts_per_hour = max(float(hot_alerts_per_hour), 1.0)
        self.hot_entries_per_hour = max(float(hot_entries_per_hour), 1.0)
        self.hot_open_upnl_usd = max(float(hot_open_upnl_usd), 1.0)
        self.hot_pc5 = max(float(hot_pc5), 1.0)
        self.hot_volume_5m = max(float(hot_volume_5m), 1.0)
        self.watch_terms = tuple(watch_terms or ())
        self.kol_terms = tuple(kol_terms or ())
        self.trigger_terms = tuple(trigger_terms or ())
        self.proximity_tokens = max(int(proximity_tokens or 0), 1)
        self._candidate_ts = deque()
        self._alert_ts = deque()

    def _prune(self, ts):
        cutoff = float(ts) - self.window_seconds
        for events in (self._candidate_ts, self._alert_ts):
            while events and events[0] < cutoff:
                events.popleft()

    def _rate_per_hour(self, events):
        return len(events) * 3600.0 / self.window_seconds

    def _narrative(self, row, alert):
        parts = [
            row.get("symbol"),
            row.get("name"),
            row.get("token_name"),
            row.get("description"),
            row.get("quality_tag"),
            row.get("source"),
            getattr(alert, "symbol", ""),
        ]
        text = _norm(" ".join(str(part or "") for part in parts))
        token_list = text.split()
        token_set = set(token_list)

        hits = []
        for term in self.watch_terms:
            if _has_term(text, token_set, term):
                hits.append(str(term))

        kol_hit_terms = [
            str(term) for term in self.kol_terms
            if _has_term(text, token_set, term)
        ]
        trigger_set = {
            clean for term in self.trigger_terms
            for clean in _norm(term).split()
            if clean
        }
        kol_set = {
            clean for term in self.kol_terms
            for clean in _norm(term).split()
            if clean
        }
        proximity = _proximity_hit(
            token_list,
            kol_set,
            trigger_set,
            self.proximity_tokens,
        )

        score = 0.0
        if hits:
            score += min(8.0, 2.0 * len(hits))
        if kol_hit_terms:
            score += 6.0
        if proximity:
            score += 6.0

        return min(score, 20.0), {
            "hits": hits[:8],
            "kol_hits": kol_hit_terms[:6],
            "proximity": bool(proximity),
        }

    def _open_book(self, open_pos):
        upnl = 0.0
        open_15x = open_2x = open_3x = 0
        for pos in (open_pos or {}).values():
            entry = _f(pos.get("entry_price"))
            last = _f(pos.get("last_price"), entry)
            remaining = _f(pos.get("remaining"))
            proceeds = _f(pos.get("proceeds"))
            cost = _f(pos.get("cost_usd"))
            pnl = proceeds + remaining * last - cost
            upnl += pnl
            mult = (last / entry) if entry > 0 else 0.0
            if mult >= 1.5:
                open_15x += 1
            if mult >= 2.0:
                open_2x += 1
            if mult >= 3.0:
                open_3x += 1

        return {
            "open_upnl_usd": round(upnl, 4),
            "open_15x": open_15x,
            "open_2x": open_2x,
            "open_3x": open_3x,
        }

    def snapshot(
        self,
        *,
        row,
        alert,
        detail=None,
        ts,
        alert_due=False,
        entry_times=(),
        open_pos=None,
    ):
        if not self.enabled:
            return {
                "enabled": False,
                "regime": "disabled",
                "score": 0.0,
                "shadow_capital_allowed": False,
                "components": {},
                "narrative": {"hits": [], "kol_hits": [], "proximity": False},
            }

        ts = float(ts or 0.0)
        self._candidate_ts.append(ts)
        if alert_due:
            self._alert_ts.append(ts)
        self._prune(ts)

        entry_cutoff = ts - self.window_seconds
        entry_rate = (
            sum(1 for t in (entry_times or []) if entry_cutoff <= float(t) <= ts)
            * 3600.0 / self.window_seconds
        )
        candidate_rate = self._rate_per_hour(self._candidate_ts)
        alert_rate = self._rate_per_hour(self._alert_ts)

        book = self._open_book(open_pos)
        pc5 = max(_f(row.get("price_change_5m")), 0.0)
        vol5 = max(_f(row.get("volume_5m")), 0.0)
        bsr = max(_f(row.get("buy_sell_ratio")), 0.0)
        pressure = max(_f(row.get("pressure")), 0.0)
        breadth = (detail or {}).get("breadth")
        breadth = _f(breadth, 0.0)

        narrative_score, narrative = self._narrative(row, alert)

        components = {
            "candidate_velocity": min(
                18.0,
                18.0 * candidate_rate / self.hot_candidates_per_hour,
            ),
            "alert_velocity": min(
                12.0,
                12.0 * alert_rate / self.hot_alerts_per_hour,
            ),
            "entry_velocity": min(
                10.0,
                10.0 * entry_rate / self.hot_entries_per_hour,
            ),
            "open_runners": min(
                18.0,
                book["open_15x"] * 2.0
                + book["open_2x"] * 3.0
                + book["open_3x"] * 3.0,
            ),
            "open_upnl": min(
                18.0,
                18.0 * max(book["open_upnl_usd"], 0.0)
                / self.hot_open_upnl_usd,
            ),
            "flow": min(
                14.0,
                5.0 * pc5 / self.hot_pc5
                + 4.0 * vol5 / self.hot_volume_5m
                + min(3.0, bsr)
                + min(2.0, pressure / 10.0)
                + max(0.0, breadth) * 2.0,
            ),
            "narrative": narrative_score,
        }
        score = round(min(sum(components.values()), 100.0), 2)

        if score >= self.euphoria_score:
            regime = "euphoria"
        elif score >= self.hot_score:
            regime = "hot"
        elif score <= self.cold_score:
            regime = "cold"
        else:
            regime = "normal"

        return {
            "enabled": True,
            "regime": regime,
            "score": score,
            "shadow_capital_allowed": regime in {"hot", "euphoria"},
            "components": {k: round(v, 2) for k, v in components.items()},
            "candidate_rate_h": round(candidate_rate, 2),
            "alert_rate_h": round(alert_rate, 2),
            "entry_rate_h": round(entry_rate, 2),
            **book,
            "narrative": narrative,
        }
