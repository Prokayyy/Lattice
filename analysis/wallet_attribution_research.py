"""Offline wallet-attribution research for dormant-revival candidates.

This script is deliberately standalone: it reads local research CSVs, fetches
bounded Helius parsed transaction data for selected Solana token windows, and
writes research CSV/Markdown files. It does not import or modify live scanner
runtime.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sqlite3
import time
import urllib.error
import urllib.request
from bisect import bisect_right
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "scanner.db"
DEFAULT_OUT = ROOT / "analysis"
DEFAULT_EVENTS = DEFAULT_OUT / "dormant_revival_events.csv"
DEFAULT_EXPLOSIVE = DEFAULT_OUT / "dormant_revival_explosive_setups.csv"
HELIUS_RPC = "https://mainnet.helius-rpc.com/?api-key={key}"
HELIUS_PARSE = "https://api.helius.xyz/v0/transactions?api-key={key}"
HEADERS = {"Content-Type": "application/json", "User-Agent": "lattice-scanner-research/1.0"}


FLOW_FIELDS = [
    "target_id",
    "token_address",
    "signature_address",
    "symbol",
    "event_time",
    "wallet",
    "direction",
    "amount",
    "tx_time",
    "rel_hours",
    "phase",
    "signature",
    "tx_type",
    "approx_price",
]
WALLET_FIELDS = [
    "target_id",
    "token_address",
    "signature_address",
    "symbol",
    "wallet",
    "flags",
    "wallet_score",
    "touch_count",
    "received_count",
    "sent_count",
    "received_amount",
    "sent_amount",
    "net_amount",
    "first_receive_time",
    "first_receive_rel_hours",
    "first_send_time",
    "first_send_rel_hours",
    "first_touch_time",
    "first_touch_rel_hours",
    "pre_event_touch_count",
    "early_touch_count",
    "pre_event_receive_count",
    "early_receive_count",
    "post_event_send_count",
    "first_receive_price",
    "first_send_price",
    "first_send_vs_receive_multiple",
    "best_forward_multiple_after_first_receive",
    "realized_proxy_usd",
    "cost_proxy_usd",
    "proceeds_proxy_usd",
]
TARGET_FIELDS = [
    "target_id",
    "token_address",
    "signature_address",
    "symbol",
    "event_time",
    "source",
    "window_start",
    "window_end",
    "pages_fetched",
    "latest_signature_time",
    "oldest_signature_time",
    "signature_count_window",
    "signature_count_used",
    "truncated",
    "parsed_transactions",
    "flow_rows",
    "unique_wallets",
    "unique_touch_wallets",
    "unique_receivers",
    "unique_senders",
    "pre_event_receivers",
    "early_receivers",
    "round_trip_wallets",
    "fetch_status",
]
CROSS_FIELDS = [
    "wallet",
    "tokens_touched",
    "symbols",
    "pre_event_receiver_tokens",
    "early_receiver_tokens",
    "round_trip_tokens",
    "post_event_seller_tokens",
    "avg_wallet_score",
    "max_wallet_score",
    "first_seen",
    "last_seen",
]


def sf(value, default=0.0):
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def fmt_ts(ts):
    if not ts:
        return ""
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(ts)))


def short_addr(addr):
    if not addr:
        return ""
    if len(addr) <= 12:
        return addr
    return f"{addr[:6]}...{addr[-4:]}"


def pct(part, whole):
    if whole <= 0:
        return "0.0%"
    return f"{100.0 * part / whole:.1f}%"


def write_csv(path, rows, fieldnames):
    rows = list(rows)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path):
    if not path.exists():
        return []
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def load_env_var(name):
    value = os.environ.get(name, "")
    if value:
        return value.strip()
    for path in (ROOT / ".env", ROOT / "config.env"):
        if not path.exists():
            continue
        for raw in path.read_text(errors="ignore").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line[len("export "):].strip()
            key, val = line.split("=", 1)
            if key.strip() == name:
                return val.strip().strip('"').strip("'")
    return ""


def target_id(row):
    symbol = (row.get("symbol") or "token").replace(" ", "_").replace("/", "_")
    token = row.get("token_address") or ""
    return f"{symbol}_{token[:8]}_{int(sf(row.get('event_ts')))}"


def parse_include(spec):
    parts = [p.strip() for p in spec.split(":")]
    token = parts[0] if parts else ""
    label = parts[1] if len(parts) > 1 and parts[1] else ""
    event_ts = sf(parts[2], None) if len(parts) > 2 and parts[2] else None
    return token, label, event_ts


def parse_signature_address(spec):
    if "=" in spec:
        token, address = spec.split("=", 1)
    elif ":" in spec:
        token, address = spec.split(":", 1)
    else:
        token, address = "", spec
    return token.strip(), address.strip()


def signature_address_map(specs):
    out = {}
    default = ""
    for spec in specs:
        token, address = parse_signature_address(spec)
        if not address:
            continue
        if token:
            out[token] = address
        else:
            default = address
    return out, default


def build_targets(args):
    events = read_csv(Path(args.events))
    explosive = read_csv(Path(args.explosive))
    events_by_token = {}
    for row in events:
        token = row.get("token_address") or ""
        if token and token not in events_by_token:
            events_by_token[token] = row

    selected = []
    for row in explosive:
        if (row.get("chain_name") or "").lower() == "solana":
            item = dict(row)
            item["source"] = "explosive_setup"
            selected.append(item)

    if args.top_events > 0:
        top = [
            r for r in events
            if (r.get("chain_name") or "").lower() == "solana"
        ]
        top.sort(key=lambda r: sf(r.get("research_score")), reverse=True)
        for row in top[:args.top_events]:
            item = dict(row)
            item["source"] = "top_event"
            selected.append(item)

    for spec in args.include_token:
        token, label, explicit_ts = parse_include(spec)
        if not token:
            continue
        base = dict(events_by_token.get(token) or {})
        if not base and explicit_ts is None:
            selected.append({
                "token_address": token,
                "symbol": label or token[:8],
                "chain_name": "solana",
                "event_ts": "",
                "source": "manual_missing_event",
            })
            continue
        base["token_address"] = token
        base["symbol"] = label or base.get("symbol") or token[:8]
        base["chain_name"] = base.get("chain_name") or "solana"
        if explicit_ts is not None:
            base["event_ts"] = explicit_ts
            base["event_time"] = fmt_ts(explicit_ts)
        base["source"] = "manual_include"
        selected.append(base)

    by_token = {}
    for row in selected:
        token = row.get("token_address") or ""
        event_ts = sf(row.get("event_ts"))
        if not token:
            continue
        if event_ts <= 0 and row.get("source") != "manual_missing_event":
            continue
        key = (token, int(event_ts))
        if key in by_token:
            old = by_token[key]
            old["source"] = ",".join(sorted(set((old.get("source") or "").split(",") + [row.get("source") or ""])))
            continue
        row = dict(row)
        row["target_id"] = target_id(row)
        row["event_ts"] = event_ts
        row["event_time"] = row.get("event_time") or fmt_ts(event_ts)
        by_token[key] = row

    targets = list(by_token.values())
    targets.sort(key=lambda r: sf(r.get("research_score")), reverse=True)
    return targets


def fetch_recent_signatures(key, mint, *, max_pages, page_limit, sleep_seconds):
    signatures = []
    before = None
    pages = 0
    latest_seen = 0
    oldest_seen = 0
    for _ in range(max_pages):
        params = {"limit": page_limit}
        if before:
            params["before"] = before
        result = helius_rpc(key, "getSignaturesForAddress", [mint, params]) or []
        pages += 1
        if not result:
            break
        for row in result:
            block_time = int(row.get("blockTime") or 0)
            signature = row.get("signature") or ""
            if not block_time or not signature:
                continue
            signatures.append({"signature": signature, "blockTime": block_time})
            latest_seen = max(latest_seen, block_time)
            oldest_seen = min([oldest_seen or block_time, block_time])
        before = result[-1].get("signature")
        if not before:
            break
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)
    signatures.sort(key=lambda r: (r["blockTime"], r["signature"]))
    return {
        "signatures": signatures,
        "pages": pages,
        "latest_seen": latest_seen,
        "oldest_seen": oldest_seen,
    }


def infer_event_from_activity(key, mint, *, max_pages, page_limit, bucket_seconds, min_bucket_signatures, sleep_seconds):
    result = fetch_recent_signatures(
        key,
        mint,
        max_pages=max_pages,
        page_limit=page_limit,
        sleep_seconds=sleep_seconds,
    )
    signatures = result["signatures"]
    if not signatures:
        return 0.0, "no_signatures", result

    buckets = defaultdict(int)
    for row in signatures:
        bucket = int(row["blockTime"] // bucket_seconds) * bucket_seconds
        buckets[bucket] += 1

    max_count = max(buckets.values())
    threshold = max(int(min_bucket_signatures), int(max_count * 0.50), 1)
    candidate_buckets = [bucket for bucket, count in buckets.items() if count >= threshold]
    if not candidate_buckets:
        bucket = max(buckets, key=lambda b: buckets[b])
    else:
        bucket = min(candidate_buckets)

    result.update({
        "bucket": bucket,
        "bucket_count": buckets[bucket],
        "max_bucket_count": max_count,
        "bucket_threshold": threshold,
        "bucket_count_total": len(buckets),
    })
    return float(bucket), "inferred_activity_bucket", result


def infer_missing_target_events(key, targets, args):
    bucket_seconds = max(60, int(args.activity_bucket_minutes * 60))
    for target in targets:
        if sf(target.get("event_ts")) > 0:
            continue
        event_ts, status, detail = infer_event_from_activity(
            key,
            target["token_address"],
            max_pages=args.event_inference_pages,
            page_limit=args.page_limit,
            bucket_seconds=bucket_seconds,
            min_bucket_signatures=args.min_activity_bucket_signatures,
            sleep_seconds=args.request_sleep,
        )
        target["event_inference_status"] = status
        target["event_inference_pages"] = detail.get("pages", 0)
        target["event_inference_signature_count"] = len(detail.get("signatures") or [])
        target["event_inference_bucket_count"] = detail.get("bucket_count", 0)
        target["event_inference_max_bucket_count"] = detail.get("max_bucket_count", 0)
        if event_ts > 0:
            target["event_ts"] = event_ts
            target["event_time"] = fmt_ts(event_ts)
            source = target.get("source") or ""
            target["source"] = ",".join([s for s in (source, "activity_inferred") if s])
            target["target_id"] = target_id(target)


def helius_rpc(key, method, params, timeout=15):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(HELIUS_RPC.format(key=key), data=body, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Helius RPC HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Helius RPC URL error: {exc.reason}") from exc
    if data.get("error"):
        raise RuntimeError(f"Helius RPC error: {data['error']}")
    return data.get("result")


def fetch_signatures(key, mint, start_ts, end_ts, *, max_pages, page_limit, sleep_seconds):
    signatures = []
    before = None
    pages = 0
    latest_seen = 0
    oldest_seen = 0
    reached_start = False

    for _ in range(max_pages):
        params = {"limit": page_limit}
        if before:
            params["before"] = before
        result = helius_rpc(key, "getSignaturesForAddress", [mint, params]) or []
        pages += 1
        if not result:
            break

        times = [int(r.get("blockTime") or 0) for r in result if r.get("blockTime")]
        if times:
            latest_seen = max(latest_seen, max(times))
            oldest_seen = min([oldest_seen or min(times), min(times)])

        for row in result:
            block_time = int(row.get("blockTime") or 0)
            if start_ts <= block_time <= end_ts:
                signatures.append({
                    "signature": row.get("signature") or "",
                    "blockTime": block_time,
                })

        if times and min(times) < start_ts:
            reached_start = True
            break
        before = result[-1].get("signature")
        if not before:
            break
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)

    signatures = [s for s in signatures if s.get("signature")]
    signatures.sort(key=lambda r: (r["blockTime"], r["signature"]))
    return {
        "signatures": signatures,
        "pages": pages,
        "latest_seen": latest_seen,
        "oldest_seen": oldest_seen,
        "reached_start": reached_start,
    }


def cap_signatures(signatures, cap, mode="split"):
    if cap <= 0 or len(signatures) <= cap:
        return list(signatures), False
    if mode == "earliest":
        return list(signatures[:cap]), True
    if mode == "latest":
        return list(signatures[-cap:]), True

    first_n = cap // 2
    second_n = cap - first_n
    used = signatures[:first_n] + signatures[-second_n:]
    seen = set()
    deduped = []
    for sig in used:
        key = sig["signature"]
        if key in seen:
            continue
        seen.add(key)
        deduped.append(sig)
    deduped.sort(key=lambda r: (r["blockTime"], r["signature"]))
    return deduped, True


def parse_transactions(key, signatures, *, chunk_size, timeout, sleep_seconds):
    parsed = []
    for i in range(0, len(signatures), chunk_size):
        chunk = signatures[i:i + chunk_size]
        body = json.dumps({"transactions": [s["signature"] for s in chunk]}).encode()
        req = urllib.request.Request(HELIUS_PARSE.format(key=key), data=body, headers=HEADERS)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"Helius parse HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Helius parse URL error: {exc.reason}") from exc
        parsed.extend(data or [])
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)
    return parsed


def transfer_amount(transfer):
    val = transfer.get("tokenAmount")
    if val not in (None, ""):
        return sf(val)
    raw = transfer.get("rawTokenAmount") or {}
    token_amount = raw.get("tokenAmount")
    decimals = int(sf(raw.get("decimals"), 0))
    if token_amount in (None, ""):
        return 0.0
    return sf(token_amount) / (10 ** decimals)


def phase_for(rel_seconds, early_minutes):
    if rel_seconds < 0:
        return "pre_event"
    if rel_seconds <= early_minutes * 60:
        return "breakout_early"
    if rel_seconds <= 4 * 3600:
        return "post_event_4h"
    return "post_event_late"


def extract_flows(target, parsed_txs, *, early_minutes, fee_payer_fallback=False):
    token = target["token_address"]
    event_ts = sf(target.get("event_ts"))
    rows = []
    for tx in parsed_txs:
        signature = tx.get("signature") or ""
        tx_ts = int(tx.get("timestamp") or tx.get("blockTime") or 0)
        tx_type = tx.get("type") or ""
        if not tx_ts:
            continue
        tx_target_rows = 0
        for transfer in tx.get("tokenTransfers", []) or []:
            mint = transfer.get("mint") or transfer.get("tokenAddress")
            if mint != token:
                continue
            amount = transfer_amount(transfer)
            rel_seconds = tx_ts - event_ts
            common = {
                "target_id": target["target_id"],
                "token_address": token,
                "signature_address": target.get("signature_address") or token,
                "symbol": target.get("symbol") or "",
                "event_time": target.get("event_time") or fmt_ts(event_ts),
                "amount": round(amount, 8),
                "tx_time": fmt_ts(tx_ts),
                "rel_hours": round(rel_seconds / 3600.0, 4),
                "phase": phase_for(rel_seconds, early_minutes),
                "signature": signature,
                "tx_type": tx_type,
                "approx_price": "",
            }
            to_wallet = transfer.get("toUserAccount")
            from_wallet = transfer.get("fromUserAccount")
            if to_wallet:
                rows.append({**common, "wallet": to_wallet, "direction": "receive"})
                tx_target_rows += 1
            if from_wallet:
                rows.append({**common, "wallet": from_wallet, "direction": "send"})
                tx_target_rows += 1
        if fee_payer_fallback and tx_target_rows == 0 and tx.get("feePayer"):
            rel_seconds = tx_ts - event_ts
            rows.append({
                "target_id": target["target_id"],
                "token_address": token,
                "signature_address": target.get("signature_address") or token,
                "symbol": target.get("symbol") or "",
                "event_time": target.get("event_time") or fmt_ts(event_ts),
                "wallet": tx.get("feePayer"),
                "direction": "touch",
                "amount": 0.0,
                "tx_time": fmt_ts(tx_ts),
                "rel_hours": round(rel_seconds / 3600.0, 4),
                "phase": phase_for(rel_seconds, early_minutes),
                "signature": signature,
                "tx_type": tx_type,
                "approx_price": "",
            })
    rows.sort(key=lambda r: (r["target_id"], sf(r.get("rel_hours")), r.get("signature") or "", r.get("wallet") or ""))
    return rows


def load_price_candles(db_path, tokens, timeframe_seconds):
    out = {}
    path = Path(db_path)
    if not path.exists():
        return out
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
    except sqlite3.Error:
        return out

    for token in tokens:
        rows = []
        try:
            for row in conn.execute(
                """
                SELECT bucket_start, close, high, low
                FROM token_candles
                WHERE token_address=? AND timeframe_seconds=?
                ORDER BY bucket_start
                """,
                (token, timeframe_seconds),
            ):
                close = sf(row["close"])
                high = sf(row["high"], close)
                if close <= 0:
                    continue
                rows.append({
                    "ts": sf(row["bucket_start"]),
                    "close": close,
                    "high": max(high, close),
                    "low": sf(row["low"], close),
                })
        except sqlite3.Error:
            rows = []
        out[token] = rows
    conn.close()
    return out


def price_at(candles, ts):
    if not candles:
        return 0.0
    times = [c["ts"] for c in candles]
    i = bisect_right(times, ts) - 1
    if i < 0:
        i = 0
    return sf(candles[i].get("close"))


def high_between(candles, start_ts, end_ts):
    highs = [
        sf(c.get("high"))
        for c in candles
        if start_ts <= sf(c.get("ts")) <= end_ts and sf(c.get("high")) > 0
    ]
    if not highs:
        return 0.0
    return max(highs)


def annotate_prices(flow_rows, candles_by_token):
    for row in flow_rows:
        tx_ts = 0.0
        try:
            tx_ts = time.mktime(time.strptime(row.get("tx_time") or "", "%Y-%m-%d %H:%M:%S"))
        except ValueError:
            tx_ts = 0.0
        price = price_at(candles_by_token.get(row.get("token_address")) or [], tx_ts)
        row["approx_price"] = round(price, 12) if price > 0 else ""


def aggregate_wallets(flow_rows, targets_by_id, candles_by_token, early_minutes):
    groups = defaultdict(list)
    for row in flow_rows:
        groups[(row["target_id"], row["wallet"])].append(row)

    wallet_rows = []
    for (tid, wallet), rows in groups.items():
        target = targets_by_id.get(tid) or {}
        token = target.get("token_address") or rows[0].get("token_address") or ""
        event_ts = sf(target.get("event_ts"))
        end_ts = event_ts + sf(target.get("post_hours"), 0.0) * 3600.0
        candles = candles_by_token.get(token) or []
        receives = [r for r in rows if r.get("direction") == "receive"]
        sends = [r for r in rows if r.get("direction") == "send"]
        touches = [r for r in rows if r.get("direction") == "touch"]
        received_amount = sum(sf(r.get("amount")) for r in receives)
        sent_amount = sum(sf(r.get("amount")) for r in sends)
        receive_times = [event_ts + sf(r.get("rel_hours")) * 3600.0 for r in receives]
        send_times = [event_ts + sf(r.get("rel_hours")) * 3600.0 for r in sends]
        touch_times = [event_ts + sf(r.get("rel_hours")) * 3600.0 for r in touches]
        first_receive = min(receive_times) if receive_times else 0.0
        first_send = min(send_times) if send_times else 0.0
        first_touch = min(touch_times) if touch_times else 0.0
        first_receive_rel = (first_receive - event_ts) / 3600.0 if first_receive else ""
        first_send_rel = (first_send - event_ts) / 3600.0 if first_send else ""
        first_touch_rel = (first_touch - event_ts) / 3600.0 if first_touch else ""

        pre_event_touch_count = sum(1 for r in touches if sf(r.get("rel_hours")) < 0)
        early_touch_count = sum(1 for r in touches if 0 <= sf(r.get("rel_hours")) <= early_minutes / 60.0)
        pre_event_receive_count = sum(1 for r in receives if sf(r.get("rel_hours")) < 0)
        early_receive_count = sum(1 for r in receives if 0 <= sf(r.get("rel_hours")) <= early_minutes / 60.0)
        post_event_send_count = sum(1 for r in sends if sf(r.get("rel_hours")) >= 0)

        first_receive_price = price_at(candles, first_receive) if first_receive else 0.0
        first_send_price = price_at(candles, first_send) if first_send else 0.0
        send_mult = first_send_price / first_receive_price if first_receive_price > 0 and first_send_price > 0 else 0.0
        forward_high = high_between(candles, first_receive, end_ts) if first_receive else 0.0
        best_forward_mult = forward_high / first_receive_price if first_receive_price > 0 and forward_high > 0 else 0.0

        cost_proxy = sum(sf(r.get("amount")) * sf(r.get("approx_price")) for r in receives)
        proceeds_proxy = sum(sf(r.get("amount")) * sf(r.get("approx_price")) for r in sends)
        realized_proxy = proceeds_proxy - cost_proxy

        flags = []
        score = 0.0
        if pre_event_touch_count:
            flags.append("pre_event_actor")
            score += 2.0
        if early_touch_count:
            flags.append("early_activity_actor")
            score += 1.5
        if pre_event_receive_count:
            flags.append("pre_event_receiver")
            score += 4.0
        if early_receive_count:
            flags.append("early_breakout_receiver")
            score += 3.0
        if receives and sends:
            flags.append("round_trip_flow")
            score += 1.5
        if post_event_send_count:
            flags.append("post_event_sender")
            score += 1.0
        if send_mult >= 2.0:
            flags.append("sold_after_2x_proxy")
            score += 3.0
        elif send_mult >= 1.3:
            flags.append("sold_after_1p3x_proxy")
            score += 1.5
        if best_forward_mult >= 2.0:
            flags.append("held_through_2x_window_proxy")
            score += 2.0
        if len(rows) >= 4:
            flags.append("repeat_flow")
            score += 1.0
        if touches and not receives and not sends:
            flags.append("activity_only")
        if not flags:
            flags.append("low_signal_flow")

        wallet_rows.append({
            "target_id": tid,
            "token_address": token,
            "signature_address": target.get("signature_address") or token,
            "symbol": target.get("symbol") or rows[0].get("symbol") or "",
            "wallet": wallet,
            "flags": ",".join(flags),
            "wallet_score": round(score, 2),
            "touch_count": len(touches),
            "received_count": len(receives),
            "sent_count": len(sends),
            "received_amount": round(received_amount, 8),
            "sent_amount": round(sent_amount, 8),
            "net_amount": round(received_amount - sent_amount, 8),
            "first_receive_time": fmt_ts(first_receive),
            "first_receive_rel_hours": round(first_receive_rel, 4) if first_receive_rel != "" else "",
            "first_send_time": fmt_ts(first_send),
            "first_send_rel_hours": round(first_send_rel, 4) if first_send_rel != "" else "",
            "first_touch_time": fmt_ts(first_touch),
            "first_touch_rel_hours": round(first_touch_rel, 4) if first_touch_rel != "" else "",
            "pre_event_touch_count": pre_event_touch_count,
            "early_touch_count": early_touch_count,
            "pre_event_receive_count": pre_event_receive_count,
            "early_receive_count": early_receive_count,
            "post_event_send_count": post_event_send_count,
            "first_receive_price": round(first_receive_price, 12) if first_receive_price > 0 else "",
            "first_send_price": round(first_send_price, 12) if first_send_price > 0 else "",
            "first_send_vs_receive_multiple": round(send_mult, 4) if send_mult > 0 else "",
            "best_forward_multiple_after_first_receive": round(best_forward_mult, 4) if best_forward_mult > 0 else "",
            "realized_proxy_usd": round(realized_proxy, 6),
            "cost_proxy_usd": round(cost_proxy, 6),
            "proceeds_proxy_usd": round(proceeds_proxy, 6),
        })

    wallet_rows.sort(
        key=lambda r: (
            sf(r.get("wallet_score")),
            sf(r.get("touch_count")),
            sf(r.get("best_forward_multiple_after_first_receive")),
            sf(r.get("received_amount")),
        ),
        reverse=True,
    )
    return wallet_rows


def aggregate_cross_token(wallet_rows):
    groups = defaultdict(list)
    for row in wallet_rows:
        groups[row["wallet"]].append(row)

    out = []
    for wallet, rows in groups.items():
        tokens = sorted(set(r.get("token_address") or "" for r in rows if r.get("token_address")))
        symbols = sorted(set(r.get("symbol") or "" for r in rows if r.get("symbol")))
        first_times = [
            r.get("first_receive_time") or r.get("first_send_time") or r.get("first_touch_time")
            for r in rows
        ]
        first_times = sorted(t for t in first_times if t)
        last_times = [
            r.get("first_send_time") or r.get("first_receive_time") or r.get("first_touch_time")
            for r in rows
        ]
        last_times = sorted(t for t in last_times if t)
        out.append({
            "wallet": wallet,
            "tokens_touched": len(tokens),
            "symbols": ",".join(symbols),
            "pre_event_receiver_tokens": sum(1 for r in rows if sf(r.get("pre_event_receive_count")) > 0),
            "early_receiver_tokens": sum(1 for r in rows if sf(r.get("early_receive_count")) > 0),
            "round_trip_tokens": sum(1 for r in rows if "round_trip_flow" in (r.get("flags") or "")),
            "post_event_seller_tokens": sum(1 for r in rows if sf(r.get("post_event_send_count")) > 0),
            "avg_wallet_score": round(sum(sf(r.get("wallet_score")) for r in rows) / max(len(rows), 1), 2),
            "max_wallet_score": round(max(sf(r.get("wallet_score")) for r in rows), 2),
            "first_seen": first_times[0] if first_times else "",
            "last_seen": last_times[-1] if last_times else "",
        })
    out.sort(key=lambda r: (sf(r.get("tokens_touched")), sf(r.get("max_wallet_score"))), reverse=True)
    return out


def write_report(path, *, args, key_present, targets, target_rows, wallet_rows, cross_rows):
    lines = [
        "# Wallet Attribution Research",
        "",
        "Standalone offline research output. This is not wired into the scanner.",
        "",
        "## Status",
        "",
        f"- Helius key present: `{str(key_present).lower()}`",
        f"- Targets selected: `{len(targets)}`",
        f"- Window: `{args.pre_hours}`h before event to `{args.post_hours}`h after event",
        f"- Early receiver bucket: first `{args.early_minutes}` minutes after event",
        f"- Max signature pages per token: `{args.max_pages}`",
        f"- Max parsed signatures per token: `{args.max_signatures_per_token}`",
        f"- Signature sample mode: `{args.signature_sample_mode}`",
        "",
        "## Target Summary",
        "",
        "| symbol | source | event time | signatures used | flows | wallets | pre-event receivers | early receivers | status |",
        "|---|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for row in target_rows:
        lines.append(
            f"| `{row.get('symbol') or '?'}` | {row.get('source') or ''} "
            f"| {row.get('event_time') or ''} "
            f"| {row.get('signature_count_used') or 0} "
            f"| {row.get('flow_rows') or 0} "
            f"| {row.get('unique_wallets') or 0} "
            f"| {row.get('pre_event_receivers') or 0} "
            f"| {row.get('early_receivers') or 0} "
            f"| {row.get('fetch_status') or ''} |"
        )

    lines += [
        "",
        "## Highest-Signal Wallet Rows",
        "",
        "| rank | symbol | wallet | flags | touches | first touch | first receive | first send mult | best forward | score |",
        "|---:|---|---|---|---:|---|---|---:|---:|---:|",
    ]
    for i, row in enumerate(wallet_rows[:25], 1):
        lines.append(
            f"| {i} | `{row.get('symbol') or '?'}` "
            f"| `{short_addr(row.get('wallet') or '')}` "
            f"| {row.get('flags') or ''} "
            f"| {int(sf(row.get('touch_count')))} "
            f"| {row.get('first_touch_time') or ''} "
            f"| {row.get('first_receive_time') or ''} "
            f"| {sf(row.get('first_send_vs_receive_multiple')):.2f}x "
            f"| {sf(row.get('best_forward_multiple_after_first_receive')):.2f}x "
            f"| {sf(row.get('wallet_score')):.1f} |"
        )

    repeated = [r for r in cross_rows if int(sf(r.get("tokens_touched"))) >= 2]
    lines += [
        "",
        "## Cross-Token Repeats",
        "",
    ]
    if repeated:
        lines += [
            "| rank | wallet | tokens | symbols | pre-event tokens | early tokens | max score |",
            "|---:|---|---:|---|---:|---:|---:|",
        ]
        for i, row in enumerate(repeated[:20], 1):
            lines.append(
                f"| {i} | `{short_addr(row.get('wallet') or '')}` "
                f"| {row.get('tokens_touched') or 0} "
                f"| `{row.get('symbols') or ''}` "
                f"| {row.get('pre_event_receiver_tokens') or 0} "
                f"| {row.get('early_receiver_tokens') or 0} "
                f"| {sf(row.get('max_wallet_score')):.1f} |"
            )
    else:
        lines.append("- No wallets appeared in two or more selected token windows in this bounded pass.")

    lines += [
        "",
        "## Outputs",
        "",
        "- `analysis/wallet_attribution_targets.csv`",
        "- `analysis/wallet_attribution_flows.csv`",
        "- `analysis/wallet_attribution_wallets.csv`",
        "- `analysis/wallet_attribution_cross_token_wallets.csv`",
        "",
        "## Interpretation",
        "",
        "- `receive` means the wallet received the target token in a parsed transfer; it is a buy-side proxy, not proof of an open-market buy.",
        "- `send` means the wallet sent the target token; it is a sell-side proxy, not proof of a profitable exit.",
        "- `touch` means the transaction fee payer touched the selected signature address when target-token transfer details were not exposed; it is participation evidence, not direction.",
        "- Price multiples use local 15m scanner candles, so they are rough research labels, not settlement-grade PnL.",
        "- Insider status is not claimed here. The next evidence layer is deployer linkage, funding-source clustering, and repeated early participation across many runners.",
        "",
    ]
    path.write_text("\n".join(lines))


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--events", default=str(DEFAULT_EVENTS))
    parser.add_argument("--explosive", default=str(DEFAULT_EXPLOSIVE))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT))
    parser.add_argument("--timeframe-seconds", type=int, default=900)
    parser.add_argument("--top-events", type=int, default=0)
    parser.add_argument("--include-token", action="append", default=[], help="MINT[:LABEL[:EVENT_TS]]")
    parser.add_argument("--signature-address", action="append", default=[], help="Optional MINT=ADDRESS to fetch signatures from a pool/pair instead of the mint")
    parser.add_argument("--fee-payer-fallback", action="store_true", help="Emit feePayer touch rows when parsed target-token transfers are absent")
    parser.add_argument("--pre-hours", type=float, default=6.0)
    parser.add_argument("--post-hours", type=float, default=24.0)
    parser.add_argument("--early-minutes", type=float, default=15.0)
    parser.add_argument("--max-pages", type=int, default=8)
    parser.add_argument("--page-limit", type=int, default=1000)
    parser.add_argument("--max-signatures-per-token", type=int, default=600)
    parser.add_argument("--signature-sample-mode", choices=("split", "earliest", "latest"), default="split")
    parser.add_argument("--chunk-size", type=int, default=100)
    parser.add_argument("--request-timeout", type=float, default=30.0)
    parser.add_argument("--request-sleep", type=float, default=0.05)
    parser.add_argument("--event-inference-pages", type=int, default=12)
    parser.add_argument("--activity-bucket-minutes", type=float, default=15.0)
    parser.add_argument("--min-activity-bucket-signatures", type=int, default=8)
    return parser.parse_args()


def main():
    args = parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    key = load_env_var("HELIUS_API_KEY")
    targets = build_targets(args)
    if key:
        infer_missing_target_events(key, targets, args)
    sig_map, default_sig_addr = signature_address_map(args.signature_address)
    for target in targets:
        target["pre_hours"] = args.pre_hours
        target["post_hours"] = args.post_hours
        target["signature_address"] = sig_map.get(target["token_address"]) or default_sig_addr or target["token_address"]

    flow_rows = []
    target_rows = []
    if key:
        for target in targets:
            event_ts = sf(target.get("event_ts"))
            start_ts = event_ts - args.pre_hours * 3600.0
            end_ts = event_ts + args.post_hours * 3600.0
            base_row = {
                "target_id": target["target_id"],
                "token_address": target["token_address"],
                "signature_address": target.get("signature_address") or target["token_address"],
                "symbol": target.get("symbol") or "",
                "event_time": target.get("event_time") or fmt_ts(event_ts),
                "source": target.get("source") or "",
                "window_start": fmt_ts(start_ts),
                "window_end": fmt_ts(end_ts),
                "pages_fetched": 0,
                "latest_signature_time": "",
                "oldest_signature_time": "",
                "signature_count_window": 0,
                "signature_count_used": 0,
                "truncated": 0,
                "parsed_transactions": 0,
                "flow_rows": 0,
                "unique_wallets": 0,
                "unique_touch_wallets": 0,
                "unique_receivers": 0,
                "unique_senders": 0,
                "pre_event_receivers": 0,
                "early_receivers": 0,
                "round_trip_wallets": 0,
                "fetch_status": "ok",
            }
            if event_ts <= 0:
                base_row["fetch_status"] = target.get("event_inference_status") or "no_event_timestamp"
                target_rows.append(base_row)
                continue
            try:
                sig_result = fetch_signatures(
                    key,
                    target.get("signature_address") or target["token_address"],
                    start_ts,
                    end_ts,
                    max_pages=args.max_pages,
                    page_limit=args.page_limit,
                    sleep_seconds=args.request_sleep,
                )
                signatures = sig_result["signatures"]
                used_signatures, truncated = cap_signatures(
                    signatures,
                    args.max_signatures_per_token,
                    args.signature_sample_mode,
                )
                parsed = parse_transactions(
                    key,
                    used_signatures,
                    chunk_size=args.chunk_size,
                    timeout=args.request_timeout,
                    sleep_seconds=args.request_sleep,
                )
                rows = extract_flows(
                    target,
                    parsed,
                    early_minutes=args.early_minutes,
                    fee_payer_fallback=args.fee_payer_fallback,
                )
                flow_rows.extend(rows)

                wallets = set(r["wallet"] for r in rows if r.get("wallet"))
                touch_wallets = set(r["wallet"] for r in rows if r.get("direction") == "touch")
                receivers = set(r["wallet"] for r in rows if r.get("direction") == "receive")
                senders = set(r["wallet"] for r in rows if r.get("direction") == "send")
                pre_receivers = set(
                    r["wallet"] for r in rows
                    if r.get("direction") == "receive" and sf(r.get("rel_hours")) < 0
                )
                early_receivers = set(
                    r["wallet"] for r in rows
                    if r.get("direction") == "receive" and 0 <= sf(r.get("rel_hours")) <= args.early_minutes / 60.0
                )
                round_trips = receivers & senders
                base_row.update({
                    "pages_fetched": sig_result["pages"],
                    "latest_signature_time": fmt_ts(sig_result["latest_seen"]),
                    "oldest_signature_time": fmt_ts(sig_result["oldest_seen"]),
                    "signature_count_window": len(signatures),
                    "signature_count_used": len(used_signatures),
                    "truncated": int(truncated),
                    "parsed_transactions": len(parsed),
                    "flow_rows": len(rows),
                    "unique_wallets": len(wallets),
                    "unique_touch_wallets": len(touch_wallets),
                    "unique_receivers": len(receivers),
                    "unique_senders": len(senders),
                    "pre_event_receivers": len(pre_receivers),
                    "early_receivers": len(early_receivers),
                    "round_trip_wallets": len(round_trips),
                })
                if not sig_result.get("reached_start"):
                    base_row["fetch_status"] = "partial_window"
            except Exception as exc:
                base_row["fetch_status"] = str(exc)
            target_rows.append(base_row)
    else:
        for target in targets:
            event_ts = sf(target.get("event_ts"))
            target_rows.append({
                "target_id": target["target_id"],
                "token_address": target["token_address"],
                "signature_address": target.get("signature_address") or target["token_address"],
                "symbol": target.get("symbol") or "",
                "event_time": target.get("event_time") or fmt_ts(event_ts),
                "source": target.get("source") or "",
                "window_start": fmt_ts(event_ts - args.pre_hours * 3600.0),
                "window_end": fmt_ts(event_ts + args.post_hours * 3600.0),
                "fetch_status": "missing_helius_key",
            })

    candles_by_token = load_price_candles(
        args.db,
        [t["token_address"] for t in targets],
        args.timeframe_seconds,
    )
    annotate_prices(flow_rows, candles_by_token)
    targets_by_id = {t["target_id"]: t for t in targets}
    wallet_rows = aggregate_wallets(flow_rows, targets_by_id, candles_by_token, args.early_minutes)
    cross_rows = aggregate_cross_token(wallet_rows)

    write_csv(out_dir / "wallet_attribution_targets.csv", target_rows, TARGET_FIELDS)
    write_csv(out_dir / "wallet_attribution_flows.csv", flow_rows, FLOW_FIELDS)
    write_csv(out_dir / "wallet_attribution_wallets.csv", wallet_rows, WALLET_FIELDS)
    write_csv(out_dir / "wallet_attribution_cross_token_wallets.csv", cross_rows, CROSS_FIELDS)
    write_report(
        out_dir / "wallet_attribution_report.md",
        args=args,
        key_present=bool(key),
        targets=targets,
        target_rows=target_rows,
        wallet_rows=wallet_rows,
        cross_rows=cross_rows,
    )

    print(f"targets={len(targets)} flows={len(flow_rows)} wallets={len(wallet_rows)} cross_wallets={len(cross_rows)}")
    print(f"wrote {out_dir / 'wallet_attribution_targets.csv'}")
    print(f"wrote {out_dir / 'wallet_attribution_flows.csv'}")
    print(f"wrote {out_dir / 'wallet_attribution_wallets.csv'}")
    print(f"wrote {out_dir / 'wallet_attribution_cross_token_wallets.csv'}")
    print(f"wrote {out_dir / 'wallet_attribution_report.md'}")


if __name__ == "__main__":
    main()
