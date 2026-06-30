import aiohttp
import asyncio
import os
import time
from collections import Counter, defaultdict

from state import (
    TRACKED_CANDIDATES,
    TOKEN_MEMORY
)

from config import (
    DEXSCREENER_SEARCH_TERMS_EXTRA,
    DEXSCREENER_SEARCH_TERMS_OVERRIDE,
    DEXSCREENER_SEARCH_TERMS_PER_REFRESH,
    BASE_DEXSCREENER_SEARCH_TERMS_EXTRA,
    HYPEREVM_DEXSCREENER_SEARCH_TERMS_EXTRA,
    HYPEREVM_SCANNER_MAX_FDV_USD,
    HYPEREVM_SCANNER_MIN_LIQUIDITY_USD,
    HYPERSWAP_SUBGRAPH_DISCOVERY_ENABLED,
    LIQD_LIQUIDCORE_DISCOVERY_ENABLED,
    LIQD_TOKEN_DISCOVERY_ENABLED,
    MAX_CANDIDATES,
    MAX_FDV_USD,
    MAX_LIQUIDITY_USD,
    MIN_BONDING_CURVE_FDV_USD,
    MIN_LIQUIDITY_USD,
    SCANNER_BAD_EVIDENCE_MAX_PENALTY,
    SCANNER_BAD_EVIDENCE_MEMORY_ENABLED,
    SCANNER_BAD_EVIDENCE_MEMORY_WINDOW_SECONDS,
    SCANNER_BAD_EVIDENCE_REPEAT_PENALTY,
    SCANNER_BAD_EVIDENCE_REPEAT_THRESHOLD,
    SCANNER_EVIDENCE_BLIND_MAX_FRACTION,
    SCANNER_EVIDENCE_RANKING_ENABLED,
    SCANNER_EVIDENCE_READY_MIN_SCORE,
    SCANNER_EVIDENCE_SCORE_WEIGHT,
    SCANNER_ENABLED_CHAINS,
    SCANNER_NOVELTY_ENABLED,
    SCANNER_NOVELTY_RECENT_WINDOW_SECONDS,
    SCANNER_NOVELTY_REPEAT_PENALTY,
    SCANNER_NOVELTY_REPEAT_THRESHOLD,
    SCANNER_SOURCE_QUOTA_DEXSCREENER,
    SCANNER_SOURCE_QUOTA_ENABLED,
    SCANNER_SOURCE_QUOTA_JUPITER,
    SCANNER_SOURCE_QUOTA_OTHER,
    SCANNER_SOURCE_QUOTA_PUMPFUN
)

from market_context import (
    build_market_context,
    is_scannable_market
)

from filters.contracts import (
    is_excluded_contract_address
)

from sources.mint_age import (
    passes_min_mint_age,
    resolve_mint_age
)

from sources.liqd import (
    LiquidCoreDiscovery,
    LiquidTokenDiscovery
)

from sources.hyperswap import (
    HyperSwapSubgraphDiscovery
)

from sources.launchpads import (
    PumpFunDiscovery
)

JUPITER_ENDPOINTS = [
    "https://lite-api.jup.ag/tokens/v2/toporganicscore/1h?limit=100",
    "https://lite-api.jup.ag/tokens/v2/toptrending/1h?limit=100",
    "https://lite-api.jup.ag/tokens/v2/recent",
]

JUPITER_PRO_ENDPOINTS = [
    "https://api.jup.ag/tokens/v2/toporganicscore/1h?limit=100",
    "https://api.jup.ag/tokens/v2/toptrending/1h?limit=100",
    "https://api.jup.ag/tokens/v2/recent",
]

JUPITER_API_KEY = os.getenv("JUPITER_API_KEY", "").strip()

DEXSCREENER_SEARCH_TERMS = [
    # launchpads & platforms
    "solana pump",
    "pump.fun solana",
    "solana bonk.fun",
    "bonk fun solana",
    "solana letsbonk",
    "letsbonk.fun",
    "solana launchpad",
    "solana launch",
    "solana bonding",
    "solana bonding curve",
    "solana curve",
    "solana fair launch",
    "solana raydium launch",
    "solana moonshot",
    "solana bags",
    "solana bags.fm",
    "bags.fm",
    "solana boop",
    "boop.fun",
    "solana believe",
    "believe app",
    "solana heaven",
    "heaven.fun",
    "solana revshare",
    # meme themes
    "solana meme",
    "solana meme coin",
    "solana pepe",
    "solana cat",
    "solana dog",
    "solana inu",
    "solana frog",
    "solana bird",
    "solana penguin",
    "solana wif",
    "dogwifhat solana",
    "solana bonk",
    "solana degen",
    "solana cope",
    "solana wojak",
    "solana chad",
    "solana based",
    "solana giga",
    # narrative / sector
    "solana ai",
    "solana agent",
    "solana ai agent",
    "solana depin",
    "solana rwa",
    "solana gaming",
    "solana nft",
    "solana defi",
    "solana desci",
    "solana governance",
    "solana dao",
    # catch-all freshness
    "solana community token",
    "solana new token",
    "solana token launch",
    "solana viral",
    "solana trending",
    "solana low cap",
    "solana micro cap",
    "solana gem",
    "solana revival",
    "solana bounce",
    "solana breakout",
]

HYPEREVM_DEXSCREENER_SEARCH_TERMS = [
    "hyperevm",
    "hyper evm",
    "hyperliquid evm",
    "hyperliquid",
    "hype",
    "hyperswap",
    "kittenswap",
    "ramses hyperevm",
    "prjx hyperevm",
]

BASE_DEXSCREENER_SEARCH_TERMS = [
    "base meme",
    "base memecoin",
    "base meme coin",
    "base token launch",
    "base new token",
    "base trending",
    "base low cap",
    "base micro cap",
    "base gem",
    "base revival",
    "base breakout",
    "base degen",
    "base based",
    "base pepe",
    "base dog",
    "base cat",
    "base frog",
    "base ai",
    "base agent",
    "base virtuals",
    "base aerodrome",
    "base zora",
]

def normalize_chain(chain):

    return str(chain or "solana").strip().lower()


def safe_float(value, default=0):

    try:
        return float(value or default)
    except (TypeError, ValueError):
        return default


def enabled_chains():

    chains = set(
        normalize_chain(chain)
        for chain in (SCANNER_ENABLED_CHAINS or ("solana",))
    )

    # BSC is intentionally retired from the active scanner path. Historical
    # rows and dormant helper modules can remain, but candidate generation
    # should not bring BSC tokens back in by accident.
    chains.discard("bsc")
    chains.discard("bnb")
    chains.discard("binance")

    return chains


def unique_terms(terms):

    return list(
        dict.fromkeys(
            term
            for term in terms
            if term
        )
    )


def interleave_term_groups(groups):

    groups = [
        unique_terms(group)
        for group in groups
        if group
    ]

    terms = []
    index = 0

    while True:
        added = False

        for group in groups:
            if index < len(group):
                terms.append(group[index])
                added = True

        if not added:
            break

        index += 1

    return tuple(
        dict.fromkeys(terms)
    )


def active_dexscreener_search_terms():

    term_groups = []
    chains = enabled_chains()

    if "solana" in chains:
        if DEXSCREENER_SEARCH_TERMS_OVERRIDE:
            solana_terms = list(DEXSCREENER_SEARCH_TERMS_OVERRIDE)
        else:
            solana_terms = list(DEXSCREENER_SEARCH_TERMS)
        solana_terms.extend(DEXSCREENER_SEARCH_TERMS_EXTRA)
        term_groups.append(solana_terms)

    if "hyperevm" in chains:
        hyperevm_terms = list(HYPEREVM_DEXSCREENER_SEARCH_TERMS)
        hyperevm_terms.extend(HYPEREVM_DEXSCREENER_SEARCH_TERMS_EXTRA)
        term_groups.append(hyperevm_terms)

    if "base" in chains:
        base_terms = list(BASE_DEXSCREENER_SEARCH_TERMS)
        base_terms.extend(BASE_DEXSCREENER_SEARCH_TERMS_EXTRA)
        term_groups.append(base_terms)

    return interleave_term_groups(term_groups)


DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/124.0.0.0 "
        "Safari/537.36"
    ),
    "Accept": (
        "application/json, "
        "text/plain, */*"
    )
}


def make_connector():
    try:
        return aiohttp.TCPConnector(
            resolver=aiohttp.AsyncResolver(
                nameservers=["8.8.8.8", "8.8.4.4"]
            ),
            ssl=True
        )
    except Exception:
        return aiohttp.TCPConnector(ssl=True)


class CandidateDiscovery:

    def __init__(
        self,
        client
    ):

        self.client = client

        self.session = aiohttp.ClientSession(
            headers=DEFAULT_HEADERS,
            connector=make_connector()
        )
        self.liqd_liquidcore = LiquidCoreDiscovery(
            self.session
        )
        self.liqd_tokens = LiquidTokenDiscovery(
            self.session
        )
        self.hyperswap_subgraph = HyperSwapSubgraphDiscovery(
            self.session
        )
        self.pumpfun = PumpFunDiscovery(
            self.session
        )
        self.dex_search_rotation_index = 0

    async def close(self):

        if self.session:

            await self.session.close()
            self.session = None

    def rejected_candidate(
        self,
        address,
        reason
    ):

        return {
            "address": address,
            "rejected": True,
            "reason": reason
        }

    def add_candidate(
        self,
        candidate_map,
        token,
        attrition,
        default_source,
        default_chain="solana"
    ):

        address = token.get("address")

        if not address:
            attrition["source_missing_address"] += 1
            return False

        chain = normalize_chain(
            token.get("chain", default_chain)
        )

        if chain not in enabled_chains():
            attrition["disabled_chain"] += 1
            return False

        if is_excluded_contract_address(address):
            attrition["excluded_suffix"] += 1
            return False

        candidate = dict(token)
        candidate["address"] = address
        candidate["chain"] = chain
        candidate["symbol"] = token.get(
            "symbol",
            "UNKNOWN"
        )
        candidate["source"] = token.get(
            "source",
            default_source
        )

        if address in candidate_map:
            attrition["duplicate_address"] += 1
            candidate_map[address].update(
                {
                    key: value
                    for key, value in candidate.items()
                    if value not in (None, "")
                }
            )
            return True

        candidate_map[address] = candidate
        return True

    def selected_dexscreener_search_terms(self):

        terms = active_dexscreener_search_terms()
        per_refresh = int(
            DEXSCREENER_SEARCH_TERMS_PER_REFRESH
            or 0
        )

        if (
            per_refresh <= 0
            or per_refresh >= len(terms)
        ):
            return terms

        start = self.dex_search_rotation_index
        selected = [
            terms[(start + index) % len(terms)]
            for index in range(per_refresh)
        ]
        self.dex_search_rotation_index = (
            start + per_refresh
        ) % len(terms)

        return tuple(selected)

    def candidate_source_family(
        self,
        source
    ):

        source = str(source or "").lower()

        if source == "jupiter":
            return "jupiter"

        if source.startswith("dexscreener"):
            return "dexscreener"

        if source.startswith(
            (
                "pumpfun_",
                "bonkfun_",
                "letsbonk_",
                "solana_launchpad_"
            )
        ):
            return "pumpfun"

        if source.startswith("liqd_"):
            return "liqd"

        if source.startswith("hyperswap_"):
            return "hyperswap"

        return "other"

    def candidate_novelty_factor(
        self,
        metadata,
        memory,
        now
    ):

        if not SCANNER_NOVELTY_ENABLED:
            return 1.0

        factor = 1.0
        last_seen_at = max(
            safe_float(memory.get("last_validated_at"), 0),
            safe_float(memory.get("last_discovery_at"), 0)
        )
        discovery_count = int(
            safe_float(memory.get("discovery_count"), 0)
        )
        source = str(metadata.get("source", "")).lower()
        source_counts = memory.get("discovery_source_counts") or {}
        source_count = int(
            safe_float(
                source_counts.get(source),
                0
            )
        )

        if last_seen_at <= 0:
            factor += 0.15
        else:
            age_seconds = max(now - last_seen_at, 0)

            if age_seconds < SCANNER_NOVELTY_RECENT_WINDOW_SECONDS:
                factor -= 0.30
            elif age_seconds < (
                2 * SCANNER_NOVELTY_RECENT_WINDOW_SECONDS
            ):
                factor -= 0.12
            elif age_seconds > (
                24 * 3600
            ):
                factor += 0.05

        if discovery_count > SCANNER_NOVELTY_REPEAT_THRESHOLD:
            repeat_penalty = (
                discovery_count
                - SCANNER_NOVELTY_REPEAT_THRESHOLD
            )
            factor -= min(
                0.20,
                SCANNER_NOVELTY_REPEAT_PENALTY * repeat_penalty
            )

        if source_count > 1:
            factor -= min(
                0.15,
                0.05 * (source_count - 1)
            )

        if metadata.get("provider_pair_pending"):
            factor += 0.05

        return max(
            0.50,
            min(factor, 1.35)
        )

    def candidate_data_completeness_score(
        self,
        candidate
    ):

        if not SCANNER_EVIDENCE_RANKING_ENABLED:
            return 1.0

        score = 0.0

        if candidate.get("pair_address"):
            score += 0.18

        if not candidate.get("provider_pair_pending"):
            score += 0.08

        if safe_float(candidate.get("liquidity"), 0) > 0:
            score += 0.14

        if safe_float(candidate.get("fdv"), 0) > 0:
            score += 0.08

        if safe_float(candidate.get("volume_5m"), 0) > 0:
            score += 0.24

        if abs(safe_float(candidate.get("price_change_5m"), 0)) > 1e-12:
            score += 0.14

        if safe_float(candidate.get("txns_5m"), 0) > 0:
            score += 0.08

        if (
            safe_float(candidate.get("buys_5m"), 0)
            + safe_float(candidate.get("sells_5m"), 0)
            > 0
        ):
            score += 0.06

        return max(0.0, min(score, 1.0))

    def candidate_bad_evidence_penalty(
        self,
        memory,
        now
    ):

        if not SCANNER_BAD_EVIDENCE_MEMORY_ENABLED:
            return 0.0

        last_bad_at = safe_float(
            memory.get("last_bad_evidence_at"),
            0
        )

        if (
            last_bad_at <= 0
            or now - last_bad_at > SCANNER_BAD_EVIDENCE_MEMORY_WINDOW_SECONDS
        ):
            return 0.0

        count = int(
            safe_float(
                memory.get("bad_evidence_count"),
                0
            )
        )

        if count <= SCANNER_BAD_EVIDENCE_REPEAT_THRESHOLD:
            return 0.0

        repeats = count - SCANNER_BAD_EVIDENCE_REPEAT_THRESHOLD
        return min(
            SCANNER_BAD_EVIDENCE_MAX_PENALTY,
            repeats * SCANNER_BAD_EVIDENCE_REPEAT_PENALTY
        )

    def candidate_source_quotas(
        self,
        validated_count,
        family_counts
    ):

        if (
            not SCANNER_SOURCE_QUOTA_ENABLED
            or validated_count <= 0
        ):
            return {
                family: count
                for family, count in family_counts.items()
            }

        weights = {
            "jupiter": SCANNER_SOURCE_QUOTA_JUPITER,
            "dexscreener": SCANNER_SOURCE_QUOTA_DEXSCREENER,
            "pumpfun": SCANNER_SOURCE_QUOTA_PUMPFUN,
            "liqd": 0.0,
            "hyperswap": 0.0,
            "other": SCANNER_SOURCE_QUOTA_OTHER
        }

        total_weight = sum(
            weight
            for family, weight in weights.items()
            if family_counts.get(family, 0) > 0
        )

        if total_weight <= 0:
            return {
                family: count
                for family, count in family_counts.items()
            }

        targets = {}
        remainders = []
        allocated = 0

        for family, count in family_counts.items():
            weight = weights.get(family, SCANNER_SOURCE_QUOTA_OTHER)

            if weight <= 0:
                targets[family] = 0
                continue

            normalized_weight = weight / total_weight
            exact = normalized_weight * validated_count
            limit = min(count, int(exact))
            targets[family] = limit
            allocated += limit
            remainders.append(
                (
                    exact - limit,
                    family
                )
            )

        remaining = max(validated_count - allocated, 0)
        remainders.sort(
            key=lambda item: (
                item[0],
                item[1]
            ),
            reverse=True
        )

        for _remainder, family in remainders:
            if remaining <= 0:
                break

            if targets.get(family, 0) >= family_counts.get(family, 0):
                continue

            targets[family] = targets.get(family, 0) + 1
            remaining -= 1

        return targets

    def refresh_ranking_snapshot(
        self,
        validated,
        now
    ):

        family_buckets = defaultdict(list)

        for address, candidate in validated.items():
            memory = TOKEN_MEMORY[address]
            family = self.candidate_source_family(
                candidate.get("source", "")
            )
            novelty_factor = self.candidate_novelty_factor(
                candidate,
                memory,
                now
            )
            completeness = self.candidate_data_completeness_score(
                candidate
            )
            bad_penalty = self.candidate_bad_evidence_penalty(
                memory,
                now
            )
            evidence_factor = 1.0
            if SCANNER_EVIDENCE_RANKING_ENABLED:
                evidence_factor = max(
                    0.20,
                    1.0
                    - (
                        SCANNER_EVIDENCE_SCORE_WEIGHT
                        * (1.0 - completeness)
                    )
                    - bad_penalty
                )
            adjusted_score = (
                safe_float(
                    candidate.get("score"),
                    0
                )
                * novelty_factor
                * evidence_factor
            )
            candidate = dict(candidate)
            candidate["source_family"] = family
            candidate["novelty_factor"] = novelty_factor
            candidate["data_completeness_score"] = round(
                completeness,
                4
            )
            candidate["evidence_factor"] = round(
                evidence_factor,
                4
            )
            candidate["bad_evidence_penalty"] = round(
                bad_penalty,
                4
            )
            candidate["evidence_bucket"] = (
                "ready"
                if completeness >= SCANNER_EVIDENCE_READY_MIN_SCORE
                else "blind"
            )
            candidate["adjusted_score"] = adjusted_score
            family_buckets[family].append(
                (
                    address,
                    candidate
                )
            )

        family_counts = {
            family: len(items)
            for family, items in family_buckets.items()
        }
        quotas = self.candidate_source_quotas(
            min(
                MAX_CANDIDATES,
                len(validated)
            ),
            family_counts
        )

        selected = []
        selected_addresses = set()

        for family, limit in quotas.items():
            if limit <= 0:
                continue

            ranked_family = sorted(
                family_buckets.get(family, []),
                key=lambda item: (
                    item[1].get("adjusted_score", 0),
                    item[1].get("score", 0),
                    item[1].get("novelty_factor", 1.0)
                ),
                reverse=True
            )

            for address, candidate in ranked_family[:limit]:
                selected.append(
                    (
                        address,
                        candidate
                    )
                )
                selected_addresses.add(address)

        leftovers = []

        for family_items in family_buckets.values():
            for address, candidate in family_items:
                if address not in selected_addresses:
                    leftovers.append(
                        (
                            address,
                            candidate
                        )
                    )

        leftovers.sort(
            key=lambda item: (
                item[1].get("adjusted_score", 0),
                item[1].get("score", 0),
                item[1].get("novelty_factor", 1.0)
            ),
            reverse=True
        )

        for address, candidate in leftovers:
            if len(selected) >= MAX_CANDIDATES:
                break
            selected.append(
                (
                    address,
                    candidate
                )
            )

        selected = selected[:MAX_CANDIDATES]

        if SCANNER_EVIDENCE_RANKING_ENABLED:
            ready = [
                item
                for item in selected
                if item[1].get("evidence_bucket") != "blind"
            ]
            blind = [
                item
                for item in selected
                if item[1].get("evidence_bucket") == "blind"
            ]
            blind_limit = max(
                1,
                int(MAX_CANDIDATES * SCANNER_EVIDENCE_BLIND_MAX_FRACTION)
            )
            selected = (ready + blind[:blind_limit])[:MAX_CANDIDATES]

        ranked_candidates = dict(selected)

        if ranked_candidates:
            evidence_counts = Counter(
                candidate.get("evidence_bucket", "unknown")
                for candidate in ranked_candidates.values()
            )
            print(
                "Discovery evidence buckets: "
                + ", ".join(
                    f"{bucket}={count}"
                    for bucket, count in sorted(evidence_counts.items())
                )
            )

        return ranked_candidates, family_counts, quotas

    def solana_pending_candidate(
        self,
        address,
        metadata
    ):

        chain = normalize_chain(
            metadata.get("chain", "solana")
        )

        if chain != "solana":
            return None

        source = str(
            metadata.get("source", "")
        )
        trusted_prefixes = (
            "pumpfun_",
            "bonkfun_",
            "letsbonk_",
            "solana_launchpad_"
        )

        if not source.startswith(trusted_prefixes):
            return None

        return {
            "address": address,
            "chain": "solana",
            "symbol": metadata.get("symbol", "UNKNOWN"),
            "name": metadata.get("name", ""),
            "pair_address": metadata.get("pair_address"),
            "source": source,
            "launchpad": metadata.get("launchpad"),
            "lifecycle_hint": metadata.get(
                "lifecycle_hint",
                "bonding_curve"
            ),
            "provider_pair_pending": True,
            "first_seen_at": metadata.get("first_seen_at"),
            "fdv": 0,
            "lifecycle": "provider_pair_pending",
            "liquidity": 0,
            "score": safe_float(
                metadata.get("source_score"),
                1
            )
        }

    def hyperswap_pending_candidate(
        self,
        address,
        metadata
    ):

        source = str(
            metadata.get("source", "")
        )

        if not source.startswith("hyperswap_"):
            return None

        liquidity = safe_float(
            metadata.get("hyperswap_liquidity_usd"),
            0
        )

        if liquidity < HYPEREVM_SCANNER_MIN_LIQUIDITY_USD:
            return None

        return {
            "address": address,
            "chain": "hyperevm",
            "symbol": metadata.get("symbol", "UNKNOWN"),
            "pair_address": metadata.get("hyperswap_pair_address"),
            "source": source,
            "hyperswap_pair_address": metadata.get(
                "hyperswap_pair_address"
            ),
            "hyperswap_liquidity_usd": liquidity,
            "hyperswap_volume_usd": safe_float(
                metadata.get("hyperswap_volume_usd"),
                0
            ),
            "provider_pair_pending": True,
            "fdv": 0,
            "lifecycle": "hyperswap_pending",
            "liquidity": liquidity,
            "score": liquidity * 0.50
        }

    def get_market_rejection_reason(
        self,
        market
    ):

        fdv = market["fdv"]
        chain = normalize_chain(
            market.get("chain", "solana")
        )

        if not fdv:
            return "missing_fdv"

        if chain == "hyperevm":
            if fdv > HYPEREVM_SCANNER_MAX_FDV_USD:
                return "fdv_above_max"

            if (
                market["liquidity"]
                < HYPEREVM_SCANNER_MIN_LIQUIDITY_USD
            ):
                return "migrated_liquidity_below_min"

            return "market_not_scannable"

        if fdv > MAX_FDV_USD:
            return "fdv_above_max"

        if market["lifecycle"] == "bonding_curve":
            if fdv < MIN_BONDING_CURVE_FDV_USD:
                return "bonding_fdv_below_floor"

            return "bonding_market_not_scannable"

        liquidity = market["liquidity"]

        if liquidity < MIN_LIQUIDITY_USD:
            return "migrated_liquidity_below_min"

        if liquidity > MAX_LIQUIDITY_USD:
            return "migrated_liquidity_above_max"

        return "market_not_scannable"

    def print_attrition_report(
        self,
        attrition
    ):

        lines = [
            (
                "Raw Jupiter",
                attrition["raw_jupiter"]
            ),
            (
                "Raw DexScreener",
                attrition["raw_dexscreener"]
            ),
            (
                "Raw Pump.fun",
                attrition["raw_pumpfun"]
            ),
            (
                "Source missing address",
                attrition["source_missing_address"]
            ),
            (
                "Excluded suffix",
                attrition["excluded_suffix"]
            ),
            (
                "Duplicate address merge",
                attrition["duplicate_address"]
            ),
            (
                "Unique after merge",
                attrition["unique_after_merge"]
            ),
            (
                "Sampled for validation",
                attrition["sampled_for_validation"]
            ),
            (
                "Not sampled",
                attrition["not_sampled"]
            ),
            (
                "No Dex pair",
                attrition["no_dex_pair"]
            ),
            (
                "Provider pair pending",
                attrition["provider_pair_pending"]
            ),
            (
                "Missing FDV",
                attrition["missing_fdv"]
            ),
            (
                "FDV above max",
                attrition["fdv_above_max"]
            ),
            (
                "Bonding FDV below floor",
                attrition["bonding_fdv_below_floor"]
            ),
            (
                "Bonding liquidity missing",
                attrition["bonding_liquidity_missing"]
            ),
            (
                "Bonding market not scannable",
                attrition["bonding_market_not_scannable"]
            ),
            (
                "Migrated liquidity below min",
                attrition["migrated_liquidity_below_min"]
            ),
            (
                "Migrated liquidity above max",
                attrition["migrated_liquidity_above_max"]
            ),
            (
                "Market not scannable",
                attrition["market_not_scannable"]
            ),
            (
                "Mint age missing",
                attrition["mint_age_missing"]
            ),
            (
                "Mint age under min",
                attrition["mint_age_under_min"]
            ),
            (
                "Validation errors",
                attrition["validation_error"]
            ),
            (
                "Validated",
                attrition["validated"]
            )
        ]

        if attrition["liqd_liquidcore_enabled"]:
            lines.insert(
                2,
                (
                    "Raw LiquidCore",
                    attrition["raw_liqd_liquidcore"]
                )
            )

        if attrition["liqd_token_list_enabled"]:
            lines.insert(
                3,
                (
                    "Raw Liquid token list",
                    attrition["raw_liqd_tokens"]
                )
            )

        if attrition["hyperswap_subgraph_enabled"]:
            lines.insert(
                4,
                (
                    "Raw HyperSwap subgraph",
                    attrition["raw_hyperswap_subgraph"]
                )
            )

        if attrition["disabled_chain"]:
            lines.insert(
                6,
                (
                    "Disabled-chain candidates",
                    attrition["disabled_chain"]
                )
            )

        print("Candidate validation attrition:")

        for label, value in lines:
            print(
                f"- {label}: {value}"
            )

    async def fetch_jupiter_tokens(self):

        timeout = aiohttp.ClientTimeout(total=30)

        discovered = {}
        saw_success = False

        endpoints = list(JUPITER_ENDPOINTS)

        if JUPITER_API_KEY:
            endpoints.extend(JUPITER_PRO_ENDPOINTS)

        for url in endpoints:

            try:

                headers = None

                if url.startswith("https://api.jup.ag"):
                    headers = {
                        "x-api-key": JUPITER_API_KEY
                    }

                async with self.session.get(
                    url,
                    headers=headers,
                    timeout=timeout
                ) as response:

                    if response.status != 200:
                        print(
                            f"Jupiter returned "
                            f"{response.status} "
                            f"from {url}"
                        )
                        continue

                    data = await response.json(
                        content_type=None
                    )

                    if not data:
                        continue

                    saw_success = True

                    for token in self.normalize_jupiter_tokens(
                        data
                    ):
                        discovered[
                            token["address"]
                        ] = token

            except aiohttp.ClientConnectorError as e:
                print(
                    f"Jupiter connection error "
                    f"({url}): {e}"
                )

            except Exception as e:
                print(
                    f"Jupiter fetch failed "
                    f"({url}): {e}"
                )

        if saw_success:
            return list(discovered.values())

        print(
            "All Jupiter endpoints failed "
            "- skipping Jupiter discovery."
        )
        return []

    @staticmethod
    def normalize_jupiter_tokens(data):

        if isinstance(data, dict):
            items = (
                data.get("tokens")
                or data.get("data")
                or []
            )
        else:
            items = data

        if not isinstance(items, list):
            return []

        tokens = []

        for item in items:

            if isinstance(item, str):
                if is_excluded_contract_address(item):
                    continue

                tokens.append({
                    "address": item,
                    "symbol": "UNKNOWN",
                    "source": "jupiter"
                })
                continue

            if not isinstance(item, dict):
                continue

            address = (
                item.get("id")
                or item.get("address")
                or item.get("mint")
            )

            if not address:
                continue

            if is_excluded_contract_address(address):
                continue

            tokens.append({
                "address": address,
                "symbol": item.get(
                    "symbol",
                    "UNKNOWN"
                ),
                "source": "jupiter"
            })

        return tokens

    async def fetch_dexscreener_candidates(self):

        discovered = {}

        try:

            terms = self.selected_dexscreener_search_terms()

            if (
                DEXSCREENER_SEARCH_TERMS_PER_REFRESH > 0
                and terms
            ):
                print(
                    "DexScreener search terms this refresh: "
                    + ", ".join(terms)
                )

            for term in terms:

                if self.client.is_backing_off():
                    print(
                        "DexScreener search skipped "
                        "while cooling down."
                    )
                    break

                group = await self.fetch_dexscreener_search(
                    term
                )

                for token in group:
                    addr = token.get("address")
                    chain = normalize_chain(
                        token.get("chain", "solana")
                    )
                    if addr:
                        discovered[(chain, addr)] = token

        except Exception as e:
            print(f"DexScreener search error: {e}")

        return list(discovered.values())

    async def fetch_dexscreener_search(self, query):

        try:
            chains = enabled_chains()
            chains.discard("bsc")

            if not chains:
                return []

            pairs = await self.client.fetch_search_pairs(
                query,
                chains=chains
            )

            if not pairs:
                return []

            seen = set()
            tokens = []

            for pair in pairs:

                base = pair.get(
                    "baseToken", {}
                )

                addr = base.get("address")
                chain = normalize_chain(
                    pair.get("chainId", "solana")
                )

                if (
                    not addr
                    or (chain, addr) in seen
                    or chain not in chains
                    or is_excluded_contract_address(addr)
                ):
                    continue

                seen.add((chain, addr))

                tokens.append(
                    {
                        "address": addr,
                        "chain": chain,
                        "symbol": base.get(
                            "symbol",
                            "UNKNOWN"
                        ),
                        "source": "dexscreener_search"
                    }
                )

            return tokens

        except Exception as e:
            print(
                f"DexScreener search failed "
                f"({query}): {e}"
            )
            return []

    async def fetch_liqd_liquidcore_candidates(self):

        try:
            return await self.liqd_liquidcore.fetch_candidates()

        except Exception as e:
            print(
                "LiquidCore discovery failed: "
                f"{e}"
            )
            return []

    async def fetch_liqd_token_candidates(self):

        try:
            return await self.liqd_tokens.fetch_candidates()

        except Exception as e:
            print(
                "Liquid token discovery failed: "
                f"{e}"
            )
            return []

    async def fetch_hyperswap_subgraph_candidates(self):

        try:
            return await self.hyperswap_subgraph.fetch_candidates()

        except Exception as e:
            print(
                "HyperSwap subgraph discovery failed: "
                f"{e}"
            )
            return []

    async def fetch_pumpfun_candidates(self):

        try:
            return await self.pumpfun.fetch_candidates()

        except Exception as e:
            print(
                "Pump.fun discovery failed: "
                f"{e}"
            )
            return []

    async def refresh_candidates(self):

        print("\nRefreshing candidate universe...")

        candidate_map = {}
        attrition = Counter()
        chains = enabled_chains()
        liqd_enabled = (
            "hyperevm" in chains
            and LIQD_LIQUIDCORE_DISCOVERY_ENABLED
        )
        liqd_tokens_enabled = (
            "hyperevm" in chains
            and LIQD_TOKEN_DISCOVERY_ENABLED
        )
        hyperswap_enabled = (
            "hyperevm" in chains
            and HYPERSWAP_SUBGRAPH_DISCOVERY_ENABLED
        )
        attrition["liqd_liquidcore_enabled"] = int(liqd_enabled)
        attrition["liqd_token_list_enabled"] = int(
            liqd_tokens_enabled
        )
        attrition["hyperswap_subgraph_enabled"] = int(
            hyperswap_enabled
        )

        jupiter_tokens = []

        if "solana" in chains:
            jupiter_tokens = (
                await self.fetch_jupiter_tokens()
            )

        attrition["raw_jupiter"] = len(jupiter_tokens)

        print(
            f"Jupiter returned "
            f"{len(jupiter_tokens)} tokens"
        )

        for token in jupiter_tokens:

            address = token.get("address")

            if not address:
                attrition[
                    "source_missing_address"
                ] += 1
                continue

            chain = normalize_chain(
                token.get("chain", "solana")
            )

            if chain not in chains:
                attrition["disabled_chain"] += 1
                continue

            if is_excluded_contract_address(address):
                attrition[
                    "excluded_suffix"
                ] += 1
                continue

            if address in candidate_map:
                attrition[
                    "duplicate_address"
                ] += 1

            candidate_map[address] = {
                "address": address,
                "chain": chain,
                "symbol": token.get(
                    "symbol",
                    "UNKNOWN"
                ),
                "source": "jupiter"
            }

        dex_tokens = (
            await self.fetch_dexscreener_candidates()
        )
        attrition["raw_dexscreener"] = len(dex_tokens)

        print(
            f"DexScreener returned "
            f"{len(dex_tokens)} candidates"
        )
        dex_chain_counts = Counter(
            token.get("chain", "unknown")
            for token in dex_tokens
        )

        if dex_chain_counts:
            print(
                "DexScreener candidates by chain: "
                + ", ".join(
                    f"{chain}={count}"
                    for chain, count in sorted(
                        dex_chain_counts.items()
                    )
                )
            )

        for token in dex_tokens:

            address = token.get("address")

            if not address:
                attrition[
                    "source_missing_address"
                ] += 1
                continue

            chain = normalize_chain(
                token.get("chain", "solana")
            )

            if chain not in chains:
                attrition["disabled_chain"] += 1
                continue

            if is_excluded_contract_address(address):
                attrition[
                    "excluded_suffix"
                ] += 1
                continue

            if address in candidate_map:
                attrition[
                    "duplicate_address"
                ] += 1

            candidate_map[address] = {
                "address": address,
                "chain": chain,
                "symbol": token.get(
                    "symbol",
                    "UNKNOWN"
                ),
                "source": token.get(
                    "source",
                    "dexscreener"
                )
            }

        pumpfun_tokens = []

        if "solana" in chains:
            pumpfun_tokens = (
                await self.fetch_pumpfun_candidates()
            )

        attrition["raw_pumpfun"] = len(
            pumpfun_tokens
        )

        if pumpfun_tokens:
            print(
                "Pump.fun returned "
                f"{len(pumpfun_tokens)} candidates"
            )

        for token in pumpfun_tokens:
            self.add_candidate(
                candidate_map,
                token,
                attrition,
                "pumpfun_recent",
                default_chain="solana"
            )

        liqd_tokens = []

        if liqd_enabled:
            liqd_tokens = (
                await self.fetch_liqd_liquidcore_candidates()
            )

        attrition["raw_liqd_liquidcore"] = len(
            liqd_tokens
        )

        if liqd_enabled:
            print(
                "LiquidCore returned "
                f"{len(liqd_tokens)} candidates"
            )

        for token in liqd_tokens:

            address = token.get("address")

            if not address:
                attrition[
                    "source_missing_address"
                ] += 1
                continue

            if is_excluded_contract_address(address):
                attrition[
                    "excluded_suffix"
                ] += 1
                continue

            if address in candidate_map:
                attrition[
                    "duplicate_address"
                ] += 1
                continue

            candidate_map[address] = {
                "address": address,
                "chain": "hyperevm",
                "symbol": token.get(
                    "symbol",
                    "UNKNOWN"
                ),
                "source": token.get(
                    "source",
                    "liqd_liquidcore"
                ),
                "liqd_pool_address": token.get(
                    "liqd_pool_address"
                ),
                "liqd_pool_pair": token.get(
                    "liqd_pool_pair"
                ),
                "liqd_tvl_usd": token.get(
                    "liqd_tvl_usd"
                ),
                "liqd_volume_24h_usd": token.get(
                    "liqd_volume_24h_usd"
                )
            }

        liqd_token_list = []

        if liqd_tokens_enabled:
            liqd_token_list = (
                await self.fetch_liqd_token_candidates()
            )

        attrition["raw_liqd_tokens"] = len(
            liqd_token_list
        )

        if liqd_tokens_enabled:
            print(
                "Liquid token list returned "
                f"{len(liqd_token_list)} candidates"
            )

        for token in liqd_token_list:

            address = token.get("address")

            if not address:
                attrition[
                    "source_missing_address"
                ] += 1
                continue

            if is_excluded_contract_address(address):
                attrition[
                    "excluded_suffix"
                ] += 1
                continue

            if address in candidate_map:
                attrition[
                    "duplicate_address"
                ] += 1
                continue

            candidate_map[address] = {
                "address": address,
                "chain": "hyperevm",
                "symbol": token.get(
                    "symbol",
                    "UNKNOWN"
                ),
                "name": token.get(
                    "name",
                    ""
                ),
                "source": token.get(
                    "source",
                    "liqd_tokens"
                ),
                "liqd_transfers_24h": token.get(
                    "liqd_transfers_24h"
                )
            }

        hyperswap_tokens = []

        if hyperswap_enabled:
            hyperswap_tokens = (
                await self.fetch_hyperswap_subgraph_candidates()
            )

        attrition["raw_hyperswap_subgraph"] = len(
            hyperswap_tokens
        )

        if hyperswap_enabled:
            print(
                "HyperSwap subgraph returned "
                f"{len(hyperswap_tokens)} candidates"
            )

        for token in hyperswap_tokens:

            address = token.get("address")

            if not address:
                attrition[
                    "source_missing_address"
                ] += 1
                continue

            if is_excluded_contract_address(address):
                attrition[
                    "excluded_suffix"
                ] += 1
                continue

            if address in candidate_map:
                attrition[
                    "duplicate_address"
                ] += 1
                continue

            candidate_map[address] = {
                "address": address,
                "chain": "hyperevm",
                "symbol": token.get(
                    "symbol",
                    "UNKNOWN"
                ),
                "name": token.get(
                    "name",
                    ""
                ),
                "source": token.get(
                    "source",
                    "hyperswap_subgraph"
                ),
                "hyperswap_pair_address": token.get(
                    "hyperswap_pair_address"
                ),
                "hyperswap_liquidity_usd": token.get(
                    "hyperswap_liquidity_usd"
                ),
                "hyperswap_volume_usd": token.get(
                    "hyperswap_volume_usd"
                )
            }

        validated = {}

        addresses = [
            address
            for address in candidate_map.keys()
            if not is_excluded_contract_address(address)
        ]

        attrition["unique_after_merge"] = len(addresses)

        sample = addresses[:MAX_CANDIDATES * 3]
        attrition["sampled_for_validation"] = len(sample)
        attrition["not_sampled"] = max(
            len(addresses) - len(sample),
            0
        )

        addresses_by_chain = defaultdict(list)

        for address in sample:
            chain = candidate_map[address].get(
                "chain",
                "solana"
            )
            addresses_by_chain[chain].append(address)

        if addresses_by_chain:
            print(
                "Validating candidates by chain: "
                + ", ".join(
                    f"{chain}={len(chain_addresses)}"
                    for chain, chain_addresses in sorted(
                        addresses_by_chain.items()
                    )
                )
            )

        pair_map = {}

        for chain, chain_addresses in addresses_by_chain.items():
            pair_map.update(
                await self.client.fetch_token_pairs_batch(
                    chain_addresses,
                    allow_fallback=True,
                    chain_id=chain
                )
            )

        tasks = [
            self.validate_candidate(
                address,
                candidate_map[address],
                pair_map.get(address, [])
            )
            for address in sample
        ]

        results = await asyncio.gather(
            *tasks,
            return_exceptions=True
        )

        for result in results:

            if not result:
                attrition["validation_error"] += 1
                continue

            if isinstance(result, Exception):
                attrition["validation_error"] += 1
                continue

            if result.get("rejected"):
                attrition[
                    result.get(
                        "reason",
                        "validation_error"
                    )
                ] += 1
                continue

            address = result["address"]
            validated[address] = result
            attrition["validated"] += 1

            if result.get("provider_pair_pending"):
                attrition["provider_pair_pending"] += 1

            memory = TOKEN_MEMORY[address]
            now = time.time()
            source = str(
                result.get("source", "")
            ).lower()

            memory["discovery_count"] = (
                int(
                    safe_float(
                        memory.get("discovery_count"),
                        0
                    )
                )
                + 1
            )
            memory["last_discovery_at"] = now
            memory["last_discovery_source"] = source
            memory["last_validated_at"] = now
            memory["last_validated_source"] = source
            memory["last_seen_at"] = now
            memory["last_seen_source"] = source
            source_counts = memory.setdefault(
                "discovery_source_counts",
                {}
            )
            source_counts[source] = (
                int(
                    safe_float(
                        source_counts.get(source),
                        0
                    )
                )
                + 1
            )

        now = time.time()
        ranked_candidates, family_counts, quotas = (
            self.refresh_ranking_snapshot(
                validated,
                now
            )
        )

        if family_counts:
            print(
                "Validated candidates by source family: "
                + ", ".join(
                    f"{family}={count}"
                    for family, count in sorted(
                        family_counts.items()
                    )
                )
            )

        if quotas:
            print(
                "Source quotas this refresh: "
                + ", ".join(
                    f"{family}={count}"
                    for family, count in sorted(
                        quotas.items()
                    )
                )
            )

        if (
            not ranked_candidates
            and TRACKED_CANDIDATES
            and (
                not candidate_map
                or self.client.recently_rate_limited()
            )
        ):
            self.print_attrition_report(
                attrition
            )

            print(
                "Refresh produced no validated candidates "
                "during an upstream data issue; keeping "
                f"{len(TRACKED_CANDIDATES)} existing candidates."
            )
            return

        TRACKED_CANDIDATES.clear()
        TRACKED_CANDIDATES.update(
            ranked_candidates
        )

        self.print_attrition_report(
            attrition
        )
        tracked_chain_counts = Counter(
            candidate.get("chain", "solana")
            for candidate in ranked_candidates.values()
        )

        print(
            f"Tracking "
            f"{len(TRACKED_CANDIDATES)} "
            f"validated candidates "
            f"("
            + ", ".join(
                f"{chain}={count}"
                for chain, count in sorted(
                    tracked_chain_counts.items()
                )
            )
            + ")"
        )

    async def validate_candidate(
        self,
        address,
        metadata,
        pairs
    ):

        try:

            if is_excluded_contract_address(address):
                return self.rejected_candidate(
                    address,
                    "excluded_suffix"
                )

            if not pairs:
                pending = (
                    self.hyperswap_pending_candidate(
                        address,
                        metadata
                    )
                    or self.solana_pending_candidate(
                        address,
                        metadata
                    )
                )

                if pending:
                    return pending

                return self.rejected_candidate(
                    address,
                    "no_dex_pair"
                )

            pair = pairs[0]
            chain = metadata.get(
                "chain",
                pair.get("chainId", "solana")
            )
            txns = pair.get("txns") or {}
            txns_5m = txns.get("m5") or {}
            volume = pair.get("volume") or {}
            price_change = pair.get("priceChange") or {}

            market = build_market_context(
                pair,
                lifecycle_hint=metadata.get(
                    "lifecycle_hint"
                )
            )

            liquidity = market["liquidity"]

            fdv = market["fdv"]

            pair_created_at = pair.get(
                "pairCreatedAt", 0
            )
	
            if not is_scannable_market(market):
                return self.rejected_candidate(
                    address,
                    self.get_market_rejection_reason(
                        market
                    )
                )

            base_token = pair.get(
                "baseToken",
                {}
            )

            mint_address = base_token.get(
                "address",
                address
            )

            if is_excluded_contract_address(mint_address):
                return self.rejected_candidate(
                    address,
                    "excluded_suffix"
                )

            mint_age = await resolve_mint_age(
                self.session,
                mint_address,
                pair_created_at,
                chain=chain
            )

            if not mint_age:
                return self.rejected_candidate(
                    address,
                    "mint_age_missing"
                )

            if not passes_min_mint_age(
                mint_age,
                chain=chain
            ):
                return self.rejected_candidate(
                    address,
                    "mint_age_under_min"
                )

            memory = TOKEN_MEMORY[address]

            priority_bonus = (
                999999
                if memory["tier"] == 1
                else 0
            )

            score = liquidity + priority_bonus

            symbol = (
                metadata.get("symbol")
                or base_token.get("symbol")
                or "UNKNOWN"
            )

            return {
                "address": address,
                "chain": chain,
                "symbol": symbol,
                "pair_address": pair.get(
                    "pairAddress"
                ),
                "source": metadata["source"],
                "liqd_pool_address": metadata.get(
                    "liqd_pool_address"
                ),
                "liqd_pool_pair": metadata.get(
                    "liqd_pool_pair"
                ),
                "liqd_tvl_usd": metadata.get(
                    "liqd_tvl_usd"
                ),
                "liqd_volume_24h_usd": metadata.get(
                    "liqd_volume_24h_usd"
                ),
                "launchpad": metadata.get(
                    "launchpad"
                ),
                "lifecycle_hint": metadata.get(
                    "lifecycle_hint"
                ),
                "fdv": fdv,
                "lifecycle": market["lifecycle"],
                "liquidity": liquidity,
                "volume_5m": safe_float(
                    volume.get("m5"),
                    0
                ),
                "price_change_5m": safe_float(
                    price_change.get("m5"),
                    0
                ),
                "buys_5m": int(
                    safe_float(
                        txns_5m.get("buys"),
                        0
                    )
                ),
                "sells_5m": int(
                    safe_float(
                        txns_5m.get("sells"),
                        0
                    )
                ),
                "txns_5m": int(
                    safe_float(
                        txns_5m.get("buys"),
                        0
                    )
                    + safe_float(
                        txns_5m.get("sells"),
                        0
                    )
                ),
                "score": score
            }

        except Exception as e:
            print(f"Validation error: {e}")
            return self.rejected_candidate(
                address,
                "validation_error"
            )
