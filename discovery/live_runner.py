"""Live alert runner for the discovery scanner.

Polls scanner.db for NEW signal_snapshots (written by the live scanner), runs
the conviction pipeline, and posts [LATTICE]-tagged Telegram messages:
  - ENTRY SIGNAL  : a token alert for each new qualifying token (deduped per
                    token via an alert cooldown)
  - PAPER BUY/SELL: a live PAPER executor (wallet + size COPIED from the main
                    bot: POSITION_INITIAL_BALANCE_SOL, POSITION_POSITION_SIZE_USD)
                    opens/closes simulated positions and reports PnL.

Reads scanner.db only; paper positions are quote-marked via Definitive Flash
when configured, with DexScreener as fallback.
On first run it anchors to the newest snapshot, so it alerts only on snapshots
arriving AFTER it starts (no history blast).

Run:  env/bin/python -m discovery.live_runner
      LATTICE_TELEGRAM_DRY_RUN=true env/bin/python -m discovery.live_runner   # preview, no send
"""
import argparse, asyncio, json, os, sqlite3, time
from collections import deque
from discovery.pipeline import ConvictionPipeline
from discovery.notify import LatticeNotifier
from discovery.narrative_context import NarrativeContextProvider
from discovery.paper_trade import manage_with_features, SIZE_USD, BALANCE_SOL, SOL_USD
from discovery.participation import HeliusAlchemyParticipationProvider
from discovery.trench_regime import TrenchRegimeShadow
from trading.live_prices import fetch_live_prices, fetch_sol_usd_price
from trading.execution import LiveExecutionManager
import config

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "scanner.db")
STATE = os.path.join(os.path.dirname(__file__), "live_state.json")
HEARTBEAT = os.path.join(os.path.dirname(__file__), "live_runner_heartbeat.json")
CANDIDATE_LOG = os.path.join(os.path.dirname(__file__), "participation_log.jsonl")
ENTRY_DECISIONS = os.path.join(os.path.dirname(__file__), "entry_decisions.jsonl")
LEDGER = os.path.join(os.path.dirname(__file__), "trades.jsonl")
# Exact Telegram-sent alerts (forward-collect for the late-moon monitor). Mirrors
# participation_log's (ts, token, entry_price) key so the two dedupe cleanly.
SENT_ALERTS = os.path.join(os.path.dirname(__file__), "sent_alerts.jsonl")
# Shadow bundle/cluster evidence (Solana Tracker), keyed (token, int(alert_ts))
# so it joins to discovery_outcomes/participation_log for later validation.
BUNDLE_EVIDENCE = os.path.join(os.path.dirname(__file__), "bundle_evidence.jsonl")
DEFAULT_MAX_HOLD_H = float(getattr(config, "LATTICE_MAX_HOLD_H", 48.0) or 0.0)
DEFAULT_ALERT_COOLDOWN_H = 12.0
DEFAULT_OPEN_POSITION_MONITOR_S = float(
    getattr(
        config,
        "LATTICE_OPEN_POSITION_MONITOR_INTERVAL_SECONDS",
        5.0
    ) or 0.0
)


def _f(row, k, d=0.0):
    try:
        v = row.get(k); return float(v) if v is not None else d
    except (TypeError, ValueError):
        return d


def _flag(name, default=False):
    return os.getenv(name, str(default)).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _env_float(name, default=0.0):
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def token_short(token):
    token = str(token or "")
    return f"{token[:8]}...{token[-6:]}" if len(token) > 18 else token


def _is_scale_fill(kind):
    kind = str(kind or "")
    return kind.startswith("scale_") or kind.startswith("q3_scale_")


class LiveRunner:
    def __init__(self, min_conviction=None,
                 alert_cooldown_h=DEFAULT_ALERT_COOLDOWN_H,
                 entry_cooldown_h=6.0, max_hold_h=DEFAULT_MAX_HOLD_H,
                 poll_s=30, paper=True, dry_run=None, batch_cap=50000,
                 participation=True, min_breadth=-0.4, min_lattice=0.0,
                 max_price_change_1h=0.0, max_price_change_24h=0.0,
                 open_position_monitor_s=DEFAULT_OPEN_POSITION_MONITOR_S):
        self.pipe = ConvictionPipeline(min_conviction=min_conviction,
                                       min_lattice=min_lattice,
                                       max_price_change_1h=max_price_change_1h,
                                       max_price_change_24h=max_price_change_24h)
        # Resolved floor: the pipeline defaults it from the deployed model's
        # recommended cutoff when min_conviction is None.
        self.min_conviction = float(self.pipe.min_conviction)
        self.notifier = LatticeNotifier(dry_run=dry_run)
        self.narrative_context = NarrativeContextProvider()
        self.trench_shadow = TrenchRegimeShadow(
            enabled=getattr(config, "LATTICE_TRENCH_SHADOW_ENABLED", True),
            window_seconds=getattr(
                config,
                "LATTICE_TRENCH_SHADOW_WINDOW_SECONDS",
                3600.0,
            ),
            hot_score=getattr(config, "LATTICE_TRENCH_SHADOW_HOT_SCORE", 65.0),
            euphoria_score=getattr(
                config,
                "LATTICE_TRENCH_SHADOW_EUPHORIA_SCORE",
                85.0,
            ),
            cold_score=getattr(config, "LATTICE_TRENCH_SHADOW_COLD_SCORE", 25.0),
            hot_candidates_per_hour=getattr(
                config,
                "LATTICE_TRENCH_SHADOW_HOT_CANDIDATES_PER_HOUR",
                140.0,
            ),
            hot_alerts_per_hour=getattr(
                config,
                "LATTICE_TRENCH_SHADOW_HOT_ALERTS_PER_HOUR",
                24.0,
            ),
            hot_entries_per_hour=getattr(
                config,
                "LATTICE_TRENCH_SHADOW_HOT_ENTRIES_PER_HOUR",
                8.0,
            ),
            hot_open_upnl_usd=getattr(
                config,
                "LATTICE_TRENCH_SHADOW_HOT_OPEN_UPNL_USD",
                250.0,
            ),
            hot_pc5=getattr(config, "LATTICE_TRENCH_SHADOW_HOT_PC5", 12.0),
            hot_volume_5m=getattr(
                config,
                "LATTICE_TRENCH_SHADOW_HOT_VOLUME_5M",
                750.0,
            ),
            watch_terms=getattr(config, "LATTICE_TRENCH_WATCH_TERMS", ()),
            kol_terms=getattr(config, "LATTICE_TRENCH_KOL_TERMS", ()),
            trigger_terms=getattr(config, "LATTICE_TRENCH_TRIGGER_TERMS", ()),
            proximity_tokens=getattr(
                config,
                "LATTICE_TRENCH_PROXIMITY_TOKENS",
                5,
            ),
        )
        self.alert_cd = alert_cooldown_h * 3600
        self.alert_list_enabled = bool(
            getattr(config, "LATTICE_ALERT_LIST_ENABLED", True)
        )
        self.alert_list_interval_s = max(
            float(
                getattr(
                    config,
                    "LATTICE_ALERT_LIST_INTERVAL_SECONDS",
                    4 * 3600,
                )
                or 0.0
            ),
            0.0,
        )
        self.alert_list_max_items = max(
            int(getattr(config, "LATTICE_ALERT_LIST_MAX_ITEMS", 30) or 30),
            1,
        )
        self.entry_cd = entry_cooldown_h * 3600
        self.max_hold_s = max_hold_h * 3600 if max_hold_h else None
        self.poll_s = poll_s
        self.open_position_monitor_s = max(
            float(open_position_monitor_s or 0),
            0.0
        )
        self.paper = paper
        self.batch_cap = batch_cap
        self.participation = HeliusAlchemyParticipationProvider() if participation else None
        self.min_breadth = min_breadth
        self.n_gated = 0
        self.cash = BALANCE_SOL * SOL_USD
        self.sol_usd = SOL_USD
        self.open_pos = {}
        self.alert_until = {}     # token -> next allowed ENTRY SIGNAL ts
        self.entry_until = {}     # token -> next allowed paper re-entry ts
        self._st_cache = {}       # token -> (ts, solanatracker evidence) [TTL]
        self._score_window = deque(
            maxlen=int(getattr(config, "LATTICE_TIER_WINDOW_SIZE", 500) or 500)
        )                         # trailing capital-candidate scores -> percentile tiers
        self.last_seen = None
        self.last_alert_list_sent_at = 0.0
        self.n_signals = 0
        self.n_trades = 0
        self.realized = 0.0
        self.live_execution = LiveExecutionManager()
        self._position_manage_lock = asyncio.Lock()
        self.paper_api_quotes = _flag(
            "LATTICE_PAPER_API_QUOTE_MONITOR_ENABLED",
            True
        )
        self.paper_quote_sanity = _flag(
            "LATTICE_PAPER_QUOTE_SANITY_ENABLED",
            True
        )
        self.paper_quote_sanity_max_deviation = _env_float(
            "LATTICE_PAPER_QUOTE_SANITY_MAX_DEVIATION_PCT",
            0.75
        )
        # Throughput brakes. 0 disables an individual brake.
        self.max_open = int(_env_float("LATTICE_MAX_OPEN_POSITIONS", 6))
        self.live_max_open = int(
            getattr(config, "LATTICE_LIVE_MAX_OPEN_POSITIONS", 0) or 0
        )
        self.max_entries_per_hour = int(
            _env_float("LATTICE_MAX_ENTRIES_PER_HOUR", 0)
        )
        self.breaker_loss_usd = _env_float(
            "LATTICE_CIRCUIT_BREAKER_24H_LOSS_USD", 0.0
        )
        self.entry_times = []      # paper-entry timestamps, 1h rolling window
        self.recent_realized = []  # [event_ts, pnl_usd], 24h rolling window
        # Zone discipline (2026-06-11): an entry while a token's published
        # signal is still live (alert cooldown) must fill inside the zone that
        # was actually called — price breaking below the called zone means the
        # called thesis failed; the bot may only re-enter at a new level after
        # a NEW signal announces it. Keeps the channel auditable vs the book.
        self.zone_discipline = _flag("LATTICE_ZONE_DISCIPLINE_ENABLED", True)
        self.zone_tolerance = _env_float(
            "LATTICE_ZONE_DISCIPLINE_TOLERANCE_PCT", 0.03
        )
        self.alert_zone = {}       # token -> {lo, hi, at} of last SENT signal
        self.entry_decision_log_enabled = bool(
            getattr(config, "LATTICE_ENTRY_DECISION_LOG_ENABLED", True)
        )
        entry_decision_log_path = str(
            getattr(
                config,
                "LATTICE_ENTRY_DECISION_LOG_PATH",
                "discovery/entry_decisions.jsonl",
            )
            or ""
        ).strip()
        if not entry_decision_log_path:
            self.entry_decision_log_path = ENTRY_DECISIONS
        elif os.path.isabs(entry_decision_log_path):
            self.entry_decision_log_path = entry_decision_log_path
        else:
            self.entry_decision_log_path = os.path.join(
                ROOT,
                entry_decision_log_path,
            )
        self._load()

    # ---- state ----
    def _load(self):
        if not os.path.exists(STATE):
            return
        try:
            d = json.load(open(STATE))
            self.last_seen = d.get("last_seen")
            self.last_alert_list_sent_at = _f(
                d,
                "last_alert_list_sent_at",
                0.0,
            )
            self.cash = d.get("cash", self.cash)
            self.sol_usd = d.get("sol_usd", self.sol_usd)
            state_balance_sol = _f(d, "balance_sol", 1.0)
            if BALANCE_SOL > state_balance_sol:
                added_cash = (BALANCE_SOL - state_balance_sol) * self.sol_usd
                self.cash += added_cash
                print(
                    "paper wallet topped up "
                    f"{state_balance_sol:.2f}->{BALANCE_SOL:.2f} SOL "
                    f"(+${added_cash:.2f})"
                )
            self.alert_until = d.get("alert_until", {})
            self.entry_until = d.get("entry_until", {})
            self.n_signals = d.get("n_signals", 0)
            self.n_trades = d.get("n_trades", 0)
            self.n_gated = d.get("n_gated", 0)
            self.realized = d.get("realized", 0.0)
            self.entry_times = [float(t) for t in d.get("entry_times") or []]
            self.recent_realized = [
                [float(t), float(v)]
                for t, v in (d.get("recent_realized") or [])
            ]
            self.alert_zone = d.get("alert_zone") or {}
            # Older state may have alert_until values written with a shorter
            # default; extend from the last sent alert timestamp when present.
            if self.alert_cd > 0:
                for tok, zone in self.alert_zone.items():
                    alerted_at = _f(zone, "at", 0.0)
                    if alerted_at > 0:
                        self.alert_until[tok] = max(
                            _f(self.alert_until, tok, 0.0),
                            alerted_at + self.alert_cd,
                        )
            if "recent_realized" not in d:
                self._backfill_recent_realized()
            for tok, p in (d.get("open_pos") or {}).items():
                p["levels_done"] = set(p.get("levels_done", []))
                self.open_pos[tok] = p
                self._backfill_open_scale_realized(p)
        except Exception as e:
            print("live_state load failed (starting fresh):", e)

    def _backfill_recent_realized(self):
        """One-time continuity for the circuit breaker: rebuild the 24h
        realized window from the trade ledger when the state predates it."""
        try:
            cutoff = time.time() - 24 * 3600
            with open(LEDGER) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    rec = json.loads(line)
                    ts = float(rec.get("exit_ts") or 0)
                    if ts >= cutoff:
                        self.recent_realized.append(
                            [ts, float(rec.get("pnl_usd") or 0)]
                        )
        except (OSError, ValueError, json.JSONDecodeError):
            pass

    def _prune_recent_realized(self, ts):
        pruned = []
        for item in self.recent_realized or []:
            try:
                event_ts = float(item[0])
                pnl_usd = float(item[1])
            except (TypeError, ValueError, IndexError):
                continue
            if 0 <= ts - event_ts <= 24 * 3600:
                pruned.append([event_ts, pnl_usd])
        self.recent_realized = pruned

    def _book_realized_pnl(self, pos, ts, pnl_usd):
        try:
            pnl_usd = float(pnl_usd)
        except (TypeError, ValueError):
            return 0.0

        if abs(pnl_usd) < 1e-9:
            return 0.0

        self.realized += pnl_usd
        pos["booked_realized_pnl_usd"] = (
            _f(pos, "booked_realized_pnl_usd", 0.0) + pnl_usd
        )
        self.recent_realized.append([ts, pnl_usd])
        self._prune_recent_realized(ts)
        return pnl_usd

    def _scale_fill_realized_pnl(self, pos, qty, price):
        entry_price = _f(pos, "entry_price", 0.0)
        if entry_price <= 0:
            return 0.0
        return float(qty or 0.0) * (float(price or 0.0) - entry_price)

    def _backfill_open_scale_realized(self, pos):
        if "booked_realized_pnl_usd" in pos:
            return 0.0

        proceeds = _f(pos, "proceeds", 0.0)
        if proceeds <= 0:
            return 0.0

        entry_price = _f(pos, "entry_price", 0.0)
        remaining = _f(pos, "remaining", 0.0)
        cost_usd = _f(pos, "cost_usd", SIZE_USD)
        if entry_price <= 0 or cost_usd <= 0:
            return 0.0

        sold_cost_basis = max(cost_usd - remaining * entry_price, 0.0)
        realized_pnl = proceeds - sold_cost_basis
        if abs(realized_pnl) < 1e-9:
            pos["booked_realized_pnl_usd"] = 0.0
            return 0.0

        event_ts = (
            _f(pos, "last_feature_ts", 0.0)
            or _f(pos, "last_price_ts", 0.0)
            or float(self.last_seen or time.time())
        )
        booked = self._book_realized_pnl(pos, event_ts, realized_pnl)
        print(
            "backfilled open scale-out realized "
            f"{pos.get('symbol', '')} ${booked:+.2f}"
        )
        return booked

    def _write_json_atomic(self, path, payload):
        tmp = f"{path}.tmp"
        with open(tmp, "w") as f:
            json.dump(payload, f, default=str)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)

    def _save(self):
        op = {}
        for tok, p in self.open_pos.items():
            q = dict(p)
            q["levels_done"] = sorted(
                p.get("levels_done", set()),
                key=lambda item: str(item),
            )
            op[tok] = q
        self._write_json_atomic(
            STATE,
            {"last_seen": self.last_seen, "cash": self.cash, "alert_until": self.alert_until,
             "entry_until": self.entry_until, "open_pos": op, "n_signals": self.n_signals,
             "n_trades": self.n_trades, "n_gated": self.n_gated,
             "realized": round(self.realized, 4), "sol_usd": self.sol_usd,
             "entry_times": self.entry_times,
             "recent_realized": self.recent_realized,
             "last_alert_list_sent_at": self.last_alert_list_sent_at,
             "alert_zone": {
                 tok: z for tok, z in self.alert_zone.items()
                 if (self.last_seen or 0) - float(z.get("at", 0)) < 24 * 3600
             },
             "balance_sol": BALANCE_SOL}
        )

    def _heartbeat(self, status="ok", error=""):
        self._write_json_atomic(
            HEARTBEAT,
            {
                "time": time.time(),
                "pid": os.getpid(),
                "status": status,
                "error": str(error or "")[:240],
                "last_seen": self.last_seen,
                "open_positions": len(self.open_pos),
                "paper_max_open": self.max_open,
                "live_open_positions": self._live_open_summary().get(
                    "open_count", 0
                ),
                "live_max_open": self.live_max_open,
                "signals": self.n_signals,
                "trades": self.n_trades,
                "cash": round(self.cash, 4),
                "poll_s": self.poll_s,
                "open_position_monitor_s": self.open_position_monitor_s,
                "max_hold_h": (self.max_hold_s / 3600) if self.max_hold_s else None,
                "exit_tp_mode": getattr(config, "LATTICE_EXIT_TP_MODE", ""),
                "live_provider": self.live_execution.preferred_live_provider(),
                "live_enabled": self.live_execution_enabled(),
            }
        )

    def _entry_block_family(self, entry_status, *, entered=False, block_kind=""):
        if entered:
            return "entered"

        if block_kind:
            return str(block_kind)

        status = str(entry_status or "").lower()
        checks = (
            ("paper_disabled", ("paper trading disabled",)),
            ("cooldown", ("entry cooldown",)),
            ("zone", ("called zone",)),
            ("live_brake", ("max live open positions",)),
            ("brake", ("max open positions", "entry rate cap", "circuit breaker")),
            ("cash", ("insufficient paper cash",)),
            ("paper_buy", ("paper buy gate",)),
            ("security", ("gmgn security",)),
            ("kline", ("kline fade",)),
            ("bundle", ("gmgn bundle",)),
        )

        for family, needles in checks:
            if any(needle in status for needle in needles):
                return family

        return "unknown"

    def _log_entry_decision(
        self,
        row,
        alert,
        detail,
        ts,
        entry_status,
        *,
        entered=False,
        alert_due=False,
        alert_sent=False,
        block_kind="",
        block_reason="",
        paper_buy_block="",
        zone_block="",
        live_brake="",
        brake="",
        security_block="",
        kline_block="",
        bundle_block="",
        bundle_features=None,
        capital_veto="",
        st_bundle=None,
        scorecard=None,
        tier="",
        trench=None,
    ):
        if not self.entry_decision_log_enabled:
            return

        try:
            entry_zone = getattr(alert, "entry_zone", None) or (None, None)
            block_family = self._entry_block_family(
                entry_status,
                entered=entered,
                block_kind=block_kind,
            )
            rec = {
                "ts": ts,
                "token": getattr(alert, "token_address", None)
                or row.get("token_address")
                or row.get("address"),
                "symbol": getattr(alert, "symbol", None) or row.get("symbol"),
                "chain": row.get("chain_name") or "solana",
                "price": _f(row, "price"),
                "conviction": float(getattr(alert, "conviction", 0.0) or 0.0),
                "entry_zone_lo": entry_zone[0] if len(entry_zone) > 0 else None,
                "entry_zone_hi": entry_zone[1] if len(entry_zone) > 1 else None,
                "entry_status": entry_status,
                "entered": bool(entered),
                "alert_due": bool(alert_due),
                "alert_sent": bool(alert_sent),
                "block_family": block_family,
                "block_reason": block_reason or entry_status,
                "paper_buy_block": paper_buy_block,
                "zone_block": zone_block,
                "live_brake": live_brake,
                "brake": brake,
                "security_block": security_block,
                "kline_block": kline_block,
                "bundle_block": bundle_block,
                "capital_veto": capital_veto,
                "tier": tier,
                "scorecard": (scorecard.get("score") if isinstance(scorecard, dict) else scorecard),
                "scorecard_axes": (scorecard.get("axes") if isinstance(scorecard, dict) else None),
                "trench_shadow_enabled": bool((trench or {}).get("enabled")),
                "trench_regime": (trench or {}).get("regime", ""),
                "trench_heat_score": (trench or {}).get("score"),
                "trench_shadow_capital_allowed": bool(
                    (trench or {}).get("shadow_capital_allowed", False)
                ),
                "trench_components": (trench or {}).get("components"),
                "trench_candidate_rate_h": (trench or {}).get(
                    "candidate_rate_h"
                ),
                "trench_alert_rate_h": (trench or {}).get("alert_rate_h"),
                "trench_entry_rate_h": (trench or {}).get("entry_rate_h"),
                "trench_open_upnl_usd": (trench or {}).get("open_upnl_usd"),
                "trench_open_15x": (trench or {}).get("open_15x"),
                "trench_open_2x": (trench or {}).get("open_2x"),
                "trench_open_3x": (trench or {}).get("open_3x"),
                "trench_narrative_hits": (
                    ((trench or {}).get("narrative") or {}).get("hits")
                ),
                "trench_kol_hits": (
                    ((trench or {}).get("narrative") or {}).get("kol_hits")
                ),
                "trench_kol_proximity": bool(
                    ((trench or {}).get("narrative") or {}).get(
                        "proximity",
                        False,
                    )
                ),
                # SolanaTracker (capital lane; separate from GMGN bundle_* below)
                "st_status": (st_bundle or {}).get("status"),
                "st_risk_level": (st_bundle or {}).get("risk_level"),
                "st_current_bundle_pct": (st_bundle or {}).get("current_bundle_pct"),
                "st_sniper_pct": (st_bundle or {}).get("sniper_pct"),
                "st_insider_pct": (st_bundle or {}).get("insider_pct"),
                "st_dev_pct": (st_bundle or {}).get("dev_pct"),
                "st_top10_pct": (st_bundle or {}).get("top10_pct"),
                "bundle_checked": False,
                "bundle_error": "",
                "bundle_value_pct": None,
                "bundle_verdict": "",
                "bundle_effective_top_pct": None,
                "bundle_naive_top1_pct": None,
                "bundle_naive_top10_pct": None,
                "bundle_obfuscation_gap_pct": None,
                "bundle_largest_cluster_pct": None,
                "bundle_largest_fund_pct": None,
                "bundle_time_clusters": None,
                "bundle_bundler_tagged": None,
                "bundle_nonbuy_pct": None,
                "bundle_holders_seen": None,
                "bundle_pools_excluded": None,
                "bundle_buyers": None,
                "bundle_transfer_dev_holders": None,
                "bundle_top_cluster_wallets": None,
                "bundle_top_cluster_pct": None,
                "bundle_top_cluster_span_s": None,
                "bundle_top_cluster_similar_n": None,
                "bundle_top_fund_wallets": None,
                "bundle_top_fund_pct": None,
                "source": row.get("source"),
                "source_family": row.get("source_family"),
                "novelty_factor": row.get("novelty_factor"),
                "adjusted_score": row.get("adjusted_score"),
                "score": row.get("score"),
                "raw_score": row.get("raw_score"),
                "penalty": row.get("penalty"),
                "alert_route": row.get("alert_route"),
                "quality_tag": row.get("quality_tag"),
                "evidence_bucket": row.get("evidence_bucket"),
                "evidence_factor": row.get("evidence_factor"),
                "bad_evidence_penalty": row.get("bad_evidence_penalty"),
                "data_completeness_score": row.get(
                    "data_completeness_score"
                ),
                "data_missing": row.get("data_missing"),
                "lifecycle": row.get("lifecycle"),
                "risk_flags": row.get("risk_flags"),
                "fdv": _f(row, "fdv"),
                "liquidity": _f(row, "liquidity") or _f(row, "raw_liquidity"),
                "volume_5m": _f(row, "volume_5m"),
                "volume_1h": _f(row, "volume_1h"),
                "pressure": _f(row, "pressure"),
                "price_change_5m": _f(row, "price_change_5m"),
                "price_change_1h": _f(row, "price_change_1h"),
                "price_change_24h": _f(row, "price_change_24h"),
                "volume_liquidity_ratio": _f(
                    row,
                    "volume_liquidity_ratio"
                ),
                "buy_sell_ratio": _f(row, "buy_sell_ratio"),
                "h1_volume_liquidity_ratio": _f(
                    row,
                    "h1_volume_liquidity_ratio"
                ),
                "h1_buy_sell_ratio": _f(row, "h1_buy_sell_ratio"),
                "breadth": (detail or {}).get("breadth"),
                "concentration": (detail or {}).get("concentration"),
                "buyers_sig": (detail or {}).get("buyers_sig"),
            }
            if bundle_features:
                rec.update(bundle_features)
            os.makedirs(os.path.dirname(self.entry_decision_log_path), exist_ok=True)
            with open(self.entry_decision_log_path, "a") as f:
                f.write(json.dumps(rec, default=str) + "\n")
        except Exception as e:
            print("entry-decision-log error:", e)

    def _db(self):
        c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True); c.row_factory = sqlite3.Row
        return c

    def _latest_position_features(self, token):
        try:
            db = self._db()
            row = db.execute(
                "SELECT * FROM signal_snapshots WHERE token_address=? "
                "AND price>0 ORDER BY timestamp DESC LIMIT 1",
                (token,),
            ).fetchone()
            return dict(row) if row else None
        except Exception as exc:
            print(f"latest feature lookup failed for {token_short(token)}: {exc}")
            return None

    async def initialize_wallet_price(self):
        price, stats = await fetch_sol_usd_price()

        if price > 0:
            old_price = self.sol_usd
            self.sol_usd = price
            if not os.path.exists(STATE):
                self.cash = BALANCE_SOL * price
                print(
                    f"initialized Lattice wallet with live SOL/USD ${price:.2f}"
                )
            elif abs(old_price - price) >= 0.01:
                print(
                    f"refreshed live SOL/USD ${old_price:.2f} -> ${price:.2f}"
                )
        else:
            print(
                "live SOL/USD unavailable; using fallback "
                f"${self.sol_usd:.2f} ({stats.get('error', '')})"
            )

    async def _signal_intel_for_message(self, token):
        """Candidate intel for the ENTRY SIGNAL message: smart-money holders
        + twitter CA mentions. Reads what the eligible-stage enrichment
        already attached to the token's candidate_events row; anything still
        missing is fetched inline with a short timeout (the paper/live entry
        has ALREADY happened above — only the telegram message waits) and
        persisted. Returns a dict for LatticeNotifier.fmt_signal or None."""

        intel = {}
        try:
            con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
            row = con.execute(
                "SELECT gmgn_smart_money, gmgn_smart_share_pct, "
                "gmgn_smart_usd, gmgn_smart_profit_n, tw_mentions, "
                "tw_authors, tw_top_followers "
                "FROM candidate_events WHERE token_address = ? "
                "ORDER BY timestamp DESC LIMIT 1",
                (token,),
            ).fetchone()
            con.close()
            if row:
                intel = {
                    "smart_count": row[0],
                    "smart_share_pct": row[1],
                    "smart_usd": row[2],
                    "smart_profit_n": row[3],
                    "tw_mentions": row[4],
                    "tw_authors": row[5],
                    "tw_top_followers": row[6],
                }
        except Exception as e:
            print(f"signal intel read error: {e}")

        scanner_storage = None

        if intel.get("smart_count") is None:
            try:
                from sources.gmgn import gmgn_client
                from storage.sqlite import ScannerStorage

                if gmgn_client.enabled():
                    features = await asyncio.wait_for(
                        gmgn_client.candidate_features(token), 6
                    )
                    if features:
                        scanner_storage = scanner_storage or ScannerStorage()
                        await scanner_storage.update_candidate_gmgn(
                            token, features
                        )
                        intel.update({
                            "smart_count": features.get("smart_count"),
                            "smart_share_pct": features.get("smart_share_pct"),
                            "smart_usd": features.get("smart_usd"),
                            "smart_profit_n": features.get("smart_profit_n"),
                        })
            except asyncio.TimeoutError:
                pass
            except Exception as e:
                print(f"gmgn signal intel fetch error: {e}")

        if intel.get("tw_mentions") is None:
            try:
                from sources.opentwitter import opentwitter_client
                from storage.sqlite import ScannerStorage

                if opentwitter_client.enabled():
                    features = await asyncio.wait_for(
                        opentwitter_client.ca_mention_features(token), 6
                    )
                    if features:
                        scanner_storage = scanner_storage or ScannerStorage()
                        await scanner_storage.update_candidate_twitter(
                            token, features
                        )
                        intel.update({
                            "tw_mentions": features.get("mentions"),
                            "tw_authors": features.get("authors"),
                            "tw_top_followers": features.get("top_followers"),
                        })
            except asyncio.TimeoutError:
                pass
            except Exception as e:
                print(f"twitter signal intel fetch error: {e}")

        # OKX vibe (X/Twitter hotness, 0-100). Keep this independent of the
        # other enrichers so a GMGN/Twitter outage cannot hide OKX data.
        try:
            from sources.okx_vibe import okx_vibe_client

            if okx_vibe_client.enabled():
                vibe = await asyncio.wait_for(
                    okx_vibe_client.vibe_features(token, chain="sol"), 6
                )
                if vibe:
                    intel.update({
                        "vibe_score": vibe.get("score"),
                        "vibe_change_rate": vibe.get("score_change_rate"),
                    })
        except asyncio.TimeoutError:
            pass
        except Exception as e:
            print(f"okx vibe fetch error: {e}")

        # OKX signal (smart-money/KOL/whale buy flow for THIS token).
        # Best-effort; only attached when tracked wallets are buying it.
        try:
            from sources.okx_signal import okx_signal_client

            if okx_signal_client.enabled():
                sig = await asyncio.wait_for(
                    okx_signal_client.token_signals(token, chain="sol"), 6
                )
                if sig and sig.get("signals"):
                    intel["okx_signal"] = sig
        except asyncio.TimeoutError:
            pass
        except Exception as e:
            print(f"okx signal fetch error: {e}")

        return intel or None

    def _zone_discipline_block_reason(self, token, price, ts):
        """While the last SENT signal for this token is still live (inside the
        alert cooldown), an entry must fill inside the zone that signal
        published: price >= zone_lo*(1 - tolerance) and <= zone_hi. A price
        below the called zone means the called breakout failed — re-entry at
        the lower level is only allowed after the cooldown lapses and a NEW
        signal announces the new zone, so the channel always matches the book."""

        if not self.zone_discipline:
            return ""

        if ts >= self.alert_until.get(token, 0):
            return ""  # no live published signal -> next signal sets the zone

        zone = self.alert_zone.get(token)
        if not zone:
            return ""  # cooldown predates zone tracking

        lo = float(zone.get("lo") or 0)
        hi = float(zone.get("hi") or 0)
        if lo <= 0 or price <= 0:
            return ""

        floor = lo * (1.0 - max(self.zone_tolerance, 0.0))
        if price < floor:
            return (
                f"below called zone {price:.3g}<{floor:.3g} "
                f"(zone {lo:.3g}-{hi:.3g})"
            )

        if hi > 0 and price > hi:
            return (
                f"above called zone {price:.3g}>{hi:.3g} "
                f"(zone {lo:.3g}-{hi:.3g})"
            )

        return ""

    def _entry_brake_reason(self, ts, *, enforce_paper_open=True):
        """Throughput brakes on book exposure and trade velocity."""

        if (
            enforce_paper_open
            and self.max_open > 0
            and len(self.open_pos) >= self.max_open
        ):
            return (
                f"max open positions {len(self.open_pos)}/{self.max_open}"
            )

        if self.max_entries_per_hour > 0:
            self.entry_times = [
                t for t in self.entry_times if 0 <= ts - t <= 3600
            ]
            if len(self.entry_times) >= self.max_entries_per_hour:
                return (
                    f"entry rate cap {len(self.entry_times)}/"
                    f"{self.max_entries_per_hour} per hour"
                )

        if self.breaker_loss_usd > 0:
            self._prune_recent_realized(ts)
            rolling = sum(v for _, v in self.recent_realized)
            if rolling <= -self.breaker_loss_usd:
                return (
                    f"circuit breaker: 24h realized {rolling:+.2f} "
                    f"<= -{self.breaker_loss_usd:.0f}"
                )

        return ""

    def _recent_entry_count(self, ts):
        self.entry_times = [t for t in self.entry_times if 0 <= ts - t <= 3600]
        return len(self.entry_times)

    def _live_entry_brake_reason(self):
        """Live exposure cap, counted from actual submitted live entries."""

        if not self.live_execution_enabled() or self.live_max_open <= 0:
            return ""

        summary = self._live_open_summary()
        live_open = int(_f(summary, "open_count", 0))

        if live_open >= self.live_max_open:
            return f"max live open positions {live_open}/{self.live_max_open}"

        return ""

    async def _close_position(self, token, pos, ts, price):
        reconcile = await self._reconcile_live_orphan_balance(token, pos, ts, price)
        if self._live_close_still_exposed(pos, reconcile):
            await self._alert_live_close_pending(pos, reconcile, ts, price)
            return False

        pos["exit_ts"] = ts; pos["exit_price"] = price
        pos["pnl_usd"] = pos["proceeds"] - pos["cost_usd"]
        pos["peak_mult"] = pos["peak"] / pos["entry_price"]
        close_realized_delta = (
            pos["pnl_usd"] - _f(pos, "booked_realized_pnl_usd", 0.0)
        )
        self._book_realized_pnl(pos, ts, close_realized_delta)
        self.n_trades += 1
        self._log_trade(pos)
        del self.open_pos[token]
        self.entry_until[token] = ts + self.entry_cd
        await self.notifier.paper_exit(pos, self.cash)
        return True

    def _live_close_still_exposed(self, pos, reconcile):
        if not pos.get("live_execution_entry_submitted"):
            return False

        if pos.get("live_execution_closed"):
            return False

        if reconcile is None:
            return True

        if reconcile.get("wallet_raw_balance", 0) <= 0:
            return False

        return not bool(reconcile.get("submitted"))

    async def _alert_live_close_pending(self, pos, reconcile, ts, price):
        pos["live_exit_pending"] = True
        pos["live_exit_pending_since"] = pos.get("live_exit_pending_since") or ts
        reason = (
            (reconcile or {}).get("reason")
            or (reconcile or {}).get("error")
            or "wallet_balance_still_open"
        )
        pos["live_exit_pending_reason"] = str(reason)
        cooldown = float(
            getattr(
                config,
                "LATTICE_LIVE_EXIT_PENDING_ALERT_COOLDOWN_SECONDS",
                300
            )
            or 300
        )
        if ts < _f(pos, "live_exit_pending_alert_after", 0):
            return

        pos["live_exit_pending_alert_after"] = ts + cooldown
        mult = price / max(_f(pos, "entry_price"), 1e-18)
        await self.notifier.text(
            "<b>LIVE EXIT PENDING</b>\n"
            f"<b>${pos.get('symbol') or '?'}</b> paper close triggered "
            f"({pos.get('reason', 'close')}) but wallet exposure remains.\n"
            f"mark: {mult:.2f}x | reason: {reason}\n"
            f"<code>{token_short(pos.get('token', ''))}</code>"
        )

    async def _reconcile_live_orphan_balance(self, token, pos, ts, price):
        if not pos.get("live_execution_entry_attempted"):
            return None

        # Orphan-balance reconcile uses the SVM token-balance RPC. The EVM
        # equivalent (ERC-20 balanceOf) is not wired here yet, so
        # skip for non-Solana rather than call a Solana-only method.
        if str(pos.get("chain", "solana")).lower() != "solana":
            return None

        try:
            balance = await self.live_execution.solana_token_raw_balance(
                config.DEFINITIVE_FLASH_FUNDER_ADDRESS,
                token
            )
        except Exception as exc:
            print(
                "LIVE orphan balance check failed "
                f"{pos.get('symbol', '')}: {exc}"
            )
            return {
                "submitted": False,
                "wallet_check_failed": True,
                "reason": str(exc)
            }

        if not balance.get("ok", True):
            return {
                "submitted": False,
                "wallet_check_failed": True,
                "reason": balance.get("error", "wallet_balance_check_failed")
            }

        raw_balance = int(balance.get("raw_balance") or 0)

        if raw_balance <= 0:
            pos["live_execution_closed"] = True
            pos["live_exit_pending"] = False
            return {
                "submitted": True,
                "wallet_raw_balance": 0,
                "reason": "no_live_wallet_balance"
            }

        decimals = int(balance.get("decimals") or 0)
        qty = raw_balance / (10 ** decimals if decimals >= 0 else 1)
        event = self._position_event(
            "close",
            pos,
            ts,
            price,
            reason="orphan_live_balance_reconcile",
            live_execution_sell_tokens=qty,
            live_execution_remaining_tokens_estimated=qty
        )
        result = await self.live_execution.execute_position_event(
            event,
            open_summary=self._live_open_summary(),
            has_live_position=True
        )
        result["wallet_raw_balance"] = raw_balance
        result["wallet_token_balance"] = qty
        if result.get("submitted"):
            pos["live_execution_closed"] = True
            pos["live_exit_pending"] = False
        pos.setdefault("live_execution_orders", []).append({
            "type": "close",
            "kind": "orphan_live_balance_reconcile",
            "timestamp": ts,
            "submitted": bool(result.get("submitted")),
            "order_id": result.get("order_id", ""),
            "reason": result.get("reason", "")
        })
        print(
            "LIVE orphan close "
            f"{pos.get('symbol', '')} raw={raw_balance} "
            f"submitted={result.get('submitted')} "
            f"reason={result.get('reason', '')} "
            f"order={result.get('order_id', '')}"
        )
        return result

    def live_execution_enabled(self):
        return bool(self.live_execution.ordering_enabled())

    def _live_open_summary(self):
        open_count = 0
        open_exposure_usd = 0.0

        for pos in self.open_pos.values():
            if not pos.get("live_execution_entry_submitted"):
                continue

            open_count += 1
            open_exposure_usd += _f(
                pos,
                "live_execution_entry_notional_usd",
                _f(pos, "cost_usd", 0)
            )

        return {
            "open_count": open_count,
            "open_exposure_usd": open_exposure_usd
        }

    def _position_event(self, event_type, pos, ts, price, **extra):
        entry_price = _f(pos, "entry_price")
        event = {
            "type": event_type,
            "timestamp": ts,
            "address": pos.get("token", ""),
            "symbol": pos.get("symbol", ""),
            "chain": pos.get("chain", "solana"),
            "entry_price": entry_price,
            "last_price": price,
            "price_multiple": price / entry_price if entry_price > 0 else 0,
            "entry_notional_usd": _f(pos, "cost_usd", SIZE_USD),
            "entry_size_sol": _f(pos, "cost_usd", SIZE_USD) / max(self.sol_usd, 1e-18),
            "entry_sol_usd": self.sol_usd,
            "sol_usd": self.sol_usd,
            "contra_asset_usd": self.sol_usd,
            "reason": str(extra.get("reason", "")),
        }
        event.update(extra)
        return event

    def _initial_stop_pct(self, pos=None):
        if pos is not None:
            per_pos = pos.get("initial_stop_pct")
            if per_pos is not None:
                return float(per_pos)
        return float(
            getattr(
                config,
                "LATTICE_EXIT_INITIAL_STOP_PCT",
                getattr(config, "POSITION_INITIAL_STOP_LOSS_PCT", 0.30),
            )
            or 0.30
        )

    def _live_entry_fill_price_usd(self, pos):
        fill_price = _f(pos, "live_execution_entry_fill_price_usd", 0)
        if fill_price > 0:
            return fill_price

        filled_target = _f(pos, "live_execution_entry_filled_target_amount", 0)
        filled_contra = _f(pos, "live_execution_entry_filled_contra_amount", 0)
        if filled_target > 0 and filled_contra > 0:
            return filled_contra * max(self.sol_usd, 0) / filled_target

        return _f(pos, "entry_price", 0)

    def _live_onchain_stop_trigger_usd(self, pos):
        fill_price = self._live_entry_fill_price_usd(pos)
        entry_price = _f(pos, "entry_price", 0)
        floor_price = _f(pos, "stop_floor_price", 0)

        if fill_price <= 0:
            return 0.0

        if floor_price > 0 and entry_price > 0:
            return fill_price * (floor_price / entry_price)

        return fill_price * (1 - self._initial_stop_pct(pos))

    def _clear_live_onchain_stop_fields(self, pos):
        pos["live_execution_onchain_stop_order_id"] = ""
        pos["live_execution_onchain_stop_trigger_usd"] = 0
        pos["live_execution_onchain_stop_qty"] = 0

    async def _sync_live_onchain_stop(self, pos, event, result, ts):
        if str(result.get("provider", "")) != "flash":
            return None

        if not result.get("submitted"):
            return None

        if str(pos.get("chain", "solana")).lower() != "solana":
            return None

        if not self.live_execution.flash_onchain_stop_armed():
            return None

        event_type = event.get("type", "")
        existing_id = pos.get("live_execution_onchain_stop_order_id", "")
        remaining_tokens = _f(pos, "live_execution_remaining_tokens_estimated", 0)

        if event_type == "close" or pos.get("closed") or remaining_tokens <= 0:
            if existing_id:
                cancelled = await self.live_execution.cancel_flash_onchain_stop(
                    existing_id
                )
                if cancelled.get("ok"):
                    self._clear_live_onchain_stop_fields(pos)
                pos.setdefault("live_execution_orders", []).append({
                    "type": "onchain_stop_cancel",
                    "timestamp": ts,
                    "submitted": bool(cancelled.get("ok")),
                    "order_id": existing_id,
                    "reason": cancelled.get("reason", ""),
                })
            return None

        trigger_usd = self._live_onchain_stop_trigger_usd(pos)
        if trigger_usd <= 0:
            return None

        current_trigger = _f(pos, "live_execution_onchain_stop_trigger_usd", 0)
        current_qty = _f(pos, "live_execution_onchain_stop_qty", 0)
        ratchet_margin = max(
            float(
                getattr(
                    config,
                    "DEFINITIVE_FLASH_ONCHAIN_STOP_RATCHET_MIN_PCT",
                    0,
                )
                or 0
            ),
            0.0,
        )
        qty_changed = (
            current_qty <= 0
            or abs(remaining_tokens - current_qty) > current_qty * 0.01
        )
        trigger_ratcheted = (
            current_trigger <= 0
            or trigger_usd >= current_trigger * (1 + ratchet_margin)
        )

        if existing_id and not (qty_changed or trigger_ratcheted):
            return None

        new_trigger = max(trigger_usd, current_trigger)
        if existing_id:
            placed = await self.live_execution.reconcile_flash_onchain_stop(
                event,
                existing_order_id=existing_id,
                qty=remaining_tokens,
                trigger_usd=new_trigger,
            )
            order_type = "onchain_stop_replace"
        else:
            placed = await self.live_execution.place_flash_onchain_stop(
                event,
                qty=remaining_tokens,
                trigger_usd=new_trigger,
            )
            order_type = "onchain_stop_place"

        if placed.get("ok"):
            pos["live_execution_onchain_stop_order_id"] = placed.get("order_id", "")
            pos["live_execution_onchain_stop_trigger_usd"] = new_trigger
            pos["live_execution_onchain_stop_qty"] = remaining_tokens
        else:
            print(
                "FLASH ONCHAIN STOP sync skipped "
                f"{pos.get('symbol', '')} "
                f"reason={placed.get('reason', '')}"
            )

        pos.setdefault("live_execution_orders", []).append({
            "type": order_type,
            "timestamp": ts,
            "submitted": bool(placed.get("ok")),
            "order_id": placed.get("order_id", ""),
            "trigger_usd": new_trigger,
            "qty": remaining_tokens,
            "reason": placed.get("reason", ""),
        })
        return placed

    async def _retry_missing_live_onchain_stop(self, pos, ts, price):
        if not pos.get("live_execution_entry_submitted"):
            return None

        if pos.get("live_execution_onchain_stop_order_id"):
            return None

        if pos.get("closed") or pos.get("live_execution_closed"):
            return None

        if str(pos.get("live_execution_provider", "")) != "flash":
            return None

        if not self.live_execution.flash_onchain_stop_armed():
            return None

        cooldown = max(
            float(
                getattr(
                    config,
                    "DEFINITIVE_FLASH_ONCHAIN_STOP_RETRY_SECONDS",
                    30,
                )
                or 30
            ),
            1.0,
        )
        retry_after = _f(pos, "live_execution_onchain_stop_retry_after", 0)
        if ts < retry_after:
            return None

        pos["live_execution_onchain_stop_retry_after"] = ts + cooldown
        event = self._position_event(
            "entry",
            pos,
            ts,
            price,
            reason="missing_onchain_stop_retry",
        )
        return await self._sync_live_onchain_stop(
            pos,
            event,
            {"provider": "flash", "submitted": True},
            ts,
        )

    def _record_position_mark(self, pos, price, ts, source=""):
        pos["last_price"] = price
        pos["last_price_ts"] = ts

        if source:
            pos["last_price_source"] = source

    def _force_live_hard_stop_if_needed(self, pos, price, ts):
        if pos.get("closed") or _f(pos, "remaining", 0) <= 0:
            return []

        if not pos.get("live_execution_entry_submitted"):
            return []

        hard_stop_pct = float(
            getattr(config, "LATTICE_LIVE_HARD_STOP_LOSS_PCT", 0.60) or 0.60
        )
        entry_price = _f(pos, "entry_price")

        if entry_price <= 0 or price > entry_price * (1 - hard_stop_pct):
            return []

        qty = _f(pos, "remaining", 0)
        pos["remaining"] = 0.0
        pos["proceeds"] += qty * price
        pos["closed"] = True
        pos["reason"] = f"live_hard_stop_{hard_stop_pct:.0%}"
        return [(pos["reason"], qty, price)]

    def _entry_safety_block_reason(self, row):
        lifecycle = str(row.get("lifecycle") or "").lower()

        if lifecycle == "bonding_curve":
            return ""

        reason = str(row.get("reason") or "").lower()
        lock_reason = str(row.get("liquidity_lock_reason") or "").lower()
        lock_required = bool(_f(row, "liquidity_lock_required", 0))
        lock_locked = bool(_f(row, "liquidity_lock_locked", 0))

        if reason in {
            "liquidity_not_locked",
            "mobula_entry_precheck_failed",
        }:
            return reason

        if lock_required and not lock_locked:
            return lock_reason or "liquidity_lock_failed"

        if lock_reason in {
            "mobula_unlocked_liquidity",
            "coingecko_unlocked_liquidity",
            "lock_lookup_failed",
            "mobula_liquidity_unavailable",
            "mobula_api_key_missing",
        }:
            return lock_reason

        return ""

    def _paper_buy_block_reason(self, row, detail):
        if not bool(getattr(config, "LATTICE_PAPER_BUY_GATE_ENABLED", True)):
            return ""

        min_breadth = float(
            getattr(config, "LATTICE_PAPER_BUY_MIN_BREADTH", 0.35)
            or 0.0
        )
        min_pc5 = float(
            getattr(config, "LATTICE_PAPER_BUY_MIN_PRICE_CHANGE_5M", 4.0)
            or 0.0
        )
        max_pc5 = float(
            getattr(config, "LATTICE_PAPER_BUY_MAX_PRICE_CHANGE_5M", 20.0)
            or 0.0
        )

        br = (detail or {}).get("breadth")
        if br is None:
            return f"breadth_missing>={min_breadth:.2f}"

        br = float(br)
        if br < min_breadth:
            return f"breadth:{br:.2f}<{min_breadth:.2f}"

        pc5 = _f(row, "price_change_5m")
        if pc5 < min_pc5:
            return f"price_change_5m:{pc5:.1f}<{min_pc5:.1f}"

        if max_pc5 > 0 and pc5 > max_pc5:
            return f"price_change_5m:{pc5:.1f}>{max_pc5:.1f}"

        return ""

    async def _kline_fade_block_reason(self, token, chain="solana"):
        """GMGN OHLCV fade-filter (skill 3): block entries on a blow-off candle
        (large upper wick on the latest candle) or already rolling over from the
        window high. Only called once a candidate cleared every cheap gate, so
        GMGN kline calls stay bounded. Blind ('' = allow) on disable/timeout/
        error so GMGN being down can never block trading."""
        if not bool(getattr(
                config, "LATTICE_GMGN_KLINE_FADE_FILTER_ENABLED", False)):
            return ""
        try:
            from sources.gmgn import gmgn_client
            if not gmgn_client.enabled():
                return ""
            gchain = ("sol" if str(chain).lower() in ("solana", "sol")
                      else str(chain).lower())
            feats = await asyncio.wait_for(
                gmgn_client.kline_features(token, chain=gchain), 8.0)
        except Exception as e:
            print(f"GMGN kline fade-filter FAILED OPEN (allow) "
                  f"{token[:12]}: {type(e).__name__}")
            return ""
        if not feats:
            print(f"GMGN kline fade-filter FAILED OPEN (allow) "
                  f"{token[:12]}: no_data")
            return ""
        max_wick = float(getattr(
            config, "LATTICE_GMGN_KLINE_MAX_UPPER_WICK_RATIO", 0.5) or 0.0)
        wick = float(feats.get("kl_last_upper_wick_ratio") or 0.0)
        if max_wick > 0 and wick > max_wick:
            return f"blow_off_wick:{wick:.2f}>{max_wick:.2f}"
        max_dd = float(getattr(
            config, "LATTICE_GMGN_KLINE_MAX_DRAWDOWN_FROM_HIGH_PCT", -25.0)
            or 0.0)
        dd = feats.get("kl_drawdown_from_high_pct")
        if max_dd < 0 and dd is not None and float(dd) < max_dd:
            return f"fade_from_high:{float(dd):.0f}<{max_dd:.0f}"
        return ""

    async def _get_gmgn_liquidity_override(self, token, row):
        """Real GMGN liquidity for pre-migration bonding_curve tokens (skill 2)
        — DexScreener under-reports them. Sourced from `token info` (Token Basic
        Info: liquidity + fdv + holder/concentration context, cached and shared
        with the scan-time backfill), not the narrower `token pool`. Returns USD
        liquidity to use in place of the row estimate, or None. Gated to
        bonding_curve, best-effort (None on disable/timeout/error)."""
        if not bool(getattr(config, "GMGN_LIQUIDITY_OVERRIDE_ENABLED", False)):
            return None
        if str(row.get("lifecycle") or "").lower() != "bonding_curve":
            return None
        try:
            from sources.gmgn import gmgn_client
            if not gmgn_client.enabled():
                return None
            chain = str(row.get("chain_name") or "solana").lower()
            gchain = "sol" if chain in ("solana", "sol") else chain
            feats = await asyncio.wait_for(
                gmgn_client.token_info_features(token, chain=gchain), 6.0)
            if feats and feats.get("gmgn_liquidity_usd"):
                return float(feats["gmgn_liquidity_usd"])
        except Exception:
            return None
        return None

    async def _gmgn_security_block_reason(self, token, chain="solana"):
        """GMGN token-security veto (skill `token security`). Safe vetoes always
        on when the gate is enabled: honeypot / unsellable / blacklist / high
        sell-tax. Concentration (MAX_TOP10_RATE) and authority-renounced checks
        are opt-in (they can block many launchpad tokens). Default OFF; blind
        ('' = allow) on disable/timeout/error so GMGN issues never block trading."""
        if not bool(getattr(config, "GMGN_SECURITY_GATE_ENABLED", False)):
            return ""
        try:
            from sources.gmgn import gmgn_client
            if not gmgn_client.enabled():
                return ""
            gchain = ("sol" if str(chain).lower() in ("solana", "sol")
                      else str(chain).lower())
            s = await asyncio.wait_for(
                gmgn_client.security_features(token, chain=gchain), 8.0)
        except Exception as e:
            print(f"GMGN security gate FAILED OPEN (allow) "
                  f"{token[:12]}: {type(e).__name__}")
            return ""
        if not s:
            print(f"GMGN security gate FAILED OPEN (allow) "
                  f"{token[:12]}: no_data")
            return ""
        # Hard, always-safe vetoes (cannot block a legitimate token).
        if s.get("sec_is_honeypot") is True:
            return "honeypot"
        if s.get("sec_cannot_sell") is True:
            return "cannot_sell"
        if s.get("sec_is_blacklist") is True:
            return "blacklist"
        max_tax = float(getattr(config, "GMGN_SECURITY_MAX_SELL_TAX", 0.10) or 0.0)
        st = s.get("sec_sell_tax")
        if max_tax > 0 and st is not None and st > max_tax:
            return f"sell_tax:{st:.2f}>{max_tax:.2f}"
        # Opt-in vetoes (0/false = off — may block launchpad tokens).
        max_top10 = float(
            getattr(config, "GMGN_SECURITY_MAX_TOP10_RATE", 0.0) or 0.0)
        t10 = s.get("sec_top_10_holder_rate")
        if max_top10 > 0 and t10 is not None and t10 > max_top10:
            return f"top10:{t10:.2f}>{max_top10:.2f}"
        if bool(getattr(config, "GMGN_SECURITY_REQUIRE_RENOUNCED", False)):
            if s.get("sec_renounced_mint") is False:
                return "mint_not_renounced"
            if s.get("sec_renounced_freeze") is False:
                return "freeze_not_renounced"
        return ""

    def _empty_bundle_features(self, *, checked=False, error=""):
        return {
            "bundle_checked": bool(checked),
            "bundle_error": str(error or ""),
            "bundle_value_pct": None,
            "bundle_verdict": "",
            "bundle_effective_top_pct": None,
            "bundle_naive_top1_pct": None,
            "bundle_naive_top10_pct": None,
            "bundle_obfuscation_gap_pct": None,
            "bundle_largest_cluster_pct": None,
            "bundle_largest_fund_pct": None,
            "bundle_time_clusters": None,
            "bundle_bundler_tagged": None,
            "bundle_nonbuy_pct": None,
            "bundle_holders_seen": None,
            "bundle_pools_excluded": None,
            "bundle_buyers": None,
            "bundle_transfer_dev_holders": None,
            "bundle_top_cluster_wallets": None,
            "bundle_top_cluster_pct": None,
            "bundle_top_cluster_span_s": None,
            "bundle_top_cluster_similar_n": None,
            "bundle_top_fund_wallets": None,
            "bundle_top_fund_pct": None,
        }

    def _bundle_features_from_summary(self, summary):
        clusters = summary.get("clusters") or []
        funds = summary.get("funds") or []
        top_cluster = clusters[0] if clusters else {}
        top_fund = funds[0] if funds else {}
        effective_top = float(summary.get("effective_top") or 0.0)
        return {
            "bundle_checked": True,
            "bundle_error": "",
            "bundle_value_pct": effective_top,
            "bundle_verdict": str(summary.get("verdict") or ""),
            "bundle_effective_top_pct": effective_top,
            "bundle_naive_top1_pct": float(summary.get("naive_top1") or 0.0),
            "bundle_naive_top10_pct": float(summary.get("naive_top10") or 0.0),
            "bundle_obfuscation_gap_pct": float(
                summary.get("obfuscation_gap") or 0.0
            ),
            "bundle_largest_cluster_pct": float(
                summary.get("largest_cluster_pct") or 0.0
            ),
            "bundle_largest_fund_pct": float(
                summary.get("largest_fund_pct") or 0.0
            ),
            "bundle_time_clusters": int(summary.get("n_time_clusters") or 0),
            "bundle_bundler_tagged": int(summary.get("bundler_tagged") or 0),
            "bundle_nonbuy_pct": float(summary.get("nonbuy_pct") or 0.0),
            "bundle_holders_seen": int(summary.get("holders_seen") or 0),
            "bundle_pools_excluded": int(summary.get("pools_excluded") or 0),
            "bundle_buyers": int(summary.get("buyers") or 0),
            "bundle_transfer_dev_holders": int(
                summary.get("nonbuyers_n") or 0
            ),
            "bundle_top_cluster_wallets": int(top_cluster.get("n") or 0),
            "bundle_top_cluster_pct": float(
                top_cluster.get("combined_pct") or 0.0
            ),
            "bundle_top_cluster_span_s": float(top_cluster.get("span_s") or 0.0),
            "bundle_top_cluster_similar_n": int(
                top_cluster.get("similar_n") or 0
            ),
            "bundle_top_fund_wallets": int(top_fund.get("n") or 0),
            "bundle_top_fund_pct": float(top_fund.get("combined_pct") or 0.0),
        }

    async def _gmgn_bundle_features(
        self,
        token,
        chain="solana",
        *,
        required=False,
    ):
        if (
            not required
            and not bool(getattr(config, "GMGN_BUNDLE_ALERT_LOG_ENABLED", True))
        ):
            return self._empty_bundle_features(error="disabled")

        try:
            from sources.gmgn import gmgn_client
            from filters import bundle

            if not gmgn_client.enabled():
                return self._empty_bundle_features(error="gmgn_disabled")

            gchain = (
                "sol"
                if str(chain).lower() in ("solana", "sol")
                else str(chain).lower()
            )
            timeout_s = float(
                getattr(config, "GMGN_BUNDLE_TIMEOUT_SECONDS", 10.0) or 10.0
            )
            holders = await asyncio.wait_for(
                gmgn_client.top_holders(token, chain=gchain, limit=100),
                timeout_s,
            )
            if not holders:
                return self._empty_bundle_features(checked=True, error="no_data")

            summary = bundle.analyze(
                holders,
                window_s=float(
                    getattr(config, "GMGN_BUNDLE_WINDOW_S", 120.0) or 120.0
                ),
                min_cluster=int(
                    getattr(config, "GMGN_BUNDLE_MIN_CLUSTER", 3) or 3
                ),
                amount_tol=float(
                    getattr(config, "GMGN_BUNDLE_AMOUNT_TOL", 0.20) or 0.20
                ),
            )
            return self._bundle_features_from_summary(summary)
        except Exception as e:
            print(
                "GMGN bundle feature collection FAILED OPEN (allow) "
                f"{token[:12]}: {type(e).__name__}"
            )
            return self._empty_bundle_features(
                checked=True,
                error=type(e).__name__,
            )

    def _gmgn_bundle_block_reason_from_features(self, features):
        if not bool(getattr(config, "GMGN_BUNDLE_GATE_ENABLED", False)):
            return ""

        max_eff = float(
            getattr(config, "GMGN_BUNDLE_MAX_EFFECTIVE_PCT", 25.0) or 0.0
        )
        if max_eff <= 0:
            return ""

        eff = features.get("bundle_value_pct")
        if eff is None:
            return ""

        eff = float(eff or 0.0)
        if eff >= max_eff:
            return f"bundle_conc:{eff:.0f}%>={max_eff:.0f}%"

        return ""

    async def _gmgn_bundle_block_reason(self, token, chain="solana"):
        """Bundle/cluster veto (filters/bundle): block entries where split-wallet
        clustering reveals an effective single-operator concentration >= the
        configured threshold (de-obfuscated — defeats the naive top-10/breadth
        check when a whale splits across many fresh wallets). Default OFF; blind
        ('' = allow) on disable/timeout/error so GMGN issues never block trading."""
        if not bool(getattr(config, "GMGN_BUNDLE_GATE_ENABLED", False)):
            return ""
        features = await self._gmgn_bundle_features(
            token,
            chain=chain,
            required=True,
        )
        return self._gmgn_bundle_block_reason_from_features(features)

    async def _execute_live_entry(self, pos, ts, price):
        if not self.live_execution_enabled():
            return None

        if pos.get("live_execution_entry_attempted"):
            return None

        pos["live_execution_entry_attempted"] = True

        if not bool(getattr(config, "LATTICE_LIVE_ENTRIES_ENABLED", True)):
            pos["live_execution_provider"] = self.live_execution.preferred_live_provider()
            pos["live_execution_entry_order_submitted"] = False
            pos["live_execution_entry_submitted"] = False
            pos["live_execution_entry_order_id"] = ""
            pos["live_execution_entry_reason"] = "lattice_live_entries_disabled"
            pos["live_execution_entry_notional_usd"] = 0
            pos["live_execution_entry_filled_target_amount"] = 0
            pos["live_execution_entry_filled_contra_amount"] = 0
            pos["live_execution_remaining_tokens_estimated"] = 0
            print(
                "LIVE entry "
                f"{pos.get('symbol', '')} submitted=False "
                f"provider={pos['live_execution_provider']} "
                "reason=lattice_live_entries_disabled order="
            )
            return {
                "submitted": False,
                "provider": pos["live_execution_provider"],
                "reason": "lattice_live_entries_disabled"
            }

        if pos.get("live_execution_policy_enabled") is False:
            pos["live_execution_provider"] = self.live_execution.preferred_live_provider()
            pos["live_execution_entry_order_submitted"] = False
            pos["live_execution_entry_submitted"] = False
            pos["live_execution_entry_order_id"] = ""
            pos["live_execution_entry_reason"] = "trade_policy_paper_only"
            pos["live_execution_entry_notional_usd"] = 0
            pos["live_execution_entry_filled_target_amount"] = 0
            pos["live_execution_entry_filled_contra_amount"] = 0
            pos["live_execution_remaining_tokens_estimated"] = 0
            print(
                "LIVE entry "
                f"{pos.get('symbol', '')} submitted=False "
                f"provider={pos['live_execution_provider']} "
                "reason=trade_policy_paper_only order="
            )
            return {
                "submitted": False,
                "provider": pos["live_execution_provider"],
                "reason": "trade_policy_paper_only"
            }

        event = self._position_event("entry", pos, ts, price)
        result = await self.live_execution.execute_position_event(
            event,
            open_summary=self._live_open_summary(),
            has_live_position=True
        )
        live_notional_usd = _f(
            result,
            "entry_notional_usd",
            min(
                _f(pos, "cost_usd", SIZE_USD),
                _f(config.__dict__, "DEFINITIVE_MAX_ENTRY_NOTIONAL_USD", 0)
                or _f(pos, "cost_usd", SIZE_USD)
            )
        )
        filled_target = _f(
            result,
            "filled_target_amount",
            0
        )
        pos["live_execution_provider"] = result.get("provider", "")
        pos["live_execution_entry_order_submitted"] = bool(
            result.get("submitted")
        )
        pos["live_execution_entry_submitted"] = bool(
            result.get("submitted")
            and filled_target > 0
        )
        pos["live_execution_entry_order_id"] = result.get("order_id", "")
        pos["live_execution_entry_strategy_order_id"] = result.get(
            "strategy_order_id",
            ""
        )
        pos["live_execution_entry_condition_order_count"] = int(_f(
            result,
            "condition_order_count",
            0
        ))
        pos["live_execution_entry_reason"] = result.get("reason", "")
        pos["live_execution_entry_notional_usd"] = live_notional_usd
        pos["live_execution_entry_filled_target_amount"] = filled_target
        pos["live_execution_entry_filled_contra_amount"] = _f(
            result,
            "filled_contra_amount",
            0
        )
        if (
            pos["live_execution_entry_filled_target_amount"] > 0
            and pos["live_execution_entry_filled_contra_amount"] > 0
        ):
            pos["live_execution_entry_fill_price_usd"] = (
                pos["live_execution_entry_filled_contra_amount"]
                * max(self.sol_usd, 0)
                / pos["live_execution_entry_filled_target_amount"]
            )
        else:
            pos["live_execution_entry_fill_price_usd"] = price
        pos["live_execution_remaining_tokens_estimated"] = (
            pos["live_execution_entry_filled_target_amount"]
            or pos.get("remaining", 0)
        )
        await self._sync_live_onchain_stop(pos, event, result, ts)
        print(
            "LIVE entry "
            f"{pos.get('symbol', '')} submitted={result.get('submitted')} "
            f"provider={result.get('provider', '')} "
            f"reason={result.get('reason', '')} "
            f"order={result.get('order_id', '')}"
        )
        return result

    async def _execute_live_fill(self, pos, kind, qty, price, ts):
        if not self.live_execution_enabled():
            return None

        if not pos.get("live_execution_entry_submitted"):
            return None

        event_type = "scale_out" if _is_scale_fill(kind) else "close"
        initial_tokens = _f(pos, "cost_usd", SIZE_USD) / max(
            _f(pos, "entry_price"),
            1e-18
        )
        size_pct = qty / initial_tokens if initial_tokens > 0 else 0
        event = self._position_event(
            event_type,
            pos,
            ts,
            price,
            reason=kind,
            size_pct=size_pct,
            proceeds_usd=qty * price,
            live_execution_sell_tokens=qty,
            live_execution_remaining_tokens_estimated=_f(
                pos,
                "live_execution_remaining_tokens_estimated",
                0
            )
        )
        result = await self.live_execution.execute_position_event(
            event,
            open_summary=self._live_open_summary(),
            has_live_position=True
        )
        if result.get("submitted"):
            sold = _f(result, "filled_target_amount", 0) or qty
            pos["live_execution_remaining_tokens_estimated"] = max(
                _f(pos, "live_execution_remaining_tokens_estimated", 0) - sold,
                0
            )
            if event_type == "close":
                pos["live_execution_closed"] = True
            await self._sync_live_onchain_stop(pos, event, result, ts)

        pos.setdefault("live_execution_orders", []).append({
            "type": event_type,
            "kind": kind,
            "timestamp": ts,
            "submitted": bool(result.get("submitted")),
            "order_id": result.get("order_id", ""),
            "reason": result.get("reason", "")
        })
        print(
            "LIVE exit "
            f"{pos.get('symbol', '')} type={event_type} "
            f"submitted={result.get('submitted')} "
            f"provider={result.get('provider', '')} "
            f"reason={result.get('reason', '')} "
            f"order={result.get('order_id', '')}"
        )
        return result

    async def _apply_fills(self, pos, fills, ts):
        remaining_cursor = _f(pos, "remaining")
        for _, qty, _ in fills:
            try:
                remaining_cursor += float(qty)
            except (TypeError, ValueError):
                pass

        for kind, qty, p in fills:
            try:
                qty_f = float(qty)
            except (TypeError, ValueError):
                qty_f = 0.0

            remaining_cursor = max(0.0, remaining_cursor - qty_f)
            self.cash += qty * p
            await self._execute_live_fill(pos, kind, qty, p, ts)
            if _is_scale_fill(kind):
                scale_realized_pnl = self._book_realized_pnl(
                    pos,
                    ts,
                    self._scale_fill_realized_pnl(pos, qty_f, p),
                )
                entry_price = _f(pos, "entry_price")
                initial_qty = (
                    _f(pos, "cost_usd", SIZE_USD) / entry_price
                    if entry_price > 0
                    else 0.0
                )
                sold_cum = (
                    1.0 - remaining_cursor / initial_qty
                    if initial_qty > 0
                    else None
                )
                await self.notifier.paper_scale_out(
                    pos,
                    kind,
                    qty,
                    p,
                    self.cash,
                    sold_cum=sold_cum,
                    realized_pnl=scale_realized_pnl,
                )

    async def _flash_quote_position_price(self, token, pos):
        if not self.paper_api_quotes:
            return None

        # Flash quote_solana_exit_value is SVM-only. Non-Solana positions
        # fall back to the multi-chain DexScreener price path in the caller.
        if str(pos.get("chain", "solana")).lower() != "solana":
            return None

        qty = _f(pos, "remaining", 0)

        if qty <= 0:
            return None

        try:
            quote = await self.live_execution.quote_solana_exit_value(
                input_mint=token,
                amount_tokens=qty,
                output_price_usd=self.sol_usd,
                emergency=False
            )
        except Exception as exc:
            return {
                "error": str(exc)
            }

        if not quote.get("quote_available"):
            return {
                "error": str(quote.get("error", "definitive_quote_failed"))
            }

        to_notional = _f(quote, "quote_value_usd", 0)

        if qty <= 0 or to_notional <= 0:
            return {
                "error": "definitive_quote_missing_notional"
            }

        return {
            "price_usd": to_notional / qty,
            "source": quote.get("provider", "definitive_quote"),
            "to_notional_usd": to_notional,
            "to_amount": _f(quote, "output_amount", 0),
        }

    def _sanity_checked_paper_mark(self, token, live_price, features):
        price = _f(live_price, "price_usd")

        if price <= 0 or not self.paper_quote_sanity:
            return price

        source = str(live_price.get("source") or "")
        if "quote" not in source.lower():
            return price

        snapshot_price = _f(features or {}, "price")
        if snapshot_price <= 0:
            return price

        deviation = abs(price - snapshot_price) / max(snapshot_price, 1e-18)
        if deviation <= self.paper_quote_sanity_max_deviation:
            return price

        print(
            "paper quote sanity rejected "
            f"{token_short(token)} quote=${price:.12g} "
            f"snapshot=${snapshot_price:.12g} "
            f"deviation={deviation:.2%}"
        )
        return snapshot_price

    async def _quote_position_prices(self, addresses):
        stats = {
            "enabled": self.paper_api_quotes,
            "attempted": len(addresses),
            "refreshed": 0,
            "fallback_refreshed": 0,
            "missing": [],
            "error": "",
            "as_of": time.time(),
        }
        prices = {}

        for token in addresses:
            pos = self.open_pos.get(token)

            if not pos:
                continue

            mark = await self._flash_quote_position_price(token, pos)

            if mark and mark.get("price_usd", 0) > 0:
                prices[token] = mark
                stats["refreshed"] += 1
                continue

            if mark and mark.get("error") and not stats["error"]:
                stats["error"] = mark["error"]

        missing = [
            address
            for address in addresses
            if address not in prices
        ]

        if missing:
            fallback, fallback_stats = await fetch_live_prices(
                missing,
                chain_by_address={
                    address: (
                        self.open_pos.get(address, {}).get("chain")
                        or "solana"
                    )
                    for address in missing
                }
            )
            prices.update(fallback)
            stats["fallback_refreshed"] = fallback_stats.get(
                "refreshed",
                0
            )
            stats["missing"] = fallback_stats.get("missing", [])

            if fallback_stats.get("error") and not stats["error"]:
                stats["error"] = fallback_stats.get("error", "")

        return prices, stats

    async def manage_open_positions_live(self):
        if not self.open_pos:
            return {
                "attempted": 0,
                "refreshed": 0,
                "closed": 0,
                "fills": 0,
                "error": "",
            }

        addresses = list(self.open_pos)
        stats = {
            "attempted": len(addresses),
            "refreshed": 0,
            "closed": 0,
            "fills": 0,
            "error": "",
        }

        try:
            live_prices, refresh = await self._quote_position_prices(
                addresses
            )
        except Exception as e:
            stats["error"] = str(e)
            return stats

        stats["refreshed"] = refresh.get("refreshed", 0)
        stats["fallback_refreshed"] = refresh.get("fallback_refreshed", 0)
        stats["error"] = refresh.get("error", "")
        ts = refresh.get("as_of") or time.time()

        async with self._position_manage_lock:
            for token, live_price in live_prices.items():
                pos = self.open_pos.get(token)

                if pos is None:
                    continue

                features = self._latest_position_features(token)
                price = self._sanity_checked_paper_mark(
                    token,
                    live_price,
                    features
                )

                if price <= 0:
                    continue

                self._record_position_mark(
                    pos,
                    price,
                    ts,
                    live_price.get("source", "")
                )
                await self._retry_missing_live_onchain_stop(pos, ts, price)
                fills = self._force_live_hard_stop_if_needed(pos, price, ts)
                if not fills:
                    fills = manage_with_features(
                        pos,
                        price,
                        ts,
                        max_hold_s=self.max_hold_s,
                        features=features,
                    )

                await self._apply_fills(pos, fills, ts)

                if fills:
                    stats["fills"] += len(fills)

                if pos.get("closed"):
                    if await self._close_position(token, pos, ts, price):
                        stats["closed"] += 1

        return stats

    async def fast_mark_open_positions_live(self):
        if not self.open_pos:
            return {
                "attempted": 0,
                "refreshed": 0,
                "closed": 0,
                "fills": 0,
                "error": "",
            }

        addresses = list(self.open_pos)
        chain_by_address = {
            address: (
                self.open_pos.get(address, {}).get("chain")
                or "solana"
            )
            for address in addresses
        }
        stats = {
            "attempted": len(addresses),
            "refreshed": 0,
            "fallback_refreshed": 0,
            "closed": 0,
            "fills": 0,
            "error": "",
        }

        try:
            live_prices, refresh = await fetch_live_prices(
                addresses,
                chain_by_address=chain_by_address
            )
        except Exception as exc:
            stats["error"] = str(exc)
            return stats

        stats["refreshed"] = refresh.get("refreshed", 0)
        stats["fallback_refreshed"] = refresh.get("refreshed", 0)
        stats["error"] = refresh.get("error", "")
        ts = refresh.get("as_of") or time.time()

        async with self._position_manage_lock:
            for token, live_price in live_prices.items():
                pos = self.open_pos.get(token)

                if pos is None:
                    continue

                price = _f(live_price, "price_usd")

                if price <= 0:
                    continue

                self._record_position_mark(
                    pos,
                    price,
                    ts,
                    live_price.get("source", "")
                )
                await self._retry_missing_live_onchain_stop(pos, ts, price)
                features = self._latest_position_features(token)
                fills = self._force_live_hard_stop_if_needed(pos, price, ts)
                if not fills:
                    fills = manage_with_features(
                        pos,
                        price,
                        ts,
                        max_hold_s=self.max_hold_s,
                        features=features,
                    )

                await self._apply_fills(pos, fills, ts)

                if fills:
                    stats["fills"] += len(fills)

                if pos.get("closed"):
                    if await self._close_position(token, pos, ts, price):
                        stats["closed"] += 1

        return stats

    # ---- main tick ----
    async def tick(self):
        self._heartbeat("tick_start")
        db = self._db()
        pre_manage = await self.manage_open_positions_live()

        if self.last_seen is None:
            self.last_seen = db.execute("SELECT MAX(timestamp) m FROM signal_snapshots WHERE price>0").fetchone()["m"] or time.time()
            self._save()
            self._heartbeat("anchored")
            print(f"anchored to last_seen={self.last_seen:.0f}; watching for new snapshots…")
            return
        rows = db.execute(
            "SELECT * FROM signal_snapshots WHERE price>0 AND price_change_5m IS NOT NULL "
            "AND timestamp>? ORDER BY timestamp ASC LIMIT ?", (self.last_seen, self.batch_cap)).fetchall()
        max_ts = self.last_seen
        for r in rows:
            rd = dict(r)
            try:
                await self._process(rd, db)
            except Exception as e:
                print("row error:", e)
            ts = _f(rd, "timestamp")
            if ts > max_ts:
                max_ts = ts
        post_manage = await self.manage_open_positions_live()
        live_manage = {
            "attempted": (
                pre_manage.get("attempted", 0)
                + post_manage.get("attempted", 0)
            ),
            "refreshed": (
                pre_manage.get("refreshed", 0)
                + post_manage.get("refreshed", 0)
            ),
            "fallback_refreshed": (
                pre_manage.get("fallback_refreshed", 0)
                + post_manage.get("fallback_refreshed", 0)
            ),
            "fills": pre_manage.get("fills", 0) + post_manage.get("fills", 0),
            "closed": pre_manage.get("closed", 0) + post_manage.get("closed", 0),
            "error": pre_manage.get("error", "") or post_manage.get("error", ""),
        }
        self.last_seen = max_ts
        self._save()
        self._heartbeat("ok")
        if rows or live_manage.get("fills") or live_manage.get("closed"):
            print(f"[{time.strftime('%H:%M:%S')}] +{len(rows)} snaps | open {len(self.open_pos)} | "
                  f"quotes {live_manage.get('refreshed', 0)}/{live_manage.get('attempted', 0)} "
                  f"fallback {live_manage.get('fallback_refreshed', 0)} "
                  f"fills {live_manage.get('fills', 0)} closed {live_manage.get('closed', 0)} | "
                  f"signals {self.n_signals} | gated {self.n_gated} | trades {self.n_trades} | realized ${self.realized:.2f} | cash ${self.cash:.2f}")

    async def _process(self, row, db):
        token = row.get("token_address") or row.get("address") or ""
        price = _f(row, "price"); ts = _f(row, "timestamp")
        if not token or price <= 0:
            return
        # (a) manage an open paper position on its token's price update
        pos = self.open_pos.get(token)
        if pos is not None:
            async with self._position_manage_lock:
                pos = self.open_pos.get(token)

                if pos is None:
                    return

                self._record_position_mark(pos, price, ts, "snapshot")
                fills = self._force_live_hard_stop_if_needed(pos, price, ts)
                if not fills:
                    fills = manage_with_features(
                        pos,
                        price,
                        ts,
                        max_hold_s=self.max_hold_s,
                        features=row,
                    )
                await self._apply_fills(pos, fills, ts)
                if pos.get("closed"):
                    await self._close_position(token, pos, ts, price)
            return
        # (b) entry scan — cheap universe gate, then full pipeline
        if _f(row, "price_change_5m") <= 2.0 or _f(row, "volume_1h") <= 0:
            return
        alert, _reason = self.pipe.evaluate(row, now=ts)
        if alert is None:
            return
        # candidate-only participation breadth — only for conviction survivors,
        # in a thread so the slow Alchemy/Helius calls never stall the loop.
        detail = None
        if self.participation is not None:
            try:
                detail = await asyncio.to_thread(self.participation.breadth_detail, token)
            except Exception as e:
                print("breadth error:", e)
            if detail:
                br = detail.get("breadth")
                alert.participation_blind = br is None
                alert.evidence["breadth"] = br
                alert.evidence["concentration"] = detail.get("concentration")
                alert.evidence["buyers_sig"] = detail.get("buyers_sig")
        self._log_candidate(row, alert, detail)   # forward-collect for retraining
        alert_due_shadow = ts >= self.alert_until.get(token, 0)
        trench = self.trench_shadow.snapshot(
            row=row,
            alert=alert,
            detail=detail,
            ts=ts,
            alert_due=alert_due_shadow,
            entry_times=self.entry_times,
            open_pos=self.open_pos,
        )
        # final breadth gate: drop clearly manufactured moves (concentrated +
        # few distinct buyers). Blind (None) candidates are NOT gated. Demoted to
        # a soft scorecard term (buyers axis) once the scorecard is the selector,
        # since this hard floor showed near-zero separation (redesign C1).
        br = (detail or {}).get("breadth")
        if (not bool(getattr(config, "LATTICE_SCORECARD_ENABLED", False))
                and br is not None and br < self.min_breadth):
            block_reason = f"{float(br):.2f}<{self.min_breadth:.2f}"
            self._log_entry_decision(
                row,
                alert,
                detail,
                ts,
                f"not entered; breadth {block_reason}",
                alert_due=alert_due_shadow,
                block_kind="breadth",
                block_reason=block_reason,
                trench=trench,
            )
            self.n_gated += 1
            return
        safety_block = self._entry_safety_block_reason(row)
        if safety_block:
            self._log_entry_decision(
                row,
                alert,
                detail,
                ts,
                f"not entered; safety {safety_block}",
                alert_due=alert_due_shadow,
                block_kind="safety",
                block_reason=safety_block,
                trench=trench,
            )
            self.n_gated += 1
            print(
                "LATTICE entry safety blocked "
                f"{row.get('symbol', '')} "
                f"reason={safety_block}"
            )
            return
        should_alert = ts >= self.alert_until.get(token, 0)
        entry_status = "not entered yet"
        pos = None
        gchain = (row.get("chain_name") or "solana")
        bundle_features = (
            await self._gmgn_bundle_features(token, chain=gchain)
            if should_alert
            else self._empty_bundle_features()
        )
        paper_buy_block = self._paper_buy_block_reason(row, detail)

        live_brake = self._live_entry_brake_reason()
        brake = self._entry_brake_reason(
            ts,
            enforce_paper_open=not self.live_execution_enabled()
        )
        zone_block = self._zone_discipline_block_reason(token, price, ts)

        # Capital tier (Layer 2): scorecard -> percentile tier -> size. Computed
        # BEFORE the cash gate so a reduced-size Tier-B entry is not blocked by a
        # full-size cash check. Dormant (tier="" / full size) until the scorecard
        # is enabled, so this preserves current behavior by default.
        tier, size_usd, scorecard = self._capital_tier(row, detail, ts, alert)

        # GMGN security veto + kline fade-filter: only pay for the GMGN calls
        # when every cheap gate already passed (token is about to enter), so
        # calls stay bounded to actual would-be entries. Security is checked
        # first; kline is skipped if security already vetoes.
        security_block = ""
        kline_block = ""
        bundle_block = ""
        capital_veto = ""
        st_bundle = None
        if (self.paper and ts >= self.entry_until.get(token, 0)
                and not zone_block and not live_brake and not brake
                and size_usd > 0
                and self.cash >= size_usd and not paper_buy_block):
            security_block = await self._gmgn_security_block_reason(
                token, chain=gchain)
            if not security_block:
                kline_block = await self._kline_fade_block_reason(
                    token, chain=gchain)
            if (
                not security_block
                and not kline_block
                and bool(getattr(config, "GMGN_BUNDLE_GATE_ENABLED", False))
            ):
                if not bundle_features.get("bundle_checked"):
                    bundle_features = await self._gmgn_bundle_features(
                        token,
                        chain=gchain,
                        required=True,
                    )
                bundle_block = self._gmgn_bundle_block_reason_from_features(
                    bundle_features
                )
            # Capital-lane hard vetoes (Layer 1). The SolanaTracker fetch is
            # cached + made only when an ST-based veto is enabled, so the request
            # budget stays bounded to would-be entries (and the alert reuses it).
            if (not security_block and not kline_block and not bundle_block
                    and bool(getattr(config, "LATTICE_CAPITAL_VETO_ENABLED", True))):
                if (bool(getattr(config, "LATTICE_BUNDLE_REJECT_IF_BUNDLED", False))
                        or bool(getattr(config, "LATTICE_BUNDLE_REJECT_RISK_HIGH", True))
                        or bool(getattr(config, "LATTICE_BUNDLE_REJECT_IF_SNIPED", False))):
                    st_bundle = await self._st_bundle_features(token, row)
                capital_veto = self._capital_veto_reason(row, detail, st_bundle)

        block_kind = ""
        block_reason = ""

        if not self.paper:
            entry_status = "not entered; paper trading disabled"
        elif ts < self.entry_until.get(token, 0):
            entry_status = "not entered yet; entry cooldown"
        elif zone_block:
            entry_status = f"not entered; {zone_block}"
            self.n_gated += 1
        elif live_brake:
            entry_status = f"not entered; {live_brake}"
            self.n_gated += 1
        elif brake:
            entry_status = f"not entered; {brake}"
            self.n_gated += 1
        elif size_usd <= 0:
            entry_status = f"not entered; tier {tier or 'C'} no capital"
            block_kind = "tier_suppress"
            block_reason = f"tier_{tier or 'C'}"
            self.n_gated += 1
        elif self.cash < size_usd:
            entry_status = (
                f"not entered; insufficient paper cash "
                f"{self.cash:.2f}<{size_usd:.2f}"
            )
            self.n_gated += 1
        elif paper_buy_block:
            entry_status = f"not entered; paper buy gate {paper_buy_block}"
            block_kind = "paper_buy"
            block_reason = paper_buy_block
            self.n_gated += 1
        elif security_block:
            entry_status = f"not entered; gmgn security {security_block}"
            block_kind = "security"
            block_reason = security_block
            self.n_gated += 1
        elif kline_block:
            entry_status = f"not entered; kline fade {kline_block}"
            block_kind = "kline"
            block_reason = kline_block
            self.n_gated += 1
        elif bundle_block:
            entry_status = f"not entered; gmgn bundle {bundle_block}"
            block_kind = "bundle"
            block_reason = bundle_block
            self.n_gated += 1
        elif capital_veto:
            entry_status = f"not entered; capital veto {capital_veto}"
            block_kind = "capital_veto"
            block_reason = capital_veto
            self.n_gated += 1
        else:
            # size_usd is the tier-scaled size computed above (full SIZE_USD when
            # the scorecard is disabled or the tier is A/warmup).
            # GMGN liquidity override (skill 2): real pool liquidity for
            # bonding_curve tokens where DexScreener under-reports. Falls back
            # to the row estimate when disabled / not applicable.
            entry_liq = (
                await self._get_gmgn_liquidity_override(token, row)
                or _f(row, "liquidity") or _f(row, "raw_liquidity")
            )
            pos = {
                "token": token, "symbol": row.get("symbol", ""), "entry_ts": ts,
                "chain": (row.get("chain_name") or "solana"),
                "entry_price": price, "remaining": size_usd / price, "peak": price,
                "proceeds": 0.0, "scaled": False, "levels_done": set(),
                "cost_usd": size_usd, "conviction": alert.conviction,
                "tp_mode": getattr(config, "LATTICE_EXIT_TP_MODE", ""),
                "entry_tier": tier,
                "entry_fdv_usd": _f(row, "fdv"),
                "entry_liquidity": entry_liq,
                "peak_liquidity": entry_liq,
                "booked_realized_pnl_usd": 0.0,
                "recent": [],
            }
            self.open_pos[token] = pos
            self.cash -= size_usd
            self.entry_times.append(ts)
            try:
                await self._execute_live_entry(pos, ts, price)
            except Exception as e:
                pos["live_execution_entry_attempted"] = True
                pos["live_execution_entry_order_submitted"] = False
                pos["live_execution_entry_submitted"] = False
                pos["live_execution_entry_reason"] = f"live_entry_exception:{type(e).__name__}"
                print(f"live entry error for {pos.get('symbol', '')}: {e}")
            entry_status = (
                "entered legacy fixed-size; "
                f"{self.notifier.fmt_live_entry_status(pos)}"
            )

        self._log_entry_decision(
            row,
            alert,
            detail,
            ts,
            entry_status,
            entered=pos is not None,
            alert_due=should_alert,
            alert_sent=should_alert,
            block_kind=block_kind,
            block_reason=block_reason,
            paper_buy_block=paper_buy_block,
            zone_block=zone_block,
            live_brake=live_brake,
            brake=brake,
            security_block=security_block,
            kline_block=kline_block,
            bundle_block=bundle_block,
            bundle_features=bundle_features,
            capital_veto=capital_veto,
            st_bundle=st_bundle,
            scorecard=scorecard,
            tier=tier,
            trench=trench,
        )

        # token alert (deduped per token)
        if should_alert:
            self.alert_until[token] = ts + self.alert_cd
            # the zone this signal publishes is the one entries must honor
            # for the cooldown's duration (zone discipline)
            self.alert_zone[token] = {
                "lo": float(alert.entry_zone[0] or 0),
                "hi": float(alert.entry_zone[1] or 0),
                "at": ts,
            }
            self.n_signals += 1
            self._log_sent_alert(alert, row, price)
            try:
                alert.narrative_context = await self.narrative_context.build(
                    alert,
                    row=row
                )
            except Exception as e:
                alert.narrative_context = {
                    "enabled": True,
                    "checked": False,
                    "label": "check_failed",
                    "reason": type(e).__name__,
                }
                print(f"narrative context error for {alert.symbol}: {e}")
            intel = await self._signal_intel_for_message(token)
            bundle = await self._bundle_evidence_for_alert(alert, row)
            await self.notifier.signal(
                alert,
                entry_status=entry_status,
                intel=intel,
                bundle=bundle
            )

        if pos is not None:
            await self.notifier.paper_entry(pos, self.cash)

    def _log_trade(self, pos):
        """Append a closed paper trade to a JSONL ledger so the full trade
        history (symbol, PnL, exit reason) survives restarts and can be summarized
        from Telegram. Aggregate counters (realized/n_trades) are unaffected."""
        try:
            rec = {"exit_ts": pos.get("exit_ts"), "entry_ts": pos.get("entry_ts"),
                   "token": pos.get("token"), "symbol": pos.get("symbol"),
                   "conviction": pos.get("conviction"),
                   "entry_price": pos.get("entry_price"), "exit_price": pos.get("exit_price"),
                   "peak_mult": round(pos.get("peak_mult", 0.0), 4),
                   "reason": pos.get("reason"), "cost_usd": pos.get("cost_usd"),
                   "proceeds": round(pos.get("proceeds", 0.0), 6),
                   "pnl_usd": round(pos.get("pnl_usd", 0.0), 4),
                   "booked_realized_pnl_usd": round(
                       pos.get("booked_realized_pnl_usd", 0.0),
                       4,
                   ),
                   "initial_stop_pct": pos.get("initial_stop_pct"),
                   "initial_stop_basis": pos.get("initial_stop_basis"),
                   "q3_tp_mode": pos.get("q3_tp_mode"),
                   "q3_targets": pos.get("q3_targets"),
                   "q3_swing_low": pos.get("q3_swing_low"),
                   "q3_swing_high": pos.get("q3_swing_high"),
                   # Layer 3: entry tier travels to the ledger so exit quality
                   # (initial-stop rate, realized PnL, MFE) can be read per tier.
                   "entry_tier": pos.get("entry_tier")}
            with open(LEDGER, "a") as f:
                f.write(json.dumps(rec, default=str) + "\n")
        except Exception as e:
            print("trade-log error:", e)

    def _log_candidate(self, row, alert, detail):
        """Append every conviction-survivor (with breadth) to a JSONL log so the
        ranker can later be retrained on real participation data (forward-collect)."""
        try:
            rec = {"ts": alert.timestamp, "token": alert.token_address,
                   "symbol": alert.symbol, "conviction": alert.conviction,
                   "entry_price": alert.entry_zone[0],
                   "breadth": (detail or {}).get("breadth"),
                   "concentration": (detail or {}).get("concentration"),
                   "buyers_sig": (detail or {}).get("buyers_sig"),
                   "source": row.get("source"),
                   "source_family": row.get("source_family"),
                   "novelty_factor": row.get("novelty_factor"),
                   "adjusted_score": row.get("adjusted_score"),
                   "data_completeness_score": row.get(
                       "data_completeness_score"
                   ),
                   "evidence_bucket": row.get("evidence_bucket"),
                   "evidence_factor": row.get("evidence_factor"),
                   "bad_evidence_penalty": row.get("bad_evidence_penalty"),
                   "data_missing": row.get("data_missing"),
                   "row": row}
            with open(CANDIDATE_LOG, "a") as f:
                f.write(json.dumps(rec, default=str) + "\n")
        except Exception as e:
            print("candidate-log error:", e)

    def _log_sent_alert(self, alert, row, price):
        """Append the exact Telegram-sent alert so the late-moon monitor can
        track post-alert multiples precisely. entry_price falls back to the live
        price so the baseline is always positive; ts/token mirror participation_log
        so the late-moon monitor dedupes the two sources by (token, int(ts))."""
        try:
            zone_lo = alert.entry_zone[0] if alert.entry_zone else None
            rec = {
                "ts": alert.timestamp,
                "token": alert.token_address,
                "symbol": alert.symbol,
                "entry_price": zone_lo or price,
                "chain": (row.get("chain_name") or "solana"),
                "conviction": alert.conviction,
                "sent": True,
            }
            with open(SENT_ALERTS, "a") as f:
                f.write(json.dumps(rec, default=str) + "\n")
        except Exception as e:
            print("sent-alert-log error:", e)

    def _load_sent_alerts(self, since, until):
        rows = []
        seen = set()
        try:
            with open(SENT_ALERTS) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not rec.get("sent", True):
                        continue
                    ts = _f(rec, "ts", 0.0)
                    if ts <= 0 or ts < since or ts > until:
                        continue
                    token = str(rec.get("token") or "")
                    key = (token, int(ts))
                    if key in seen:
                        continue
                    seen.add(key)
                    rec["ts"] = ts
                    rows.append(rec)
        except FileNotFoundError:
            return []
        except Exception as e:
            print("sent-alert-list load error:", e)
            return rows

        rows.sort(key=lambda r: (_f(r, "ts", 0.0), str(r.get("token") or "")))
        return rows

    async def send_alert_list_digest(self, now=None):
        if not self.alert_list_enabled or self.alert_list_interval_s <= 0:
            return 0

        now = float(now or time.time())
        last_sent = float(self.last_alert_list_sent_at or 0.0)
        since = (
            max(last_sent, now - self.alert_list_interval_s)
            if last_sent > 0
            else now - self.alert_list_interval_s
        )
        alerts = self._load_sent_alerts(since, now)
        sent = await self.notifier.alert_list(
            alerts,
            since,
            now,
            max_items=self.alert_list_max_items,
        )
        self.last_alert_list_sent_at = now
        self._save()
        return sent

    async def alert_list_loop(self):
        if not self.alert_list_enabled or self.alert_list_interval_s <= 0:
            return

        while True:
            now = time.time()
            last_sent = float(self.last_alert_list_sent_at or 0.0)
            delay = self.alert_list_interval_s
            if last_sent > 0:
                delay = max(
                    1.0,
                    self.alert_list_interval_s - (now - last_sent),
                )

            await asyncio.sleep(delay)

            try:
                await self.send_alert_list_digest()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self._heartbeat("alert_list_error", e)
                print("alert list digest error:", e)

    async def _st_bundle_features(self, token, row):
        """Fetch + cache SolanaTracker risk/bundle evidence for a candidate.

        Cached per-token with a short TTL so the capital-lane vetoes (V1-V3) and
        the alert annotation reuse ONE request (the free tier is request-metered).
        Solana addresses only. Returns None when the provider is disabled or the
        chain is non-Solana (no evidence to act on); otherwise the normalized
        evidence dict, fail-open to status!="ok"/risk_level="unknown" on any
        error or timeout (a timeout is not evidence of a clean book)."""
        from sources import solanatracker as st
        if not st.enabled():
            return None
        chain = (row.get("chain_name") or "solana").lower()
        if chain not in ("solana", "sol", ""):
            return None
        now = time.time()
        ttl = float(getattr(config, "SOLANATRACKER_CACHE_TTL_S", 300.0) or 0.0)
        cached = self._st_cache.get(token)
        if cached and ttl > 0 and (now - cached[0]) <= ttl:
            return cached[1]
        try:
            ev = await asyncio.wait_for(
                st.fetch_risk(token),
                float(getattr(config, "SOLANATRACKER_ALERT_TIMEOUT_S", 7.0) or 7.0),
            )
        except Exception as e:                               # noqa: BLE001
            ev = {"provider": "solanatracker", "token": token,
                  "status": "error", "error": f"{type(e).__name__}: {e}",
                  "risk_level": "unknown", "observed_at": int(now)}
        self._st_cache[token] = (now, ev)
        return ev

    async def _bundle_evidence_for_alert(self, alert, row):
        """Single-provider (Solana Tracker) bundle/cluster label for an alert.

        Scanner-focused: this annotates the alert, it does NOT gate it. Reuses the
        cached pre-entry fetch (_st_bundle_features) so no second request is made
        for the same candidate. Every result is shadow-logged to
        bundle_evidence.jsonl keyed (token, int(alert_ts)) for later validation
        against discovery_outcomes."""
        ev = await self._st_bundle_features(alert.token_address, row)
        if ev is None:
            return None
        try:
            rec = dict(ev)
            rec["alert_ts"] = alert.timestamp
            rec["symbol"] = alert.symbol
            with open(BUNDLE_EVIDENCE, "a") as f:
                f.write(json.dumps(rec, default=str) + "\n")
        except Exception as e:
            print("bundle-evidence-log error:", e)
        return ev

    def _score_percentile(self, pct):
        vals = sorted(self._score_window)
        if not vals:
            return float("-inf")
        pct = max(0.0, min(100.0, pct))
        idx = int(pct / 100.0 * (len(vals) - 1))
        return vals[idx]

    def _capital_tier(self, row, detail, ts, alert, st_bundle=None):
        """Assign a capital tier (A/B/C) and the resulting paper size, replacing
        the conviction float as the SELECTOR. A = full size; B = reduced size or
        alert-only; C = no capital (still alertable). Returns (tier, size_usd,
        scorecard_dict). When the scorecard is disabled or the trailing score
        window has not warmed, returns legacy full-size with a sentinel tier so
        behavior is unchanged (Layer 2 is dormant until LATTICE_SCORECARD_ENABLED).
        """
        if not bool(getattr(config, "LATTICE_SCORECARD_ENABLED", False)):
            return "", SIZE_USD, None
        from discovery import scorecard as SC
        sc = SC.score(row, detail=detail, st_bundle=st_bundle, regime=None,
                      conviction=getattr(alert, "conviction", None))
        self._score_window.append(sc["score"])
        min_n = int(getattr(config, "LATTICE_TIER_WINDOW_MIN", 50) or 0)
        if len(self._score_window) < max(min_n, 1):
            return "warmup", SIZE_USD, sc
        a_cut = self._score_percentile(
            float(getattr(config, "LATTICE_TIER_A_PCT", 60.0))
        )
        b_cut = self._score_percentile(
            float(getattr(config, "LATTICE_TIER_B_PCT", 30.0)))
        floors_ok, _r = SC.passes_tier_a_floors(
            row, detail=detail, st_bundle=st_bundle)
        s = sc["score"]
        if s >= a_cut and floors_ok:
            return "A", SIZE_USD, sc
        if s >= b_cut:
            if bool(getattr(config, "LATTICE_TIER_B_ALERT_ONLY", False)):
                return "B", 0.0, sc
            frac = float(getattr(config, "LATTICE_TIER_B_SIZE_FRAC", 0.5))
            return "B", SIZE_USD * max(0.0, frac), sc
        return "C", 0.0, sc

    def _capital_veto_reason(self, row, detail, st_bundle):
        """Capital-lane hard vetoes (precision). Returns the first veto string or
        "". Applied to PAPER ENTRIES only — the alert lane is untouched. V1-V3 use
        SolanaTracker evidence and degrade-blind (never fire) when status != ok;
        V4-V5 use the scanner-lane risk_flags on the row; the refined wash/
        deep-fader vetoes use buyers_sig / pc1h, available only here. Each is
        knob-gated; the default-on set (V2, V4, V5, deep-fader) is the set that
        cleared discovery/redesign_validate.py's offline directional bar."""
        if not bool(getattr(config, "LATTICE_CAPITAL_VETO_ENABLED", True)):
            return ""

        # --- SolanaTracker actor vetoes (thin sample; V1/V3 default OFF) ---
        if st_bundle and st_bundle.get("status") == "ok":
            if (bool(getattr(config, "LATTICE_BUNDLE_REJECT_IF_BUNDLED", False))
                    and getattr(config, "LATTICE_BUNDLE_REJECT_BUNDLE_PCT", 0)):
                thr = float(config.LATTICE_BUNDLE_REJECT_BUNDLE_PCT)
                cur = _f(st_bundle, "current_bundle_pct", None)
                if cur is not None and cur >= thr:
                    return f"st_bundle:{cur:.0f}%>={thr:.0f}%"
            if bool(getattr(config, "LATTICE_BUNDLE_REJECT_RISK_HIGH", True)):
                want = str(getattr(
                    config, "LATTICE_BUNDLE_REJECT_RISK_LEVEL", "high")).lower()
                if str(st_bundle.get("risk_level", "")).lower() == want:
                    return f"st_risk:{want}"
            if bool(getattr(config, "LATTICE_BUNDLE_REJECT_IF_SNIPED", False)):
                sn = _f(st_bundle, "sniper_pct", None)
                if sn is not None and sn > 0:
                    return f"st_sniped:{sn:.0f}%"

        # --- scanner-lane risk-flag vetoes (full population; V4/V5 default ON) ---
        rf = row.get("risk_flags")
        if isinstance(rf, str):
            try:
                rf = json.loads(rf)
            except (ValueError, TypeError):
                rf = []
        rf = rf or []
        if bool(getattr(config, "LATTICE_VETO_FLAG_STACK", True)):
            max_rf = int(getattr(config, "LATTICE_MAX_RISK_FLAGS", 4) or 0)
            if max_rf and len(rf) >= max_rf:
                return f"flag_stack:{len(rf)}>={max_rf}"
        if bool(getattr(config, "LATTICE_VETO_SELL_PRESSURE", True)):
            if "sell_pressure" in rf:
                return "sell_pressure"

        # --- refined structural vetoes (capital lane: buyers_sig / pc1h) ---
        if bool(getattr(config, "LATTICE_VETO_WASH_BUYERS_SIG", False)):
            bs = (detail or {}).get("buyers_sig")
            floor = float(getattr(config, "LATTICE_VETO_BUYERS_SIG_MIN", -0.3))
            if bs is not None:
                try:
                    if float(bs) < floor:
                        return f"wash_buyers_sig:{float(bs):.2f}<{floor:.2f}"
                except (ValueError, TypeError):
                    pass
        if bool(getattr(config, "LATTICE_VETO_DEEP_FADER", True)):
            pc1h = _f(row, "price_change_1h", None)
            lo = float(getattr(config, "LATTICE_DEEP_FADER_LO", -40.0))
            hi = float(getattr(config, "LATTICE_DEEP_FADER_HI", -15.0))
            if pc1h is not None and lo <= pc1h < hi:
                return f"deep_fader:{pc1h:.0f}"

        return ""

    async def run_forever(self):
        mode = "DRY-RUN (no send)" if self.notifier.dry or not self.notifier.enabled else "LIVE → Telegram"
        await self.initialize_wallet_price()
        self._heartbeat("starting")
        print(f"discovery live runner — {mode} | paper={self.paper} | "
              f"wallet ${BALANCE_SOL*self.sol_usd:.0f} size ${SIZE_USD:.0f} | "
              f"live_provider={self.live_execution.preferred_live_provider()} | "
              f"live_enabled={self.live_execution_enabled()} | "
              f"paper_open_cap={self.max_open} | "
              f"live_open_cap={self.live_max_open} | "
              f"paper_quotes={self.paper_api_quotes} | "
              f"position_monitor={self.open_position_monitor_s:g}s | "
              f"alert_list={self.alert_list_interval_s:g}s | "
              f"max_hold={self.max_hold_s / 3600 if self.max_hold_s else 0:g}h | "
              f"poll {self.poll_s}s")
        await self.notifier.text(f"discovery live runner started ({mode}, paper={self.paper})")
        monitor_task = None
        alert_list_task = None

        if self.open_position_monitor_s > 0:
            monitor_task = asyncio.create_task(
                self.open_position_monitor_loop()
            )

        if self.alert_list_enabled and self.alert_list_interval_s > 0:
            alert_list_task = asyncio.create_task(
                self.alert_list_loop()
            )

        try:
            while True:
                try:
                    await self.tick()
                except Exception as e:
                    self._heartbeat("tick_error", e)
                    print("tick error:", e)
                await asyncio.sleep(self.poll_s)
        finally:
            if monitor_task is not None:
                monitor_task.cancel()
            if alert_list_task is not None:
                alert_list_task.cancel()

    async def open_position_monitor_loop(self):
        while True:
            await asyncio.sleep(self.open_position_monitor_s)

            if not self.open_pos:
                continue

            try:
                stats = await self.fast_mark_open_positions_live()

                if stats.get("attempted", 0):
                    self._save()
                    self._heartbeat("position_monitor")

                if (
                    stats.get("fills", 0)
                    or stats.get("closed", 0)
                    or stats.get("error")
                ):
                    print(
                        f"[{time.strftime('%H:%M:%S')}] position monitor | "
                        f"open {len(self.open_pos)} | "
                        f"quotes {stats.get('refreshed', 0)}/"
                        f"{stats.get('attempted', 0)} "
                        f"fallback {stats.get('fallback_refreshed', 0)} "
                        f"fills {stats.get('fills', 0)} "
                        f"closed {stats.get('closed', 0)} "
                        f"error {stats.get('error', '')}"
                    )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self._heartbeat("position_monitor_error", e)
                print("position monitor error:", e)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-conviction", type=float, default=None,
                    help="conviction floor; default derives from the deployed "
                         "model's recommended_min_conviction")
    ap.add_argument(
        "--alert-cooldown-h",
        type=float,
        default=DEFAULT_ALERT_COOLDOWN_H,
    )
    ap.add_argument("--entry-cooldown-h", type=float, default=6.0)
    ap.add_argument("--max-hold-h", type=float, default=DEFAULT_MAX_HOLD_H)
    ap.add_argument("--poll-s", type=float, default=10)
    ap.add_argument("--no-paper", action="store_true")
    ap.add_argument("--no-participation", action="store_true", help="disable live breadth (run blind)")
    ap.add_argument("--min-breadth", type=float, default=-0.4, help="drop alerts with breadth below this")
    ap.add_argument("--min-lattice", type=float,
                    default=getattr(config, "LATTICE_MIN_ENTRY_LATTICE", 0.0),
                    help="drop entries whose lattice composite is below this (0=off)")
    ap.add_argument("--max-price-change-1h", type=float,
                    default=getattr(config, "LATTICE_MAX_ENTRY_PRICE_CHANGE_1H", 0.0),
                    help="reject entries already pumped past this 1h percent (0=off)")
    ap.add_argument("--max-price-change-24h", type=float,
                    default=getattr(config, "LATTICE_MAX_ENTRY_PRICE_CHANGE_24H", 0.0),
                    help="reject entries already pumped past this 24h percent (0=off)")
    ap.add_argument("--open-position-monitor-s", type=float,
                    default=DEFAULT_OPEN_POSITION_MONITOR_S,
                    help="refresh open position marks independently of scanner ticks (0=off)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--once", action="store_true", help="run a single tick and exit")
    ap.add_argument("--post-methodology", action="store_true", help="post the methodology message and exit")
    args = ap.parse_args()
    r = LiveRunner(min_conviction=args.min_conviction, alert_cooldown_h=args.alert_cooldown_h,
                   entry_cooldown_h=args.entry_cooldown_h, max_hold_h=args.max_hold_h,
                   poll_s=args.poll_s, paper=not args.no_paper,
                   participation=not args.no_participation, min_breadth=args.min_breadth,
                   min_lattice=args.min_lattice,
                   max_price_change_1h=args.max_price_change_1h,
                   max_price_change_24h=args.max_price_change_24h,
                   open_position_monitor_s=args.open_position_monitor_s,
                   dry_run=True if args.dry_run else None)
    if args.post_methodology:
        asyncio.run(r.notifier.methodology(poll_s=args.poll_s, min_conviction=r.min_conviction,
                                           max_hold_h=args.max_hold_h, paper=not args.no_paper))
    elif args.once:
        async def run_once():
            await r.initialize_wallet_price()
            await r.tick()

        asyncio.run(run_once())
    else:
        asyncio.run(r.run_forever())
