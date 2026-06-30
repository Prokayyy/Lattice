#!/usr/bin/env python3
"""Weekly bad-outcome wallet recurrence report.

Selects bad / rug-like Lattice paper trades, pulls GMGN top-holder data for
their tokens, runs the shared bundle analyzer, then ranks wallets and funders
that recur across multiple bad tokens.

This is analysis-only. It does not block entries or write live state.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from filters import bundle  # noqa: E402
from sources.gmgn import gmgn_client  # noqa: E402


DEFAULT_TRADES = ROOT / "discovery" / "trades.jsonl"
DEFAULT_OUT_DIR = ROOT / "analysis" / "bad_wallet_clusters"

BAD_REASON_PREFIXES = (
    "initial_stop",
    "liquidity_collapse",
    "sell_only_flow",
    "live_hard_stop",
    "strict_early_failure_exit",
)

TOKEN_FIELDS = [
    "token",
    "symbol",
    "exit_utc",
    "reason",
    "pnl_usd",
    "cost_usd",
    "exit_mult",
    "peak_mult",
    "bad_tags",
    "bundle_value_pct",
    "bundle_verdict",
    "naive_top1_pct",
    "naive_top10_pct",
    "largest_cluster_pct",
    "largest_fund_pct",
    "time_clusters",
    "bundler_tagged",
    "nonbuy_pct",
    "holder_fetch_status",
]

WALLET_FIELDS = [
    "wallet",
    "risk_score",
    "tokens_touched",
    "symbols",
    "roles",
    "bad_pnl_usd",
    "rug_like_tokens",
    "high_bundle_tokens",
    "total_pct",
    "max_pct",
    "first_seen_utc",
    "last_seen_utc",
    "tags",
    "flag_reason",
]

ACTOR_FIELDS = [
    "wallet",
    "token",
    "symbol",
    "role",
    "pct",
    "fund",
    "cluster_id",
    "cluster_pct",
    "cluster_wallets",
    "bundle_value_pct",
    "bad_tags",
    "pnl_usd",
    "tags",
]


def safe_float(value, default=0.0):
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_int(value, default=0):
    try:
        if value is None or value == "":
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def fmt_utc(ts):
    if not ts:
        return ""
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(float(ts)))


def short_addr(addr):
    text = str(addr or "")
    if len(text) <= 14:
        return text
    return f"{text[:6]}...{text[-4:]}"


def read_jsonl(path):
    path = Path(path)
    if not path.exists():
        return []

    rows = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def exit_multiple(trade):
    entry = safe_float(trade.get("entry_price"))
    exit_price = safe_float(trade.get("exit_price"))
    if entry <= 0 or exit_price <= 0:
        return 0.0
    return exit_price / entry


def bad_trade_tags(trade, args):
    pnl = safe_float(trade.get("pnl_usd"))
    cost = safe_float(trade.get("cost_usd"), 20.0)
    reason = str(trade.get("reason") or "")
    peak = safe_float(trade.get("peak_mult"))
    exit_mult = exit_multiple(trade)
    tags = []

    if pnl <= args.max_pnl_usd:
        tags.append("loss")
    if cost > 0 and pnl / cost <= args.max_loss_fraction:
        tags.append("loss_fraction")
    if exit_mult and exit_mult <= args.max_exit_mult:
        tags.append("dump")
    if (
        reason.startswith(BAD_REASON_PREFIXES)
        and (peak <= args.max_peak_mult or pnl <= args.max_pnl_usd)
    ):
        tags.append(f"bad_{reason}")
    if exit_mult and exit_mult <= args.rug_exit_mult and peak <= args.rug_peak_mult:
        tags.append("rug_like")

    return tags


def select_bad_trades(trades, args):
    cutoff = time.time() - max(args.days, 0.1) * 86400
    by_token = {}

    for trade in trades:
        exit_ts = safe_float(trade.get("exit_ts") or trade.get("entry_ts"))
        token = str(trade.get("token") or "").strip()
        if not token or exit_ts < cutoff:
            continue

        tags = bad_trade_tags(trade, args)
        if not tags:
            continue

        rec = dict(trade)
        rec["bad_tags"] = tags
        rec["exit_mult"] = exit_multiple(trade)
        rec["severity"] = (
            abs(min(safe_float(trade.get("pnl_usd")), 0.0))
            + max(0.0, 1.0 - rec["exit_mult"]) * 20.0
            + (10.0 if "rug_like" in tags else 0.0)
        )

        prev = by_token.get(token)
        if not prev or rec["severity"] > prev["severity"]:
            by_token[token] = rec

    rows = sorted(
        by_token.values(),
        key=lambda row: (
            row["severity"],
            safe_float(row.get("exit_ts")),
        ),
        reverse=True,
    )
    return rows[: max(args.max_tokens, 1)]


def has_bundler_tag(record):
    return any(
        "bundl" in str(tag).lower()
        for tag in (record.get("tags") or [])
    )


def role_weight(role):
    return {
        "shared_funder": 20.0,
        "cluster_member": 14.0,
        "bundler_tagged": 12.0,
        "funded_wallet": 10.0,
        "transfer_dev_holder": 8.0,
        "large_holder": 6.0,
        "top_holder": 5.0,
    }.get(role, 2.0)


def fund_groups(records, min_wallets=2):
    groups = defaultdict(list)
    for record in records:
        funder = record.get("fund")
        if funder:
            groups[funder].append(record)
    out = []
    for funder, rows in groups.items():
        if len(rows) >= min_wallets:
            out.append({
                "fund": funder,
                "members": rows,
                "n": len(rows),
                "combined_pct": sum(safe_float(r.get("pct")) for r in rows),
            })
    out.sort(key=lambda row: row["combined_pct"], reverse=True)
    return out


def actor_rows_for_token(token_row, summary, holders, args):
    buyers, nonbuyers, _pools = bundle.build_records(holders)
    wallet_recs = sorted(buyers + nonbuyers, key=lambda row: -row["pct"])
    rows = []
    seen = set()

    def add_actor(record, role, *, cluster=None, fund=None):
        wallet = str(record.get("addr") or "").strip()
        if not wallet:
            return

        key = (
            wallet,
            role,
            token_row["token"],
            str(cluster.get("id") if cluster else ""),
            str(fund or ""),
        )
        if key in seen:
            return
        seen.add(key)

        rows.append({
            "wallet": wallet,
            "token": token_row["token"],
            "symbol": token_row.get("symbol") or "",
            "role": role,
            "pct": round(safe_float(record.get("pct")), 6),
            "fund": fund or record.get("fund") or "",
            "cluster_id": cluster.get("id") if cluster else "",
            "cluster_pct": round(safe_float(cluster.get("combined_pct")), 6)
            if cluster
            else 0.0,
            "cluster_wallets": safe_int(cluster.get("n")) if cluster else 0,
            "bundle_value_pct": round(safe_float(summary.get("effective_top")), 6),
            "start": safe_float(record.get("start")),
            "bad_tags": ",".join(token_row.get("bad_tags") or []),
            "pnl_usd": safe_float(token_row.get("pnl_usd")),
            "tags": ",".join(str(t) for t in (record.get("tags") or [])),
        })

    for idx, cluster in enumerate(summary.get("clusters") or []):
        if idx >= args.max_clusters_per_token:
            break
        if (
            safe_float(cluster.get("combined_pct")) < args.min_cluster_pct
            and safe_int(cluster.get("similar_n")) < args.min_similar_wallets
        ):
            continue
        cluster = dict(cluster)
        cluster["id"] = f"time_cluster_{idx + 1}"
        for record in cluster.get("members") or []:
            add_actor(record, "cluster_member", cluster=cluster)

    for group in fund_groups(buyers + nonbuyers):
        if safe_float(group.get("combined_pct")) < args.min_fund_pct:
            continue
        funder_rec = {
            "addr": group["fund"],
            "pct": group["combined_pct"],
            "tags": ["candidate_funder"],
        }
        add_actor(funder_rec, "shared_funder", fund=group["fund"])
        for record in group["members"]:
            add_actor(record, "funded_wallet", fund=group["fund"])

    for record in wallet_recs:
        if has_bundler_tag(record):
            add_actor(record, "bundler_tagged")
        if safe_float(record.get("pct")) >= args.min_holder_pct:
            add_actor(record, "large_holder")

    if wallet_recs:
        add_actor(wallet_recs[0], "top_holder")

    for record in nonbuyers:
        if safe_float(record.get("pct")) >= args.min_nonbuy_pct:
            add_actor(record, "transfer_dev_holder")

    return rows


async def fetch_one_token(token_row, args):
    token = token_row["token"]
    try:
        holders = await gmgn_client.top_holders(
            token,
            chain=args.chain,
            limit=args.holder_limit,
        )
    except Exception as exc:
        return {
            "token": token_row,
            "summary": None,
            "actors": [],
            "status": type(exc).__name__,
        }

    if not holders:
        return {
            "token": token_row,
            "summary": None,
            "actors": [],
            "status": "no_holder_data",
        }

    summary = bundle.analyze(
        holders,
        window_s=args.window_s,
        min_cluster=args.min_cluster,
        amount_tol=args.amount_tol,
    )
    actors = actor_rows_for_token(token_row, summary, holders, args)
    return {
        "token": token_row,
        "summary": summary,
        "actors": actors,
        "status": "ok",
    }


async def fetch_tokens(token_rows, args):
    if not gmgn_client.enabled():
        raise RuntimeError("GMGN not enabled; need gmgn-cli and GMGN_API_KEY")

    sem = asyncio.Semaphore(max(args.concurrency, 1))

    async def guarded(row):
        async with sem:
            return await fetch_one_token(row, args)

    return await asyncio.gather(*(guarded(row) for row in token_rows))


def token_output_row(result):
    token = result["token"]
    summary = result.get("summary") or {}
    return {
        "token": token.get("token"),
        "symbol": token.get("symbol"),
        "exit_utc": fmt_utc(safe_float(token.get("exit_ts"))),
        "reason": token.get("reason"),
        "pnl_usd": round(safe_float(token.get("pnl_usd")), 4),
        "cost_usd": round(safe_float(token.get("cost_usd")), 4),
        "exit_mult": round(safe_float(token.get("exit_mult")), 4),
        "peak_mult": round(safe_float(token.get("peak_mult")), 4),
        "bad_tags": ",".join(token.get("bad_tags") or []),
        "bundle_value_pct": round(safe_float(summary.get("effective_top")), 4),
        "bundle_verdict": summary.get("verdict") or "",
        "naive_top1_pct": round(safe_float(summary.get("naive_top1")), 4),
        "naive_top10_pct": round(safe_float(summary.get("naive_top10")), 4),
        "largest_cluster_pct": round(
            safe_float(summary.get("largest_cluster_pct")),
            4,
        ),
        "largest_fund_pct": round(safe_float(summary.get("largest_fund_pct")), 4),
        "time_clusters": safe_int(summary.get("n_time_clusters")),
        "bundler_tagged": safe_int(summary.get("bundler_tagged")),
        "nonbuy_pct": round(safe_float(summary.get("nonbuy_pct")), 4),
        "holder_fetch_status": result.get("status") or "",
    }


def aggregate_wallets(actor_rows, min_repeat):
    groups = defaultdict(lambda: {
        "wallet": "",
        "tokens": set(),
        "symbols": set(),
        "roles": Counter(),
        "tags": Counter(),
        "bad_pnl_usd": 0.0,
        "rug_like_tokens": set(),
        "high_bundle_tokens": set(),
        "total_pct": 0.0,
        "max_pct": 0.0,
        "first_seen": 0.0,
        "last_seen": 0.0,
        "risk_score": 0.0,
    })

    for row in actor_rows:
        wallet = row.get("wallet") or ""
        if not wallet:
            continue

        group = groups[wallet]
        group["wallet"] = wallet
        group["tokens"].add(row.get("token") or "")
        if row.get("symbol"):
            group["symbols"].add(row.get("symbol"))
        role = row.get("role") or "unknown"
        group["roles"][role] += 1
        group["bad_pnl_usd"] += safe_float(row.get("pnl_usd"))
        group["total_pct"] += safe_float(row.get("pct"))
        group["max_pct"] = max(group["max_pct"], safe_float(row.get("pct")))
        group["risk_score"] += role_weight(role) + safe_float(row.get("pct")) / 2.0

        tags = str(row.get("tags") or "")
        for tag in tags.split(","):
            tag = tag.strip()
            if tag:
                group["tags"][tag] += 1

        bad_tags = set(str(row.get("bad_tags") or "").split(","))
        if "rug_like" in bad_tags:
            group["rug_like_tokens"].add(row.get("token") or "")
        if safe_float(row.get("bundle_value_pct")) >= 25.0:
            group["high_bundle_tokens"].add(row.get("token") or "")

        start = safe_float(row.get("start"))
        if start:
            if not group["first_seen"] or start < group["first_seen"]:
                group["first_seen"] = start
            if start > group["last_seen"]:
                group["last_seen"] = start

    out = []
    for wallet, group in groups.items():
        token_count = len(group["tokens"])
        if token_count < min_repeat:
            continue

        role_text = ",".join(
            f"{role}:{count}"
            for role, count in group["roles"].most_common()
        )
        flag_parts = []
        flag_parts.append(f"repeat_{token_count}_bad_tokens")
        if group["roles"].get("shared_funder", 0) >= 1:
            flag_parts.append("shared_funder")
        if group["roles"].get("cluster_member", 0) >= min_repeat:
            flag_parts.append("repeat_cluster_member")
        if group["roles"].get("bundler_tagged", 0) >= min_repeat:
            flag_parts.append("repeat_bundler_tag")
        if group["rug_like_tokens"]:
            flag_parts.append(f"rug_like:{len(group['rug_like_tokens'])}")

        out.append({
            "wallet": wallet,
            "risk_score": round(
                group["risk_score"] + max(token_count - 1, 0) * 25.0,
                4,
            ),
            "tokens_touched": token_count,
            "symbols": ",".join(sorted(group["symbols"])),
            "roles": role_text,
            "bad_pnl_usd": round(group["bad_pnl_usd"], 4),
            "rug_like_tokens": len(group["rug_like_tokens"]),
            "high_bundle_tokens": len(group["high_bundle_tokens"]),
            "total_pct": round(group["total_pct"], 4),
            "max_pct": round(group["max_pct"], 4),
            "first_seen_utc": fmt_utc(group["first_seen"]),
            "last_seen_utc": fmt_utc(group["last_seen"]),
            "tags": ",".join(
                tag for tag, _count in group["tags"].most_common(8)
            ),
            "flag_reason": ",".join(flag_parts),
        })

    out.sort(
        key=lambda row: (
            safe_float(row.get("tokens_touched")),
            safe_float(row.get("risk_score")),
        ),
        reverse=True,
    )
    return out


def write_csv(path, rows, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def json_ready(value):
    if isinstance(value, Counter):
        return dict(value)
    if isinstance(value, set):
        return sorted(value)
    raise TypeError(type(value).__name__)


def render_markdown(payload):
    lines = [
        "# Bad Wallet Cluster Report",
        "",
        "Candidate repeat-risk wallet report. This is not proof of common "
        "ownership; it flags recurring wallet/funder/cluster involvement "
        "across bad Lattice outcomes.",
        "",
        "## Summary",
        "",
        f"- Window: `{payload['days']:g}d`",
        f"- Bad tokens selected: `{len(payload['tokens'])}`",
        f"- Actor rows: `{len(payload['actors'])}`",
        f"- Flagged wallets/funders: `{len(payload['wallets'])}`",
        "",
    ]

    if payload["wallets"]:
        lines += [
            "## Top Flagged Wallets / Funders",
            "",
            "| rank | wallet | tokens | roles | score | symbols | reason |",
            "|---:|---|---:|---|---:|---|---|",
        ]
        for idx, row in enumerate(payload["wallets"][:25], 1):
            lines.append(
                f"| {idx} | `{short_addr(row['wallet'])}` | "
                f"{row['tokens_touched']} | `{row['roles']}` | "
                f"{row['risk_score']:.1f} | `{row['symbols']}` | "
                f"`{row['flag_reason']}` |"
            )
    else:
        lines += [
            "## Top Flagged Wallets / Funders",
            "",
            "- No wallet met the repeat threshold in this run.",
        ]

    lines += [
        "",
        "## Bad Tokens",
        "",
        "| symbol | reason | pnl | exit | peak | bundle | verdict | status |",
        "|---|---|---:|---:|---:|---:|---|---|",
    ]
    for row in payload["tokens"][:40]:
        lines.append(
            f"| `{row['symbol']}` | `{row['reason']}` | "
            f"{row['pnl_usd']:+.2f} | {row['exit_mult']:.2f}x | "
            f"{row['peak_mult']:.2f}x | {row['bundle_value_pct']:.1f}% | "
            f"{row['bundle_verdict'] or '-'} | `{row['holder_fetch_status']}` |"
        )

    return "\n".join(lines) + "\n"


def render_telegram(payload, limit=12):
    lines = [
        "<b>[ BAD WALLET CLUSTERS ]</b>",
        f"Window: <code>{payload['days']:g}d</code>",
        (
            f"Bad tokens <b>{len(payload['tokens'])}</b> | "
            f"actors <b>{len(payload['actors'])}</b> | "
            f"flagged <b>{len(payload['wallets'])}</b>"
        ),
    ]

    if payload["wallets"]:
        lines.append("")
        lines.append("<b>Top wallets/funders</b>")
        for row in payload["wallets"][:limit]:
            lines.append(
                f"<code>{short_addr(row['wallet'])}</code> "
                f"tokens {row['tokens_touched']} | "
                f"score {row['risk_score']:.1f} | "
                f"{row['roles']} | {row['flag_reason']}"
            )
    else:
        lines.append("No recurring wallet met the repeat threshold.")

    return "\n".join(lines)


async def build_report_async(args):
    trades = read_jsonl(args.trades)
    bad_trades = select_bad_trades(trades, args)
    results = await fetch_tokens(bad_trades, args) if bad_trades else []
    token_rows = [token_output_row(result) for result in results]
    actor_rows = [
        actor
        for result in results
        for actor in result.get("actors") or []
    ]
    wallet_rows = aggregate_wallets(actor_rows, args.min_repeat)

    payload = {
        "generated_at": time.time(),
        "days": args.days,
        "thresholds": {
            "max_pnl_usd": args.max_pnl_usd,
            "max_loss_fraction": args.max_loss_fraction,
            "max_exit_mult": args.max_exit_mult,
            "max_peak_mult": args.max_peak_mult,
            "rug_exit_mult": args.rug_exit_mult,
            "rug_peak_mult": args.rug_peak_mult,
            "min_repeat": args.min_repeat,
        },
        "tokens": token_rows,
        "actors": actor_rows,
        "wallets": wallet_rows,
    }
    return payload


def build_report(**kwargs):
    args = parse_args([])
    for key, value in kwargs.items():
        setattr(args, key, value)
    return asyncio.run(build_report_async(args))


def write_outputs(payload, out_dir):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    write_csv(out_dir / "bad_tokens.csv", payload["tokens"], TOKEN_FIELDS)
    write_csv(out_dir / "bad_wallets.csv", payload["wallets"], WALLET_FIELDS)
    write_csv(out_dir / "bad_wallet_actors.csv", payload["actors"], ACTOR_FIELDS)
    (out_dir / "bad_wallet_cluster_report.json").write_text(
        json.dumps(payload, indent=2, default=json_ready) + "\n",
        encoding="utf-8",
    )
    (out_dir / "bad_wallet_cluster_report.md").write_text(
        render_markdown(payload),
        encoding="utf-8",
    )


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=float, default=7.0)
    parser.add_argument("--trades", default=str(DEFAULT_TRADES))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--chain", default="sol")
    parser.add_argument("--max-tokens", type=int, default=30)
    parser.add_argument("--holder-limit", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--window-s", type=float, default=120.0)
    parser.add_argument("--min-cluster", type=int, default=3)
    parser.add_argument("--amount-tol", type=float, default=0.20)
    parser.add_argument("--max-clusters-per-token", type=int, default=5)
    parser.add_argument("--min-cluster-pct", type=float, default=5.0)
    parser.add_argument("--min-similar-wallets", type=int, default=3)
    parser.add_argument("--min-fund-pct", type=float, default=5.0)
    parser.add_argument("--min-holder-pct", type=float, default=3.0)
    parser.add_argument("--min-nonbuy-pct", type=float, default=2.0)
    parser.add_argument("--min-repeat", type=int, default=2)
    parser.add_argument("--max-pnl-usd", type=float, default=-4.0)
    parser.add_argument("--max-loss-fraction", type=float, default=-0.25)
    parser.add_argument("--max-exit-mult", type=float, default=0.75)
    parser.add_argument("--max-peak-mult", type=float, default=1.35)
    parser.add_argument("--rug-exit-mult", type=float, default=0.55)
    parser.add_argument("--rug-peak-mult", type=float, default=1.15)
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--telegram", action="store_true")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    payload = asyncio.run(build_report_async(args))

    if not args.no_write:
        write_outputs(payload, args.out_dir)

    if args.json:
        print(json.dumps(payload, indent=2, default=json_ready))
    elif args.telegram:
        print(render_telegram(payload))
    else:
        print(render_markdown(payload))
        if not args.no_write:
            print(f"wrote {Path(args.out_dir).resolve()}")


if __name__ == "__main__":
    main()
