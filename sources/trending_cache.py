import re
import time

import aiohttp


TRENDING_ENDPOINTS = [
    "https://lite-api.jup.ag/tokens/v2/toptrending/1h?limit=100",
    "https://lite-api.jup.ag/tokens/v2/toporganicscore/1h?limit=100",
]

_TRENDING_CACHE = {}
_LAST_REFRESH = 0

_NAME_STOP_WORDS = frozenset({
    'THE', 'AND', 'FOR', 'WITH', 'TOKEN', 'COIN',
    'INU', 'THIS', 'THAT', 'FROM'
})


def _normalize(s):
    return re.sub(r'[^A-Z0-9]', '', str(s or '').upper())


def _name_words(s):
    return {
        w.upper()
        for w in re.findall(r'[A-Za-z0-9]+', str(s or ''))
        if len(w) >= 4
        and w.upper() not in _NAME_STOP_WORDS
    }


def _levenshtein(a, b):
    if len(a) > len(b):
        a, b = b, a
    row = list(range(len(a) + 1))
    for c in b:
        prev, row[0] = row[0], row[0] + 1
        for j, d in enumerate(a, 1):
            prev, row[j] = row[j], min(
                row[j] + 1,
                row[j - 1] + 1,
                prev + (c != d)
            )
    return row[-1]


def _parse_token(item):

    if isinstance(item, str):
        return {
            "address": item,
            "symbol": "",
            "name": ""
        }

    address = (
        item.get("id")
        or item.get("address")
        or item.get("mint")
        or ""
    )
    return {
        "address": str(address),
        "symbol": str(item.get("symbol") or ""),
        "name": str(item.get("name") or "")
    }


async def refresh_trending_cache():

    global _TRENDING_CACHE, _LAST_REFRESH

    timeout = aiohttp.ClientTimeout(total=20)
    seen = {}

    async with aiohttp.ClientSession(timeout=timeout) as session:

        for url in TRENDING_ENDPOINTS:

            try:
                async with session.get(url) as response:

                    if response.status != 200:
                        print(
                            f"Trending cache: "
                            f"{response.status} from {url}"
                        )
                        continue

                    data = await response.json(content_type=None)

            except Exception as e:
                print(
                    f"Trending cache fetch error "
                    f"({url}): {e}"
                )
                continue

            items = data
            if isinstance(data, dict):
                items = (
                    data.get("tokens")
                    or data.get("data")
                    or []
                )

            if not isinstance(items, list):
                continue

            for item in items:
                token = _parse_token(item)
                if token["address"]:
                    seen[token["address"]] = token

    if seen:
        _TRENDING_CACHE = seen
        _LAST_REFRESH = time.time()
        print(
            f"Trending cache refreshed: "
            f"{len(seen)} tokens"
        )


def cache_loaded():
    return bool(_TRENDING_CACHE)


def find_trending_match(
    candidate_symbol,
    candidate_name="",
    candidate_address=""
):

    if not _TRENDING_CACHE:
        return None

    c_sym = _normalize(candidate_symbol)
    c_name = _normalize(candidate_name or "")

    if not c_sym or len(c_sym) < 3:
        return None

    for token in _TRENDING_CACHE.values():

        addr = token.get("address", "")

        if candidate_address and addr == candidate_address:
            continue

        t_sym = _normalize(token.get("symbol", ""))
        t_name = _normalize(token.get("name", ""))

        if not t_sym or len(t_sym) < 3:
            continue

        # Exact ticker match
        if c_sym == t_sym:
            return token

        # One ticker contains the other (min 4 chars)
        if len(c_sym) >= 4 and len(t_sym) >= 4:
            if c_sym in t_sym or t_sym in c_sym:
                return token

        # Name cross-contains ticker (min 4 chars)
        if len(t_sym) >= 4 and c_name and t_sym in c_name:
            return token
        if len(c_sym) >= 4 and t_name and c_sym in t_name:
            return token

        # Levenshtein distance <= 1 for tickers of similar length
        if (
            len(c_sym) >= 4
            and len(t_sym) >= 4
            and abs(len(c_sym) - len(t_sym)) <= 1
        ):
            if _levenshtein(c_sym, t_sym) <= 1:
                return token

        # Exact normalized name match
        if len(c_name) >= 4 and c_name == t_name:
            return token

        # One normalized name contains the other (min 5 chars)
        if len(c_name) >= 5 and len(t_name) >= 5:
            if c_name in t_name or t_name in c_name:
                return token

        # Shared significant name words
        if c_name and t_name:
            c_words = _name_words(candidate_name)
            t_words = _name_words(token.get("name", ""))
            if c_words and t_words and c_words & t_words:
                return token

    return None
