"""Late-moon monitor.

Watches alerts the lattice scanner has SENT and notifies (Telegram) when a
token first reaches a fresh integer multiple — 2x, 3x, 4x, ... — of its
alert-time price. Only alerts whose alert time is within the last
LATE_MOON_WINDOW_DAYS (default 10) days are tracked; older alerts age out.

Alert population (deduped by (token, int(alert_ts)); baseline = the EARLIEST
alert per token still inside the window):
  - discovery/participation_log.jsonl  — back-history: every conviction survivor
    with its alert-time `entry_price`.
  - discovery/sent_alerts.jsonl        — forward: the exact Telegram-sent alerts,
    appended by the live runner at the should_alert site.

Prices come from DexScreener (trading.live_prices.fetch_live_prices) — NO Alchemy
RPC, so this monitor adds zero RPC-credit cost. It never touches scanner.db.

Milestone state persists in discovery/late_moon_state.json so a restart never
re-announces a milestone already sent. A heartbeat is written to
discovery/late_moon_heartbeat.json each pass.

Run:
  env/bin/python -m discovery.late_moon_monitor --poll-s 600
  env/bin/python -m discovery.late_moon_monitor --once      # single pass, no loop
  env/bin/python -m discovery.late_moon_monitor --dry-run   # never send Telegram
"""
import argparse
import asyncio
import json
import math
import os
import time
from datetime import datetime, timezone

from discovery.notify import LatticeNotifier
from trading.live_prices import fetch_live_prices

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERE = os.path.dirname(os.path.abspath(__file__))
PARTICIPATION_LOG = os.path.join(HERE, "participation_log.jsonl")
SENT_ALERTS = os.path.join(HERE, "sent_alerts.jsonl")
STATE_PATH = os.path.join(HERE, "late_moon_state.json")
HEARTBEAT_PATH = os.path.join(HERE, "late_moon_heartbeat.json")


def _env_float(name, default):
    try:
        return float(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        return float(default)


def _env_int(name, default):
    try:
        return int(float(os.environ.get(name, "") or default))
    except (TypeError, ValueError):
        return int(default)


# Defaults; CLI flags and these env vars both override.
WINDOW_DAYS = _env_float("LATE_MOON_WINDOW_DAYS", 10.0)
MIN_MULTIPLE = _env_int("LATE_MOON_MIN_MULTIPLE", 2)
POLL_SECONDS = _env_float("LATE_MOON_POLL_SECONDS", 600.0)
# Glitch guards: ignore an implausible jump (DexScreener can briefly mis-price a
# thin pair) and pairs with sub-floor liquidity so a dead-pool print can't fire a
# fake "100x". MAX_MULTIPLE<=0 or MIN_LIQUIDITY<=0 disables that guard.
MAX_MULTIPLE = _env_float("LATE_MOON_MAX_MULTIPLE", 1000.0)
MIN_LIQUIDITY_USD = _env_float("LATE_MOON_MIN_LIQUIDITY_USD", 300.0)


def _fmt_price(p):
    p = float(p or 0)
    if p <= 0:
        return "?"
    if p >= 1:
        return f"${p:,.4f}"
    # small-cap prices: show enough significant digits
    decimals = min(12, max(4, 2 - int(math.floor(math.log10(p)))))
    return f"${p:.{decimals}f}"


def _fmt_age(seconds):
    seconds = max(0.0, float(seconds or 0))
    hours = seconds / 3600.0
    if hours < 48:
        return f"{hours:.0f}h"
    return f"{hours / 24:.1f}d"


def _iter_jsonl(path):
    if not os.path.exists(path):
        return
    try:
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
    except OSError:
        return


def _record_fields(rec):
    """Normalize a participation_log / sent_alerts row to
    (token, ts, entry_price, symbol, chain) or None if unusable."""
    token = rec.get("token")
    try:
        ts = float(rec.get("ts") or rec.get("alert_ts") or 0)
        entry_price = float(rec.get("entry_price") or 0)
    except (TypeError, ValueError):
        return None
    if not token or ts <= 0 or entry_price <= 0:
        return None
    row = rec.get("row") or {}
    chain = (
        rec.get("chain")
        or row.get("chain_name")
        or row.get("chain")
        or "solana"
    )
    symbol = rec.get("symbol") or row.get("symbol") or ""
    return token, ts, entry_price, symbol, chain


def load_window_alerts(now, window_days):
    """Earliest in-window alert per token across both sources, deduped by
    (token, int(ts)). Returns {token: {symbol, alert_ts, baseline_price, chain}}."""
    cutoff = now - window_days * 86400.0
    seen_keys = set()
    by_token = {}
    for path in (PARTICIPATION_LOG, SENT_ALERTS):
        for rec in _iter_jsonl(path):
            fields = _record_fields(rec)
            if fields is None:
                continue
            token, ts, entry_price, symbol, chain = fields
            if ts < cutoff:
                continue
            key = (token, int(ts))
            if key in seen_keys:
                continue
            seen_keys.add(key)
            existing = by_token.get(token)
            if existing is None or ts < existing["alert_ts"]:
                by_token[token] = {
                    "symbol": symbol or (existing or {}).get("symbol", ""),
                    "alert_ts": ts,
                    "baseline_price": entry_price,
                    "chain": chain,
                }
            elif not existing.get("symbol") and symbol:
                existing["symbol"] = symbol
    return by_token


def load_state():
    try:
        with open(STATE_PATH) as fh:
            data = json.load(fh)
        if isinstance(data, dict) and isinstance(data.get("tokens"), dict):
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return {"version": 1, "tokens": {}}


def save_state(state):
    tmp = STATE_PATH + ".tmp"
    try:
        with open(tmp, "w") as fh:
            json.dump(state, fh)
        os.replace(tmp, STATE_PATH)
    except OSError as e:
        print("late-moon state save error:", e)


def write_heartbeat(payload):
    try:
        payload = dict(payload)
        payload["iso"] = datetime.now(timezone.utc).isoformat()
        with open(HEARTBEAT_PATH, "w") as fh:
            json.dump(payload, fh)
    except OSError:
        pass


def _milestone_message(symbol, milestone, prev, info, cur_price, mult, liq, chain, token):
    sym = (symbol or "?").lstrip("$")
    jumped = ""
    if prev > 0 and milestone - prev > 1:
        # live progression past several integers between polls
        jumped = f" (passed {prev + 1}x–{milestone - 1}x)"
    elif prev == 0 and milestone > MIN_MULTIPLE:
        # first time we've seen this (already-mooned) alert — it's a catch-up
        jumped = " (since alert)"
    age = _fmt_age(time.time() - info["alert_ts"])
    liq_str = f"${liq:,.0f}" if liq else "n/a"
    url = f"https://dexscreener.com/{chain}/{token}"
    return (
        f"\U0001F319 <b>${sym}</b> late-moon: <b>{milestone}x</b>{jumped}\n"
        f"alerted {age} ago @ {_fmt_price(info['baseline_price'])} "
        f"→ now {_fmt_price(cur_price)} (<b>{mult:.1f}x</b>)\n"
        f"liq {liq_str} · <a href=\"{url}\">chart</a>\n"
        f"<code>{token}</code>"
    )


async def run_once(notifier, *, window_days, min_multiple, max_multiple,
                   min_liquidity):
    now = time.time()
    alerts = load_window_alerts(now, window_days)
    state = load_state()
    tokens_state = state["tokens"]

    # Drop state for alerts that have aged out of the window entirely.
    for token in list(tokens_state):
        if token not in alerts:
            del tokens_state[token]

    fired = 0
    priced = 0
    skipped_glitch = 0
    error = ""

    if alerts:
        chain_by_address = {t: a["chain"] for t, a in alerts.items()}
        try:
            live_prices, stats = await fetch_live_prices(
                list(alerts), chain_by_address=chain_by_address
            )
            error = stats.get("error", "") or ""
        except Exception as exc:  # never let a fetch error kill the loop
            live_prices, error = {}, f"{type(exc).__name__}: {exc}"

        for token, info in alerts.items():
            live = live_prices.get(token)
            if not live:
                continue
            cur = float(live.get("price_usd") or 0)
            base = float(info["baseline_price"] or 0)
            if cur <= 0 or base <= 0:
                continue
            priced += 1
            mult = cur / base
            if max_multiple > 0 and mult > max_multiple:
                skipped_glitch += 1
                continue
            liq = float(live.get("liquidity_usd") or 0)
            milestone = int(math.floor(mult))
            if milestone < min_multiple:
                continue
            if min_liquidity > 0 and liq < min_liquidity:
                continue

            st = tokens_state.get(token) or {}
            prev = int(st.get("max_milestone") or 0)
            st.update({
                "symbol": info["symbol"] or st.get("symbol", ""),
                "alert_ts": info["alert_ts"],
                "chain": info["chain"],
                "baseline_price": base,
                "last_mult": round(mult, 4),
                "last_seen": now,
            })
            if milestone > prev:
                msg = _milestone_message(
                    info["symbol"], milestone, prev, info,
                    cur, mult, liq, info["chain"], token,
                )
                try:
                    await notifier._send(msg)
                    st["max_milestone"] = milestone
                    st["last_fire"] = now
                    fired += 1
                except Exception as exc:
                    error = error or f"send_error: {type(exc).__name__}"
            tokens_state[token] = st

    save_state(state)
    write_heartbeat({
        "status": "ok" if not error else "degraded",
        "ts": now,
        "in_window": len(alerts),
        "priced": priced,
        "milestones_fired": fired,
        "skipped_glitch": skipped_glitch,
        "tracked_total": len(tokens_state),
        "error": error,
    })
    print(
        f"late-moon pass: in_window={len(alerts)} priced={priced} "
        f"fired={fired} glitch_skipped={skipped_glitch} err={error or '-'}"
    )
    return fired


async def run_forever(args):
    notifier = LatticeNotifier(dry_run=args.dry_run)
    poll_s = max(30.0, float(args.poll_s))
    mode = "DRY-RUN" if (notifier.dry or not notifier.enabled) else "LIVE → Telegram"
    print(
        f"late-moon monitor started ({mode}) | window={args.window_days:g}d "
        f"min={args.min_multiple}x poll={poll_s:g}s "
        f"max_mult={args.max_multiple:g} min_liq=${args.min_liquidity:g}"
    )
    while True:
        try:
            await run_once(
                notifier,
                window_days=args.window_days,
                min_multiple=args.min_multiple,
                max_multiple=args.max_multiple,
                min_liquidity=args.min_liquidity,
            )
        except Exception as e:  # heartbeat the failure, keep looping
            write_heartbeat({"status": "error", "ts": time.time(),
                             "error": f"{type(e).__name__}: {e}"})
            print("late-moon loop error:", e)
        await asyncio.sleep(poll_s)


def build_parser():
    ap = argparse.ArgumentParser(description="Late-moon milestone monitor")
    ap.add_argument("--poll-s", type=float, default=POLL_SECONDS)
    ap.add_argument("--window-days", type=float, default=WINDOW_DAYS)
    ap.add_argument("--min-multiple", type=int, default=MIN_MULTIPLE)
    ap.add_argument("--max-multiple", type=float, default=MAX_MULTIPLE)
    ap.add_argument("--min-liquidity", type=float, default=MIN_LIQUIDITY_USD)
    ap.add_argument("--once", action="store_true",
                    help="run a single pass and exit")
    ap.add_argument("--dry-run", action="store_true",
                    help="never send Telegram (print instead)")
    return ap


def main():
    args = build_parser().parse_args()
    if args.once:
        notifier = LatticeNotifier(dry_run=args.dry_run)
        asyncio.run(run_once(
            notifier,
            window_days=args.window_days,
            min_multiple=args.min_multiple,
            max_multiple=args.max_multiple,
            min_liquidity=args.min_liquidity,
        ))
        return
    asyncio.run(run_forever(args))


if __name__ == "__main__":
    main()
