"""pc24 entry-cap sweep (single-variant worker). Replays real signal_snapshots
through the LIVE exit path with the CURRENTLY-CONFIGURED exit ladder (now the
2-rung 3x/6x in .env), while gating ENTRIES on a max price_change_24h cap.
Answers: where does an entry overheating cap trim fade-losers without knifing
runners?

The cap is injected by monkeypatching ConvictionPipeline.evaluate to reject
rows with price_change_24h > threshold (the production pipeline has only a 1h
cap today; this previews a 24h cap without touching prod code).

Runner-retention signal: n_reached_2x and best_usd — if total $ improves but
those collapse, the cap is knifing runners.

NB: replay marks at snapshot prices -> absolute optimistic; trust DELTAS + shape.

Examples:
  pc24_cap_sweep.py --max-pc24 1e9 --out /tmp/c_base.json   # no cap (baseline)
  pc24_cap_sweep.py --max-pc24 200 --out /tmp/c_200.json
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import discovery.pipeline as PL  # noqa: E402
from discovery import paper_trade as PT  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-pc24", type=float, required=True, help="reject entries with price_change_24h above this; 1e9 = no cap")
    ap.add_argument("--days", type=float, default=45.0)
    ap.add_argument("--min-conviction", type=float, default=0.18)
    ap.add_argument("--cooldown-h", type=float, default=6.0)
    ap.add_argument("--max-hold-h", type=float, default=12.0)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    THRESH = args.max_pc24
    _orig = PL.ConvictionPipeline.evaluate

    def patched(self, row):
        if THRESH < 1e8:
            v = row.get("price_change_24h")
            try:
                v = float(v) if v is not None else None
            except (TypeError, ValueError):
                v = None
            if v is not None and v > THRESH:
                return None, f"overheated_24h:{v:.0f}>{THRESH:.0f}"
        return _orig(self, row)

    PL.ConvictionPipeline.evaluate = patched

    out = PT.run(args.days, args.min_conviction, args.cooldown_h,
                 max_hold_h=args.max_hold_h, quiet=True)
    r = out["result"]
    label = "no_cap" if THRESH >= 1e8 else f"pc24<={int(THRESH)}"
    rec = {
        "label": label, "max_pc24": (None if THRESH >= 1e8 else THRESH),
        "trades": r["trades"], "win_rate_pct": r["win_rate_pct"],
        "total_pnl_usd": r["total_pnl_usd"], "profit_factor": r["profit_factor"],
        "best_usd": r["best_usd"], "worst_usd": r["worst_usd"],
        "n_reached_2x": r["n_reached_2x"], "still_open_at_end": r.get("still_open_at_end"),
    }
    Path(args.out).write_text(json.dumps(rec))
    print(f"DONE {label}: trades={r['trades']} win%={r['win_rate_pct']} total=${r['total_pnl_usd']} 2x={r['n_reached_2x']}")


if __name__ == "__main__":
    main()
