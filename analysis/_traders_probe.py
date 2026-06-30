"""Throwaway: verify what per-wallet fields GMGN `token holders` returns for a
real memecoin (first-buy time, buy amount, funding source) so the bundle
clustering script can be grounded in the actual data."""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sources.gmgn import gmgn_client

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
toks = toks[-8:]


async def go():
    for sym, t in toks:
        data = await gmgn_client._run(
            "token", "holders", "--chain", "sol", "--address", t,
            "--limit", "100", "--order-by", "amount_percentage",
            "--direction", "desc", "--raw",
        )
        lst = (data or {}).get("list") or []
        if not lst:
            print(sym, "-> no holders data")
            continue
        print("\n===", sym, t, "-> %d holders ===" % len(lst))
        for h in lst[:6]:
            nt = h.get("native_transfer") or {}
            print("  pct=%-7s start=%-12s buy_amt=%-14s buy_usd=%-10s fund=%s tags=%s" % (
                h.get("amount_percentage"),
                h.get("start_holding_at"),
                h.get("buy_amount_cur"),
                h.get("buy_volume_cur"),
                str((nt.get("address") if isinstance(nt, dict) else nt))[:10],
                h.get("maker_token_tags") or h.get("tags"),
            ))
        return  # one token with data is enough


asyncio.run(go())
