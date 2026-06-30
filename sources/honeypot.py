"""GoPlus Security honeypot / sell-tax screening for EVM tokens (BSC-first).

BSC's scam density (honeypots, 99% sell tax, blacklist-on-buy, pausable
transfers) makes a real *sellability* check the highest-value chain-specific
safety gate — far more than the `eth_getCode` existence check the scanner uses
for generic EVM tokens. GoPlus exposes contract-level security analysis with a
keyless free tier (an access token only raises rate limits).

The verdict is consumed by `filters.safety.SafetyChecker.check_liquidity_lock`,
which maps an unsafe verdict onto a `locked=False` liquidity-lock result so the
EXISTING alert/entry gates (main.py Block B, live_runner `_entry_safety_block_
reason`) veto it with no extra plumbing.

Posture: block on a CONFIRMED-bad verdict, fail OPEN on unknown/unindexed. New
tokens GoPlus has not indexed yet are rare here because the scanner already
enforces MIN_TOKEN_AGE_HOURS, by which point GoPlus has almost always indexed
the contract.
"""

import time

import aiohttp

from config import (
    BSC_MAX_BUY_TAX,
    BSC_MAX_SELL_TAX,
    GOPLUS_API_BASE_URL,
    GOPLUS_API_KEY,
    GOPLUS_API_TIMEOUT_SECONDS,
    GOPLUS_CACHE_TTL_SECONDS,
)


# GoPlus numeric chain ids (https://docs.gopluslabs.io/reference/supported-chains)
GOPLUS_CHAIN_IDS = {
    "bsc": "56",
    "ethereum": "1",
    "base": "8453",
}


_CACHE = {}


def supported_chain(chain):

    return str(chain or "").lower() in GOPLUS_CHAIN_IDS


def _flag(value):
    # GoPlus returns flags as the strings "0"/"1" (sometimes ints).
    return str(value).strip() == "1"


def _fraction(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class GoPlusHoneypotChecker:

    def __init__(self, session=None):
        self._session = session

    def _headers(self):
        headers = {"accept": "application/json"}

        # A configured key is treated as a pre-obtained GoPlus access token
        # (sent in Authorization). Keyless requests use the free tier.
        if GOPLUS_API_KEY:
            headers["Authorization"] = GOPLUS_API_KEY

        return headers

    async def _get_json(self, url, params):
        timeout = aiohttp.ClientTimeout(
            total=GOPLUS_API_TIMEOUT_SECONDS
        )

        if self._session is not None:
            async with self._session.get(
                url,
                params=params,
                headers=self._headers(),
                timeout=timeout,
            ) as response:
                if response.status != 200:
                    return None
                return await response.json(content_type=None)

        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                params=params,
                headers=self._headers(),
                timeout=timeout,
            ) as response:
                if response.status != 200:
                    return None
                return await response.json(content_type=None)

    async def assess(self, chain, token_address):
        """Return a structured sellability verdict:
          checked: True only when GoPlus returned token data
          safe:    True / False when checked, else None (unknown)
          vetoes:  list of confirmed-bad reasons (empty when safe/unknown)
        """
        chain = str(chain or "").lower()
        address = str(token_address or "").lower()

        if not supported_chain(chain) or not address:
            return self._unknown("unsupported_chain_or_address")

        cached = _CACHE.get((chain, address))

        if cached and (time.time() - cached[0]) < GOPLUS_CACHE_TTL_SECONDS:
            return cached[1]

        url = (
            f"{GOPLUS_API_BASE_URL}/api/v1/token_security/"
            f"{GOPLUS_CHAIN_IDS[chain]}"
        )

        try:
            data = await self._get_json(
                url,
                {"contract_addresses": address},
            )
        except Exception as exc:
            # Transient network/timeout — unknown, do not veto.
            return self._unknown(
                f"goplus_request_failed:{type(exc).__name__}"
            )

        result = self._parse(data, address)
        _CACHE[(chain, address)] = (time.time(), result)
        return result

    def _parse(self, data, address):
        if not isinstance(data, dict) or data.get("code") != 1:
            return self._unknown("goplus_no_data")

        results = data.get("result") or {}
        info = results.get(address) or results.get(address.lower())

        if not isinstance(info, dict) or not info:
            return self._unknown("goplus_token_not_indexed")

        is_honeypot = _flag(info.get("is_honeypot"))
        cannot_sell_all = _flag(info.get("cannot_sell_all"))
        cannot_buy = _flag(info.get("cannot_buy"))
        transfer_pausable = _flag(info.get("transfer_pausable"))
        is_blacklisted = _flag(info.get("is_blacklisted"))
        buy_tax = _fraction(info.get("buy_tax"))
        sell_tax = _fraction(info.get("sell_tax"))

        vetoes = []

        if is_honeypot:
            vetoes.append("honeypot")

        if cannot_sell_all:
            vetoes.append("cannot_sell_all")

        if cannot_buy:
            vetoes.append("cannot_buy")

        if sell_tax is not None and sell_tax > BSC_MAX_SELL_TAX:
            vetoes.append(f"sell_tax_{sell_tax:.2f}")

        if buy_tax is not None and buy_tax > BSC_MAX_BUY_TAX:
            vetoes.append(f"buy_tax_{buy_tax:.2f}")

        # Pausable transfers and blacklists are the two most common BSC rug
        # levers (freeze sells after accumulation); treat as hard vetoes.
        if transfer_pausable:
            vetoes.append("transfer_pausable")

        if is_blacklisted:
            vetoes.append("blacklist")

        return {
            "checked": True,
            "safe": len(vetoes) == 0,
            "is_honeypot": is_honeypot,
            "can_sell": not (is_honeypot or cannot_sell_all),
            "buy_tax": buy_tax,
            "sell_tax": sell_tax,
            "vetoes": vetoes,
            "source": "goplus",
            "reason": "ok" if not vetoes else ",".join(vetoes),
        }

    def _unknown(self, reason):
        return {
            "checked": False,
            "safe": None,
            "is_honeypot": None,
            "can_sell": None,
            "buy_tax": None,
            "sell_tax": None,
            "vetoes": [],
            "source": "goplus",
            "reason": reason,
        }
