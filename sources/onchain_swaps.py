"""True per-trade swap extraction from chain (Helius/Alchemy RPC).

Solana memecoins have no public trade feed on the keys this bot holds (no
Birdeye key; Helius enhanced-txn API is 403; GMGN exposes no per-token trade
list). But the swaps are on-chain: enumerate the pool's signatures, then derive
each trade from the SIGNER's token/SOL balance deltas -- a DEX-agnostic method
(no per-program instruction decoding). Feeds
trading.volume_profile.volume_profile_from_trades for a true per-trade profile.

Pool address comes from gmgn-cli `token pool`. RPC from HELIUS_API_KEY /
ALCHEMY_SOLANA_RPC_URL / public.

CLI:
  env/bin/python sources/onchain_swaps.py <TOKEN_MINT>
  env/bin/python sources/onchain_swaps.py <TOKEN_MINT> --max-sigs 600 --bins 30
"""
import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import aiohttp

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

WSOL = "So11111111111111111111111111111111111111112"
_EPS = 1e-12


def _rpc_url():
    helius = os.getenv("HELIUS_API_KEY", "").strip()
    if helius:
        return f"https://mainnet.helius-rpc.com/?api-key={helius}"
    url = os.getenv("ALCHEMY_SOLANA_RPC_URL", "").strip()
    if url:
        return url
    key = os.getenv("ALCHEMY_API_KEY", "").strip()
    if key:
        return f"https://solana-mainnet.g.alchemy.com/v2/{key}"
    return "https://api.mainnet-beta.solana.com"


def pool_for_token(mint, chain="sol"):
    """Resolve the main pool address via gmgn-cli `token pool`."""
    cli = shutil.which("gmgn-cli") or str(Path.home() / ".npm-global" / "bin"
                                          / "gmgn-cli")
    if not Path(cli).exists():
        return None
    env = dict(os.environ)
    try:
        from config import GMGN_API_KEY
        env["GMGN_API_KEY"] = GMGN_API_KEY
    except Exception:
        pass
    try:
        r = subprocess.run([cli, "token", "pool", "--chain", chain,
                            "--address", mint, "--raw"],
                           capture_output=True, text=True, env=env, timeout=25)
        txt = r.stdout
        j = json.loads(txt[txt.find("{"):])
        return j.get("pool_address") or j.get("pair_address")
    except Exception:
        return None


async def _rpc(session, method, params, retries=4):
    """Single JSON-RPC call with backoff on rate-limit/5xx (batch is blocked on
    the free Helius tier). Returns result or None."""
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    for attempt in range(retries):
        try:
            async with session.post(_rpc_url(), json=payload) as r:
                if r.status in (429, 500, 502, 503, 504):
                    await asyncio.sleep(0.4 * (attempt + 1))
                    continue
                r.raise_for_status()
                data = await r.json()
                return data.get("result")
        except aiohttp.ClientError:
            if attempt + 1 >= retries:
                return None
            await asyncio.sleep(0.4 * (attempt + 1))
    return None


def _balances_by_owner_mint(bals):
    m = {}
    for b in bals or []:
        owner = b.get("owner")
        mint = b.get("mint")
        ui = b.get("uiTokenAmount", {}).get("uiAmount")
        if owner is None or mint is None:
            continue
        m[(owner, mint)] = m.get((owner, mint), 0.0) + (ui or 0.0)
    return m


def parse_swap(tx, mint, min_sol=0.0):
    """Derive one trade from a parsed transaction via signer balance deltas.

    Returns {ts, price, sol_volume, side} (price = SOL per token) or None when
    the txn is not a SOL<->token swap of `mint` (failed tx, liquidity event,
    token-token route, or dust below min_sol).
    """
    meta = (tx or {}).get("meta") or {}
    if meta.get("err") is not None:
        return None
    msg = ((tx.get("transaction") or {}).get("message") or {})
    keys = msg.get("accountKeys") or []
    if not keys:
        return None
    signer = keys[0]["pubkey"] if isinstance(keys[0], dict) else keys[0]
    fee = (meta.get("fee") or 0) / 1e9

    pre = _balances_by_owner_mint(meta.get("preTokenBalances"))
    post = _balances_by_owner_mint(meta.get("postTokenBalances"))

    def d(owner, m):
        return post.get((owner, m), 0.0) - pre.get((owner, m), 0.0)

    tok_d = d(signer, mint)
    wsol_d = d(signer, WSOL)
    pre_b = meta.get("preBalances") or [0]
    post_b = meta.get("postBalances") or [0]
    nat_d = (post_b[0] - pre_b[0]) / 1e9 + fee          # remove the tx fee
    sol_d = wsol_d if abs(wsol_d) > _EPS else nat_d

    if abs(tok_d) <= _EPS or abs(sol_d) <= _EPS:
        return None
    if (tok_d > 0) == (sol_d > 0):                      # same sign = not a swap
        return None
    sol_vol = abs(sol_d)
    if sol_vol < min_sol:
        return None
    return {
        "ts": tx.get("blockTime"),
        "price": sol_vol / abs(tok_d),                  # SOL per token
        "sol_volume": sol_vol,
        "side": "buy" if tok_d > 0 else "sell",
    }


async def _collect_signatures(session, pool, max_pages, page_limit, since_ts,
                              progress):
    """Page getSignaturesForAddress backwards (via the `before` cursor) until
    max_pages, an empty/short page, or signatures older than since_ts. Cheap:
    one RPC call per page_limit signatures. Returns (sigs, oldest_blocktime)."""
    sigs, before, oldest = [], None, None
    for page in range(max_pages):
        opts = {"limit": page_limit}
        if before:
            opts["before"] = before
        res = await _rpc(session, "getSignaturesForAddress", [pool, opts])
        if not res:
            break
        stop = False
        for s in res:
            bt = s.get("blockTime")
            if since_ts and bt and bt < since_ts:
                stop = True
                break
            sigs.append(s["signature"])
            if bt:
                oldest = bt
        before = res[-1]["signature"]
        if progress:
            print(f"  [sigs] page {page + 1}: +{len(res)} (total {len(sigs)})",
                  file=sys.stderr)
        if stop or len(res) < page_limit:
            break
    return sigs, oldest


async def fetch_token_swaps(mint, pool=None, max_pages=6, page_limit=1000,
                            since_ts=None, concurrency=6, min_sol=0.0,
                            timeout_s=900, progress=False):
    """Fetch + parse swaps over MANY pages of signatures (full-history capable).

    Bound the work with max_pages (x page_limit signatures) and/or since_ts
    (stop paging once signatures are older than this unix time). Returns
    {pool, mint, sigs, parsed, oldest_ts, trades:[...]} or {error}.
    """
    pool = pool or pool_for_token(mint)
    if not pool:
        return {"mint": mint, "error": "no_pool"}
    timeout = aiohttp.ClientTimeout(total=timeout_s)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        sigs, oldest = await _collect_signatures(
            session, pool, max_pages, page_limit, since_ts, progress)
        if progress:
            print(f"  [txns] fetching {len(sigs)} transactions ...",
                  file=sys.stderr)
        sem = asyncio.Semaphore(concurrency)
        done = {"n": 0, "sw": 0}

        async def one(sig):
            async with sem:
                tx = await _rpc(session, "getTransaction",
                                [sig, {"maxSupportedTransactionVersion": 0,
                                       "encoding": "jsonParsed"}])
                t = parse_swap(tx, mint, min_sol=min_sol)
                done["n"] += 1
                if t:
                    done["sw"] += 1
                if progress and done["n"] % 250 == 0:
                    print(f"  [txns] {done['n']}/{len(sigs)} "
                          f"({done['sw']} swaps)", file=sys.stderr)
                return t

        results = await asyncio.gather(*[one(s) for s in sigs],
                                       return_exceptions=True)
        trades = [r for r in results if isinstance(r, dict)]
        trades.sort(key=lambda t: t.get("ts") or 0, reverse=True)
        return {"pool": pool, "mint": mint, "sigs": len(sigs),
                "parsed": len(trades), "oldest_ts": oldest, "trades": trades}


def _summary(trades):
    if not trades:
        return "no trades parsed"
    prices = sorted(t["price"] for t in trades)
    med = prices[len(prices) // 2]
    buys = sum(1 for t in trades if t["side"] == "buy")
    vol = sum(t["sol_volume"] for t in trades)
    return (f"{len(trades)} trades | median price {med:.3e} SOL/token | "
            f"buys {buys}/{len(trades)} | total {vol:.1f} SOL")


async def _main(args):
    since_ts = None
    if args.days:
        since_ts = time.time() - args.days * 86400
    elif args.hours:
        since_ts = time.time() - args.hours * 3600
    res = await fetch_token_swaps(
        args.token, pool=args.pool, max_pages=args.max_pages,
        page_limit=args.page_limit, since_ts=since_ts,
        concurrency=args.concurrency, min_sol=args.min_sol,
        progress=not args.quiet)
    if res.get("error"):
        print(f"{args.token}: {res['error']}")
        return
    span = ""
    if res.get("oldest_ts"):
        span = " | back to " + time.strftime("%Y-%m-%d %H:%M",
                                             time.gmtime(res["oldest_ts"]))
    print(f"pool {res['pool']} | {res['sigs']} sigs -> {res['parsed']} swaps{span}")
    print("  " + _summary(res["trades"]))
    from trading.volume_profile import volume_profile_from_trades, _print_profile
    prof = volume_profile_from_trades(res["trades"], bins=args.bins)
    if prof.get("dropped_outliers"):
        print(f"  ({prof['dropped_outliers']} price-outlier swaps clipped)")
    print()
    last = res["trades"][0]["price"] if res["trades"] else None   # newest first
    _print_profile(prof, price=last)


def main():
    ap = argparse.ArgumentParser(
        description="On-chain per-trade swaps + true volume profile "
                    "(paginated / full-history capable)")
    ap.add_argument("token")
    ap.add_argument("--pool", default=None)
    ap.add_argument("--max-pages", type=int, default=6,
                    help="signature pages to walk (x page-limit each)")
    ap.add_argument("--page-limit", type=int, default=1000)
    ap.add_argument("--days", type=float, default=0.0,
                    help="only swaps from the last N days")
    ap.add_argument("--hours", type=float, default=0.0,
                    help="only swaps from the last N hours")
    ap.add_argument("--concurrency", type=int, default=6)
    ap.add_argument("--min-sol", type=float, default=0.0,
                    help="ignore swaps smaller than this many SOL")
    ap.add_argument("--bins", type=int, default=30)
    ap.add_argument("--quiet", action="store_true",
                    help="suppress progress to stderr")
    args = ap.parse_args()
    asyncio.run(_main(args))


if __name__ == "__main__":
    main()
