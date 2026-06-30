#!/usr/bin/env python3
"""Standalone free-first narrative watchlist collector.

This is intentionally separate from the live scanner architecture:

* no imports from config.py or scanner runtime modules
* no .env loading
* no paid API keys
* no Telegram/webhook delivery

Default storage is an ignored local SQLite file:
analysis/narrative_watchlist.db
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
import sqlite3
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from xml.etree import ElementTree


DEFAULT_DB = Path(__file__).resolve().with_name("narrative_watchlist.db")
USER_AGENT = "lattice-scanner-narrative-watchlist/0.1"
DEFAULT_SOURCES = ("gdelt", "gnews", "reddit", "hn", "github", "polymarket")
ALL_SOURCES = DEFAULT_SOURCES + ("youtube",)
SOURCE_MIN_INTERVAL_SECONDS = {
    "gdelt": 5.5,
}


class SourceError(RuntimeError):
    pass


@dataclass
class Topic:
    topic_id: int | None
    name: str
    query: str
    notes: str = ""


@dataclass
class SourceItem:
    source: str
    source_id: str
    source_url: str
    title: str
    author: str
    published_at: float | None
    text: str
    raw_score: float
    engagement_score: float
    relevance_score: float
    total_score: float
    raw: dict[str, Any]


SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS topics (
        topic_id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        query TEXT NOT NULL,
        notes TEXT NOT NULL DEFAULT '',
        active INTEGER NOT NULL DEFAULT 1,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS research_runs (
        run_id INTEGER PRIMARY KEY AUTOINCREMENT,
        topic_id INTEGER NOT NULL DEFAULT 0,
        source TEXT NOT NULL,
        query TEXT NOT NULL,
        lookback_days INTEGER NOT NULL,
        started_at REAL NOT NULL,
        finished_at REAL,
        status TEXT NOT NULL,
        item_count INTEGER NOT NULL DEFAULT 0,
        error TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS findings (
        finding_id INTEGER PRIMARY KEY AUTOINCREMENT,
        source TEXT NOT NULL,
        source_id TEXT NOT NULL,
        source_url TEXT NOT NULL UNIQUE,
        title TEXT NOT NULL,
        author TEXT NOT NULL DEFAULT '',
        published_at REAL,
        first_seen_at REAL NOT NULL,
        last_seen_at REAL NOT NULL,
        raw_score REAL NOT NULL DEFAULT 0,
        engagement_score REAL NOT NULL DEFAULT 0,
        relevance_score REAL NOT NULL DEFAULT 0,
        total_score REAL NOT NULL DEFAULT 0,
        text TEXT NOT NULL DEFAULT '',
        raw_json TEXT NOT NULL DEFAULT '{}'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS finding_sightings (
        finding_id INTEGER NOT NULL,
        run_id INTEGER NOT NULL,
        topic_id INTEGER NOT NULL DEFAULT 0,
        seen_at REAL NOT NULL,
        matched_query TEXT NOT NULL,
        raw_score REAL NOT NULL DEFAULT 0,
        engagement_score REAL NOT NULL DEFAULT 0,
        relevance_score REAL NOT NULL DEFAULT 0,
        total_score REAL NOT NULL DEFAULT 0,
        PRIMARY KEY(finding_id, run_id, topic_id),
        FOREIGN KEY(finding_id) REFERENCES findings(finding_id),
        FOREIGN KEY(run_id) REFERENCES research_runs(run_id)
    )
    """,
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS findings_fts USING fts5(
        title,
        text,
        content='findings',
        content_rowid='finding_id'
    )
    """,
    """
    CREATE TRIGGER IF NOT EXISTS findings_ai AFTER INSERT ON findings BEGIN
        INSERT INTO findings_fts(rowid, title, text)
        VALUES (new.finding_id, new.title, new.text);
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS findings_au AFTER UPDATE ON findings BEGIN
        INSERT INTO findings_fts(findings_fts, rowid, title, text)
        VALUES ('delete', old.finding_id, old.title, old.text);
        INSERT INTO findings_fts(rowid, title, text)
        VALUES (new.finding_id, new.title, new.text);
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS findings_ad AFTER DELETE ON findings BEGIN
        INSERT INTO findings_fts(findings_fts, rowid, title, text)
        VALUES ('delete', old.finding_id, old.title, old.text);
    END
    """,
    "CREATE INDEX IF NOT EXISTS idx_topics_active ON topics(active)",
    "CREATE INDEX IF NOT EXISTS idx_runs_started ON research_runs(started_at)",
    "CREATE INDEX IF NOT EXISTS idx_findings_source ON findings(source, source_id)",
    "CREATE INDEX IF NOT EXISTS idx_findings_seen ON findings(last_seen_at)",
    "CREATE INDEX IF NOT EXISTS idx_sightings_seen ON finding_sightings(seen_at)",
)


def now_ts() -> float:
    return time.time()


def iso_date(ts: float | None) -> str:
    if not ts:
        return "unknown"
    return datetime.fromtimestamp(float(ts), timezone.utc).strftime("%Y-%m-%d")


def parse_iso_ts(value: Any) -> float | None:
    if not value:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        if text.isdigit() and len(text) == 14:
            dt = datetime.strptime(text, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
            return dt.timestamp()
        if text.isdigit() and len(text) == 8:
            dt = datetime.strptime(text, "%Y%m%d").replace(tzinfo=timezone.utc)
            return dt.timestamp()
        return datetime.fromisoformat(
            text.replace("Z", "+00:00")
        ).timestamp()
    except ValueError:
        return None


def parse_rfc2822_ts(value: Any) -> float | None:
    try:
        dt = parsedate_to_datetime(str(value or "").strip())
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def cutoff_for_days(days: int) -> float:
    return now_ts() - max(1, int(days)) * 86400


def clean_text(value: Any, max_len: int = 900) -> str:
    text = " ".join(str(value or "").replace("\n", " ").split())
    if len(text) > max_len:
        return text[: max_len - 3].rstrip() + "..."
    return text


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def terms_for_query(query: str) -> list[str]:
    terms: list[str] = []
    for part in query.replace(",", " ").replace("|", " ").split():
        token = part.strip().lower()
        if len(token) >= 2 and token not in {"and", "or", "the"}:
            terms.append(token)
    return list(dict.fromkeys(terms))


def score_relevance(title: str, text: str, terms: list[str]) -> float:
    if not terms:
        return 0.0
    haystack = f"{title} {text}".lower()
    hits = sum(1 for term in terms if term in haystack)
    exact_bonus = 1.5 if " ".join(terms) in haystack else 0.0
    return min(8.0, hits * 1.25 + exact_bonus)


def score_item(
    raw_score: float,
    published_at: float | None,
    relevance_score: float,
    days: int,
) -> tuple[float, float]:
    engagement_score = math.log1p(max(0.0, raw_score))
    recency_score = 0.0
    if published_at:
        age_days = max(0.0, (now_ts() - published_at) / 86400)
        recency_score = max(0.0, 1.0 - age_days / max(1.0, days)) * 4.0
    total = engagement_score + relevance_score + recency_score
    return engagement_score, total


def make_item(
    *,
    source: str,
    source_id: str,
    source_url: str,
    title: str,
    author: str = "",
    published_at: float | None = None,
    text: str = "",
    raw_score: float = 0.0,
    raw: dict[str, Any] | None = None,
    query: str,
    days: int,
) -> SourceItem:
    title = clean_text(title, 300) or "(untitled)"
    text = clean_text(text, 1200)
    source_id = clean_text(source_id, 200) or source_url
    source_url = clean_text(source_url, 800) or f"{source}:{source_id}"
    terms = terms_for_query(query)
    relevance = score_relevance(title, text, terms)
    engagement, total = score_item(raw_score, published_at, relevance, days)
    return SourceItem(
        source=source,
        source_id=source_id,
        source_url=source_url,
        title=title,
        author=clean_text(author, 160),
        published_at=published_at,
        text=text,
        raw_score=float(raw_score or 0),
        engagement_score=round(engagement, 4),
        relevance_score=round(relevance, 4),
        total_score=round(total, 4),
        raw=raw or {},
    )


def http_json(url: str, timeout: int = 20) -> Any:
    req = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:500]
        raise SourceError(f"HTTP {exc.code}: {body}") from exc
    except URLError as exc:
        raise SourceError(str(exc.reason)) from exc
    except TimeoutError as exc:
        raise SourceError("request timed out") from exc
    try:
        return json.loads(raw.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        raise SourceError("response was not JSON") from exc


def http_bytes(url: str, timeout: int = 20) -> bytes:
    req = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:500]
        raise SourceError(f"HTTP {exc.code}: {body}") from exc
    except URLError as exc:
        raise SourceError(str(exc.reason)) from exc
    except TimeoutError as exc:
        raise SourceError("request timed out") from exc


def fetch_gdelt(query: str, days: int, limit: int) -> list[SourceItem]:
    params = {
        "query": query,
        "mode": "ArtList",
        "format": "json",
        "maxrecords": min(max(limit, 1), 250),
        "timespan": f"{max(1, int(days))}d",
        "sort": "HybridRel",
    }
    data = http_json("https://api.gdeltproject.org/api/v2/doc/doc?" + urlencode(params))
    rows = data.get("articles", []) if isinstance(data, dict) else []
    items: list[SourceItem] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        title = row.get("title") or ""
        url = row.get("url") or ""
        domain = row.get("domain") or row.get("sourceCollectionIdentifier") or ""
        published = (
            parse_iso_ts(row.get("seendate"))
            or parse_iso_ts(row.get("publishedDate"))
            or parse_iso_ts(row.get("published"))
        )
        text = " ".join(
            part for part in [
                row.get("sourcecountry") or "",
                row.get("language") or "",
                row.get("theme") or "",
            ] if part
        )
        items.append(
            make_item(
                source="gdelt",
                source_id=url or title,
                source_url=url,
                title=title,
                author=domain,
                published_at=published,
                text=text,
                raw_score=1.0,
                raw=row,
                query=query,
                days=days,
            )
        )
    return items


def fetch_google_news(query: str, days: int, limit: int) -> list[SourceItem]:
    params = {
        "q": f"{query} when:{max(1, int(days))}d",
        "hl": "en-US",
        "gl": "US",
        "ceid": "US:en",
    }
    raw = http_bytes("https://news.google.com/rss/search?" + urlencode(params))
    try:
        root = ElementTree.fromstring(raw)
    except ElementTree.ParseError as exc:
        raise SourceError("RSS response was not XML") from exc

    items: list[SourceItem] = []
    for node in root.findall("./channel/item")[: max(1, limit)]:
        title = node.findtext("title") or ""
        url = node.findtext("link") or ""
        pub_date = parse_rfc2822_ts(node.findtext("pubDate"))
        source_node = node.find("source")
        source_name = source_node.text if source_node is not None else ""
        text = " ".join(
            part for part in [
                node.findtext("description") or "",
                source_name or "",
            ] if part
        )
        guid = node.findtext("guid") or url or title
        items.append(
            make_item(
                source="gnews",
                source_id=guid,
                source_url=url,
                title=title,
                author=source_name or "",
                published_at=pub_date,
                text=text,
                raw_score=1.0,
                raw={
                    "title": title,
                    "link": url,
                    "pubDate": node.findtext("pubDate"),
                    "source": source_name,
                },
                query=query,
                days=days,
            )
        )
    return items


def fetch_reddit(query: str, days: int, limit: int) -> list[SourceItem]:
    params = {
        "q": query,
        "sort": "new",
        "t": "month",
        "limit": min(max(limit, 1), 100),
        "raw_json": 1,
    }
    data = http_json("https://www.reddit.com/search.json?" + urlencode(params))
    cutoff = cutoff_for_days(days)
    items: list[SourceItem] = []
    for child in data.get("data", {}).get("children", []):
        row = child.get("data", {}) if isinstance(child, dict) else {}
        created = safe_float(row.get("created_utc"), 0)
        if created and created < cutoff:
            continue
        permalink = row.get("permalink") or ""
        url = "https://www.reddit.com" + permalink if permalink else row.get("url")
        raw_score = safe_float(row.get("score")) + 2 * safe_float(row.get("num_comments"))
        items.append(
            make_item(
                source="reddit",
                source_id=row.get("id") or url,
                source_url=url,
                title=row.get("title"),
                author=row.get("author") or "",
                published_at=created or None,
                text=row.get("selftext") or "",
                raw_score=raw_score,
                raw=row,
                query=query,
                days=days,
            )
        )
    return items


def fetch_hn(query: str, days: int, limit: int) -> list[SourceItem]:
    cutoff = cutoff_for_days(days)
    params = {
        "query": query,
        "tags": "story",
        "numericFilters": f"created_at_i>{int(cutoff)}",
        "hitsPerPage": min(max(limit, 1), 100),
    }
    data = http_json("https://hn.algolia.com/api/v1/search_by_date?" + urlencode(params))
    items: list[SourceItem] = []
    for row in data.get("hits", []):
        created = safe_float(row.get("created_at_i"), 0)
        raw_score = safe_float(row.get("points")) + 2 * safe_float(row.get("num_comments"))
        item_url = row.get("url") or f"https://news.ycombinator.com/item?id={row.get('objectID')}"
        items.append(
            make_item(
                source="hn",
                source_id=row.get("objectID") or item_url,
                source_url=item_url,
                title=row.get("title") or row.get("story_title"),
                author=row.get("author") or "",
                published_at=created or None,
                text=row.get("comment_text") or "",
                raw_score=raw_score,
                raw=row,
                query=query,
                days=days,
            )
        )
    return items


def fetch_github(query: str, days: int, limit: int) -> list[SourceItem]:
    after = datetime.fromtimestamp(cutoff_for_days(days), timezone.utc).strftime("%Y-%m-%d")
    github_query = f"{query} created:>={after}"
    params = {
        "q": github_query,
        "sort": "stars",
        "order": "desc",
        "per_page": min(max(limit, 1), 100),
    }
    data = http_json("https://api.github.com/search/repositories?" + urlencode(params))
    items: list[SourceItem] = []
    for row in data.get("items", []):
        created = parse_iso_ts(row.get("created_at"))
        raw_score = (
            safe_float(row.get("stargazers_count"))
            + 3 * safe_float(row.get("forks_count"))
            + safe_float(row.get("watchers_count"))
        )
        owner = (row.get("owner") or {}).get("login") or ""
        text = " ".join(
            part for part in [
                row.get("description") or "",
                " ".join(row.get("topics") or []),
                row.get("language") or "",
            ] if part
        )
        items.append(
            make_item(
                source="github",
                source_id=row.get("full_name") or str(row.get("id") or ""),
                source_url=row.get("html_url"),
                title=row.get("full_name") or row.get("name"),
                author=owner,
                published_at=created,
                text=text,
                raw_score=raw_score,
                raw=row,
                query=query,
                days=days,
            )
        )
    return items


def fetch_polymarket(query: str, days: int, limit: int) -> list[SourceItem]:
    params = {
        "search": query,
        "limit": min(max(limit, 1), 100),
        "closed": "false",
    }
    data = http_json("https://gamma-api.polymarket.com/markets?" + urlencode(params))
    rows = data.get("markets", data) if isinstance(data, dict) else data
    if not isinstance(rows, list):
        rows = []
    items: list[SourceItem] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        created = (
            parse_iso_ts(row.get("createdAt"))
            or parse_iso_ts(row.get("created_at"))
            or parse_iso_ts(row.get("updatedAt"))
        )
        slug = row.get("slug") or row.get("eventSlug") or row.get("conditionId")
        url = row.get("url") or (f"https://polymarket.com/event/{slug}" if slug else "")
        raw_score = safe_float(row.get("volume")) + 0.25 * safe_float(row.get("liquidity"))
        title = row.get("question") or row.get("title") or row.get("market_slug") or slug
        text = " ".join(
            part for part in [
                row.get("description") or "",
                row.get("category") or "",
                row.get("endDate") or "",
            ] if part
        )
        items.append(
            make_item(
                source="polymarket",
                source_id=str(row.get("id") or row.get("conditionId") or url),
                source_url=url,
                title=title,
                author="",
                published_at=created,
                text=text,
                raw_score=raw_score,
                raw=row,
                query=query,
                days=days,
            )
        )
    return items


def fetch_youtube(query: str, days: int, limit: int) -> list[SourceItem]:
    binary = shutil.which("yt-dlp")
    if not binary:
        raise SourceError("yt-dlp is not installed")
    search = f"ytsearchdate{min(max(limit, 1), 50)}:{query}"
    cmd = [
        binary,
        search,
        "--dump-json",
        "--skip-download",
        "--no-playlist",
        "--ignore-errors",
        "--no-warnings",
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise SourceError("yt-dlp timed out") from exc
    if proc.returncode not in {0, 1}:
        raise SourceError((proc.stderr or proc.stdout or "yt-dlp failed").strip()[:500])

    cutoff = cutoff_for_days(days)
    items: list[SourceItem] = []
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        published = parse_iso_ts(row.get("timestamp")) or parse_iso_ts(row.get("upload_date"))
        if published and published < cutoff:
            continue
        video_id = row.get("id") or row.get("display_id") or row.get("webpage_url")
        url = row.get("webpage_url") or f"https://www.youtube.com/watch?v={video_id}"
        raw_score = (
            safe_float(row.get("view_count")) / 1000
            + safe_float(row.get("like_count")) / 50
            + 2 * safe_float(row.get("comment_count"))
        )
        items.append(
            make_item(
                source="youtube",
                source_id=str(video_id),
                source_url=url,
                title=row.get("title"),
                author=row.get("channel") or row.get("uploader") or "",
                published_at=published,
                text=row.get("description") or "",
                raw_score=raw_score,
                raw=row,
                query=query,
                days=days,
            )
        )
    return items


FETCHERS = {
    "gdelt": fetch_gdelt,
    "gnews": fetch_google_news,
    "reddit": fetch_reddit,
    "hn": fetch_hn,
    "github": fetch_github,
    "polymarket": fetch_polymarket,
    "youtube": fetch_youtube,
}


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    return con


def init_db(con: sqlite3.Connection) -> None:
    for stmt in SCHEMA:
        con.execute(stmt)
    con.commit()


def insert_run(
    con: sqlite3.Connection,
    topic: Topic,
    source: str,
    days: int,
) -> int:
    cur = con.execute(
        """
        INSERT INTO research_runs (
            topic_id, source, query, lookback_days, started_at, status
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (topic.topic_id or 0, source, topic.query, days, now_ts(), "running"),
    )
    con.commit()
    return int(cur.lastrowid)


def finish_run(
    con: sqlite3.Connection,
    run_id: int,
    status: str,
    item_count: int,
    error: str | None = None,
) -> None:
    con.execute(
        """
        UPDATE research_runs
        SET finished_at = ?, status = ?, item_count = ?, error = ?
        WHERE run_id = ?
        """,
        (now_ts(), status, item_count, error, run_id),
    )
    con.commit()


def upsert_topic(con: sqlite3.Connection, name: str, query: str, notes: str) -> int:
    ts = now_ts()
    con.execute(
        """
        INSERT INTO topics(name, query, notes, active, created_at, updated_at)
        VALUES (?, ?, ?, 1, ?, ?)
        ON CONFLICT(name) DO UPDATE SET
            query = excluded.query,
            notes = excluded.notes,
            active = 1,
            updated_at = excluded.updated_at
        """,
        (name.strip(), query.strip(), notes.strip(), ts, ts),
    )
    con.commit()
    row = con.execute(
        "SELECT topic_id FROM topics WHERE name = ?",
        (name.strip(),),
    ).fetchone()
    return int(row["topic_id"])


def load_topics(con: sqlite3.Connection, selector: str | None) -> list[Topic]:
    where = "active = 1"
    params: list[Any] = []
    if selector and selector != "all":
        if selector.isdigit():
            where += " AND topic_id = ?"
            params.append(int(selector))
        else:
            where += " AND name = ?"
            params.append(selector)
    rows = con.execute(
        f"""
        SELECT topic_id, name, query, notes
        FROM topics
        WHERE {where}
        ORDER BY topic_id
        """,
        params,
    ).fetchall()
    return [
        Topic(
            topic_id=int(row["topic_id"]),
            name=row["name"],
            query=row["query"],
            notes=row["notes"] or "",
        )
        for row in rows
    ]


def disable_topic(con: sqlite3.Connection, selector: str) -> int:
    if selector.isdigit():
        cur = con.execute(
            "UPDATE topics SET active = 0, updated_at = ? WHERE topic_id = ?",
            (now_ts(), int(selector)),
        )
    else:
        cur = con.execute(
            "UPDATE topics SET active = 0, updated_at = ? WHERE name = ?",
            (now_ts(), selector),
        )
    con.commit()
    return int(cur.rowcount)


def upsert_finding(
    con: sqlite3.Connection,
    item: SourceItem,
    run_id: int,
    topic: Topic,
) -> int:
    ts = now_ts()
    raw_json = json.dumps(item.raw, ensure_ascii=True, sort_keys=True)
    con.execute(
        """
        INSERT INTO findings (
            source, source_id, source_url, title, author, published_at,
            first_seen_at, last_seen_at, raw_score, engagement_score,
            relevance_score, total_score, text, raw_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source_url) DO UPDATE SET
            last_seen_at = excluded.last_seen_at,
            title = excluded.title,
            author = excluded.author,
            published_at = COALESCE(excluded.published_at, findings.published_at),
            raw_score = MAX(findings.raw_score, excluded.raw_score),
            engagement_score = MAX(findings.engagement_score, excluded.engagement_score),
            relevance_score = MAX(findings.relevance_score, excluded.relevance_score),
            total_score = MAX(findings.total_score, excluded.total_score),
            text = excluded.text,
            raw_json = excluded.raw_json
        """,
        (
            item.source,
            item.source_id,
            item.source_url,
            item.title,
            item.author,
            item.published_at,
            ts,
            ts,
            item.raw_score,
            item.engagement_score,
            item.relevance_score,
            item.total_score,
            item.text,
            raw_json,
        ),
    )
    row = con.execute(
        "SELECT finding_id FROM findings WHERE source_url = ?",
        (item.source_url,),
    ).fetchone()
    finding_id = int(row["finding_id"])
    con.execute(
        """
        INSERT OR REPLACE INTO finding_sightings (
            finding_id, run_id, topic_id, seen_at, matched_query,
            raw_score, engagement_score, relevance_score, total_score
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            finding_id,
            run_id,
            topic.topic_id or 0,
            ts,
            topic.query,
            item.raw_score,
            item.engagement_score,
            item.relevance_score,
            item.total_score,
        ),
    )
    con.commit()
    return finding_id


def parse_sources(value: str | None) -> list[str]:
    if not value:
        return list(DEFAULT_SOURCES)
    if value == "all":
        return list(ALL_SOURCES)
    sources = [part.strip().lower() for part in value.split(",") if part.strip()]
    unknown = sorted(set(sources) - set(ALL_SOURCES))
    if unknown:
        raise SystemExit(f"unknown source(s): {', '.join(unknown)}")
    return sources


def run_topic_source(
    con: sqlite3.Connection,
    topic: Topic,
    source: str,
    days: int,
    limit: int,
    min_relevance: float,
) -> tuple[int, str | None]:
    run_id = insert_run(con, topic, source, days)
    fetcher = FETCHERS[source]
    try:
        items = fetcher(topic.query, days, limit)
        items = [
            item for item in items
            if item.relevance_score >= min_relevance
        ]
        for item in items:
            upsert_finding(con, item, run_id, topic)
    except SourceError as exc:
        finish_run(con, run_id, "error", 0, str(exc))
        return 0, str(exc)
    except Exception as exc:  # defensive: source adapters should not kill a full run
        finish_run(con, run_id, "error", 0, repr(exc))
        return 0, repr(exc)
    finish_run(con, run_id, "success", len(items), None)
    return len(items), None


def print_topics(con: sqlite3.Connection) -> None:
    rows = con.execute(
        """
        SELECT topic_id, name, query, notes, active, created_at, updated_at
        FROM topics
        ORDER BY active DESC, topic_id
        """
    ).fetchall()
    if not rows:
        print("no topics yet")
        return
    for row in rows:
        status = "active" if row["active"] else "inactive"
        notes = f" | {row['notes']}" if row["notes"] else ""
        print(
            f"{row['topic_id']:>3} [{status}] {row['name']} :: "
            f"{row['query']}{notes}"
        )


def print_status(con: sqlite3.Connection, db_path: Path) -> None:
    topic_count = con.execute(
        "SELECT COUNT(*) AS n FROM topics WHERE active = 1"
    ).fetchone()["n"]
    finding_count = con.execute(
        "SELECT COUNT(*) AS n FROM findings"
    ).fetchone()["n"]
    run_count = con.execute(
        "SELECT COUNT(*) AS n FROM research_runs"
    ).fetchone()["n"]
    last_run = con.execute(
        """
        SELECT started_at, status, source, query, item_count, error
        FROM research_runs
        ORDER BY started_at DESC
        LIMIT 1
        """
    ).fetchone()
    print(f"db: {db_path}")
    print(f"active_topics: {topic_count}")
    print(f"findings: {finding_count}")
    print(f"runs: {run_count}")
    if last_run:
        err = f" | error={last_run['error']}" if last_run["error"] else ""
        print(
            "last_run: "
            f"{iso_date(last_run['started_at'])} {last_run['source']} "
            f"{last_run['status']} items={last_run['item_count']} "
            f"query={last_run['query']}{err}"
        )


def run_collector(args: argparse.Namespace) -> None:
    sources = parse_sources(args.sources)
    con = connect(Path(args.db))
    init_db(con)

    if args.query:
        topics = [Topic(topic_id=None, name="ad-hoc", query=args.query)]
    else:
        topics = load_topics(con, args.topic)

    if not topics:
        print("no active topics matched; add one with `add-topic` or pass --query")
        return

    if args.dry_run:
        for topic in topics:
            for source in sources:
                print(
                    f"would run source={source} topic={topic.name} "
                    f"days={args.days} limit={args.limit} query={topic.query}"
                )
        return

    total = 0
    errors: list[str] = []
    last_source_call: dict[str, float] = {}
    for topic in topics:
        for source in sources:
            min_interval = SOURCE_MIN_INTERVAL_SECONDS.get(source, 0.0)
            last_call = last_source_call.get(source)
            if min_interval and last_call:
                wait = min_interval - (time.monotonic() - last_call)
                if wait > 0:
                    time.sleep(wait)
            last_source_call[source] = time.monotonic()
            count, error = run_topic_source(
                con,
                topic,
                source,
                args.days,
                args.limit,
                args.min_relevance,
            )
            total += count
            if error:
                errors.append(f"{topic.name}/{source}: {error}")
            print(f"{topic.name}/{source}: {count} findings")

    print(f"stored sightings: {total}")
    if errors:
        print("source errors:")
        for error in errors:
            print(f"- {error}")


def brief_rows(
    con: sqlite3.Connection,
    days: int,
    limit: int,
    min_relevance: float = 1.0,
    topic_selector: str | None = None,
) -> list[sqlite3.Row]:
    cutoff = cutoff_for_days(days)
    topic_filter = ""
    params: list[Any] = [cutoff]
    if topic_selector and topic_selector != "all":
        if topic_selector.isdigit():
            topic_filter = "AND s.topic_id = ?"
            params.append(int(topic_selector))
        else:
            topic_filter = "AND t.name = ?"
            params.append(topic_selector)
    params.extend([min_relevance, limit])
    return con.execute(
        """
        SELECT
            f.finding_id,
            f.source,
            f.source_url,
            f.title,
            f.author,
            f.published_at,
            MAX(s.seen_at) AS last_seen_at,
            MAX(s.total_score) AS seen_score,
            MAX(f.total_score) AS total_score,
            COUNT(*) AS sightings,
            GROUP_CONCAT(DISTINCT COALESCE(t.name, 'ad-hoc')) AS topics
        FROM findings f
        JOIN finding_sightings s ON s.finding_id = f.finding_id
        LEFT JOIN topics t ON t.topic_id = s.topic_id
        WHERE s.seen_at >= ?
        {topic_filter}
        GROUP BY f.finding_id
        HAVING MAX(s.relevance_score) >= ?
        ORDER BY seen_score DESC, sightings DESC, last_seen_at DESC
        LIMIT ?
        """.format(topic_filter=topic_filter),
        params,
    ).fetchall()


def print_brief(args: argparse.Namespace) -> None:
    con = connect(Path(args.db))
    init_db(con)
    rows = brief_rows(
        con,
        args.days,
        args.limit,
        args.min_relevance,
        args.topic,
    )
    if args.json:
        payload = [
            {
                "source": row["source"],
                "title": row["title"],
                "url": row["source_url"],
                "author": row["author"],
                "published_at": row["published_at"],
                "published_date": iso_date(row["published_at"]),
                "score": row["seen_score"],
                "sightings": row["sightings"],
                "topics": (row["topics"] or "").split(",") if row["topics"] else [],
            }
            for row in rows
        ]
        print(json.dumps(payload, ensure_ascii=True, indent=2))
        return

    print(f"Narrative watchlist brief: last {args.days}d")
    if not rows:
        print("no findings yet")
        return
    for row in rows:
        author = f" by {row['author']}" if row["author"] else ""
        topics = f" | topics: {row['topics']}" if row["topics"] else ""
        print(
            f"- [{row['source']}] {row['title']}{author} "
            f"({iso_date(row['published_at'])}, score {row['seen_score']:.2f}, "
            f"seen {row['sightings']}x{topics})"
        )
        print(f"  {row['source_url']}")


def export_csv(args: argparse.Namespace) -> None:
    con = connect(Path(args.db))
    init_db(con)
    rows = brief_rows(
        con,
        args.days,
        args.limit,
        args.min_relevance,
        args.topic,
    )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow([
            "source",
            "title",
            "url",
            "author",
            "published_date",
            "score",
            "sightings",
            "topics",
        ])
        for row in rows:
            writer.writerow([
                row["source"],
                row["title"],
                row["source_url"],
                row["author"],
                iso_date(row["published_at"]),
                f"{row['seen_score']:.4f}",
                row["sightings"],
                row["topics"] or "",
            ])
    print(f"wrote {out}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Standalone free-first narrative watchlist collector. "
            "Uses public/free sources and writes only a local SQLite DB."
        )
    )
    parser.add_argument(
        "--db",
        default=str(DEFAULT_DB),
        help=f"SQLite DB path (default: {DEFAULT_DB})",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="Create the local watchlist database.")

    add = sub.add_parser("add-topic", help="Add or update a watchlist topic.")
    add.add_argument("name")
    add.add_argument(
        "--query",
        help="Search query; defaults to the topic name.",
    )
    add.add_argument("--notes", default="")

    disable = sub.add_parser("disable-topic", help="Deactivate a topic.")
    disable.add_argument("topic", help="Topic id or exact name.")

    sub.add_parser("list-topics", help="List configured topics.")
    sub.add_parser("status", help="Show DB counts and last run.")

    run = sub.add_parser("run", help="Run source searches and store findings.")
    run.add_argument(
        "--topic",
        default="all",
        help="Topic id/name, or all active topics (default: all).",
    )
    run.add_argument(
        "--query",
        help="Ad-hoc query; bypasses saved topics.",
    )
    run.add_argument(
        "--sources",
        default=",".join(DEFAULT_SOURCES),
        help=(
            "Comma-separated sources: reddit,hn,github,polymarket,youtube "
            "(default: gdelt,gnews,reddit,hn,github,polymarket; "
            "use all to include youtube)."
        ),
    )
    run.add_argument("--days", type=int, default=30)
    run.add_argument("--limit", type=int, default=25)
    run.add_argument(
        "--min-relevance",
        type=float,
        default=1.0,
        help="Minimum query relevance required before storing a source item.",
    )
    run.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned searches without network requests or writes.",
    )

    brief = sub.add_parser("brief", help="Print top recent findings.")
    brief.add_argument(
        "--topic",
        default="all",
        help="Topic id/name, or all topics (default: all).",
    )
    brief.add_argument("--days", type=int, default=7)
    brief.add_argument("--limit", type=int, default=25)
    brief.add_argument(
        "--min-relevance",
        type=float,
        default=1.0,
        help="Minimum stored query relevance required in the brief.",
    )
    brief.add_argument("--json", action="store_true")

    export = sub.add_parser("export", help="Export recent findings to CSV.")
    export.add_argument(
        "--topic",
        default="all",
        help="Topic id/name, or all topics (default: all).",
    )
    export.add_argument("--days", type=int, default=30)
    export.add_argument("--limit", type=int, default=500)
    export.add_argument(
        "--min-relevance",
        type=float,
        default=1.0,
        help="Minimum stored query relevance required in the export.",
    )
    export.add_argument(
        "--out",
        default=str(Path("analysis") / "narrative_watchlist_findings.csv"),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    db_path = Path(args.db)
    con = connect(db_path)
    init_db(con)

    if args.cmd == "init":
        print(f"initialized {db_path}")
    elif args.cmd == "add-topic":
        query = args.query or args.name
        topic_id = upsert_topic(con, args.name, query, args.notes)
        print(f"saved topic {topic_id}: {args.name} :: {query}")
    elif args.cmd == "disable-topic":
        count = disable_topic(con, args.topic)
        print(f"disabled topics: {count}")
    elif args.cmd == "list-topics":
        print_topics(con)
    elif args.cmd == "status":
        print_status(con, db_path)
    elif args.cmd == "run":
        con.close()
        run_collector(args)
    elif args.cmd == "brief":
        con.close()
        print_brief(args)
    elif args.cmd == "export":
        con.close()
        export_csv(args)
    else:
        raise SystemExit(f"unknown command: {args.cmd}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
