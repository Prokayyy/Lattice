# Lattice Scanner - Definitive Engineering Summary

## Context

Lattice Scanner is a Solana-first memecoin discovery, alerting, paper-trading,
and live-execution research system. It started as a scanner for fast-moving
token opportunities and has evolved into a full pipeline for signal collection,
entry decisioning, Telegram alerting, paper-position management, replay analysis,
and provider-gated live execution.

The current runtime is intentionally conservative: it is in paper/data-collection
mode by default, with live execution disabled unless multiple independent gates
are explicitly enabled.

## What The System Does

- Collects Solana token candidates from sources such as DexScreener, Jupiter,
  Pump.fun, GeckoTerminal fallbacks, and local SQLite telemetry.
- Writes structured signal snapshots with price, liquidity, volume, buy/sell
  flow, pressure, source, quality, risk, and entry-gate metadata.
- Runs a Lattice entry pipeline that separates candidate discovery, conviction
  scoring, timing, entry-zone validation, and paper-buy gating.
- Sends Telegram `ENTRY SIGNAL` alerts independently from simulated entries, so
  useful alerts can still be reviewed even when capital gates block a paper buy.
- Maintains a forward paper-trading ledger, open-position state, scale-outs,
  max-hold behavior, realized PnL, and replay/backtest outputs.
- Supports provider-gated live execution paths in `trading/execution.py`,
  including Definitive QuickTrade/portfolio quote checks, Definitive Flash, and
  GMGN swap execution.

## Definitive Flash Integration

The repo includes a Flash v1 client and execution manager that cover the main
SVM lifecycle:

- `POST /v1/quote`, `POST /v1/order`, `GET /v1/orders/{id}`,
  `GET /v1/orders`, and cancel-order calls.
- Flash API host separation via `DEFINITIVE_FLASH_API_BASE_URL`, currently
  defaulting to `https://flash.definitive.fi`, while older Definitive portfolio
  endpoints remain on `DEFINITIVE_API_BASE_URL`.
- Ed25519 signing of the SVM `orderMessage` exactly as returned by Flash.
- Partial signing of `svm.sponsoredDelegateTx` while preserving the sponsor
  signature slot.
- Manual non-sponsored SVM onboarding for cases where Flash returns
  `svm.delegateIx` without a sponsored delegate transaction:
  - create associated token accounts idempotently;
  - wrap SOL into wSOL;
  - call `syncNative`;
  - approve the Flash delegate over the relevant token account;
  - simulate, broadcast, and confirm the setup transaction.
- Market entry and exit order submission with fresh quote-per-retry behavior.
- Terminal order polling and fill accounting using Flash fill fields
  (`targetAmount` and `contraAmount`).
- Surfacing `FlashOrder.closeReason`, including cases such as
  `REASON_ORDER_EXPIRED`, into result metadata so failures are diagnosable in
  alerts and ledgers.
- Flash trigger-order scaffolding for protective stop-loss and take-profit
  orders using the `triggers` array shape from the OpenAPI spec.
- A read-only smoke test script, `tools/flash_quote_smoke.py`, that validates
  market and stop-loss quote request shapes without calling `POST /order`.

## Live-Test Learnings

During constrained live testing, the system was run with very small notional
limits and independent arming gates. A prior Flash SOL bonding-curve buy expired
server-side with `REASON_ORDER_EXPIRED`, which led to several defensive changes:

- capture and surface `closeReason` on all terminal Flash order outcomes;
- retry with a fresh quote rather than reusing stale quote data;
- add configurable fill-confirmation windows and retry delays;
- precheck wSOL and native SOL funding before buy submission;
- always top up wSOL before Flash buys because live submits debit pre-wrapped
  SOL through delegate authority;
- add a short wrap-settlement delay so the submit-time balance check can observe
  the newly wrapped wSOL;
- keep Flash live execution behind explicit environment gates.

The project later kept Flash available as a fallback/integration path while
testing an alternate GMGN execution provider. Flash remains one of the most
technically interesting integrations in the repo because it forced the system to
handle SVM signing, delegated token movement, quote expiry, terminal order
states, and server-side trigger orders carefully.

## Safety Model

Live behavior is deliberately split from scanning, alerting, and paper trading.
Real order submission requires multiple gates, including:

- global live execution enabled;
- dry-run disabled;
- provider-specific enabled flag;
- provider-specific live confirmation flag;
- configured credentials;
- per-position and total-exposure limits.

The scanner and Lattice runner can be kept separate so only one process owns
live-trading responsibility. Current local defaults keep live execution disarmed
and allow paper/data collection to continue.

## Analysis And Feedback Loops

The project is not only an execution wrapper. It includes an analysis workflow
around whether signals actually work:

- `scanner.db` stores signal snapshots, candles, candidate events, and outcomes.
- `discovery/trades.jsonl` stores the forward Lattice paper-trade ledger.
- replay scripts compare changes against historical signal snapshots.
- Q3/tail exit sweeps evaluate runner behavior and scale-out rules.
- entry-decision logs attribute why candidates were blocked, alerted, or
  paper-entered.
- bad-wallet and bundle-cluster reports look for repeat-risk patterns in poor
  outcomes.

This has made the project a practical environment for learning async Python,
on-chain execution constraints, provider integrations, telemetry, and failure
analysis.

## Areas Where Definitive Feedback Would Be Valuable

- Recommended retry and timeout strategy for high-volatility SVM Flash market
  orders.
- How to interpret and remediate different Flash terminal `closeReason` values.
- Best practices for sponsored vs non-sponsored delegate onboarding.
- Whether stop-loss and take-profit trigger orders are intended as production
  exit primitives for fast-moving Solana tokens, or mainly as backstops.
- Whether a diagnostics endpoint could expose failure phase, executor latency,
  quote expiry, route choice, or more granular rejection context.
- Whether there is or could be a safe sandbox/paper Flash environment for
  lifecycle testing without moving funds.

## Internship Interest

I would be interested in an internship or early-career opportunity with the
Definitive engineering team, especially around developer experience, trading
infrastructure, integrations, or technical support tooling.

This project has given me hands-on experience with:

- integrating a real trading provider into an automated system;
- managing live-execution safety gates;
- handling Solana-specific signing and token-account mechanics;
- building read-only and live-path diagnostics;
- designing retry, reconciliation, and failure-reporting logic;
- using data and replay outputs to evaluate trading behavior rather than relying
  only on intuition.

I would appreciate feedback on the integration design and whether this kind of
work maps to an internship opportunity at Definitive.
