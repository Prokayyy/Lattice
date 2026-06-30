"""Three-stage conviction pipeline: Universe -> Conviction -> Timing.

The old scanner collapsed "is this interesting", "is it real", and "is now a
good tick to buy" into one live gate cascade, so jitter on any gate dropped good
trades. Here they are separate stages, and the output is a windowed entry plan
(entry zone + invalidation + conviction), NOT a one-tick pass — the executor
re-checks light liveness conditions, it does not re-run the whole stack.

Stage 1 Universe   : cheap re-acceleration filter -> candidate.
Stage 2 Conviction : lattice vetoes + revival shape + calibrated P(>=2x).
Stage 3 Timing     : lifecycle phase (reject exhaustion) -> entry zone + stop.
"""

import os
import time
from dataclasses import dataclass, field, asdict

import config

from discovery import features as F
from discovery.ranker import ConvictionRanker
from discovery.lattice import lattice_verdict, NullParticipationProvider
from discovery.revival import revival_shape

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB = os.path.join(ROOT, "scanner.db")
DEFAULT_MODEL = os.path.join(os.path.dirname(__file__), "models", "conviction_ranker.json")
# Displayed invalidation must reflect the REAL exit stop the manager uses
# (LATTICE_EXIT_INITIAL_STOP_PCT -> POSITION_INITIAL_STOP_LOSS_PCT), not a separate
# hardcoded value, or ENTRY SIGNAL alerts mislead when the configured stop changes.
DEFAULT_INITIAL_STOP_PCT = float(
    getattr(config, "LATTICE_EXIT_INITIAL_STOP_PCT",
            getattr(config, "POSITION_INITIAL_STOP_LOSS_PCT", 0.30)) or 0.30
)
# Used only when the caller passes no explicit floor AND the loaded model
# carries no recommended_min_conviction (older model files). The deployed pnl
# model records its own cutoff (0.30), which takes precedence over this.
FALLBACK_MIN_CONVICTION = 0.30


@dataclass
class EntryAlert:
    token_address: str
    symbol: str
    timestamp: float
    conviction: float                 # calibrated P(>=2x) from the ranker
    entry_zone: tuple                 # (low, high) USD price the executor may fill in
    invalidation_price: float         # thesis-break: exit if breached
    revival_score: float
    lattice_composite: float
    participation_blind: bool
    evidence: dict = field(default_factory=dict)
    narrative_context: dict = field(default_factory=dict)
    reason: str = ""

    def to_dict(self):
        d = asdict(self)
        d["entry_zone"] = list(self.entry_zone)
        return d


class ConvictionPipeline:
    def __init__(self, db_path=DEFAULT_DB, model_path=DEFAULT_MODEL,
                 participation=None, min_conviction=None, initial_stop_pct=DEFAULT_INITIAL_STOP_PCT,
                 min_lattice=0.0, max_price_change_1h=0.0,
                 max_price_change_24h=0.0):
        self.db_path = db_path
        self.model = ConvictionRanker.load(model_path) if os.path.exists(model_path) else None
        self.participation = participation or NullParticipationProvider()
        # Default the conviction floor to the deployed model's own recommended
        # cutoff so the gate tracks whatever model is loaded. Callers may still
        # pin a value (sweeps and the retrain pass-through use 0.0 for no gate).
        if min_conviction is None:
            # Layer 2: when the additive scorecard is the capital selector, the
            # conviction float is retired to a low safety floor (it anti-ranks
            # outcomes). Otherwise keep the deployed model's recommended cutoff.
            if bool(getattr(config, "LATTICE_SCORECARD_ENABLED", False)):
                min_conviction = float(
                    getattr(config, "LATTICE_MIN_CONVICTION_FLOOR", 0.05) or 0.0
                )
            else:
                rec = getattr(self.model, "recommended_min_conviction", None)
                min_conviction = rec if rec is not None else FALLBACK_MIN_CONVICTION
        self.min_conviction = float(min_conviction)
        self.initial_stop_pct = initial_stop_pct
        self.min_lattice = min_lattice
        self.max_price_change_1h = max_price_change_1h
        self.max_price_change_24h = max_price_change_24h
        # Layer 2: with the scorecard as the selector, 1h/24h overextension is a
        # soft scorecard penalty (scorecard._pc1h_shape / _overextension), not a
        # hard reject. Drop the hard caps when the scorecard is enabled — else
        # pc24>300 (which the data shows beats base on dead-rate) keeps getting
        # rejected before it can be scored. Mirrors the conviction retirement
        # above so enabling L2 is self-consistent without also having to zero
        # LATTICE_MAX_ENTRY_PRICE_CHANGE_1H/_24H by hand.
        if bool(getattr(config, "LATTICE_SCORECARD_ENABLED", False)):
            self.max_price_change_1h = 0.0
            self.max_price_change_24h = 0.0

    # ---- Stage 1 ----
    def universe(self, row):
        pc5 = float(row.get("price_change_5m") or 0)
        vol = float(row.get("volume_1h") or 0)
        # re-accelerating with some real volume; wide net, cheap.
        # pc5 is a PERCENT; 2% matches the live system's min entry impulse.
        return pc5 > 2.0 and vol > 0

    def evaluate(self, row, now=None):
        """row: a signal_snapshots-shaped dict (live or replayed).
        Returns EntryAlert, or (None, reason)."""
        now = now or float(row.get("timestamp") or time.time())
        token = row.get("token_address") or row.get("address") or ""
        price = float(row.get("price") or 0)

        if not self.universe(row):
            return None, "not_in_universe"

        # Entry-overheating cap: skip tokens already pumped hard over 1h. Higher
        # entry price_change_1h correlated with WORSE realized PnL (rho ~ -0.15).
        if self.max_price_change_1h:
            pc1h = float(row.get("price_change_1h") or 0)
            if pc1h > self.max_price_change_1h:
                return None, f"overheated_1h:{pc1h:.0f}>{self.max_price_change_1h:.0f}"

        # Entry-overheating cap on the 24h horizon. The decisive late-pump
        # signal: a token already up >Nx on 24h is a chase even if its 1h/5m
        # look calm (e.g. GAEJOOK: 1h +7.6% but 24h +690%). Validated +$973
        # in-sample and revival-safe. 0 = off.
        if self.max_price_change_24h:
            pc24h = float(row.get("price_change_24h") or 0)
            if pc24h > self.max_price_change_24h:
                return None, f"overheated_24h:{pc24h:.0f}>{self.max_price_change_24h:.0f}"

        # Stage 2a: participation breadth (the decisive axis; may be blind)
        breadth = self.participation.breadth(token)
        sv = lattice_verdict(row, participation=breadth,
                               liquidity_change_pct=row.get("liquidity_change_pct"))
        if not sv["passed"]:
            return None, "lattice_veto:" + ",".join(sv["vetoes"])

        # Lattice-level floor: the single entry feature that separated winning
        # paper trades from losers in the outcome data. 0.0 = disabled.
        if sv["composite"] < self.min_lattice:
            return None, f"low_lattice:{sv['composite']:.3f}<{self.min_lattice}"

        # Stage 2b: calibrated conviction. Cheap, and it GATES, so run it before
        # the costlier revival-shape DB lookup.
        conviction = 0.0
        if self.model is not None:
            conviction = self.model.proba(F.extract(row, participation=breadth))
        if conviction < self.min_conviction:
            return None, f"low_conviction:{conviction:.3f}<{self.min_conviction}"
        if price <= 0:
            return None, "no_price"

        # Stage 2c: revival shape (token-relative; evidence only, never gates) —
        # computed lazily, only for rows that already cleared conviction.
        rev = revival_shape(self.db_path, token, now)

        # Stage 3: timing/exit plan. Entry window = now..slightly above; the
        # executor fills anywhere in the band while liveness holds. Invalidation
        # = route-style initial stop below entry.
        entry_zone = (price, price * 1.05)
        invalidation = price * (1.0 - self.initial_stop_pct)

        return EntryAlert(
            token_address=token,
            symbol=row.get("symbol", ""),
            timestamp=now,
            conviction=round(conviction, 4),
            entry_zone=(round(entry_zone[0], 12), round(entry_zone[1], 12)),
            invalidation_price=round(invalidation, 12),
            revival_score=rev["revival_score"],
            lattice_composite=round(sv["composite"], 4),
            participation_blind=sv["participation_blind"],
            evidence={"lattice_axes": sv["axes"], "revival": rev,
                      "vetoes": sv["vetoes"]},
            reason="ok",
        ), "ok"
