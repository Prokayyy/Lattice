"""Discovery-native outcomes recorder.

WHY: the conviction ranker has been training on the MAIN bot's
`ignition_alerts` -> `alert_outcomes`, which (a) hard-stopped on 2026-05-31 and
(b) is a DIFFERENT token population than the discovery system actually scores
(~19 tokens overlap). So training data is both dead and mismatched. This records
the discovery system's OWN outcomes, so the ranker can train on its real,
accruing population — and with participation breadth already attached.

SOURCE of candidates: `participation_log.jsonl` — every conviction survivor,
already carrying its decision snapshot `row`, `entry_price`, `conviction`, and
`breadth`. OUTCOME: forward `max_multiple` over standard windows + realized PnL
under the live `new` exit engine, computed from the (fresh) `signal_snapshots`
forward path once the window has elapsed. De-glitched with a self-contained
rolling-median filter (no dependency on the dead alert_outcomes).

Incremental + idempotent: keyed by (token, alert_ts); only newly-complete
candidates are processed. Append-only `discovery_outcomes.jsonl`. Never writes
the (read-only, symlinked) scanner.db.

Run:
  env/bin/python -m discovery.outcomes            # record newly-complete candidates
  env/bin/python -m discovery.outcomes --report   # summarize the outcomes store
"""
import argparse
import json
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from discovery.manager import manage
from discovery.train_ranker import DB  # scanner.db (read-only)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLOG = os.path.join(ROOT, "discovery", "participation_log.jsonl")
OUT = os.path.join(ROOT, "discovery", "discovery_outcomes.jsonl")

SIZE_USD = float(getattr(config, "POSITION_POSITION_SIZE_USD", 20) or 20)
WINDOWS = {"5m": 300, "15m": 900, "1h": 3600, "6h": 21600}
MAX_WINDOW = max(WINDOWS.values())
MAX_HOLD_S = 3 * 3600


def _reject(price, accepted):
    """Self-contained glitch filter: reject ticks that deviate wildly from the
    rolling median of recent ACCEPTED prices (anchored, so glitch runs can't drag
    it). High side (fake-gain risk) tight at 4x; low side at 1/10 for near-zero
    crater glitches (real dumps trip the engine's -30% stop before this floor)."""
    window = accepted[-9:]
    ref = sorted(window)[len(window) // 2] if window else price
    return price > 4.0 * ref or price < ref / 10.0


def forward_outcome(con, token, alert_ts, entry_price, entry_liq):
    rows = con.execute(
        "SELECT * FROM signal_snapshots WHERE token_address=? AND timestamp>? "
        "AND timestamp<=? AND price>0 ORDER BY timestamp ASC",
        (token, alert_ts, alert_ts + MAX_WINDOW),
    ).fetchall()
    if len(rows) < 2:
        return None
    pos = {
        "token": token, "symbol": "", "entry_ts": alert_ts,
        "entry_price": entry_price, "remaining": SIZE_USD / entry_price,
        "peak": entry_price, "proceeds": 0.0, "scaled": False,
        "levels_done": set(), "cost_usd": SIZE_USD,
        "entry_liquidity": entry_liq, "peak_liquidity": entry_liq,
    }
    accepted = [entry_price]
    last_price = entry_price
    max_mult = {k: 1.0 for k in WINDOWS}
    for r in rows:
        row = dict(r)
        price = float(row.get("price") or 0)
        if price <= 0 or _reject(price, accepted):
            continue
        accepted.append(price)
        last_price = price
        mult = price / entry_price
        dt = float(row.get("timestamp") or 0) - alert_ts
        for k, w in WINDOWS.items():
            if dt <= w and mult > max_mult[k]:
                max_mult[k] = mult
        if not pos.get("closed"):  # engine replay (PnL) runs until it exits
            manage(pos, price, float(row.get("timestamp") or 0),
                   max_hold_s=MAX_HOLD_S, features=row, engine="new")
    if not pos.get("closed"):  # mark-to-last at horizon end
        pos["proceeds"] += pos["remaining"] * last_price
        pos["remaining"] = 0.0
    return {
        "max_mult": max_mult,
        "realized_pnl": pos["proceeds"] - SIZE_USD,
        "exit_reason": pos.get("reason", "open_at_end"),
        "peak_mult": pos["peak"] / entry_price,
        "n_fwd": len(accepted) - 1,
    }


def _iter_candidates():
    if not os.path.exists(PLOG):
        return
    for line in open(PLOG):
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def _recorded_keys():
    keys = set()
    if os.path.exists(OUT):
        for line in open(OUT):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                keys.add((r.get("token"), int(float(r.get("alert_ts") or 0))))
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
    return keys


def record():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    recorded = _recorded_keys()
    now = time.time()
    n_new = n_incomplete = n_nodata = n_dup = 0
    with open(OUT, "a") as out:
        for c in _iter_candidates():
            tok = c.get("token")
            ts = float(c.get("ts") or 0)
            ep = float(c.get("entry_price") or 0)
            if not tok or ts <= 0 or ep <= 0:
                continue
            key = (tok, int(ts))
            if key in recorded:
                n_dup += 1
                continue
            if now - ts < MAX_WINDOW:        # window not elapsed yet
                n_incomplete += 1
                continue
            row = c.get("row") or {}
            eliq = float(row.get("liquidity") or row.get("raw_liquidity") or 0)
            oc = forward_outcome(con, tok, ts, ep, eliq)
            recorded.add(key)
            if oc is None:                   # token stopped being tracked; no path
                out.write(json.dumps({"token": tok, "alert_ts": ts, "no_data": True,
                                      "recorded_at": now}) + "\n")
                n_nodata += 1
                continue
            rec = {
                "token": tok, "symbol": c.get("symbol", ""), "alert_ts": ts,
                "entry_price": ep, "conviction": c.get("conviction"),
                "breadth": c.get("breadth"), "concentration": c.get("concentration"),
                "buyers_sig": c.get("buyers_sig"),
                "max_mult_5m": round(oc["max_mult"]["5m"], 4),
                "max_mult_15m": round(oc["max_mult"]["15m"], 4),
                "max_mult_1h": round(oc["max_mult"]["1h"], 4),
                "max_mult_6h": round(oc["max_mult"]["6h"], 4),
                "realized_pnl": round(oc["realized_pnl"], 4),
                "exit_reason": oc["exit_reason"],
                "peak_mult": round(oc["peak_mult"], 4),
                "n_fwd": oc["n_fwd"], "recorded_at": now, "row": row,
            }
            out.write(json.dumps(rec, default=str) + "\n")
            n_new += 1
    print(f"recorded {n_new} new outcomes | {n_incomplete} not-yet-complete (<6h) | "
          f"{n_nodata} no-forward-data | {n_dup} already-recorded -> {OUT}")


def report():
    rows = [r for r in _iter_candidates_file(OUT) if not r.get("no_data")]
    n = len(rows)
    if n == 0:
        print("no outcomes recorded yet; run `python -m discovery.outcomes` first.")
        return
    import collections, datetime
    ge2 = sum(1 for r in rows if (r.get("max_mult_1h") or 0) >= 2.0)
    prof = sum(1 for r in rows if (r.get("realized_pnl") or 0) > 0)
    with_breadth = sum(1 for r in rows if r.get("breadth") is not None)
    tot_pnl = sum(float(r.get("realized_pnl") or 0) for r in rows)
    ts = [float(r.get("alert_ts") or 0) for r in rows if r.get("alert_ts")]
    span = (max(ts) - min(ts)) / 86400 if ts else 0
    d = lambda t: datetime.datetime.utcfromtimestamp(t).strftime("%m-%d %H:%M")
    print(f"discovery_outcomes: {n} labeled rows over {span:.1f} days "
          f"({d(min(ts))} -> {d(max(ts))})")
    print(f"  label rates: >=2x@1h {ge2} ({100*ge2/n:.1f}%) | "
          f"profitable-under-engine {prof} ({100*prof/n:.1f}%)")
    print(f"  participation breadth attached: {with_breadth} ({100*with_breadth/n:.1f}%)  "
          f"<- aligned population, unlike the dead 384-row alert_outcomes")
    print(f"  take-all realized PnL: ${tot_pnl:.2f} (mean ${tot_pnl/n:+.2f}/alert)")
    print(f"  exit reasons: {dict(collections.Counter(r.get('exit_reason') for r in rows))}")


def _iter_candidates_file(path):
    if not os.path.exists(path):
        return
    for line in open(path):
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true", help="summarize the store; do not record")
    args = ap.parse_args()
    if args.report:
        report()
    else:
        record()
        report()


if __name__ == "__main__":
    main()
