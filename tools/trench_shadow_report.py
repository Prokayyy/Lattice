"""Summarize shadow trench-regime telemetry from entry_decisions.jsonl.

Read-only. This does not affect runner state or trading behavior.

Run:
  env/bin/python tools/trench_shadow_report.py --window-h 24
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import sys
import time


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_PATH = os.path.join(ROOT, "discovery", "entry_decisions.jsonl")


def _f(value, default=0.0):
    try:
        return float(value if value is not None else default)
    except (TypeError, ValueError):
        return float(default)


def _read_rows(path, since):
    rows = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if _f(row.get("ts") or row.get("timestamp")) >= since:
                    rows.append(row)
    except OSError as exc:
        print(f"read failed: {exc}", file=sys.stderr)
        return []
    return rows


def _block(row):
    return str(row.get("block_family") or row.get("block") or "")


def _hit_items(row, key):
    value = row.get(key)
    if isinstance(value, list):
        return [str(item) for item in value if item]
    if isinstance(value, str) and value:
        return [value]
    return []


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--window-h", type=float, default=24.0)
    parser.add_argument("--path", default=DEFAULT_PATH)
    args = parser.parse_args()

    since = time.time() - args.window_h * 3600.0
    rows = [
        row for row in _read_rows(args.path, since)
        if row.get("trench_regime")
    ]

    print(f"Shadow trench report: last {args.window_h:g}h")
    print(f"rows={len(rows)}")
    if not rows:
        return 0

    by_regime = collections.defaultdict(list)
    hits = collections.Counter()
    kol_hits = collections.Counter()
    for row in rows:
        by_regime[str(row.get("trench_regime") or "unknown")].append(row)
        hits.update(_hit_items(row, "trench_narrative_hits"))
        kol_hits.update(_hit_items(row, "trench_kol_hits"))

    print()
    print("regime     rows entries paper_buy veto allowed avg_score avg_upnl")
    for regime, items in sorted(by_regime.items()):
        entries = sum(1 for row in items if row.get("entered") or _block(row) == "entered")
        paper_buy = sum(1 for row in items if _block(row) == "paper_buy")
        veto = sum(1 for row in items if _block(row) == "capital_veto")
        allowed = sum(1 for row in items if row.get("trench_shadow_capital_allowed"))
        avg_score = sum(_f(row.get("trench_heat_score")) for row in items) / len(items)
        avg_upnl = sum(_f(row.get("trench_open_upnl_usd")) for row in items) / len(items)
        print(
            f"{regime:<10} {len(items):>4} {entries:>7} {paper_buy:>9} "
            f"{veto:>4} {allowed:>7} {avg_score:>9.1f} {avg_upnl:>8.1f}"
        )

    if hits:
        print()
        print("top narrative hits:", ", ".join(
            f"{term}x{count}" for term, count in hits.most_common(12)
        ))
    if kol_hits:
        print("top KOL hits:", ", ".join(
            f"{term}x{count}" for term, count in kol_hits.most_common(12)
        ))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
