"""Backtest the 'give positions room before 2x' exit philosophy vs the prior config.

Replays signal_snapshots through the LIVE exit code path (`discovery.paper_trade.run`
-> `discovery.manager.manage`) on identical entries/tick-streams, rebuilding the
module-global PositionManager per variant via config overrides. Isolates each lever
(remove break-even, remove no-progress, max-hold 6h, first scale 1.5x->2x) and the
combined NEW config, against the OLD config. Strict-early-exit is held OFF in all (it
was already shipped). NB: the replay marks on snapshot prices, so ABSOLUTE PnL runs
optimistic vs the live ledger -- trust RELATIVE deltas (same entries, only exits differ).

Result that shipped (7d, 2026-06-05): NEW lifted total PnL +$35 -> +$432 (PF 1.04 ->
1.51) with win-rate flat (~39%); the gain was pure tail capture -- positions reaching
2x nearly doubled (24 -> 45). It is the COMBINATION: max-hold-6h and ladder->2x are each
negative in isolation, and only pay off once break-even + no-progress are removed so
winners survive long enough to develop. See TRADING_CHANGELOG.md.

Run:  env/bin/python analysis/exit_philosophy_backtest.py --days 7 --min-conviction 0.18
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config  # noqa: E402
import discovery.manager as M  # noqa: E402
from discovery import paper_trade as PT  # noqa: E402

L15 = ((1.5, 0.30), (2.5, 0.50), (4.0, 0.70))   # old first rung 1.5x
L20 = ((2.0, 0.30), (2.5, 0.50), (4.0, 0.70))   # new first rung 2x

# (label, break_even_on, no_progress_on, ladder, max_hold_h)
VARIANTS = [
    ("OLD  (BE+NP, 1.5x, 3h)", True,  True,  L15, 3),
    ("NEW  (no BE/NP, 2x, 6h)", False, False, L20, 6),
    ("only -break_even",       False, True,  L15, 3),
    ("only -no_progress",      True,  False, L15, 3),
    ("only max_hold 6h",       True,  True,  L15, 6),
    ("only ladder 1.5x->2x",   True,  True,  L20, 3),
]


def apply_variant(be, npr, ladder):
    """Override config + rebuild the module-global manager so manage() uses it.
    Strict-early-exit is held OFF in all variants (already shipped)."""
    config.LATTICE_STRICT_EARLY_EXIT_ENABLED = False
    config.LATTICE_BREAK_EVEN_EXIT_ENABLED = be
    config.LATTICE_NO_PROGRESS_EXIT_ENABLED = npr
    config.LATTICE_EXIT_SCALE_OUT_LADDER = ladder
    M._NEW_MANAGER = M.PositionManager()
    return M._NEW_MANAGER


def main():
    ap = argparse.ArgumentParser(description="Exit-philosophy A/B backtest")
    ap.add_argument("--days", type=float, default=7.0)
    ap.add_argument("--min-conviction", type=float, default=0.18)
    ap.add_argument("--cooldown-h", type=float, default=6.0)
    args = ap.parse_args()

    results = []
    for label, be, npr, ladder, mh in VARIANTS:
        m = apply_variant(be, npr, ladder)
        out = PT.run(args.days, args.min_conviction, args.cooldown_h,
                     max_hold_h=mh, quiet=True)
        r = out["result"]
        xb = out["exit_breakdown"]
        results.append((label, r, xb))
        print(f"{label:26s} trades {r['trades']:3d}  win {r['win_rate_pct']:5.1f}%  "
              f"total ${r['total_pnl_usd']:+8.2f}  PF {r['profit_factor']}  "
              f"best ${r['best_usd']:+7.1f} worst ${r['worst_usd']:+6.1f}  "
              f"2x {r['n_reached_2x']:3d}  "
              f"[BE={m.break_even_enabled} NP={m.no_progress_enabled} "
              f"ladder0={m.ladder[0][0]} maxhold={mh}h]")

    base = results[0][1]
    print("\nDelta vs OLD (same entries, only exit policy differs):")
    for label, r, xb in results:
        print(f"  {label:26s} dPnL ${r['total_pnl_usd'] - base['total_pnl_usd']:+8.2f}   "
              f"dWin {r['win_rate_pct'] - base['win_rate_pct']:+5.1f}pp   "
              f"dTrades {r['trades'] - base['trades']:+4d}   "
              f"d2x {r['n_reached_2x'] - base['n_reached_2x']:+3d}   "
              f"total ${r['total_pnl_usd']:+8.2f}")

    print("\nExit breakdown (OLD vs NEW):")
    for label, r, xb in (results[0], results[1]):
        items = ", ".join(f"{k}={v}" for k, v in sorted(xb.items(), key=lambda kv: -kv[1]))
        print(f"  {label}: {items}")


if __name__ == "__main__":
    main()
