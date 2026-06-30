"""Sweep the CHANDELIER / ATR-trailing runner-exit + TP-ladder over the LIVE exit
code path, across a true 20-day trade history.

Design + variant rationale + prior-art caveats live in
`analysis/chandelier_strategy_design.md` -- READ IT FIRST. Short version:

  Same harness as stop_sweep.py / exit_config_backtest.py: replay snapshots through
  discovery.paper_trade.run -> discovery.manager.manage, rebuilding the module-global
  PositionManager per variant. ONLY the TP-ladder + Chandelier knobs change between
  runs (see apply()); the initial ATR stop, break-even, moonbag/VP floors and all the
  protective flow exits are held at PROD across EVERY variant, so any PnL delta is
  attributable to the take-profit split + the runner trail, not stop sizing.

Two things this harness adds over the siblings:

  1. 20-DAY WINDOW. The hot signal_snapshots table only retains ~14d, so we
     materialise a full-history snapshot table (hot UNION archive, via
     storage.history.open_history) into a temp DB ONCE and point paper_trade.DB at
     it. Candles for the ATR/Chandelier come from the live hot token_candles (tf=60,
     ~24.5d span) so the trail is never candle-starved inside a 20d run. The
     as_of_ts<=ts bound in _recent_candles_for_atr still blocks look-ahead.

  2. CONTROLS. The blind-earlier-exit trap (see memory ohlcv-indicators-rsi-removal)
     means a trail can "win" just by exiting sooner on a winner-poor slice. So the
     grid bakes in a no-trail control (B_runner_noTrail) and fixed-% trail controls
     (B_runner_fixed30, F_meme_fixed35). A Chandelier variant is only real if it
     beats BOTH its no-trail and its fixed-trail control on the same entries.

NB (same caveat as the siblings): the replay marks on snapshot prices, so ABSOLUTE
PnL is optimistic vs the live ledger. Trust the RELATIVE deltas between variants.
n_reached_2x is the runner-survival metric: a trail that "wins" by crushing it is
the trap, not an edge.

Run:  env/bin/python analysis/chandelier_sweep.py --days 20 --min-conviction 0.18
      env/bin/python analysis/chandelier_sweep.py --days 7 --limit 5   # fast smoke
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
import trading.adaptive_stop as ADP  # noqa: E402
from storage.history import open_history  # noqa: E402

CACHE_DIR = ROOT / "analysis" / ".cache"
SNAP_DB = CACHE_DIR / "chandelier_snaps_full.db"


# --------------------------------------------------------------------------- #
# 1. Materialise a full-history snapshot table (hot UNION archive) once, so the
#    20-day window survives hot-table retention. Reused across runs.
# --------------------------------------------------------------------------- #
def materialize_snapshots(rebuild=False, max_age_h=12.0):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if SNAP_DB.exists() and not rebuild:
        age_h = (time.time() - SNAP_DB.stat().st_mtime) / 3600.0
        if age_h <= max_age_h:
            # index-cheap validation only: MIN/MAX use the ts index (O(1)); a
            # COUNT(*) would full-scan the multi-GB cache on this slow disk.
            con = __import__("sqlite3").connect(f"file:{SNAP_DB}?mode=ro", uri=True)
            try:
                mn, mx = con.execute(
                    "SELECT MIN(timestamp), MAX(timestamp) FROM signal_snapshots"
                ).fetchone()
            finally:
                con.close()
            span = (mx - mn) / 86400.0 if mn else 0
            print(f"[snap] reuse {SNAP_DB.name}: span={span:.1f}d "
                  f"(age {age_h:.1f}h; --rebuild to force)", flush=True)
            return
    t0 = time.time()
    con = open_history()                       # ro hot + attached archive
    con.execute("ATTACH DATABASE ? AS out", (str(SNAP_DB),))
    # bulk-load pragmas on the attached out DB: no journal / no fsync makes the
    # one-shot 4M-row copy ~an order of magnitude faster (it's a throwaway cache).
    con.execute("PRAGMA out.journal_mode=OFF")
    con.execute("PRAGMA out.synchronous=OFF")
    con.execute("PRAGMA out.locking_mode=EXCLUSIVE")
    con.execute("DROP TABLE IF EXISTS out.signal_snapshots")
    # full history, price>0 only (paper_trade filters price>0 anyway). The *_all
    # view already restricts to columns shared by hot+archive (here: all 78).
    con.execute(
        "CREATE TABLE out.signal_snapshots AS "
        "SELECT * FROM signal_snapshots_all WHERE price > 0"
    )
    con.execute("CREATE INDEX out.idx_ss_ts ON signal_snapshots(timestamp)")
    con.execute(
        "CREATE INDEX out.idx_ss_tok ON signal_snapshots(token_address, timestamp)"
    )
    n, mn, mx = con.execute(
        "SELECT COUNT(*), MIN(timestamp), MAX(timestamp) FROM out.signal_snapshots"
    ).fetchone()
    con.close()
    span = (mx - mn) / 86400.0 if mn else 0
    print(f"[snap] built {SNAP_DB.name}: n={n:,} span={span:.1f}d "
          f"in {time.time()-t0:.1f}s", flush=True)


# --------------------------------------------------------------------------- #
# 2. Memoise candle fetches (read-only, pure fn of token+period+tf+minute-bucket)
#    so re-running manage() per tick across 13 variants doesn't re-query sqlite
#    millions of times. The minute-bucket key is EXACT: bucket_start<=ts returns
#    the same rows for any ts within a [k*tf,(k+1)*tf) bucket. Preserves look-ahead
#    safety (still keyed on the floored as_of_ts).
# --------------------------------------------------------------------------- #
_ORIG_CANDLES = ADP._recent_candles_for_atr
_CANDLE_CACHE = {}
# Hard cap so the memo can't grow unbounded and OOM this 3.8GB box (it shares RAM
# with the live scanner -- an unbounded memo OOM-killed both once). Dicts keep
# insertion order, so on overflow we drop the oldest ~25% (rough LRU).
_CANDLE_CACHE_CAP = 10000   # ~10k * ~20KB/entry ~= 200MB; keep small (shares a
#                             3.8GB box with the live scanner -- 60k OOM'd it)


def _memo_candles(address, as_of_ts=None):
    tf = int(config.POSITION_ATR_STOP_TIMEFRAME_SECONDS)
    period = int(config.POSITION_ATR_STOP_PERIOD)
    bucket = None if as_of_ts is None else int(float(as_of_ts) // tf)
    key = (str(address), period, tf, bucket)
    hit = _CANDLE_CACHE.get(key)
    if hit is not None:
        return hit
    val = _ORIG_CANDLES(address, as_of_ts=as_of_ts)
    if len(_CANDLE_CACHE) >= _CANDLE_CACHE_CAP:
        for k in list(_CANDLE_CACHE.keys())[:_CANDLE_CACHE_CAP // 4]:
            del _CANDLE_CACHE[k]
    _CANDLE_CACHE[key] = val
    return val


def install_memo():
    ADP._recent_candles_for_atr = _memo_candles       # initial-stop path
    M._recent_candles_for_atr = _memo_candles          # q3_targets / chandelier path


# --------------------------------------------------------------------------- #
# 3. Variant matrix. See chandelier_strategy_design.md section 4 for the table.
#    Every variant FULLY specifies the varied knobs so nothing leaks across runs.
# --------------------------------------------------------------------------- #
PROD_LADDER = ((3.0, 0.50), (6.0, 0.95))
RUNNER_LADDER = ((1.5, 0.30), (2.5, 0.60))    # 40% runner
MEME_LADDER = ((1.4, 0.35), (2.0, 0.65))      # 35% runner, earlier first partial
FIB_LADDER = ((1.272, 0.30), (1.618, 0.60))   # q3: multiples are placeholders;
#                                               fractions 0.30/0.60 used, fib targets


def V(label, *, tp="mult", ladder=RUNNER_LADDER, fib=(2.618, 4.236), mintgt=2.0,
      chand=False, k=5.0, period=14, trail=0.0, role=""):
    return (label, dict(tp=tp, ladder=ladder, fib=fib, mintgt=mintgt, chand=chand,
                        k=k, period=period, trail=trail, role=role))


VARIANTS = [
    # 0: current production = baseline for all deltas
    V("A0_PROD            ", tp="q3", ladder=PROD_LADDER, chand=False,
      role="baseline (current prod)"),
    # 1: prod + existing chandelier on the 5% moonbag
    V("A1_PROD_chand      ", tp="q3", ladder=PROD_LADDER, chand=True, k=5.0,
      period=14, role="prod + chandelier on 5% moonbag"),
    # 2: CONTROL -- bigger 40% runner, NO trail (just hold to step/VP floors)
    V("B_runner_noTrail   ", ladder=RUNNER_LADDER, chand=False, trail=0.0,
      role="CONTROL: 40% runner, no trail"),
    # 3: CONTROL -- bigger runner, dumb fixed 30% trail
    V("B_runner_fixed30   ", ladder=RUNNER_LADDER, chand=False, trail=0.30,
      role="CONTROL: 40% runner, fixed 30% trail"),
    # 4-8: ATR Chandelier on the 40% runner
    V("C_l14_k2.5         ", ladder=RUNNER_LADDER, chand=True, k=2.5, period=14,
      role="chandelier normal tight"),
    V("C_l14_k3.0         ", ladder=RUNNER_LADDER, chand=True, k=3.0, period=14,
      role="chandelier normal mid"),
    V("C_l14_k3.5         ", ladder=RUNNER_LADDER, chand=True, k=3.5, period=14,
      role="chandelier normal wide"),
    V("C_l22_k3.0         ", ladder=RUNNER_LADDER, chand=True, k=3.0, period=22,
      role="chandelier long-ATR mid"),
    V("C_l22_k3.5         ", ladder=RUNNER_LADDER, chand=True, k=3.5, period=22,
      role="chandelier long-ATR wide"),
    # 9-11: memecoin profile -- earlier/larger first partial, wider trail
    V("D_meme_l14_k4.0    ", ladder=MEME_LADDER, chand=True, k=4.0, period=14,
      role="memecoin wide"),
    V("D_meme_l14_k5.0    ", ladder=MEME_LADDER, chand=True, k=5.0, period=14,
      role="memecoin widest"),
    V("D_meme_l22_k4.0    ", ladder=MEME_LADDER, chand=True, k=4.0, period=22,
      role="memecoin long-ATR"),
    # 12: CONTROL for the D group -- same ladder, fixed 35% trail
    V("F_meme_fixed35     ", ladder=MEME_LADDER, chand=False, trail=0.35,
      role="CONTROL: meme ladder, fixed 35% trail"),
    # 13: the brief's literal fib TP1/TP2 (1.272 / 1.618), chandelier runner
    V("E_fib_low          ", tp="q3", ladder=FIB_LADDER, fib=(1.272, 1.618),
      mintgt=1.2, chand=True, k=3.0, period=14, role="literal 1.272/1.618 fib TPs"),
]

PROD_INDEX = 0


def apply(o):
    # ---- varied knobs (every one set explicitly so nothing leaks) ----
    config.LATTICE_EXIT_TP_MODE = o["tp"]
    config.LATTICE_EXIT_SCALE_OUT_LADDER = o["ladder"]
    config.LATTICE_Q3_FIB_EXTENSIONS = o["fib"]
    config.LATTICE_Q3_MIN_TARGET_MULTIPLE = o["mintgt"]
    config.LATTICE_Q3_ATR_TRAIL_ENABLED = o["chand"]
    config.LATTICE_Q3_ATR_TRAIL_K = o["k"]
    config.POSITION_ATR_STOP_PERIOD = o["period"]
    config.LATTICE_POST_SCALE_TRAIL_PCT = o["trail"]
    config.LATTICE_HIGH_MULT_TRAIL_PCT = o["trail"]   # so the fixed trail also
    #                                                     binds above the 4x trigger
    # ---- scaffolding held constant at PROD across ALL variants ----
    config.POSITION_ATR_STOP_ENABLED = True
    config.POSITION_ATR_STOP_K = 5.0
    config.POSITION_ATR_STOP_MAX_PCT = 0.70
    config.POSITION_ATR_STOP_MIN_PCT = 0.12
    config.LATTICE_EXIT_INITIAL_STOP_PCT = 0.30
    config.LATTICE_EXIT_SCALE_STOP_FLOORS = ((3.0, 1.50), (6.0, 3.00))
    config.LATTICE_MOONBAG_STEP_FLOORS_ENABLED = True
    M._NEW_MANAGER = M.PositionManager()    # re-reads config at __init__
    return M._NEW_MANAGER


def stops(xb):
    """trades that exited on a stop/floor (incl. the chandelier floor)."""
    return (xb.get("initial_stop", 0)
            + xb.get("break_even_floor", 0)
            + xb.get("scale_stop_floor", 0)
            + xb.get("trailing_stop", 0))


def main():
    ap = argparse.ArgumentParser(description="Chandelier/ATR-trail + TP-ladder sweep")
    ap.add_argument("--days", type=float, default=20.0)
    ap.add_argument("--min-conviction", type=float, default=0.18)
    ap.add_argument("--cooldown-h", type=float, default=6.0)
    ap.add_argument("--max-hold-h", type=float, default=12.0)
    ap.add_argument("--limit", type=int, default=0,
                    help="run only the first N variants (0 = all)")
    ap.add_argument("--only", type=str, default="",
                    help="comma-separated variant labels (substring match) to run; "
                         "the full matrix stays the design, this picks a tractable subset")
    ap.add_argument("--native", action="store_true",
                    help="replay against the live hot scanner.db (~14d, warm cache) "
                         "instead of the materialised 20d cache")
    ap.add_argument("--rebuild", action="store_true",
                    help="force-rebuild the materialised snapshot DB")
    ap.add_argument("--dump-ledgers", action="store_true", help="dump per-variant ledgers to analysis/.oos/")
    args = ap.parse_args()

    if args.native:
        print("[snap] NATIVE mode: replaying the live hot scanner.db "
              "(~14d window, warm OS cache)", flush=True)
    else:
        materialize_snapshots(rebuild=args.rebuild)
        PT.DB = str(SNAP_DB)    # entries/management read the 20d+ materialised table
    install_memo()              # candles still come from live hot token_candles

    variants = VARIANTS
    if args.only:
        want = [s.strip() for s in args.only.split(",") if s.strip()]
        variants = [(lbl, o) for (lbl, o) in variants
                    if any(w in lbl for w in want)]
    elif args.limit:
        variants = variants[:args.limit]
    results = []
    for i, (label, o) in enumerate(variants, 1):
        apply(o)
        t0 = time.time()
        out = PT.run(args.days, args.min_conviction, args.cooldown_h,
                     max_hold_h=args.max_hold_h, quiet=True)
        dt = time.time() - t0
        r = out["result"]
        xb = out.get("exit_breakdown", {})
        results.append((label, r, xb, o))
        if args.dump_ledgers:           # per-variant ledger for chandelier_oos --combine
            import json as _json
            led_dir = ROOT / "analysis" / ".oos"
            led_dir.mkdir(parents=True, exist_ok=True)
            ledger = _json.load(open(ROOT / "discovery" / "paper_results.json")
                                )["ledger"]
            keep = [{"entry_ts": t.get("entry_ts"), "pnl_usd": t.get("pnl_usd"),
                     "peak_mult": t.get("peak_mult"), "reason": t.get("reason")}
                    for t in ledger]
            _json.dump({"label": label.strip(), "trades": len(keep), "ledger": keep},
                       open(led_dir / f"{label.strip()}.json", "w"))
        print(f"[{i:2d}/{len(variants)} {dt:5.1f}s] {label} "
              f"trades {r['trades']:3d}  win {r['win_rate_pct']:5.1f}%  "
              f"total ${r['total_pnl_usd']:+9.2f}  PF {r.get('profit_factor')}  "
              f"best ${r['best_usd']:+8.1f} worst ${r['worst_usd']:+6.1f}  "
              f"2x {r.get('n_reached_2x','?'):>3}  chand/floor {xb.get('scale_stop_floor',0):>3}"
              f"  trail {xb.get('trailing_stop',0):>3}", flush=True)

    if len(results) < 2:
        return
    base_idx = PROD_INDEX if len(results) > PROD_INDEX else 0
    base = results[base_idx][1]
    print(f"\nDelta vs ({results[base_idx][0].strip()}) "
          f"-- same entries, only TP-ladder + runner trail differ:")
    for label, r, xb, o in results:
        d2x = r.get("n_reached_2x", 0) - base.get("n_reached_2x", 0)
        print(f"  {label} dPnL ${r['total_pnl_usd'] - base['total_pnl_usd']:+9.2f}"
              f"   dWin {r['win_rate_pct'] - base['win_rate_pct']:+5.1f}pp"
              f"   d2x {d2x:+d}   total ${r['total_pnl_usd']:+9.2f}   [{o['role']}]")

    # Control check: is any chandelier beating BOTH its no-trail and fixed-trail
    # control? (See design doc section 5.)
    by = {lbl.strip(): r for lbl, r, _, _ in results}
    no_trail = by.get("B_runner_noTrail")
    fixed = by.get("B_runner_fixed30")
    if no_trail and fixed:
        print("\nControl test (40% runner group) -- a chandelier is only REAL if it "
              "beats BOTH:")
        print(f"  no-trail control   total ${no_trail['total_pnl_usd']:+9.2f}")
        print(f"  fixed-30% control  total ${fixed['total_pnl_usd']:+9.2f}")
        for lbl in ("C_l14_k2.5", "C_l14_k3.0", "C_l14_k3.5", "C_l22_k3.0",
                    "C_l22_k3.5"):
            r = by.get(lbl)
            if not r:
                continue
            beats = ("PASS" if r['total_pnl_usd'] > no_trail['total_pnl_usd']
                     and r['total_pnl_usd'] > fixed['total_pnl_usd'] else "fail")
            print(f"    {lbl:<12} ${r['total_pnl_usd']:+9.2f}  "
                  f"vs no-trail {r['total_pnl_usd']-no_trail['total_pnl_usd']:+8.2f}  "
                  f"vs fixed {r['total_pnl_usd']-fixed['total_pnl_usd']:+8.2f}  -> {beats}")

    # persist
    import json
    payload = {
        "params": {"days": args.days, "min_conviction": args.min_conviction,
                   "cooldown_h": args.cooldown_h, "max_hold_h": args.max_hold_h},
        "variants": [
            {"label": lbl.strip(), "role": o["role"], "knobs": {
                k: (list(v) if isinstance(v, tuple) else v) for k, v in o.items()
                if k != "role"},
             "result": r, "exit_breakdown": xb}
            for lbl, r, xb, o in results
        ],
    }
    outp = ROOT / "analysis" / "chandelier_sweep_results.json"
    json.dump(payload, open(outp, "w"), indent=2, default=str)
    print(f"\nwrote {outp}")


if __name__ == "__main__":
    main()
