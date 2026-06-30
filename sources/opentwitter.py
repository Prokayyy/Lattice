"""OpenTwitter (6551) data client — CA-mention features for candidates.

Calls the REST API behind 6551Team/opentwitter-mcp directly (the MCP layer is
just a wrapper): POST https://ai.6551.io/open/twitter_search with
Authorization: Bearer TWITTER_TOKEN (key from https://6551.io/mcp). Only the
search tool is used — one keyword search for the candidate's contract address
per eligible candidate, answering "is crypto-twitter already talking about
this token, who, and how loudly".

Inert (enabled() False) until TWITTER_TOKEN is set in .env.
"""

import asyncio
import json
import time

import aiohttp

from config import OPENTWITTER_ENRICH_ENABLED, TWITTER_TOKEN

_BASE_URL = "https://ai.6551.io"
_CACHE_TTL_SECONDS = 900
_TIMEOUT_SECONDS = 25


def _f(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _pick(obj, *names):
    for name in names:
        value = obj.get(name)
        if value is not None:
            return value
    return None


class OpenTwitterClient:

    def __init__(self):
        self._cache = {}
        self._semaphore = asyncio.Semaphore(2)

    def enabled(self):
        return bool(OPENTWITTER_ENRICH_ENABLED and TWITTER_TOKEN)

    async def _search(self, keywords, max_results=20):
        timeout = aiohttp.ClientTimeout(total=_TIMEOUT_SECONDS)
        headers = {
            "Authorization": f"Bearer {TWITTER_TOKEN}",
            "Content-Type": "application/json",
        }
        body = {
            "keywords": keywords,
            "maxResults": max_results,
            "product": "Latest",
        }
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                f"{_BASE_URL}/open/twitter_search",
                json=body,
                headers=headers,
            ) as response:
                if response.status >= 400:
                    return None
                try:
                    return await response.json(content_type=None)
                except Exception:
                    return None

    async def ca_mention_features(self, address):
        """Mention aggregate for a contract address, or None on failure.
        Tolerant to payload variants; the compact raw sample is stored so the
        exact shape can be confirmed from the first live rows."""

        key = address
        hit = self._cache.get(key)
        if hit and time.monotonic() - hit[0] < _CACHE_TTL_SECONDS:
            return hit[1]

        async with self._semaphore:
            hit = self._cache.get(key)
            if hit and time.monotonic() - hit[0] < _CACHE_TTL_SECONDS:
                return hit[1]
            data = await self._search(address)

        features = self._aggregate(data) if data is not None else None
        self._cache[key] = (time.monotonic(), features)
        if len(self._cache) > 500:
            for k in sorted(self._cache, key=lambda k: self._cache[k][0])[:100]:
                self._cache.pop(k, None)
        return features

    @staticmethod
    def _aggregate(data):
        tweets = None
        for name in ("tweets", "list", "data", "results", "items"):
            value = data.get(name) if isinstance(data, dict) else None
            if isinstance(value, list):
                tweets = value
                break
        if tweets is None:
            inner = data.get("data") if isinstance(data, dict) else None
            if isinstance(inner, dict):
                for name in ("tweets", "list", "results", "items"):
                    if isinstance(inner.get(name), list):
                        tweets = inner[name]
                        break
        if tweets is None:
            tweets = []

        authors = set()
        top_followers = 0
        first_ts = None
        for tweet in tweets:
            if not isinstance(tweet, dict):
                continue
            user = (
                tweet.get("user") or tweet.get("author") or {}
            )
            handle = _pick(user, "screen_name", "username", "userName", "handle")
            if handle:
                authors.add(str(handle).lower())
            followers = _f(_pick(
                user, "followers_count", "followersCount", "followers"
            ))
            if followers and followers > top_followers:
                top_followers = followers
            ts = _f(_pick(
                tweet, "created_at_ts", "timestamp", "createdAtTs", "time"
            ))
            if ts and (first_ts is None or ts < first_ts):
                first_ts = ts

        return {
            "mentions": len(tweets),
            "authors": len(authors),
            "top_followers": int(top_followers),
            "first_mention_ts": first_ts,
            "raw": json.dumps({
                "n": len(tweets),
                "sample": [
                    {
                        "user": _pick(
                            (t.get("user") or t.get("author") or {}),
                            "screen_name", "username", "userName",
                        ),
                        "keys": sorted(t.keys())[:12],
                    }
                    for t in tweets[:3]
                    if isinstance(t, dict)
                ],
            }, default=str),
        }


opentwitter_client = OpenTwitterClient()
