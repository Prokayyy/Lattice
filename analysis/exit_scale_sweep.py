"""Scale-out sweep (single-variant worker) — replay real signal_snapshots
through the LIVE exit path (discovery.paper_trade.run -> discovery.manager.manage),
varying ONLY the scale-out ladder, to answer: what happens to book profitability /
$ if the first scale-out rung moves to 5x, swept over scale-out percentages.

Designed to be fanned out in parallel (one process per variant) since each replay
is single-core CPU-bound (~10 min). Writes one JSON result line to --out.

NB (per exit_config_backtest): the replay marks on snapshot prices, so ABSOLUTE
PnL is rosier than the live ledger -- trust the DELTAS between variants and the
SHAPE of the sweep, not the absolute total.

Examples:
  env/bin/python analysis/exit_scale_sweep.py --baseline --out /tmp/r_base.json
  env/bin/python analysis/exit_scale_sweep.py --pct 0.5 --be 0 --out /tmp/r_5x50_beoff.json
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config  # noqa: E402
import discovery.manager as M  # noqa: E402
from discovery import paper_trade as PT  # noqa: E402

CUR_LADDER = ((2.0, 0.3), (5.0, 0.5), (10.0, 0.8))
CUR_FLOORS = ((2.0, 1.0), (5.0, 2.0), (10.0, 5.0))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", action="store_true", help="run current live config unchanged")
    ap.add_argument("--pct", type=float, default=0.5, help="cumulative fraction sold at 5x")
    ap.add_argument("--be", type=int, default=0, help="1=enable pre-scale break-even floor")
    ap.add_argument("--days", type=float, default=45.0)
    ap.add_argument("--min-conviction", type=float, default=0.18)
    ap.add_argument("--cooldown-h", type=float, default=6.0)
    ap.add_argument("--max-hold-h", type=float, default=12.0)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    if args.baseline:
        label = "baseline_current"
        config.LATTICE_EXIT_SCALE_OUT_LADDER = CUR_LADDER
        config.LATTICE_EXIT_SCALE_STOP_FLOORS = CUR_FLOORS
        # leave break-even at its live value (False)
    else:
        label = f"5x:{int(args.pct*100)}%_be{'on' if args.be else 'off'}"
        config.LATTICE_EXIT_SCALE_OUT_LADDER = ((5.0, round(args.pct, 2)),)
        config.LATTICE_EXIT_SCALE_STOP_FLOORS = ((5.0, 2.0),)  # lock 2x floor on runner once 5x hit
        config.LATTICE_BREAK_EVEN_EXIT_ENABLED = bool(args.be)

    M._NEW_MANAGER = M.PositionManager()
    out = PT.run(args.days, args.min_conviction, args.cooldown_h,
                 max_hold_h=args.max_hold_h, quiet=True)
    r = out["result"]
    xb = out["exit_breakdown"]
    rec = {
        "label": label,
        "trades": r["trades"],
        "win_rate_pct": r["win_rate_pct"],
        "total_pnl_usd": r["total_pnl_usd"],
        "profit_factor": r["profit_factor"],
        "best_usd": r["best_usd"],
        "worst_usd": r["worst_usd"],
        "n_reached_2x": r["n_reached_2x"],
        "still_open_at_end": r.get("still_open_at_end"),
        "exit_breakdown": xb,
    }
    Path(args.out).write_text(json.dumps(rec))
    print(f"DONE {label}: trades={r['trades']} win%={r['win_rate_pct']} total=${r['total_pnl_usd']}")


if __name__ == "__main__":
    main()
