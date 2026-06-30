"""Solana Tracker risk/bundle adapter for the Lattice 💎 scanner.

Single-provider bundle/cluster evidence. Fetches

    GET https://data.solanatracker.io/tokens/{mint}      header: x-api-key

and normalizes the top-level ``risk`` object (bundlers / insiders / snipers /
dev / top10 / rugged / score) into a flat evidence dict that follows the
BUNDLE_DETECTOR_PLAN data contract.

Scanner-focused, NOT a trading gate: this *labels* alerts with the bundle
wallets/clusters, it never withholds an alert or blocks an entry. Disabled
unless ``SOLANATRACKER_API_KEY`` is set (and ``SOLANATRACKER_BUNDLE_ENABLED``
is not "false").

The free tier is request-metered, so the only caller (the live runner) queries
this at the rare ALERT site, never per candidate. Any failure fails open to
``status="timeout"/"error"`` with ``risk_level="unknown"`` — never a silent
"low". A timeout is not evidence that the distribution is clean.

CLI (manual probe):
    env/bin/python sources/solanatracker.py <TOKEN_MINT>
"""
import argparse
import asyncio
import json
import os
import sys
import time

import aiohttp

BASE_URL = "https://data.solanatracker.io"

# Label thresholds (cosmetic — this is a label, not a gate). Env-tunable.
HIGH_BUNDLE_PCT = float(os.getenv("SOLANATRACKER_BUNDLE_HIGH_PCT", "20") or 20)
HIGH_INSIDER_PCT = float(os.getenv("SOLANATRACKER_INSIDER_HIGH_PCT", "15") or 15)
REVIEW_BUNDLE_PCT = float(os.getenv("SOLANATRACKER_BUNDLE_REVIEW_PCT", "8") or 8)
REVIEW_SNIPER_PCT = float(os.getenv("SOLANATRACKER_SNIPER_REVIEW_PCT", "25") or 25)

# how many wallets to surface in the alert / keep for audit
TOP_WALLETS = int(os.getenv("SOLANATRACKER_BUNDLE_TOP_WALLETS", "5") or 5)
RAW_WALLETS = 20


def api_key():
    return os.getenv("SOLANATRACKER_API_KEY", "").strip()


def enabled():
    if os.getenv("SOLANATRACKER_BUNDLE_ENABLED", "true").strip().lower() in (
        "0", "false", "no", "off"
    ):
        return False
    return bool(api_key())


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def short_addr(addr, head=4, tail=4):
    addr = str(addr or "")
    return f"{addr[:head]}…{addr[-tail:]}" if len(addr) > head + tail + 1 else addr


def _group(obj):
    """Normalize a risk group. Real Solana Tracker wallet entries key the address
    under ``wallet`` and expose both CURRENT (``percentage``) and launch-time
    (``initialPercentage``) supply plus ``bundleTime`` — so this single provider
    can fill both current and launch-acquisition fields of the contract."""
    obj = obj or {}
    wallets = []
    for w in (obj.get("wallets") or []):
        addr = str(w.get("wallet") or w.get("address") or "")
        if not addr:
            continue
        wallets.append({
            "address": addr,
            "percentage": _f(w.get("percentage")) or 0.0,
            "initial_percentage": _f(w.get("initialPercentage")),
            "balance": _f(w.get("balance")),
            "bundle_time": w.get("bundleTime"),
        })
    wallets.sort(key=lambda w: -(w["percentage"] or w["initial_percentage"] or 0))
    return {
        "count": int(obj.get("count") or len(wallets) or 0),
        "pct": _f(obj.get("totalPercentage")) or 0.0,
        "initial_pct": _f(obj.get("totalInitialPercentage")),
        "wallets": wallets,
    }


def _risk_level(bundle_pct, insider_pct, sniper_pct, rugged):
    """Driven by CURRENT controlled supply — the reliable number. The provider's
    totalInitialPercentage is a gross sum (can exceed 100% from wallet churn), so
    it is NOT used for thresholding."""
    if rugged:
        return "high"
    if bundle_pct >= HIGH_BUNDLE_PCT or insider_pct >= HIGH_INSIDER_PCT:
        return "high"
    if (bundle_pct >= REVIEW_BUNDLE_PCT or insider_pct >= REVIEW_BUNDLE_PCT
            or sniper_pct >= REVIEW_SNIPER_PCT):
        return "review"
    return "low"


def normalize(mint, risk, latency_ms, status="ok"):
    """Map Solana Tracker's ``risk`` object onto the evidence data contract.

    Single provider only fills CURRENT control; launch-acquisition supply is not
    derivable from one provider, so ``launch_bundle_pct`` stays None (unknown)
    rather than being faked from current holdings."""
    risk = risk or {}
    bundlers = _group(risk.get("bundlers"))
    insiders = _group(risk.get("insiders"))
    snipers = _group(risk.get("snipers"))
    dev = risk.get("dev") or {}
    rugged = bool(risk.get("rugged"))

    risks = []
    for r in (risk.get("risks") or []):
        if isinstance(r, dict):
            risks.append(str(r.get("name") or r.get("description") or r))
        else:
            risks.append(str(r))

    return {
        "provider": "solanatracker",
        "token": mint,
        "observed_at": int(time.time()),
        "latency_ms": latency_ms,
        "status": status,
        "risk_level": _risk_level(bundlers["pct"], insiders["pct"],
                                  snipers["pct"], rugged),
        # supply attribution. launch_bundle_pct is NOT the provider's
        # totalInitialPercentage (a >100% gross sum); a clean launch share isn't
        # reliably derivable from one provider, so it stays unknown. The raw
        # provider total is kept for audit only.
        "launch_bundle_pct": None,
        "provider_total_initial_pct": bundlers["initial_pct"],
        "current_bundle_pct": bundlers["pct"],          # still held by the cluster
        "insider_pct": insiders["pct"],
        "sniper_pct": snipers["pct"],
        "dev_pct": _f(dev.get("percentage")) or 0.0,
        "top10_pct": _f(risk.get("top10")) or 0.0,
        # cluster membership
        "bundle_wallet_count": bundlers["count"],
        "insider_wallet_count": insiders["count"],
        "sniper_wallet_count": snipers["count"],
        "bundle_wallets": bundlers["wallets"][:RAW_WALLETS],
        "insider_wallets": insiders["wallets"][:RAW_WALLETS],
        "rugged": rugged,
        "provider_score": _f(risk.get("score")),
        "provider_risks": risks,
        "jupiter_verified": bool(risk.get("jupiterVerified")),
    }


def _unknown(mint, status, error, latency_ms=None):
    return {
        "provider": "solanatracker", "token": mint,
        "observed_at": int(time.time()), "latency_ms": latency_ms,
        "status": status, "error": str(error)[:200], "risk_level": "unknown",
    }


async def fetch_risk(mint, timeout_s=None, session=None):
    """GET /tokens/{mint} and normalize ``response.risk``. Fail-open: returns a
    status!="ok" / risk_level="unknown" dict on any error or timeout."""
    key = api_key()
    if not key:
        return _unknown(mint, "disabled", "no SOLANATRACKER_API_KEY")
    if timeout_s is None:
        timeout_s = float(os.getenv("SOLANATRACKER_TIMEOUT_S", "6") or 6)
    url = f"{BASE_URL}/tokens/{mint}"
    headers = {"x-api-key": key}
    t0 = time.monotonic()
    own = session is None
    try:
        if own:
            session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=timeout_s))
        async with session.get(url, headers=headers) as r:
            latency = int((time.monotonic() - t0) * 1000)
            if r.status == 404:
                return _unknown(mint, "not_found", "token not indexed", latency)
            if r.status == 429:
                return _unknown(mint, "rate_limited", "429 quota", latency)
            if r.status != 200:
                return _unknown(mint, "error", f"http {r.status}", latency)
            data = await r.json()
        return normalize(mint, (data or {}).get("risk"), latency)
    except asyncio.TimeoutError:
        return _unknown(mint, "timeout", f">{timeout_s}s",
                        int((time.monotonic() - t0) * 1000))
    except Exception as e:                                   # noqa: BLE001
        return _unknown(mint, "error", f"{type(e).__name__}: {e}",
                        int((time.monotonic() - t0) * 1000))
    finally:
        if own and session is not None:
            await session.close()


# ---- presentation helpers (plain text; notify.py owns HTML) ----

_LEVEL_EMOJI = {"high": "🔴", "review": "🟠", "low": "🟢", "unknown": "⚪"}


def headline(ev):
    """One compact line for the alert, or None when there is nothing useful."""
    if not ev:
        return None
    lvl = ev.get("risk_level", "unknown")
    emoji = _LEVEL_EMOJI.get(lvl, "⚪")
    if ev.get("status") != "ok":
        return f"{emoji} bundle: unknown ({ev.get('status')})"
    cur = ev.get("current_bundle_pct") or 0.0
    parts = [f"bundle {cur:.1f}% held · {ev['bundle_wallet_count']} wallets"]
    if ev.get("insider_pct", 0) >= 1:
        parts.append(f"insiders {ev['insider_pct']:.1f}%")
    if ev.get("sniper_pct", 0) >= 1:
        parts.append(f"snipers {ev['sniper_pct']:.1f}%")
    if ev.get("dev_pct", 0) >= 1:
        parts.append(f"dev {ev['dev_pct']:.1f}%")
    if ev.get("rugged"):
        parts.append("RUGGED")
    return f"{emoji} " + " · ".join(parts)


def wallet_summary(ev, n=TOP_WALLETS):
    """Top-N bundle wallets as (address, current_pct, initial_pct) for display.
    A wallet with current≈0 but a real initial_pct has already dumped its bag —
    the signal current-supply alone hides."""
    if not ev or ev.get("status") != "ok":
        return []
    return [(w["address"], w["percentage"], w.get("initial_percentage"))
            for w in (ev.get("bundle_wallets") or [])[:n]]


def _print(ev):
    print(json.dumps(ev, indent=2)[:4000])
    print("\n" + (headline(ev) or "(no label)"))
    for addr, pct, init in wallet_summary(ev):
        tag = f"{pct:5.1f}%" if pct >= 0.1 else f" sold (was {init or 0:.1f}%)"
        print(f"  {short_addr(addr):>12}  {tag}")


async def _main(args):
    ev = await fetch_risk(args.mint, timeout_s=args.timeout)
    _print(ev)


def main():
    ap = argparse.ArgumentParser(description="Solana Tracker bundle/risk probe")
    ap.add_argument("mint", help="token mint address")
    ap.add_argument("--timeout", type=float, default=8.0)
    args = ap.parse_args()
    if not api_key():
        print("SOLANATRACKER_API_KEY not set in env", file=sys.stderr)
    asyncio.run(_main(args))


if __name__ == "__main__":
    main()
