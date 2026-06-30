"""Sweep the INITIAL-STOP policy (flat % and adaptive downside-ATR cap/K) over the
LIVE exit code path.

Same harness as exit_config_backtest.py: replays signal_snapshots through
discovery.paper_trade.run -> discovery.manager.manage, rebuilding the global
PositionManager per variant. ONLY the initial-stop knobs change between runs --
the scale ladder, step floors, break-even, strict-early and no-progress exits are
held at config defaults across ALL variants, so any delta is attributable to stop
sizing alone.

NB (same caveat as the sibling backtest): the replay marks on snapshot prices, so
ABSOLUTE PnL is optimistic vs the live ledger. Trust the RELATIVE deltas between
variants. n_reached_2x is the runner-survival metric: if a tighter stop drops it,
the stop is cutting winners off on early dips before they run.

Findings so far (10-day window, ~940 trades): WIDER = better, monotonic on BOTH K
and cap; ATR-on beats flat by ~$216; best tested was K3.0/cap40 (+$934) and the
gradient had NOT peaked. This grid hunts the knee at the wide end.

Run:  env/bin/python analysis/stop_sweep.py --days 10 --min-conviction 0.18
"""
import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config  # noqa: E402
import discovery.manager as M  # noqa: E402
from discovery import paper_trade as PT  # noqa: E402


def V(label, *, atr, k=2.5, cap=0.40, flat=0.30, mn=0.12):
    return (label, dict(atr=atr, k=k, cap=cap, flat=flat, mn=mn))


# Wide-end grid: the prior 10-day sweep found WIDER = better (monotonic on K and
# cap), best tested K3.0/cap40, gradient not peaked. This run hunts the knee via
# two clean axes through (K3.0, cap40) plus a far corner. K3.0/cap40 repeats as a
# determinism cross-check (should reproduce ~$934). MIN_PCT held at 0.12.
VARIANTS = [
    V("atr K2.5 cap40 (PROD)", atr=True, k=2.5, cap=0.40),
    V("atr K3.0 cap40      ", atr=True, k=3.0, cap=0.40),
    V("atr K3.5 cap40      ", atr=True, k=3.5, cap=0.40),
    V("atr K4.0 cap40      ", atr=True, k=4.0, cap=0.40),
    V("atr K3.0 cap50      ", atr=True, k=3.0, cap=0.50),
    V("atr K3.0 cap60      ", atr=True, k=3.0, cap=0.60),
    V("atr K4.0 cap60      ", atr=True, k=4.0, cap=0.60),
    # Extended per user request to hunt the knee
    V("atr K5.0 cap60      ", atr=True, k=5.0, cap=0.60),
    V("atr K5.0 cap70      ", atr=True, k=5.0, cap=0.70),
    V("atr K6.0 cap80      ", atr=True, k=6.0, cap=0.80),
    V("atr K8.0 cap100     ", atr=True, k=8.0, cap=1.00),
]

PROD_INDEX = 0  # baseline for deltas (current production config)


def apply(o):
    config.POSITION_ATR_STOP_ENABLED = o["atr"]
    config.POSITION_ATR_STOP_K = o["k"]
    config.POSITION_ATR_STOP_MAX_PCT = o["cap"]
    config.POSITION_ATR_STOP_MIN_PCT = o["mn"]
    config.POSITION_INITIAL_STOP_LOSS_PCT = o["flat"]
    M._NEW_MANAGER = M.PositionManager()  # re-reads config at __init__
    return M._NEW_MANAGER


def main():
    ap = argparse.ArgumentParser(description="Initial-stop cap/K sweep")
    ap.add_argument("--days", type=float, default=10.0)
    ap.add_argument("--min-conviction", type=float, default=0.18)
    ap.add_argument("--cooldown-h", type=float, default=6.0)
    ap.add_argument("--max-hold-h", type=float, default=3.0)
    ap.add_argument("--limit", type=int, default=0,
                    help="run only the first N variants (0 = all)")
    args = ap.parse_args()

    variants = VARIANTS[:args.limit] if args.limit else VARIANTS

    def stops(xb):
        return (xb.get("initial_stop", 0)
                + xb.get("break_even_floor", 0)
                + xb.get("scale_stop_floor", 0))

    results = []
    for i, (label, o) in enumerate(variants, 1):
        apply(o)
        t0 = time.time()
        out = PT.run(args.days, args.min_conviction, args.cooldown_h,
                     max_hold_h=args.max_hold_h, quiet=True)
        dt = time.time() - t0
        r = out["result"]
        xb = out.get("exit_breakdown", {})
        results.append((label, r, xb))
        print(f"[{i}/{len(variants)} {dt:5.1f}s] {label}  trades {r['trades']:3d}  "
              f"win {r['win_rate_pct']:5.1f}%  "
              f"total ${r['total_pnl_usd']:+9.2f}  PF {r.get('profit_factor')}  "
              f"best ${r['best_usd']:+7.1f} worst ${r['worst_usd']:+6.1f}  "
              f"2x {r.get('n_reached_2x', '?')}  stop-exits {stops(xb)}",
              flush=True)

    if len(results) < 2:
        return
    base_idx = PROD_INDEX if len(results) > PROD_INDEX else 0
    base = results[base_idx][1]
    print(f"\nDelta vs ({results[base_idx][0].strip()}) "
          f"-- same entries, only the initial stop differs:")
    for label, r, xb in results:
        d2x = r.get("n_reached_2x", 0) - base.get("n_reached_2x", 0)
        print(f"  {label}  dPnL ${r['total_pnl_usd'] - base['total_pnl_usd']:+9.2f}"
              f"   dWin {r['win_rate_pct'] - base['win_rate_pct']:+5.1f}pp"
              f"   d2x {d2x:+d}   total ${r['total_pnl_usd']:+9.2f}")


if __name__ == "__main__":
    main()
