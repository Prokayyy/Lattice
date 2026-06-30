import asyncio
import json
import sqlite3
import time
from datetime import datetime
from html import escape
from pathlib import Path

from config import (
    build_defined_token_url,
    HELIUS_API_KEY,
    HELIUS_LINEAGE_ENABLED,
    HELIUS_LINEAGE_MAX_PAGES,
    TICKER_LINEAGE_CACHE_TTL_SECONDS,
    TICKER_LINEAGE_ENABLED,
    TICKER_LINEAGE_LIMIT,
    TICKER_LINEAGE_MAX_CANDIDATES,
    TICKER_LINEAGE_MINT_CONCURRENCY,
    TICKER_LINEAGE_OVERRIDES_FILE
)

from sources.mint_age import (
    resolve_mint_age
)

from filters.contracts import (
    is_excluded_contract_address
)

from storage.sqlite import (
    DATABASE_NAME
)


JUPITER_ENDPOINTS = [
    "https://tokens.jup.ag/tokens/v1/all",
    "https://token.jup.ag/all"
]

PUMPFUN_ENDPOINTS = [
    (
        "https://frontend-api.pump.fun/coins/search"
        "?searchTerm={ticker}&limit=50&includeNsfw=true"
    ),
    (
        "https://client-api-2-74b1891ee9f9.herokuapp.com"
        "/coins?searchTerm={ticker}&limit=50&includeNsfw=true"
    )
]

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://pump.fun",
    "Referer": "https://pump.fun/"
}

LINEAGE_CACHE = {}
LINEAGE_DB_READY = False
ROOT = Path(__file__).resolve().parent.parent


def normalize_ticker(ticker):

    return str(ticker or "").strip().upper()


def safe_float(value, default=0):

    try:
        return float(value or default)
    except (TypeError, ValueError):
        return default


def safe_int(value, default=0):

    try:
        return int(float(value or default))
    except (TypeError, ValueError):
        return default


def parse_time_value(value):

    if value in (None, ""):
        return None

    if isinstance(value, (int, float)):
        timestamp = float(value)
    else:
        text = str(value).strip()

        if not text:
            return None

        if text.isdigit():
            timestamp = float(text)
        else:
            try:
                timestamp = datetime.fromisoformat(
                    text.replace("Z", "+00:00")
                ).timestamp()
            except ValueError:
                return None

    if timestamp > 1_000_000_000_000:
        timestamp = timestamp / 1000

    return timestamp if timestamp > 0 else None


def format_mint_time(block_time):

    if not block_time:
        return "unknown"

    return datetime.utcfromtimestamp(
        block_time
    ).strftime(
        "%Y-%m-%d %H:%M UTC"
    )


def short_address(address):

    if not address or len(address) <= 12:
        return address or "unknown"

    return (
        f"{address[:6]}..."
        f"{address[-6:]}"
    )


def same_address(
    left,
    right
):

    return str(left or "") == str(right or "")


def find_record_index(
    records,
    address
):

    for index, record in enumerate(records):
        if same_address(
            record.get("address"),
            address
        ):
            return index

    return None


def sort_ranked_records(records):

    return sorted(
        records,
        key=lambda record: (
            record["mint_time"],
            record.get("address", "")
        )
    )


def serialize_sources(sources):

    return json.dumps(
        sorted(
            str(source)
            for source in sources or []
            if source
        )
    )


def deserialize_sources(value):

    if not value:
        return set()

    try:
        decoded = json.loads(value)
    except (TypeError, ValueError):
        return {
            item.strip()
            for item in str(value).split(",")
            if item.strip()
        }

    if isinstance(decoded, list):
        return {
            str(item)
            for item in decoded
            if item
        }

    return set()


def serialize_source_urls(source_urls):

    return json.dumps(
        sorted(
            str(url)
            for url in source_urls or []
            if url
        )
    )


def deserialize_source_urls(value):

    if not value:
        return []

    try:
        decoded = json.loads(value)
    except (TypeError, ValueError):
        return [
            item.strip()
            for item in str(value).split(",")
            if item.strip()
        ]

    if isinstance(decoded, list):
        return [
            str(item)
            for item in decoded
            if item
        ]

    return []


def ensure_lineage_db():

    global LINEAGE_DB_READY

    if LINEAGE_DB_READY:
        return

    with sqlite3.connect(DATABASE_NAME) as db:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS ticker_lineage_records (
                ticker TEXT NOT NULL,
                token_address TEXT NOT NULL,
                symbol TEXT,
                name TEXT,
                chain_name TEXT,
                pair_address TEXT,
                pair_created_at REAL,
                market_cap REAL,
                mint_time REAL,
                mint_age_source TEXT,
                source_labels TEXT,
                source_urls TEXT,
                confidence TEXT,
                first_seen_at REAL,
                updated_at REAL,
                PRIMARY KEY (ticker, token_address)
            )
            """
        )
        db.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_ticker_lineage_records_ticker_time
            ON ticker_lineage_records (
                ticker,
                mint_time
            )
            """
        )

    LINEAGE_DB_READY = True


def load_persisted_lineage_records(ticker):

    ensure_lineage_db()

    with sqlite3.connect(DATABASE_NAME) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute(
            """
            SELECT *
            FROM ticker_lineage_records
            WHERE ticker = ?
                AND mint_time IS NOT NULL
            ORDER BY mint_time ASC,
                token_address ASC
            """,
            (ticker,)
        ).fetchall()

    records = []

    for row in rows:
        pair_address = row["pair_address"] or ""
        chain_name = row["chain_name"] or "solana"
        pair = None

        if pair_address:
            pair = {
                "chainId": chain_name,
                "pairAddress": pair_address
            }

        source = row["mint_age_source"] or "persisted"
        mint_time = safe_float(row["mint_time"], 0)
        records.append(
            {
                "address": row["token_address"],
                "symbol": row["symbol"] or ticker,
                "name": row["name"] or "Unknown",
                "sources": deserialize_sources(
                    row["source_labels"]
                ) | {"cache"},
                "source_urls": deserialize_source_urls(
                    row["source_urls"]
                ),
                "pair": pair,
                "pair_created_at": row["pair_created_at"],
                "market_cap": row["market_cap"],
                "mint_time": mint_time,
                "mint_age": {
                    "block_time": mint_time,
                    "source": source,
                    "age_hours": (
                        (time.time() - mint_time) / 3600
                        if mint_time
                        else 0
                    )
                },
                "confidence": row["confidence"] or "cached",
                "first_seen_at": row["first_seen_at"]
            }
        )

    return records


def lineage_confidence(record):

    sources = set(record.get("sources", set()))
    mint_source = (
        record.get("mint_age", {})
        .get("source", "")
    )

    if "manual" in sources and mint_source == "mint_tx":
        return "manual+onchain"

    if mint_source == "mint_tx" and len(sources) >= 2:
        return "high"

    if mint_source == "mint_tx":
        return "onchain"

    if "manual" in sources:
        return "manual_unverified"

    if mint_source:
        return mint_source

    return "discovered"


def persist_lineage_records(ticker, records):

    if not records:
        return

    ensure_lineage_db()
    now = time.time()

    with sqlite3.connect(DATABASE_NAME) as db:
        for record in records:
            mint_time = safe_float(record.get("mint_time"), 0)

            if mint_time <= 0:
                continue

            pair = record.get("pair") or {}
            mint_age = record.get("mint_age") or {}
            db.execute(
                """
                INSERT INTO ticker_lineage_records (
                    ticker,
                    token_address,
                    symbol,
                    name,
                    chain_name,
                    pair_address,
                    pair_created_at,
                    market_cap,
                    mint_time,
                    mint_age_source,
                    source_labels,
                    source_urls,
                    confidence,
                    first_seen_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(ticker, token_address) DO UPDATE SET
                    symbol = excluded.symbol,
                    name = excluded.name,
                    chain_name = excluded.chain_name,
                    pair_address = COALESCE(
                        excluded.pair_address,
                        ticker_lineage_records.pair_address
                    ),
                    pair_created_at = COALESCE(
                        excluded.pair_created_at,
                        ticker_lineage_records.pair_created_at
                    ),
                    market_cap = COALESCE(
                        excluded.market_cap,
                        ticker_lineage_records.market_cap
                    ),
                    mint_time = MIN(
                        ticker_lineage_records.mint_time,
                        excluded.mint_time
                    ),
                    mint_age_source = excluded.mint_age_source,
                    source_labels = excluded.source_labels,
                    source_urls = excluded.source_urls,
                    confidence = excluded.confidence,
                    first_seen_at = COALESCE(
                        excluded.first_seen_at,
                        ticker_lineage_records.first_seen_at
                    ),
                    updated_at = excluded.updated_at
                """,
                (
                    ticker,
                    record.get("address"),
                    record.get("symbol") or ticker,
                    record.get("name") or "Unknown",
                    pair.get("chainId", "solana"),
                    pair.get("pairAddress"),
                    record.get("pair_created_at"),
                    record.get("market_cap"),
                    mint_time,
                    mint_age.get("source", ""),
                    serialize_sources(record.get("sources", set())),
                    serialize_source_urls(record.get("source_urls", [])),
                    lineage_confidence(record),
                    record.get("first_seen_at"),
                    now
                )
            )


def merge_ranked_record(
    records,
    incoming
):

    if not incoming:
        return records, False

    merged = list(records)

    index = find_record_index(
        merged,
        incoming.get("address")
    )

    if index is None:
        merged.append(incoming)
        return sort_ranked_records(merged), True

    existing = dict(merged[index])

    existing["sources"] = set(
        existing.get("sources", set())
    ) | set(
        incoming.get("sources", set())
    )
    existing["source_urls"] = list(
        dict.fromkeys(
            list(existing.get("source_urls", []))
            + list(incoming.get("source_urls", []))
        )
    )

    for key in (
        "pair",
        "market_cap",
        "mint_age"
    ):
        if not existing.get(key) and incoming.get(key):
            existing[key] = incoming[key]

    for key in ("pair_created_at", "mint_time"):
        current = safe_float(existing.get(key), 0)
        incoming_value = safe_float(incoming.get(key), 0)

        if incoming_value > 0 and (
            current <= 0
            or incoming_value < current
        ):
            existing[key] = incoming.get(key)

            if key == "mint_time" and incoming.get("mint_age"):
                existing["mint_age"] = incoming["mint_age"]

    if incoming.get("first_seen_at"):
        current = safe_float(
            existing.get("first_seen_at"),
            0
        )
        incoming_seen = safe_float(
            incoming.get("first_seen_at"),
            0
        )

        if current <= 0 or incoming_seen < current:
            existing["first_seen_at"] = incoming_seen

    if (
        existing.get("symbol") == "UNKNOWN"
        and incoming.get("symbol")
    ):
        existing["symbol"] = incoming["symbol"]

    if (
        existing.get("name") == "Unknown"
        and incoming.get("name")
    ):
        existing["name"] = incoming["name"]

    merged[index] = existing
    return sort_ranked_records(merged), False


def merge_ranked_records(
    current,
    incoming
):

    merged = list(current)
    added = 0

    for record in incoming:
        merged, was_added = merge_ranked_record(
            merged,
            record
        )

        if was_added:
            added += 1

    return merged, added


def build_defined_url(record):

    pair = record.get("pair") or {}

    return build_defined_token_url(
        address=record.get("address", ""),
        chain=pair.get("chainId", "solana"),
        pair_address=pair.get("pairAddress", "")
    )


def format_lineage_sources(record):

    sources = sorted(
        record.get("sources", [])
    )

    if not sources:
        return "unknown"

    defined_url = build_defined_url(
        record
    )

    formatted = []

    for source in sources:

        if source == "dex" and defined_url:
            formatted.append(
                f'<a href="{escape(defined_url, quote=True)}">dex</a>'
            )
        else:
            formatted.append(
                escape(str(source))
            )

    return ",".join(formatted)


def format_lineage_confidence(record):

    label = lineage_confidence(record).replace("_", " ")
    return escape(label)


def format_evidence_link(record):

    urls = record.get("source_urls", []) or []

    if not urls:
        return ""

    return (
        ' | <a href="'
        f'{escape(str(urls[0]), quote=True)}'
        '">evidence</a>'
    )


def format_defined_address_link(record):

    address = record.get("address", "")
    label = escape(
        short_address(address)
    )
    defined_url = build_defined_url(
        record
    )

    if not defined_url:
        return label

    return (
        f'<a href="{escape(defined_url, quote=True)}">'
        f"{label}"
        "</a>"
    )


def register_record(
    records,
    address,
    symbol,
    name=None,
    source=None,
    source_url=None,
    source_urls=None,
    pair=None,
    pair_created_at=None,
    market_cap=None,
    mint_time=None,
    first_seen_at=None
):

    if not address:
        return

    if is_excluded_contract_address(address):
        return

    record = records.setdefault(
        address,
        {
            "address": address,
            "symbol": symbol or "UNKNOWN",
            "name": name or "Unknown",
            "sources": set(),
            "source_urls": [],
            "pair": None,
            "pair_created_at": None,
            "market_cap": None,
            "mint_time": None,
            "first_seen_at": None
        }
    )

    if symbol and record["symbol"] == "UNKNOWN":
        record["symbol"] = symbol

    if name and record["name"] == "Unknown":
        record["name"] = name

    if source:
        record["sources"].add(source)

    urls = []

    if source_url:
        urls.append(source_url)

    if source_urls:
        urls.extend(source_urls)

    if urls:
        record["source_urls"] = list(
            dict.fromkeys(
                record.get("source_urls", [])
                + [
                    str(url)
                    for url in urls
                    if url
                ]
            )
        )

    if pair:
        record["pair"] = pair

    if pair_created_at:
        current = record.get("pair_created_at")
        if not current or pair_created_at < current:
            record["pair_created_at"] = pair_created_at

    if market_cap is not None:
        record["market_cap"] = market_cap

    parsed_mint_time = parse_time_value(mint_time)

    if parsed_mint_time:
        current = safe_float(record.get("mint_time"), 0)

        if current <= 0 or parsed_mint_time < current:
            record["mint_time"] = parsed_mint_time
            record["mint_age"] = {
                "block_time": parsed_mint_time,
                "source": "manual_mint_time",
                "age_hours": (
                    time.time()
                    - parsed_mint_time
                ) / 3600
            }

    parsed_first_seen = parse_time_value(first_seen_at)

    if parsed_first_seen:
        current = safe_float(record.get("first_seen_at"), 0)

        if current <= 0 or parsed_first_seen < current:
            record["first_seen_at"] = parsed_first_seen


def register_dex_pair(records, pair, ticker):

    if pair.get("chainId") != "solana":
        return

    base = pair.get("baseToken", {})
    quote = pair.get("quoteToken", {})

    token = None

    if normalize_ticker(base.get("symbol")) == ticker:
        token = base
    elif normalize_ticker(quote.get("symbol")) == ticker:
        token = quote

    if not token:
        return

    register_record(
        records,
        token.get("address"),
        token.get("symbol"),
        name=token.get("name"),
        source="dex",
        pair=pair,
        pair_created_at=pair.get("pairCreatedAt")
    )


async def fetch_jupiter_matches(session, ticker):

    for url in JUPITER_ENDPOINTS:

        try:
            async with session.get(
                url,
                headers={
                    "Accept": "application/json"
                },
                timeout=30
            ) as response:

                if response.status != 200:
                    continue

                data = await response.json(
                    content_type=None
                )

            token_list = (
                data
                if isinstance(data, list)
                else data.get("tokens", [])
            )

            return [
                token
                for token in token_list
                if normalize_ticker(
                    token.get("symbol")
                ) == ticker
            ]

        except Exception:
            continue

    return []


async def fetch_pumpfun_matches(session, ticker):

    for template in PUMPFUN_ENDPOINTS:

        try:
            async with session.get(
                template.format(ticker=ticker),
                headers=BROWSER_HEADERS,
                timeout=15
            ) as response:

                if response.status != 200:
                    continue

                data = await response.json(
                    content_type=None
                )

            coins = (
                data
                if isinstance(data, list)
                else data.get("coins", [])
            )

            return [
                coin
                for coin in coins
                if normalize_ticker(
                    coin.get("symbol")
                ) == ticker
            ]

        except Exception:
            continue

    return []


async def fetch_helius_matches(session, ticker):

    if not HELIUS_LINEAGE_ENABLED or not HELIUS_API_KEY:
        return []

    url = (
        f"https://mainnet.helius-rpc.com/"
        f"?api-key={HELIUS_API_KEY}"
    )
    matches = []

    for page in range(1, HELIUS_LINEAGE_MAX_PAGES + 1):

        payload = {
            "jsonrpc": "2.0",
            "id": f"lineage-{page}",
            "method": "searchAssets",
            "params": {
                "tokenType": "fungibleToken",
                "name": ticker,
                "page": page,
                "limit": 1000
            }
        }

        try:
            async with session.post(
                url,
                json=payload,
                timeout=15
            ) as resp:

                if resp.status != 200:
                    break

                data = await resp.json(content_type=None)

        except Exception:
            break

        # Helius returns HTTP 200 with a JSON-RPC error body on bad
        # params (e.g. unsupported tokenType, or name search without an
        # owner address). Surface it instead of silently returning [].
        if isinstance(data, dict) and data.get("error"):
            print(
                "Helius lineage searchAssets error: "
                f"{data['error']}"
            )
            break

        items = (
            data.get("result", {}).get("items", [])
            if isinstance(data.get("result"), dict)
            else []
        )

        if not items:
            break

        for item in items:
            content = item.get("content") or {}
            metadata = content.get("metadata") or {}
            item_symbol = normalize_ticker(
                metadata.get("symbol", "")
            )
            item_name = normalize_ticker(
                metadata.get("name", "")
            )

            if item_symbol != ticker and item_name != ticker:
                continue

            address = item.get("id")

            if not address:
                continue

            matches.append({
                "address": address,
                "symbol": metadata.get("symbol") or ticker,
                "name": metadata.get("name") or "",
            })

        if len(items) < 1000:
            break

    return matches


def lineage_overrides_path():

    path = Path(TICKER_LINEAGE_OVERRIDES_FILE)

    if not path.is_absolute():
        path = ROOT / path

    return path


def load_lineage_overrides():

    path = lineage_overrides_path()

    if not path.exists():
        return []

    try:
        data = json.loads(
            path.read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return []

    if isinstance(data, list):
        return data

    if isinstance(data, dict):
        entries = []

        for ticker, values in data.items():
            values = values if isinstance(values, list) else [values]

            for value in values:
                if isinstance(value, dict):
                    item = dict(value)
                    item.setdefault("ticker", ticker)
                    entries.append(item)

        return entries

    return []


def override_entries_for_ticker(ticker):

    normalized = normalize_ticker(ticker)
    entries = []

    for entry in load_lineage_overrides():
        if not isinstance(entry, dict):
            continue

        entry_ticker = normalize_ticker(
            entry.get("ticker")
            or entry.get("symbol")
        )

        if entry_ticker == normalized:
            entries.append(entry)

    return entries


def register_lineage_overrides(records, ticker):

    for entry in override_entries_for_ticker(ticker):
        address = (
            entry.get("address")
            or entry.get("token_address")
            or entry.get("mint")
        )

        if not address:
            continue

        source_url = (
            entry.get("source_url")
            or entry.get("url")
            or entry.get("tweet_url")
        )
        source_urls = entry.get("source_urls") or []

        if isinstance(source_urls, str):
            source_urls = [source_urls]

        register_record(
            records,
            address,
            entry.get("symbol", ticker),
            name=entry.get("name"),
            source="manual",
            source_url=source_url,
            source_urls=source_urls,
            pair_created_at=(
                parse_time_value(entry.get("pair_created_at"))
                or parse_time_value(entry.get("created_at"))
                or parse_time_value(entry.get("first_seen_at"))
            ),
            market_cap=safe_float(
                entry.get("market_cap")
                or entry.get("market_cap_usd"),
                None
            ),
            mint_time=(
                entry.get("mint_time")
                or entry.get("minted_at")
                or entry.get("created_at")
            ),
            first_seen_at=(
                entry.get("first_seen_at")
                or entry.get("tweet_at")
                or entry.get("created_at")
            )
        )


def lineage_candidate_sort_key(record):

    hint_time = (
        parse_time_value(record.get("mint_time"))
        or parse_time_value(record.get("pair_created_at"))
        or parse_time_value(record.get("first_seen_at"))
        or float("inf")
    )

    priority = 1

    if "manual" in record.get("sources", set()):
        priority = 0
    elif "cache" in record.get("sources", set()):
        priority = 0.5

    return (
        priority,
        hint_time,
        record.get("address", "")
    )


async def resolve_record_mint_age(
    session,
    record,
    semaphore
):

    if record.get("mint_time") and record.get("mint_age"):
        return dict(record)

    async with semaphore:
        # Lineage ranks tokens against each other by birth time, so it needs the
        # true InitializeMint time — not the early-exit "old enough" floor the
        # entry gate uses. Walk all the way back to the mint's first signature.
        mint_age = await resolve_mint_age(
            session,
            record["address"],
            record.get("pair_created_at"),
            walk_to_genesis=True
        )

    if not mint_age or not mint_age.get("block_time"):
        first_seen_at = safe_float(
            record.get("first_seen_at"),
            0
        )

        if "manual" in record.get("sources", set()) and first_seen_at > 0:
            enriched = dict(record)
            enriched["mint_age"] = {
                "block_time": first_seen_at,
                "source": "manual_first_seen",
                "age_hours": (
                    time.time()
                    - first_seen_at
                ) / 3600
            }
            enriched["mint_time"] = first_seen_at
            return enriched

        return None

    enriched = dict(record)
    enriched["mint_age"] = mint_age
    enriched["mint_time"] = mint_age["block_time"]
    return enriched


def build_lineage_text(
    ticker,
    records,
    focus_address,
    total_found
):

    if not records:
        return (
            "━━━━━━━━━━ TICKER LINEAGE ━━━━━━━━━━\n"
            "No same-ticker Solana mints with mint tx time "
            "were found."
        )

    focus_index = None

    for index, record in enumerate(records, 1):
        if record["address"] == focus_address:
            focus_index = index
            break

    top_records = records[:TICKER_LINEAGE_LIMIT]
    lines = [
        "━━━━━━━━━━ TICKER LINEAGE ━━━━━━━━━━"
    ]

    for index, record in enumerate(top_records, 1):
        marker = (
            " ← ignition"
            if record["address"] == focus_address
            else ""
        )
        lines.extend([
            f"{index:02d}. {format_mint_time(record['mint_time'])}",
            f"    ${escape(str(record['symbol']))}",
            (
                "    "
                f"{format_defined_address_link(record)}"
                f"{marker}"
            ),
            (
                "    "
                f"{format_lineage_sources(record)}"
                " | "
                f"{format_lineage_confidence(record)}"
                f"{format_evidence_link(record)}"
            ),
            ""
        ])

    if focus_index and focus_index > TICKER_LINEAGE_LIMIT:
        focus = records[focus_index - 1]
        lines.extend([
            (
                "Ignition token rank: "
                f"#{focus_index}"
            ),
            f"    {format_mint_time(focus['mint_time'])}",
            f"    ${escape(str(focus['symbol']))}",
            (
                "    "
                f"{format_defined_address_link(focus)}"
                " ← ignition"
            ),
            (
                "    "
                f"{format_lineage_sources(focus)}"
                " | "
                f"{format_lineage_confidence(focus)}"
                f"{format_evidence_link(focus)}"
            )
        ])
    elif focus_index is None:
        lines.extend([
            (
                "Ignition token was not found in the resolved "
                "same-ticker set."
            )
        ])

    lines.extend([
        "",
        (
            "Note: oldest discovered, not guaranteed OG. "
            "Manual evidence and cached history are included when available."
        ),
        (
            f"Resolved {len(records)} / {total_found} "
            "discovered same-ticker records."
        )
    ])

    return "\n".join(lines).rstrip()


async def collect_ticker_lineage(
    client,
    ticker
):

    ticker = normalize_ticker(ticker)

    records = {}

    for record in load_persisted_lineage_records(ticker):
        records[record["address"]] = record

    register_lineage_overrides(records, ticker)

    for pair in await client.fetch_search_pairs(ticker):
        register_dex_pair(records, pair, ticker)

    jupiter_tokens, pumpfun_tokens, helius_tokens = await asyncio.gather(
        fetch_jupiter_matches(client.session, ticker),
        fetch_pumpfun_matches(client.session, ticker),
        fetch_helius_matches(client.session, ticker),
        return_exceptions=True
    )

    if isinstance(jupiter_tokens, Exception):
        jupiter_tokens = []

    if isinstance(pumpfun_tokens, Exception):
        pumpfun_tokens = []

    if isinstance(helius_tokens, Exception):
        helius_tokens = []

    jupiter_addresses = []

    for token in jupiter_tokens:
        address = (
            token.get("address")
            or token.get("id")
            or token.get("mint")
        )

        register_record(
            records,
            address,
            token.get("symbol", ticker),
            name=token.get("name"),
            source="jupiter"
        )

        if address:
            jupiter_addresses.append(address)

    if jupiter_addresses:
        pair_map = await client.fetch_token_pairs_batch(
            jupiter_addresses[:TICKER_LINEAGE_MAX_CANDIDATES]
        )

        for pairs in pair_map.values():
            for pair in pairs:
                register_dex_pair(records, pair, ticker)

    for coin in pumpfun_tokens:
        created = coin.get("created_timestamp") or 0

        if created and created < 1_000_000_000_000:
            created *= 1000

        register_record(
            records,
            coin.get("mint"),
            coin.get("symbol", ticker),
            name=coin.get("name"),
            source="pumpfun",
            pair_created_at=created,
            market_cap=safe_float(
                coin.get("usd_market_cap")
            )
        )

    for token in helius_tokens:
        register_record(
            records,
            token.get("address"),
            token.get("symbol") or ticker,
            name=token.get("name"),
            source="helius"
        )

    all_records = sorted(
        records.values(),
        key=lineage_candidate_sort_key
    )

    # Split into tokens that have some time hint vs those with none.
    # Tokens with no hint (hint_time=inf) are sorted last — but they
    # could be old mints with no DEX pair yet known, so never cut them
    # off entirely. Reserve up to 20% of candidate slots for them.
    with_hint = [
        r for r in all_records
        if lineage_candidate_sort_key(r)[1] < float("inf")
    ]
    without_hint = [
        r for r in all_records
        if lineage_candidate_sort_key(r)[1] >= float("inf")
    ]
    no_hint_slots = min(
        len(without_hint),
        max(10, TICKER_LINEAGE_MAX_CANDIDATES // 5)
    )
    dated_slots = TICKER_LINEAGE_MAX_CANDIDATES - no_hint_slots
    candidates = with_hint[:dated_slots] + without_hint[:no_hint_slots]

    semaphore = asyncio.Semaphore(
        TICKER_LINEAGE_MINT_CONCURRENCY
    )

    resolved = await asyncio.gather(
        *[
            resolve_record_mint_age(
                client.session,
                record,
                semaphore
            )
            for record in candidates
        ],
        return_exceptions=True
    )

    ranked = [
        record
        for record in resolved
        if record and not isinstance(record, Exception)
    ]

    ranked.sort(
        key=lambda record: record["mint_time"]
    )

    persist_lineage_records(ticker, ranked)

    return ranked, len(records)


async def resolve_focus_lineage_record(
    client,
    ticker,
    focus_address
):

    if (
        not focus_address
        or is_excluded_contract_address(focus_address)
    ):
        return None

    records = {}

    try:
        pairs = await client.fetch_token_pairs(
            focus_address
        )
    except Exception:
        pairs = []

    for pair in pairs:
        register_dex_pair(
            records,
            pair,
            ticker
        )

    if focus_address not in records and pairs:
        return None

    if focus_address not in records:
        register_record(
            records,
            focus_address,
            ticker,
            source="ignition"
        )

    focus_record = records.get(
        focus_address
    )

    if not focus_record:
        return None

    semaphore = asyncio.Semaphore(1)

    return await resolve_record_mint_age(
        client.session,
        focus_record,
        semaphore
    )


async def get_cached_ticker_lineage(
    client,
    ticker
):

    cached = LINEAGE_CACHE.get(ticker)

    if cached:
        cached_at, ranked, total_found = cached

        if (
            time.time() - cached_at
            < TICKER_LINEAGE_CACHE_TTL_SECONDS
        ):
            return list(ranked), total_found

    ranked, total_found = await collect_ticker_lineage(
        client,
        ticker
    )

    if cached:
        _, previous_ranked, previous_total = cached

        ranked, added = merge_ranked_records(
            previous_ranked,
            ranked
        )

        total_found = max(
            total_found,
            previous_total,
            len(ranked),
            len(previous_ranked) + added
        )

    LINEAGE_CACHE[ticker] = (
        time.time(),
        ranked,
        total_found
    )

    return list(ranked), total_found


async def build_ticker_lineage_section(
    client,
    ticker,
    focus_address=None
):

    if not TICKER_LINEAGE_ENABLED:
        return ""

    ticker = normalize_ticker(ticker)

    if not ticker:
        return ""

    ranked, total_found = await get_cached_ticker_lineage(
        client,
        ticker
    )

    if (
        focus_address
        and find_record_index(ranked, focus_address) is None
    ):
        focus_record = await resolve_focus_lineage_record(
            client,
            ticker,
            focus_address
        )

        if focus_record:
            ranked, was_added = merge_ranked_record(
                ranked,
                focus_record
            )

            if was_added:
                total_found = max(
                    total_found,
                    len(ranked)
                )

            LINEAGE_CACHE[ticker] = (
                time.time(),
                ranked,
                total_found
            )

    return build_lineage_text(
        ticker,
        ranked,
        focus_address,
        total_found
    )
