"""Revival-aware pc24 cap sweep. Tests whether a max-24h-price-change entry cap
cuts the bot's REVIVAL winners (the user's strategy watches revivals, which are
'advanced' tokens) or just non-revival junk.

Variants compared on identical entries (only the cap rule differs), all with the
currently-configured 3x/6x exits:
  - no_cap
  - cap_flat          : reject pc24 > THRESH for ALL tokens
  - cap_exempt_revival: reject pc24 > THRESH only for NON-revival tokens
                        (revival_score >= EXEMPT always allowed through)

If cap_exempt_revival ~= cap_flat -> revivals weren't the winners being cut (cap
is safe). If cap_exempt_revival << cap_flat (toward no_cap) -> the cut >THRESH
revivals were LOSERS, cutting them helps. If cap_exempt_revival >> cap_flat ->
revivals >THRESH are winners; exempt them.

NB: replay marks at snapshot prices -> absolute optimistic; trust deltas/shape.
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
    ap.add_argument("--max-pc24", type=float, required=True, help="cap; 1e9 = no cap")
    ap.add_argument("--exempt-revival-score", type=float, default=-1.0,
                    help=">=0 exempts tokens with revival_score>=this from the cap; -1 = flat cap")
    ap.add_argument("--days", type=float, default=45.0)
    ap.add_argument("--min-conviction", type=float, default=0.18)
    ap.add_argument("--cooldown-h", type=float, default=6.0)
    ap.add_argument("--max-hold-h", type=float, default=12.0)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    THRESH = args.max_pc24
    EXEMPT = args.exempt_revival_score
    _orig = PL.ConvictionPipeline.evaluate

    def patched(self, row):
        alert, reason = _orig(self, row)
        if alert is None:
            return alert, reason
        if THRESH < 1e8:
            v = row.get("price_change_24h")
            try:
                v = float(v) if v is not None else None
            except (TypeError, ValueError):
                v = None
            if v is not None and v > THRESH:
                rs = getattr(alert, "revival_score", 0.0) or 0.0
                if EXEMPT >= 0 and rs >= EXEMPT:
                    return alert, reason  # revival -> exempt
                return None, f"overheated_24h:{v:.0f}"
        return alert, reason

    PL.ConvictionPipeline.evaluate = patched

    out = PT.run(args.days, args.min_conviction, args.cooldown_h,
                 max_hold_h=args.max_hold_h, quiet=True)
    r = out["result"]
    if THRESH >= 1e8:
        label = "no_cap"
    elif EXEMPT >= 0:
        label = f"cap{int(THRESH)}_exemptRev>={EXEMPT:g}"
    else:
        label = f"cap{int(THRESH)}_flat"
    rec = {
        "label": label, "max_pc24": (None if THRESH >= 1e8 else THRESH),
        "exempt_revival_score": (None if EXEMPT < 0 else EXEMPT),
        "trades": r["trades"], "win_rate_pct": r["win_rate_pct"],
        "total_pnl_usd": r["total_pnl_usd"], "profit_factor": r["profit_factor"],
        "best_usd": r["best_usd"], "n_reached_2x": r["n_reached_2x"],
        "still_open_at_end": r.get("still_open_at_end"),
    }
    Path(args.out).write_text(json.dumps(rec))
    print(f"DONE {label}: trades={r['trades']} win%={r['win_rate_pct']} total=${r['total_pnl_usd']} 2x={r['n_reached_2x']}")


if __name__ == "__main__":
    main()
