"""Sweep the min-substance (a.k.a. min-lattice) entry floor and report which
value is most profitable for the alerts it produces.

WHAT "substance" IS
  The lattice composite score in [0,1] (discovery/lattice.py:lattice_verdict).
  It is gated live by ConvictionPipeline via `min_lattice`
  (config LATTICE_MIN_ENTRY_LATTICE, default 0.0 = OFF).

FAITHFUL REPLAY
  The live pipeline (discovery/live_runner.py) builds ConvictionPipeline WITHOUT
  a participation provider, so `pipe.evaluate` — and therefore the `min_lattice`
  floor — gates on the PARTICIPATION-BLIND composite (flow+liquidity+structure).
  Breadth is applied separately as `min_breadth` (default -0.4). We mirror that:
    * composite computed participation-blind (the knob the floor controls)
    * breadth gate: drop breadth < -0.4 (keep None/blind), as live does
    * 2h per-token alert cooldown applied AFTER the floor, as live does
      (discovery_outcomes logs every ~10s conviction-survivor TICK, not alerts)

DATA
  discovery/discovery_outcomes.jsonl — every conviction survivor (min_conviction
  0.18, lattice-veto already passed) with forward realized PnL under the live
  exit engine ($20/alert) plus max-multiple windows. This is the alert
  population; the substance floor is an additional filter on it.

Run:  env/bin/python -m analysis.substance_floor_sweep
"""
import csv
import json
import os
import statistics

from discovery.lattice import lattice_verdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "discovery", "discovery_outcomes.jsonl")
CSV_OUT = os.path.join(os.path.dirname(__file__), "substance_floor_sweep_results.csv")

MIN_BREADTH = -0.4          # live default (LATTICE paper buy / runner min_breadth)
COOLDOWN_S = 2 * 3600       # live ENTRY SIGNAL per-token cooldown
GRID = [0.0, 0.45, 0.50, 0.55, 0.58, 0.60, 0.62, 0.64, 0.66, 0.68,
        0.70, 0.72, 0.74, 0.76, 0.78, 0.80]


def load():
    rows = []
    with open(OUT) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            if r.get("no_data"):
                continue
            rows.append(r)
    return rows


def prep(rows):
    """Attach blind composite; apply the live breadth gate (drop br < -0.4,
    keep blind/None)."""
    out = []
    for r in rows:
        br = r.get("breadth")
        if br is not None and br < MIN_BREADTH:
            continue
        snap = r.get("row") or {}
        sv = lattice_verdict(snap, participation=None,
                             liquidity_change_pct=snap.get("liquidity_change_pct"))
        if not sv["passed"]:          # lattice veto (all survivors pass; guard anyway)
            continue
        out.append({
            "token": r["token"],
            "ts": float(r["alert_ts"]),
            "composite": sv["composite"],
            "pnl": float(r.get("realized_pnl") or 0.0),
            "mm1h": float(r.get("max_mult_1h") or 0.0),
            "peak": float(r.get("peak_mult") or 0.0),
        })
    out.sort(key=lambda x: x["ts"])
    return out


def dedup(rows):
    """2h per-token cooldown: keep the first qualifying tick per token per 2h."""
    last = {}
    kept = []
    for r in rows:
        t, ts = r["token"], r["ts"]
        if t in last and ts - last[t] < COOLDOWN_S:
            continue
        last[t] = ts
        kept.append(r)
    return kept


def metrics(alerts):
    n = len(alerts)
    if not n:
        return None
    pnls = [a["pnl"] for a in alerts]
    tot = sum(pnls)
    wins = sum(1 for p in pnls if p > 0)
    ge2 = sum(1 for a in alerts if a["mm1h"] >= 2.0)
    return {
        "n": n,
        "total_pnl": tot,
        "mean_pnl": tot / n,
        "win_rate": wins / n,
        "ge2_1h": ge2 / n,
        "median_peak": statistics.median(a["peak"] for a in alerts),
    }


def main():
    rows = prep(load())
    base_alerts = len(dedup(rows))
    print(f"alert population (post breadth-gate, pre-floor, ticks): {len(rows)}")
    print(f"baseline alerts after 2h cooldown (floor=0.00): {base_alerts}\n")

    header = ("floor", "alerts", "ret%", "total_pnl", "mean_pnl",
              "win%", ">=2x@1h%", "med_peak")
    print("{:>6} {:>7} {:>6} {:>11} {:>9} {:>6} {:>8} {:>9}".format(*header))
    print("-" * 70)

    table = []
    for t in GRID:
        alerts = dedup([r for r in rows if r["composite"] >= t])
        m = metrics(alerts)
        if not m:
            continue
        row = {
            "floor": t,
            "alerts": m["n"],
            "ret_pct": 100.0 * m["n"] / base_alerts,
            "total_pnl": m["total_pnl"],
            "mean_pnl": m["mean_pnl"],
            "win_pct": 100.0 * m["win_rate"],
            "ge2_pct": 100.0 * m["ge2_1h"],
            "med_peak": m["median_peak"],
        }
        table.append(row)
        print("{floor:>6.2f} {alerts:>7d} {ret_pct:>5.0f}% "
              "{total_pnl:>+11.2f} {mean_pnl:>+9.3f} {win_pct:>5.1f}% "
              "{ge2_pct:>7.1f}% {med_peak:>9.3f}".format(**row))

    with open(CSV_OUT, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(table[0].keys()))
        w.writeheader()
        w.writerows(table)

    best_total = max(table, key=lambda r: r["total_pnl"])
    # per-alert quality, but require a non-trivial sample (>=20% of baseline)
    elig = [r for r in table if r["alerts"] >= 0.20 * base_alerts]
    best_mean = max(elig, key=lambda r: r["mean_pnl"])

    print("\n=== BEST ===")
    print(f"By TOTAL realized PnL : floor={best_total['floor']:.2f} -> "
          f"${best_total['total_pnl']:+.2f} over {best_total['alerts']} alerts "
          f"(mean ${best_total['mean_pnl']:+.3f}/alert, win {best_total['win_pct']:.1f}%)")
    print(f"By MEAN PnL/alert     : floor={best_mean['floor']:.2f} -> "
          f"${best_mean['mean_pnl']:+.3f}/alert over {best_mean['alerts']} alerts "
          f"(total ${best_mean['total_pnl']:+.2f}, win {best_mean['win_pct']:.1f}%)")
    print(f"\nCSV written: {CSV_OUT}")


if __name__ == "__main__":
    main()
