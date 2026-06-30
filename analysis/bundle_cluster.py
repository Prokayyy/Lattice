"""Bundle / cluster detector CLI — re-merge split wallets into effective holding.

Thin wrapper over filters.bundle.analyze (shared with the live entry gate and
the /bundle Telegram command). Catches the "split one position across many fresh
wallets to look distributed" obfuscation that defeats the top-10 / breadth check.

Usage:
  env/bin/python analysis/bundle_cluster.py <token_address>
      [--chain sol] [--window-s 120] [--min-cluster 3] [--amount-tol 0.20]
      [--limit 100] [--json]
"""
import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sources.gmgn import gmgn_client
from filters import bundle


def ts(v):
    try:
        return time.strftime("%m-%d %H:%M:%S", time.localtime(float(v)))
    except Exception:
        return "?"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("address")
    ap.add_argument("--chain", default="sol")
    ap.add_argument("--window-s", type=float, default=120.0)
    ap.add_argument("--min-cluster", type=int, default=3)
    ap.add_argument("--amount-tol", type=float, default=0.20)
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    holders = asyncio.run(
        gmgn_client.top_holders(args.address, chain=args.chain, limit=args.limit))
    if not holders:
        print("no holder data (GMGN returned nothing)")
        return

    s = bundle.analyze(holders, window_s=args.window_s,
                       min_cluster=args.min_cluster, amount_tol=args.amount_tol)

    if args.json:
        print(json.dumps({k: v for k, v in s.items()
                          if k not in ("clusters", "funds", "nonbuyers")}))
        return

    print(f"=== BUNDLE / CLUSTER SCAN  {args.address} ===")
    print(f"holders={s['holders_seen']}  pools_excluded={s['pools_excluded']}  "
          f"buyers={s['buyers']}  transfer/dev={s['nonbuyers_n']}")
    print(f"naive concentration : top1 {s['naive_top1']:.1f}%  "
          f"top10 {s['naive_top10']:.1f}%")
    print(f"DE-OBFUSCATED       : effective top holder {s['effective_top']:.1f}%  "
          f"(+{s['obfuscation_gap']:.1f}pp hidden via splitting)")
    print(f"bundler-tagged wallets: {s['bundler_tagged']}  |  "
          f"non-buy (transfer/dev) supply: {s['nonbuy_pct']:.1f}%")
    print(f"VERDICT: {s['verdict']}\n")

    if s["clusters"]:
        print("Time clusters (wallets buying in the same burst):")
        for c in s["clusters"][:6]:
            print(f"  • {c['n']} wallets in {c['span_s']:.0f}s @ {ts(c['t0'])} "
                  f"-> combined {c['combined_pct']:.1f}%  "
                  f"(amount-similar: {c['similar_n']}/{c['n']} = {c['similar_pct']:.1f}%)")
    else:
        print("No multi-wallet time clusters at these thresholds.")

    if s["funds"]:
        print("\nShared-funding clusters (same native_transfer origin):")
        for c in s["funds"][:5]:
            print(f"  • {c['n']} wallets funded by {c['fund'][:12]} "
                  f"-> combined {c['combined_pct']:.1f}%")

    if s["nonbuyers"]:
        big = sorted(s["nonbuyers"], key=lambda r: -r["pct"])[:5]
        print("\nTop non-buy holders (received via transfer/curve, not bought):")
        for r in big:
            print(f"  • {r['addr'][:12]} {r['pct']:.1f}%  tags={r['tags']}")


if __name__ == "__main__":
    main()
