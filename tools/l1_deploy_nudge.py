"""One-shot L1 capital-veto nudge.

Summarizes the capital lane's activity from discovery/entry_decisions.jsonl over a
window and sends ONE message to the main Telegram via the project's own notifier.
Intended to be fired after an L1 collection window, so the operator can eyeball
the active veto rate/mix before deciding whether to enable L2
(LATTICE_SCORECARD_ENABLED=true).

Self-contained: importing config auto-loads .env (cwd-independent), and
LatticeNotifier targets TELEGRAM_CHAT_IDS (the main channel).

Run:  env/bin/python tools/l1_deploy_nudge.py --since <epoch>
      env/bin/python tools/l1_deploy_nudge.py --window-h 24 --dry   # print, don't send
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import datetime as dt
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

DECISIONS = os.path.join(ROOT, "discovery", "entry_decisions.jsonl")


def _ts(d):
    for k in ("alert_ts", "ts", "timestamp"):
        v = d.get(k)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    return None


def _block(d):
    return str(d.get("block_family") or d.get("block") or "")


def summarize(since: float):
    total = entries = paper_buy_blocks = paper_disabled = evaluated = 0
    vetoes = collections.Counter()
    blocks = collections.Counter()
    last_ts = since
    if not os.path.exists(DECISIONS):
        return None
    with open(DECISIONS, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = _ts(d)
            if ts is None or ts < since:
                continue
            total += 1
            last_ts = max(last_ts, ts)
            blk = _block(d)
            blocks[blk] += 1
            if blk == "paper_disabled":
                paper_disabled += 1
            if d.get("entered") or blk == "entered":
                entries += 1
            if blk == "paper_buy":
                paper_buy_blocks += 1
            cv = d.get("capital_veto")
            # the key exists ("") on every paper-mode record; non-empty = fired
            if "capital_veto" in d:
                evaluated += 1
            if cv:
                vetoes[cv] += 1
    return {
        "total": total, "entries": entries,
        "paper_buy_blocks": paper_buy_blocks,
        "paper_disabled": paper_disabled,
        "evaluated": evaluated, "vetoes": vetoes, "blocks": blocks,
        "last_ts": last_ts,
    }


def render(since: float, s: dict) -> str:
    hours = max((s["last_ts"] - since) / 3600.0, 0.0)
    start = dt.datetime.fromtimestamp(since).strftime("%b %d %H:%M")
    veto_total = sum(s["vetoes"].values())

    if s["paper_disabled"] and not s["entries"] and not veto_total:
        head = ("⚠️ L1 reconciled-veto window — runner looks ALERT-ONLY: "
                f"{s['paper_disabled']} decisions logged 'paper_disabled' and the "
                "capital lane never ran. Is --no-paper still set?")
        return head

    lines = [
        f"🧪 L1 reconciled-veto window — {hours:.0f}h check",
        "",
        "Veto set: ST risk_high OFF · ST sniped ON · row vetoes ON",
        f"Window: {start} → now ({hours:.1f}h)",
        f"Decisions logged: {s['total']}",
        f"Paper entries (passed reconciled L1): {s['entries']}",
        f"Paper-buy gate blocks (pre-L1, not in veto rate): {s.get('paper_buy_blocks', 0)}",
        f"L1 capital_veto fired: {veto_total}",
    ]
    if veto_total:
        for reason, n in s["vetoes"].most_common():
            lines.append(f"  • {reason}: {n}")
    else:
        lines.append("  (none — no vetoable actor reached the capital gate)")
    denom = veto_total + s["entries"]
    if denom:
        lines.append(f"L1 rejection rate (vetoes / capital-eligible): "
                     f"{100.0 * veto_total / denom:.1f}%")
    lines += [
        "",
        "L2/L3 still off. If this reconciled veto rate and mix look sane, next step is L2:",
        "set LATTICE_SCORECARD_ENABLED=true in .env and restart the supervisor.",
    ]
    return "\n".join(lines)


async def _send(msg: str):
    from discovery.notify import LatticeNotifier
    await LatticeNotifier().text(msg)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--since", type=float, help="epoch seconds; window start")
    g.add_argument("--window-h", type=float, help="window = last N hours")
    ap.add_argument("--dry", action="store_true", help="print, do not send")
    args = ap.parse_args()

    if args.since is not None:
        since = args.since
    elif args.window_h is not None:
        since = time.time() - args.window_h * 3600.0
    else:
        since = time.time() - 24 * 3600.0

    s = summarize(since)
    if s is None:
        msg = "🧪 L1 reconciled-veto check: entry_decisions.jsonl not found."
    elif s["total"] == 0:
        msg = (f"🧪 L1 reconciled-veto check: no decisions logged since "
               f"{dt.datetime.fromtimestamp(since):%b %d %H:%M} (scanner idle?).")
    else:
        msg = render(since, s)

    if args.dry:
        print(msg)
        return 0
    try:
        asyncio.run(_send(msg))
        print("nudge sent.")
    except Exception as e:                                       # noqa: BLE001
        print(f"nudge send FAILED: {type(e).__name__}: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
