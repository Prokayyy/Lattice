import argparse
import asyncio
import json
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import (  # noqa: E402
    ALERT_REPORT_OHLCV_MAX_POOL_ADDRESSES,
    ALERT_REPORT_OHLCV_MAX_PAGES,
    ALERT_REPORT_OHLCV_MIN_POOL_LIQUIDITY_USD,
    ALERT_REPORT_OHLCV_REFRESH_ENABLED,
    BIRDEYE_API_BASE_URL,
    BIRDEYE_API_KEY,
    COINGECKO_API_BASE_URL,
    COINGECKO_API_KEY
)
from storage.sqlite import DATABASE_NAME  # noqa: E402
from trading.live_prices import fetch_live_prices  # noqa: E402


BIRDEYE_OHLCV_URL = (
    f"{BIRDEYE_API_BASE_URL.rstrip('/')}"
    "/defi/v3/ohlcv"
)

COINGECKO_OHLCV_URL = (
    f"{COINGECKO_API_BASE_URL.rstrip('/')}"
    "/onchain/networks/solana/pools/{pool_address}/ohlcv/minute"
)

GECKOTERMINAL_OHLCV_URL = (
    "https://api.geckoterminal.com/api/v2"
    "/networks/{chain_id}/pools/{pool_address}/ohlcv/minute"
)

DEXSCREENER_TOKEN_PAIRS_URL = (
    "https://api.dexscreener.com/token-pairs/v1"
    "/{chain_id}/{token_address}"
)


def coingecko_auth_headers():

    headers = {}

    if not COINGECKO_API_KEY:
        return headers

    if "pro-api.coingecko.com" in COINGECKO_API_BASE_URL:
        headers["x-cg-pro-api-key"] = COINGECKO_API_KEY
    else:
        headers["x-cg-demo-api-key"] = COINGECKO_API_KEY

    return headers


class OhlcvAuthError(Exception):

    pass


def parse_date(value, end_of_day=False):

    if not value:
        return None

    if value.isdigit():
        return float(value)

    text = value.strip()

    if len(text) == 10:
        dt = datetime.fromisoformat(text)

        if end_of_day:
            dt = dt + timedelta(days=1)

        return dt.replace(
            tzinfo=timezone.utc
        ).timestamp()

    dt = datetime.fromisoformat(
        text.replace("Z", "+00:00")
    )

    if not dt.tzinfo:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(
        timezone.utc
    ).timestamp()


def format_time(timestamp):

    if not timestamp:
        return "unknown"

    return datetime.fromtimestamp(
        float(timestamp),
        timezone.utc
    ).strftime("%Y-%m-%d %H:%M UTC")


def local_now():

    return datetime.now().astimezone()


def local_day_window():

    now = local_now()
    start = now.replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0
    )

    return start.timestamp(), now.timestamp()


def local_week_window():

    now = local_now()
    start = (
        now
        - timedelta(days=now.weekday())
    ).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0
    )

    return start.timestamp(), now.timestamp()


def local_month_window():

    now = local_now()
    start = now.replace(
        day=1,
        hour=0,
        minute=0,
        second=0,
        microsecond=0
    )

    return start.timestamp(), now.timestamp()


def safe_float(value, default=0):

    try:
        return float(value or default)
    except (TypeError, ValueError):
        return default


def price(value):

    return f"${safe_float(value):.8f}"


def load_alerts(since=None, until=None, open_only=False):

    clauses = []
    params = []

    if since is not None:
        clauses.append("alert_timestamp >= ?")
        params.append(since)

    if until is not None:
        clauses.append("alert_timestamp <= ?")
        params.append(until)

    if open_only:
        clauses.append("status = 'open'")

    where = ""

    if clauses:
        where = "WHERE " + " AND ".join(clauses)

    with sqlite3.connect(DATABASE_NAME) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute(
            f"""
            SELECT *
            FROM ignition_alerts
            {where}
            ORDER BY alert_timestamp ASC,
                id ASC
            """,
            params
        ).fetchall()

    return [dict(row) for row in rows]


def lookup_pair_address(db, alert):

    pair_address = str(
        alert.get("pair_address")
        or ""
    ).strip()

    if pair_address:
        return pair_address

    token_address = alert.get("token_address")
    alert_timestamp = safe_float(
        alert.get("alert_timestamp"),
        0
    )

    if not token_address or alert_timestamp <= 0:
        return ""

    row = db.execute(
        """
        SELECT pair_address
        FROM signal_snapshots
        WHERE token_address = ?
            AND pair_address IS NOT NULL
            AND pair_address != ''
            AND timestamp >= ?
        ORDER BY ABS(timestamp - ?) ASC
        LIMIT 1
        """,
        (
            token_address,
            alert_timestamp - 300,
            alert_timestamp
        )
    ).fetchone()

    if not row:
        return ""

    return str(
        row["pair_address"]
        if isinstance(row, sqlite3.Row)
        else row[0]
    )


def chain_for_alert(alert):

    return str(
        alert.get("chain_name")
        or alert.get("chain")
        or "solana"
    ).strip().lower() or "solana"


def dex_chain_for_ohlcv(chain):

    normalized = str(chain or "solana").strip().lower()

    if normalized in ("sol", "solana"):
        return "solana"

    return normalized


def fetch_dexscreener_token_pairs(token_address, chain_id):

    if not token_address:
        return []

    url = DEXSCREENER_TOKEN_PAIRS_URL.format(
        chain_id=chain_id,
        token_address=urllib.parse.quote(
            str(token_address),
            safe=""
        )
    )

    try:
        data = fetch_json(
            url,
            timeout=20
        )
    except Exception:
        return []

    if isinstance(data, list):
        pairs = data
    elif isinstance(data, dict):
        pairs = data.get("pairs") or []
    else:
        pairs = []

    requested = str(token_address).lower()
    filtered = []

    for pair in pairs:
        pair_address = str(
            pair.get("pairAddress")
            or ""
        ).strip()

        if not pair_address:
            continue

        base_address = (
            pair.get("baseToken", {})
            .get("address", "")
            .lower()
        )

        if base_address and base_address != requested:
            continue

        if safe_float(pair.get("priceUsd"), 0) <= 0:
            continue

        filtered.append(pair)

    return filtered


def candidate_ohlcv_pool_addresses(db, alert):

    token_address = alert.get("token_address")
    chain_id = dex_chain_for_ohlcv(
        chain_for_alert(alert)
    )
    stored_pair_address = lookup_pair_address(
        db,
        alert
    )
    addresses = []
    seen = set()

    def add(address):
        address = str(address or "").strip()

        if not address or address in seen:
            return

        seen.add(address)
        addresses.append(address)

    add(stored_pair_address)

    for pair in fetch_dexscreener_token_pairs(
        token_address,
        chain_id
    ):
        liquidity = safe_float(
            (pair.get("liquidity") or {}).get("usd"),
            0
        )

        if liquidity >= ALERT_REPORT_OHLCV_MIN_POOL_LIQUIDITY_USD:
            add(pair.get("pairAddress"))

        if len(addresses) >= ALERT_REPORT_OHLCV_MAX_POOL_ADDRESSES:
            break

    return addresses[:max(
        ALERT_REPORT_OHLCV_MAX_POOL_ADDRESSES,
        1
    )]


def fetch_json(
    url,
    params=None,
    timeout=20,
    headers=None,
    auth_error_message=None
):

    query = ""

    if params:
        query = (
            "?"
            + urllib.parse.urlencode(params)
        )

    request_headers = {
        "accept": "application/json",
        "user-agent": "organic-revival-scanner/1.0"
    }

    if headers:
        request_headers.update(headers)

    request = urllib.request.Request(
        url + query,
        headers=request_headers
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=timeout
        ) as response:
            return json.loads(
                response.read().decode("utf-8")
            )
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise OhlcvAuthError(
                auth_error_message
                or "OHLCV refresh API key is missing or invalid."
            ) from e

        raise


def first_number(mapping, keys, default=0):

    for key in keys:
        if key in mapping:
            value = safe_float(
                mapping.get(key),
                None
            )

            if value is not None:
                return value

    return default


def normalize_ohlcv_item(item, source):

    if isinstance(item, dict):
        timestamp = first_number(
            item,
            (
                "unixTime",
                "unix_time",
                "timestamp",
                "time",
                "t"
            )
        )
        high = first_number(
            item,
            ("h", "high")
        )
        low = first_number(
            item,
            ("l", "low")
        )
        close = first_number(
            item,
            ("c", "close")
        )
        open_price = first_number(
            item,
            ("o", "open"),
            close
        )
        volume = first_number(
            item,
            (
                "v",
                "volume",
                "volume_usd",
                "volumeUsd",
                "volumeUSD"
            )
        )
    elif isinstance(item, (list, tuple)) and len(item) >= 6:
        timestamp = safe_float(item[0], 0)
        open_price = safe_float(item[1], 0)
        high = safe_float(item[2], 0)
        low = safe_float(item[3], 0)
        close = safe_float(item[4], 0)
        volume = safe_float(item[5], 0)
    else:
        return None

    if timestamp <= 0:
        return None

    return {
        "timestamp": timestamp,
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "source": source
    }


def parse_birdeye_ohlcv_list(data):

    payload = data.get("data", {}) if isinstance(data, dict) else {}

    if isinstance(payload, dict):
        raw_list = (
            payload.get("items")
            or payload.get("ohlcv")
            or payload.get("candles")
            or []
        )
    elif isinstance(payload, list):
        raw_list = payload
    else:
        raw_list = []

    candles = []

    for item in raw_list:
        candle = normalize_ohlcv_item(
            item,
            "birdeye_ohlcv"
        )

        if candle:
            candles.append(candle)

    candles.sort(
        key=lambda candle: candle["timestamp"]
    )

    return candles


def parse_ohlcv_list(data):

    attributes = (
        data.get("data", {})
        .get("attributes", {})
        if isinstance(data, dict)
        else {}
    )
    raw_list = (
        attributes.get("ohlcv_list")
        or []
    )
    candles = []

    for item in raw_list:
        candle = normalize_ohlcv_item(
            item,
            "coingecko_ohlcv"
        )

        if candle:
            candles.append(candle)

    candles.sort(
        key=lambda candle: candle["timestamp"]
    )

    return candles


def fetch_birdeye_ohlcv_window(
    token_address,
    since,
    until,
    max_pages
):

    if not BIRDEYE_API_KEY or not token_address:
        return []

    candles = []
    start = int(since)
    end = int(until)
    max_candle_span_seconds = 5000 * 60

    for _ in range(max_pages):
        chunk_end = min(
            start + max_candle_span_seconds,
            end
        )
        params = {
            "address": token_address,
            "type": "1m",
            "currency": "usd",
            "time_from": start,
            "time_to": chunk_end
        }
        data = fetch_json(
            BIRDEYE_OHLCV_URL,
            params=params,
            headers={
                "X-API-KEY": BIRDEYE_API_KEY,
                "x-chain": "solana"
            },
            auth_error_message=(
                "Birdeye OHLCV refresh requires BIRDEYE_API_KEY."
            )
        )
        candles.extend(
            parse_birdeye_ohlcv_list(data)
        )

        if chunk_end >= end:
            break

        start = chunk_end + 60

    deduped = {
        candle["timestamp"]: candle
        for candle in candles
    }

    return [
        candle
        for candle in sorted(
            deduped.values(),
            key=lambda value: value["timestamp"]
        )
        if candle["timestamp"] <= until
        and candle["timestamp"] + 60 >= since
    ]


def fetch_coingecko_ohlcv_window(
    pool_address,
    token_address,
    since,
    until,
    max_pages,
    chain_id="solana"
):

    if not pool_address:
        return []

    chain_id = dex_chain_for_ohlcv(chain_id)
    use_coingecko_api = (
        bool(COINGECKO_API_KEY)
        and chain_id == "solana"
    )
    headers = (
        coingecko_auth_headers()
        if use_coingecko_api
        else {}
    )

    before_timestamp = int(until) + 60
    all_candles = []

    for _ in range(max_pages):
        params = {
            "aggregate": 1,
            "before_timestamp": before_timestamp,
            "limit": 1000,
            "currency": "usd"
        }

        if use_coingecko_api and token_address:
            params["token"] = token_address

        if use_coingecko_api:
            url = COINGECKO_OHLCV_URL.format(
                pool_address=pool_address
            )
            ohlcv_source = "coingecko_ohlcv"
        else:
            url = GECKOTERMINAL_OHLCV_URL.format(
                chain_id=dex_chain_for_ohlcv(chain_id),
                pool_address=pool_address
            )
            ohlcv_source = "geckoterminal_ohlcv"

        try:
            data = fetch_json(
                url,
                params=params,
                headers=headers,
                auth_error_message=(
                    "CoinGecko OHLCV refresh requires COINGECKO_API_KEY."
                )
            )
        except OhlcvAuthError:
            if not use_coingecko_api:
                raise

            use_coingecko_api = False
            headers = {}
            continue

        candles = parse_ohlcv_list(data)

        if not candles:
            break

        for candle in candles:
            candle["pool_address"] = pool_address
            candle["source"] = ohlcv_source

        all_candles.extend(candles)
        oldest = min(
            candle["timestamp"]
            for candle in candles
        )

        if oldest <= since - 60:
            break

        next_before = int(oldest) - 1

        if next_before >= before_timestamp:
            break

        before_timestamp = next_before

    deduped = {
        candle["timestamp"]: candle
        for candle in all_candles
    }

    return [
        candle
        for candle in sorted(
            deduped.values(),
            key=lambda value: value["timestamp"]
        )
        if candle["timestamp"] <= until
        and candle["timestamp"] + 60 >= since
    ]


def fetch_ohlcv_window(
    pool_address,
    token_address,
    since,
    until,
    max_pages,
    chain_id="solana"
):

    until = until or time.time()

    if BIRDEYE_API_KEY and token_address and chain_id == "solana":
        candles = fetch_birdeye_ohlcv_window(
            token_address,
            since,
            until,
            max_pages
        )

        if candles:
            return candles

    return fetch_coingecko_ohlcv_window(
        pool_address,
        token_address,
        since,
        until,
        max_pages,
        chain_id=chain_id
    )


def fetch_ohlcv_windows(
    pool_addresses,
    token_address,
    since,
    until,
    max_pages,
    chain_id="solana"
):

    if BIRDEYE_API_KEY and token_address and chain_id == "solana":
        candles = fetch_birdeye_ohlcv_window(
            token_address,
            since,
            until or time.time(),
            max_pages
        )

        if candles:
            for candle in candles:
                candle["pool_rank"] = 0
            return candles

    candles = []

    for index, pool_address in enumerate(pool_addresses):
        pool_candles = fetch_coingecko_ohlcv_window(
            pool_address,
            token_address,
            since,
            until,
            max_pages,
            chain_id=chain_id
        )

        for candle in pool_candles:
            candle["pool_address"] = (
                candle.get("pool_address")
                or pool_address
            )
            candle["pool_rank"] = index

        candles.extend(pool_candles)

        if index < len(pool_addresses) - 1:
            time.sleep(0.20)

    return candles


def apply_ohlcv_to_alert(alert, candles):

    alert_price = safe_float(
        alert.get("alert_price"),
        0
    )

    if alert_price <= 0 or not candles:
        return False

    peak_candle = max(
        candles,
        key=lambda candle: candle["high"]
    )
    min_candle = min(
        candles,
        key=lambda candle: candle["low"]
    )
    last_candle = max(
        candles,
        key=lambda candle: (
            candle["timestamp"],
            -safe_float(candle.get("pool_rank"), 999)
        )
    )

    peak_price = safe_float(
        peak_candle.get("high"),
        0
    )
    min_price = safe_float(
        min_candle.get("low"),
        0
    )
    last_price = safe_float(
        last_candle.get("close"),
        0
    )
    last_timestamp = safe_float(
        last_candle.get("timestamp"),
        0
    )

    changed = False

    if peak_price > safe_float(alert.get("max_price"), 0):
        alert["max_price"] = peak_price
        alert["max_multiple"] = peak_price / alert_price
        alert["peak_source"] = peak_candle.get(
            "source",
            "ohlcv"
        )
        alert["peak_pair_address"] = peak_candle.get(
            "pool_address",
            ""
        )
        changed = True

    existing_min = safe_float(
        alert.get("min_price"),
        alert_price
    )

    if min_price > 0 and min_price < existing_min:
        alert["min_price"] = min_price
        alert["min_multiple"] = min_price / alert_price
        changed = True

    if (
        last_price > 0
        and last_timestamp
        >= safe_float(alert.get("last_timestamp"), 0)
    ):
        alert["last_price"] = last_price
        alert["last_timestamp"] = last_timestamp
        changed = True

    return changed


def save_ohlcv_alert_update(db, alert):

    if not alert.get("id"):
        return

    db.execute(
        """
        UPDATE ignition_alerts
        SET
            last_price = ?,
            last_timestamp = ?,
            max_price = ?,
            max_multiple = ?,
            min_price = ?,
            min_multiple = ?
        WHERE id = ?
        """,
        (
            alert.get("last_price"),
            alert.get("last_timestamp"),
            alert.get("max_price"),
            alert.get("max_multiple"),
            alert.get("min_price"),
            alert.get("min_multiple"),
            alert.get("id")
        )
    )


async def refresh_alerts_with_live_prices(alerts):

    addresses = [
        alert.get("token_address")
        for alert in alerts
        if alert.get("token_address")
    ]
    live_prices, stats = await fetch_live_prices(
        addresses
    )

    for alert in alerts:
        token_address = alert.get("token_address")

        if not token_address:
            continue

        live_price = live_prices.get(token_address)

        if not live_price:
            alert["live_refresh_error"] = "no_live_price"
            continue

        last_price = live_price["price_usd"]
        alert_price = safe_float(
            alert.get("alert_price")
        )

        alert["live_refreshed"] = True
        alert["live_previous_last_price"] = safe_float(
            alert.get("last_price")
        )
        alert["last_price"] = last_price
        alert["last_timestamp"] = stats.get("as_of")
        alert["last_liquidity"] = live_price.get(
            "liquidity_usd",
            0
        )
        alert["last_fdv"] = live_price.get(
            "fdv_usd",
            0
        )
        alert["live_pair_address"] = live_price.get(
            "pair_address",
            ""
        )
        alert["live_volume_1h_usd"] = live_price.get(
            "volume_1h_usd",
            0
        )
        alert["live_price_source"] = live_price.get(
            "source",
            "dexscreener"
        )

        if alert_price <= 0:
            continue

        current_multiple = last_price / alert_price
        max_price = safe_float(
            alert.get("max_price")
        )

        if last_price > max_price:
            alert["max_price"] = last_price
            alert["max_multiple"] = current_multiple
            alert["peak_source"] = "live_price"

        min_price = safe_float(
            alert.get("min_price"),
            alert_price
        )

        if last_price > 0 and last_price < min_price:
            alert["min_price"] = last_price
            alert["min_multiple"] = current_multiple

    return stats


def refresh_alerts_with_ohlcv(
    alerts,
    until=None,
    save=False,
    max_pages=ALERT_REPORT_OHLCV_MAX_PAGES
):

    stats = {
        "attempted": 0,
        "updated": 0,
        "missing_pair_address": 0,
        "failed_or_empty": 0,
        "auth_required": False,
        "resolved_extra_pools": 0
    }

    with sqlite3.connect(DATABASE_NAME) as db:
        db.row_factory = sqlite3.Row

        for alert in alerts:
            alert_timestamp = safe_float(
                alert.get("alert_timestamp"),
                0
            )

            if alert_timestamp <= 0:
                continue

            pair_addresses = candidate_ohlcv_pool_addresses(
                db,
                alert
            )
            token_address = alert.get("token_address")
            chain_id = dex_chain_for_ohlcv(
                chain_for_alert(alert)
            )

            if not pair_addresses:
                stats["missing_pair_address"] += 1
                continue

            if len(pair_addresses) > 1:
                stats["resolved_extra_pools"] += (
                    len(pair_addresses) - 1
                )

            stats["attempted"] += 1
            try:
                candles = fetch_ohlcv_windows(
                    pair_addresses,
                    token_address,
                    alert_timestamp,
                    until,
                    max_pages=max_pages,
                    chain_id=chain_id
                )
            except OhlcvAuthError:
                stats["auth_required"] = True
                break
            except Exception:
                stats["failed_or_empty"] += 1
                continue

            if not candles:
                stats["failed_or_empty"] += 1
                continue

            if apply_ohlcv_to_alert(alert, candles):
                stats["updated"] += 1

                if save:
                    save_ohlcv_alert_update(
                        db,
                        alert
                    )

            time.sleep(0.20)

    return stats


def multiple(alert, field):

    alert_price = safe_float(alert.get("alert_price"), 0)

    if alert_price <= 0:
        return 0

    return safe_float(alert.get(field), 0) / alert_price


def summarize(alerts):

    valid = [
        alert
        for alert in alerts
        if safe_float(alert.get("alert_price"), 0) > 0
    ]

    current_multiples = [
        multiple(alert, "last_price")
        for alert in valid
    ]
    peak_multiples = [
        safe_float(alert.get("max_multiple"), 0)
        for alert in valid
    ]

    current_positive = [
        alert
        for alert in valid
        if multiple(alert, "last_price") > 1
    ]

    winners = [
        alert
        for alert in valid
        if safe_float(alert.get("max_multiple"), 0) >= 2
    ]

    return {
        "alerts": len(valid),
        "open_alerts": sum(
            1
            for alert in valid
            if alert.get("status") == "open"
        ),
        "winners": len(winners),
        "current_positive": len(current_positive),
        "win_rate": (
            len(winners) / len(valid)
            if valid
            else 0
        ),
        "current_multiple_avg": (
            sum(current_multiples) / len(current_multiples)
            if current_multiples
            else 0
        ),
        "peak_multiple_avg": (
            sum(peak_multiples) / len(peak_multiples)
            if peak_multiples
            else 0
        ),
        "best_current_multiple": max(current_multiples) if current_multiples else 0,
        "best_peak_multiple": max(peak_multiples) if peak_multiples else 0,
        "worst_current_multiple": min(current_multiples) if current_multiples else 0,
        "hit_2x": sum(1 for value in peak_multiples if value >= 2),
        "hit_4x": sum(1 for value in peak_multiples if value >= 4),
    }


def print_report(
    summary,
    alerts,
    since,
    until,
    open_only,
    limit=10,
    ohlcv_refresh=None,
    live_refresh=None
):

    window = "all time"

    if since or until:
        window = (
            f"{format_time(since) if since else 'beginning'}"
            " to "
            f"{format_time(until) if until else 'now'}"
        )

    scope = "open alerts only" if open_only else "all alerts"

    print(f"Alert performance: {window} ({scope})")
    print("")
    print("Calls")
    print(f"- Calls sent: {summary['alerts']}")
    print(f"- Still open: {summary['open_alerts']}")
    print(f"- Positive now: {summary['current_positive']}")
    print(f"- 2x+ at peak: {summary['winners']}")
    print(f"- Win rate: {summary['win_rate']:.1%}")
    print(
        f"- Average now / peak: "
        f"{summary['current_multiple_avg']:.2f}x / "
        f"{summary['peak_multiple_avg']:.2f}x"
    )
    print(
        f"- 2x hits / 4x hits: {summary['hit_2x']}/{summary['hit_4x']}"
    )

    if live_refresh:
        print(
            "- Live prices: "
            f"{live_refresh.get('refreshed', 0)}/"
            f"{live_refresh.get('attempted', 0)} "
            "tokens fetched for display only"
        )

        if live_refresh.get("error"):
            print(
                "- Live price error: "
                f"{live_refresh['error']}"
            )

    if ohlcv_refresh:
        print(
            "- OHLCV refresh: "
            f"{ohlcv_refresh.get('updated', 0)} updated / "
            f"{ohlcv_refresh.get('attempted', 0)} attempted"
        )
        print(
            "- OHLCV pools: "
            f"{ohlcv_refresh.get('resolved_extra_pools', 0)} "
            "extra active pools checked"
        )

        if ohlcv_refresh.get("auth_required"):
            print(
                "- OHLCV refresh needs BIRDEYE_API_KEY "
                "or COINGECKO_API_KEY for the configured paid endpoint"
            )

    if alerts:
        print("")
        print("Top runners")
        ranked = sorted(
            alerts,
            key=lambda alert: (
                safe_float(alert.get("max_multiple"), 0),
                multiple(alert, "last_price")
            ),
            reverse=True
        )
        rows = ranked if limit is None else ranked[:limit]

        for alert in rows:
            current_multiple = multiple(alert, "last_price")
            peak_multiple = safe_float(alert.get("max_multiple"), 0)
            peak_source = ""
            live_marker = (
                " live"
                if alert.get("live_refreshed")
                else ""
            )

            if alert.get("peak_source") == "birdeye_ohlcv":
                peak_source = " | birdeye"
            elif str(alert.get("peak_source", "")).endswith("_ohlcv"):
                peak_source = " | ohlcv"
            elif alert.get("peak_source") == "live_price":
                peak_source = " | live peak"

            print(
                f"- "
                f"${alert.get('symbol', 'UNKNOWN')} "
                f"peak {peak_multiple:.2f}x | "
                f"now {current_multiple:.2f}x{live_marker} "
                f"({price(alert.get('last_price'))}) | "
                f"{alert.get('status', 'open')} | "
                f"{alert.get('alert_route', 'none')} | "
                f"at {format_time(alert.get('alert_timestamp'))}"
                f"{peak_source}"
            )


def main():

    parser = argparse.ArgumentParser(
        description="Query ignition alert performance."
    )
    parser.add_argument(
        "--days",
        type=float,
        help="Look back this many days from now."
    )
    parser.add_argument(
        "--today",
        action="store_true",
        help="Show the current local day."
    )
    parser.add_argument(
        "--week",
        action="store_true",
        help="Show the current local week."
    )
    parser.add_argument(
        "--month",
        action="store_true",
        help="Show the current local month."
    )
    parser.add_argument(
        "--since",
        help="Start date/time, e.g. 2026-05-10 or ISO timestamp."
    )
    parser.add_argument(
        "--until",
        help="End date/time, e.g. 2026-05-11 or ISO timestamp."
    )
    parser.add_argument(
        "--open",
        action="store_true",
        help="Show only alerts that are still open."
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON."
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Number of ranked calls to print."
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Print every ranked call in the selected window."
    )
    parser.add_argument(
        "--refresh-ohlcv",
        action="store_true",
        help="Refresh alert peaks with Birdeye/CoinGecko 1m OHLCV before reporting."
    )
    parser.add_argument(
        "--refresh-live",
        action="store_true",
        help=(
            "Fetch current DexScreener prices for selected alerts before "
            "reporting. This does not update scanner.db."
        )
    )
    parser.add_argument(
        "--no-refresh-ohlcv",
        action="store_true",
        help="Disable config-enabled OHLCV refresh for this run."
    )
    parser.add_argument(
        "--save-ohlcv",
        action="store_true",
        help="Persist OHLCV-refreshed peak/current prices back to scanner.db."
    )

    args = parser.parse_args()

    now = datetime.now(
        timezone.utc
    ).timestamp()

    since = parse_date(args.since)
    until = parse_date(
        args.until,
        end_of_day=True
    )

    selected_periods = sum(
        1
        for enabled in (
            args.today,
            args.week,
            args.month
        )
        if enabled
    )

    if selected_periods > 1:
        parser.error(
            "Choose only one of --today, --week, or --month."
        )

    if args.days is not None:
        since = now - args.days * 86400
        until = None

    if args.today:
        since, until = local_day_window()

    if args.week:
        since, until = local_week_window()

    if args.month:
        since, until = local_month_window()

    alerts = load_alerts(
        since=since,
        until=until,
        open_only=args.open
    )

    ohlcv_refresh = None
    refresh_ohlcv = (
        args.refresh_ohlcv
        or ALERT_REPORT_OHLCV_REFRESH_ENABLED
    )

    if args.no_refresh_ohlcv:
        refresh_ohlcv = False

    if refresh_ohlcv and alerts:
        ohlcv_refresh = refresh_alerts_with_ohlcv(
            alerts,
            until=until or now,
            save=args.save_ohlcv
        )

    live_refresh = None

    if args.refresh_live and alerts:
        live_refresh = asyncio.run(
            refresh_alerts_with_live_prices(alerts)
        )

    summary = summarize(alerts)

    result = {
        "window": {
            "since": since,
            "until": until
        },
        "summary": summary,
        "alerts": alerts,
        "ohlcv_refresh": ohlcv_refresh,
        "live_refresh": live_refresh
    }

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True, default=str))
        return

    print_report(
        summary,
        alerts,
        since,
        until,
        args.open,
        limit=None if args.all else args.limit,
        ohlcv_refresh=ohlcv_refresh,
        live_refresh=live_refresh
    )


if __name__ == "__main__":

    main()
