"""Throwaway: probe GMGN kline health from the bot's runtime + show whether
recent entry tokens would trip the kline fade-filter NOW (candles change over
time, so this is indicative, not the entry-moment truth)."""
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from sources.gmgn import gmgn_client

MAX_WICK = float(getattr(config, "LATTICE_GMGN_KLINE_MAX_UPPER_WICK_RATIO", 0.5) or 0.5)
MAX_DD = float(getattr(config, "LATTICE_GMGN_KLINE_MAX_DRAWDOWN_FROM_HIGH_PCT", -25.0) or -25.0)

toks = []
for line in open("discovery/trades.jsonl"):
    line = line.strip()
    if not line:
        continue
    try:
        d = json.loads(line)
    except Exception:
        continue
    toks.append((d.get("symbol"), d.get("token")))
toks = toks[-6:]

print("gmgn enabled:", gmgn_client.enabled())
print("thresholds: wick>%.2f  drawdown<%.0f%%" % (MAX_WICK, MAX_DD))


async def go():
    ok = 0
    for sym, t in toks:
        t0 = time.time()
        try:
            kf = await asyncio.wait_for(gmgn_client.kline_features(t), 9.0)
        except Exception as e:
            print("%-12s EXCEPTION %s %s" % (sym, type(e).__name__, e))
            continue
        dt = time.time() - t0
        if not kf:
            print("%-12s None in %.1fs  (would FAIL-OPEN -> allow)" % (sym, dt))
            continue
        ok += 1
        wick = float(kf.get("kl_last_upper_wick_ratio") or 0.0)
        dd = kf.get("kl_drawdown_from_high_pct")
        verdict = "allow"
        if MAX_WICK > 0 and wick > MAX_WICK:
            verdict = "BLOCK blow_off_wick"
        elif dd is not None and MAX_DD < 0 and float(dd) < MAX_DD:
            verdict = "BLOCK fade_from_high"
        print("%-12s OK in %.1fs  wick=%.2f dd=%s -> %s"
              % (sym, dt, wick, (round(float(dd), 1) if dd is not None else "n/a"), verdict))
    print("\nGMGN reachable for %d/%d recent tokens" % (ok, len(toks)))

asyncio.run(go())
