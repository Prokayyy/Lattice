# `discovery/` — lattice-first conviction scanner (clean-slate rebuild)

A from-scratch redesign of the scan path. The old scanner collapsed three
different questions — *is this token interesting?*, *is the move real?*, and
*is now a good tick to buy?* — into one live gate cascade, so jitter on any
single gate dropped otherwise-good trades. This package separates them into a
three-stage pipeline whose output is a **windowed entry plan**, not a one-tick
pass/fail.

> **Status: live Lattice alert and paper-runner layer.** The pipeline runs
> end-to-end on `scanner.db`, emits structured ENTRY SIGNAL alerts, and
> `live_runner.py` can simulate paper entries from those alerts. Q3 is now the
> primary take-profit path in `manager.py`: fib-extension targets are snapped to
> volume-profile nodes, and the ladder supplies cumulative sell fractions.

---

## Architecture

```
signal_snapshots row (live or replayed)
        │
        ▼
Stage 1  UNIVERSE        cheap re-acceleration filter (pc5 > 2%, has volume)
        │                wide net — "worth scoring at all?"
        ▼
Stage 2  CONVICTION
   2a  lattice vetoes  anti-fakeness sanity gates (lattice.py)
   2b  revival shape     token-relative trajectory: base→dormancy→reawakening
   2c  calibrated rank   P(≥2x) from a logistic ranker (ranker.py)
        │
        ▼
Stage 3  TIMING          entry zone + invalidation price -> EntryAlert
```

Files:

| file | role |
|------|------|
| `features.py`   | 14 numeric features incl. an inverted-U momentum term (`price_change_5m_sq`) and a `participation_breadth` slot |
| `ranker.py`     | `ConvictionRanker` — pure-Python L2 logistic regression (fit / proba / importance / save / load JSON). No numpy/sklearn (not in venv). `roc_auc` via Mann–Whitney. |
| `lattice.py`  | multi-axis lattice score (flow / liquidity / structure / **participation**) + hard anti-fakeness vetoes. Defines `ParticipationProvider` ABC. |
| `revival.py`    | `revival_shape()` — token-relative volume-z, drawdown-from-peak, reawakening flag from `token_candles`. |
| `pipeline.py`   | `ConvictionPipeline.evaluate(row)` → `EntryAlert` or `(None, reason)`. |
| `train_ranker.py` | builds the labelled dataset from `alert_outcomes` ⨝ `signal_snapshots`, runs 5-fold OOS CV, compares to the existing `score` baseline, saves the model. |
| `models/conviction_ranker.json` | the trained model (weights, standardization mean/std, feature names). |

## Design principles (the "forget the labels" rebuild)

1. **Lattice = corroboration across independent axes, not one metric.** Flow,
   liquidity behaviour, price structure, and participation breadth must *agree*.
   Flow/price alone is the most gameable signal.
2. **Token-relative normalization, not global $ floors.** Revival is a
   *trajectory shape* (a base, a dormant stretch, a reawakening with volume
   expanding relative to *that token's own* recent history) — not an absolute
   liquidity/volume threshold that biases toward large caps.
3. **Calibrated ranking, not pass/fail gates.** Stage 2c emits P(≥2x) so alerts
   can be ordered and thresholded, instead of a brittle boolean cascade.
4. **Windowed alerts.** An alert carries an `entry_zone` (price band) and an
   `invalidation_price`, so the executor fills anywhere in the band while
   liveness holds — it does **not** re-run the whole stack on every tick. This
   directly attacks the "good alert dropped by one-tick jitter" failure.

---

## THE HEADLINE FINDING (why this is honest, not hype)

We trained the conviction ranker on real outcomes and evaluated it strictly
out-of-sample (5-fold CV), against the existing `score` the live gates already
use, on the actual labelled population:

```
usable rows = 349   |  ≥2x positives = 48 (13.8% base rate)   window = 1h

Out-of-sample AUC (5-fold):
  conviction ranker : 0.568
  existing `score`  : 0.586     <- the baseline already in production

Top-decile hit-rate (base rate 13.8%):
  ranker  14.7%  (lift 1.07x)
  score   20.6%  (lift 1.50x)   <- the existing score is the BETTER ranker

Feature importance:  participation_breadth  weight = +0.000
```

**Read this plainly: a fresh model on the currently-available features does not
beat the existing `score` — the `score` is actually better.** That is not a
failure of the model; it is evidence of a **feature ceiling**. The features in
`signal_snapshots` (price change, volume, buy/sell ratios, VLR) have already had
most of their separable signal extracted by the existing `score`. Re-modeling
the same inputs cannot conjure information that isn't there.

`participation_breadth` carrying **exactly 0.000 weight** is the tell: the column
is null in the data, so the one axis the design says is decisive is the one we
*cannot currently measure*.

### So what is the actual unlock?

**New data, not a better model.** The largest available gain is wiring a real
`ParticipationProvider` (Stage 2a) — unique buyers, new-holder rate, top-N
wallet concentration from an on-chain indexer (Helius / Birdeye / etc.). This is
the axis that distinguishes a *broad organic revival* from a *few-wallet
manufactured pump*, and it is exactly the dimension that flow/price features are
blind to. Secondary candidates: real `liquidity_change_pct` trend, holder-count
velocity, and time-since-dormancy from richer candle history.

The scaffold is built so that adding this is a *drop-in*, not a rewrite: the
`participation` slot exists in the feature vector, the lattice axis and the
`flow_without_breadth_wash` veto already consume it, and the pipeline degrades
gracefully (`participation_blind=True`) until a provider is supplied.

---

## What is REAL vs STAGED

**Real (runs today, on live `scanner.db`):**
- All five modules import, parse, and run end-to-end.
- The ranker is genuinely trained + OOS-evaluated on real outcomes (numbers above
  reproduce via `train_ranker`).
- Lattice vetoes, revival shape, universe filter, and windowed `EntryAlert`
  output are live and unit-correct (see calibration note below).
- End-to-end smoke test over 4,000 recent snapshots: 0 spurious vetoes,
  ~2–3 alerts at the default threshold (appropriately selective).
- `live_runner.py` consumes new snapshots, sends ENTRY SIGNAL alerts, opens
  simulated paper positions when entry gates allow, and writes
  `discovery/trades.jsonl`.
- `live_runner.py` also writes `discovery/entry_decisions.jsonl` when
  `LATTICE_ENTRY_DECISION_LOG_ENABLED=true`, so source families and entry
  blockers can be attributed after the fact. Sent alerts also attempt passive
  bundle telemetry when `GMGN_BUNDLE_ALERT_LOG_ENABLED=true`; the logged
  `bundle_value_pct` is the de-obfuscated effective top-holder percentage used
  for later threshold testing.
- `manager.py` owns the current exit engine: ATR-scaled initial stops, Q3 fib
  targets, 50/95 cumulative scale-outs, BE-style post-scale floor, moonbag step
  floors, and max-hold partial-runner grace.

**Staged / interface-only (clearly marked, not faked):**
- `liquidity_change_pct` is consumed if present in the row but is not yet
  independently sourced.
- Breadth/participation is forward-collected and used by live candidate checks,
  but the older ranker result above still describes the pre-breadth historical
  training set. Retraining should wait until enough forward breadth rows exist.

## Calibration note (units matter)

`signal_snapshots.price_change_5m` is stored as a **percent** (observed range
≈ −59 … +95, p99 ≈ 11), and `volume_liquidity_ratio` is small (p99 ≈ 0.42,
max ≈ 5.9). The lattice thresholds are set in those real units:
- momentum sweet spot centred ~15%, blow-off veto at pc5 > 150% (the live
  system's most permissive early-revival entry cap);
- thin-book VLR veto at > 4.0 (a true outlier; the old 8.0 never fired).

A first build that assumed *fractions* vetoed 337/400 snapshots as "parabolic";
correcting the units dropped spurious vetoes to 0. Keep this in mind before
copying any threshold.

---

## How to run

```bash
# from lattice-scanner/  (venv has no numpy/sklearn; pure-python by design)
env/bin/python -m discovery.train_ranker        # rebuild + OOS-evaluate the model

# score live/replayed snapshots:
env/bin/python - <<'PY'
import sqlite3
from discovery.pipeline import ConvictionPipeline
db = sqlite3.connect("file:scanner.db?mode=ro", uri=True); db.row_factory = sqlite3.Row
pipe = ConvictionPipeline(min_conviction=0.18)   # P(>=2x); calibrate to your alert budget (output spans ~0–0.6)
for r in db.execute("SELECT * FROM signal_snapshots WHERE price>0 ORDER BY timestamp DESC LIMIT 2000"):
    alert, reason = pipe.evaluate(dict(r))
    if alert: print(alert.symbol, alert.conviction, alert.entry_zone, alert.evidence["vetoes"])
PY
```

`scanner.db` is a symlink to the live DB (read-only use here); `.env` is a
private (mode 600) copy and is never read or logged by this package.

## Next step (highest leverage)

Keep collecting breadth, candle, source-family, bad-evidence, and GMGN
smart-wallet features, then retrain on the forward-collected rows. The evidence
still says re-modeling only old price/flow inputs is unlikely to beat the
existing scanner score; the leverage is new independent data.

---

## Paper trading (`paper_trade.py`)

There are two paper paths:

- **Forward runner**: `live_runner.py` consumes new `signal_snapshots`, sends
  ENTRY SIGNAL alerts, opens simulated positions when entry gates allow, keeps
  current state in `discovery/live_state.json`, and appends closed trades to
  `discovery/trades.jsonl`.
- **Replay runner**: `paper_trade.py` walks historical `signal_snapshots`
  chronologically and writes a replay ledger to `discovery/paper_results.json`.
  It does not touch the forward live state.

Balance and entry size are copied from config/env. The current local
data-collection profile uses `POSITION_INITIAL_BALANCE_SOL=100.00`,
`POSITION_POSITION_SIZE_USD=20`, and `LATTICE_MAX_OPEN_POSITIONS=0` so the
paper open-position cap is disabled.

The active exit model is `discovery.manager.PositionManager`: the initial stop
is ATR-scaled before first scale-out (`POSITION_ATR_STOP_K=5.0`, min `0.12`,
cap `0.70`, flat `0.30` fallback), Q3 is the primary take-profit mode, and the
configured ladder supplies cumulative sell fractions. With the current
`LATTICE_Q3_FIB_EXTENSIONS=2.618,4.236` and
`LATTICE_EXIT_SCALE_OUT_LADDER=3.0:0.50,6.0:0.95`, the first Q3 target sells
to 50% cumulative, the second sells to 95%, and 5% remains as the moonbag.
Post-scale management uses the scale/BE floor plus moonbag step floors; Q3 ATR
trailing is currently disabled and `LATTICE_Q3_VP_FLOOR_BUFFER_PCT=1.0` keeps
the VP component loose.

Stagnant positions have `LATTICE_MAX_HOLD_H=12`. Positions that touched
`LATTICE_MAX_HOLD_PARTIAL_RUNNER_MULTIPLE=1.5` and are still above entry get
until `LATTICE_MAX_HOLD_PARTIAL_RUNNER_H=24`; positions that touched
`LATTICE_MAX_HOLD_EXEMPT_MULTIPLE=2.0` are fully exempt from max hold.

```bash
env/bin/python -m discovery.paper_trade --days 3 --min-conviction 0.18 --cooldown-h 6
```

Outputs a summary (trades, win rate, total PnL, ending balance, profit factor,
exit breakdown, top trades) and writes a full ledger to
`discovery/paper_results.json`. This is replay/backtest paper trading; it
places no real orders and is safe to run while the forward runner is active.

For Q3 changes, prefer the actual-entry sweep:

```bash
env/bin/python analysis/q3_sweep.py --days 15
env/bin/python analysis/q3_sweep.py --days 15 --worst-runner-days 2
```

For discovery/source changes, prefer the read-only source attribution report:

```bash
env/bin/python analysis/discovery_quality_report.py --days 3
```

The Telegram command agent exposes the same view with `/discovery 3`.

Manual bundle scans use the same pure analyzer as the live log/gate:

```bash
env/bin/python analysis/bundle_cluster.py <token_address>
```

Weekly bad-outcome wallet recurrence uses the same bundle analyzer across
losing / rug-like Lattice trades:

```bash
env/bin/python analysis/bad_wallet_cluster_report.py --days 7 --max-tokens 30
```

It writes `bad_wallets.csv`, `bad_tokens.csv`, `bad_wallet_actors.csv`, JSON,
and Markdown under `analysis/bad_wallet_clusters/`. The Telegram command-agent
shortcut is `/badwallets 7 10`.

---

## Telegram alerting (`notify.py` + `live_runner.py`)

The pipeline emits `EntryAlert` objects; delivery is a separate layer so the
research path stays clean. Two modules:

- **`notify.py`** — `LatticeNotifier` formats and sends messages via the
  existing `TelegramAlertSender`. Every message is prefixed **`💎 [LATTICE]`**
  so it is distinguishable from the live bot's alerts. It posts to the SAME
  chat(s) the live bot uses (`TELEGRAM_CHAT_IDS` in `.env`). Safety flags:
  `LATTICE_TELEGRAM_DRY_RUN=true` (print, never send) and
  `LATTICE_TELEGRAM_ENABLED=false` (disable).
- **`live_runner.py`** — a daemon that polls `scanner.db` for NEW snapshots,
  runs the pipeline, and posts:
  - `ENTRY SIGNAL` — a token alert per new qualifying token (deduped per token
    via `--alert-cooldown-h`, default 12h);
  - `PAPER BUY` / `PAPER SCALE-OUT` / `PAPER SELL` — a live paper executor
    ($20 by current local env) opens, scales, closes, and reports simulated
    position changes.

  On first run it anchors `last_seen` to the newest snapshot, so it only alerts
  on snapshots arriving AFTER it starts — no history blast. State (wallet, open
  positions, cooldowns, last_seen) persists to `discovery/live_state.json`.
  Open positions are quote/fallback refreshed by a dedicated monitor controlled
  by `LATTICE_OPEN_POSITION_MONITOR_INTERVAL_SECONDS` (current local env:
  `2.0` seconds),
  so Telegram `/positions` does not have to wait for the full scanner poll.
  ENTRY SIGNAL alerts also attach a manual narrative-context line when
  `LATTICE_NARRATIVE_CONTEXT_ENABLED=true`: DexScreener token metadata plus
  Google News RSS are used to label the call as news-backed, weak/no-news, or
  no-news-found, with manual News/X search links. This is display-only context
  and never gates alerts, paper buys, exits, or live execution.

  Scanner chain routing is controlled by `SCANNER_ENABLED_CHAINS`. The active
  default is `solana,base`: Base uses DexScreener discovery/search and the
  normal pair validation/scoring flow. BSC native discovery is retired and
  disabled; old BSC analysis rows remain historical data only.

```bash
# preview formatting, nothing sent:
LATTICE_TELEGRAM_DRY_RUN=true env/bin/python -m discovery.live_runner --once

# live (posts [LATTICE] alerts to your Telegram channel):
env/bin/python -m discovery.live_runner --min-conviction 0.18 --poll-s 30
#   --no-paper      signals only, no paper BUY/SELL
#   --max-hold-h H  recycle stagnant positions (config default 12h)
#   --open-position-monitor-s S  refresh open marks independently
```

It reads `scanner.db`, places no real orders while live execution is disabled,
and owns the Lattice paper/live runner state. The main scanner remains the
telemetry and discovery producer.

---

## Participation / breadth — the unlock (`participation.py`)

The decisive lattice axis: is a move backed by *many distinct wallets* or a
*few*? Implemented as `HeliusAlchemyParticipationProvider` (the
`ParticipationProvider` interface), combining two independent signals into
breadth ∈ [-1, 1]:

- **Concentration** — top-N holder share via your existing **Alchemy** Solana RPC
  (`getTokenLargestAccounts` + `getTokenSupply`), LP/pool roughly excluded. High
  concentration → negative. No extra key.
- **Unique buyers** — distinct buyer wallets over a recent window via **Helius**
  parsed swaps (`getSignaturesForAddress` → `POST /v0/transactions`). Many
  distinct buyers → positive. Needs `HELIUS_API_KEY` in `.env` (already present).

The unique-buyers signal is decisive: if it's unavailable, breadth is `None`
(blind) rather than acting on concentration alone (misleading for bonding-curve
tokens). Gotcha handled: Helius's enhanced API is behind Cloudflare, which 403s
urllib's default User-Agent (error 1010) — a `User-Agent` header is required.

**Wired candidate-only + live** (`live_runner.py`): breadth is computed only for
tokens that clear conviction (a handful), in a worker thread so the slow
network calls (≈2–6s each) never stall the poll loop, cached per token (180s).
The signal message shows the breadth value + components, and a final gate
(`--min-breadth`, default −0.4) drops clearly-manufactured moves (concentrated +
few buyers); blind candidates are not gated. Disable with `--no-participation`.

**Forward-collection for retraining:** every conviction survivor (with its
breadth) is appended to `discovery/participation_log.jsonl`. Once enough
accumulate, the ranker is retrained with `participation_breadth` as a real
(non-null) feature — the one input the evidence says can push the model past the
existing scanner `score` (its weight is 0.000 today only because it was null).

> Status: live breadth verified end-to-end (e.g. SPIKE breadth +0.52 — buyers
> +0.70, conc 33%). Historical backfill of breadth is NOT done (Helius gives
> recent signatures cheaply, not deep history), so retraining waits on
> forward-collected data.
