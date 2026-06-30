"""Out-of-sample validation of the "wider ATR stop = better" finding, plus a
replay-optimism decomposition.

Two tests:
  1) WALK-FORWARD SPLIT. Each variant replays the same N-day window, then its
     per-trade ledger is split by ENTRY time at a fixed calendar midpoint into an
     early (tune) half and a late (holdout) half. We pick the variant that wins
     the early half and check whether it still beats PROD on the held-out late
     half. If the early winner underperforms late -> overfit to one regime.
  2) OPTIMISM DECOMPOSITION. The replay marks on snapshot prices, so positions
     that exit by HOLDING to a mark (max_hold / open_at_end) are scored more
     optimistically than positions that hit a triggered exit. Wider stops let
     more positions survive to those held marks. We split each variant's PnL into
     held$ (max_hold+open_at_end) vs realized$ (triggered exits) to see whether
     the wide variants' ADVANTAGE over PROD is real-exit PnL or just held marks.

Reuses discovery.paper_trade.run, which writes discovery/paper_results.json with
the full ledger every call (read right after, same as exit_config_backtest.py
overwrites it). Entries are ~identical across variants (entry logic is
stop-independent; small drift only from cash-timing).

Run: env/bin/python analysis/stop_oos.py --days 10 --min-conviction 0.18
"""
import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config  # noqa: E402
import discovery.manager as M  # noqa: E402
from discovery import paper_trade as PT  # noqa: E402

LEDGER = ROOT / "discovery" / "paper_results.json"
DB = ROOT / "scanner.db"
HELD = {"max_hold", "open_at_end"}  # exits scored at a mark, not a triggered fill
PROD = "K2.5 cap40 (PROD)"


def Vv(label, **o):
    base = dict(atr=True, k=2.5, cap=0.40, flat=0.30, mn=0.12)
    base.update(o)
    return (label, base)


VARIANTS = [
    Vv("K2.5 cap40 (PROD)", k=2.5, cap=0.40),
    Vv("K3.0 cap40", k=3.0, cap=0.40),
    Vv("K3.0 cap50", k=3.0, cap=0.50),
    Vv("K3.0 cap60", k=3.0, cap=0.60),
    Vv("K4.0 cap60", k=4.0, cap=0.60),
    Vv("flat_30 (ATR off)", atr=False, flat=0.30),
]


def apply(o):
    config.POSITION_ATR_STOP_ENABLED = o["atr"]
    config.POSITION_ATR_STOP_K = o["k"]
    config.POSITION_ATR_STOP_MAX_PCT = o["cap"]
    config.POSITION_ATR_STOP_MIN_PCT = o["mn"]
    config.POSITION_INITIAL_STOP_LOSS_PCT = o["flat"]
    M._NEW_MANAGER = M.PositionManager()


def main():
    ap = argparse.ArgumentParser(description="Stop-width OOS + optimism check")
    ap.add_argument("--days", type=float, default=10.0)
    ap.add_argument("--min-conviction", type=float, default=0.18)
    ap.add_argument("--cooldown-h", type=float, default=6.0)
    ap.add_argument("--max-hold-h", type=float, default=3.0)
    args = ap.parse_args()

    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    now = con.execute(
        "SELECT MAX(timestamp) FROM signal_snapshots WHERE price>0").fetchone()[0]
    con.close()
    mid = now - (args.days / 2.0) * 86400  # fixed calendar split point

    per = {}
    for label, o in VARIANTS:
        apply(o)
        t0 = time.time()
        PT.run(args.days, args.min_conviction, args.cooldown_h,
               max_hold_h=args.max_hold_h, quiet=True)
        led = json.load(open(LEDGER))["ledger"]
        per[label] = led
        print(f"[{time.time()-t0:5.0f}s] ran {label}: {len(led)} trades", flush=True)

    def pnl(rows):
        return round(sum(t["pnl_usd"] for t in rows), 2)

    def n2x(rows):
        return sum(1 for t in rows if t.get("peak_mult", 0) >= 2.0)

    # ---- 1) optimism decomposition (full window) ----
    print("\n=== Optimism decomposition (full window) ===")
    print(f"{'variant':20}{'total$':>9}{'held$':>9}{'realized$':>11}"
          f"{'held%trades':>12}{'2x':>5}")
    for label, _ in VARIANTS:
        led = per[label]
        held_rows = [t for t in led if t["reason"] in HELD]
        tot, held, real = pnl(led), pnl(held_rows), pnl(
            [t for t in led if t["reason"] not in HELD])
        hpct = round(100 * len(held_rows) / max(len(led), 1))
        print(f"{label:20}{tot:>9.2f}{held:>9.2f}{real:>11.2f}"
              f"{hpct:>11}%{n2x(led):>5}", flush=True)

    # vs PROD: is the advantage in realized$ (real) or held$ (optimistic)?
    base = per[PROD]
    base_held = pnl([t for t in base if t["reason"] in HELD])
    base_real = pnl([t for t in base if t["reason"] not in HELD])
    print("\nAdvantage vs PROD split into realized vs held:")
    for label, _ in VARIANTS:
        if label == PROD:
            continue
        led = per[label]
        d_held = pnl([t for t in led if t["reason"] in HELD]) - base_held
        d_real = pnl([t for t in led if t["reason"] not in HELD]) - base_real
        print(f"  {label:18} d_realized ${d_real:+8.2f}   d_held ${d_held:+8.2f}",
              flush=True)

    # ---- 2) walk-forward split ----
    print(f"\n=== Walk-forward split @ entry_ts midpoint "
          f"({time.strftime('%m-%d %H:%M', time.gmtime(mid))} UTC) ===")
    print(f"{'variant':20}{'early$':>9}{'late$':>9}{'late2x':>8}")
    rows = []
    for label, _ in VARIANTS:
        led = per[label]
        early = [t for t in led if t["entry_ts"] < mid]
        late = [t for t in led if t["entry_ts"] >= mid]
        rows.append((label, pnl(early), pnl(late), n2x(late)))
        print(f"{label:20}{pnl(early):>9.2f}{pnl(late):>9.2f}{n2x(late):>8}",
              flush=True)

    early_winner = max(rows, key=lambda r: r[1])
    prod_late = next(r[2] for r in rows if r[0] == PROD)
    late_winner = max(rows, key=lambda r: r[2])
    print(f"\nEARLY winner: {early_winner[0]} (early ${early_winner[1]:.2f})")
    print(f"  its LATE PnL ${early_winner[2]:.2f}  vs PROD LATE ${prod_late:.2f}"
          f"  => {'HOLDS OOS' if early_winner[2] >= prod_late else 'OVERFIT'}")
    print(f"LATE winner (hindsight): {late_winner[0]} (${late_winner[2]:.2f})")


if __name__ == "__main__":
    main()
