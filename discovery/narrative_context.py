"""Free news-based narrative context for Lattice ENTRY SIGNAL alerts.

This module is alert context only. It does not participate in entry, exit,
paper-buy, or live-execution decisions.
"""

from __future__ import annotations

import asyncio
import html
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from urllib.parse import quote, urlencode
from xml.etree import ElementTree

import aiohttp

import config


USER_AGENT = "lattice-scanner-narrative-context/0.1"
DEX_TOKEN_URL = "https://api.dexscreener.com/latest/dex/tokens/{token}"
GNEWS_URL = "https://news.google.com/rss/search"


def _clean(value, limit=240):
    text = " ".join(str(value or "").replace("\n", " ").split())
    if len(text) > limit:
        return text[: limit - 3].rstrip() + "..."
    return text


def _terms(query):
    out = []
    for part in str(query or "").replace(",", " ").replace("|", " ").split():
        token = part.strip().lower().strip("\"'()[]{}")
        if len(token) >= 3 and token not in {"and", "the", "for", "with"}:
            out.append(token)
    return list(dict.fromkeys(out))


def _relevance(title, text, query):
    terms = _terms(query)
    if not terms:
        return 0.0
    haystack = f"{title} {text}".lower()
    hits = sum(1 for term in terms if term in haystack)
    phrase = " ".join(terms)
    exact_bonus = 1.5 if phrase and phrase in haystack else 0.0
    return min(8.0, hits * 1.25 + exact_bonus)


def _parse_rss_time(value):
    try:
        dt = parsedate_to_datetime(str(value or "").strip())
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    return dt.timestamp()


def _has_narrative_keyword(text, keywords):
    lowered = str(text or "").lower()
    return any(str(keyword or "").lower() in lowered for keyword in keywords)


def _build_search_url(query):
    return (
        "https://news.google.com/search?q="
        f"{quote(query, safe='')}"
        "&hl=en-US&gl=US&ceid=US:en"
    )


def _build_x_search_url(query):
    return (
        "https://x.com/search?q="
        f"{quote(query, safe='')}"
        "&f=live"
    )


@dataclass
class NewsHit:
    title: str
    source: str
    url: str
    published_at: float | None
    relevance: float


class NarrativeContextProvider:
    def __init__(self):
        self.enabled = bool(
            getattr(config, "LATTICE_NARRATIVE_CONTEXT_ENABLED", True)
        )
        self.news_enabled = bool(
            getattr(config, "LATTICE_NARRATIVE_CONTEXT_NEWS_ENABLED", True)
        )
        self.metadata_enabled = bool(
            getattr(
                config,
                "LATTICE_NARRATIVE_CONTEXT_TOKEN_METADATA_ENABLED",
                True,
            )
        )
        self.lookback_days = max(
            1,
            int(getattr(config, "LATTICE_NARRATIVE_CONTEXT_LOOKBACK_DAYS", 30) or 30),
        )
        self.max_results = max(
            1,
            int(getattr(config, "LATTICE_NARRATIVE_CONTEXT_MAX_NEWS_RESULTS", 10) or 10),
        )
        self.min_relevance = float(
            getattr(config, "LATTICE_NARRATIVE_CONTEXT_MIN_RELEVANCE", 1.0)
            or 0.0
        )
        self.timeout = max(
            0.5,
            float(
                getattr(
                    config,
                    "LATTICE_NARRATIVE_CONTEXT_TIMEOUT_SECONDS",
                    4.0,
                )
                or 4.0
            ),
        )
        self.cache_seconds = max(
            0.0,
            float(
                getattr(
                    config,
                    "LATTICE_NARRATIVE_CONTEXT_CACHE_SECONDS",
                    1800.0,
                )
                or 0.0
            ),
        )
        self.always_show = bool(
            getattr(config, "LATTICE_NARRATIVE_CONTEXT_ALWAYS_SHOW", True)
        )
        self.x_link_enabled = bool(
            getattr(config, "LATTICE_NARRATIVE_CONTEXT_X_LINK_ENABLED", True)
        )
        self.keywords = tuple(
            getattr(config, "LATTICE_NARRATIVE_CONTEXT_KEYWORDS", ()) or ()
        )
        self._cache = {}

    async def build(self, alert, row=None):
        if not self.enabled:
            return {
                "enabled": False,
                "checked": False,
                "label": "disabled",
            }

        token = str(getattr(alert, "token_address", "") or "").strip()
        symbol = str(getattr(alert, "symbol", "") or "").strip()
        cache_key = f"{token}:{symbol}".lower()
        cached = self._cache.get(cache_key)
        now = time.time()
        if cached and now - cached.get("cached_at", 0) <= self.cache_seconds:
            return dict(cached.get("payload") or {})

        try:
            payload = await asyncio.wait_for(
                self._build_uncached(token, symbol),
                timeout=self.timeout,
            )
        except asyncio.TimeoutError:
            payload = self._base_payload(symbol=symbol, token_name="", checked=False)
            payload.update({
                "label": "check_timeout",
                "reason": "timeout",
            })
        except Exception as exc:
            payload = self._base_payload(symbol=symbol, token_name="", checked=False)
            payload.update({
                "label": "check_failed",
                "reason": f"{type(exc).__name__}",
            })

        self._cache[cache_key] = {
            "cached_at": now,
            "payload": dict(payload),
        }
        return payload

    async def _build_uncached(self, token, symbol):
        token_meta = {}
        if self.metadata_enabled and token:
            try:
                token_meta = await self._fetch_token_metadata(token)
            except Exception:
                token_meta = {}

        token_name = _clean(token_meta.get("name") or "", 120)
        token_symbol = _clean(token_meta.get("symbol") or symbol or "", 80)
        query = self._choose_query(token_name, token_symbol)
        payload = self._base_payload(
            symbol=token_symbol,
            token_name=token_name,
            checked=True,
        )
        payload["query"] = query
        payload["news_search_url"] = _build_search_url(query) if query else ""
        payload["x_search_url"] = _build_x_search_url(query) if (
            self.x_link_enabled and query
        ) else ""

        if not query:
            payload.update({
                "label": "no_identity",
                "reason": "missing token identity",
            })
            return payload

        narrative_like = _has_narrative_keyword(query, self.keywords)
        payload["narrative_like"] = narrative_like

        hits = []
        if self.news_enabled:
            try:
                hits = await self._fetch_google_news(query)
            except Exception as exc:
                payload.update({
                    "label": "check_failed",
                    "reason": type(exc).__name__,
                })
                return payload

        payload["news_hits"] = len(hits)
        if hits:
            top = hits[0]
            payload["top_title"] = top.title
            payload["top_source"] = top.source
            payload["top_url"] = top.url

        if len(hits) >= 3 and narrative_like:
            label = "news_backed"
        elif len(hits) >= 1 and narrative_like:
            label = "some_news"
        elif narrative_like:
            label = "weak_no_news"
        elif len(hits) >= 1:
            label = "name_mentions"
        else:
            label = "none_found"

        payload["label"] = label
        return payload

    def _base_payload(self, symbol, token_name, checked):
        return {
            "enabled": True,
            "checked": bool(checked),
            "symbol": symbol,
            "token_name": token_name,
            "label": "unchecked",
            "news_hits": 0,
            "query": "",
            "news_search_url": "",
            "x_search_url": "",
            "top_title": "",
            "top_source": "",
            "top_url": "",
            "narrative_like": False,
            "reason": "",
        }

    def _choose_query(self, token_name, symbol):
        identity = token_name or symbol
        identity = _clean(identity, 120)
        if not identity:
            return ""

        if _has_narrative_keyword(identity, self.keywords):
            return f"\"{identity}\""

        # Search a bare symbol/name, but avoid ticker punctuation. For manual
        # context, this is intentionally permissive and later relevance-filtered.
        return identity.lstrip("$")

    async def _fetch_token_metadata(self, token):
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        async with aiohttp.ClientSession(
            timeout=timeout,
            headers={"User-Agent": USER_AGENT},
        ) as session:
            async with session.get(DEX_TOKEN_URL.format(token=quote(token, safe=""))) as resp:
                if resp.status >= 400:
                    return {}
                data = await resp.json(content_type=None)

        pairs = data.get("pairs") or []
        for pair in pairs:
            base = (pair or {}).get("baseToken") or {}
            address = str(base.get("address") or "")
            if address.lower() == token.lower():
                return {
                    "name": _clean(base.get("name") or "", 120),
                    "symbol": _clean(base.get("symbol") or "", 80),
                }
        if pairs:
            base = (pairs[0] or {}).get("baseToken") or {}
            return {
                "name": _clean(base.get("name") or "", 120),
                "symbol": _clean(base.get("symbol") or "", 80),
            }
        return {}

    async def _fetch_google_news(self, query):
        rss_query = f"{query} when:{self.lookback_days}d"
        params = {
            "q": rss_query,
            "hl": "en-US",
            "gl": "US",
            "ceid": "US:en",
        }
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        async with aiohttp.ClientSession(
            timeout=timeout,
            headers={"User-Agent": USER_AGENT},
        ) as session:
            async with session.get(GNEWS_URL + "?" + urlencode(params)) as resp:
                if resp.status >= 400:
                    return []
                raw = await resp.read()

        try:
            root = ElementTree.fromstring(raw)
        except ElementTree.ParseError:
            return []

        hits = []
        for node in root.findall("./channel/item")[: self.max_results]:
            title = _clean(node.findtext("title") or "", 260)
            url = _clean(node.findtext("link") or "", 900)
            pub_date = _parse_rss_time(node.findtext("pubDate"))
            source_node = node.find("source")
            source = _clean(source_node.text if source_node is not None else "", 120)
            text = " ".join(
                part for part in [
                    node.findtext("description") or "",
                    source,
                ] if part
            )
            relevance = _relevance(title, text, query)
            if relevance < self.min_relevance:
                continue
            hits.append(
                NewsHit(
                    title=title,
                    source=source,
                    url=url,
                    published_at=pub_date,
                    relevance=relevance,
                )
            )
        hits.sort(key=lambda hit: hit.relevance, reverse=True)
        return hits


def format_narrative_context(context):
    if not context or not context.get("enabled", True):
        return ""

    label = str(context.get("label") or "unchecked")
    news_hits = int(context.get("news_hits") or 0)
    top_source = _clean(context.get("top_source") or "", 60)

    display = {
        "news_backed": "NEWS-BACKED",
        "some_news": "SOME NEWS",
        "weak_no_news": "WEAK/NO NEWS",
        "name_mentions": "NAME MENTIONS",
        "none_found": "NO NEWS FOUND",
        "check_timeout": "CHECK TIMEOUT",
        "check_failed": "CHECK FAILED",
        "no_identity": "NO IDENTITY",
        "unchecked": "UNCHECKED",
    }.get(label, label.upper())

    parts = [
        f"narrative: <b>{html.escape(display, quote=False)}</b>",
    ]

    if label in {"check_timeout", "check_failed"}:
        reason = context.get("reason") or ""
        if reason:
            parts.append(html.escape(str(reason), quote=False))
    else:
        detail = f"{news_hits} news"
        if top_source:
            detail += f", top {top_source}"
        parts.append(html.escape(detail, quote=False))

    links = []
    news_url = context.get("news_search_url") or ""
    x_url = context.get("x_search_url") or ""
    if news_url:
        links.append(f'<a href="{html.escape(news_url, quote=True)}">News</a>')
    if x_url:
        links.append(f'<a href="{html.escape(x_url, quote=True)}">X</a>')
    if links:
        parts.append(" ".join(links))

    return " | ".join(parts)
