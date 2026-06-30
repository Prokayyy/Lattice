"""A/B backtest for discovery exit-config variants (esp. strict_early_failure_exit).

Replays signal_snapshots through the LIVE exit code path (`discovery.paper_trade.run`,
which calls `discovery.manager.manage`) on identical entries/tick-streams, varying only
the strict-early-exit config between runs. Rebuilds the module-global PositionManager
per variant via config overrides, so each run uses a different exit policy on the very
same trades.

Use this to decide exit-tuning changes from data instead of intuition. NB: the replay
marks on snapshot prices, so ABSOLUTE PnL runs more optimistic than the live ledger --
trust the RELATIVE deltas between variants (same entries, only exits differ).

Result that shipped (7d, 2026-06-05): disabling strict_early_failure_exit beat the
current config by ~+$69 / +7pp win-rate with worst-case-per-trade unchanged; partial
tunings (3/3 ticks, -18% loss) were noisy. See TRADING_CHANGELOG.md.

Run:  env/bin/python analysis/exit_config_backtest.py --days 7 --min-conviction 0.18
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

# (label, strict-exit overrides). Each fully specifies the four knobs so nothing
# leaks across iterations. EN=enabled, W=min weak signals, T=confirm ticks, L=loss pct.
VARIANTS = [
    ("A_current  (2/2/0.12)", dict(EN=True,  W=2, T=2, L=0.12)),
    ("B1_precision(3/3/0.12)", dict(EN=True,  W=3, T=3, L=0.12)),
    ("B2_room    (2/2/0.18)", dict(EN=True,  W=2, T=2, L=0.18)),
    ("B3_both    (3/3/0.18)", dict(EN=True,  W=3, T=3, L=0.18)),
    ("B4_disabled         ", dict(EN=False, W=2, T=2, L=0.12)),
]


def apply_variant(o):
    """Override config + rebuild the module-global manager so manage() uses it."""
    config.LATTICE_STRICT_EARLY_EXIT_ENABLED = o["EN"]
    config.LATTICE_STRICT_EARLY_EXIT_MIN_WEAK_SIGNALS = o["W"]
    config.LATTICE_STRICT_EARLY_EXIT_CONFIRM_TICKS = o["T"]
    config.LATTICE_STRICT_EARLY_EXIT_LOSS_PCT = o["L"]
    M._NEW_MANAGER = M.PositionManager()  # re-reads config at __init__
    return M._NEW_MANAGER


def main():
    ap = argparse.ArgumentParser(description="Exit-config A/B backtest")
    ap.add_argument("--days", type=float, default=7.0)
    ap.add_argument("--min-conviction", type=float, default=0.18)
    ap.add_argument("--cooldown-h", type=float, default=6.0)
    ap.add_argument("--max-hold-h", type=float, default=3.0)
    args = ap.parse_args()

    results = []
    for label, o in VARIANTS:
        m = apply_variant(o)
        out = PT.run(args.days, args.min_conviction, args.cooldown_h,
                     max_hold_h=args.max_hold_h, quiet=True)
        r = out["result"]
        xb = out["exit_breakdown"]
        results.append((label, r, xb))
        eff = (f"en={m.strict_enabled} W={m.strict_min_weak} "
               f"T={m.strict_confirm_ticks} L={m.strict_loss_pct}")
        print(f"{label}  trades {r['trades']:3d}  win {r['win_rate_pct']:5.1f}%  "
              f"total ${r['total_pnl_usd']:+8.2f}  PF {r['profit_factor']}  "
              f"best ${r['best_usd']:+7.1f} worst ${r['worst_usd']:+6.1f}  "
              f"2x {r['n_reached_2x']:3d}  strict_cuts "
              f"{xb.get('strict_early_failure_exit', 0):3d}   [{eff}]")

    base = results[0][1]
    print("\nDelta vs first variant (same entries, only exits differ):")
    for label, r, xb in results:
        print(f"  {label}  dPnL ${r['total_pnl_usd'] - base['total_pnl_usd']:+8.2f}   "
              f"dWin {r['win_rate_pct'] - base['win_rate_pct']:+5.1f}pp   "
              f"total ${r['total_pnl_usd']:+8.2f}")

    print("\nFull exit breakdown per variant:")
    for label, r, xb in results:
        items = ", ".join(f"{k}={v}" for k, v in sorted(xb.items(), key=lambda kv: -kv[1]))
        print(f"  {label}: {items}")


if __name__ == "__main__":
    main()
