import asyncio
import time

import aiohttp

from config import (
    ALCHEMY_RPC_URLS,
    BSC_HONEYPOT_CHECK_ENABLED,
    COINGECKO_API_BASE_URL,
    COINGECKO_API_KEY,
    LIQUIDITY_LOCK_MIN_PERCENT,
    MOBULA_API_BASE_URL,
    MOBULA_API_KEY,
    MOBULA_API_TIMEOUT_SECONDS,
    MOBULA_CACHE_TTL_SECONDS,
    MOBULA_MIN_BURNED_OR_LOCKED_PERCENT,
    MOBULA_SAFETY_CHAINS,
    RUGCHECK_API_KEY,
    RUGCHECK_API_BASE_URL,
    RUGCHECK_API_TIMEOUT_SECONDS,
    RUGCHECK_CACHE_TTL_SECONDS,
    REQUIRE_LIQUIDITY_LOCK
)

from sources.honeypot import GoPlusHoneypotChecker


class SafetyChecker:

    def __init__(self):
        self._liquidity_lock_cache = {}
        self._mobula_cache = {}
        self._honeypot = None

    async def check_token(self, chain, token_address):

        if chain == "solana":
            return await self.check_solana(
                token_address
            )

        return await self.check_evm(
            chain,
            token_address
        )

    async def check_evm(self, chain, token_address):

        rpc = ALCHEMY_RPC_URLS.get(chain)

        if not rpc:
            return False

        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "eth_getCode",
            "params": [token_address, "latest"]
        }

        try:

            async with aiohttp.ClientSession() as session:

                async with session.post(
                    rpc,
                    json=payload
                ) as response:

                    data = await response.json()

                    code = data.get("result")

                    if code and code != "0x":
                        return True

        except Exception as e:
            print(f"EVM safety error: {e}")

        return False

    async def check_solana(self, token_address):

        rpc = ALCHEMY_RPC_URLS.get("solana")

        if not rpc:
            return True

        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getAccountInfo",
            "params": [
                token_address,
                {
                    "encoding": "jsonParsed"
                }
            ]
        }

        last_error = None

        for attempt in range(3):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        rpc,
                        json=payload,
                        timeout=aiohttp.ClientTimeout(total=8)
                    ) as response:
                        data = await response.json(content_type=None)

                # RPC answered: True if the mint account exists, False only
                # if it genuinely does not (a real negative).
                value = (data.get("result") or {}).get("value")
                return bool(value)

            except Exception as e:
                last_error = e
                await asyncio.sleep(0.5 * (attempt + 1))

        # RPC failed after retries. This check only verifies the mint account
        # exists (always true for DEX-listed tokens), so an RPC hiccup is NOT
        # evidence the token is unsafe. Fail open so transient errors don't
        # silently drop legitimate candidates (the old behaviour returned
        # False here, dropping the token at the `if not safe: return` gate).
        print(
            "Solana safety RPC unavailable "
            f"({type(last_error).__name__}: {last_error}) — "
            f"allowing {token_address[:8]}"
        )
        return True

    def _cache_liquidity_lock(
        self,
        chain,
        token_address,
        result,
        pair_address=""
    ):

        self._liquidity_lock_cache[
            (chain, token_address, pair_address or "")
        ] = {
            "value": result,
            "cached_at": time.time()
        }

    def _get_cached_liquidity_lock(
        self,
        chain,
        token_address,
        pair_address=""
    ):

        entry = self._liquidity_lock_cache.get(
            (chain, token_address, pair_address or "")
        )

        if not entry:
            return None

        if (
            time.time()
            - entry.get("cached_at", 0)
            > RUGCHECK_CACHE_TTL_SECONDS
        ):
            self._liquidity_lock_cache.pop(
                (chain, token_address, pair_address or ""),
                None
            )
            return None

        return entry.get("value")

    def _cache_mobula_result(
        self,
        chain,
        token_address,
        result
    ):

        self._mobula_cache[
            (chain, token_address)
        ] = {
            "value": result,
            "cached_at": time.time()
        }

    def _get_cached_mobula_result(
        self,
        chain,
        token_address
    ):

        entry = self._mobula_cache.get(
            (chain, token_address)
        )

        if not entry:
            return None

        if (
            time.time()
            - entry.get("cached_at", 0)
            > MOBULA_CACHE_TTL_SECONDS
        ):
            self._mobula_cache.pop(
                (chain, token_address),
                None
            )
            return None

        return entry.get("value")

    def _normalize_lock_result(
        self,
        *,
        checked,
        required,
        locked,
        locked_percent=None,
        source="unknown",
        reason="unknown"
    ):

        return {
            "checked": bool(checked),
            "required": bool(required),
            "locked": bool(locked),
            "locked_percent": locked_percent,
            "source": source,
            "reason": reason
        }

    def _normalize_mobula_chain(
        self,
        chain
    ):

        aliases = {
            "sol": "solana"
        }

        return aliases.get(
            str(chain or "").lower(),
            str(chain or "").lower()
        )

    def _normalize_coingecko_network(
        self,
        chain
    ):

        aliases = {
            "sol": "solana",
            "solana": "solana"
        }

        return aliases.get(
            str(chain or "").lower(),
            str(chain or "").lower()
        )

    def _coingecko_onchain_headers(self):

        headers = {
            "accept": "application/json"
        }

        if not COINGECKO_API_KEY:
            return headers

        if "pro-api.coingecko.com" in COINGECKO_API_BASE_URL:
            headers["x-cg-pro-api-key"] = COINGECKO_API_KEY
        else:
            headers["x-cg-demo-api-key"] = COINGECKO_API_KEY

        return headers

    def _coingecko_onchain_base_url(self):

        return (
            COINGECKO_API_BASE_URL.rstrip("/")
            + "/onchain"
        )

    def _coerce_percent(self, value):

        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _extract_liquidity_lock_result(self, payload):

        candidates = []

        if isinstance(payload, dict):
            candidates.append(("root", payload))

            for key in (
                "data",
                "report",
                "summary",
                "result",
                "token"
            ):
                nested = payload.get(key)
                if isinstance(nested, dict):
                    candidates.append((key, nested))

        for source, candidate in candidates:

            nested_items = []

            for key in (
                "lockers",
                "locks",
                "lpLocks",
                "lp_locks"
            ):
                items = candidate.get(key)
                if isinstance(items, list):
                    nested_items.extend(
                        item
                        for item in items
                        if isinstance(item, dict)
                    )

            for item in nested_items:
                locked_value = item.get("locked")
                locked_percent = self._coerce_percent(
                    item.get("lockedPct")
                    or item.get("locked_percent")
                    or item.get("percentLocked")
                    or item.get("percent_locked")
                )

                if isinstance(locked_value, bool) and locked_value:
                    return self._normalize_lock_result(
                        checked=True,
                        required=True,
                        locked=True,
                        locked_percent=locked_percent,
                        source=source,
                        reason="nested_lock_entry"
                    )

                if (
                    locked_percent is not None
                    and locked_percent >= LIQUIDITY_LOCK_MIN_PERCENT
                ):
                    return self._normalize_lock_result(
                        checked=True,
                        required=True,
                        locked=True,
                        locked_percent=locked_percent,
                        source=source,
                        reason="nested_lock_pct"
                    )

            for lock_key in (
                "lpLocked",
                "lp_locked",
                "liquidityLocked",
                "liquidity_locked",
                "locked",
                "isLocked",
                "is_locked"
            ):
                value = candidate.get(lock_key)

                if isinstance(value, bool):
                    locked_percent = None
                    for pct_key in (
                        "lpLockedPct",
                        "lp_locked_pct",
                        "liquidityLockedPct",
                        "liquidity_locked_pct",
                        "lockedPct",
                        "locked_pct",
                        "lockedPercent",
                        "locked_percent",
                        "percentLocked",
                        "percent_locked"
                    ):
                        locked_percent = self._coerce_percent(
                            candidate.get(pct_key)
                        )
                        if locked_percent is not None:
                            break

                    return self._normalize_lock_result(
                        checked=True,
                        required=True,
                        locked=value,
                        locked_percent=locked_percent,
                        source=source,
                        reason=lock_key
                    )

                if isinstance(value, dict):
                    nested_locked = value.get("locked")
                    locked_percent = self._coerce_percent(
                        value.get("lockedPct")
                        or value.get("locked_pct")
                        or value.get("percentLocked")
                        or value.get("percent_locked")
                    )

                    if nested_locked is True:
                        return self._normalize_lock_result(
                            checked=True,
                            required=True,
                            locked=True,
                            locked_percent=locked_percent,
                            source=source,
                            reason=lock_key
                        )

                    if (
                        locked_percent is not None
                        and locked_percent >= LIQUIDITY_LOCK_MIN_PERCENT
                    ):
                        return self._normalize_lock_result(
                            checked=True,
                            required=True,
                            locked=True,
                            locked_percent=locked_percent,
                            source=source,
                            reason=lock_key
                        )

        return self._normalize_lock_result(
            checked=False,
            required=True,
            locked=False,
            locked_percent=None,
            source="unavailable",
            reason="lock_status_unavailable"
        )

    def _extract_mobula_lock_result(
        self,
        payload
    ):

        data = payload

        if isinstance(payload, dict):
            for key in (
                "data",
                "result",
                "token"
            ):
                if isinstance(payload.get(key), dict):
                    data = payload[key]
                    break

        if not isinstance(data, dict):
            return self._normalize_lock_result(
                checked=False,
                required=True,
                locked=False,
                locked_percent=None,
                source="mobula",
                reason="invalid_response"
            )

        pools = data.get("liquidityAnalysis") or []
        pool_count = 0
        burned_total = 0
        locked_total = 0
        unlocked_max = 0

        for pool in pools:
            if not isinstance(pool, dict):
                continue

            burned = self._coerce_percent(
                pool.get("burnedPercentage")
            )
            locked = self._coerce_percent(
                pool.get("lockedPercentage")
            )
            unlocked = self._coerce_percent(
                pool.get("unlockedPercentage")
            )

            burned_total += burned or 0
            locked_total += locked or 0
            unlocked_max = max(
                unlocked_max,
                unlocked or 0
            )
            pool_count += 1

        if pool_count:
            burned_percent = burned_total / pool_count
            locked_percent = locked_total / pool_count
            safe_percent = burned_percent + locked_percent
        else:
            burned_percent = self._coerce_percent(
                data.get("liquidityBurnPercentage")
            )
            locked_percent = self._coerce_percent(
                data.get("locked")
                or data.get("liquidityLockedPercentage")
            )
            safe_percent = (
                (burned_percent or 0)
                + (locked_percent or 0)
            )

        checked = (
            pool_count > 0
            or burned_percent is not None
            or locked_percent is not None
        )

        if not checked:
            return self._normalize_lock_result(
                checked=False,
                required=True,
                locked=False,
                locked_percent=None,
                source="mobula",
                reason="mobula_liquidity_unavailable"
            )

        passes = (
            safe_percent
            >= MOBULA_MIN_BURNED_OR_LOCKED_PERCENT
        )

        result = self._normalize_lock_result(
            checked=True,
            required=True,
            locked=passes,
            locked_percent=safe_percent,
            source="mobula",
            reason=(
                "mobula_safe_liquidity"
                if passes
                else "mobula_unlocked_liquidity"
            )
        )
        result.update(
            {
                "burned_percent": burned_percent,
                "lp_locked_percent": locked_percent,
                "unlocked_percent": unlocked_max,
                "pool_count": pool_count,
                "min_safe_percent": (
                    MOBULA_MIN_BURNED_OR_LOCKED_PERCENT
                )
            }
        )
        return result

    async def check_mobula_liquidity_safety(
        self,
        chain,
        token_address
    ):

        chain = self._normalize_mobula_chain(chain)
        cached = self._get_cached_mobula_result(
            chain,
            token_address
        )

        if cached is not None:
            return cached

        if not MOBULA_API_KEY:
            result = self._normalize_lock_result(
                checked=False,
                required=True,
                locked=False,
                locked_percent=None,
                source="mobula",
                reason="mobula_api_key_missing"
            )
            self._cache_mobula_result(
                chain,
                token_address,
                result
            )
            return result

        url = (
            MOBULA_API_BASE_URL.rstrip("/")
            + "/token/security"
        )
        timeout = aiohttp.ClientTimeout(
            total=MOBULA_API_TIMEOUT_SECONDS
        )
        headers = {
            "Authorization": MOBULA_API_KEY
        }

        try:
            async with aiohttp.ClientSession(
                timeout=timeout
            ) as session:
                async with session.get(
                    url,
                    params={
                        "blockchain": chain,
                        "address": token_address
                    },
                    headers=headers
                ) as response:
                    if response.status != 200:
                        result = self._normalize_lock_result(
                            checked=False,
                            required=True,
                            locked=False,
                            locked_percent=None,
                            source="mobula",
                            reason=f"mobula_http_{response.status}"
                        )
                    else:
                        payload = await response.json(
                            content_type=None
                        )
                        result = self._extract_mobula_lock_result(
                            payload
                        )

        except Exception as e:
            print(
                f"Mobula liquidity safety error: {e}"
            )
            result = self._normalize_lock_result(
                checked=False,
                required=True,
                locked=False,
                locked_percent=None,
                source="mobula",
                reason="mobula_lookup_failed"
            )

        self._cache_mobula_result(
            chain,
            token_address,
            result
        )
        return result

    async def _fetch_coingecko_onchain_json(
        self,
        session,
        path
    ):

        async with session.get(
            self._coingecko_onchain_base_url() + path,
            headers=self._coingecko_onchain_headers()
        ) as response:

            if response.status != 200:
                return None

            return await response.json(
                content_type=None
            )

    def _extract_coingecko_pool_lock_result(
        self,
        payload
    ):

        attributes = (
            payload.get("data", {}).get("attributes", {})
            if isinstance(payload, dict)
            else {}
        )
        locked_percent = self._coerce_percent(
            attributes.get("locked_liquidity_percentage")
        )

        if locked_percent is None:
            return self._normalize_lock_result(
                checked=False,
                required=True,
                locked=False,
                locked_percent=None,
                source="coingecko_onchain",
                reason="coingecko_lock_unavailable"
            )

        locked = locked_percent >= LIQUIDITY_LOCK_MIN_PERCENT

        return self._normalize_lock_result(
            checked=True,
            required=True,
            locked=locked,
            locked_percent=locked_percent,
            source="coingecko_onchain",
            reason=(
                "coingecko_locked_liquidity"
                if locked
                else "coingecko_unlocked_liquidity"
            )
        )

    async def check_coingecko_liquidity_lock(
        self,
        chain,
        token_address,
        pair_address=""
    ):

        network = self._normalize_coingecko_network(
            chain
        )

        if network != "solana":
            return self._normalize_lock_result(
                checked=False,
                required=True,
                locked=False,
                locked_percent=None,
                source="coingecko_onchain",
                reason="coingecko_chain_unsupported"
            )

        timeout = aiohttp.ClientTimeout(
            total=RUGCHECK_API_TIMEOUT_SECONDS
        )

        try:
            async with aiohttp.ClientSession(
                timeout=timeout
            ) as session:
                pool_address = str(pair_address or "").strip()

                if not pool_address:
                    pools_payload = await self._fetch_coingecko_onchain_json(
                        session,
                        f"/networks/{network}/tokens/{token_address}/pools"
                    )
                    pools = (
                        pools_payload.get("data", [])
                        if isinstance(pools_payload, dict)
                        else []
                    )
                    pools.sort(
                        key=lambda pool: self._coerce_percent(
                            pool.get("attributes", {})
                            .get("reserve_in_usd")
                        ) or 0,
                        reverse=True
                    )

                    for pool in pools:
                        pool_address = (
                            pool.get("attributes", {})
                            .get("address", "")
                        )

                        if pool_address:
                            break

                if not pool_address:
                    return self._normalize_lock_result(
                        checked=False,
                        required=True,
                        locked=False,
                        locked_percent=None,
                        source="coingecko_onchain",
                        reason="coingecko_pool_missing"
                    )

                pool_payload = await self._fetch_coingecko_onchain_json(
                    session,
                    f"/networks/{network}/pools/{pool_address}"
                )
                return self._extract_coingecko_pool_lock_result(
                    pool_payload
                )

        except Exception as e:
            print(
                f"CoinGecko liquidity lock check error: {e}"
            )

        return self._normalize_lock_result(
            checked=False,
            required=True,
            locked=False,
            locked_percent=None,
            source="coingecko_onchain",
            reason="coingecko_lookup_failed"
        )

    async def check_bsc_honeypot(self, token_address):
        """GoPlus sellability/sell-tax verdict for a BSC token. Returns the
        structured dict from sources.honeypot (checked/safe/vetoes/...)."""

        if not BSC_HONEYPOT_CHECK_ENABLED:
            return {
                "checked": False,
                "safe": None,
                "vetoes": [],
                "source": "goplus",
                "reason": "honeypot_check_disabled"
            }

        if self._honeypot is None:
            self._honeypot = GoPlusHoneypotChecker()

        return await self._honeypot.assess("bsc", token_address)

    async def check_liquidity_lock(
        self,
        chain,
        token_address,
        lifecycle="migrated",
        pair_address=""
    ):

        if lifecycle == "bonding_curve":
            return self._normalize_lock_result(
                checked=False,
                required=False,
                locked=True,
                locked_percent=None,
                source="bonding_curve_exempt",
                reason="bonding_curve_exempt"
            )

        chain = str(chain or "").lower()

        # BSC honeypot / sell-tax gate. Runs BEFORE the liquidity-lock paths so a
        # confirmed-unsafe verdict takes precedence. An unsafe verdict is mapped
        # onto locked=False so the existing alert/entry gates veto it; safe or
        # unknown falls through to the normal (Mobula / exempt) lock handling.
        if chain == "bsc":
            honeypot = await self.check_bsc_honeypot(token_address)

            if honeypot.get("checked") and honeypot.get("safe") is False:
                result = self._normalize_lock_result(
                    checked=True,
                    required=True,
                    locked=False,
                    locked_percent=None,
                    source="goplus",
                    reason="honeypot:" + honeypot.get("reason", "unsafe")
                )
                result["honeypot"] = honeypot
                self._cache_liquidity_lock(
                    chain,
                    token_address,
                    result,
                    pair_address=pair_address
                )
                return result

        mobula_chains = {
            str(item).lower()
            for item in MOBULA_SAFETY_CHAINS
        }

        if chain in mobula_chains:
            result = await self.check_mobula_liquidity_safety(
                chain,
                token_address
            )
            self._cache_liquidity_lock(
                chain,
                token_address,
                result
            )
            return result

        if chain != "solana":
            return self._normalize_lock_result(
                checked=False,
                required=False,
                locked=True,
                locked_percent=None,
                source="non_required_chain_exempt",
                reason="non_required_chain_exempt"
            )

        cached = self._get_cached_liquidity_lock(
            chain,
            token_address,
            pair_address=pair_address
        )

        if cached is not None:
            return cached

        if not REQUIRE_LIQUIDITY_LOCK:
            result = self._normalize_lock_result(
                checked=False,
                required=False,
                locked=True,
                locked_percent=None,
                source="disabled",
                reason="requirement_disabled"
            )
            self._cache_liquidity_lock(
                chain,
                token_address,
                result,
                pair_address=pair_address
            )
            return result

        base_url = RUGCHECK_API_BASE_URL.rstrip("/")
        urls = [
            f"{base_url}/v1/tokens/{token_address}/report/summary",
            f"{base_url}/v1/tokens/{token_address}/report"
        ]

        timeout = aiohttp.ClientTimeout(
            total=RUGCHECK_API_TIMEOUT_SECONDS
        )

        headers = {}

        if RUGCHECK_API_KEY:
            headers["X-API-KEY"] = RUGCHECK_API_KEY

        try:
            async with aiohttp.ClientSession(
                timeout=timeout
            ) as session:

                for url in urls:
                    try:
                        async with session.get(
                            url,
                            headers=headers
                        ) as response:
                            if response.status != 200:
                                continue

                            payload = await response.json(
                                content_type=None
                            )
                            result = self._extract_liquidity_lock_result(
                                payload
                            )
                            if result.get("checked"):
                                self._cache_liquidity_lock(
                                    chain,
                                    token_address,
                                    result,
                                    pair_address=pair_address
                                )
                                return result
                    except Exception:
                        continue

        except Exception as e:
            print(
                f"Liquidity lock check error: {e}"
            )

        result = await self.check_coingecko_liquidity_lock(
            chain,
            token_address,
            pair_address=pair_address
        )

        if result.get("checked"):
            self._cache_liquidity_lock(
                chain,
                token_address,
                result,
                pair_address=pair_address
            )
            return result

        result = self._normalize_lock_result(
            checked=False,
            required=True,
            locked=False,
            locked_percent=None,
            source="error",
            reason="lock_lookup_failed"
        )
        self._cache_liquidity_lock(
            chain,
            token_address,
            result,
            pair_address=pair_address
        )
        return result
