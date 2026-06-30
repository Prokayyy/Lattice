"""Offline forward-outcome harness for the scanner gate redesign (no-shadow gate).

The redesign skips the live shadow phase, so THIS script is the promotion gate
re-run before each binding layer. It re-scores the historical forward outcomes
already recorded in ``discovery_outcomes.jsonl`` (entry-time row + realized
forward multiples) and reports, per candidate hard veto, how cleanly it removes
duds without sacrificing big winners.

Provenance: the prior-session "§9" script and its exact eff/dead anchors are not
in the repo and were not reproducible from the outcome data under any natural
metric definition (swept BIG x dead x eff formulas; closest fit missed the
anchors). Per an explicit decision, this harness uses TRANSPARENT, repo-standard
definitions and a DIRECTIONAL promotion bar instead of exact-anchor reproduction:

  BIG  (a winner)  = max_mult_1h >= BIG_MULT           (default 2.0; matches the
                     existing convention in discovery/outcomes.py and
                     discovery/eval_outcomes.py)
  DEAD (a dud)     = exit_reason == "initial_stop"     (knifed at the entry stop)

  PROMOTION BAR (Layer 1): every enabled veto's REMOVED group must be materially
  deadier AND lower BIG-rate than the base population, and the combined survivor
  set must retain >= MIN_RECALL of all token-deduped BIG winners (default 0.90).

Reported eff numbers are descriptive (three interpretations printed side by
side), NOT the gate — the gate is the directional bar above.

SolanaTracker vetoes (V1-V3) are validated only on the small subset of outcomes
that have a bundle_evidence.jsonl match (joined on (token, int(alert_ts))); that
sample is thin, so they are shipped knob-gated and treated as provisional.

Reads only JSONL (no DB, no replay, touches no live state).

Run:  python3 discovery/redesign_validate.py
      python3 discovery/redesign_validate.py --json out.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from discovery.jsonl_archive import iter_records, live_path  # noqa: E402

try:
    import config  # source of truth for veto thresholds (env-tunable)
except Exception:                                            # noqa: BLE001
    config = None

BUNDLE_EVIDENCE = os.path.join(os.path.dirname(__file__), "bundle_evidence.jsonl")

BIG_MULT = 2.0
MIN_RECALL = 0.90


# ---- knob access (mirror live config so harness and gate use identical values)

def _knob(name, default):
    if config is not None:
        v = getattr(config, name, None)
        if v is not None:
            return v
    return default


# ---- field coercion helpers ------------------------------------------------

def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _row(o):
    return o.get("row") or {}


def _rf(o):
    """risk_flags as a list, coercing the stored JSON-string form."""
    rf = _row(o).get("risk_flags")
    if isinstance(rf, str):
        try:
            rf = json.loads(rf)
        except json.JSONDecodeError:
            rf = []
    return rf or []


def _rget(o, key):
    return _f(_row(o).get(key))


# ---- labels ----------------------------------------------------------------

def is_big(o):
    return (_f(o.get("max_mult_1h")) or 0.0) >= BIG_MULT


def is_dead(o):
    return o.get("exit_reason") == "initial_stop"


# ---- veto predicates (True => this veto would REMOVE the candidate) ---------
# V1-V3 take the joined SolanaTracker evidence dict (or None when unmatched).
# Degrade-blind: if ST status != ok (or no match), V1-V3 never fire.

def _st_ok(st):
    return bool(st) and st.get("status") == "ok"


def v1_bundle(o, st):
    if not _st_ok(st):
        return False
    thr = float(_knob("LATTICE_BUNDLE_REJECT_BUNDLE_PCT", 25.0))
    return (_f(st.get("current_bundle_pct")) or 0.0) >= thr


def v2_risk_high(o, st):
    if not _st_ok(st):
        return False
    want = str(_knob("LATTICE_BUNDLE_REJECT_RISK_LEVEL", "high")).lower()
    return str(st.get("risk_level", "")).lower() == want


def v3_sniped(o, st):
    if not _st_ok(st):
        return False
    return (_f(st.get("sniper_pct")) or 0.0) > 0.0


def v4_flag_stack(o, st):
    return len(_rf(o)) >= int(_knob("LATTICE_MAX_RISK_FLAGS", 4))


def v5_sell_pressure(o, st):
    return "sell_pressure" in _rf(o)


def v6_weak_pc5(o, st):
    floor = float(_knob("LATTICE_PAPER_BUY_MIN_PRICE_CHANGE_5M", 4.0))
    pc5 = _rget(o, "price_change_5m")
    return pc5 is not None and pc5 < floor


def w_wash(o, st):
    """Refined wash veto (moved from lattice to the capital lane): few distinct
    buyers. Uses the entry-time buyers_sig recorded on the outcome."""
    bs = _f(o.get("buyers_sig"))
    return bs is not None and bs < float(_knob("LATTICE_VETO_BUYERS_SIG_MIN", -0.3))


def w_deepfader(o, st):
    """Deep-fader veto only in the -40..-15 band; exempt < -40 (capitulation
    rebounds) and >= -15 (shallow)."""
    pc1h = _rget(o, "price_change_1h")
    if pc1h is None:
        return False
    return -40.0 <= pc1h < -15.0


VETOES = [
    ("V1_bundle>=pct", v1_bundle, "st"),
    ("V2_risk_high", v2_risk_high, "st"),
    ("V3_sniped", v3_sniped, "st"),
    ("V4_flag_stack", v4_flag_stack, "row"),
    ("V5_sell_pressure", v5_sell_pressure, "row"),
    ("V6_weak_pc5", v6_weak_pc5, "row"),
    ("W_wash_buyers_sig", w_wash, "row"),
    ("W_deep_fader", w_deepfader, "row"),
]


# ---- data load -------------------------------------------------------------

def load_outcomes():
    out = []
    for o in iter_records(live_path("discovery_outcomes.jsonl")):
        if o.get("no_data"):
            continue
        out.append(o)
    return out


def load_bundle_index():
    idx = {}
    if not os.path.exists(BUNDLE_EVIDENCE):
        return idx
    with open(BUNDLE_EVIDENCE) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                b = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = _f(b.get("alert_ts"))
            if ts is None:
                continue
            idx[(b.get("token"), int(ts))] = b
    return idx


def st_for(o, bidx):
    ts = _f(o.get("alert_ts"))
    if ts is None:
        return None
    return bidx.get((o.get("token"), int(ts)))


# ---- metrics ---------------------------------------------------------------

def _rate(rows, pred):
    return (sum(1 for r in rows if pred(r)) / len(rows)) if rows else 0.0


def _big_tokens(rows):
    return {o.get("token") for o in rows if is_big(o)}


def veto_report(outs, bidx, name, fn, scope):
    """Population for an ST veto is only the ST-matched subset; for a row veto
    it is the full outcome set."""
    if scope == "st":
        pop = [o for o in outs if _st_ok(st_for(o, bidx))]
    else:
        pop = outs
    if not pop:
        return None
    removed = [o for o in pop if fn(o, st_for(o, bidx))]
    kept = [o for o in pop if not fn(o, st_for(o, bidx))]
    base_dead = _rate(pop, is_dead)
    base_big = _rate(pop, is_big)
    rem_dead = _rate(removed, is_dead)
    rem_big = _rate(removed, is_big)
    big_removed = sum(1 for o in removed if is_big(o))
    dead_removed = sum(1 for o in removed if is_dead(o))
    return {
        "veto": name,
        "scope": scope,
        "pop": len(pop),
        "removed": len(removed),
        "removed_pct": 100.0 * len(removed) / len(pop),
        "base_dead_rate": base_dead,
        "removed_dead_rate": rem_dead,
        "kept_dead_rate": _rate(kept, is_dead),
        "base_big_rate": base_big,
        "removed_big_rate": rem_big,
        # descriptive eff interpretations (NOT the gate)
        "eff_dead_per_big_removed": (dead_removed / big_removed) if big_removed else None,
        "eff_deadrate_lift": (rem_dead / base_dead) if base_dead else None,
        "eff_bigrate_suppression": (base_big / rem_big) if rem_big else None,
        # directional bar inputs
        "deadier_than_base": rem_dead > base_dead,
        "lower_big_than_base": rem_big < base_big,
    }


def combined_report(outs, bidx, enabled_names):
    """Apply all enabled vetoes together (an outcome is removed if ANY fires)
    and measure token-deduped BIG recall of the survivors over the FULL set."""
    fns = [(n, fn, sc) for (n, fn, sc) in VETOES if n in enabled_names]
    survivors = []
    for o in outs:
        st = st_for(o, bidx)
        removed = any(fn(o, st) for (_n, fn, _sc) in fns)
        if not removed:
            survivors.append(o)
    all_big = _big_tokens(outs)
    surv_big = _big_tokens(survivors)
    recall = (len(surv_big) / len(all_big)) if all_big else 0.0
    return {
        "n": len(outs),
        "survivors": len(survivors),
        "removed": len(outs) - len(survivors),
        "base_dead_rate": _rate(outs, is_dead),
        "survivor_dead_rate": _rate(survivors, is_dead),
        "big_tokens_total": len(all_big),
        "big_tokens_survived": len(surv_big),
        "token_deduped_big_recall": recall,
    }


def scorecard_tiers(outs, bidx, pct_cutoff=60.0):
    """Layer-2 validation: score every outcome with discovery.scorecard, assign
    Tier A = score >= the given percentile AND clears the absolute Tier-A floors,
    and report Tier-A BIG-rate (win%) vs base. The bar is Tier-A win% > base."""
    try:
        from discovery import scorecard as SC
    except Exception as e:                                   # noqa: BLE001
        return {"error": f"scorecard import failed: {type(e).__name__}: {e}"}

    scored = []
    for o in outs:
        st = st_for(o, bidx)
        detail = {"buyers_sig": o.get("buyers_sig")}
        sc = SC.score(_row(o), detail=detail, st_bundle=st,
                      conviction=_f(o.get("conviction")))
        floors_ok, _r = SC.passes_tier_a_floors(_row(o), detail=detail, st_bundle=st)
        scored.append((sc["score"], floors_ok, o))

    vals = sorted(s for s, _fl, _o in scored)
    if not vals:
        return {"error": "no scores"}
    idx = int(pct_cutoff / 100.0 * (len(vals) - 1))
    cut = vals[idx]

    tier_a = [o for s, fl, o in scored if s >= cut and fl]
    base_big = _rate(outs, is_big)
    a_big = _rate(tier_a, is_big)
    return {
        "pct_cutoff": pct_cutoff,
        "score_cut": cut,
        "tier_a_n": len(tier_a),
        "tier_a_win_pct": 100.0 * a_big,
        "base_win_pct": 100.0 * base_big,
        "tier_a_dead_rate": 100.0 * _rate(tier_a, is_dead),
        "base_dead_rate": 100.0 * _rate(outs, is_dead),
        "lift": (a_big / base_big) if base_big else None,
    }


TRADES_LEDGER = os.path.join(os.path.dirname(__file__), "trades.jsonl")


def tier_exit_report():
    """Layer-3 diagnostics: per entry-tier exit quality from the trade ledger
    (initial-stop rate, realized PnL, MFE = mean peak_mult). Trades predating the
    tier field are bucketed as 'untiered'."""
    if not os.path.exists(TRADES_LEDGER):
        return None
    by = {}
    with open(TRADES_LEDGER) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                t = json.loads(line)
            except json.JSONDecodeError:
                continue
            tier = t.get("entry_tier") or "untiered"
            b = by.setdefault(tier, {"n": 0, "stops": 0, "pnl": 0.0, "peak": 0.0})
            b["n"] += 1
            if t.get("reason") == "initial_stop":
                b["stops"] += 1
            b["pnl"] += _f(t.get("pnl_usd")) or 0.0
            b["peak"] += _f(t.get("peak_mult")) or 0.0
    return by


def conviction_decile_dead(outs):
    """The motivating finding: does conviction rank outcomes? Dead-rate per
    conviction decile (top decile worst => float does not rank)."""
    scored = [(_f(o.get("conviction")) or 0.0, o) for o in outs]
    scored.sort(key=lambda x: x[0])
    n = len(scored)
    out = []
    for d in range(10):
        lo = d * n // 10
        hi = (d + 1) * n // 10
        chunk = [o for _c, o in scored[lo:hi]]
        if not chunk:
            continue
        out.append({
            "decile": d + 1,
            "conv_lo": round(scored[lo][0], 4),
            "conv_hi": round(scored[hi - 1][0], 4),
            "dead_rate": round(_rate(chunk, is_dead), 4),
            "big_rate": round(_rate(chunk, is_big), 4),
        })
    return out


def main():
    global BIG_MULT, MIN_RECALL
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", help="write the full report as JSON to this path")
    ap.add_argument("--big-mult", type=float, default=BIG_MULT)
    ap.add_argument("--min-recall", type=float, default=MIN_RECALL)
    args = ap.parse_args()

    BIG_MULT = args.big_mult
    MIN_RECALL = args.min_recall

    outs = load_outcomes()
    bidx = load_bundle_index()
    st_matched = sum(1 for o in outs if _st_ok(st_for(o, bidx)))

    print(f"outcomes (data-bearing): {len(outs)}")
    print(f"bundle_evidence rows: {len(bidx)} | ST-ok matched outcomes: {st_matched}")
    print(f"BIG = max_mult_1h>={BIG_MULT} | DEAD = exit_reason==initial_stop")
    base_big = _rate(outs, is_big)
    base_dead = _rate(outs, is_dead)
    print(f"base BIG-rate {100*base_big:.1f}% | base DEAD-rate {100*base_dead:.1f}%\n")

    print("== conviction-decile dead-rate (does the float rank outcomes?) ==")
    for r in conviction_decile_dead(outs):
        print(f"  d{r['decile']:>2} conv[{r['conv_lo']:.3f}..{r['conv_hi']:.3f}] "
              f"dead {100*r['dead_rate']:5.1f}%  big {100*r['big_rate']:4.1f}%")
    print()

    print("== per-veto (directional bar: deadier AND lower-big than base) ==")
    reports = []
    passes = {}
    for name, fn, scope in VETOES:
        rep = veto_report(outs, bidx, name, fn, scope)
        if rep is None:
            continue
        reports.append(rep)
        ok = rep["deadier_than_base"] and rep["lower_big_than_base"]
        passes[name] = ok
        edpb = rep["eff_dead_per_big_removed"]
        edpb_s = f"{edpb:.2f}" if edpb is not None else "  inf"
        print(f"  {name:<20} pop={rep['pop']:>5} rm={rep['removed']:>4} "
              f"({rep['removed_pct']:4.1f}%)  "
              f"dead {100*rep['removed_dead_rate']:5.1f}% vs {100*rep['base_dead_rate']:4.1f}%  "
              f"big {100*rep['removed_big_rate']:4.1f}% vs {100*rep['base_big_rate']:4.1f}%  "
              f"dead/big={edpb_s}  {'PASS' if ok else 'FAIL'}")
    print()

    # The binding default set = vetoes that clear the per-veto directional bar.
    # Vetoes that fail it (thin ST sample, or fizzle-not-stop filters) still
    # ship as knobs but DEFAULT-OFF; they are excluded from the combined gate.
    enabled = [n for n in passes if passes[n]]
    comb = combined_report(outs, bidx, enabled)
    recall_ok = comb["token_deduped_big_recall"] >= MIN_RECALL
    dead_ok = comb["survivor_dead_rate"] <= comb["base_dead_rate"]
    print("== combined (default-binding = directionally-passing vetoes) ==")
    print(f"  default-on : {', '.join(enabled) or '(none)'}")
    knob_off = [n for n in passes if not passes[n]]
    print(f"  knob-off   : {', '.join(knob_off) or '(none)'}  (ship gated, off by default)")
    print(f"  survivors {comb['survivors']}/{comb['n']} | "
          f"survivor dead-rate {100*comb['survivor_dead_rate']:.1f}% "
          f"(base {100*comb['base_dead_rate']:.1f}%)  {'PASS' if dead_ok else 'FAIL'}")
    print(f"  token-deduped BIG recall {100*comb['token_deduped_big_recall']:.1f}% "
          f"(bar >= {100*MIN_RECALL:.0f}%)  {'PASS' if recall_ok else 'FAIL'}")

    # GATE = the combined default-on set keeps recall AND does not worsen dud
    # rate. Per-veto PASS/FAIL is a diagnostic that selects the default-on set,
    # not an all-must-pass requirement.
    gate_pass = recall_ok and dead_ok
    print(f"\nLAYER-1 GATE (combined default-on set): {'PASS' if gate_pass else 'FAIL'}")

    print("\n== Layer-2 scorecard tiers (Tier-A win% must beat base) ==")
    l2_pass = None
    for cut in (50.0, 60.0, 70.0):
        t = scorecard_tiers(outs, bidx, pct_cutoff=cut)
        if "error" in t:
            print(f"  {t['error']}")
            break
        ok = t["tier_a_win_pct"] > t["base_win_pct"]
        if cut == 60.0:
            l2_pass = ok
        print(f"  p{int(cut)} cut={t['score_cut']:+.2f}  TierA n={t['tier_a_n']:>5}  "
              f"win {t['tier_a_win_pct']:4.1f}% vs base {t['base_win_pct']:4.1f}%  "
              f"(lift {t['lift']:.2f}x)  dead {t['tier_a_dead_rate']:4.1f}% vs "
              f"{t['base_dead_rate']:4.1f}%  {'PASS' if ok else 'FAIL'}")
    if l2_pass is not None:
        print(f"LAYER-2 GATE (Tier-A @ p60 win% > base): {'PASS' if l2_pass else 'FAIL'}")

    print("\n== Layer-3 per-tier exit quality (trade ledger) ==")
    tex = tier_exit_report()
    if not tex:
        print("  (no trades.jsonl yet — populates once tiered entries close)")
    else:
        for tier in sorted(tex):
            b = tex[tier]
            n = b["n"] or 1
            print(f"  tier {tier:<8} n={b['n']:>4}  initial-stop {100*b['stops']/n:4.1f}%  "
                  f"PnL ${b['pnl']:+.0f}  MFE {b['peak']/n:.2f}x")

    if args.json:
        with open(args.json, "w") as fh:
            json.dump({
                "n_outcomes": len(outs),
                "st_matched": st_matched,
                "base_big_rate": base_big,
                "base_dead_rate": base_dead,
                "conviction_deciles": conviction_decile_dead(outs),
                "vetoes": reports,
                "veto_pass": passes,
                "combined": comb,
                "gate_pass": gate_pass,
            }, fh, indent=2, default=str)
        print(f"\nwrote {args.json}")

    return 0 if gate_pass else 1


if __name__ == "__main__":
    sys.exit(main())
