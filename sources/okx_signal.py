"""OKX OnchainOS DEX Signal client — per-token smart-money / KOL / whale buy flow.

Calls the v6 DEX Signal API:
  POST /api/v6/dex/market/signal/list
       body {chainIndex, tokenAddress, walletType?, limit, ...}
Returns the recent buy-direction signals for a single token: which curated
wallet classes (smart money / KOL / whale) are buying it right now, how many,
how much, and whether they're still holding (low soldRatioPercent) or already
exiting. Used to corroborate an ENTRY SIGNAL with independent flow data.

Per-token queries return `[]` for tokens not being actively accumulated, so a
populated result is itself meaningful (smart money is buying *this* now).

Auth/transport live in sources.okx_onchainos. Inert (enabled() False) until the
AK trio is set in .env and OKX_SIGNAL_ENABLED=true.
"""

import asyncio
import time

import config
from sources import okx_onchainos as okx

_SIGNAL_PATH = "/api/v6/dex/market/signal/list"
_CACHE_TTL_SECONDS = 120          # flow is timely; keep the cache short
_DEFAULT_LIMIT = 50
# Heuristic thresholds for the holding/exiting label (avg soldRatioPercent).
_HOLDING_MAX = 50.0
_TRIMMING_MAX = 80.0

# walletType -> our bucket name
_WALLET_BUCKET = {"1": "smart", "2": "kol", "3": "whale"}


def _f(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _i(value):
    f = _f(value)
    return int(f) if f is not None else 0


class OkxSignalClient:

    def __init__(self):
        self._cache = {}
        self._semaphore = asyncio.Semaphore(2)

    def enabled(self):
        return bool(config.OKX_SIGNAL_ENABLED and okx.creds_present())

    async def token_signals(self, address, chain="sol", limit=_DEFAULT_LIMIT):
        """Aggregated buy-flow summary for a token, or None on error. Returns a
        dict (possibly all-zero `signals=0` when nobody tracked is buying it)."""

        if not self.enabled():
            return None
        chain_index = okx.CHAIN_INDEX.get(str(chain).lower())
        if not chain_index:
            return None

        key = (address, chain_index)
        hit = self._cache.get(key)
        if hit and time.monotonic() - hit[0] < _CACHE_TTL_SECONDS:
            return hit[1]

        async with self._semaphore:
            hit = self._cache.get(key)
            if hit and time.monotonic() - hit[0] < _CACHE_TTL_SECONDS:
                return hit[1]
            data = await okx.signed_post(_SIGNAL_PATH, {
                "chainIndex": chain_index,
                "tokenAddress": address,
                "limit": str(limit),
            })

        features = self._summarize(data)
        self._cache[key] = (time.monotonic(), features)
        if len(self._cache) > 500:
            for k in sorted(self._cache, key=lambda k: self._cache[k][0])[:100]:
                self._cache.pop(k, None)
        return features

    @staticmethod
    def _summarize(data):
        rows = okx.ok_rows(data)
        if rows is None:                # error / unexpected shape (not the same as [])
            return None

        buckets = {"smart": 0, "kol": 0, "whale": 0}
        wallets = 0
        amount_usd = 0.0
        sold_ratios = []
        last_ts = None
        for s in rows:
            if not isinstance(s, dict):
                continue
            cnt = _i(s.get("triggerWalletCount"))
            bucket = _WALLET_BUCKET.get(str(s.get("walletType") or ""))
            if bucket:
                buckets[bucket] += cnt
            wallets += cnt
            amount_usd += _f(s.get("amountUsd")) or 0.0
            sr = _f(s.get("soldRatioPercent"))
            if sr is not None:
                sold_ratios.append(sr)
            ts = _f(s.get("timestamp"))
            if ts and (last_ts is None or ts > last_ts):
                last_ts = ts

        sold_avg = sum(sold_ratios) / len(sold_ratios) if sold_ratios else None
        return {
            "signals": len(rows),
            "smart": buckets["smart"],
            "kol": buckets["kol"],
            "whale": buckets["whale"],
            "wallets": wallets,
            "amount_usd": amount_usd,
            "sold_ratio_avg": sold_avg,
            "last_ts": last_ts,
        }

    @staticmethod
    def holding_label(sold_ratio_avg):
        """Human label + whether it's a caution flag, from avg soldRatioPercent."""
        if sold_ratio_avg is None:
            return "", False
        if sold_ratio_avg < _HOLDING_MAX:
            return "holding", False
        if sold_ratio_avg < _TRIMMING_MAX:
            return "trimming", False
        return "mostly sold", True


okx_signal_client = OkxSignalClient()
