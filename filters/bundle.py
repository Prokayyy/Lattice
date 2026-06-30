"""Bundle / cluster analysis on a GMGN holder list (pure, no I/O).

Re-merges split wallets into their effective holding to beat the "split one
position across many fresh wallets to look distributed" obfuscation that defeats
the naive top-10 / breadth checks. Shared by analysis/bundle_cluster.py (CLI),
the live entry gate (discovery/live_runner._gmgn_bundle_block_reason), and the
/bundle Telegram command.

Clusters CURRENT holders by:
  (1) first-buy TIME proximity (single-linkage, gap <= window_s)
  (2) buy-AMOUNT similarity within a time cluster (within amount_tol, relative)
  (3) shared funding source (native_transfer), when present
AMM/CEX pool addresses (addr_type==2 / exchange) are excluded so the LP is not
mistaken for a whale; transfer-in/dev holders (no recorded buy) are tracked
separately.
"""
import statistics


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _is_pool(h):
    return h.get("addr_type") in (2, "2") or bool(h.get("exchange"))


def build_records(holders):
    buyers, nonbuyers, pools = [], [], []
    for h in holders:
        rec = {
            "addr": str(h.get("address") or ""),
            "pct": (_f(h.get("amount_percentage")) or 0.0) * 100.0,
            "amt": _f(h.get("buy_amount_cur")) or 0.0,
            "usd": _f(h.get("buy_volume_cur")) or 0.0,
            "start": _f(h.get("start_holding_at")),
            "fund": ((h.get("native_transfer") or {}).get("address")
                     if isinstance(h.get("native_transfer"), dict) else None),
            "tags": h.get("maker_token_tags") or h.get("tags") or [],
        }
        if _is_pool(h):
            pools.append(rec)
        elif rec["amt"] > 0 and rec["start"]:
            buyers.append(rec)
        else:
            nonbuyers.append(rec)
    return buyers, nonbuyers, pools


def time_clusters(buyers, window_s, min_cluster, amount_tol):
    buyers = sorted(buyers, key=lambda r: r["start"])
    groups, cur = [], []
    for r in buyers:
        if cur and (r["start"] - cur[-1]["start"]) > window_s:
            groups.append(cur)
            cur = []
        cur.append(r)
    if cur:
        groups.append(cur)

    out = []
    for g in groups:
        if len(g) < min_cluster:
            continue
        amts = [r["amt"] for r in g]
        med = statistics.median(amts)
        similar = [r for r in g
                   if med > 0 and abs(r["amt"] - med) <= amount_tol * med]
        out.append({
            "n": len(g),
            "combined_pct": sum(r["pct"] for r in g),
            "similar_n": len(similar),
            "similar_pct": sum(r["pct"] for r in similar),
            "t0": g[0]["start"], "t1": g[-1]["start"],
            "span_s": g[-1]["start"] - g[0]["start"],
            "median_amt": med,
            "members": g,
        })
    return sorted(out, key=lambda c: -c["combined_pct"])


def funding_clusters(records):
    groups = {}
    for r in records:
        if r["fund"]:
            groups.setdefault(r["fund"], []).append(r)
    return sorted(
        ({"fund": k, "n": len(v), "combined_pct": sum(x["pct"] for x in v)}
         for k, v in groups.items() if len(v) >= 2),
        key=lambda c: -c["combined_pct"],
    )


def analyze(holders, window_s=120.0, min_cluster=3, amount_tol=0.20):
    """Returns a summary dict with naive vs de-obfuscated concentration, the
    clusters/funds/non-buyers, and a LOW/MEDIUM/HIGH verdict."""
    buyers, nonbuyers, pools = build_records(holders)
    clusters = time_clusters(buyers, window_s, min_cluster, amount_tol)
    funds = funding_clusters(buyers + nonbuyers)

    wallet_recs = sorted(buyers + nonbuyers, key=lambda r: -r["pct"])
    top1 = wallet_recs[0]["pct"] if wallet_recs else 0.0
    top10 = sum(r["pct"] for r in wallet_recs[:10])
    largest_cluster = clusters[0]["combined_pct"] if clusters else 0.0
    largest_fund = funds[0]["combined_pct"] if funds else 0.0
    effective_top = max(top1, largest_cluster, largest_fund)
    bundler_tagged = sum(
        1 for r in buyers + nonbuyers
        if any("bundl" in str(t).lower() for t in r["tags"]))
    nonbuy_pct = sum(r["pct"] for r in nonbuyers)

    if effective_top >= 25 or (clusters and clusters[0]["similar_n"] >= 5
                               and largest_cluster >= 15):
        verdict = "HIGH"
    elif effective_top >= 12 or bundler_tagged >= 5 or largest_fund >= 12:
        verdict = "MEDIUM"
    else:
        verdict = "LOW"

    return {
        "holders_seen": len(holders), "pools_excluded": len(pools),
        "buyers": len(buyers), "nonbuyers_n": len(nonbuyers),
        "naive_top1": top1, "naive_top10": top10,
        "largest_cluster_pct": largest_cluster, "largest_fund_pct": largest_fund,
        "effective_top": effective_top, "obfuscation_gap": effective_top - top1,
        "n_time_clusters": len(clusters), "bundler_tagged": bundler_tagged,
        "nonbuy_pct": nonbuy_pct, "verdict": verdict,
        "clusters": clusters, "funds": funds, "nonbuyers": nonbuyers,
    }
