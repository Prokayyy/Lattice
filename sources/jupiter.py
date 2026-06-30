"""Jupiter quote-ladder depth probe for Solana AMM liquidity.

Memecoins trade on AMMs -- there is no resting limit-order book, so a stop
placed as a resting limit can be gapped through when liquidity is thin (the
-30% -> -41% slip). This module measures the REAL sell-side depth by asking
Jupiter -- which aggregates every pool/route -- how much price impact you eat to
exit at a ladder of sizes. That answers the operational question directly:

  "To get OUT with N SOL, how far does my own exit push the price?"
  i.e. is a stop at a given level a genuine wall, or a thin gap I'll blow through.

Default endpoint is the free lite-api.jup.ag tier; set JUPITER_API_KEY to use the
keyed api.jup.ag tier (higher rate limits). Decimals come from the Solana RPC
(ALCHEMY_SOLANA_RPC_URL / ALCHEMY_API_KEY / HELIUS_API_KEY, else public RPC).

CLI:
  python3 sources/jupiter.py <TOKEN_MINT>
  python3 sources/jupiter.py <TOKEN_MINT> --rungs 0.5,1,2,5,10,25
"""
import argparse
import asyncio
import os
import sys

import aiohttp

SOL_MINT = "So11111111111111111111111111111111111111112"
LAMPORTS = 1_000_000_000
DEFAULT_RUNGS = (0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 25.0)


def _base_and_headers():
    key = os.getenv("JUPITER_API_KEY", "").strip()
    if key:
        return "https://api.jup.ag", {"x-api-key": key}
    return "https://lite-api.jup.ag", {}


def _rpc_url():
    url = os.getenv("ALCHEMY_SOLANA_RPC_URL", "").strip()
    if url:
        return url
    key = os.getenv("ALCHEMY_API_KEY", "").strip()
    if key:
        return f"https://solana-mainnet.g.alchemy.com/v2/{key}"
    helius = os.getenv("HELIUS_API_KEY", "").strip()
    if helius:
        return f"https://mainnet.helius-rpc.com/?api-key={helius}"
    return "https://api.mainnet-beta.solana.com"


async def quote(session, input_mint, output_mint, amount,
                slippage_bps=300, swap_mode="ExactIn"):
    """Raw Jupiter /quote. amount is in base units of the input mint."""
    base, headers = _base_and_headers()
    params = {
        "inputMint": input_mint,
        "outputMint": output_mint,
        "amount": str(int(amount)),
        "slippageBps": str(slippage_bps),
        "swapMode": swap_mode,
    }
    async with session.get(base + "/swap/v1/quote", params=params,
                           headers=headers) as r:
        r.raise_for_status()
        return await r.json()


async def token_decimals(session, mint):
    payload = {"jsonrpc": "2.0", "id": 1, "method": "getTokenSupply",
               "params": [mint]}
    async with session.post(_rpc_url(), json=payload) as r:
        d = await r.json()
    return int(d["result"]["value"]["decimals"])


def _impact_pct(q):
    try:
        return float(q.get("priceImpactPct") or 0) * 100.0
    except (TypeError, ValueError):
        return 0.0


async def sell_depth_ladder(token_mint, sol_rungs=DEFAULT_RUNGS,
                            slippage_bps=300, ref_sol=0.1, timeout_s=20):
    """Trace sell-side depth: for each SOL-notional rung, how much does exiting
    that size move the realised price below spot, plus Jupiter's own impact%.

    Returns {token, decimals, spot_sol_per_token, ladder:[...]} where each ladder
    row has sol, impact_pct, realized_sol, slip_vs_spot_pct, routes.
    """
    timeout = aiohttp.ClientTimeout(total=timeout_s)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        # spot reference from a small buy (SOL -> token), decimals-aware.
        # A failure here = Jupiter has no route at all = zero exit liquidity,
        # which is itself the answer: a resting stop here cannot fill.
        try:
            dec = await token_decimals(session, token_mint)
            ref = await quote(session, SOL_MINT, token_mint,
                              int(ref_sol * LAMPORTS), slippage_bps, "ExactIn")
            out_tokens = int(ref["outAmount"]) / (10 ** dec)
        except Exception as e:                           # noqa: BLE001
            return {"token": token_mint, "error": "no_route",
                    "detail": f"{type(e).__name__}: {str(e)[:100]}"}
        if out_tokens <= 0:
            return {"token": token_mint, "decimals": dec, "error": "no_route"}
        spot = ref_sol / out_tokens                       # SOL per token
        rows = []
        for s in sol_rungs:
            token_amt = int((s / spot) * (10 ** dec))
            if token_amt <= 0:
                continue
            try:
                q = await quote(session, token_mint, SOL_MINT, token_amt,
                                slippage_bps, "ExactIn")
                out_sol = int(q["outAmount"]) / LAMPORTS
                realized = out_sol / (token_amt / (10 ** dec))  # SOL/token
                rows.append({
                    "sol": s,
                    "impact_pct": _impact_pct(q),
                    "realized_sol": out_sol,
                    "slip_vs_spot_pct": (realized / spot - 1) * 100 if spot else 0,
                    "routes": len(q.get("routePlan", [])),
                })
            except Exception as e:                       # noqa: BLE001
                rows.append({"sol": s, "error": str(e)[:70]})
        return {"token": token_mint, "decimals": dec,
                "spot_sol_per_token": spot, "ladder": rows}


def sol_to_move_price(result, drop_pct):
    """From a ladder result, the smallest rung whose realised slip <= -drop_pct.
    Returns the SOL size, or None if no rung reaches that drop (deeper than probed)."""
    for row in result.get("ladder", []):
        if "slip_vs_spot_pct" in row and row["slip_vs_spot_pct"] <= -abs(drop_pct):
            return row["sol"]
    return None


def _print_report(result):
    if result.get("error"):
        print(f"Token: {result['token']}")
        if result["error"] == "no_route":
            print("  NO JUPITER ROUTE -> zero exit liquidity (rugged / illiquid / "
                  "not indexed).")
            print("  A resting stop here cannot fill -- the position can only be "
                  "exited into a vacuum.")
        else:
            print(f"  error: {result['error']}")
        if result.get("detail"):
            print(f"  detail: {result['detail']}")
        return
    print(f"Token: {result['token']}  (decimals={result['decimals']})")
    print(f"Spot: {result['spot_sol_per_token']:.3e} SOL/token\n")
    print(f"  {'sell (SOL)':>10} | {'jup impact%':>11} | {'realised slip%':>14} | routes")
    print("  " + "-" * 52)
    for row in result["ladder"]:
        if "error" in row:
            print(f"  {row['sol']:>10.2f} | {'ERR: ' + row['error']}")
            continue
        print(f"  {row['sol']:>10.2f} | {row['impact_pct']:>10.2f}% | "
              f"{row['slip_vs_spot_pct']:>13.2f}% | {row['routes']}")
    print()
    for d in (10, 20, 30):
        sz = sol_to_move_price(result, d)
        msg = f"~{sz:.2f} SOL" if sz is not None else f">{result['ladder'][-1].get('sol', '?')} SOL (deeper than probed)"
        print(f"  sell pressure to push price -{d}%: {msg}")
    # quick verdict for resting-limit viability
    first = next((r for r in result["ladder"] if "slip_vs_spot_pct" in r), None)
    if first:
        verdict = ("THIN - a resting stop here gets gapped through"
                   if first["slip_vs_spot_pct"] <= -10
                   else "OK depth at small size")
        print(f"\n  verdict @ {first['sol']} SOL exit: "
              f"{first['slip_vs_spot_pct']:.1f}% slip -> {verdict}")


async def _main(args):
    rungs = (tuple(float(x) for x in args.rungs.split(","))
             if args.rungs else DEFAULT_RUNGS)
    result = await sell_depth_ladder(args.token, sol_rungs=rungs,
                                     slippage_bps=args.slippage_bps)
    _print_report(result)


def main():
    ap = argparse.ArgumentParser(description="Jupiter sell-side depth ladder")
    ap.add_argument("token", help="token mint address")
    ap.add_argument("--rungs", default="", help="comma SOL sizes e.g. 0.5,1,2,5")
    ap.add_argument("--slippage-bps", type=int, default=300)
    args = ap.parse_args()
    asyncio.run(_main(args))


if __name__ == "__main__":
    main()
