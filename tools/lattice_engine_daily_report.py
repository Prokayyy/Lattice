"""Daily engine-activity report for the Lattice discovery runner.

Watches the paper-first forward validation of the new exit engine: summarizes
new-engine exit activity + paper PnL from the local runner state and posts it to
Telegram (reusing the bot's LatticeNotifier, tagged [LATTICE]). Also prints
to stdout so a cron log captures it.

The point: the new engine is proven to be "actually running" only when its
exit reasons (strict_early_failure_exit / break_even_floor / no_progress_time_stop
/ liquidity_drain_* / sell_only_flow_exit) start appearing in the trade ledger.
This report surfaces that count plus the rolling paper PnL.

Run:
  env/bin/python tools/lattice_engine_daily_report.py            # send to Telegram
  env/bin/python tools/lattice_engine_daily_report.py --print    # preview only
  env/bin/python tools/lattice_engine_daily_report.py --window-h 24
"""
import argparse
import asyncio
import collections
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config
from discovery.notify import LatticeNotifier

LEDGER = os.path.join(ROOT, "discovery", "trades.jsonl")
STATE = os.path.join(ROOT, "discovery", "live_state.json")
HEARTBEAT = os.path.join(ROOT, "discovery", "live_runner_heartbeat.json")

# Exit reasons only the NEW engine can produce — their presence proves the
# live runner is executing the new code (the old engine emits only
# initial_stop / trailing_stop / max_hold).
NEW_ENGINE_REASONS = {
    "strict_early_failure_exit",
    "break_even_floor",
    "scale_stop_floor",
    "no_progress_time_stop",
    "liquidity_drain_from_entry",
    "liquidity_drain_from_peak",
    "sell_only_flow_exit",
}


def _load_jsonl(path):
    rows = []
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    except OSError:
        pass
    return rows


def _load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def _f(d, k, default=0.0):
    try:
        v = d.get(k)
        return float(v) if v is not None else float(default)
    except (TypeError, ValueError, AttributeError):
        return float(default)


def _fmt_reasons(counter):
    if not counter:
        return "—"
    return ", ".join(f"{k}×{v}" for k, v in counter.most_common())


def build_report(window_h=24.0):
    now = time.time()
    cutoff = now - window_h * 3600
    trades = _load_jsonl(LEDGER)
    state = _load_json(STATE)
    hb = _load_json(HEARTBEAT)

    recent = [t for t in trades if _f(t, "exit_ts") >= cutoff]
    rec_pnl = sum(_f(t, "pnl_usd") for t in recent)
    rec_wins = sum(1 for t in recent if _f(t, "pnl_usd") > 0)
    rec_reasons = collections.Counter(t.get("reason") for t in recent)
    all_reasons = collections.Counter(t.get("reason") for t in trades)
    new_recent = sum(v for k, v in rec_reasons.items() if k in NEW_ENGINE_REASONS)
    new_all = sum(v for k, v in all_reasons.items() if k in NEW_ENGINE_REASONS)
    ge2 = sum(1 for t in recent if _f(t, "peak_mult") >= 2)
    ge3 = sum(1 for t in recent if _f(t, "peak_mult") >= 3)

    engine = getattr(config, "LATTICE_EXIT_ENGINE", "?")
    dry = getattr(config, "LIVE_EXECUTION_DRY_RUN", None)
    posture = "PAPER (dry-run, no real orders)" if dry else "LIVE (real orders)"

    hb_time = _f(hb, "time")
    hb_age = int(now - hb_time) if hb_time > 0 else None
    if hb_age is None:
        runner = "⚠️ no heartbeat"
    elif hb_age > 300:
        runner = f"⚠️ stale {hb_age}s"
    else:
        runner = f"ok {hb_age}s ago"

    open_n = hb.get("open_positions", len(state.get("open_pos", {})))
    cash = _f(state, "cash")
    realized = _f(state, "realized")
    win_pct = round(100 * rec_wins / max(len(recent), 1))

    if engine != "new":
        flag = "⚠️ engine is NOT 'new'"
    elif new_recent > 0:
        flag = "✅ new-engine exits firing"
    elif new_all > 0:
        flag = "✅ new engine active (no new-style exits in window)"
    else:
        flag = "⏳ no new-engine exits yet — watch this climb"

    return "\n".join([
        f"<b>— Engine daily report</b> (last {window_h:.0f}h)",
        f"engine <b>{engine}</b> | {posture} | runner {runner}",
        "",
        f"<b>24h:</b> {len(recent)} closes | win {win_pct}% | "
        f"PnL <b>{rec_pnl:+.2f}</b> | ≥2x {ge2} ≥3x {ge3}",
        f"24h exits: {_fmt_reasons(rec_reasons)}",
        f"new-engine exits: 24h <b>{new_recent}</b> | all-time <b>{new_all}</b> — {flag}",
        "",
        f"<b>book:</b> open {open_n} | cash ${cash:.2f} | "
        f"realized total ${realized:.2f} | ledger {len(trades)} trades",
        f"all-time exits: {_fmt_reasons(all_reasons)}",
    ])


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--window-h", type=float, default=24.0)
    ap.add_argument("--print", action="store_true",
                    help="print only; do not send to Telegram")
    args = ap.parse_args()

    report = build_report(args.window_h)
    print(report)
    if not args.print:
        await LatticeNotifier().text(report)


if __name__ == "__main__":
    asyncio.run(main())
