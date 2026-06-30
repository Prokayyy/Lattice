# Lattice

Solana-first token discovery, alerting, paper-trading, and gated live-execution
research system.

Lattice is an async Python scanner for fast-moving Solana token markets. It
collects candidate tokens, enriches them with market and flow data, scores
entry quality, sends Telegram alerts, simulates position management, and keeps
the live-trading path behind explicit environment gates.

This public snapshot is intended for engineering review. Local credentials,
runtime ledgers, session files, database files, and machine-specific settings
are intentionally excluded.

## What It Does

- Discovers Solana token candidates from DexScreener, Jupiter, Pump.fun,
  GeckoTerminal fallbacks, and local signal history.
- Builds structured signal snapshots with price, liquidity, volume, buy/sell
  flow, source, quality, risk, and entry-gate metadata.
- Separates alert generation from simulated entries so useful signals can be
  reviewed even when capital or risk gates block a paper buy.
- Maintains a forward paper-trading ledger with open-position state,
  scale-outs, max-hold behavior, realized PnL, and replay outputs.
- Provides Telegram alerting, a command agent, and a public relay with narrowed
  public commands.
- Includes read-only and gated tooling for Definitive Flash integration work.

## Architecture

| Area | Files |
|---|---|
| Scanner entrypoint | `main.py` |
| Runtime configuration | `config.py`, `.env.example` |
| Candidate sources | `sources/` |
| Signal scoring and runner logic | `discovery/`, `scoring/` |
| Paper positions and reports | `trading/` |
| Telegram alerts and formatting | `alerts/`, `agents/`, `utils/tg_format.py` |
| Replay, research, and reports | `analysis/` |
| Operational and safety tooling | `tools/` |
| Definitive handoff summary | `DEFINITIVE_ENGINEERING_SUMMARY.md` |

## Definitive Flash Work

The repo includes a provider-gated Flash v1 integration path in
`trading/execution.py` and related tools. The implemented work covers:

- Flash quote, order, order-status, order-list, and cancel calls.
- Separate Flash host configuration from older Definitive portfolio endpoints.
- Ed25519 signing for SVM order messages.
- Partial signing for sponsored delegate transactions.
- Manual non-sponsored SVM setup for associated token accounts, wSOL wrapping,
  `syncNative`, and delegate approval.
- Fresh quote-per-retry behavior for market entry and exit submission.
- Terminal order polling, fill accounting, and `closeReason` surfacing.
- Trigger-order scaffolding for protective stop-loss and take-profit orders.
- A read-only quote smoke test in `tools/flash_quote_smoke.py`.

For a focused engineering handoff, see
[DEFINITIVE_ENGINEERING_SUMMARY.md](DEFINITIVE_ENGINEERING_SUMMARY.md).

## Safety Boundaries

Live order submission is disabled by default. Real trading requires several
independent gates, including global live enablement, dry-run disablement,
provider-specific enablement, provider-specific live confirmation, configured
credentials, and exposure limits.

The scanner, Telegram alerts, paper trading, and live execution are kept as
separate concerns. Public Telegram commands are limited by default, relay input
requires sender validation, external Telegram links are URL-validated, and LLM
inputs used by analysis helpers are treated as untrusted context.

## Quick Start

Create a virtual environment and install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Create a local environment file:

```bash
cp .env.example .env
```

Fill only the credentials needed for the parts of the system you plan to run.
Then start the scanner:

```bash
python main.py
```

Most integrations can be left unset for code review or offline analysis. Live
execution should stay disabled unless the relevant gates and risk limits have
been reviewed.

## Useful Commands

Syntax-check the key runtime modules:

```bash
python3 -m py_compile config.py main.py trading/execution.py alerts/telegram.py agents/telegram_agent.py
```

Run the public-release security check:

```bash
python3 tools/security_agent.py --strict
```

Generate a discovery quality report when local `scanner.db` data is available:

```bash
python3 analysis/discovery_quality_report.py --days 3
```

Run the read-only Flash quote smoke test after configuring credentials:

```bash
python3 tools/flash_quote_smoke.py
```

## Public Repo Notes

- `.env` and secret-bearing local files are ignored.
- Runtime JSON, JSONL, database, session, and telemetry files are ignored.
- Example configuration lives in `.env.example`.
- `data/ticker_lineage_overrides.example.json` is the public template for local
  ticker lineage overrides.
- Generated protobuf files are checked in. If files under `proto/` change,
  regenerate the matching `*_pb2.py` and `*_pb2_grpc.py` files.
- No license has been selected yet.
