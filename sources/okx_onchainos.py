"""Shared low-level client for the OKX OnchainOS v6 DEX API.

Centralises the AK / HMAC-SHA256 request signing used by every OnchainOS
endpoint so the per-feature clients (sources/okx_vibe.py, sources/okx_signal.py)
only deal with paths, params, and response shaping.

NOTE (hard-won): the edge (Cloudflare) rejects non-browser User-Agents with
`HTTP 403 error code: 1010` *before* auth is evaluated, so a browser-ish UA is
mandatory on every request.
"""

import base64
import hashlib
import hmac
import json
from datetime import datetime, timezone

import aiohttp

import config

BASE_URL = "https://web3.okx.com"
# Cloudflare 1010 blocks default client UAs; present as a browser.
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
# DEX chainIndex values (mirrors the OnchainOS chain map). Solana only for now.
CHAIN_INDEX = {"sol": "501", "solana": "501"}

_DEFAULT_TIMEOUT_SECONDS = 6


def creds_present():
    return bool(
        config.OKX_API_KEY and config.OKX_API_SECRET and config.OKX_API_PASSPHRASE
    )


def _iso_ms():
    """OKX timestamp: ISO8601 UTC with millisecond precision, e.g.
    2026-06-25T19:30:00.123Z."""
    dt = datetime.now(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def _sign(timestamp, method, request_path, body=""):
    prehash = f"{timestamp}{method}{request_path}{body}"
    return base64.b64encode(
        hmac.new(
            config.OKX_API_SECRET.encode(), prehash.encode(), hashlib.sha256
        ).digest()
    ).decode()


def _headers(method, request_path, body=""):
    ts = _iso_ms()
    return {
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
        "Ok-Access-Client-type": "agent-cli",
        "OK-ACCESS-KEY": config.OKX_API_KEY,
        "OK-ACCESS-SIGN": _sign(ts, method, request_path, body),
        "OK-ACCESS-PASSPHRASE": config.OKX_API_PASSPHRASE,
        "OK-ACCESS-TIMESTAMP": ts,
    }


async def signed_get(path, query_pairs, timeout_s=_DEFAULT_TIMEOUT_SECONDS):
    """Signed GET. `query_pairs` is an ordered list of (key, value); the signed
    request_path must match the URL exactly, so order is preserved and no
    re-encoding is done (callers pass already-safe values)."""
    qs = "&".join(f"{k}={v}" for k, v in query_pairs)
    request_path = f"{path}?{qs}" if qs else path
    timeout = aiohttp.ClientTimeout(total=timeout_s)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(
            f"{BASE_URL}{request_path}",
            headers=_headers("GET", request_path),
        ) as response:
            if response.status >= 400:
                return None
            try:
                return await response.json(content_type=None)
            except Exception:
                return None


async def signed_post(path, body_dict, timeout_s=_DEFAULT_TIMEOUT_SECONDS):
    """Signed POST. The body is signed verbatim, so the exact bytes sent must be
    the bytes signed — serialise once with compact separators and reuse."""
    raw = json.dumps(body_dict, separators=(",", ":"))
    timeout = aiohttp.ClientTimeout(total=timeout_s)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(
            f"{BASE_URL}{path}",
            data=raw.encode(),
            headers=_headers("POST", path, raw),
        ) as response:
            if response.status >= 400:
                return None
            try:
                return await response.json(content_type=None)
            except Exception:
                return None


def ok_rows(data):
    """Extract the `data` list from a `{code:"0", data:[...]}` envelope, or None
    on a non-OK code / unexpected shape."""
    if not isinstance(data, dict) or str(data.get("code")) != "0":
        return None
    rows = data.get("data")
    if isinstance(rows, list):
        return rows
    if isinstance(rows, dict):
        for name in ("list", "items", "results"):
            if isinstance(rows.get(name), list):
                return rows[name]
    return None
