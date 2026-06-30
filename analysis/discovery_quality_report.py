#!/usr/bin/env python3
"""Discovery/source quality attribution for the Lattice scanner.

This is a read-only report. It joins scanner candidate events, live-runner
entry decisions, and closed paper trades to show which discovery sources are
producing route-ready candidates, paper entries, and runners.
"""

import argparse
import bisect
import json
import sqlite3
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from html import escape
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "scanner.db"
DEFAULT_TRADES = ROOT / "discovery" / "trades.jsonl"
DEFAULT_DECISIONS = ROOT / "discovery" / "entry_decisions.jsonl"


def safe_float(value, default=0.0):
    try:
        return float(value if value is not None else default)
    except (TypeError, ValueError):
        return default


def safe_int(value, default=0):
    try:
        return int(value if value is not None else default)
    except (TypeError, ValueError):
        return default


def pct(value):
    return f"{100 * safe_float(value):.1f}%"


def money(value):
    return f"${safe_float(value):+.2f}"


def group_key(value):
    text = str(value or "").strip()
    return text or "unknown"


def read_jsonl(path, since=None, until=None, ts_keys=("ts",)):
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
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue

            ts = 0.0
            for key in ts_keys:
                ts = safe_float(rec.get(key))
                if ts:
                    break

            if since is not None and ts and ts < since:
                continue
            if until is not None and ts and ts > until:
                continue
            rows.append(rec)

    return rows


def table_columns(db, table):
    try:
        return {
            str(row[1])
            for row in db.execute(f"PRAGMA table_info({table})").fetchall()
        }
    except sqlite3.Error:
        return set()


def load_candidate_events(db_path, since, until):
    fields = [
        "token_address",
        "symbol",
        "chain_name",
        "timestamp",
        "price",
        "liquidity",
        "score",
        "raw_score",
        "penalty",
        "pressure",
        "volume_5m",
        "volume_1h",
        "volume_liquidity_ratio",
        "buy_sell_ratio",
        "h1_volume_liquidity_ratio",
        "h1_buy_sell_ratio",
        "price_change_5m",
        "price_change_1h",
        "alert_route",
        "quality_tag",
        "lifecycle",
        "risk_flags",
        "alerted",
        "source",
        "source_family",
        "novelty_factor",
        "adjusted_score",
        "data_completeness_score",
        "evidence_bucket",
        "evidence_factor",
        "bad_evidence_penalty",
        "data_missing",
    ]

    db_path = Path(db_path)
    if not db_path.exists():
        return []

    db = None
    try:
        db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        db.row_factory = sqlite3.Row
        cols = table_columns(db, "candidate_events")
        selected = [field for field in fields if field in cols]
        if not selected:
            return []
        rows = db.execute(
            f"""
            SELECT {", ".join(selected)}
            FROM candidate_events
            WHERE timestamp >= ? AND timestamp <= ?
            ORDER BY timestamp ASC
            """,
            (since, until),
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                pass

    out = []
    for row in rows:
        rec = dict(row)
        for field in fields:
            rec.setdefault(field, None)
        out.append(rec)
    return out


def init_stats():
    return {
        "candidates": 0,
        "tokens": set(),
        "alerted": 0,
        "ready": 0,
        "adjusted_sum": 0.0,
        "adjusted_n": 0,
        "completeness_sum": 0.0,
        "completeness_n": 0,
        "decisions": 0,
        "decision_tokens": set(),
        "entries": 0,
        "alert_sent": 0,
        "blocks": Counter(),
        "bundle_n": 0,
        "bundle_value_sum": 0.0,
        "bundle_max_value": 0.0,
        "bundle_high25": 0,
        "bundle_errors": Counter(),
        "trades": 0,
        "pnl": 0.0,
        "wins": 0,
        "runners_2x": 0,
        "runners_5x": 0,
        "peak_sum": 0.0,
        "initial_stops": 0,
        "reasons": Counter(),
    }


def add_candidate(stats, row):
    token = row.get("token_address")
    stats["candidates"] += 1
    if token:
        stats["tokens"].add(token)
    stats["alerted"] += 1 if safe_int(row.get("alerted")) else 0
    if group_key(row.get("evidence_bucket")).lower() == "ready":
        stats["ready"] += 1
    adjusted = row.get("adjusted_score")
    if adjusted is not None:
        stats["adjusted_sum"] += safe_float(adjusted)
        stats["adjusted_n"] += 1
    completeness = row.get("data_completeness_score")
    if completeness is not None:
        stats["completeness_sum"] += safe_float(completeness)
        stats["completeness_n"] += 1


def add_decision(stats, row):
    token = row.get("token") or row.get("token_address")
    stats["decisions"] += 1
    if token:
        stats["decision_tokens"].add(token)
    if bool(row.get("entered")):
        stats["entries"] += 1
    if bool(row.get("alert_sent")):
        stats["alert_sent"] += 1

    bundle_value = row.get("bundle_value_pct")
    if bundle_value is not None:
        bundle_value = safe_float(bundle_value)
        stats["bundle_n"] += 1
        stats["bundle_value_sum"] += bundle_value
        stats["bundle_max_value"] = max(stats["bundle_max_value"], bundle_value)
        if bundle_value >= 25.0:
            stats["bundle_high25"] += 1

    bundle_error = group_key(row.get("bundle_error"))
    if bundle_error != "unknown":
        stats["bundle_errors"][bundle_error] += 1

    family = group_key(row.get("block_family"))
    if family != "entered":
        stats["blocks"][family] += 1


def add_trade(stats, row):
    pnl = safe_float(row.get("pnl_usd"))
    peak = safe_float(row.get("peak_mult"))
    reason = group_key(row.get("reason"))
    stats["trades"] += 1
    stats["pnl"] += pnl
    stats["wins"] += 1 if pnl > 0 else 0
    stats["runners_2x"] += 1 if peak >= 2.0 else 0
    stats["runners_5x"] += 1 if peak >= 5.0 else 0
    stats["peak_sum"] += peak
    stats["initial_stops"] += 1 if reason == "initial_stop" else 0
    stats["reasons"][reason] += 1


def finalize_stats(name, stats):
    candidates = stats["candidates"]
    decisions = stats["decisions"]
    trades = stats["trades"]
    top_block = stats["blocks"].most_common(1)
    top_reason = stats["reasons"].most_common(1)

    return {
        "name": name,
        "candidates": candidates,
        "tokens": len(stats["tokens"]),
        "alerted": stats["alerted"],
        "alert_rate": stats["alerted"] / candidates if candidates else 0.0,
        "ready": stats["ready"],
        "ready_rate": stats["ready"] / candidates if candidates else 0.0,
        "avg_adjusted_score": (
            stats["adjusted_sum"] / stats["adjusted_n"]
            if stats["adjusted_n"]
            else 0.0
        ),
        "avg_completeness": (
            stats["completeness_sum"] / stats["completeness_n"]
            if stats["completeness_n"]
            else 0.0
        ),
        "decisions": decisions,
        "decision_tokens": len(stats["decision_tokens"]),
        "entries": stats["entries"],
        "entry_rate": stats["entries"] / decisions if decisions else 0.0,
        "alert_sent": stats["alert_sent"],
        "block_rate": (
            sum(stats["blocks"].values()) / decisions if decisions else 0.0
        ),
        "top_block": top_block[0][0] if top_block else "",
        "top_block_n": top_block[0][1] if top_block else 0,
        "bundle_n": stats["bundle_n"],
        "avg_bundle_value_pct": (
            stats["bundle_value_sum"] / stats["bundle_n"]
            if stats["bundle_n"]
            else 0.0
        ),
        "max_bundle_value_pct": stats["bundle_max_value"],
        "bundle_high25_rate": (
            stats["bundle_high25"] / stats["bundle_n"]
            if stats["bundle_n"]
            else 0.0
        ),
        "bundle_error_counts": dict(stats["bundle_errors"]),
        "trades": trades,
        "pnl_usd": round(stats["pnl"], 4),
        "pnl_per_trade": stats["pnl"] / trades if trades else 0.0,
        "win_rate": stats["wins"] / trades if trades else 0.0,
        "runner_2x": stats["runners_2x"],
        "runner_2x_rate": stats["runners_2x"] / trades if trades else 0.0,
        "runner_5x": stats["runners_5x"],
        "avg_peak_mult": stats["peak_sum"] / trades if trades else 0.0,
        "initial_stop_rate": stats["initial_stops"] / trades if trades else 0.0,
        "top_exit_reason": top_reason[0][0] if top_reason else "",
        "top_exit_reason_n": top_reason[0][1] if top_reason else 0,
        "block_counts": dict(stats["blocks"]),
        "exit_reason_counts": dict(stats["reasons"]),
    }


def build_event_index(events):
    by_token = defaultdict(list)
    for row in events:
        token = row.get("token_address")
        if not token:
            continue
        by_token[token].append((safe_float(row.get("timestamp")), row))

    for rows in by_token.values():
        rows.sort(key=lambda item: item[0])

    return by_token


def match_event(index, token, ts, max_after_s=600, max_before_s=24 * 3600):
    rows = index.get(token) or []
    if not rows:
        return None

    stamps = [item[0] for item in rows]
    pos = bisect.bisect_right(stamps, ts)
    choices = []
    if pos > 0:
        before_ts, before = rows[pos - 1]
        if ts - before_ts <= max_before_s:
            choices.append((abs(ts - before_ts), before))
    if pos < len(rows):
        after_ts, after = rows[pos]
        if after_ts - ts <= max_after_s:
            choices.append((abs(after_ts - ts), after))

    if not choices:
        return None

    choices.sort(key=lambda item: item[0])
    return choices[0][1]


def add_source_shares(rows):
    scored = []
    for row in rows:
        score = (
            0.10
            + 0.55 * row["ready_rate"]
            + 0.30 * row["alert_rate"]
            + 0.35 * row["entry_rate"]
            + 1.20 * row["runner_2x_rate"]
            + 0.02 * max(min(row["pnl_per_trade"], 25.0), -25.0)
            - 0.35 * row["block_rate"]
        )

        if row["trades"] < 3:
            score *= 0.65
        if row["candidates"] < 10:
            score *= 0.70

        score = max(score, 0.03)
        row["source_quality_score"] = round(score, 4)
        scored.append(row)

    total = sum(row["source_quality_score"] for row in scored) or 1.0
    for row in scored:
        row["suggested_share_pct"] = round(
            100 * row["source_quality_score"] / total,
            1,
        )

    return rows


def build_report(
    days=3.0,
    db_path=DEFAULT_DB,
    trades_path=DEFAULT_TRADES,
    decisions_path=DEFAULT_DECISIONS,
):
    until = time.time()
    since = until - max(float(days), 0.1) * 86400
    events = load_candidate_events(db_path, since, until)
    decisions = read_jsonl(decisions_path, since, until, ("ts",))
    trades = read_jsonl(trades_path, since, until, ("entry_ts", "exit_ts"))
    event_index = build_event_index(events)

    overall = init_stats()
    by_source = defaultdict(init_stats)
    by_evidence = defaultdict(init_stats)
    by_route = defaultdict(init_stats)
    block_counts = Counter()

    for row in events:
        source = group_key(row.get("source_family"))
        evidence = group_key(row.get("evidence_bucket"))
        route = group_key(row.get("alert_route"))
        add_candidate(overall, row)
        add_candidate(by_source[source], row)
        add_candidate(by_evidence[evidence], row)
        add_candidate(by_route[route], row)

    for row in decisions:
        source = group_key(row.get("source_family"))
        evidence = group_key(row.get("evidence_bucket"))
        route = group_key(row.get("alert_route"))
        add_decision(overall, row)
        add_decision(by_source[source], row)
        add_decision(by_evidence[evidence], row)
        add_decision(by_route[route], row)
        family = group_key(row.get("block_family"))
        if family != "entered":
            block_counts[family] += 1

    unmatched_trades = 0
    for row in trades:
        token = row.get("token") or row.get("token_address")
        entry_ts = safe_float(row.get("entry_ts") or row.get("exit_ts"))
        matched = match_event(event_index, token, entry_ts)
        if not matched:
            unmatched_trades += 1
            matched = {
                "source_family": "unknown",
                "evidence_bucket": "unknown",
                "alert_route": "unknown",
            }

        source = group_key(matched.get("source_family"))
        evidence = group_key(matched.get("evidence_bucket"))
        route = group_key(matched.get("alert_route"))
        add_trade(overall, row)
        add_trade(by_source[source], row)
        add_trade(by_evidence[evidence], row)
        add_trade(by_route[route], row)

    source_rows = [
        finalize_stats(name, stats)
        for name, stats in by_source.items()
    ]
    source_rows = add_source_shares(source_rows)
    source_rows.sort(
        key=lambda row: (
            row["trades"],
            row["pnl_usd"],
            row["entries"],
            row["candidates"],
        ),
        reverse=True,
    )

    evidence_rows = [
        finalize_stats(name, stats)
        for name, stats in by_evidence.items()
    ]
    evidence_rows.sort(key=lambda row: row["candidates"], reverse=True)

    route_rows = [
        finalize_stats(name, stats)
        for name, stats in by_route.items()
    ]
    route_rows.sort(key=lambda row: row["candidates"], reverse=True)

    return {
        "generated_at": until,
        "since": since,
        "until": until,
        "days": float(days),
        "paths": {
            "db": str(db_path),
            "trades": str(trades_path),
            "decisions": str(decisions_path),
        },
        "overall": finalize_stats("overall", overall),
        "source_families": source_rows,
        "evidence_buckets": evidence_rows,
        "routes": route_rows,
        "block_counts": dict(block_counts),
        "entry_decision_rows": len(decisions),
        "candidate_event_rows": len(events),
        "trade_rows": len(trades),
        "unmatched_trade_rows": unmatched_trades,
    }


def fmt_time(ts):
    return datetime.fromtimestamp(
        safe_float(ts),
        timezone.utc,
    ).strftime("%Y-%m-%d %H:%M UTC")


def render_text(report, limit=12):
    overall = report["overall"]
    lines = [
        "Discovery Quality Report",
        (
            f"Window: {report['days']:g}d "
            f"({fmt_time(report['since'])} -> {fmt_time(report['until'])})"
        ),
        (
            f"Candidates {overall['candidates']} ({overall['tokens']} tokens), "
            f"candidate alerts {overall['alerted']} ({pct(overall['alert_rate'])}), "
            f"entry decisions {overall['decisions']}, entries {overall['entries']}"
        ),
        (
            f"Closed trades {overall['trades']} | PnL {money(overall['pnl_usd'])} | "
            f"win {pct(overall['win_rate'])} | 2x {overall['runner_2x']} "
            f"({pct(overall['runner_2x_rate'])}) | avg peak {overall['avg_peak_mult']:.2f}x"
        ),
    ]

    if report["entry_decision_rows"] == 0:
        lines.append(
            "Entry decision log has no rows in this window yet; restart the "
            "live runner after this change to start collecting block "
            "attribution."
        )
    elif overall["bundle_n"]:
        lines.append(
            f"Bundle checks: {overall['bundle_n']} | "
            f"avg value {overall['avg_bundle_value_pct']:.1f}% | "
            f"max {overall['max_bundle_value_pct']:.1f}% | "
            f">=25% {pct(overall['bundle_high25_rate'])}"
        )
    if report["unmatched_trade_rows"]:
        lines.append(
            f"Unmatched trades: {report['unmatched_trade_rows']} "
            "(no nearby candidate_event source match)."
        )

    lines.extend(["", "Source families"])
    for row in report["source_families"][:limit]:
        top_block = row["top_block"] or "-"
        lines.append(
            f"- {row['name']}: cand {row['candidates']} | ready {pct(row['ready_rate'])} | "
            f"alerts {pct(row['alert_rate'])} | entries {row['entries']}/{row['decisions']} | "
            f"trades {row['trades']} | pnl {money(row['pnl_usd'])} | "
            f"2x {pct(row['runner_2x_rate'])} | avg peak {row['avg_peak_mult']:.2f}x | "
            f"bundle {row['avg_bundle_value_pct']:.1f}%/{row['bundle_n']} | "
            f"top block {top_block} | suggested share {row['suggested_share_pct']:.1f}%"
        )

    if report["evidence_buckets"]:
        lines.extend(["", "Evidence buckets"])
        for row in report["evidence_buckets"][:min(limit, 8)]:
            lines.append(
                f"- {row['name']}: cand {row['candidates']} | alerts {pct(row['alert_rate'])} | "
                f"entries {row['entries']} | trades {row['trades']} | pnl {money(row['pnl_usd'])}"
            )

    if report["block_counts"]:
        lines.extend(["", "Entry blockers"])
        for name, count in Counter(report["block_counts"]).most_common(limit):
            lines.append(f"- {name}: {count}")

    return "\n".join(lines)


def html_text(value):
    return escape(str(value or ""), quote=False)


def render_telegram(report, limit=8):
    overall = report["overall"]
    lines = [
        "<b>[ DISCOVERY QUALITY ]</b>",
        f"Window: <code>{report['days']:g}d</code>",
        (
            f"Candidates <b>{overall['candidates']}</b> "
            f"({overall['tokens']} tokens) | ready {pct(overall['ready_rate'])} | "
            f"alerts {pct(overall['alert_rate'])}"
        ),
        (
            f"Entry decisions <b>{overall['decisions']}</b> | "
            f"entries <b>{overall['entries']}</b> | "
            f"closed trades <b>{overall['trades']}</b>"
        ),
        (
            f"PnL <b>{money(overall['pnl_usd'])}</b> | "
            f"win {pct(overall['win_rate'])} | "
            f"2x {overall['runner_2x']} ({pct(overall['runner_2x_rate'])}) | "
            f"avg peak {overall['avg_peak_mult']:.2f}x"
        ),
    ]

    if report["entry_decision_rows"] == 0:
        lines.append(
            "Entry decision log is empty for this window; restart the runner "
            "to begin collecting block attribution."
        )
    elif overall["bundle_n"]:
        lines.append(
            f"Bundle checks <b>{overall['bundle_n']}</b> | "
            f"avg {overall['avg_bundle_value_pct']:.1f}% | "
            f"max {overall['max_bundle_value_pct']:.1f}% | "
            f"&gt;=25% {pct(overall['bundle_high25_rate'])}"
        )

    lines.append("")
    lines.append("<b>Source Families</b>")
    for row in report["source_families"][:limit]:
        top_block = row["top_block"] or "-"
        lines.append(
            f"<code>{html_text(row['name'])}</code> "
            f"cand {row['candidates']} | ready {pct(row['ready_rate'])} | "
            f"ent {row['entries']}/{row['decisions']} | tr {row['trades']} | "
            f"pnl {money(row['pnl_usd'])} | 2x {pct(row['runner_2x_rate'])} | "
            f"bundle {row['avg_bundle_value_pct']:.1f}%/{row['bundle_n']} | "
            f"share {row['suggested_share_pct']:.1f}% | block {html_text(top_block)}"
        )

    if report["block_counts"]:
        lines.append("")
        lines.append("<b>Entry Blockers</b>")
        for name, count in Counter(report["block_counts"]).most_common(limit):
            lines.append(f"<code>{html_text(name)}</code> {count}")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=float, default=3.0)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--trades", default=str(DEFAULT_TRADES))
    parser.add_argument("--decisions", default=str(DEFAULT_DECISIONS))
    parser.add_argument("--limit", type=int, default=12)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    report = build_report(
        days=args.days,
        db_path=Path(args.db),
        trades_path=Path(args.trades),
        decisions_path=Path(args.decisions),
    )

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_text(report, limit=args.limit))


if __name__ == "__main__":
    main()
