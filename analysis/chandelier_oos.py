"""Out-of-sample / optimism validation of the chandelier_sweep finding.

The full-window sweep (analysis/chandelier_sweep.py + chandelier_strategy_design.md
section 8) found ONE win: A1_PROD_chand (PROD's aggressive ladder + the ATR
chandelier on the 5% moonbag tail) beat bare PROD by ~+$116 over ~14d, while every
"keep a fat 40% runner" variant LOST to PROD. This script stress-tests the A1 win
two ways, like analysis/stop_oos.py does for the stop-width finding:

  1) OPTIMISM DECOMPOSITION. The replay marks on snapshot prices, so positions that
     exit by HOLDING to a mark (max_hold / open_at_end) are scored more
     optimistically than positions that hit a TRIGGERED exit. We split each
     variant's PnL into held$ vs realized$, and split A1's advantage-over-PROD the
     same way. A1's +$116 is only trustworthy if it's mostly realized$.

  2) WALK-FORWARD SPLIT. Each variant's per-trade ledger is split by ENTRY time at
     the window midpoint into early (tune) and late (holdout) halves. Headline test:
     does A1 still beat PROD on the HELD-OUT late half?

MEMORY ISOLATION (important on this 3.8GB box that shares RAM with the live
scanner): each variant is replayed in its OWN subprocess (`--one LABEL --out FILE`)
which dumps its ledger and EXITS, freeing all memory, before the next variant
starts. Running two full 14d replays in one process OOM-killed both the backtest
AND the live trader. The orchestrator parent stays tiny and just combines the dumps.

Reuses chandelier_sweep's VARIANTS + apply() + (capped) candle memo, and
discovery.paper_trade.run. Replays NATIVELY against the live hot scanner.db (~14d).
Absolute $ is replay-optimistic; trust relative deltas.

Run: env/bin/python analysis/chandelier_oos.py --days 20 --min-conviction 0.18
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "analysis"))

import chandelier_sweep as CS  # noqa: E402  (reuse VARIANTS/apply/install_memo)
from discovery import paper_trade as PT  # noqa: E402

LEDGER = ROOT / "discovery" / "paper_results.json"
OOS_DIR = ROOT / "analysis" / ".oos"
HELD = {"max_hold", "open_at_end"}     # exits scored at a mark, not a triggered fill
PROD = "A0_PROD"
A1 = "A1_PROD_chand"
WANT = ["A0_PROD", "A1_PROD_chand"]    # headline comparison only (memory-safe)


def _find(label):
    for lbl, o in CS.VARIANTS:
        if lbl.strip() == label:
            return o
    raise SystemExit(f"unknown variant label: {label}")


def run_one(label, out_path, args):
    """Child process: replay ONE variant, dump its ledger, exit (frees memory)."""
    CS.install_memo()
    CS.apply(_find(label))
    t0 = time.time()
    PT.run(args.days, args.min_conviction, args.cooldown_h,
           max_hold_h=args.max_hold_h, quiet=True)
    led = json.load(open(LEDGER))["ledger"]
    keep = [{"entry_ts": t.get("entry_ts"), "pnl_usd": t.get("pnl_usd"),
             "peak_mult": t.get("peak_mult"), "reason": t.get("reason")}
            for t in led]
    json.dump({"label": label, "trades": len(keep), "ledger": keep},
              open(out_path, "w"))
    print(f"[{time.time()-t0:5.0f}s] {label}: {len(keep)} trades -> {out_path}",
          flush=True)


def now_span(db):
    import sqlite3
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        now = c.execute(
            "SELECT MAX(timestamp) FROM signal_snapshots WHERE price>0").fetchone()[0]
        mn = c.execute(
            "SELECT MIN(timestamp) FROM signal_snapshots WHERE price>0").fetchone()[0]
        return now, (now - mn) / 86400.0
    finally:
        c.close()


def analyze(dumps, mid):
    def pnl(rows):
        return round(sum(t["pnl_usd"] for t in rows), 2)

    def n2x(rows):
        return sum(1 for t in rows if (t.get("peak_mult") or 0) >= 2.0)

    per = {d["label"]: d["ledger"] for d in dumps}

    print("\n=== Optimism decomposition (full window) ===")
    print(f"{'variant':18}{'total$':>10}{'held$':>10}{'realized$':>11}"
          f"{'held%tr':>9}{'2x':>5}")
    for label in WANT:
        led = per.get(label)
        if not led:
            continue
        held_rows = [t for t in led if t["reason"] in HELD]
        tot, held, real = pnl(led), pnl(held_rows), pnl(
            [t for t in led if t["reason"] not in HELD])
        hpct = round(100 * len(held_rows) / max(len(led), 1))
        print(f"{label:18}{tot:>10.2f}{held:>10.2f}{real:>11.2f}"
              f"{hpct:>8}%{n2x(led):>5}", flush=True)

    if PROD in per and A1 in per:
        base = per[PROD]
        b_held = pnl([t for t in base if t["reason"] in HELD])
        b_real = pnl([t for t in base if t["reason"] not in HELD])
        a = per[A1]
        d_held = pnl([t for t in a if t["reason"] in HELD]) - b_held
        d_real = pnl([t for t in a if t["reason"] not in HELD]) - b_real
        print("\nA1 advantage vs PROD, split (is the +$ a real exit or held marks?):")
        print(f"  d_realized ${d_real:+8.2f}   d_held ${d_held:+8.2f}   "
              f"d_total ${d_real + d_held:+8.2f}", flush=True)

    print(f"\n=== Walk-forward split @ entry_ts midpoint "
          f"({time.strftime('%m-%d %H:%M', time.gmtime(mid))} UTC) ===")
    print(f"{'variant':18}{'early$':>10}{'late$':>10}{'late2x':>8}{'lateN':>7}")
    res = {}
    for label in WANT:
        led = per.get(label)
        if not led:
            continue
        early = [t for t in led if (t["entry_ts"] or 0) < mid]
        late = [t for t in led if (t["entry_ts"] or 0) >= mid]
        res[label] = (pnl(early), pnl(late), n2x(late), len(late))
        print(f"{label:18}{res[label][0]:>10.2f}{res[label][1]:>10.2f}"
              f"{res[label][2]:>8}{res[label][3]:>7}", flush=True)

    if PROD in res and A1 in res:
        a0, a1 = res[PROD], res[A1]
        print("\n--- HEADLINE: A1 (PROD+chandelier-on-moonbag) vs PROD ---")
        print(f"  EARLY: A1 ${a1[0]:.2f} vs PROD ${a0[0]:.2f}  => dA1 ${a1[0]-a0[0]:+.2f}")
        print(f"  LATE : A1 ${a1[1]:.2f} vs PROD ${a0[1]:.2f}  => dA1 ${a1[1]-a0[1]:+.2f}"
              f"  {'HOLDS OOS' if a1[1] > a0[1] else 'FAILS OOS (overfit/regime)'}")


def main():
    ap = argparse.ArgumentParser(description="Chandelier OOS + optimism check")
    ap.add_argument("--days", type=float, default=20.0)
    ap.add_argument("--min-conviction", type=float, default=0.18)
    ap.add_argument("--cooldown-h", type=float, default=6.0)
    ap.add_argument("--max-hold-h", type=float, default=12.0)
    ap.add_argument("--one", type=str, default="", help="run ONE variant, dump to --out")
    ap.add_argument("--out", type=str, default="", help="dump path for --one")
    ap.add_argument("--combine", nargs="*", default=None,
                    help="combine dumped ledgers (paths) into the OOS analysis")
    args = ap.parse_args()

    if args.one:                       # isolated single-variant replay
        run_one(args.one, args.out, args)
        return

    if args.combine is not None:       # final analysis over pre-dumped ledgers
        dumps = [json.load(open(p)) for p in args.combine]
        now, span = now_span(PT.DB)
        mid = now - (min(args.days, span) / 2.0) * 86400
        print(f"window: ~{min(args.days, span):.1f}d  "
              f"mid={time.strftime('%m-%d %H:%M', time.gmtime(mid))} UTC", flush=True)
        analyze(dumps, mid)
        return

    OOS_DIR.mkdir(parents=True, exist_ok=True)
    now, span = now_span(PT.DB)
    mid = now - (min(args.days, span) / 2.0) * 86400
    print(f"window: ~{min(args.days, span):.1f}d  "
          f"mid={time.strftime('%m-%d %H:%M', time.gmtime(mid))} UTC", flush=True)

    dumps = []
    for label in WANT:
        out = OOS_DIR / f"{label}.json"
        print(f"--- spawning isolated replay: {label} ---", flush=True)
        r = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()),
             "--one", label, "--out", str(out),
             "--days", str(args.days), "--min-conviction", str(args.min_conviction),
             "--cooldown-h", str(args.cooldown_h), "--max-hold-h", str(args.max_hold_h)],
            cwd=str(ROOT))
        if r.returncode != 0 or not out.exists():
            print(f"  !! {label} child failed (rc={r.returncode}); skipping", flush=True)
            continue
        dumps.append(json.load(open(out)))

    if len(dumps) < 2:
        print("need >=2 variants for the split; aborting", flush=True)
        return
    analyze(dumps, mid)


if __name__ == "__main__":
    main()
