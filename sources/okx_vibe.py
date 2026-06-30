"""OKX OnchainOS Social Analysis client — token "vibe" hotness score.

Calls the v6 DEX Social Analysis API:
  GET /api/v6/dex/market/social/vibe/timeline
      ?chainIndex=<idx>&tokenAddress=<addr>&timeFrame=<1|2|3|4>
The response's `data[0].summary.score` is an X/Twitter-derived hotness score on
a 0-100 scale (same number the OKX wallet UI shows as "Vibe score"). One call
per eligible candidate answers "how hot is crypto-twitter on this token right
now", to fold into the ENTRY SIGNAL message.

Auth/transport (AK / HMAC, browser-UA requirement) live in sources.okx_onchainos.
Inert (enabled() False) until the AK trio is set in .env and OKX_VIBE_ENABLED=true.
"""

import asyncio
import time

import config
from sources import okx_onchainos as okx

_VIBE_PATH = "/api/v6/dex/market/social/vibe/timeline"
_CACHE_TTL_SECONDS = 600          # vibe moves slowly; one call per ~10 min/token


def _f(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class OkxVibeClient:

    def __init__(self):
        self._cache = {}
        self._semaphore = asyncio.Semaphore(2)

    def enabled(self):
        return bool(config.OKX_VIBE_ENABLED and okx.creds_present())

    async def vibe_features(self, address, chain="sol", time_frame="1"):
        """Vibe summary for a token, or None on failure / no data. Returns a
        dict with `score` (0-100 float) and the underlying social counts, or
        None when the token has no vibe score this window (score field is
        frequently empty for fresh/illiquid mints)."""

        if not self.enabled():
            return None
        chain_index = okx.CHAIN_INDEX.get(str(chain).lower())
        if not chain_index:
            return None

        key = (address, chain_index, time_frame)
        hit = self._cache.get(key)
        if hit and time.monotonic() - hit[0] < _CACHE_TTL_SECONDS:
            return hit[1]

        async with self._semaphore:
            hit = self._cache.get(key)
            if hit and time.monotonic() - hit[0] < _CACHE_TTL_SECONDS:
                return hit[1]
            # query order must match the signed request_path
            data = await okx.signed_get(_VIBE_PATH, [
                ("chainIndex", chain_index),
                ("tokenAddress", address),
                ("timeFrame", time_frame),
            ])

        features = self._summarize(data)
        self._cache[key] = (time.monotonic(), features)
        if len(self._cache) > 500:
            for k in sorted(self._cache, key=lambda k: self._cache[k][0])[:100]:
                self._cache.pop(k, None)
        return features

    async def vibe_score(self, address, chain="sol", time_frame="1"):
        """Just the 0-100 vibe score as a float, or None."""
        features = await self.vibe_features(address, chain, time_frame)
        return features.get("score") if features else None

    @staticmethod
    def _summarize(data):
        # vibe returns `data` as a single object ({summary, timeline}); some
        # variants wrap it in a one-element list. Handle both.
        if not isinstance(data, dict) or str(data.get("code")) != "0":
            return None
        payload = data.get("data")
        if isinstance(payload, list):
            payload = payload[0] if payload else None
        summary = payload.get("summary") if isinstance(payload, dict) else None
        if not isinstance(summary, dict):
            return None
        score = _f(summary.get("score"))
        if score is None:               # empty string -> no vibe data this window
            return None
        return {
            "score": score,
            "score_change_rate": _f(summary.get("scoreChangeRate")),
            "mentions": _f(summary.get("mentionsCount")),
            "engagement": _f(summary.get("engagement")),
            "impressions": _f(summary.get("impressions")),
        }


okx_vibe_client = OkxVibeClient()
