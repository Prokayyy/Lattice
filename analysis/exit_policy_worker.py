"""General exit-policy worker — replay real signal_snapshots through the LIVE
exit path (discovery.paper_trade.run -> discovery.manager.manage), overriding any
combination of scale ladder / scale-stop-floors / post-scale trail / break-even /
max-hold, to sweep exit-parameter variants. One process per variant (each replay
is ~10 min single-core CPU-bound); writes one JSON result line to --out.

NB: replay marks on snapshot prices, so ABSOLUTE PnL is rosier than the live
ledger (it does NOT model stop slippage) -- trust DELTAS vs baseline and SHAPE.

ladder/floors format: "m:frac,m:frac"  e.g. --ladder "3.0:0.5,6.0:0.9"
Examples:
  exit_policy_worker.py --label baseline --baseline --out /tmp/x.json
  exit_policy_worker.py --label "5x100" --ladder "5.0:1.0" --floors "5.0:2.0" --out /tmp/x.json
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


def parse_pairs(s):
    out = []
    for part in s.split(","):
        part = part.strip()
        if not part:
            continue
        m, v = part.split(":")
        out.append((float(m), float(v)))
    return tuple(sorted(out))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    ap.add_argument("--baseline", action="store_true")
    ap.add_argument("--ladder", default=None, help='e.g. "5.0:0.9" or "3.0:0.5,6.0:0.9"')
    ap.add_argument("--floors", default=None, help='e.g. "5.0:2.0"')
    ap.add_argument("--trail", type=float, default=None, help="POST_SCALE_TRAIL_PCT (e.g. 0.20)")
    ap.add_argument("--be", type=int, default=0)
    ap.add_argument("--days", type=float, default=45.0)
    ap.add_argument("--min-conviction", type=float, default=0.18)
    ap.add_argument("--cooldown-h", type=float, default=6.0)
    ap.add_argument("--max-hold-h", type=float, default=12.0)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    if args.baseline:
        config.LATTICE_EXIT_SCALE_OUT_LADDER = CUR_LADDER
        config.LATTICE_EXIT_SCALE_STOP_FLOORS = CUR_FLOORS
    else:
        config.LATTICE_EXIT_SCALE_OUT_LADDER = parse_pairs(args.ladder)
        config.LATTICE_EXIT_SCALE_STOP_FLOORS = parse_pairs(args.floors) if args.floors else CUR_FLOORS
        config.LATTICE_BREAK_EVEN_EXIT_ENABLED = bool(args.be)
        if args.trail is not None:
            config.LATTICE_POST_SCALE_TRAIL_PCT = args.trail

    M._NEW_MANAGER = M.PositionManager()
    out = PT.run(args.days, args.min_conviction, args.cooldown_h,
                 max_hold_h=args.max_hold_h, quiet=True)
    r = out["result"]
    rec = {
        "label": args.label,
        "trades": r["trades"], "win_rate_pct": r["win_rate_pct"],
        "total_pnl_usd": r["total_pnl_usd"], "profit_factor": r["profit_factor"],
        "best_usd": r["best_usd"], "worst_usd": r["worst_usd"],
        "n_reached_2x": r["n_reached_2x"], "still_open_at_end": r.get("still_open_at_end"),
        "exit_breakdown": out["exit_breakdown"],
    }
    Path(args.out).write_text(json.dumps(rec))
    print(f"DONE {args.label}: trades={r['trades']} win%={r['win_rate_pct']} total=${r['total_pnl_usd']}")


if __name__ == "__main__":
    main()
