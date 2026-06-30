"""Alchemy RPC wallet evidence for dormant-revival candidates.

Standalone offline research. It reads no live scanner runtime state, makes
bounded Solana JSON-RPC calls through Alchemy, and writes evidence CSV/Markdown
files for:

- owner-level token balance deltas around candidate windows
- fee-payer activity fallback when no target-token balance delta is exposed
- funding-source candidates for early actors
- bounded token/pair origin candidates
- repeated early participation across selected targets
"""

from __future__ import annotations

import argparse
import csv
import json
import time
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "analysis" / "alchemy_wallet_evidence"
HEADERS = {"Content-Type": "application/json", "User-Agent": "lattice-scanner-research/1.0"}


ACTOR_FIELDS = [
    "target_id",
    "symbol",
    "token_address",
    "signature_address",
    "wallet",
    "role_flags",
    "first_seen_time",
    "first_seen_rel_hours",
    "tx_count",
    "touch_count",
    "token_delta_count",
    "receive_count",
    "send_count",
    "pre_event_touch_count",
    "early_touch_count",
    "pre_event_receive_count",
    "early_receive_count",
    "net_token_delta",
    "abs_token_delta",
    "first_receive_time",
    "first_send_time",
]
TARGET_FIELDS = [
    "target_id",
    "symbol",
    "token_address",
    "signature_address",
    "event_time",
    "window_start",
    "window_end",
    "signature_count_window",
    "signature_count_used",
    "truncated",
    "transactions_parsed",
    "actor_wallets",
    "token_delta_wallets",
    "touch_wallets",
    "early_actor_wallets",
    "status",
]
CROSS_FIELDS = [
    "wallet",
    "targets_touched",
    "symbols",
    "early_targets",
    "token_delta_targets",
    "touch_only_targets",
    "first_seen",
    "last_seen",
]
FUNDING_FIELDS = [
    "wallet",
    "wallet_first_seen_time",
    "funding_time",
    "signature",
    "fee_payer",
    "target_native_delta_sol",
    "candidate_funder",
    "candidate_funder_delta_sol",
    "other_negative_wallets",
    "status",
]
FUNDING_CLUSTER_FIELDS = [
    "candidate_funder",
    "funded_wallet_count",
    "funded_wallets",
    "total_target_native_delta_sol",
    "first_funding_time",
    "last_funding_time",
]
ORIGIN_FIELDS = [
    "target_id",
    "symbol",
    "address_type",
    "address",
    "status",
    "pages_fetched",
    "signatures_seen",
    "oldest_signature_time",
    "oldest_signature",
    "oldest_fee_payer",
    "oldest_signers",
    "initialize_mint_seen",
]


def sf(value, default=0.0):
    try:
        if value in (None, ""):
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
    return addr if len(addr) <= 12 else f"{addr[:6]}...{addr[-4:]}"


def load_env_var(name):
    import os

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


def rpc_url():
    explicit = load_env_var("ALCHEMY_SOLANA_RPC_URL")
    if explicit:
        return explicit
    key = load_env_var("ALCHEMY_API_KEY")
    if key:
        return f"https://solana-mainnet.g.alchemy.com/v2/{key}"
    return ""


def rpc_call(url, method, params, *, timeout=30):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    for attempt in range(6):
        req = urllib.request.Request(url, data=body, headers=HEADERS)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode())
            break
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < 5:
                delay = sf(exc.headers.get("Retry-After"), 0.0) or min(2 ** attempt, 12)
                time.sleep(delay)
                continue
            raise RuntimeError(f"Alchemy HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            if attempt < 5:
                time.sleep(min(2 ** attempt, 12))
                continue
            raise RuntimeError(f"Alchemy URL error: {exc.reason}") from exc
    if data.get("error"):
        raise RuntimeError(f"Alchemy RPC error: {data['error']}")
    return data.get("result")


def rpc_batch(url, calls, *, timeout=60):
    if not calls:
        return []
    body = json.dumps([
        {"jsonrpc": "2.0", "id": i, "method": method, "params": params}
        for i, (method, params) in enumerate(calls)
    ]).encode()
    for attempt in range(6):
        req = urllib.request.Request(url, data=body, headers=HEADERS)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode())
            break
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < 5:
                delay = sf(exc.headers.get("Retry-After"), 0.0) or min(2 ** attempt, 12)
                time.sleep(delay)
                continue
            raise RuntimeError(f"Alchemy batch HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            if attempt < 5:
                time.sleep(min(2 ** attempt, 12))
                continue
            raise RuntimeError(f"Alchemy batch URL error: {exc.reason}") from exc
    if isinstance(data, dict):
        if data.get("error"):
            raise RuntimeError(f"Alchemy batch error: {data['error']}")
        return [data.get("result")]
    by_id = {item.get("id"): item for item in data or []}
    results = []
    for i in range(len(calls)):
        item = by_id.get(i) or {}
        if item.get("error"):
            results.append(None)
        else:
            results.append(item.get("result"))
    return results


def parse_target(spec):
    parts = [p.strip() for p in spec.split(":")]
    if len(parts) < 3:
        raise ValueError("target must be TOKEN:SYMBOL:EVENT_TS[:SIGNATURE_ADDRESS]")
    token, symbol, event_ts = parts[:3]
    signature_address = parts[3] if len(parts) > 3 and parts[3] else token
    return {
        "token_address": token,
        "symbol": symbol or token[:8],
        "event_ts": sf(event_ts),
        "signature_address": signature_address,
        "target_id": f"{symbol or token[:8]}_{token[:8]}_{int(sf(event_ts))}",
    }


def write_csv(path, rows, fieldnames):
    rows = list(rows)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def fetch_signatures(url, address, start_ts=None, end_ts=None, *, max_pages=10, page_limit=1000, sleep_seconds=0.0):
    out = []
    before = None
    pages = 0
    oldest_seen = 0
    latest_seen = 0
    reached_start = False
    for _ in range(max_pages):
        cfg = {"limit": page_limit}
        if before:
            cfg["before"] = before
        result = rpc_call(url, "getSignaturesForAddress", [address, cfg]) or []
        pages += 1
        if not result:
            reached_start = True
            break
        times = [int(r.get("blockTime") or 0) for r in result if r.get("blockTime")]
        if times:
            oldest_seen = min(oldest_seen or min(times), min(times))
            latest_seen = max(latest_seen, max(times))
        for row in result:
            ts = int(row.get("blockTime") or 0)
            sig = row.get("signature") or ""
            if not ts or not sig:
                continue
            if start_ts is not None and ts < start_ts:
                continue
            if end_ts is not None and ts > end_ts:
                continue
            out.append({"signature": sig, "blockTime": ts})
        if start_ts is not None and times and min(times) < start_ts:
            reached_start = True
            break
        before = result[-1].get("signature")
        if not before:
            break
        if sleep_seconds:
            time.sleep(sleep_seconds)
    out.sort(key=lambda r: (r["blockTime"], r["signature"]))
    return {
        "signatures": out,
        "pages": pages,
        "oldest_seen": oldest_seen,
        "latest_seen": latest_seen,
        "reached_start": reached_start,
    }


def cap_signatures(signatures, cap, mode):
    if cap <= 0 or len(signatures) <= cap:
        return list(signatures), False
    if mode == "earliest":
        return list(signatures[:cap]), True
    if mode == "latest":
        return list(signatures[-cap:]), True
    first_n = cap // 2
    second_n = cap - first_n
    return signatures[:first_n] + signatures[-second_n:], True


def fetch_transactions(url, signatures, *, batch_size=25, sleep_seconds=0.0):
    calls = [
        ("getTransaction", [
            sig,
            {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0},
        ])
        for sig in signatures
    ]
    out = []
    for i in range(0, len(calls), batch_size):
        chunk = calls[i:i + batch_size]
        out.extend(rpc_batch(url, chunk, timeout=90))
        if sleep_seconds:
            time.sleep(sleep_seconds)
    return out


def account_keys(tx):
    msg = ((tx or {}).get("transaction") or {}).get("message") or {}
    raw = msg.get("accountKeys") or []
    keys = []
    signers = []
    for item in raw:
        if isinstance(item, dict):
            pubkey = item.get("pubkey") or ""
            signer = bool(item.get("signer"))
        else:
            pubkey = str(item)
            signer = False
        keys.append(pubkey)
        if signer and pubkey:
            signers.append(pubkey)
    return keys, signers


def raw_amount(balance):
    amt = ((balance or {}).get("uiTokenAmount") or {}).get("amount")
    decimals = int(sf(((balance or {}).get("uiTokenAmount") or {}).get("decimals"), 0))
    if amt in (None, ""):
        return 0.0, decimals
    return sf(amt) / (10 ** decimals), decimals


def token_owner_deltas(tx, mint):
    meta = (tx or {}).get("meta") or {}
    pre = {}
    post = {}
    for balance in meta.get("preTokenBalances") or []:
        if balance.get("mint") != mint:
            continue
        owner = balance.get("owner") or f"account_index:{balance.get('accountIndex')}"
        amount, _ = raw_amount(balance)
        pre[owner] = pre.get(owner, 0.0) + amount
    for balance in meta.get("postTokenBalances") or []:
        if balance.get("mint") != mint:
            continue
        owner = balance.get("owner") or f"account_index:{balance.get('accountIndex')}"
        amount, _ = raw_amount(balance)
        post[owner] = post.get(owner, 0.0) + amount
    owners = set(pre) | set(post)
    return {owner: post.get(owner, 0.0) - pre.get(owner, 0.0) for owner in owners}


def native_deltas(tx):
    meta = (tx or {}).get("meta") or {}
    pre = meta.get("preBalances") or []
    post = meta.get("postBalances") or []
    keys, _ = account_keys(tx)
    out = {}
    for i, key in enumerate(keys[:min(len(pre), len(post))]):
        out[key] = (sf(post[i]) - sf(pre[i])) / 1_000_000_000.0
    return out


def phase(rel_seconds, early_minutes):
    if rel_seconds < 0:
        return "pre_event"
    if rel_seconds <= early_minutes * 60:
        return "early"
    return "late"


def actor_rows_for_target(target, parsed_txs, *, early_minutes, fee_payer_fallback):
    event_ts = sf(target["event_ts"])
    excluded_actors = {
        target.get("token_address") or "",
        target.get("signature_address") or "",
    }
    groups = defaultdict(lambda: {
        "tx_count": 0,
        "touch_count": 0,
        "token_delta_count": 0,
        "receive_count": 0,
        "send_count": 0,
        "pre_event_touch_count": 0,
        "early_touch_count": 0,
        "pre_event_receive_count": 0,
        "early_receive_count": 0,
        "net_token_delta": 0.0,
        "abs_token_delta": 0.0,
        "first_seen_ts": 0.0,
        "first_receive_ts": 0.0,
        "first_send_ts": 0.0,
    })
    for tx in parsed_txs:
        if not tx:
            continue
        tx_ts = sf(tx.get("blockTime"))
        if tx_ts <= 0:
            continue
        rel = tx_ts - event_ts
        ph = phase(rel, early_minutes)
        deltas = {
            owner: delta
            for owner, delta in token_owner_deltas(tx, target["token_address"]).items()
            if abs(delta) > 0 and owner not in excluded_actors
        }
        for owner, delta in deltas.items():
            row = groups[owner]
            row["tx_count"] += 1
            row["token_delta_count"] += 1
            row["net_token_delta"] += delta
            row["abs_token_delta"] += abs(delta)
            row["first_seen_ts"] = tx_ts if not row["first_seen_ts"] else min(row["first_seen_ts"], tx_ts)
            if delta > 0:
                row["receive_count"] += 1
                row["first_receive_ts"] = tx_ts if not row["first_receive_ts"] else min(row["first_receive_ts"], tx_ts)
                if ph == "pre_event":
                    row["pre_event_receive_count"] += 1
                elif ph == "early":
                    row["early_receive_count"] += 1
            elif delta < 0:
                row["send_count"] += 1
                row["first_send_ts"] = tx_ts if not row["first_send_ts"] else min(row["first_send_ts"], tx_ts)
        if fee_payer_fallback and not deltas:
            keys, _ = account_keys(tx)
            payer = keys[0] if keys else ""
            if payer and payer not in excluded_actors:
                row = groups[payer]
                row["tx_count"] += 1
                row["touch_count"] += 1
                row["first_seen_ts"] = tx_ts if not row["first_seen_ts"] else min(row["first_seen_ts"], tx_ts)
                if ph == "pre_event":
                    row["pre_event_touch_count"] += 1
                elif ph == "early":
                    row["early_touch_count"] += 1

    rows = []
    for wallet, data in groups.items():
        flags = []
        if data["pre_event_receive_count"]:
            flags.append("pre_event_receiver")
        if data["early_receive_count"]:
            flags.append("early_receiver")
        if data["pre_event_touch_count"]:
            flags.append("pre_event_actor")
        if data["early_touch_count"]:
            flags.append("early_actor")
        if data["receive_count"] and data["send_count"]:
            flags.append("round_trip_delta")
        if data["touch_count"] and not data["token_delta_count"]:
            flags.append("activity_only")
        if data["tx_count"] >= 4:
            flags.append("repeat_activity")
        if not flags:
            flags.append("low_signal")
        first_seen = data["first_seen_ts"]
        rows.append({
            "target_id": target["target_id"],
            "symbol": target["symbol"],
            "token_address": target["token_address"],
            "signature_address": target["signature_address"],
            "wallet": wallet,
            "role_flags": ",".join(flags),
            "first_seen_time": fmt_ts(first_seen),
            "first_seen_rel_hours": round((first_seen - event_ts) / 3600.0, 4) if first_seen else "",
            "tx_count": data["tx_count"],
            "touch_count": data["touch_count"],
            "token_delta_count": data["token_delta_count"],
            "receive_count": data["receive_count"],
            "send_count": data["send_count"],
            "pre_event_touch_count": data["pre_event_touch_count"],
            "early_touch_count": data["early_touch_count"],
            "pre_event_receive_count": data["pre_event_receive_count"],
            "early_receive_count": data["early_receive_count"],
            "net_token_delta": round(data["net_token_delta"], 8),
            "abs_token_delta": round(data["abs_token_delta"], 8),
            "first_receive_time": fmt_ts(data["first_receive_ts"]),
            "first_send_time": fmt_ts(data["first_send_ts"]),
        })
    rows.sort(
        key=lambda r: (
            int(sf(r.get("early_receive_count"))) + int(sf(r.get("early_touch_count"))),
            int(sf(r.get("receive_count"))),
            int(sf(r.get("tx_count"))),
            sf(r.get("abs_token_delta")),
        ),
        reverse=True,
    )
    return rows


def target_evidence(url, target, args):
    start_ts = sf(target["event_ts"]) - args.pre_hours * 3600.0
    end_ts = sf(target["event_ts"]) + args.post_hours * 3600.0
    sig_result = fetch_signatures(
        url,
        target["signature_address"],
        start_ts,
        end_ts,
        max_pages=args.max_pages,
        page_limit=args.page_limit,
        sleep_seconds=args.request_sleep,
    )
    signatures, truncated = cap_signatures(sig_result["signatures"], args.max_signatures_per_target, args.signature_sample_mode)
    parsed = fetch_transactions(
        url,
        [s["signature"] for s in signatures],
        batch_size=args.batch_size,
        sleep_seconds=args.request_sleep,
    )
    actor_rows = actor_rows_for_target(
        target,
        parsed,
        early_minutes=args.early_minutes,
        fee_payer_fallback=args.fee_payer_fallback,
    )
    early_wallets = [
        r for r in actor_rows
        if int(sf(r.get("early_receive_count"))) > 0 or int(sf(r.get("early_touch_count"))) > 0
    ]
    token_delta_wallets = [r for r in actor_rows if int(sf(r.get("token_delta_count"))) > 0]
    touch_wallets = [r for r in actor_rows if int(sf(r.get("touch_count"))) > 0]
    target_row = {
        "target_id": target["target_id"],
        "symbol": target["symbol"],
        "token_address": target["token_address"],
        "signature_address": target["signature_address"],
        "event_time": fmt_ts(target["event_ts"]),
        "window_start": fmt_ts(start_ts),
        "window_end": fmt_ts(end_ts),
        "signature_count_window": len(sig_result["signatures"]),
        "signature_count_used": len(signatures),
        "truncated": int(truncated),
        "transactions_parsed": sum(1 for tx in parsed if tx),
        "actor_wallets": len(actor_rows),
        "token_delta_wallets": len(token_delta_wallets),
        "touch_wallets": len(touch_wallets),
        "early_actor_wallets": len(early_wallets),
        "status": "ok" if sig_result.get("reached_start") else "partial_window",
    }
    return target_row, actor_rows


def cross_target_rows(actor_rows):
    groups = defaultdict(list)
    for row in actor_rows:
        groups[row["wallet"]].append(row)
    out = []
    for wallet, rows in groups.items():
        targets = sorted(set(r["target_id"] for r in rows))
        symbols = sorted(set(r["symbol"] for r in rows))
        early = sum(
            1 for r in rows
            if int(sf(r.get("early_receive_count"))) > 0 or int(sf(r.get("early_touch_count"))) > 0
        )
        delta_targets = sum(1 for r in rows if int(sf(r.get("token_delta_count"))) > 0)
        touch_only = sum(1 for r in rows if int(sf(r.get("touch_count"))) > 0 and int(sf(r.get("token_delta_count"))) == 0)
        times = sorted(r["first_seen_time"] for r in rows if r.get("first_seen_time"))
        out.append({
            "wallet": wallet,
            "targets_touched": len(targets),
            "symbols": ",".join(symbols),
            "early_targets": early,
            "token_delta_targets": delta_targets,
            "touch_only_targets": touch_only,
            "first_seen": times[0] if times else "",
            "last_seen": times[-1] if times else "",
        })
    out.sort(key=lambda r: (int(sf(r["targets_touched"])), int(sf(r["early_targets"]))), reverse=True)
    return out


def signer_info(tx):
    keys, signers = account_keys(tx)
    return (keys[0] if keys else ""), signers


def origin_candidate(url, target, address_type, address, args):
    result = fetch_signatures(
        url,
        address,
        None,
        None,
        max_pages=args.origin_pages,
        page_limit=args.page_limit,
        sleep_seconds=args.request_sleep,
    )
    sigs = result["signatures"]
    row = {
        "target_id": target["target_id"],
        "symbol": target["symbol"],
        "address_type": address_type,
        "address": address,
        "status": "no_signatures",
        "pages_fetched": result["pages"],
        "signatures_seen": len(sigs),
        "oldest_signature_time": "",
        "oldest_signature": "",
        "oldest_fee_payer": "",
        "oldest_signers": "",
        "initialize_mint_seen": 0,
    }
    if not sigs:
        return row
    oldest = sigs[0]
    row["oldest_signature_time"] = fmt_ts(oldest["blockTime"])
    row["oldest_signature"] = oldest["signature"]
    row["status"] = "exhausted_history" if result.get("reached_start") else "bounded_oldest_only"
    tx = fetch_transactions(url, [oldest["signature"]], batch_size=1)[0]
    if tx:
        payer, signers = signer_info(tx)
        row["oldest_fee_payer"] = payer
        row["oldest_signers"] = ",".join(signers)
        text = json.dumps(((tx.get("transaction") or {}).get("message") or {}).get("instructions") or [])
        row["initialize_mint_seen"] = int("initializeMint" in text or "initializeMint2" in text)
    return row


def funding_edges(url, actor_rows, args):
    selected = [
        row for row in actor_rows
        if int(sf(row.get("early_receive_count"))) > 0 or int(sf(row.get("early_touch_count"))) > 0
    ][:args.funding_top_wallets]
    out = []
    for actor in selected:
        wallet = actor["wallet"]
        first_seen = 0.0
        if actor.get("first_seen_time"):
            first_seen = time.mktime(time.strptime(actor["first_seen_time"], "%Y-%m-%d %H:%M:%S"))
        start_ts = first_seen - args.funding_lookback_days * 86400.0 if first_seen else None
        end_ts = first_seen if first_seen else None
        try:
            sig_result = fetch_signatures(
                url,
                wallet,
                start_ts,
                end_ts,
                max_pages=args.funding_pages,
                page_limit=args.page_limit,
                sleep_seconds=args.request_sleep,
            )
            signatures, _ = cap_signatures(sig_result["signatures"], args.funding_max_signatures, "latest")
            parsed = fetch_transactions(url, [s["signature"] for s in signatures], batch_size=args.batch_size, sleep_seconds=args.request_sleep)
        except Exception as exc:
            out.append({
                "wallet": wallet,
                "wallet_first_seen_time": actor.get("first_seen_time") or "",
                "status": str(exc),
            })
            continue
        found = False
        for tx in parsed:
            if not tx:
                continue
            tx_ts = sf(tx.get("blockTime"))
            native = native_deltas(tx)
            target_delta = native.get(wallet, 0.0)
            if target_delta < args.funding_min_sol:
                continue
            negatives = [
                (addr, delta)
                for addr, delta in native.items()
                if addr != wallet and delta < -args.funding_min_sol
            ]
            negatives.sort(key=lambda item: abs(item[1]), reverse=True)
            payer, _ = signer_info(tx)
            candidate = negatives[0] if negatives else ("", 0.0)
            out.append({
                "wallet": wallet,
                "wallet_first_seen_time": actor.get("first_seen_time") or "",
                "funding_time": fmt_ts(tx_ts),
                "signature": ((tx.get("transaction") or {}).get("signatures") or [""])[0],
                "fee_payer": payer,
                "target_native_delta_sol": round(target_delta, 9),
                "candidate_funder": candidate[0],
                "candidate_funder_delta_sol": round(candidate[1], 9),
                "other_negative_wallets": ",".join(addr for addr, _ in negatives[1:6]),
                "status": "ok" if candidate[0] else "no_negative_counterparty",
            })
            found = True
        if not found:
            out.append({
                "wallet": wallet,
                "wallet_first_seen_time": actor.get("first_seen_time") or "",
                "status": "no_recent_native_funding_above_threshold",
            })
    out.sort(key=lambda r: (r.get("candidate_funder") or "", sf(r.get("target_native_delta_sol"))), reverse=True)
    return out


def funding_clusters(edges):
    groups = defaultdict(list)
    for row in edges:
        funder = row.get("candidate_funder") or ""
        if not funder:
            continue
        groups[funder].append(row)
    out = []
    for funder, rows in groups.items():
        wallets = sorted(set(r.get("wallet") or "" for r in rows if r.get("wallet")))
        times = sorted(r.get("funding_time") or "" for r in rows if r.get("funding_time"))
        out.append({
            "candidate_funder": funder,
            "funded_wallet_count": len(wallets),
            "funded_wallets": ",".join(wallets),
            "total_target_native_delta_sol": round(sum(sf(r.get("target_native_delta_sol")) for r in rows), 9),
            "first_funding_time": times[0] if times else "",
            "last_funding_time": times[-1] if times else "",
        })
    out.sort(key=lambda r: (int(sf(r["funded_wallet_count"])), sf(r["total_target_native_delta_sol"])), reverse=True)
    return out


def write_report(path, *, args, target_rows, actor_rows, cross_rows, funding_rows, clusters, origins):
    lines = [
        "# Alchemy Wallet Evidence",
        "",
        "Standalone offline research output. This is not wired into the scanner.",
        "",
        "## Run",
        "",
        f"- Targets: `{len(target_rows)}`",
        f"- Target window: `{args.pre_hours}`h before to `{args.post_hours}`h after event",
        f"- Early bucket: `{args.early_minutes}` minutes",
        f"- Parsed cap per target: `{args.max_signatures_per_target}` signatures",
        f"- Fee-payer fallback: `{str(args.fee_payer_fallback).lower()}`",
        "",
        "## Target Summary",
        "",
        "| symbol | signatures used | parsed tx | actor wallets | token-delta wallets | early actors | status |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for row in target_rows:
        lines.append(
            f"| `{row.get('symbol')}` | {row.get('signature_count_used')} | {row.get('transactions_parsed')} "
            f"| {row.get('actor_wallets')} | {row.get('token_delta_wallets')} | {row.get('early_actor_wallets')} "
            f"| {row.get('status')} |"
        )
    lines += [
        "",
        "## Top Actors",
        "",
        "| rank | symbol | wallet | flags | tx | touches | receive | send | net token | first seen |",
        "|---:|---|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for i, row in enumerate(actor_rows[:30], 1):
        lines.append(
            f"| {i} | `{row.get('symbol')}` | `{short_addr(row.get('wallet'))}` | {row.get('role_flags')} "
            f"| {row.get('tx_count')} | {row.get('touch_count')} | {row.get('receive_count')} | {row.get('send_count')} "
            f"| {sf(row.get('net_token_delta')):.4f} | {row.get('first_seen_time')} |"
        )
    repeated = [r for r in cross_rows if int(sf(r.get("targets_touched"))) >= 2]
    lines += [
        "",
        "## Repeated Participation",
        "",
    ]
    if repeated:
        lines += [
            "| rank | wallet | targets | symbols | early targets | delta targets |",
            "|---:|---|---:|---|---:|---:|",
        ]
        for i, row in enumerate(repeated[:20], 1):
            lines.append(
                f"| {i} | `{short_addr(row.get('wallet'))}` | {row.get('targets_touched')} "
                f"| `{row.get('symbols')}` | {row.get('early_targets')} | {row.get('token_delta_targets')} |"
            )
    else:
        lines.append("- No wallet appeared across two or more selected targets in this bounded Alchemy pass.")
    lines += [
        "",
        "## Funding Clusters",
        "",
    ]
    if clusters:
        lines += [
            "| rank | candidate funder | funded wallets | total SOL in | first | last |",
            "|---:|---|---:|---:|---|---|",
        ]
        for i, row in enumerate(clusters[:20], 1):
            lines.append(
                f"| {i} | `{short_addr(row.get('candidate_funder'))}` | {row.get('funded_wallet_count')} "
                f"| {sf(row.get('total_target_native_delta_sol')):.4f} | {row.get('first_funding_time')} | {row.get('last_funding_time')} |"
            )
    else:
        lines.append("- No shared funding clusters found above the configured SOL threshold.")
    lines += [
        "",
        "## Origin Candidates",
        "",
        "| symbol | type | status | oldest time | fee payer | initialize mint |",
        "|---|---|---|---|---|---:|",
    ]
    for row in origins:
        lines.append(
            f"| `{row.get('symbol')}` | {row.get('address_type')} | {row.get('status')} "
            f"| {row.get('oldest_signature_time')} | `{short_addr(row.get('oldest_fee_payer'))}` | {row.get('initialize_mint_seen')} |"
        )
    lines += [
        "",
        "## Outputs",
        "",
        "- `alchemy_targets.csv`",
        "- `alchemy_actor_evidence.csv`",
        "- `alchemy_cross_target_wallets.csv`",
        "- `alchemy_funding_edges.csv`",
        "- `alchemy_funding_clusters.csv`",
        "- `alchemy_origin_candidates.csv`",
        "",
        "## Interpretation",
        "",
        "- Token balance deltas are stronger evidence than fee-payer touches because they come from Alchemy transaction metadata.",
        "- Fee-payer touches are activity evidence only; they do not prove buy/sell direction.",
        "- Funding clusters are candidate links from native SOL balance movement, not proof of common ownership.",
        "- Origin rows are bounded unless `status=exhausted_history`; bounded rows should not be treated as confirmed deployers.",
        "",
    ]
    path.write_text("\n".join(lines))


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", action="append", required=True, help="TOKEN:SYMBOL:EVENT_TS[:SIGNATURE_ADDRESS]")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT))
    parser.add_argument("--pre-hours", type=float, default=0.5)
    parser.add_argument("--post-hours", type=float, default=1.0)
    parser.add_argument("--early-minutes", type=float, default=30.0)
    parser.add_argument("--max-pages", type=int, default=10)
    parser.add_argument("--page-limit", type=int, default=1000)
    parser.add_argument("--max-signatures-per-target", type=int, default=1000)
    parser.add_argument("--signature-sample-mode", choices=("split", "earliest", "latest"), default="earliest")
    parser.add_argument("--batch-size", type=int, default=25)
    parser.add_argument("--request-sleep", type=float, default=0.0)
    parser.add_argument("--fee-payer-fallback", action="store_true")
    parser.add_argument("--origin-pages", type=int, default=5)
    parser.add_argument("--funding-top-wallets", type=int, default=12)
    parser.add_argument("--funding-lookback-days", type=float, default=14.0)
    parser.add_argument("--funding-pages", type=int, default=3)
    parser.add_argument("--funding-max-signatures", type=int, default=60)
    parser.add_argument("--funding-min-sol", type=float, default=0.05)
    return parser.parse_args()


def main():
    args = parse_args()
    url = rpc_url()
    if not url:
        raise SystemExit("missing ALCHEMY_SOLANA_RPC_URL or ALCHEMY_API_KEY")
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    targets = [parse_target(spec) for spec in args.target]

    target_rows = []
    actor_rows = []
    origins = []
    for target in targets:
        target_row, rows = target_evidence(url, target, args)
        target_rows.append(target_row)
        actor_rows.extend(rows)
        origins.append(origin_candidate(url, target, "token_mint", target["token_address"], args))
        if target["signature_address"] != target["token_address"]:
            origins.append(origin_candidate(url, target, "signature_address", target["signature_address"], args))

    cross_rows = cross_target_rows(actor_rows)
    actor_rows.sort(
        key=lambda r: (
            int(sf(r.get("early_receive_count"))) + int(sf(r.get("early_touch_count"))),
            int(sf(r.get("receive_count"))),
            int(sf(r.get("tx_count"))),
            sf(r.get("abs_token_delta")),
        ),
        reverse=True,
    )
    funding_rows = funding_edges(url, actor_rows, args)
    clusters = funding_clusters(funding_rows)

    write_csv(out_dir / "alchemy_targets.csv", target_rows, TARGET_FIELDS)
    write_csv(out_dir / "alchemy_actor_evidence.csv", actor_rows, ACTOR_FIELDS)
    write_csv(out_dir / "alchemy_cross_target_wallets.csv", cross_rows, CROSS_FIELDS)
    write_csv(out_dir / "alchemy_funding_edges.csv", funding_rows, FUNDING_FIELDS)
    write_csv(out_dir / "alchemy_funding_clusters.csv", clusters, FUNDING_CLUSTER_FIELDS)
    write_csv(out_dir / "alchemy_origin_candidates.csv", origins, ORIGIN_FIELDS)
    write_report(
        out_dir / "alchemy_wallet_evidence_report.md",
        args=args,
        target_rows=target_rows,
        actor_rows=actor_rows,
        cross_rows=cross_rows,
        funding_rows=funding_rows,
        clusters=clusters,
        origins=origins,
    )
    print(f"targets={len(target_rows)} actors={len(actor_rows)} repeated={sum(1 for r in cross_rows if int(sf(r.get('targets_touched'))) >= 2)} funding_edges={len(funding_rows)} clusters={len(clusters)} origins={len(origins)}")
    print(f"wrote {out_dir / 'alchemy_wallet_evidence_report.md'}")


if __name__ == "__main__":
    main()
