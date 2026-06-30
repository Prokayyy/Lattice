import os
from pathlib import Path


def _load_dotenv():
    path = Path(__file__).resolve().parent / ".env"

    if not path.exists():
        return

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return

    for line in lines:
        text = line.strip()

        if not text or text.startswith("#") or "=" not in text:
            continue

        key, value = text.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")

        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv()


def _env(name, default=""):
    return os.getenv(name, default).strip()


def _env_bool(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name, default=0):
    value = os.getenv(name)
    if not value:
        return default
    return int(value)


def _env_float(name, default=0.0):
    value = os.getenv(name)
    if not value:
        return default
    return float(value)


def _env_list(name, default=""):
    value = _env(name)

    if not value:
        value = default

    return tuple(
        item.strip()
        for item in str(value).replace(";", ",").split(",")
        if item.strip()
    )


def _env_float_tuple(name, default=""):
    return tuple(
        float(item)
        for item in _env_list(name, default)
    )


def _env_scale_out_ladder(name, default):
    value = _env(name)

    if not value:
        return default

    steps = []
    last_target = 0

    for raw_step in value.replace(";", ",").split(","):
        step = raw_step.strip()

        if not step:
            continue

        if ":" in step:
            multiple_text, target_text = step.split(":", 1)
        elif "=" in step:
            multiple_text, target_text = step.split("=", 1)
        else:
            raise ValueError(
                f"{name} step must use multiple:target format: {step}"
            )

        multiple = float(multiple_text.strip())
        target = float(target_text.strip())

        if multiple <= 1:
            raise ValueError(
                f"{name} multiple must be greater than 1: {step}"
            )

        if target <= 0 or target > 1:
            raise ValueError(
                f"{name} target must be between 0 and 1: {step}"
            )

        if target < last_target:
            raise ValueError(
                f"{name} targets must be non-decreasing: {step}"
            )

        steps.append((
            multiple,
            target
        ))
        last_target = target

    if not steps:
        return default

    return tuple(
        sorted(
            steps,
            key=lambda item: item[0]
        )
    )


def _env_fraction_tiers(name, default):
    value = _env(name)

    if not value:
        return default

    steps = []

    for raw_step in value.replace(";", ",").split(","):
        step = raw_step.strip()

        if not step:
            continue

        if ":" in step:
            multiple_text, fraction_text = step.split(":", 1)
        elif "=" in step:
            multiple_text, fraction_text = step.split("=", 1)
        else:
            raise ValueError(
                f"{name} step must use multiple:fraction format: {step}"
            )

        multiple = float(multiple_text.strip())
        fraction = float(fraction_text.strip())

        if multiple <= 1:
            raise ValueError(
                f"{name} multiple must be greater than 1: {step}"
            )

        if fraction <= 0 or fraction > 1:
            raise ValueError(
                f"{name} fraction must be between 0 and 1: {step}"
            )

        steps.append((
            multiple,
            fraction
        ))

    if not steps:
        return default

    return tuple(
        sorted(
            steps,
            key=lambda item: item[0]
        )
    )


def _env_multiple_floor_map(name, default):
    value = _env(name)

    if not value:
        return default

    steps = []
    last_floor = 0

    for raw_step in value.replace(";", ",").split(","):
        step = raw_step.strip()

        if not step:
            continue

        if ":" in step:
            trigger_text, floor_text = step.split(":", 1)
        elif "=" in step:
            trigger_text, floor_text = step.split("=", 1)
        else:
            raise ValueError(
                f"{name} step must use trigger:floor format: {step}"
            )

        trigger = float(trigger_text.strip())
        floor = float(floor_text.strip())

        if trigger <= 1:
            raise ValueError(
                f"{name} trigger must be greater than 1: {step}"
            )

        if floor <= 0:
            raise ValueError(
                f"{name} floor must be positive: {step}"
            )

        if floor < last_floor:
            raise ValueError(
                f"{name} floors must be non-decreasing: {step}"
            )

        steps.append((trigger, floor))
        last_floor = floor

    if not steps:
        return default

    return tuple(sorted(steps, key=lambda item: item[0]))


def _alchemy_rpc_url(env_name, default_host):
    explicit_url = _env(env_name)
    if explicit_url:
        return explicit_url

    api_key = _env("ALCHEMY_API_KEY")
    if not api_key:
        return ""

    return f"https://{default_host}.g.alchemy.com/v2/{api_key}"


# Scanner Settings

# Organic revival alerts are dormant while ignition-only mode is active.
# Re-enable these together with the commented block in main.py if needed.
# ALERT_THRESHOLD = 60

# ALERT_COOLDOWN_SECONDS = 1800

CANDIDATE_REFRESH_INTERVAL = _env_int(
    "CANDIDATE_REFRESH_INTERVAL",
    600
)

MAX_CANDIDATES = 1000

SCANNER_ENABLED_CHAINS = _env_list(
    "SCANNER_ENABLED_CHAINS",
    "solana,base"
)

SCANNER_NOVELTY_ENABLED = _env_bool(
    "SCANNER_NOVELTY_ENABLED",
    True
)

SCANNER_NOVELTY_RECENT_WINDOW_SECONDS = _env_int(
    "SCANNER_NOVELTY_RECENT_WINDOW_SECONDS",
    6 * 3600
)

SCANNER_NOVELTY_REPEAT_THRESHOLD = _env_int(
    "SCANNER_NOVELTY_REPEAT_THRESHOLD",
    2
)

SCANNER_NOVELTY_REPEAT_PENALTY = _env_float(
    "SCANNER_NOVELTY_REPEAT_PENALTY",
    0.25
)

SCANNER_SOURCE_QUOTA_ENABLED = _env_bool(
    "SCANNER_SOURCE_QUOTA_ENABLED",
    True
)

SCANNER_SOURCE_QUOTA_JUPITER = _env_float(
    "SCANNER_SOURCE_QUOTA_JUPITER",
    0.30
)

SCANNER_SOURCE_QUOTA_DEXSCREENER = _env_float(
    "SCANNER_SOURCE_QUOTA_DEXSCREENER",
    0.30
)

SCANNER_SOURCE_QUOTA_PUMPFUN = _env_float(
    "SCANNER_SOURCE_QUOTA_PUMPFUN",
    0.15
)

SCANNER_SOURCE_QUOTA_BSC_NATIVE = _env_float(
    "SCANNER_SOURCE_QUOTA_BSC_NATIVE",
    0.0
)

SCANNER_SOURCE_QUOTA_OTHER = _env_float(
    "SCANNER_SOURCE_QUOTA_OTHER",
    0.05
)

# Evidence-aware discovery ranking. Candidates with real pair data, nonzero 5m
# volume/price movement, and observed 5m flow get priority in TRACKED_CANDIDATES.
# Evidence-blind launchpad/pending tokens are still sampled, but only as a small
# exploration slice so they do not crowd out route-ready tokens during provider
# throttling or broad search noise.
SCANNER_EVIDENCE_RANKING_ENABLED = _env_bool(
    "SCANNER_EVIDENCE_RANKING_ENABLED",
    True
)

SCANNER_EVIDENCE_READY_MIN_SCORE = _env_float(
    "SCANNER_EVIDENCE_READY_MIN_SCORE",
    0.65
)

SCANNER_EVIDENCE_BLIND_MAX_FRACTION = _env_float(
    "SCANNER_EVIDENCE_BLIND_MAX_FRACTION",
    0.10
)

SCANNER_EVIDENCE_SCORE_WEIGHT = _env_float(
    "SCANNER_EVIDENCE_SCORE_WEIGHT",
    0.55
)

SCANNER_BAD_EVIDENCE_MEMORY_ENABLED = _env_bool(
    "SCANNER_BAD_EVIDENCE_MEMORY_ENABLED",
    True
)

SCANNER_BAD_EVIDENCE_MEMORY_WINDOW_SECONDS = _env_int(
    "SCANNER_BAD_EVIDENCE_MEMORY_WINDOW_SECONDS",
    3 * 3600
)

SCANNER_BAD_EVIDENCE_REPEAT_THRESHOLD = _env_int(
    "SCANNER_BAD_EVIDENCE_REPEAT_THRESHOLD",
    2
)

SCANNER_BAD_EVIDENCE_REPEAT_PENALTY = _env_float(
    "SCANNER_BAD_EVIDENCE_REPEAT_PENALTY",
    0.12
)

SCANNER_BAD_EVIDENCE_MAX_PENALTY = _env_float(
    "SCANNER_BAD_EVIDENCE_MAX_PENALTY",
    0.36
)

LIQD_LIQUIDCORE_DISCOVERY_ENABLED = _env_bool(
    "LIQD_LIQUIDCORE_DISCOVERY_ENABLED",
    False
)

LIQD_TOKEN_DISCOVERY_ENABLED = _env_bool(
    "LIQD_TOKEN_DISCOVERY_ENABLED",
    False
)

LIQD_TOKEN_DISCOVERY_LIMIT = _env_int(
    "LIQD_TOKEN_DISCOVERY_LIMIT",
    500
)

LIQD_API_BASE_URL = _env(
    "LIQD_API_BASE_URL",
    "https://api.liqd.ag"
).rstrip("/")

LIQD_LIQUIDCORE_TIMEOUT_SECONDS = _env_float(
    "LIQD_LIQUIDCORE_TIMEOUT_SECONDS",
    12
)

LIQD_LIQUIDCORE_SKIP_ADDRESSES = _env_list(
    "LIQD_LIQUIDCORE_SKIP_ADDRESSES",
    (
        "0x5555555555555555555555555555555555555555,"
        "0xb88339CB7199b77E23DB6E890353E22632Ba630f,"
        "0xB8CE59FC3717ada4C02eaDF9682A9e934F625ebb,"
        "0x111111a1a0667d36bD57c0A9f569b98057111111"
    )
)

LIQD_LIQUIDCORE_SKIP_SYMBOLS = _env_list(
    "LIQD_LIQUIDCORE_SKIP_SYMBOLS",
    "WHYPE,HYPE,USDC,USDT,USDT0,USDH,UBTC,UETH,USOL,KHYPE"
)

HYPERSWAP_SUBGRAPH_DISCOVERY_ENABLED = _env_bool(
    "HYPERSWAP_SUBGRAPH_DISCOVERY_ENABLED",
    False
)

HYPERSWAP_SUBGRAPH_TIMEOUT_SECONDS = _env_float(
    "HYPERSWAP_SUBGRAPH_TIMEOUT_SECONDS",
    12
)

HYPERSWAP_SUBGRAPH_PAIR_LIMIT = _env_int(
    "HYPERSWAP_SUBGRAPH_PAIR_LIMIT",
    100
)

DEXSCREENER_SEARCH_TERMS_OVERRIDE = _env_list(
    "DEXSCREENER_SEARCH_TERMS_OVERRIDE",
    ""
)

DEXSCREENER_SEARCH_TERMS_EXTRA = _env_list(
    "DEXSCREENER_SEARCH_TERMS_EXTRA",
    ""
)

HYPEREVM_DEXSCREENER_SEARCH_TERMS_EXTRA = _env_list(
    "HYPEREVM_DEXSCREENER_SEARCH_TERMS_EXTRA",
    ""
)

BASE_DEXSCREENER_SEARCH_TERMS_EXTRA = _env_list(
    "BASE_DEXSCREENER_SEARCH_TERMS_EXTRA",
    ""
)

DEXSCREENER_SEARCH_TERMS_PER_REFRESH = _env_int(
    "DEXSCREENER_SEARCH_TERMS_PER_REFRESH",
    0
)

BSC_NATIVE_DISCOVERY_ENABLED = _env_bool(
    "BSC_NATIVE_DISCOVERY_ENABLED",
    False
)

BSC_NATIVE_LOOKBACK_BLOCKS = _env_int(
    "BSC_NATIVE_LOOKBACK_BLOCKS",
    1200
)

BSC_NATIVE_RPC_MAX_LOG_BLOCKS = _env_int(
    "BSC_NATIVE_RPC_MAX_LOG_BLOCKS",
    600
)

BSC_NATIVE_MAX_CANDIDATES = _env_int(
    "BSC_NATIVE_MAX_CANDIDATES",
    300
)

BSC_FOURMEME_DISCOVERY_ENABLED = _env_bool(
    "BSC_FOURMEME_DISCOVERY_ENABLED",
    False
)

BSC_FOURMEME_EXCHANGE_ADDRESS = _env(
    "BSC_FOURMEME_EXCHANGE_ADDRESS",
    "0x5c952063c7fc8610ffdb798152d69f0b9550762b"
)

BSC_FOURMEME_MAX_EVENTS = _env_int(
    "BSC_FOURMEME_MAX_EVENTS",
    300
)

BSC_PANCAKE_DISCOVERY_ENABLED = _env_bool(
    "BSC_PANCAKE_DISCOVERY_ENABLED",
    False
)

BSC_PANCAKE_V2_FACTORY_ADDRESS = _env(
    "BSC_PANCAKE_V2_FACTORY_ADDRESS",
    "0xca143ce32fe78f1f7019d7d551a6402fc5350c73"
)

BSC_PANCAKE_MAX_PAIRS = _env_int(
    "BSC_PANCAKE_MAX_PAIRS",
    300
)

HYPERSWAP_V2_SUBGRAPH_URL = _env(
    "HYPERSWAP_V2_SUBGRAPH_URL",
    (
        "https://api.subgraph.ormilabs.com/api/public/"
        "33c67399-d625-4929-b239-5709cd66e422/"
        "subgraphs/hyperswap-v2/v1.0.0/gn"
    )
)

HYPERSWAP_V3_SUBGRAPH_URL = _env(
    "HYPERSWAP_V3_SUBGRAPH_URL",
    (
        "https://api.subgraph.ormilabs.com/api/public/"
        "33c67399-d625-4929-b239-5709cd66e422/"
        "subgraphs/hyperswap-v3/v0.1.2/gn"
    )
)

EXCLUDED_CONTRACT_SUFFIXES = (
    "moon",
)

DEFINED_TOKEN_URL_TEMPLATE = (
    "https://www.defined.fi/{chain_slug}/{address}"
)

DEFINED_CHAIN_SLUGS = {
    "solana": "sol",
    "hyperevm": "hyperevm",
    "ethereum": "eth",
    "base": "base",
    "bsc": "bsc"
}


def defined_chain_slug(chain):

    return DEFINED_CHAIN_SLUGS.get(
        str(chain or "").lower(),
        str(chain or "solana").lower()
    )


def build_defined_token_url(
    address,
    chain="solana",
    pair_address=""
):

    return DEFINED_TOKEN_URL_TEMPLATE.format(
        address=address,
        chain=chain,
        chain_slug=defined_chain_slug(chain),
        pair_address=pair_address
    )

DEXSCREENER_REQUESTS_PER_MINUTE = 150

DEXSCREENER_BATCH_SIZE = 30

DEXSCREENER_BACKOFF_SECONDS = 60

DEXSCREENER_PAIR_CACHE_SECONDS = 8

GECKOTERMINAL_FALLBACK_ENABLED = True

GECKOTERMINAL_REQUESTS_PER_MINUTE = 24

GECKOTERMINAL_BACKOFF_SECONDS = 60

GECKOTERMINAL_RATE_LIMIT_COOLDOWN_SECONDS = _env_int(
    "GECKOTERMINAL_RATE_LIMIT_COOLDOWN_SECONDS",
    900
)

GECKOTERMINAL_RATE_LIMIT_LOG_INTERVAL_SECONDS = _env_int(
    "GECKOTERMINAL_RATE_LIMIT_LOG_INTERVAL_SECONDS",
    300
)

GECKOTERMINAL_5M_ENRICHMENT_ENABLED = _env_bool(
    "GECKOTERMINAL_5M_ENRICHMENT_ENABLED",
    True
)

GECKOTERMINAL_FALLBACK_MAX_PER_BATCH = _env_int(
    "GECKOTERMINAL_FALLBACK_MAX_PER_BATCH",
    2
)

BIRDEYE_API_KEY = _env("BIRDEYE_API_KEY")

BIRDEYE_API_BASE_URL = _env(
    "BIRDEYE_API_BASE_URL",
    "https://public-api.birdeye.so"
)

COINGECKO_API_KEY = _env("COINGECKO_API_KEY")

COINGECKO_API_BASE_URL = _env(
    "COINGECKO_API_BASE_URL",
    "https://api.coingecko.com/api/v3"
)

ALERT_REPORT_OHLCV_REFRESH_ENABLED = _env_bool(
    "ALERT_REPORT_OHLCV_REFRESH_ENABLED",
    False
)

ALERT_REPORT_OHLCV_MAX_PAGES = _env_int(
    "ALERT_REPORT_OHLCV_MAX_PAGES",
    8
)

ALERT_REPORT_OHLCV_MAX_POOL_ADDRESSES = _env_int(
    "ALERT_REPORT_OHLCV_MAX_POOL_ADDRESSES",
    4
)

ALERT_REPORT_OHLCV_MIN_POOL_LIQUIDITY_USD = _env_float(
    "ALERT_REPORT_OHLCV_MIN_POOL_LIQUIDITY_USD",
    500
)

TICKER_LINEAGE_ENABLED = True

TICKER_LINEAGE_LIMIT = 5

TICKER_LINEAGE_MAX_CANDIDATES = 80

TICKER_LINEAGE_CACHE_TTL_SECONDS = 1800

TICKER_LINEAGE_MINT_CONCURRENCY = 8

TICKER_LINEAGE_OVERRIDES_FILE = _env(
    "TICKER_LINEAGE_OVERRIDES_FILE",
    "data/ticker_lineage_overrides.json"
)

HELIUS_API_KEY = _env("HELIUS_API_KEY", "")

HELIUS_LINEAGE_ENABLED = _env_bool(
    "HELIUS_LINEAGE_ENABLED",
    bool(HELIUS_API_KEY)
)

HELIUS_LINEAGE_MAX_PAGES = _env_int("HELIUS_LINEAGE_MAX_PAGES", 3)

ENABLE_ALCHEMY_GRPC = _env_bool("ENABLE_ALCHEMY_GRPC", False)

ALCHEMY_GRPC_ENDPOINT = _env(
    "ALCHEMY_GRPC_ENDPOINT",
    "https://solana-mainnet.g.alchemy.com"
)

ALCHEMY_GRPC_X_TOKEN = _env(
    "ALCHEMY_GRPC_X_TOKEN",
    _env("ALCHEMY_API_KEY")
)

REQUIRE_LIQUIDITY_LOCK = _env_bool(
    "REQUIRE_LIQUIDITY_LOCK",
    True
)

LIQUIDITY_LOCK_MIN_PERCENT = _env_float(
    "LIQUIDITY_LOCK_MIN_PERCENT",
    80.0
)

RUGCHECK_API_BASE_URL = _env(
    "RUGCHECK_API_BASE_URL",
    "https://api.rugcheck.xyz"
)

RUGCHECK_API_KEY = _env("RUGCHECK_API_KEY")

RUGCHECK_API_TIMEOUT_SECONDS = _env_int(
    "RUGCHECK_API_TIMEOUT_SECONDS",
    10
)

RUGCHECK_CACHE_TTL_SECONDS = _env_int(
    "RUGCHECK_CACHE_TTL_SECONDS",
    1800
)

MOBULA_API_BASE_URL = _env(
    "MOBULA_API_BASE_URL",
    "https://api.mobula.io/api/2"
)

MOBULA_API_KEY = _env("MOBULA_API_KEY")

MOBULA_API_TIMEOUT_SECONDS = _env_int(
    "MOBULA_API_TIMEOUT_SECONDS",
    10
)

MOBULA_CACHE_TTL_SECONDS = _env_int(
    "MOBULA_CACHE_TTL_SECONDS",
    1800
)

MOBULA_SAFETY_CHAINS = _env_list(
    "MOBULA_SAFETY_CHAINS",
    ""
)

MOBULA_MIN_BURNED_OR_LOCKED_PERCENT = _env_float(
    "MOBULA_MIN_BURNED_OR_LOCKED_PERCENT",
    80.0
)

# ── BSC honeypot / sell-tax screening (GoPlus Security) ───────────────────────
# BSC's scam density (honeypots, 99% sell tax, blacklist-on-buy) makes a real
# sellability check the highest-value BSC-specific gate. GoPlus has a keyless
# free tier; a key only raises rate limits. Enabled by default but only ever
# consulted for chain == "bsc", so this is inert for the Solana-only bot.
GOPLUS_API_BASE_URL = _env(
    "GOPLUS_API_BASE_URL",
    "https://api.gopluslabs.io"
).rstrip("/")

GOPLUS_API_KEY = _env("GOPLUS_API_KEY")

GOPLUS_API_TIMEOUT_SECONDS = _env_int(
    "GOPLUS_API_TIMEOUT_SECONDS",
    10
)

GOPLUS_CACHE_TTL_SECONDS = _env_int(
    "GOPLUS_CACHE_TTL_SECONDS",
    1800
)

BSC_HONEYPOT_CHECK_ENABLED = _env_bool(
    "BSC_HONEYPOT_CHECK_ENABLED",
    True
)

# Max acceptable buy/sell tax as a fraction (0.10 = 10%). Tokens above the sell
# cap are vetoed as effective honeypots.
BSC_MAX_BUY_TAX = _env_float(
    "BSC_MAX_BUY_TAX",
    0.10
)

BSC_MAX_SELL_TAX = _env_float(
    "BSC_MAX_SELL_TAX",
    0.10
)

LIVE_EXECUTION_ENABLED = _env_bool(
    "LIVE_EXECUTION_ENABLED",
    False
)

LIVE_EXECUTION_QUOTE_CHECK_ENABLED = _env_bool(
    "LIVE_EXECUTION_QUOTE_CHECK_ENABLED",
    False
)

LIVE_EXECUTION_USE_QUOTES_FOR_STOPS = _env_bool(
    "LIVE_EXECUTION_USE_QUOTES_FOR_STOPS",
    False
)

LIVE_EXECUTION_REQUIRE_EXIT_QUOTE_FOR_STOPS = _env_bool(
    "LIVE_EXECUTION_REQUIRE_EXIT_QUOTE_FOR_STOPS",
    False
)

LIVE_EXECUTION_DRY_RUN = _env_bool(
    "LIVE_EXECUTION_DRY_RUN",
    True
)

LIVE_EXECUTION_RETRY_ENABLED = _env_bool(
    "LIVE_EXECUTION_RETRY_ENABLED",
    _env_bool(
        "LIVE_EXECUTION_SELL_RETRY_ENABLED",
        True
    )
)

LIVE_EXECUTION_RETRY_INITIAL_DELAY_SECONDS = _env_float(
    "LIVE_EXECUTION_RETRY_INITIAL_DELAY_SECONDS",
    _env_float(
        "LIVE_EXECUTION_SELL_RETRY_INITIAL_DELAY_SECONDS",
        2.0
    )
)

LIVE_EXECUTION_RETRY_MAX_DELAY_SECONDS = _env_float(
    "LIVE_EXECUTION_RETRY_MAX_DELAY_SECONDS",
    _env_float(
        "LIVE_EXECUTION_SELL_RETRY_MAX_DELAY_SECONDS",
        30.0
    )
)

# Entry-retry chase guard: a buy that failed (e.g. Definitive API error) is
# retried, but only while price is still near the intended entry. If it has run
# more than this fraction past the intended entry price, abort the retry rather
# than chase the top. 0 disables the guard (retry regardless of price).
LIVE_EXECUTION_ENTRY_RETRY_MAX_PRICE_RUN_PCT = _env_float(
    "LIVE_EXECUTION_ENTRY_RETRY_MAX_PRICE_RUN_PCT",
    0.25
)

LIVE_EXECUTION_SELL_RETRY_ENABLED = (
    LIVE_EXECUTION_RETRY_ENABLED
)

LIVE_EXECUTION_SELL_RETRY_INITIAL_DELAY_SECONDS = (
    LIVE_EXECUTION_RETRY_INITIAL_DELAY_SECONDS
)

LIVE_EXECUTION_SELL_RETRY_MAX_DELAY_SECONDS = (
    LIVE_EXECUTION_RETRY_MAX_DELAY_SECONDS
)

LIVE_EXECUTION_TELEGRAM_ENABLED = _env_bool(
    "LIVE_EXECUTION_TELEGRAM_ENABLED",
    True
)

LIVE_EXECUTION_TELEGRAM_CHAT_ID = _env(
    "LIVE_EXECUTION_TELEGRAM_CHAT_ID",
    ""
)

LIVE_EXECUTION_TELEGRAM_CHAT_IDS = _env_list(
    "LIVE_EXECUTION_TELEGRAM_CHAT_IDS",
    LIVE_EXECUTION_TELEGRAM_CHAT_ID
)

LIVE_EXECUTION_NORMAL_SLIPPAGE_BPS = _env_int(
    "LIVE_EXECUTION_NORMAL_SLIPPAGE_BPS",
    500
)

LIVE_EXECUTION_EMERGENCY_SLIPPAGE_BPS = _env_int(
    "LIVE_EXECUTION_EMERGENCY_SLIPPAGE_BPS",
    1500
)

LIVE_EXECUTION_STOP_QUOTE_BUFFER_PCT = _env_float(
    "LIVE_EXECUTION_STOP_QUOTE_BUFFER_PCT",
    0.00
)

LIVE_EXECUTION_STOP_QUOTE_MAX_SPOT_PREMIUM_PCT = _env_float(
    "LIVE_EXECUTION_STOP_QUOTE_MAX_SPOT_PREMIUM_PCT",
    0.02
)

LIVE_EXECUTION_QUOTE_REFRESH_SECONDS = _env_float(
    "LIVE_EXECUTION_QUOTE_REFRESH_SECONDS",
    2.00
)

LIVE_EXECUTION_SOLANA_EXIT_MINT = _env(
    "LIVE_EXECUTION_SOLANA_EXIT_MINT",
    "So11111111111111111111111111111111111111112"
)

LIVE_EXECUTION_SOLANA_EXIT_MINT_DECIMALS = _env_int(
    "LIVE_EXECUTION_SOLANA_EXIT_MINT_DECIMALS",
    9
)

LIVE_EXECUTION_EVM_PROVIDER = _env(
    "LIVE_EXECUTION_EVM_PROVIDER",
    ""
)

# EVM hot-wallet credentials for optional non-Solana live execution.
# These are inert unless the explicit live-execution arming gates allow them.
# secp256k1 private key as 0x-prefixed hex (NOT the base58 Solana Flash key) and
# its 0x address. Empty = EVM live execution stays disabled, on top of the
# DEFINITIVE_ALLOWED_CHAINS / LIVE_EXECUTION_ENABLED arming gates.
LIVE_EXECUTION_EVM_PRIVATE_KEY = _env(
    "LIVE_EXECUTION_EVM_PRIVATE_KEY",
    ""
)

LIVE_EXECUTION_EVM_FUNDER_ADDRESS = _env(
    "LIVE_EXECUTION_EVM_FUNDER_ADDRESS",
    ""
)

LIVE_EXECUTION_EVM_EXIT_TOKEN = _env(
    "LIVE_EXECUTION_EVM_EXIT_TOKEN",
    "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
)

LIVE_EXECUTION_EVM_EXIT_TOKEN_DECIMALS = _env_int(
    "LIVE_EXECUTION_EVM_EXIT_TOKEN_DECIMALS",
    18
)

LIVE_EXECUTION_EVM_DEFAULT_TOKEN_DECIMALS = _env_int(
    "LIVE_EXECUTION_EVM_DEFAULT_TOKEN_DECIMALS",
    18
)

LIVE_EXECUTION_STOP_WATCHDOG_ENABLED = _env_bool(
    "LIVE_EXECUTION_STOP_WATCHDOG_ENABLED",
    True
)

LIVE_EXECUTION_STOP_WATCHDOG_STALE_SECONDS = _env_float(
    "LIVE_EXECUTION_STOP_WATCHDOG_STALE_SECONDS",
    120
)

DEFINITIVE_API_BASE_URL = _env(
    "DEFINITIVE_API_BASE_URL",
    "https://ddp.definitive.fi"
).rstrip("/")

DEFINITIVE_FLASH_API_BASE_URL = _env(
    "DEFINITIVE_FLASH_API_BASE_URL",
    "https://flash.definitive.fi"
).rstrip("/")

DEFINITIVE_API_KEY = _env("DEFINITIVE_API_KEY")

DEFINITIVE_API_SECRET = _env("DEFINITIVE_API_SECRET")

DEFINITIVE_EXECUTION_ENABLED = _env_bool(
    "DEFINITIVE_EXECUTION_ENABLED",
    False
)

DEFINITIVE_EXECUTION_CONFIRM_LIVE = _env_bool(
    "DEFINITIVE_EXECUTION_CONFIRM_LIVE",
    False
)

DEFINITIVE_API_MODE = _env(
    "DEFINITIVE_API_MODE",
    "portfolio"
).lower()

DEFINITIVE_PORTFOLIO_ID = _env("DEFINITIVE_PORTFOLIO_ID")

DEFINITIVE_ALLOWED_CHAINS = _env_list(
    "DEFINITIVE_ALLOWED_CHAINS",
    "solana"
)

DEFINITIVE_SOLANA_CONTRA_ASSET = _env(
    "DEFINITIVE_SOLANA_CONTRA_ASSET",
    "So11111111111111111111111111111111111111112"
)

DEFINITIVE_ETHEREUM_CONTRA_ASSET = _env(
    "DEFINITIVE_ETHEREUM_CONTRA_ASSET",
    "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
)

DEFINITIVE_BASE_CONTRA_ASSET = _env(
    "DEFINITIVE_BASE_CONTRA_ASSET",
    "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
)

DEFINITIVE_HYPEREVM_CONTRA_ASSET = _env(
    "DEFINITIVE_HYPEREVM_CONTRA_ASSET"
)

# BSC contra/cash leg. Defaults to WBNB (the canonical wrapped gas token, the
# way Solana uses WSOL) since most PancakeSwap memecoin pairs route through it;
# override to BSC-USDT (0x55d398326f99059fF775485246999027B3197955) if desired.
DEFINITIVE_BSC_CONTRA_ASSET = _env(
    "DEFINITIVE_BSC_CONTRA_ASSET",
    "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c"
)

DEFINITIVE_DEFAULT_CONTRA_ASSET = _env(
    "DEFINITIVE_DEFAULT_CONTRA_ASSET"
)

DEFINITIVE_MIRROR_PAPER_POSITION_SIZE = _env_bool(
    "DEFINITIVE_MIRROR_PAPER_POSITION_SIZE",
    True
)

DEFINITIVE_MIN_ENTRY_NOTIONAL_USD = _env_float(
    "DEFINITIVE_MIN_ENTRY_NOTIONAL_USD",
    5
)

DEFINITIVE_MAX_ENTRY_NOTIONAL_USD = _env_float(
    "DEFINITIVE_MAX_ENTRY_NOTIONAL_USD",
    20
)

DEFINITIVE_MAX_ACCOUNT_EXPOSURE_USD = _env_float(
    "DEFINITIVE_MAX_ACCOUNT_EXPOSURE_USD",
    80
)

DEFINITIVE_MAX_OPEN_POSITIONS = _env_int(
    "DEFINITIVE_MAX_OPEN_POSITIONS",
    3
)

DEFINITIVE_QUOTE_BEFORE_SUBMIT = _env_bool(
    "DEFINITIVE_QUOTE_BEFORE_SUBMIT",
    False
)

DEFINITIVE_ABORT_ON_QUOTE_WARNINGS = _env_bool(
    "DEFINITIVE_ABORT_ON_QUOTE_WARNINGS",
    False
)

DEFINITIVE_MAX_PRICE_IMPACT = _env_float(
    "DEFINITIVE_MAX_PRICE_IMPACT",
    0.15
)

DEFINITIVE_ENTRY_MAX_PRICE_IMPACT = _env_float(
    "DEFINITIVE_ENTRY_MAX_PRICE_IMPACT",
    DEFINITIVE_MAX_PRICE_IMPACT
)

DEFINITIVE_EXIT_MAX_PRICE_IMPACT = _env_float(
    "DEFINITIVE_EXIT_MAX_PRICE_IMPACT",
    max(DEFINITIVE_MAX_PRICE_IMPACT, 0.25)
)

DEFINITIVE_SLIPPAGE_TOLERANCE = _env(
    "DEFINITIVE_SLIPPAGE_TOLERANCE",
    "0.15"
)

DEFINITIVE_SECONDS_TO_EXPIRE = _env_int(
    "DEFINITIVE_SECONDS_TO_EXPIRE",
    10
)

DEFINITIVE_ENTRY_SECONDS_TO_EXPIRE = _env_int(
    "DEFINITIVE_ENTRY_SECONDS_TO_EXPIRE",
    max(DEFINITIVE_SECONDS_TO_EXPIRE, 60)
)

DEFINITIVE_USE_DISPLAY_ASSET_PRICE = _env_bool(
    "DEFINITIVE_USE_DISPLAY_ASSET_PRICE",
    True
)

DEFINITIVE_SELL_ORDER_ENDPOINT = _env(
    "DEFINITIVE_SELL_ORDER_ENDPOINT",
    "trade"
).lower()

DEFINITIVE_SELL_CONFIRM_FILL_SECONDS = _env_float(
    "DEFINITIVE_SELL_CONFIRM_FILL_SECONDS",
    30
)

DEFINITIVE_ENTRY_CONFIRM_FILL_SECONDS = _env_float(
    "DEFINITIVE_ENTRY_CONFIRM_FILL_SECONDS",
    30
)

DEFINITIVE_SUBMIT_MAX_ATTEMPTS = _env_int(
    "DEFINITIVE_SUBMIT_MAX_ATTEMPTS",
    2
)

DEFINITIVE_SUBMIT_RETRY_DELAY_SECONDS = _env_float(
    "DEFINITIVE_SUBMIT_RETRY_DELAY_SECONDS",
    1
)

DEFINITIVE_INITIAL_TAKE_PROFIT_ENABLED = _env_bool(
    "DEFINITIVE_INITIAL_TAKE_PROFIT_ENABLED",
    True
)

DEFINITIVE_INITIAL_TAKE_PROFIT_MULTIPLE = _env_float(
    "DEFINITIVE_INITIAL_TAKE_PROFIT_MULTIPLE",
    2.00
)

DEFINITIVE_INITIAL_TAKE_PROFIT_RECOVERY_PCT = _env_float(
    "DEFINITIVE_INITIAL_TAKE_PROFIT_RECOVERY_PCT",
    1.00
)

DEFINITIVE_INITIAL_TAKE_PROFIT_MIN_NOTIONAL_USD = _env_float(
    "DEFINITIVE_INITIAL_TAKE_PROFIT_MIN_NOTIONAL_USD",
    5
)

DEFINITIVE_FLASH_API_KEY = _env("DEFINITIVE_FLASH_API_KEY")

DEFINITIVE_FLASH_ENABLED = _env_bool(
    "DEFINITIVE_FLASH_ENABLED",
    False
)

DEFINITIVE_FLASH_FUNDER_ADDRESS = _env("DEFINITIVE_FLASH_FUNDER_ADDRESS")

DEFINITIVE_FLASH_PRIVATE_KEY = _env("DEFINITIVE_FLASH_PRIVATE_KEY")

DEFINITIVE_FLASH_MAX_SLIPPAGE = _env_float(
    "DEFINITIVE_FLASH_MAX_SLIPPAGE",
    0.15
)

DEFINITIVE_FLASH_MAX_PRICE_IMPACT = _env_float(
    "DEFINITIVE_FLASH_MAX_PRICE_IMPACT",
    0.15
)

DEFINITIVE_FLASH_CONFIRM_FILL_SECONDS = _env_float(
    "DEFINITIVE_FLASH_CONFIRM_FILL_SECONDS",
    30
)

DEFINITIVE_FLASH_SUBMIT_MAX_ATTEMPTS = _env_int(
    "DEFINITIVE_FLASH_SUBMIT_MAX_ATTEMPTS",
    2
)

DEFINITIVE_FLASH_SUBMIT_RETRY_DELAY_SECONDS = _env_float(
    "DEFINITIVE_FLASH_SUBMIT_RETRY_DELAY_SECONDS",
    1
)

DEFINITIVE_FLASH_WRAP_SETTLE_SECONDS = _env_float(
    "DEFINITIVE_FLASH_WRAP_SETTLE_SECONDS",
    8
)

# ─── Flash on-chain SL/TP (bracket) — scaffolding for the QuickTrade->Flash
# migration. These are placeholders; the entry/bracket wiring is built once the
# exit-model design (full-bracket vs hybrid) is confirmed. CONFIRM_LIVE is a
# second arming gate (like DEFINITIVE_EXECUTION_CONFIRM_LIVE) so nothing trades
# until explicitly armed. SL/TP define the on-chain bracket levels.
DEFINITIVE_FLASH_CONFIRM_LIVE = _env_bool(
    "DEFINITIVE_FLASH_CONFIRM_LIVE",
    False
)

DEFINITIVE_FLASH_ALLOWED_CHAINS = _env_list(
    "DEFINITIVE_FLASH_ALLOWED_CHAINS",
    "solana"
)

# Use a single bracket order (entry + SL + TP) per position.
DEFINITIVE_FLASH_USE_BRACKET = _env_bool(
    "DEFINITIVE_FLASH_USE_BRACKET",
    True
)

# On-chain hard stop: sell when price falls to entry*(1-PCT).
DEFINITIVE_FLASH_STOP_LOSS_PCT = _env_float(
    "DEFINITIVE_FLASH_STOP_LOSS_PCT",
    0.30
)

# On-chain take-profit: sell when price rises to entry*MULTIPLE.
DEFINITIVE_FLASH_TAKE_PROFIT_MULTIPLE = _env_float(
    "DEFINITIVE_FLASH_TAKE_PROFIT_MULTIPLE",
    2.0
)

# ─── Flash on-chain catastrophe stop (Phase 2 — LOCKED hybrid design) ─────────
# Supersedes the bracket scaffolding above (NOT a bracket). After a Flash entry
# fills, a single coarse on-chain STOP (sell) backstop is placed, mapped to the
# per-route initial_stop_loss_pct (PositionEngine.initial_stop_loss_pct) — NOT
# the flat DEFINITIVE_FLASH_STOP_LOSS_PCT. Bot-managed exits stay primary; this
# only fires on a crash or if the bot dies. It ratchets up with the peak via
# cancel-and-replace (Flash has no modify). Triple-gated: needs live submit
# armed AND this flag AND Flash credentials, so it is dormant until go-live.
DEFINITIVE_FLASH_ONCHAIN_STOP_ENABLED = _env_bool(
    "DEFINITIVE_FLASH_ONCHAIN_STOP_ENABLED",
    False
)

# Flash orderType for the backstop sell. "stop-loss" = sell on a lower trigger.
# Valid OrderType enum values: market|limit|twap|stop|stop-loss|take-profit|
# bracket. Both "stop-loss" and "stop" work with a single "lower" trigger.
DEFINITIVE_FLASH_ONCHAIN_STOP_ORDER_TYPE = _env(
    "DEFINITIVE_FLASH_ONCHAIN_STOP_ORDER_TYPE",
    "stop-loss"
)

# Coarse ratchet: only cancel-and-replace the resting stop when the new trigger
# is at least this fraction above the current one (avoids churn / rate limits).
DEFINITIVE_FLASH_ONCHAIN_STOP_RATCHET_MIN_PCT = _env_float(
    "DEFINITIVE_FLASH_ONCHAIN_STOP_RATCHET_MIN_PCT",
    0.10
)

DEFINITIVE_FLASH_ONCHAIN_STOP_RETRY_SECONDS = _env_float(
    "DEFINITIVE_FLASH_ONCHAIN_STOP_RETRY_SECONDS",
    30
)

# ── Flash resting take-profit ladder: one take-profit (upper-trigger) order
# per scale-out rung, placed right after a Flash entry fills, so profits trigger
# server-side between scans instead of waiting for the bot's poll. Flash
# brackets are not yet supported, so the protective stop stays with the onchain
# stop (DEFINITIVE_FLASH_ONCHAIN_STOP_ENABLED) -- enable BOTH. Dormant by default.
DEFINITIVE_FLASH_RESTING_EXITS_ENABLED = _env_bool(
    "DEFINITIVE_FLASH_RESTING_EXITS_ENABLED",
    False
)

LATTICE_LIVE_ENTRIES_ENABLED = _env_bool(
    "LATTICE_LIVE_ENTRIES_ENABLED",
    True
)

LATTICE_LIVE_MAX_OPEN_POSITIONS = _env_int(
    "LATTICE_LIVE_MAX_OPEN_POSITIONS",
    3
)

# Main scanner (main.py) live-execution gate. Default False so discovery/
# live_runner is the SOLE live trader (one bot per wallet); main.py runs
# paper + alerts only. Set true to also let the scanner trade live.
SCANNER_LIVE_EXECUTION_ENABLED = _env_bool(
    "SCANNER_LIVE_EXECUTION_ENABLED",
    False
)

LATTICE_LIVE_HARD_STOP_LOSS_PCT = _env_float(
    "LATTICE_LIVE_HARD_STOP_LOSS_PCT",
    0.30
)

LATTICE_OPEN_POSITION_MONITOR_INTERVAL_SECONDS = _env_float(
    "LATTICE_OPEN_POSITION_MONITOR_INTERVAL_SECONDS",
    2.0
)

LATTICE_LIVE_EXIT_PENDING_ALERT_COOLDOWN_SECONDS = _env_float(
    "LATTICE_LIVE_EXIT_PENDING_ALERT_COOLDOWN_SECONDS",
    300
)

LATTICE_RUNNER_STALE_SECONDS = _env_float(
    "LATTICE_RUNNER_STALE_SECONDS",
    180
)

LATTICE_SUPERVISOR_CHECK_SECONDS = _env_float(
    "LATTICE_SUPERVISOR_CHECK_SECONDS",
    15
)

LATTICE_SUPERVISOR_RESTART_DELAY_SECONDS = _env_float(
    "LATTICE_SUPERVISOR_RESTART_DELAY_SECONDS",
    5
)

LATTICE_SUPERVISOR_ALERT_COOLDOWN_SECONDS = _env_float(
    "LATTICE_SUPERVISOR_ALERT_COOLDOWN_SECONDS",
    300
)

HYPEREVM_CHAIN_ID = "hyperevm"

GRPC_RESUBSCRIBE_INTERVAL = 60

GRPC_MAX_WATCH_ACCOUNTS = 300

GRPC_IMMEDIATE_SCAN_COOLDOWN_SECONDS = 20

MAX_FDV_USD = 60000

MIN_LIQUIDITY_USD = 5000

MAX_LIQUIDITY_USD = 30000

HYPEREVM_SCANNER_MIN_LIQUIDITY_USD = _env_float(
    "HYPEREVM_SCANNER_MIN_LIQUIDITY_USD",
    1000
)

HYPEREVM_SCANNER_MAX_FDV_USD = _env_float(
    "HYPEREVM_SCANNER_MAX_FDV_USD",
    500000
)

# Base uses the generic migrated-token gate unless chain-specific evidence
# justifies different FDV/liquidity bounds.

MIN_TOKEN_AGE_HOURS = 4

REQUIRE_MINT_AGE = True

MINT_AGE_CACHE_TTL_SECONDS = _env_int(
    "MINT_AGE_CACHE_TTL_SECONDS",
    6 * 3600
)

MINT_AGE_RPC_PAGE_LIMIT = _env_int(
    "MINT_AGE_RPC_PAGE_LIMIT",
    1000
)

MINT_AGE_RPC_MAX_PAGES = _env_int(
    "MINT_AGE_RPC_MAX_PAGES",
    3
)

MINT_AGE_RPC_GENESIS_MAX_PAGES = _env_int(
    "MINT_AGE_RPC_GENESIS_MAX_PAGES",
    100
)

# Stop the normal gate check as soon as we prove the mint is old enough.
# Lineage/OG lookups pass walk_to_genesis=True and still page to the first
# signature when they need true creation time.
MINT_AGE_EARLY_EXIT_HOURS = _env_float(
    "MINT_AGE_EARLY_EXIT_HOURS",
    MIN_TOKEN_AGE_HOURS
)

IGNITION_ALERT_THRESHOLD = 45

IGNITION_ALERT_COOLDOWN_SECONDS = 3600  # per-contract, 1 hour

IGNITION_RECALL_OVERRIDE_ENABLED = _env_bool(
    "IGNITION_RECALL_OVERRIDE_ENABLED",
    True
)

IGNITION_RECALL_OVERRIDE_MIN_SECONDS = _env_int(
    "IGNITION_RECALL_OVERRIDE_MIN_SECONDS",
    300
)

IGNITION_RECALL_OVERRIDE_VOLUME_MULTIPLE = _env_float(
    "IGNITION_RECALL_OVERRIDE_VOLUME_MULTIPLE",
    3.00
)

IGNITION_RECALL_OVERRIDE_PRICE_MULTIPLE = _env_float(
    "IGNITION_RECALL_OVERRIDE_PRICE_MULTIPLE",
    2.00
)

IGNITION_RECALL_OVERRIDE_PRICE_STEP = _env_float(
    "IGNITION_RECALL_OVERRIDE_PRICE_STEP",
    0.50
)

IGNITION_STATE_FILE = "data/ignition_calls.json"

POSITION_ENABLED = True

POSITION_TELEGRAM_ENABLED = True

POSITION_STATUS_REPORTS_ENABLED = True

POSITION_STATUS_REPORT_INTERVAL_SECONDS = 1800

ALERT_PERFORMANCE_SUMMARY_INTERVAL_SECONDS = _env_int(
    "ALERT_PERFORMANCE_SUMMARY_INTERVAL_SECONDS",
    4 * 3600
)

LATTICE_ALERT_LIST_ENABLED = _env_bool(
    "LATTICE_ALERT_LIST_ENABLED",
    True
)

LATTICE_ALERT_LIST_INTERVAL_SECONDS = _env_int(
    "LATTICE_ALERT_LIST_INTERVAL_SECONDS",
    4 * 3600
)

LATTICE_ALERT_LIST_MAX_ITEMS = _env_int(
    "LATTICE_ALERT_LIST_MAX_ITEMS",
    30
)

ROUTE_OUTCOME_SCORING_ENABLED = _env_bool(
    "ROUTE_OUTCOME_SCORING_ENABLED",
    True
)

ROUTE_OUTCOME_LOOKBACK_DAYS = _env_float(
    "ROUTE_OUTCOME_LOOKBACK_DAYS",
    7
)

ROUTE_OUTCOME_WINDOW_SECONDS = _env_int(
    "ROUTE_OUTCOME_WINDOW_SECONDS",
    3600
)

ROUTE_OUTCOME_MIN_ALERTS = _env_int(
    "ROUTE_OUTCOME_MIN_ALERTS",
    10
)

ROUTE_OUTCOME_APPLY_MIN_ALERTS = _env_int(
    "ROUTE_OUTCOME_APPLY_MIN_ALERTS",
    30
)

ROUTE_OUTCOME_MAX_BONUS = _env_float(
    "ROUTE_OUTCOME_MAX_BONUS",
    8
)

ROUTE_OUTCOME_MAX_PENALTY = _env_float(
    "ROUTE_OUTCOME_MAX_PENALTY",
    12
)

ROUTE_OUTCOME_FALSE_POSITIVE_PENALTY_SCALE = _env_float(
    "ROUTE_OUTCOME_FALSE_POSITIVE_PENALTY_SCALE",
    8
)

ROUTE_OUTCOME_CACHE_SECONDS = _env_int(
    "ROUTE_OUTCOME_CACHE_SECONDS",
    300
)

PRIORITY_SCANNER_ENABLED = _env_bool(
    "PRIORITY_SCANNER_ENABLED",
    True
)

PRIORITY_SCANNER_MIN_SCORE = _env_int(
    "PRIORITY_SCANNER_MIN_SCORE",
    35
)

PRIORITY_SCANNER_MIN_PRESSURE = _env_float(
    "PRIORITY_SCANNER_MIN_PRESSURE",
    60
)

PRIORITY_SCANNER_MIN_VOLUME_LIQUIDITY_RATIO = _env_float(
    "PRIORITY_SCANNER_MIN_VOLUME_LIQUIDITY_RATIO",
    0.50
)

PRIORITY_SCANNER_MIN_BUY_SELL_VOLUME_RATIO = _env_float(
    "PRIORITY_SCANNER_MIN_BUY_SELL_VOLUME_RATIO",
    1.10
)

PRIORITY_SCANNER_COOLDOWN_SECONDS = _env_int(
    "PRIORITY_SCANNER_COOLDOWN_SECONDS",
    20
)

PRIORITY_SCANNER_MAX_QUEUE = _env_int(
    "PRIORITY_SCANNER_MAX_QUEUE",
    300
)

SCAN_GATE_ATTRITION_REPORT_ENABLED = _env_bool(
    "SCAN_GATE_ATTRITION_REPORT_ENABLED",
    True
)

SCAN_GATE_ATTRITION_REPORT_INTERVAL_SECONDS = _env_int(
    "SCAN_GATE_ATTRITION_REPORT_INTERVAL_SECONDS",
    300
)

SCAN_GATE_ATTRITION_REPORT_TOP_N = _env_int(
    "SCAN_GATE_ATTRITION_REPORT_TOP_N",
    12
)

LATTICE_ENTRY_DECISION_LOG_ENABLED = _env_bool(
    "LATTICE_ENTRY_DECISION_LOG_ENABLED",
    True
)

LATTICE_ENTRY_DECISION_LOG_PATH = _env(
    "LATTICE_ENTRY_DECISION_LOG_PATH",
    "discovery/entry_decisions.jsonl"
)

CTO_METADATA_ALERTS_ENABLED = _env_bool(
    "CTO_METADATA_ALERTS_ENABLED",
    True
)

CTO_METADATA_SCORE_BONUS = _env_int(
    "CTO_METADATA_SCORE_BONUS",
    10
)

CTO_METADATA_MIN_BASE_SCORE = _env_int(
    "CTO_METADATA_MIN_BASE_SCORE",
    35
)

CTO_METADATA_MIN_PRESSURE = _env_float(
    "CTO_METADATA_MIN_PRESSURE",
    45
)

CTO_METADATA_MIN_VOLUME_LIQUIDITY_RATIO = _env_float(
    "CTO_METADATA_MIN_VOLUME_LIQUIDITY_RATIO",
    0.25
)

CTO_METADATA_MIN_BUY_SELL_VOLUME_RATIO = _env_float(
    "CTO_METADATA_MIN_BUY_SELL_VOLUME_RATIO",
    1.05
)

CTO_METADATA_ALERT_COOLDOWN_SECONDS = _env_int(
    "CTO_METADATA_ALERT_COOLDOWN_SECONDS",
    3600
)

TELEGRAM_AGENT_ALERT_REFRESH_ENABLED = _env_bool(
    "TELEGRAM_AGENT_ALERT_REFRESH_ENABLED",
    True
)

TELEGRAM_AGENT_ALERT_REFRESH_MAX_TOKENS = _env_int(
    "TELEGRAM_AGENT_ALERT_REFRESH_MAX_TOKENS",
    120
)

TELEGRAM_AGENT_ALERT_OHLCV_REFRESH_ENABLED = _env_bool(
    "TELEGRAM_AGENT_ALERT_OHLCV_REFRESH_ENABLED",
    True
)

TELEGRAM_AGENT_ALERT_OHLCV_MAX_PAGES = _env_int(
    "TELEGRAM_AGENT_ALERT_OHLCV_MAX_PAGES",
    3
)

LLM_PATTERN_REPORTS_ENABLED = _env_bool(
    "LLM_PATTERN_REPORTS_ENABLED",
    True
)

LLM_PROVIDER = _env(
    "LLM_PROVIDER",
    "deepseek"
)

LLM_API_KEY = _env(
    "LLM_API_KEY",
    _env("DEEPSEEK_API_KEY")
)

LLM_API_BASE_URL = _env(
    "LLM_API_BASE_URL",
    "https://api.deepseek.com"
)

LLM_MODEL = _env(
    "LLM_MODEL",
    "deepseek-v4-flash"
)

LLM_PATTERN_REPORT_INTERVAL_SECONDS = _env_int(
    "LLM_PATTERN_REPORT_INTERVAL_SECONDS",
    7200
)

LLM_PATTERN_REPORT_LOOKBACK_HOURS = _env_int(
    "LLM_PATTERN_REPORT_LOOKBACK_HOURS",
    24
)

LLM_PATTERN_REPORT_MIN_ALERTS = _env_int(
    "LLM_PATTERN_REPORT_MIN_ALERTS",
    5
)

LLM_PATTERN_REPORT_MAX_ALERTS = _env_int(
    "LLM_PATTERN_REPORT_MAX_ALERTS",
    60
)

LLM_PATTERN_REPORT_TIMEOUT_SECONDS = _env_int(
    "LLM_PATTERN_REPORT_TIMEOUT_SECONDS",
    45
)

LLM_PATTERN_REPORT_MAX_TOKENS = _env_int(
    "LLM_PATTERN_REPORT_MAX_TOKENS",
    900
)

POSITION_STATE_FILE = "data/position_state.json"

POSITION_OPEN_POSITION_SCAN_INTERVAL_SECONDS = _env_float(
    "POSITION_OPEN_POSITION_SCAN_INTERVAL_SECONDS",
    2.0
)

POSITION_INITIAL_BALANCE_SOL = _env_float(
    "POSITION_INITIAL_BALANCE_SOL",
    100.00
)

POSITION_MIN_ENTRY_FDV_USD = _env_float(
    "POSITION_MIN_ENTRY_FDV_USD",
    3000
)

POSITION_MIN_ENTRY_PRICE_CHANGE_1H = _env_float(
    "POSITION_MIN_ENTRY_PRICE_CHANGE_1H",
    30
)

POSITION_EARLY_REVIVAL_MIN_ENTRY_PRICE_CHANGE_1H = _env_float(
    "POSITION_EARLY_REVIVAL_MIN_ENTRY_PRICE_CHANGE_1H",
    10
)

POSITION_MIGRATED_REVIVAL_MIN_ENTRY_PRICE_CHANGE_1H = _env_float(
    "POSITION_MIGRATED_REVIVAL_MIN_ENTRY_PRICE_CHANGE_1H",
    POSITION_EARLY_REVIVAL_MIN_ENTRY_PRICE_CHANGE_1H
)

POSITION_MIN_ENTRY_PRICE_CHANGE_5M = _env_float(
    "POSITION_MIN_ENTRY_PRICE_CHANGE_5M",
    2
)

POSITION_EARLY_REVIVAL_MIN_ENTRY_PRICE_CHANGE_5M = _env_float(
    "POSITION_EARLY_REVIVAL_MIN_ENTRY_PRICE_CHANGE_5M",
    POSITION_MIN_ENTRY_PRICE_CHANGE_5M
)

POSITION_MIGRATED_REVIVAL_MIN_ENTRY_PRICE_CHANGE_5M = _env_float(
    "POSITION_MIGRATED_REVIVAL_MIN_ENTRY_PRICE_CHANGE_5M",
    POSITION_EARLY_REVIVAL_MIN_ENTRY_PRICE_CHANGE_5M
)

POSITION_MAX_ENTRY_PRICE_CHANGE_5M = _env_float(
    "POSITION_MAX_ENTRY_PRICE_CHANGE_5M",
    20
)

POSITION_MIN_ENTRY_IMPULSE = _env_float(
    "POSITION_MIN_ENTRY_IMPULSE",
    0.90
)

POSITION_MAX_ENTRY_IMPULSE = _env_float(
    "POSITION_MAX_ENTRY_IMPULSE",
    1.80
)

POSITION_MAX_ENTRY_PENALTY = _env_float(
    "POSITION_MAX_ENTRY_PENALTY",
    10
)

POSITION_EARLY_REVIVAL_MAX_ENTRY_PENALTY = _env_float(
    "POSITION_EARLY_REVIVAL_MAX_ENTRY_PENALTY",
    20
)

POSITION_MIGRATED_REVIVAL_MAX_ENTRY_PENALTY = _env_float(
    "POSITION_MIGRATED_REVIVAL_MAX_ENTRY_PENALTY",
    12
)

POSITION_HC_MAX_ENTRY_PENALTY = _env_float(
    "POSITION_HC_MAX_ENTRY_PENALTY",
    10
)

POSITION_EARLY_REVIVAL_MIN_ENTRY_SCORE = _env_float(
    "POSITION_EARLY_REVIVAL_MIN_ENTRY_SCORE",
    45
)

# Early-revival entries are capped to lower FDV — above this, the setup is no
# longer an "early" revival. Tokens above it must qualify on another route.
POSITION_EARLY_REVIVAL_MAX_ENTRY_FDV_USD = _env_float(
    "POSITION_EARLY_REVIVAL_MAX_ENTRY_FDV_USD",
    20000
)

POSITION_MIGRATED_REVIVAL_MIN_ENTRY_SCORE = _env_float(
    "POSITION_MIGRATED_REVIVAL_MIN_ENTRY_SCORE",
    POSITION_EARLY_REVIVAL_MIN_ENTRY_SCORE
)

POSITION_IMMEDIATE_MIN_ENTRY_SCORE = _env_float(
    "POSITION_IMMEDIATE_MIN_ENTRY_SCORE",
    55
)

POSITION_MIN_ENTRY_VOLUME_1H_USD = _env_float(
    "POSITION_MIN_ENTRY_VOLUME_1H_USD",
    20000
)

POSITION_EARLY_REVIVAL_MIN_ENTRY_VOLUME_1H_USD = _env_float(
    "POSITION_EARLY_REVIVAL_MIN_ENTRY_VOLUME_1H_USD",
    5000
)

POSITION_MIGRATED_REVIVAL_MIN_ENTRY_VOLUME_1H_USD = _env_float(
    "POSITION_MIGRATED_REVIVAL_MIN_ENTRY_VOLUME_1H_USD",
    POSITION_EARLY_REVIVAL_MIN_ENTRY_VOLUME_1H_USD
)

POSITION_QUALITY_VOLUME_GATE_ENABLED = _env_bool(
    "POSITION_QUALITY_VOLUME_GATE_ENABLED",
    True
)

POSITION_MIN_ENTRY_VOLUME_MULTIPLE = _env_float(
    "POSITION_MIN_ENTRY_VOLUME_MULTIPLE",
    3.00
)

POSITION_EARLY_REVIVAL_MIN_ENTRY_VOLUME_MULTIPLE = _env_float(
    "POSITION_EARLY_REVIVAL_MIN_ENTRY_VOLUME_MULTIPLE",
    1.5
)

POSITION_MIGRATED_REVIVAL_MIN_ENTRY_VOLUME_MULTIPLE = _env_float(
    "POSITION_MIGRATED_REVIVAL_MIN_ENTRY_VOLUME_MULTIPLE",
    POSITION_EARLY_REVIVAL_MIN_ENTRY_VOLUME_MULTIPLE
)

POSITION_FULL_SIZE_VOLUME_MULTIPLE = _env_float(
    "POSITION_FULL_SIZE_VOLUME_MULTIPLE",
    5.00
)

POSITION_MID_VOLUME_CONFIRM_ENABLED = _env_bool(
    "POSITION_MID_VOLUME_CONFIRM_ENABLED",
    True
)

POSITION_MID_VOLUME_MIN_PRESSURE = _env_float(
    "POSITION_MID_VOLUME_MIN_PRESSURE",
    55
)

POSITION_MID_VOLUME_MIN_VOLUME_LIQUIDITY_RATIO = _env_float(
    "POSITION_MID_VOLUME_MIN_VOLUME_LIQUIDITY_RATIO",
    0.50
)

POSITION_MID_VOLUME_MIN_BUY_SELL_RATIO = _env_float(
    "POSITION_MID_VOLUME_MIN_BUY_SELL_RATIO",
    1.00
)

POSITION_MIN_ENTRY_BUY_SELL_VOLUME_RATIO = _env_float(
    "POSITION_MIN_ENTRY_BUY_SELL_VOLUME_RATIO",
    1.10
)

POSITION_REQUIRE_OBSERVED_BUY_SELL_VOLUME = _env_bool(
    "POSITION_REQUIRE_OBSERVED_BUY_SELL_VOLUME",
    True
)

POSITION_ENTRY_CONFIRMATION_ENABLED = _env_bool(
    "POSITION_ENTRY_CONFIRMATION_ENABLED",
    True
)

POSITION_ENTRY_CONFIRMATION_SHADOW_MODE = _env_bool(
    "POSITION_ENTRY_CONFIRMATION_SHADOW_MODE",
    True
)

POSITION_ENTRY_CONFIRMATION_MIN_SCORE = _env_float(
    "POSITION_ENTRY_CONFIRMATION_MIN_SCORE",
    70
)

POSITION_ENTRY_CONFIRMATION_REQUIRED_SCANS = _env_int(
    "POSITION_ENTRY_CONFIRMATION_REQUIRED_SCANS",
    2
)

POSITION_ENTRY_CONFIRMATION_WATCH_SECONDS = _env_int(
    "POSITION_ENTRY_CONFIRMATION_WATCH_SECONDS",
    600
)

POSITION_ENTRY_CONFIRMATION_MIN_PRESSURE = _env_float(
    "POSITION_ENTRY_CONFIRMATION_MIN_PRESSURE",
    50
)

POSITION_ENTRY_CONFIRMATION_MIN_VOLUME_LIQUIDITY_RATIO = _env_float(
    "POSITION_ENTRY_CONFIRMATION_MIN_VOLUME_LIQUIDITY_RATIO",
    0.35
)

POSITION_ENTRY_CONFIRMATION_MAX_VOLUME_LIQUIDITY_RATIO = _env_float(
    "POSITION_ENTRY_CONFIRMATION_MAX_VOLUME_LIQUIDITY_RATIO",
    6.00
)

POSITION_ENTRY_CONFIRMATION_MIN_BUY_SELL_RATIO = _env_float(
    "POSITION_ENTRY_CONFIRMATION_MIN_BUY_SELL_RATIO",
    1.10
)

POSITION_ENTRY_CONFIRMATION_MIN_BUY_VOLUME_5M_USD = _env_float(
    "POSITION_ENTRY_CONFIRMATION_MIN_BUY_VOLUME_5M_USD",
    500
)

POSITION_ENTRY_CONFIRMATION_MAX_VWAP_DISTANCE_PCT = _env_float(
    "POSITION_ENTRY_CONFIRMATION_MAX_VWAP_DISTANCE_PCT",
    0.35
)

POSITION_ENTRY_CONFIRMATION_MIN_PRICE_CHANGE_5M = _env_float(
    "POSITION_ENTRY_CONFIRMATION_MIN_PRICE_CHANGE_5M",
    0
)

POSITION_ENTRY_CONFIRMATION_MIN_PRICE_CHANGE_1H = _env_float(
    "POSITION_ENTRY_CONFIRMATION_MIN_PRICE_CHANGE_1H",
    0
)

POSITION_REENTRY_MIN_VOLUME_5M_USD = _env_float(
    "POSITION_REENTRY_MIN_VOLUME_5M_USD",
    1500
)

POSITION_REENTRY_COOLDOWN_SECONDS = _env_int(
    "POSITION_REENTRY_COOLDOWN_SECONDS",
    300
)

POSITION_REENTRY_POSITION_SIZE_MULTIPLIER = _env_float(
    "POSITION_REENTRY_POSITION_SIZE_MULTIPLIER",
    0.50
)

POSITION_REENTRY_STATE_FILTER_ENABLED = _env_bool(
    "POSITION_REENTRY_STATE_FILTER_ENABLED",
    True
)

POSITION_REENTRY_BLOCK_AFTER_WIN_ENABLED = _env_bool(
    "POSITION_REENTRY_BLOCK_AFTER_WIN_ENABLED",
    True
)

POSITION_REENTRY_RISKY_PRIOR_CLOSE_REASONS = _env_list(
    "POSITION_REENTRY_RISKY_PRIOR_CLOSE_REASONS",
    "score_pressure_decay,liquidity_drain_from_entry,liquidity_drain_from_peak"
)

POSITION_REENTRY_RECLAIM_EXIT_PCT = _env_float(
    "POSITION_REENTRY_RECLAIM_EXIT_PCT",
    0.02
)

POSITION_REENTRY_NEW_HIGH_PCT = _env_float(
    "POSITION_REENTRY_NEW_HIGH_PCT",
    0.00
)

POSITION_TRAILING_REBOUND_REENTRY_ENABLED = _env_bool(
    "POSITION_TRAILING_REBOUND_REENTRY_ENABLED",
    True
)

POSITION_TRAILING_REBOUND_WATCH_SECONDS = _env_int(
    "POSITION_TRAILING_REBOUND_WATCH_SECONDS",
    3600
)

POSITION_TRAILING_REBOUND_RECLAIM_PCT = _env_float(
    "POSITION_TRAILING_REBOUND_RECLAIM_PCT",
    0.02
)

POSITION_TRAILING_REBOUND_MIN_BUY_VOLUME_5M_USD = _env_float(
    "POSITION_TRAILING_REBOUND_MIN_BUY_VOLUME_5M_USD",
    1500
)

POSITION_TRAILING_REBOUND_MIN_BUY_SELL_VOLUME_RATIO = _env_float(
    "POSITION_TRAILING_REBOUND_MIN_BUY_SELL_VOLUME_RATIO",
    1.10
)

POSITION_TRAILING_REBOUND_MIN_PRESSURE = _env_float(
    "POSITION_TRAILING_REBOUND_MIN_PRESSURE",
    50
)

POSITION_TRAILING_REBOUND_MIN_VOLUME_LIQUIDITY_RATIO = _env_float(
    "POSITION_TRAILING_REBOUND_MIN_VOLUME_LIQUIDITY_RATIO",
    0.35
)

POSITION_TRAILING_REBOUND_REQUIRE_VWAP_RECLAIM = _env_bool(
    "POSITION_TRAILING_REBOUND_REQUIRE_VWAP_RECLAIM",
    True
)

POSITION_TRAILING_REBOUND_REQUIRE_VWAP_READY = _env_bool(
    "POSITION_TRAILING_REBOUND_REQUIRE_VWAP_READY",
    True
)

POSITION_LINEAGE_EXPOSURE_BLOCK_ENABLED = _env_bool(
    "POSITION_LINEAGE_EXPOSURE_BLOCK_ENABLED",
    True
)

LOCAL_RSI_ENABLED = _env_bool(
    "LOCAL_RSI_ENABLED",
    True
)

LOCAL_RSI_ENTRY_ENABLED = _env_bool(
    "LOCAL_RSI_ENTRY_ENABLED",
    False
)

LOCAL_RSI_TIMEFRAME_SECONDS = _env_int(
    "LOCAL_RSI_TIMEFRAME_SECONDS",
    60
)

LOCAL_RSI_PERIOD = _env_int(
    "LOCAL_RSI_PERIOD",
    14
)

LOCAL_RSI_EMA_PERIOD = _env_int(
    "LOCAL_RSI_EMA_PERIOD",
    9
)

LOCAL_RSI_MIN_ENTRY = _env_float(
    "LOCAL_RSI_MIN_ENTRY",
    45
)

LOCAL_RSI_ENTRY_RECLAIM_LEVEL = _env_float(
    "LOCAL_RSI_ENTRY_RECLAIM_LEVEL",
    50
)

LOCAL_RSI_OVERBOUGHT_ENTRY_BLOCKER_ENABLED = _env_bool(
    "LOCAL_RSI_OVERBOUGHT_ENTRY_BLOCKER_ENABLED",
    True
)

LOCAL_RSI_OVERBOUGHT_ENTRY_LEVEL = _env_float(
    "LOCAL_RSI_OVERBOUGHT_ENTRY_LEVEL",
    70
)

LOCAL_RSI_ENTRY_RESET_ZONE_LOW = _env_float(
    "LOCAL_RSI_ENTRY_RESET_ZONE_LOW",
    45
)

LOCAL_RSI_ENTRY_RESET_ZONE_HIGH = _env_float(
    "LOCAL_RSI_ENTRY_RESET_ZONE_HIGH",
    55
)

LOCAL_RSI_ENTRY_WATCH_SECONDS = _env_int(
    "LOCAL_RSI_ENTRY_WATCH_SECONDS",
    900
)

LOCAL_RSI_DEFERRED_ENTRY_REQUIRE_EMA9_RECLAIM = _env_bool(
    "LOCAL_RSI_DEFERRED_ENTRY_REQUIRE_EMA9_RECLAIM",
    True
)

LOCAL_RSI_DEFERRED_ENTRY_REQUIRE_VWAP_RECLAIM = _env_bool(
    "LOCAL_RSI_DEFERRED_ENTRY_REQUIRE_VWAP_RECLAIM",
    True
)

ANCHORED_VWAP_ENABLED = _env_bool(
    "ANCHORED_VWAP_ENABLED",
    True
)

ANCHORED_VWAP_ENTRY_ENABLED = _env_bool(
    "ANCHORED_VWAP_ENTRY_ENABLED",
    True
)

ANCHORED_VWAP_ENTRY_REQUIRE_READY = _env_bool(
    "ANCHORED_VWAP_ENTRY_REQUIRE_READY",
    False
)

ANCHORED_VWAP_TRAILING_STOP_ENABLED = _env_bool(
    "ANCHORED_VWAP_TRAILING_STOP_ENABLED",
    True
)

ANCHORED_VWAP_TRAILING_STOP_BUFFER_PCT = _env_float(
    "ANCHORED_VWAP_TRAILING_STOP_BUFFER_PCT",
    0.10
)

ANCHORED_VWAP_TRAILING_ACTIVATE_PROFIT_PCT = _env_float(
    "ANCHORED_VWAP_TRAILING_ACTIVATE_PROFIT_PCT",
    0.10
)

ANCHORED_VWAP_PEAK_TRAIL_PCT = _env_float(
    "ANCHORED_VWAP_PEAK_TRAIL_PCT",
    0.30
)

ANCHORED_VWAP_PEAK_TRAIL_MIN_MULTIPLE = _env_float(
    "ANCHORED_VWAP_PEAK_TRAIL_MIN_MULTIPLE",
    1.05
)

ANCHORED_VWAP_STOP_CONFIRMATION_TICKS = _env_int(
    "ANCHORED_VWAP_STOP_CONFIRMATION_TICKS",
    2
)

# Confirmation ticks for the initial hard stop and standard trailing stop.
# These fired on the first breached scan, so a single glitch price tick
# (which reverts the next scan) could force a false stop-out (e.g. Molt.id).
# Requiring 2 consecutive breaches means a one-scan glitch can never trigger
# a stop; a real breach persists and still fires ~2s later.
POSITION_HARD_STOP_CONFIRMATION_TICKS = _env_int(
    "POSITION_HARD_STOP_CONFIRMATION_TICKS",
    2
)

ANCHORED_VWAP_LOOKBACK_SECONDS = _env_int(
    "ANCHORED_VWAP_LOOKBACK_SECONDS",
    3600
)

ANCHORED_VWAP_TIMEFRAME_SECONDS = _env_int(
    "ANCHORED_VWAP_TIMEFRAME_SECONDS",
    60
)

ANCHORED_VWAP_MIN_CANDLES = _env_int(
    "ANCHORED_VWAP_MIN_CANDLES",
    3
)

ANCHORED_VWAP_CANDLE_LIMIT = _env_int(
    "ANCHORED_VWAP_CANDLE_LIMIT",
    120
)

ANCHORED_VWAP_PROVIDER_REFRESH_ENABLED = _env_bool(
    "ANCHORED_VWAP_PROVIDER_REFRESH_ENABLED",
    True
)

ANCHORED_VWAP_PROVIDER_REFRESH_SECONDS = _env_int(
    "ANCHORED_VWAP_PROVIDER_REFRESH_SECONDS",
    300
)

ANCHORED_VWAP_PROVIDER_MAX_PAGES = _env_int(
    "ANCHORED_VWAP_PROVIDER_MAX_PAGES",
    1
)

ANCHORED_VWAP_PROVIDER_PADDING_SECONDS = _env_int(
    "ANCHORED_VWAP_PROVIDER_PADDING_SECONDS",
    900
)

LOCAL_RSI_ALLOW_STRONG_ENTRY_WHILE_WARMING = _env_bool(
    "LOCAL_RSI_ALLOW_STRONG_ENTRY_WHILE_WARMING",
    True
)

LOCAL_RSI_STRONG_ENTRY_MIN_PRESSURE = _env_float(
    "LOCAL_RSI_STRONG_ENTRY_MIN_PRESSURE",
    80
)

LOCAL_RSI_STRONG_ENTRY_MIN_VOLUME_LIQUIDITY_RATIO = _env_float(
    "LOCAL_RSI_STRONG_ENTRY_MIN_VOLUME_LIQUIDITY_RATIO",
    1.00
)

LOCAL_RSI_STRONG_ENTRY_MIN_BUY_SELL_RATIO = _env_float(
    "LOCAL_RSI_STRONG_ENTRY_MIN_BUY_SELL_RATIO",
    1.50
)

LOCAL_RSI_EXIT_ENABLED = _env_bool(
    "LOCAL_RSI_EXIT_ENABLED",
    True
)

LOCAL_RSI_BEARISH_EXIT_ENABLED = _env_bool(
    "LOCAL_RSI_BEARISH_EXIT_ENABLED",
    False
)

LOCAL_RSI_EXIT_REQUIRE_50_BREAK = _env_bool(
    "LOCAL_RSI_EXIT_REQUIRE_50_BREAK",
    True
)

LOCAL_RSI_EXIT_CONFIRM_LEVEL = _env_float(
    "LOCAL_RSI_EXIT_CONFIRM_LEVEL",
    50
)

LOCAL_RSI_EXIT_MAX_PRESSURE = _env_float(
    "LOCAL_RSI_EXIT_MAX_PRESSURE",
    55
)

LOCAL_RSI_EXIT_MAX_VOLUME_LIQUIDITY_RATIO = _env_float(
    "LOCAL_RSI_EXIT_MAX_VOLUME_LIQUIDITY_RATIO",
    0.50
)

LOCAL_RSI_EXIT_MAX_BUY_SELL_RATIO = _env_float(
    "LOCAL_RSI_EXIT_MAX_BUY_SELL_RATIO",
    1.00
)

RUNNER_RSI_ENABLED = _env_bool(
    "RUNNER_RSI_ENABLED",
    True
)

RUNNER_RSI_TIMEFRAME_SECONDS = _env_int(
    "RUNNER_RSI_TIMEFRAME_SECONDS",
    900
)

RUNNER_RSI_PERIOD = _env_int(
    "RUNNER_RSI_PERIOD",
    14
)

RUNNER_RSI_EMA_PERIOD = _env_int(
    "RUNNER_RSI_EMA_PERIOD",
    9
)

RUNNER_RSI_CANDLE_LIMIT = _env_int(
    "RUNNER_RSI_CANDLE_LIMIT",
    96
)

RUNNER_RSI_OBSERVATION_INTERVAL_SECONDS = _env_float(
    "RUNNER_RSI_OBSERVATION_INTERVAL_SECONDS",
    30
)

RUNNER_RSI_MIN_SCALED_OUT_PCT = _env_float(
    "RUNNER_RSI_MIN_SCALED_OUT_PCT",
    0.80
)

RUNNER_RSI_MANAGE_ALL_TRADES = _env_bool(
    "RUNNER_RSI_MANAGE_ALL_TRADES",
    True
)

RUNNER_RSI_EXIT_REQUIRE_50_BREAK = _env_bool(
    "RUNNER_RSI_EXIT_REQUIRE_50_BREAK",
    True
)

RUNNER_RSI_EXIT_CONFIRM_LEVEL = _env_float(
    "RUNNER_RSI_EXIT_CONFIRM_LEVEL",
    50
)

RUNNER_RSI_EXIT_MAX_PRESSURE = _env_float(
    "RUNNER_RSI_EXIT_MAX_PRESSURE",
    60
)

RUNNER_RSI_EXIT_MAX_VOLUME_LIQUIDITY_RATIO = _env_float(
    "RUNNER_RSI_EXIT_MAX_VOLUME_LIQUIDITY_RATIO",
    0.75
)

RUNNER_RSI_EXIT_MAX_BUY_SELL_RATIO = _env_float(
    "RUNNER_RSI_EXIT_MAX_BUY_SELL_RATIO",
    1.10
)

RUNNER_RSI_DISASTER_FLOOR_MULTIPLE = _env_float(
    "RUNNER_RSI_DISASTER_FLOOR_MULTIPLE",
    3.00
)

RUNNER_RSI_PEAK_TRAIL_PCT = _env_float(
    "RUNNER_RSI_PEAK_TRAIL_PCT",
    0.45
)

POSITION_MAX_ENTRY_FDV_USD = _env_float(
    "POSITION_MAX_ENTRY_FDV_USD",
    50000
)

POSITION_HYPEREVM_MAX_ENTRY_FDV_USD = _env_float(
    "POSITION_HYPEREVM_MAX_ENTRY_FDV_USD",
    500000
)

POSITION_MAX_ENTRIES_PER_TOKEN_PER_HOUR = _env_int(
    "POSITION_MAX_ENTRIES_PER_TOKEN_PER_HOUR",
    3
)

POSITION_AVOID_MIGRATION_FDV_ZONE = _env_bool(
    "POSITION_AVOID_MIGRATION_FDV_ZONE",
    True
)

POSITION_MIGRATION_FDV_BUFFER_USD = _env_float(
    "POSITION_MIGRATION_FDV_BUFFER_USD",
    7000
)

POSITION_CHOP_FILTER_ENABLED = _env_bool(
    "POSITION_CHOP_FILTER_ENABLED",
    True
)

POSITION_CHOP_LOOKBACK_SCANS = _env_int(
    "POSITION_CHOP_LOOKBACK_SCANS",
    6
)

POSITION_CHOP_MIN_DIRECTION_FLIPS = _env_int(
    "POSITION_CHOP_MIN_DIRECTION_FLIPS",
    3
)

POSITION_CHOP_MIN_LEG_MOVE_PCT = _env_float(
    "POSITION_CHOP_MIN_LEG_MOVE_PCT",
    0.04
)

POSITION_CHOP_MIN_RANGE_PCT = _env_float(
    "POSITION_CHOP_MIN_RANGE_PCT",
    0.25
)

POSITION_CHOP_MAX_RANGE_POSITION = _env_float(
    "POSITION_CHOP_MAX_RANGE_POSITION",
    0.55
)

POSITION_CHOP_MAX_BUY_SELL_RATIO = _env_float(
    "POSITION_CHOP_MAX_BUY_SELL_RATIO",
    1.00
)

POSITION_FIXED_USD_POSITION_SIZING_ENABLED = _env_bool(
    "POSITION_FIXED_USD_POSITION_SIZING_ENABLED",
    True
)

POSITION_POSITION_SIZE_USD = _env_float(
    "POSITION_POSITION_SIZE_USD",
    20
)

POSITION_POSITION_SIZE_SOL = _env_float(
    "POSITION_POSITION_SIZE_SOL",
    0.50
)

POSITION_SOL_USD = _env_float(
    "POSITION_SOL_USD",
    150
)

POSITION_SOL_MINT_ADDRESS = _env(
    "POSITION_SOL_MINT_ADDRESS",
    "So11111111111111111111111111111111111111112"
)

POSITION_SOL_PRICE_REFRESH_SECONDS = _env_float(
    "POSITION_SOL_PRICE_REFRESH_SECONDS",
    60
)

POSITION_MAX_OPEN_POSITIONS = 4

POSITION_SCALE_OUT_LADDER = _env_scale_out_ladder(
    "POSITION_SCALE_OUT_LADDER",
    (
        (4.00, 0.50),
        (10.00, 0.70)
    )
)

POSITION_EARLY_REVIVAL_SCALE_OUT_LADDER = _env_scale_out_ladder(
    "POSITION_EARLY_REVIVAL_SCALE_OUT_LADDER",
    POSITION_SCALE_OUT_LADDER
)

POSITION_MIGRATED_REVIVAL_SCALE_OUT_LADDER = _env_scale_out_ladder(
    "POSITION_MIGRATED_REVIVAL_SCALE_OUT_LADDER",
    POSITION_EARLY_REVIVAL_SCALE_OUT_LADDER
)

POSITION_HC_MIN_ENTRY_VOLUME_1H_USD = _env_float(
    "POSITION_HC_MIN_ENTRY_VOLUME_1H_USD",
    10000
)

POSITION_HC_MIN_ENTRY_VOLUME_MULTIPLE = _env_float(
    "POSITION_HC_MIN_ENTRY_VOLUME_MULTIPLE",
    2.0
)

POSITION_HC_MAX_ENTRY_PRICE_CHANGE_5M = _env_float(
    "POSITION_HC_MAX_ENTRY_PRICE_CHANGE_5M",
    35
)

POSITION_EARLY_REVIVAL_MAX_ENTRY_PRICE_CHANGE_5M = _env_float(
    "POSITION_EARLY_REVIVAL_MAX_ENTRY_PRICE_CHANGE_5M",
    150
)

# HC route: tighter ladder — 43.7% blowup at 1h requires fast-exit discipline.
# Take 30% at 1.5x and 60% at 2.5x; do not wait for 4x+ default rungs.
POSITION_HC_SCALE_OUT_LADDER = _env_scale_out_ladder(
    "POSITION_HC_SCALE_OUT_LADDER",
    (
        (1.50, 0.30),
        (2.50, 0.60),
    )
)

POSITION_TAKE_PROFIT_MULTIPLE = _env_float(
    "POSITION_TAKE_PROFIT_MULTIPLE",
    4.00
)

POSITION_TAKE_PROFIT_SELL_PCT = _env_float(
    "POSITION_TAKE_PROFIT_SELL_PCT",
    0.50
)

POSITION_MAX_SCALE_OUT_PCT = _env_float(
    "POSITION_MAX_SCALE_OUT_PCT",
    0.70
)

POSITION_MIN_SCALE_OUT_STEP_PCT = _env_float(
    "POSITION_MIN_SCALE_OUT_STEP_PCT",
    0.10
)

POSITION_POST_SCALE_TRAIL_ENABLED = _env_bool(
    "POSITION_POST_SCALE_TRAIL_ENABLED",
    True
)

POSITION_POST_SCALE_TRAIL_RULES = (
    (
        0.80,
        _env_float("POSITION_POST_SCALE_TRAIL_80_PCT", 0.45),
        _env_float("POSITION_POST_SCALE_FLOOR_80_MULTIPLE", 3.00)
    ),
    (
        0.65,
        _env_float("POSITION_POST_SCALE_TRAIL_65_PCT", 0.30),
        _env_float("POSITION_POST_SCALE_FLOOR_65_MULTIPLE", 2.00)
    ),
    (
        0.50,
        _env_float("POSITION_POST_SCALE_TRAIL_50_PCT", 0.25),
        _env_float("POSITION_POST_SCALE_FLOOR_50_MULTIPLE", 1.00)
    )
)

POSITION_RUNNER_RELAXED_TRAIL_PCT = _env_float(
    "POSITION_RUNNER_RELAXED_TRAIL_PCT",
    0.55
)

POSITION_HIGH_MULT_TRAIL_TRIGGER = _env_float(
    "POSITION_HIGH_MULT_TRAIL_TRIGGER",
    4.0
)

POSITION_HIGH_MULT_TRAIL_PCT = _env_float(
    "POSITION_HIGH_MULT_TRAIL_PCT",
    0.50
)

POSITION_RUNNER_RELAXED_MIN_PRESSURE = _env_float(
    "POSITION_RUNNER_RELAXED_MIN_PRESSURE",
    65
)

POSITION_RUNNER_RELAXED_MIN_BUY_SELL_RATIO = _env_float(
    "POSITION_RUNNER_RELAXED_MIN_BUY_SELL_RATIO",
    1.00
)

POSITION_RUNNER_RELAXED_MIN_VOLUME_LIQUIDITY_RATIO = _env_float(
    "POSITION_RUNNER_RELAXED_MIN_VOLUME_LIQUIDITY_RATIO",
    0.25
)

POSITION_RUNNER_RELAXED_MIN_PRICE_MULTIPLE = _env_float(
    "POSITION_RUNNER_RELAXED_MIN_PRICE_MULTIPLE",
    1.20
)

POSITION_HIGH_VOLUME_TRAIL_GRACE_ENABLED = _env_bool(
    "POSITION_HIGH_VOLUME_TRAIL_GRACE_ENABLED",
    False
)

POSITION_HIGH_VOLUME_TRAIL_GRACE_MIN_VOLUME_MULTIPLE = _env_float(
    "POSITION_HIGH_VOLUME_TRAIL_GRACE_MIN_VOLUME_MULTIPLE",
    2.00
)

POSITION_HIGH_VOLUME_TRAIL_GRACE_TRAIL_PCT = _env_float(
    "POSITION_HIGH_VOLUME_TRAIL_GRACE_TRAIL_PCT",
    0.55
)

POSITION_HIGH_VOLUME_TRAIL_GRACE_UNTIL_PEAK_MULTIPLE = _env_float(
    "POSITION_HIGH_VOLUME_TRAIL_GRACE_UNTIL_PEAK_MULTIPLE",
    2.00
)

POSITION_HIGH_VOLUME_TRAIL_GRACE_MIN_PRESSURE = _env_float(
    "POSITION_HIGH_VOLUME_TRAIL_GRACE_MIN_PRESSURE",
    55
)

POSITION_HIGH_VOLUME_TRAIL_GRACE_MIN_VOLUME_LIQUIDITY_RATIO = (
    _env_float(
        "POSITION_HIGH_VOLUME_TRAIL_GRACE_MIN_VOLUME_LIQUIDITY_RATIO",
        0.50
    )
)

POSITION_HIGH_VOLUME_TRAIL_GRACE_MIN_BUY_SELL_RATIO = _env_float(
    "POSITION_HIGH_VOLUME_TRAIL_GRACE_MIN_BUY_SELL_RATIO",
    1.00
)

POSITION_INITIAL_STOP_LOSS_PCT = _env_float(
    "POSITION_INITIAL_STOP_LOSS_PCT",
    0.30
)

POSITION_EARLY_REVIVAL_INITIAL_STOP_LOSS_PCT = _env_float(
    "POSITION_EARLY_REVIVAL_INITIAL_STOP_LOSS_PCT",
    POSITION_INITIAL_STOP_LOSS_PCT
)

POSITION_MIGRATED_REVIVAL_INITIAL_STOP_LOSS_PCT = _env_float(
    "POSITION_MIGRATED_REVIVAL_INITIAL_STOP_LOSS_PCT",
    POSITION_EARLY_REVIVAL_INITIAL_STOP_LOSS_PCT
)

POSITION_HC_INITIAL_STOP_LOSS_PCT = _env_float(
    "POSITION_HC_INITIAL_STOP_LOSS_PCT",
    POSITION_INITIAL_STOP_LOSS_PCT
)

# ── Adaptive (volatility-scaled) initial stop ────────────────────────────────
# Default ON: open_position sets the stop distance from the token's own
# downside-ATR over recent 1m candles (k * downside_ATR / entry_price), clamped
# to [MIN_PCT, MAX_PCT]; falls back to the route % when candle history is thin.
# The chosen % is stored on the position so every later stop reference (hard
# stop, on-chain resting stop, trail floor) stays consistent.
POSITION_ATR_STOP_ENABLED = _env_bool(
    "POSITION_ATR_STOP_ENABLED",
    True
)

POSITION_ATR_STOP_K = _env_float(
    "POSITION_ATR_STOP_K",
    5.0
)

POSITION_ATR_STOP_PERIOD = _env_int(
    "POSITION_ATR_STOP_PERIOD",
    14
)

POSITION_ATR_STOP_TIMEFRAME_SECONDS = _env_int(
    "POSITION_ATR_STOP_TIMEFRAME_SECONDS",
    60
)

POSITION_ATR_STOP_MIN_CANDLES = _env_int(
    "POSITION_ATR_STOP_MIN_CANDLES",
    20
)

POSITION_ATR_STOP_MIN_PCT = _env_float(
    "POSITION_ATR_STOP_MIN_PCT",
    0.12
)

POSITION_ATR_STOP_MAX_PCT = _env_float(
    "POSITION_ATR_STOP_MAX_PCT",
    0.70
)

# Cross-check the gRPC/DexScreener spot price against the executable
# Definitive exit quote that is already fetched every scan. A single
# spurious near-zero on-chain tick (mid-transaction pool read, dust
# swap) was force-closing positions at fabricated ~-99% losses via the
# RSI/stop path (e.g. SIC closed at 0.15% of entry while the real price
# was ~89%). When the spot price deviates from the quote-implied price
# by more than MAX_DEVIATION_PCT, the spot tick is treated as bad and
# replaced with the quote-implied price before it feeds RSI/VWAP/stops.
POSITION_PRICE_QUOTE_SANITY_ENABLED = _env_bool(
    "POSITION_PRICE_QUOTE_SANITY_ENABLED",
    True
)

# Glitch ticks deviate ~99.8%+ from the quote; legitimate spot-vs-quote
# disagreement (exit-quote price impact on small positions) is well
# under this. 0.8 cleanly separates the two with margin.
POSITION_PRICE_QUOTE_SANITY_MAX_DEVIATION_PCT = _env_float(
    "POSITION_PRICE_QUOTE_SANITY_MAX_DEVIATION_PCT",
    0.8
)

# Ignore dust-sized quotes when sanity-checking, so a tiny/illiquid
# quote cannot itself overwrite a legitimate spot price.
POSITION_PRICE_QUOTE_SANITY_MIN_QUOTE_VALUE_USD = _env_float(
    "POSITION_PRICE_QUOTE_SANITY_MIN_QUOTE_VALUE_USD",
    1.0
)

# Telemetry retention. signal_snapshots + token_candles are high-volume
# analysis tables written every scan; left unbounded they bloated the DB to
# 1.18 GB and worsened write-lock contention. Rows older than this are moved
# to scanner_archive.db before being deleted from the hot DB, so live scanning
# stays fast while backtest history remains available.
SCANNER_TELEMETRY_RETENTION_DAYS = _env_int(
    "SCANNER_TELEMETRY_RETENTION_DAYS",
    7
)

SCANNER_TELEMETRY_ARCHIVE_ENABLED = _env_bool(
    "SCANNER_TELEMETRY_ARCHIVE_ENABLED",
    True
)

SCANNER_TELEMETRY_ARCHIVE_DATABASE = _env(
    "SCANNER_TELEMETRY_ARCHIVE_DATABASE",
    ""
)

SCANNER_TELEMETRY_PRUNE_INTERVAL_SECONDS = _env_int(
    "SCANNER_TELEMETRY_PRUNE_INTERVAL_SECONDS",
    21600
)

# Per-table hot retention (days). signal_snapshots churn ~215k rows/day; the
# live path only ever reads the newest snapshot per token and outcome labeling
# only looks back 6h, so snapshots can be trimmed hard. token_candles are the
# aggregated series used for backtests/RSI, so they're kept longer. Anything
# older is moved to the cold archive first (never deleted outright). Missing
# overrides fall back to SCANNER_TELEMETRY_RETENTION_DAYS.
SCANNER_TELEMETRY_RETENTION_DAYS_SIGNAL_SNAPSHOTS = _env_int(
    "SCANNER_TELEMETRY_RETENTION_DAYS_SIGNAL_SNAPSHOTS",
    3
)

SCANNER_TELEMETRY_RETENTION_DAYS_TOKEN_CANDLES = _env_int(
    "SCANNER_TELEMETRY_RETENTION_DAYS_TOKEN_CANDLES",
    14
)

SCANNER_TELEMETRY_RETENTION_BY_TABLE = {
    "signal_snapshots": SCANNER_TELEMETRY_RETENTION_DAYS_SIGNAL_SNAPSHOTS,
    "token_candles": SCANNER_TELEMETRY_RETENTION_DAYS_TOKEN_CANDLES,
}

# Cap the WAL so a stalled checkpoint (a long-lived reader) can't let it balloon
# the way it once grew to ~291 MB. Per-connection PRAGMA journal_size_limit
# truncates the WAL back to this size after each checkpoint.
SCANNER_SQLITE_WAL_SIZE_LIMIT_BYTES = _env_int(
    "SCANNER_SQLITE_WAL_SIZE_LIMIT_BYTES",
    134217728  # 128 MB
)

# Cold Parquet roll-off. Rows older than the warm window are exported from
# scanner_archive.db into monthly Parquet files (compact, ideal for
# fine-tuning/backtests) and then deleted from the archive DB so the warm
# layer stays queryable-but-small. Requires pyarrow; only the maintenance
# job imports it, never the live scanner.
SCANNER_ARCHIVE_PARQUET_ENABLED = _env_bool(
    "SCANNER_ARCHIVE_PARQUET_ENABLED",
    True
)

SCANNER_ARCHIVE_PARQUET_DIR = _env(
    "SCANNER_ARCHIVE_PARQUET_DIR",
    ""
)

SCANNER_ARCHIVE_WARM_RETENTION_DAYS = _env_int(
    "SCANNER_ARCHIVE_WARM_RETENTION_DAYS",
    30
)

LATTICE_MAX_HOLD_H = _env_float(
    "LATTICE_MAX_HOLD_H",
    12.0
)

LATTICE_MAX_HOLD_EXEMPT_MULTIPLE = _env_float(
    "LATTICE_MAX_HOLD_EXEMPT_MULTIPLE",
    2.0
)

LATTICE_MAX_HOLD_PARTIAL_RUNNER_MULTIPLE = _env_float(
    "LATTICE_MAX_HOLD_PARTIAL_RUNNER_MULTIPLE",
    1.5
)

LATTICE_MAX_HOLD_PARTIAL_RUNNER_H = _env_float(
    "LATTICE_MAX_HOLD_PARTIAL_RUNNER_H",
    24.0
)

LATTICE_MAX_HOLD_PARTIAL_RUNNER_REQUIRE_PROFIT = _env_bool(
    "LATTICE_MAX_HOLD_PARTIAL_RUNNER_REQUIRE_PROFIT",
    True
)

# The gRPC on-chain price is derived from a single swap's deltas
# (sol_delta/base_delta), so a dust/routed/MEV swap can yield a glitch
# price that false-fires stops (e.g. Molt.id: a one-scan tick to 0.65x
# while the real price was 0.95x). Only let the gRPC price override the
# DexScreener price when the two agree within this deviation; otherwise
# the gRPC tick is rejected and the DexScreener price stands.
GRPC_PRICE_MAX_DEX_DEVIATION_PCT = _env_float(
    "GRPC_PRICE_MAX_DEX_DEVIATION_PCT",
    0.25
)

# Alert-window entry: the per-scan route/score flickers to "none" for fast
# movers, so the scanner alerts on big runners but drops them at entry (only
# 37% of >=5x alerts entered; BABEL alerted at 59 then showed route=none/25-40
# on every entry scan and ran 10x). When a token fired a VALID alert recently,
# allow entry to use that alert's route+score as a fallback, gated by
# anti-chase guards. Ships shadow-first (logs would-enters, places nothing).
ALERT_WINDOW_ENTRY_ENABLED = _env_bool(
    "ALERT_WINDOW_ENTRY_ENABLED",
    False
)
ALERT_WINDOW_ENTRY_SHADOW_MODE = _env_bool(
    "ALERT_WINDOW_ENTRY_SHADOW_MODE",
    True
)
ALERT_WINDOW_ENTRY_SECONDS = _env_int(
    "ALERT_WINDOW_ENTRY_SECONDS",
    120
)
# Don't enter if price already ran past alert*(1+MAX_RUN) — that's chasing.
ALERT_WINDOW_MAX_RUN_PCT = _env_float(
    "ALERT_WINDOW_MAX_RUN_PCT",
    0.25
)
# Don't enter if price already fell below alert*(1-MAX_DROP) — already dead.
ALERT_WINDOW_MAX_DROP_PCT = _env_float(
    "ALERT_WINDOW_MAX_DROP_PCT",
    0.20
)
ALERT_WINDOW_MIN_SCORE = _env_float(
    "ALERT_WINDOW_MIN_SCORE",
    0
)
ALERT_WINDOW_ROUTES = _env_list(
    "ALERT_WINDOW_ROUTES",
    "bonding_early_revival,bonding_momentum_high_conviction"
)

POSITION_PRESSURE_EXIT_MAX_LOSS_PCT = 0.15

POSITION_PRESSURE_EXIT_MAX_PRESSURE = 35

POSITION_PRESSURE_EXIT_MAX_IMPULSE = 1.10

POSITION_PRESSURE_EXIT_MAX_VOLUME_LIQUIDITY_RATIO = 0.35

POSITION_PRESSURE_EXIT_MAX_BUY_SELL_RATIO = 0.80

POSITION_SELL_ONLY_FLOW_EXIT_ENABLED = _env_bool(
    "POSITION_SELL_ONLY_FLOW_EXIT_ENABLED",
    True
)

POSITION_SELL_ONLY_FLOW_MAX_BUY_VOLUME_5M_USD = _env_float(
    "POSITION_SELL_ONLY_FLOW_MAX_BUY_VOLUME_5M_USD",
    25
)

POSITION_SELL_ONLY_FLOW_MAX_BUY_SELL_VOLUME_RATIO = _env_float(
    "POSITION_SELL_ONLY_FLOW_MAX_BUY_SELL_VOLUME_RATIO",
    0.05
)

POSITION_SELL_ONLY_FLOW_MIN_SELL_VOLUME_5M_USD = _env_float(
    "POSITION_SELL_ONLY_FLOW_MIN_SELL_VOLUME_5M_USD",
    5000
)

POSITION_SELL_ONLY_FLOW_MIN_SELL_ENTRY_NOTIONAL_MULTIPLE = _env_float(
    "POSITION_SELL_ONLY_FLOW_MIN_SELL_ENTRY_NOTIONAL_MULTIPLE",
    5
)

POSITION_SELL_ONLY_FLOW_MAX_PRICE_MULTIPLE = _env_float(
    "POSITION_SELL_ONLY_FLOW_MAX_PRICE_MULTIPLE",
    1.20
)

POSITION_LIQUIDITY_COLLAPSE_EXIT_ENABLED = _env_bool(
    "POSITION_LIQUIDITY_COLLAPSE_EXIT_ENABLED",
    True
)

POSITION_LIQUIDITY_COLLAPSE_FROM_ENTRY_PCT = _env_float(
    "POSITION_LIQUIDITY_COLLAPSE_FROM_ENTRY_PCT",
    0.45
)

POSITION_LIQUIDITY_COLLAPSE_FROM_PEAK_PCT = _env_float(
    "POSITION_LIQUIDITY_COLLAPSE_FROM_PEAK_PCT",
    0.50
)

POSITION_LIQUIDITY_COLLAPSE_PRESSURE_CAP = _env_float(
    "POSITION_LIQUIDITY_COLLAPSE_PRESSURE_CAP",
    20
)

POSITION_LIQUIDITY_COLLAPSE_MIN_REFERENCE_USD = _env_float(
    "POSITION_LIQUIDITY_COLLAPSE_MIN_REFERENCE_USD",
    1000
)

POSITION_STRICT_EARLY_EXIT_ENABLED = _env_bool(
    "POSITION_STRICT_EARLY_EXIT_ENABLED",
    True
)

# Kept at 0.05 after analysis (n=424 would-cut events): conditional on all 3
# weak signals, recovery-to-entry is only 43% at -5% and holding averages
# -19% (most reach the hard stop). Cutting EARLY is +EV and the edge is
# LARGEST at -5% (+14.4pp vs hold) — a brief detour to 0.15 was reverted as
# it shrank the edge. The all-3-signal + confirmation gates (below) are what
# make the early cut safe. (Exact optimum still wants a full strategy backtest
# that credits recovery upside; direction = cut early on confirmed weakness.)
POSITION_STRICT_EARLY_EXIT_LOSS_PCT = _env_float(
    "POSITION_STRICT_EARLY_EXIT_LOSS_PCT",
    0.05
)

# Require ALL THREE weak signals (was 2 of 3) so a single soft metric on a
# normal dip can't trigger the cut.
POSITION_STRICT_EARLY_EXIT_MIN_WEAK_SIGNALS = _env_int(
    "POSITION_STRICT_EARLY_EXIT_MIN_WEAK_SIGNALS",
    3
)

# Require the loss+weak-signal condition to persist N consecutive scans, so a
# transient dip/low-pressure tick can't force a premature cut.
POSITION_STRICT_EARLY_EXIT_CONFIRM_TICKS = _env_int(
    "POSITION_STRICT_EARLY_EXIT_CONFIRM_TICKS",
    2
)

# Migration-zone grace: for entries within +/-POSITION_MIGRATION_FDV_BUFFER_USD
# of the migration FDV, suppress the momentum/signal soft exits (strict-early,
# local/runner-RSI, pressure, decay) until the position reaches GRACE_UNTIL
# multiple — keeping ONLY the hard stop (+ catastrophic rug exits, which fire
# outside these methods). Post-migration tokens often spike then flush hard
# before running; the soft exits otherwise shake you out of the runners.
# Data (n=67 zone): hold-til-2x mean +5% vs -5% outside; ~10% dipped >=15%
# then ran. After GRACE_UNTIL, normal scale+trail resumes. Scoped to the zone.
POSITION_MIGRATION_ZONE_GRACE_ENABLED = _env_bool(
    "POSITION_MIGRATION_ZONE_GRACE_ENABLED",
    True
)
POSITION_MIGRATION_ZONE_GRACE_UNTIL_MULTIPLE = _env_float(
    "POSITION_MIGRATION_ZONE_GRACE_UNTIL_MULTIPLE",
    2.0
)

# Runner-hold leg: when a SOFT exit fires before the position has ever printed
# the release multiple, sell only (1 - fraction) and keep the rest as a hold
# tranche exempt from soft exits, with a hard floor and a max-hold horizon.
# Rationale (analysis/runner_trainability_report.md, 2026-06-10): 35% of entries
# touched 2x within 24h but exits captured >=1.5x on only 5/29; median realized
# on runner tokens was 0.96x. Moon-bag counterfactual on 82 candle-covered
# trades: PnL -$158 -> -$73. 32% of true runners dip below 0.7x before peaking,
# so the floor uses hard-stop-style confirmation ticks.
POSITION_RUNNER_HOLD_ENABLED = _env_bool(
    "POSITION_RUNNER_HOLD_ENABLED",
    False
)
POSITION_RUNNER_HOLD_FRACTION = _env_float(
    "POSITION_RUNNER_HOLD_FRACTION",
    0.50
)
POSITION_RUNNER_HOLD_FLOOR_MULTIPLE = _env_float(
    "POSITION_RUNNER_HOLD_FLOOR_MULTIPLE",
    0.70
)
POSITION_RUNNER_HOLD_RELEASE_MULTIPLE = _env_float(
    "POSITION_RUNNER_HOLD_RELEASE_MULTIPLE",
    2.0
)
POSITION_RUNNER_HOLD_MAX_HOURS = _env_float(
    "POSITION_RUNNER_HOLD_MAX_HOURS",
    24.0
)

POSITION_STRICT_EARLY_EXIT_MAX_PRESSURE = _env_float(
    "POSITION_STRICT_EARLY_EXIT_MAX_PRESSURE",
    40
)

POSITION_STRICT_EARLY_EXIT_MAX_VOLUME_LIQUIDITY_RATIO = _env_float(
    "POSITION_STRICT_EARLY_EXIT_MAX_VOLUME_LIQUIDITY_RATIO",
    0.50
)

POSITION_STRICT_EARLY_EXIT_MAX_BUY_SELL_RATIO = _env_float(
    "POSITION_STRICT_EARLY_EXIT_MAX_BUY_SELL_RATIO",
    0.65
)

POSITION_DECAY_LOOKBACK_SCANS = 3

POSITION_DECAY_MAX_PRESSURE = 35

POSITION_DECAY_MAX_VOLUME_LIQUIDITY_RATIO = 0.35

POSITION_DECAY_MAX_BUY_SELL_RATIO = 0.80

POSITION_DECAY_SCORE_DROP = 25

POSITION_SCORE_DECAY_EXIT_ENABLED = _env_bool(
    "POSITION_SCORE_DECAY_EXIT_ENABLED",
    False
)

POSITION_SCORE_DECAY_MAX_PRICE_MULTIPLE = _env_float(
    "POSITION_SCORE_DECAY_MAX_PRICE_MULTIPLE",
    1.20
)

POSITION_PRESSURE_LOSS_EXIT_ENABLED = _env_bool(
    "POSITION_PRESSURE_LOSS_EXIT_ENABLED",
    False
)

POSITION_MISSING_PAIR_ALERT_SCANS = 2

POSITION_CLOSED_POSITION_LIMIT = 200

# Discovery-layer conviction-pipeline ENTRY selectivity ("be pickier" knobs).
# Outcome data (analysis/lattice_*_outcomes.csv) shows the lattice composite
# is the entry feature that separates winning paper trades from losers; raising a
# floor here flipped the paper book positive, while conviction/score did not.
# Gates discovery/pipeline.py:ConvictionPipeline.evaluate. 0.0 = disabled.
LATTICE_MIN_ENTRY_LATTICE = _env_float(
    "LATTICE_MIN_ENTRY_LATTICE",
    0.0
)

# Reject entries already pumped past this 1h percent (don't chase: higher entry
# price_change_1h correlated with worse realized PnL). 0 = no cap.
LATTICE_MAX_ENTRY_PRICE_CHANGE_1H = _env_float(
    "LATTICE_MAX_ENTRY_PRICE_CHANGE_1H",
    0.0
)

# Overheating cap on the 24h horizon. Validated 2026-06-13 (analysis/
# pc24_cap_sweep.py + pc24_cap_revival.py): with the 3x/6x exits, capping
# entries already up >300% on 24h is the single biggest book lever and is
# revival-safe (already->300% revivals are net losers too). 0 = off.
LATTICE_MAX_ENTRY_PRICE_CHANGE_24H = _env_float(
    "LATTICE_MAX_ENTRY_PRICE_CHANGE_24H",
    0.0
)

# --- GMGN data-augmentation (sources/gmgn.py skills 2 & 3) ---------------- #
# All default OFF: new behavioural features, enable in paper to validate.
# Skill 2 — override DexScreener liquidity with real GMGN pool liquidity for
# pre-migration bonding_curve tokens (DexScreener under-reports these). Entry
# path only, gated to lifecycle=="bonding_curve", cached 900s.
GMGN_LIQUIDITY_OVERRIDE_ENABLED = _env_bool(
    "GMGN_LIQUIDITY_OVERRIDE_ENABLED",
    False
)
# Skill 3 — kline fade-filter: skip entries on a blow-off candle (large upper
# wick) or already rolling over from the window high. Real OHLCV candles. Only
# called for candidates that already cleared every cheap gate (bounded calls).
LATTICE_GMGN_KLINE_FADE_FILTER_ENABLED = _env_bool(
    "LATTICE_GMGN_KLINE_FADE_FILTER_ENABLED",
    False
)
LATTICE_GMGN_KLINE_MAX_UPPER_WICK_RATIO = _env_float(
    "LATTICE_GMGN_KLINE_MAX_UPPER_WICK_RATIO",
    0.5
)
LATTICE_GMGN_KLINE_MAX_DRAWDOWN_FROM_HIGH_PCT = _env_float(
    "LATTICE_GMGN_KLINE_MAX_DRAWDOWN_FROM_HIGH_PCT",
    -25.0
)
# Scan-time backfill — when a token would be discarded ONLY for missing/zero
# DexScreener liquidity/volume/price_change, backfill from GMGN token info.
# TARGETED: bonding_curve + young + near-miss only; deduped + cached; background.
GMGN_SCAN_BACKFILL_ENABLED = _env_bool(
    "GMGN_SCAN_BACKFILL_ENABLED",
    False
)
GMGN_BACKFILL_MAX_AGE_HOURS = _env_float(
    "GMGN_BACKFILL_MAX_AGE_HOURS",
    6.0
)
# GMGN token-security entry veto (skill `token security`). When enabled, the
# always-safe vetoes fire (honeypot / unsellable / blacklist / sell-tax above
# MAX_SELL_TAX). The concentration and authority-renounced checks are opt-in
# (0/False = off) because they can block many legitimate launchpad tokens.
GMGN_SECURITY_GATE_ENABLED = _env_bool(
    "GMGN_SECURITY_GATE_ENABLED",
    False
)
GMGN_SECURITY_MAX_SELL_TAX = _env_float(
    "GMGN_SECURITY_MAX_SELL_TAX",
    0.10
)
GMGN_SECURITY_MAX_TOP10_RATE = _env_float(
    "GMGN_SECURITY_MAX_TOP10_RATE",
    0.0
)
GMGN_SECURITY_REQUIRE_RENOUNCED = _env_bool(
    "GMGN_SECURITY_REQUIRE_RENOUNCED",
    False
)
# Bundle/cluster gate (filters/bundle.py): block entries where split-wallet
# clustering reveals a single operator effectively holding >= MAX_EFFECTIVE_PCT
# of supply (de-obfuscated concentration), defeating the naive top-10/breadth
# check. Default OFF; the 25% threshold matches the analyzer's current HIGH
# heuristic, but should be outcome-tested before enabling as a hard gate.
GMGN_BUNDLE_ALERT_LOG_ENABLED = _env_bool(
    "GMGN_BUNDLE_ALERT_LOG_ENABLED",
    True
)
GMGN_BUNDLE_GATE_ENABLED = _env_bool(
    "GMGN_BUNDLE_GATE_ENABLED",
    False
)
GMGN_BUNDLE_MAX_EFFECTIVE_PCT = _env_float(
    "GMGN_BUNDLE_MAX_EFFECTIVE_PCT",
    25.0
)
GMGN_BUNDLE_WINDOW_S = _env_float(
    "GMGN_BUNDLE_WINDOW_S",
    120.0
)
GMGN_BUNDLE_MIN_CLUSTER = _env_int(
    "GMGN_BUNDLE_MIN_CLUSTER",
    3
)
GMGN_BUNDLE_AMOUNT_TOL = _env_float(
    "GMGN_BUNDLE_AMOUNT_TOL",
    0.20
)
GMGN_BUNDLE_TIMEOUT_SECONDS = _env_float(
    "GMGN_BUNDLE_TIMEOUT_SECONDS",
    10.0
)

# --- Capital-lane hard vetoes (scanner gate redesign, Layer 1) ------------- #
# A small set of high-efficiency vetoes applied in the CAPITAL lane only (paper
# entries); the broad alert lane is untouched. Validated offline by
# discovery/redesign_validate.py against the historical forward outcomes: the
# default-on set (V2 risk-high, V4 flag-stack, V5 sell-pressure, deep-fader)
# cleared the directional bar (removed group materially deadier AND lower
# BIG-rate than base) while keeping 92% token-deduped BIG recall. The
# SolanaTracker bundle vetoes (V1, V3) and the wash veto only had a thin/ambiguous
# offline sample, so they ship gated and OFF by default until live coverage grows.
LATTICE_CAPITAL_VETO_ENABLED = _env_bool(
    "LATTICE_CAPITAL_VETO_ENABLED",
    True
)
# Cache TTL for the per-candidate SolanaTracker fetch so V1-V3 and the alert
# annotation reuse a single request (free tier is request-metered).
SOLANATRACKER_CACHE_TTL_S = _env_float(
    "SOLANATRACKER_CACHE_TTL_S",
    300.0
)
# V1 — SolanaTracker current bundle concentration (thin sample; default OFF).
LATTICE_BUNDLE_REJECT_IF_BUNDLED = _env_bool(
    "LATTICE_BUNDLE_REJECT_IF_BUNDLED",
    False
)
LATTICE_BUNDLE_REJECT_BUNDLE_PCT = _env_float(
    "LATTICE_BUNDLE_REJECT_BUNDLE_PCT",
    25.0
)
# V2 — SolanaTracker overall risk_level == high (default ON; cleared the bar).
LATTICE_BUNDLE_REJECT_RISK_HIGH = _env_bool(
    "LATTICE_BUNDLE_REJECT_RISK_HIGH",
    True
)
LATTICE_BUNDLE_REJECT_RISK_LEVEL = os.getenv(
    "LATTICE_BUNDLE_REJECT_RISK_LEVEL",
    "high"
).strip().lower()
# V3 — any snipers detected (thin/ambiguous sample; default OFF).
LATTICE_BUNDLE_REJECT_IF_SNIPED = _env_bool(
    "LATTICE_BUNDLE_REJECT_IF_SNIPED",
    False
)
# V4 — scanner-lane risk-flag stack (default ON). risk_flags is built in main.py
# (low_pressure / weak_volume_liquidity / sell_pressure / weak_impulse).
LATTICE_VETO_FLAG_STACK = _env_bool(
    "LATTICE_VETO_FLAG_STACK",
    True
)
LATTICE_MAX_RISK_FLAGS = _env_int(
    "LATTICE_MAX_RISK_FLAGS",
    4
)
# V5 — sell_pressure flag present (default ON).
LATTICE_VETO_SELL_PRESSURE = _env_bool(
    "LATTICE_VETO_SELL_PRESSURE",
    True
)
# Refined wash veto (moved from lattice.py to the capital lane where buyers_sig
# is available): few distinct buyers. Thin offline separation; default OFF.
LATTICE_VETO_WASH_BUYERS_SIG = _env_bool(
    "LATTICE_VETO_WASH_BUYERS_SIG",
    False
)
LATTICE_VETO_BUYERS_SIG_MIN = _env_float(
    "LATTICE_VETO_BUYERS_SIG_MIN",
    -0.3
)
# Deep-fader veto (default ON): reject entries fading hard on 1h but only in the
# -40..-15 band; exempt < -40 (capitulation rebounds) and >= -15 (shallow).
LATTICE_VETO_DEEP_FADER = _env_bool(
    "LATTICE_VETO_DEEP_FADER",
    True
)
LATTICE_DEEP_FADER_LO = _env_float(
    "LATTICE_DEEP_FADER_LO",
    -40.0
)
LATTICE_DEEP_FADER_HI = _env_float(
    "LATTICE_DEEP_FADER_HI",
    -15.0
)

# --- Capital scorecard + adaptive tiers (scanner gate redesign, Layer 2) ---- #
# Retires the conviction float as the capital SELECTOR (it anti-ranks outcomes)
# in favour of an additive scorecard (discovery/scorecard.py) -> trailing
# percentile tiers. DORMANT by default: with the scorecard disabled the entry
# path keeps legacy full-size behavior and the conviction floor stays in force.
# Validated offline (discovery/redesign_validate.py): Tier-A @ p60 win% beats
# base (8.6% vs 6.3%) with a lower dead-rate.
LATTICE_SCORECARD_ENABLED = _env_bool(
    "LATTICE_SCORECARD_ENABLED",
    False
)
# When the scorecard is enabled, the pipeline conviction gate relaxes to this
# low safety floor (conviction becomes a weak scorecard axis, not the selector).
LATTICE_MIN_CONVICTION_FLOOR = _env_float(
    "LATTICE_MIN_CONVICTION_FLOOR",
    0.05
)
# Trailing window of recent capital-candidate scores for percentile tiering.
LATTICE_TIER_WINDOW_SIZE = _env_int(
    "LATTICE_TIER_WINDOW_SIZE",
    500
)
LATTICE_TIER_WINDOW_MIN = _env_int(
    "LATTICE_TIER_WINDOW_MIN",
    50
)
# Tier-A cutoff percentile for scorecard capital tiering.
LATTICE_TIER_A_PCT = _env_float(
    "LATTICE_TIER_A_PCT",
    60.0
)
LATTICE_TIER_A_PCT_CAUTION_BUMP = _env_float(
    "LATTICE_TIER_A_PCT_CAUTION_BUMP",
    10.0
)
LATTICE_TIER_A_PCT_RISK_OFF_BUMP = _env_float(
    "LATTICE_TIER_A_PCT_RISK_OFF_BUMP",
    20.0
)
# Tier-B floor percentile; below it is Tier C (no capital, still alertable).
LATTICE_TIER_B_PCT = _env_float(
    "LATTICE_TIER_B_PCT",
    30.0
)
# Tier-B action: reduced size (fraction of SIZE_USD) or alert-only (no capital).
LATTICE_TIER_B_SIZE_FRAC = _env_float(
    "LATTICE_TIER_B_SIZE_FRAC",
    0.5
)
LATTICE_TIER_B_ALERT_ONLY = _env_bool(
    "LATTICE_TIER_B_ALERT_ONLY",
    False
)

# --- Shadow trench/narrative regime --------------------------------------- #
# Telemetry only. The runner logs trench_* fields on entry decisions so HOT vs
# non-HOT periods can be validated before any capital behavior is gated by it.
LATTICE_TRENCH_SHADOW_ENABLED = _env_bool(
    "LATTICE_TRENCH_SHADOW_ENABLED",
    True
)
LATTICE_TRENCH_SHADOW_WINDOW_SECONDS = _env_float(
    "LATTICE_TRENCH_SHADOW_WINDOW_SECONDS",
    3600.0
)
LATTICE_TRENCH_SHADOW_HOT_SCORE = _env_float(
    "LATTICE_TRENCH_SHADOW_HOT_SCORE",
    65.0
)
LATTICE_TRENCH_SHADOW_EUPHORIA_SCORE = _env_float(
    "LATTICE_TRENCH_SHADOW_EUPHORIA_SCORE",
    85.0
)
LATTICE_TRENCH_SHADOW_COLD_SCORE = _env_float(
    "LATTICE_TRENCH_SHADOW_COLD_SCORE",
    25.0
)
LATTICE_TRENCH_SHADOW_HOT_CANDIDATES_PER_HOUR = _env_float(
    "LATTICE_TRENCH_SHADOW_HOT_CANDIDATES_PER_HOUR",
    140.0
)
LATTICE_TRENCH_SHADOW_HOT_ALERTS_PER_HOUR = _env_float(
    "LATTICE_TRENCH_SHADOW_HOT_ALERTS_PER_HOUR",
    24.0
)
LATTICE_TRENCH_SHADOW_HOT_ENTRIES_PER_HOUR = _env_float(
    "LATTICE_TRENCH_SHADOW_HOT_ENTRIES_PER_HOUR",
    8.0
)
LATTICE_TRENCH_SHADOW_HOT_OPEN_UPNL_USD = _env_float(
    "LATTICE_TRENCH_SHADOW_HOT_OPEN_UPNL_USD",
    250.0
)
LATTICE_TRENCH_SHADOW_HOT_PC5 = _env_float(
    "LATTICE_TRENCH_SHADOW_HOT_PC5",
    12.0
)
LATTICE_TRENCH_SHADOW_HOT_VOLUME_5M = _env_float(
    "LATTICE_TRENCH_SHADOW_HOT_VOLUME_5M",
    750.0
)
LATTICE_TRENCH_PROXIMITY_TOKENS = _env_int(
    "LATTICE_TRENCH_PROXIMITY_TOKENS",
    5
)
_LATTICE_TRENCH_DEFAULT_KOLS = (
    "ansem,blknoiz06,murad,murad mahmudov,muststopmurad,"
    "banditxbt,wale swoosh,james wynn,jameswynnreal"
)
_LATTICE_TRENCH_DEFAULT_TRIGGERS = (
    "token,coin,launch,launched,launches,meta,tokenized,"
    "backed,shilled,raided,kol,influencer,pumpfun"
)
_LATTICE_TRENCH_DEFAULT_WATCH = (
    _LATTICE_TRENCH_DEFAULT_KOLS
    + ",kol token,kol coin,kol launch,kol memecoin,influencer token,"
    "influencer coin,celebrity token,tokenized kol,degen kol,solana kol,"
    "attention meta,narrative shift,meta rotation,story coin,lore coin,"
    "wagmi,ngmi,gm,gn,ser,fren,alpha,degen,ape,aped,diamond hands,"
    "paper hands,to the moon,wen,moon,pump,dump,rug,rugged,based,"
    "redacted,cope,seethe,mald,jeet,bagholder,shitcoin,fomo,fud,hodl,"
    "rekt,dyor,alpha call,narrative play,meta,supercycle,trenches,"
    "stimmy,vibe check,looks rare,iykyk,lfg,buy the dip,btd,"
    "pepe,pepe the frog,frog meta,wojak,doomer,yes chad,gigachad,chad,"
    "sigma,doge,shiba,dogwifhat,wif,popcat,pixel cat,mew,pengu,pudgy,"
    "brett,mog,giga,ponke,bonk,pnut,fartcoin,cat meta,dog meta,"
    "political meta,absurd meta,ai meta,agent meta"
)
LATTICE_TRENCH_KOL_TERMS = _env_list(
    "LATTICE_TRENCH_KOL_TERMS",
    _LATTICE_TRENCH_DEFAULT_KOLS
)
LATTICE_TRENCH_TRIGGER_TERMS = _env_list(
    "LATTICE_TRENCH_TRIGGER_TERMS",
    _LATTICE_TRENCH_DEFAULT_TRIGGERS
)
LATTICE_TRENCH_WATCH_TERMS = _env_list(
    "LATTICE_TRENCH_WATCH_TERMS",
    _LATTICE_TRENCH_DEFAULT_WATCH
)

# --- Layer 3: tier -> stop coupling (default OFF; diagnostics first) -------- #
# Widen the initial invalidation for reduced-size Tier-B entries. Capped at the
# ATR-stop max so it can never exceed the configured worst-case stop width.
LATTICE_TIER_STOP_COUPLING_ENABLED = _env_bool(
    "LATTICE_TIER_STOP_COUPLING_ENABLED",
    False
)
LATTICE_TIER_B_STOP_WIDEN_MULT = _env_float(
    "LATTICE_TIER_B_STOP_WIDEN_MULT",
    1.3
)

# Paper-buy selectivity after an ENTRY SIGNAL is formed. Alerts still send when
# this gate blocks; only simulated capital deployment is skipped.
LATTICE_PAPER_BUY_GATE_ENABLED = _env_bool(
    "LATTICE_PAPER_BUY_GATE_ENABLED",
    True
)

LATTICE_PAPER_BUY_MIN_BREADTH = _env_float(
    "LATTICE_PAPER_BUY_MIN_BREADTH",
    0.35
)

LATTICE_PAPER_BUY_MIN_PRICE_CHANGE_5M = _env_float(
    "LATTICE_PAPER_BUY_MIN_PRICE_CHANGE_5M",
    4.0
)

LATTICE_PAPER_BUY_MAX_PRICE_CHANGE_5M = _env_float(
    "LATTICE_PAPER_BUY_MAX_PRICE_CHANGE_5M",
    20.0
)

# Retired rolling weak-regime guard. Kept as inert rollback configuration;
# current runtime no longer throttles entries based on risk-on/off state.
LATTICE_ENTRY_REGIME_GUARD_ENABLED = _env_bool(
    "LATTICE_ENTRY_REGIME_GUARD_ENABLED",
    False
)

LATTICE_ENTRY_REGIME_LOOKBACK_H = _env_float(
    "LATTICE_ENTRY_REGIME_LOOKBACK_H",
    24.0
)

LATTICE_ENTRY_REGIME_MIN_TRADES = _env_int(
    "LATTICE_ENTRY_REGIME_MIN_TRADES",
    20
)

LATTICE_ENTRY_REGIME_DAY_MIN_TRADES = _env_int(
    "LATTICE_ENTRY_REGIME_DAY_MIN_TRADES",
    10
)

LATTICE_ENTRY_REGIME_SCARCE_2X_RATE = _env_float(
    "LATTICE_ENTRY_REGIME_SCARCE_2X_RATE",
    0.10
)

LATTICE_ENTRY_REGIME_MAX_WIN_RATE = _env_float(
    "LATTICE_ENTRY_REGIME_MAX_WIN_RATE",
    0.15
)

LATTICE_ENTRY_REGIME_MIN_INITIAL_STOP_RATE = _env_float(
    "LATTICE_ENTRY_REGIME_MIN_INITIAL_STOP_RATE",
    0.70
)

LATTICE_ENTRY_REGIME_MAX_AVG_PEAK_MULT = _env_float(
    "LATTICE_ENTRY_REGIME_MAX_AVG_PEAK_MULT",
    1.45
)

LATTICE_ENTRY_REGIME_MIN_LOSS_USD = _env_float(
    "LATTICE_ENTRY_REGIME_MIN_LOSS_USD",
    50.0
)

LATTICE_ENTRY_REGIME_CAUTION_MAX_ENTRIES_PER_HOUR = _env_int(
    "LATTICE_ENTRY_REGIME_CAUTION_MAX_ENTRIES_PER_HOUR",
    3
)

LATTICE_ENTRY_REGIME_RISK_OFF_MAX_ENTRIES_PER_HOUR = _env_int(
    "LATTICE_ENTRY_REGIME_RISK_OFF_MAX_ENTRIES_PER_HOUR",
    2
)

LATTICE_ENTRY_REGIME_CAUTION_MIN_CONVICTION_BUMP = _env_float(
    "LATTICE_ENTRY_REGIME_CAUTION_MIN_CONVICTION_BUMP",
    0.02
)

LATTICE_ENTRY_REGIME_RISK_OFF_MIN_CONVICTION_BUMP = _env_float(
    "LATTICE_ENTRY_REGIME_RISK_OFF_MIN_CONVICTION_BUMP",
    0.05
)

LATTICE_ENTRY_REGIME_RISK_OFF_MIN_BREADTH = _env_float(
    "LATTICE_ENTRY_REGIME_RISK_OFF_MIN_BREADTH",
    0.45
)

LATTICE_ENTRY_REGIME_RISK_OFF_MIN_BUY_SELL_RATIO = _env_float(
    "LATTICE_ENTRY_REGIME_RISK_OFF_MIN_BUY_SELL_RATIO",
    1.50
)

LATTICE_ENTRY_REGIME_RISK_OFF_EVIDENCE_BUCKET = _env(
    "LATTICE_ENTRY_REGIME_RISK_OFF_EVIDENCE_BUCKET",
    "ready"
).strip().lower()

# Manual-trading context only: attach a compact news/narrative label to
# Lattice ENTRY SIGNAL alerts. This never gates alerts or paper buys.
LATTICE_NARRATIVE_CONTEXT_ENABLED = _env_bool(
    "LATTICE_NARRATIVE_CONTEXT_ENABLED",
    True
)

LATTICE_NARRATIVE_CONTEXT_NEWS_ENABLED = _env_bool(
    "LATTICE_NARRATIVE_CONTEXT_NEWS_ENABLED",
    True
)

LATTICE_NARRATIVE_CONTEXT_TOKEN_METADATA_ENABLED = _env_bool(
    "LATTICE_NARRATIVE_CONTEXT_TOKEN_METADATA_ENABLED",
    True
)

LATTICE_NARRATIVE_CONTEXT_LOOKBACK_DAYS = _env_int(
    "LATTICE_NARRATIVE_CONTEXT_LOOKBACK_DAYS",
    30
)

LATTICE_NARRATIVE_CONTEXT_MAX_NEWS_RESULTS = _env_int(
    "LATTICE_NARRATIVE_CONTEXT_MAX_NEWS_RESULTS",
    10
)

LATTICE_NARRATIVE_CONTEXT_MIN_RELEVANCE = _env_float(
    "LATTICE_NARRATIVE_CONTEXT_MIN_RELEVANCE",
    1.0
)

LATTICE_NARRATIVE_CONTEXT_TIMEOUT_SECONDS = _env_float(
    "LATTICE_NARRATIVE_CONTEXT_TIMEOUT_SECONDS",
    4.0
)

LATTICE_NARRATIVE_CONTEXT_CACHE_SECONDS = _env_float(
    "LATTICE_NARRATIVE_CONTEXT_CACHE_SECONDS",
    1800.0
)

LATTICE_NARRATIVE_CONTEXT_ALWAYS_SHOW = _env_bool(
    "LATTICE_NARRATIVE_CONTEXT_ALWAYS_SHOW",
    True
)

LATTICE_NARRATIVE_CONTEXT_X_LINK_ENABLED = _env_bool(
    "LATTICE_NARRATIVE_CONTEXT_X_LINK_ENABLED",
    True
)

LATTICE_NARRATIVE_CONTEXT_KEYWORDS = _env_list(
    "LATTICE_NARRATIVE_CONTEXT_KEYWORDS",
    (
        "justice,police,brutality,death,died,killed,murder,"
        "custody,bail,released,parole,victim,officer,shooting,"
        "stabbing,convict,crime"
    )
)

# Discovery-layer exit engine. `old` preserves the original price-only
# paper_trade.manage behavior; `new` enables the feature-aware state machine in
# discovery/manager.py.
LATTICE_EXIT_ENGINE = _env(
    "LATTICE_EXIT_ENGINE",
    "new"
).lower()

LATTICE_EXIT_TP_MODE = _env(
    "LATTICE_EXIT_TP_MODE",
    "tail"
).lower()

LATTICE_EXIT_INITIAL_STOP_PCT = _env_float(
    "LATTICE_EXIT_INITIAL_STOP_PCT",
    0.30
)

LATTICE_STRICT_EARLY_EXIT_ENABLED = _env_bool(
    "LATTICE_STRICT_EARLY_EXIT_ENABLED",
    True
)

LATTICE_STRICT_EARLY_EXIT_LOSS_PCT = _env_float(
    "LATTICE_STRICT_EARLY_EXIT_LOSS_PCT",
    0.12
)

LATTICE_STRICT_EARLY_EXIT_MIN_WEAK_SIGNALS = _env_int(
    "LATTICE_STRICT_EARLY_EXIT_MIN_WEAK_SIGNALS",
    2
)

LATTICE_STRICT_EARLY_EXIT_CONFIRM_TICKS = _env_int(
    "LATTICE_STRICT_EARLY_EXIT_CONFIRM_TICKS",
    2
)

LATTICE_STRICT_EARLY_EXIT_MAX_PRESSURE = _env_float(
    "LATTICE_STRICT_EARLY_EXIT_MAX_PRESSURE",
    POSITION_STRICT_EARLY_EXIT_MAX_PRESSURE
)

LATTICE_STRICT_EARLY_EXIT_MAX_VOLUME_LIQUIDITY_RATIO = _env_float(
    "LATTICE_STRICT_EARLY_EXIT_MAX_VOLUME_LIQUIDITY_RATIO",
    POSITION_STRICT_EARLY_EXIT_MAX_VOLUME_LIQUIDITY_RATIO
)

LATTICE_STRICT_EARLY_EXIT_MAX_BUY_SELL_RATIO = _env_float(
    "LATTICE_STRICT_EARLY_EXIT_MAX_BUY_SELL_RATIO",
    POSITION_STRICT_EARLY_EXIT_MAX_BUY_SELL_RATIO
)

LATTICE_LIQUIDITY_COLLAPSE_EXIT_ENABLED = _env_bool(
    "LATTICE_LIQUIDITY_COLLAPSE_EXIT_ENABLED",
    POSITION_LIQUIDITY_COLLAPSE_EXIT_ENABLED
)

LATTICE_LIQUIDITY_COLLAPSE_FROM_ENTRY_PCT = _env_float(
    "LATTICE_LIQUIDITY_COLLAPSE_FROM_ENTRY_PCT",
    POSITION_LIQUIDITY_COLLAPSE_FROM_ENTRY_PCT
)

LATTICE_LIQUIDITY_COLLAPSE_FROM_PEAK_PCT = _env_float(
    "LATTICE_LIQUIDITY_COLLAPSE_FROM_PEAK_PCT",
    POSITION_LIQUIDITY_COLLAPSE_FROM_PEAK_PCT
)

LATTICE_LIQUIDITY_COLLAPSE_MIN_REFERENCE_USD = _env_float(
    "LATTICE_LIQUIDITY_COLLAPSE_MIN_REFERENCE_USD",
    POSITION_LIQUIDITY_COLLAPSE_MIN_REFERENCE_USD
)

LATTICE_SELL_ONLY_FLOW_EXIT_ENABLED = _env_bool(
    "LATTICE_SELL_ONLY_FLOW_EXIT_ENABLED",
    POSITION_SELL_ONLY_FLOW_EXIT_ENABLED
)

LATTICE_SELL_ONLY_FLOW_MAX_BUY_VOLUME_5M_USD = _env_float(
    "LATTICE_SELL_ONLY_FLOW_MAX_BUY_VOLUME_5M_USD",
    POSITION_SELL_ONLY_FLOW_MAX_BUY_VOLUME_5M_USD
)

LATTICE_SELL_ONLY_FLOW_MAX_BUY_SELL_VOLUME_RATIO = _env_float(
    "LATTICE_SELL_ONLY_FLOW_MAX_BUY_SELL_VOLUME_RATIO",
    POSITION_SELL_ONLY_FLOW_MAX_BUY_SELL_VOLUME_RATIO
)

LATTICE_SELL_ONLY_FLOW_MIN_SELL_VOLUME_5M_USD = _env_float(
    "LATTICE_SELL_ONLY_FLOW_MIN_SELL_VOLUME_5M_USD",
    POSITION_SELL_ONLY_FLOW_MIN_SELL_VOLUME_5M_USD
)

LATTICE_SELL_ONLY_FLOW_MIN_SELL_ENTRY_NOTIONAL_MULTIPLE = _env_float(
    "LATTICE_SELL_ONLY_FLOW_MIN_SELL_ENTRY_NOTIONAL_MULTIPLE",
    POSITION_SELL_ONLY_FLOW_MIN_SELL_ENTRY_NOTIONAL_MULTIPLE
)

LATTICE_SELL_ONLY_FLOW_MAX_PRICE_MULTIPLE = _env_float(
    "LATTICE_SELL_ONLY_FLOW_MAX_PRICE_MULTIPLE",
    POSITION_SELL_ONLY_FLOW_MAX_PRICE_MULTIPLE
)

LATTICE_NO_PROGRESS_EXIT_ENABLED = _env_bool(
    "LATTICE_NO_PROGRESS_EXIT_ENABLED",
    True
)

LATTICE_NO_PROGRESS_EXIT_MIN_SECONDS = _env_float(
    "LATTICE_NO_PROGRESS_EXIT_MIN_SECONDS",
    45 * 60
)

LATTICE_NO_PROGRESS_EXIT_MAX_PEAK_MULTIPLE = _env_float(
    "LATTICE_NO_PROGRESS_EXIT_MAX_PEAK_MULTIPLE",
    1.20
)

LATTICE_NO_PROGRESS_EXIT_MAX_PRESSURE = _env_float(
    "LATTICE_NO_PROGRESS_EXIT_MAX_PRESSURE",
    35
)

LATTICE_NO_PROGRESS_EXIT_MAX_BUY_SELL_RATIO = _env_float(
    "LATTICE_NO_PROGRESS_EXIT_MAX_BUY_SELL_RATIO",
    0.90
)

LATTICE_BREAK_EVEN_EXIT_ENABLED = _env_bool(
    "LATTICE_BREAK_EVEN_EXIT_ENABLED",
    False
)

LATTICE_BREAK_EVEN_ARM_MULTIPLE = _env_float(
    "LATTICE_BREAK_EVEN_ARM_MULTIPLE",
    1.30
)

LATTICE_BREAK_EVEN_FLOOR_MULTIPLE = _env_float(
    "LATTICE_BREAK_EVEN_FLOOR_MULTIPLE",
    1.02
)

LATTICE_EXIT_SCALE_OUT_LADDER = _env_scale_out_ladder(
    "LATTICE_EXIT_SCALE_OUT_LADDER",
    (
        (3.00, 0.50),
        (6.00, 0.95),
    )
)

LATTICE_TAIL_COST_RECOVERY_MULTIPLE = _env_float(
    "LATTICE_TAIL_COST_RECOVERY_MULTIPLE",
    2.0
)

LATTICE_TAIL_COST_RECOVERY_PCT = _env_float(
    "LATTICE_TAIL_COST_RECOVERY_PCT",
    1.0
)

LATTICE_TAIL_COST_RECOVERY_MAX_SELL_PCT = _env_float(
    "LATTICE_TAIL_COST_RECOVERY_MAX_SELL_PCT",
    0.55
)

LATTICE_TAIL_SCALE_OUT_TIERS = _env_fraction_tiers(
    "LATTICE_TAIL_SCALE_OUT_TIERS",
    (
        (5.0, 0.10),
        (10.0, 0.10),
        (15.0, 0.10),
        (20.0, 0.10),
    )
)

LATTICE_Q3_FIB_EXTENSIONS = _env_float_tuple(
    "LATTICE_Q3_FIB_EXTENSIONS",
    "2.618,4.236"
)

LATTICE_Q3_TP_NODE_SNAP_BAND = _env_float(
    "LATTICE_Q3_TP_NODE_SNAP_BAND",
    0.15
)

LATTICE_Q3_MIN_TARGET_MULTIPLE = _env_float(
    "LATTICE_Q3_MIN_TARGET_MULTIPLE",
    2.0
)

LATTICE_Q3_ATR_TRAIL_ENABLED = _env_bool(
    "LATTICE_Q3_ATR_TRAIL_ENABLED",
    False
)

LATTICE_Q3_ATR_TRAIL_K = _env_float(
    "LATTICE_Q3_ATR_TRAIL_K",
    POSITION_ATR_STOP_K
)

LATTICE_Q3_VP_FLOOR_BUFFER_PCT = _env_float(
    "LATTICE_Q3_VP_FLOOR_BUFFER_PCT",
    1.0
)

LATTICE_EXIT_SCALE_STOP_FLOORS = _env_multiple_floor_map(
    "LATTICE_EXIT_SCALE_STOP_FLOORS",
    (
        (3.00, 1.50),
        (6.00, 3.00),
        (10.00, 5.00),
    )
)

LATTICE_MOONBAG_STEP_FLOORS_ENABLED = _env_bool(
    "LATTICE_MOONBAG_STEP_FLOORS_ENABLED",
    True
)

LATTICE_MOONBAG_STEP_TRIGGER_MULT = _env_float(
    "LATTICE_MOONBAG_STEP_TRIGGER_MULT",
    20.0
)

LATTICE_MOONBAG_STEP_INTERVAL_MULT = _env_float(
    "LATTICE_MOONBAG_STEP_INTERVAL_MULT",
    10.0
)

LATTICE_MOONBAG_STEP_FLOOR_LAG_MULT = _env_float(
    "LATTICE_MOONBAG_STEP_FLOOR_LAG_MULT",
    10.0
)

LATTICE_POST_SCALE_TRAIL_PCT = _env_float(
    "LATTICE_POST_SCALE_TRAIL_PCT",
    0.0
)

LATTICE_HIGH_MULT_TRAIL_TRIGGER = _env_float(
    "LATTICE_HIGH_MULT_TRAIL_TRIGGER",
    POSITION_HIGH_MULT_TRAIL_TRIGGER
)

LATTICE_HIGH_MULT_TRAIL_PCT = _env_float(
    "LATTICE_HIGH_MULT_TRAIL_PCT",
    POSITION_HIGH_MULT_TRAIL_PCT
)

IGNITION_MIN_PRICE_JUMP = 1.30

IGNITION_MIN_VOLUME_LIQUIDITY_RATIO = 0.08

IGNITION_MIN_BUY_SELL_RATIO = 1.50

IGNITION_MIN_VOLUME_USD = 1000

MIN_BONDING_CURVE_FDV_USD = 2000

PUMPFUN_TOTAL_SUPPLY = 1000000000

PUMPFUN_INITIAL_VIRTUAL_TOKEN_RESERVES = 1073000000

PUMPFUN_INITIAL_VIRTUAL_SOL_RESERVES = 30

PUMPFUN_BONDING_CURVE_REAL_TOKEN_RESERVES = 793100000

PUMPFUN_SOL_USD_FALLBACK = 150

IGNITION_BONDING_CURVE_BANDS = [
    {
        "name": "bonding $2k-$10k",
        "min_fdv": 2000,
        "max_fdv": 10000,
        "min_price_jump": 1.30,
        "min_volume_liquidity_ratio": 0.20,
        "min_buy_sell_ratio": 2.00,
        "min_volume_usd": 400
    },
    {
        "name": "bonding $10k-$20k",
        "min_fdv": 10000,
        "max_fdv": 20000,
        "min_price_jump": 1.30,
        "min_volume_liquidity_ratio": 0.14,
        "min_buy_sell_ratio": 1.70,
        "min_volume_usd": 750
    },
    {
        "name": "bonding $20k-$60k",
        "min_fdv": 20000,
        "max_fdv": 60000,
        "min_price_jump": 1.30,
        "min_volume_liquidity_ratio": 0.09,
        "min_buy_sell_ratio": 1.50,
        "min_volume_usd": 1000
    }
]

IGNITION_MIGRATED_BANDS = [
    {
        "name": "migrated <$10k",
        "min_fdv": 0,
        "max_fdv": 10000,
        "min_price_jump": 1.30,
        "min_volume_liquidity_ratio": 0.16,
        "min_buy_sell_ratio": 1.80,
        "min_volume_usd": 500
    },
    {
        "name": "migrated $10k-$20k",
        "min_fdv": 10000,
        "max_fdv": 20000,
        "min_price_jump": 1.30,
        "min_volume_liquidity_ratio": 0.12,
        "min_buy_sell_ratio": 1.60,
        "min_volume_usd": 750
    },
    {
        "name": "migrated $20k-$60k",
        "min_fdv": 20000,
        "max_fdv": 60000,
        "min_price_jump": 1.30,
        "min_volume_liquidity_ratio": 0.08,
        "min_buy_sell_ratio": 1.50,
        "min_volume_usd": 1000
    }
]

IGNITION_MIGRATED_FRAGILE_MIN_FDV = 40000

IGNITION_MIGRATED_FRAGILE_MAX_VOLUME_LIQUIDITY_RATIO_5M = 0.25

IGNITION_MIGRATED_BUY_SELL_SCORE_CAP_TXNS_5M = 35

IGNITION_MIGRATED_BUY_SELL_SCORE_CAP_POINTS = 10

IGNITION_MIGRATED_STALE_MAX_VOLUME_SHARE_5M_1H = 0.35

IGNITION_MIGRATED_STALE_VOLUME_SHARE_PENALTY = 12

# Penalty audit 2026-06-10 (analysis/_penalty_audit.py, _penalty8_split.py):
# the -8 "extended 6h move >=150%" penalty fired on the BEST cohort — 33%/42%
# h6/h24 runner rate vs 22%/37% for zero-penalty alerts (78/79 of labeled
# penalty=8 alerts were this source; lifetime label confirms in every week
# May 11-31, n=257 deduped). Momentum continuation is a positive signal at
# this horizon, so the penalty is demoted to 0 by default. Set 8 to revert.
IGNITION_EXTENDED_6H_MOVE_PENALTY = _env_float(
    "IGNITION_EXTENDED_6H_MOVE_PENALTY",
    0
)

IGNITION_MIGRATED_HIGH_QUALITY_MIN_PRICE_JUMP = 2.00

IGNITION_MIGRATED_HIGH_QUALITY_MIN_VOLUME_LIQUIDITY_RATIO_5M = 0.40

IGNITION_MIGRATED_HIGH_QUALITY_MIN_TXNS_5M = 50

IGNITION_MIGRATED_HIGH_QUALITY_MIN_VOLUME_SHARE_5M_1H = 0.50

HYPEREVM_IGNITION_MIN_PRICE_CHANGE_5M = _env_float(
    "HYPEREVM_IGNITION_MIN_PRICE_CHANGE_5M",
    50
)

HYPEREVM_IGNITION_MIN_PRICE_CHANGE_24H = _env_float(
    "HYPEREVM_IGNITION_MIN_PRICE_CHANGE_24H",
    100
)

HYPEREVM_IGNITION_MIN_LIQUIDITY_USD = _env_float(
    "HYPEREVM_IGNITION_MIN_LIQUIDITY_USD",
    HYPEREVM_SCANNER_MIN_LIQUIDITY_USD
)

HYPEREVM_IGNITION_MAX_FDV_USD = _env_float(
    "HYPEREVM_IGNITION_MAX_FDV_USD",
    HYPEREVM_SCANNER_MAX_FDV_USD
)

HYPEREVM_IGNITION_MIN_VOLUME_1H_USD = _env_float(
    "HYPEREVM_IGNITION_MIN_VOLUME_1H_USD",
    3000
)

HYPEREVM_IGNITION_SCORE = _env_int(
    "HYPEREVM_IGNITION_SCORE",
    40
)

POSITION_HYPEREVM_POSITION_SIZE_USD = _env_float(
    "POSITION_HYPEREVM_POSITION_SIZE_USD",
    40
)

POSITION_HYPEREVM_SCALE_OUT_LADDER = _env_scale_out_ladder(
    "POSITION_HYPEREVM_SCALE_OUT_LADDER",
    (
        (4.00, 0.30),
        (10.00, 0.50),
    )
)

POSITION_HYPEREVM_TAKE_PROFIT_SELL_PCT = _env_float(
    "POSITION_HYPEREVM_TAKE_PROFIT_SELL_PCT",
    0.30
)

POSITION_HYPEREVM_MAX_SCALE_OUT_PCT = _env_float(
    "POSITION_HYPEREVM_MAX_SCALE_OUT_PCT",
    0.50
)

IGNITION_BONDING_MOMENTUM_MIN_PRICE_CHANGE_1H = 12

IGNITION_BONDING_MOMENTUM_MIN_PRICE_CHANGE_6H = 50

IGNITION_BONDING_MOMENTUM_MIN_VOLUME_LIQUIDITY_RATIO_1H = 0.50

IGNITION_BONDING_MOMENTUM_MIN_BUY_SELL_RATIO_1H = 0.90

IGNITION_BONDING_MOMENTUM_MIN_TXNS_1H = 20

IGNITION_BONDING_MOMENTUM_MIN_VOLUME_MULTIPLE_1H = 2

IGNITION_BONDING_EXTENDED_COOLING_MIN_PRICE_CHANGE = 300

IGNITION_BONDING_EXTENDED_COOLING_MAX_VOLUME_LIQUIDITY_RATIO_5M = 0.40

IGNITION_BONDING_HIGH_CONVICTION_MIN_VOLUME_LIQUIDITY_RATIO_5M = 1.00

IGNITION_BONDING_HIGH_CONVICTION_MIN_TXNS_1H = 300

IGNITION_BONDING_HIGH_CONVICTION_MIN_VOLUME_1H = 10000

IGNITION_BONDING_EARLY_REVIVAL_MIN_VOLUME_LIQUIDITY_RATIO_5M = 0.40

IGNITION_BONDING_EARLY_REVIVAL_MIN_TXNS_5M = 20

IGNITION_BONDING_EARLY_REVIVAL_MIN_BUY_SELL_RATIO_5M = 1.20

IGNITION_MIGRATED_REVIVAL_MIN_DRAWDOWN_PCT = _env_float(
    "IGNITION_MIGRATED_REVIVAL_MIN_DRAWDOWN_PCT",
    0.50
)

IGNITION_MIGRATED_REVIVAL_MAX_DRAWDOWN_PCT = _env_float(
    "IGNITION_MIGRATED_REVIVAL_MAX_DRAWDOWN_PCT",
    0.90
)

IGNITION_MIGRATED_REVIVAL_MIN_VOLUME_LIQUIDITY_RATIO_5M = _env_float(
    "IGNITION_MIGRATED_REVIVAL_MIN_VOLUME_LIQUIDITY_RATIO_5M",
    0.30
)

IGNITION_MIGRATED_REVIVAL_MIN_TXNS_5M = _env_int(
    "IGNITION_MIGRATED_REVIVAL_MIN_TXNS_5M",
    15
)

IGNITION_MIGRATED_REVIVAL_MIN_BUY_SELL_RATIO_5M = _env_float(
    "IGNITION_MIGRATED_REVIVAL_MIN_BUY_SELL_RATIO_5M",
    1.20
)

IGNITION_MIGRATED_REVIVAL_MIN_VOLUME_5M_USD = _env_float(
    "IGNITION_MIGRATED_REVIVAL_MIN_VOLUME_5M_USD",
    500
)

IGNITION_BONDING_SCALP_MIN_VOLUME_LIQUIDITY_RATIO_5M = 2.00

IGNITION_BONDING_SCALP_MAX_TXNS_1H = 150

IGNITION_LOW_FDV_ACCUMULATION_MAX_FDV = _env_float(
    "IGNITION_LOW_FDV_ACCUMULATION_MAX_FDV",
    10000
)

IGNITION_LOW_FDV_ACCUMULATION_MIN_LIQUIDITY = _env_float(
    "IGNITION_LOW_FDV_ACCUMULATION_MIN_LIQUIDITY",
    1000
)

IGNITION_LOW_FDV_ACCUMULATION_MIN_VOLUME_LIQUIDITY_RATIO_5M = _env_float(
    "IGNITION_LOW_FDV_ACCUMULATION_MIN_VOLUME_LIQUIDITY_RATIO_5M",
    1.50
)

IGNITION_LOW_FDV_ACCUMULATION_MIN_VOLUME_LIQUIDITY_RATIO_1H = _env_float(
    "IGNITION_LOW_FDV_ACCUMULATION_MIN_VOLUME_LIQUIDITY_RATIO_1H",
    10.0
)

IGNITION_LOW_FDV_ACCUMULATION_MAX_PRICE_CHANGE_5M = _env_float(
    "IGNITION_LOW_FDV_ACCUMULATION_MAX_PRICE_CHANGE_5M",
    0
)

IGNITION_LOW_FDV_ACCUMULATION_MIN_PRICE_CHANGE_1H = _env_float(
    "IGNITION_LOW_FDV_ACCUMULATION_MIN_PRICE_CHANGE_1H",
    20
)

IGNITION_LOW_FDV_ACCUMULATION_MIN_PRICE_CHANGE_6H = _env_float(
    "IGNITION_LOW_FDV_ACCUMULATION_MIN_PRICE_CHANGE_6H",
    40
)

IGNITION_LOW_FDV_ACCUMULATION_MIN_BUY_SELL_RATIO_5M = _env_float(
    "IGNITION_LOW_FDV_ACCUMULATION_MIN_BUY_SELL_RATIO_5M",
    1.20
)

IGNITION_LOW_FDV_ACCUMULATION_MIN_BUY_SELL_RATIO_1H = _env_float(
    "IGNITION_LOW_FDV_ACCUMULATION_MIN_BUY_SELL_RATIO_1H",
    1.00
)

# RPCs

ALCHEMY_RPC_URLS = {
    "ethereum": _alchemy_rpc_url(
        "ALCHEMY_ETHEREUM_RPC_URL",
        "eth-mainnet"
    ),
    "base": _alchemy_rpc_url(
        "ALCHEMY_BASE_RPC_URL",
        "base-mainnet"
    ) or "https://mainnet.base.org",
    "solana": _alchemy_rpc_url(
        "ALCHEMY_SOLANA_RPC_URL",
        "solana-mainnet"
    ),
    "hyperevm": (
        _alchemy_rpc_url(
            "ALCHEMY_HYPEREVM_RPC_URL",
            "hyperliquid-mainnet"
        )
        or "https://rpc.hyperliquid.xyz/evm"
    )
}

HYPEREVM_RPC_URL = _env(
    "HYPEREVM_RPC_URL",
    ALCHEMY_RPC_URLS.get("hyperevm", "")
)

if HYPEREVM_RPC_URL:
    ALCHEMY_RPC_URLS["hyperevm"] = HYPEREVM_RPC_URL

ALCHEMY_RPC_URLS["hyperliquid"] = ALCHEMY_RPC_URLS.get("hyperevm", "")

PUMPFUN_DISCOVERY_ENABLED = _env_bool(
    "PUMPFUN_DISCOVERY_ENABLED",
    True
)

PUMPFUN_DISCOVERY_ENDPOINTS = _env_list(
    "PUMPFUN_DISCOVERY_ENDPOINTS",
    (
        "https://frontend-api-v3.pump.fun/coins"
        "?offset=0&limit={limit}&sort=created_timestamp"
        "&order=DESC&includeNsfw=true,"
        "https://frontend-api.pump.fun/coins"
        "?offset=0&limit={limit}&sort=created_timestamp"
        "&order=DESC&includeNsfw=true"
    )
)

PUMPFUN_DISCOVERY_LIMIT = _env_int(
    "PUMPFUN_DISCOVERY_LIMIT",
    75
)

PUMPFUN_DISCOVERY_CANDIDATE_TTL_SECONDS = _env_int(
    "PUMPFUN_DISCOVERY_CANDIDATE_TTL_SECONDS",
    6 * 3600
)

TELEGRAM_BOT_TOKEN = _env("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = _env("TELEGRAM_CHAT_ID")
TELEGRAM_CHAT_IDS = _env_list(
    "TELEGRAM_CHAT_IDS",
    TELEGRAM_CHAT_ID
)

ORGANIC_TELEGRAM_ALERTS_ENABLED = _env_bool(
    "ORGANIC_TELEGRAM_ALERTS_ENABLED",
    True
)

IGNITION_SUMMARY_CHAT_ENABLED = _env_bool(
    "IGNITION_SUMMARY_CHAT_ENABLED",
    False
)
IGNITION_SUMMARY_CHAT_ID = _env(
    "IGNITION_SUMMARY_CHAT_ID",
    TELEGRAM_CHAT_ID
)
IGNITION_SUMMARY_CHAT_IDS = _env_list(
    "IGNITION_SUMMARY_CHAT_IDS",
    ",".join(TELEGRAM_CHAT_IDS)
)

TELEGRAM_AGENT_ENABLED = _env_bool(
    "TELEGRAM_AGENT_ENABLED",
    False
)

TELEGRAM_AGENT_ALLOWED_CHAT_IDS = _env_list(
    "TELEGRAM_AGENT_ALLOWED_CHAT_IDS",
    ",".join(TELEGRAM_CHAT_IDS)
)

TELEGRAM_AGENT_ADMIN_USER_IDS = _env_list(
    "TELEGRAM_AGENT_ADMIN_USER_IDS",
    ""
)

# Chats where the explicitly configured public read-only commands may be used by
# ANY member, no admin required. Every other command stays admin-only. Typically
# the public broadcast groups.
TELEGRAM_AGENT_PUBLIC_CHAT_IDS = _env_list(
    "TELEGRAM_AGENT_PUBLIC_CHAT_IDS",
    ""
)

TELEGRAM_AGENT_PUBLIC_COMMANDS = _env_list(
    "TELEGRAM_AGENT_PUBLIC_COMMANDS",
    "/og,/help,/agent"
)

TELEGRAM_AGENT_WRITE_ACTIONS_ENABLED = _env_bool(
    "TELEGRAM_AGENT_WRITE_ACTIONS_ENABLED",
    False
)

TELEGRAM_AGENT_LIVE_ACTIONS_ENABLED = _env_bool(
    "TELEGRAM_AGENT_LIVE_ACTIONS_ENABLED",
    False
)

TELEGRAM_AGENT_RESTART_ENABLED = _env_bool(
    "TELEGRAM_AGENT_RESTART_ENABLED",
    False
)

TELEGRAM_AGENT_RESTART_STATUS_PATH = _env(
    "TELEGRAM_AGENT_RESTART_STATUS_PATH",
    "data/telegram_agent_restart.json"
)

TELEGRAM_AGENT_POLL_TIMEOUT_SECONDS = _env_int(
    "TELEGRAM_AGENT_POLL_TIMEOUT_SECONDS",
    20
)

TELEGRAM_AGENT_POLL_INTERVAL_SECONDS = _env_float(
    "TELEGRAM_AGENT_POLL_INTERVAL_SECONDS",
    1.0
)

TELEGRAM_AGENT_MAX_REPORT_LINES = _env_int(
    "TELEGRAM_AGENT_MAX_REPORT_LINES",
    8
)

# GMGN data-only enrichment (sources/gmgn.py): smart-money holders analysis
# for candidate_events via the official gmgn-cli + API key. Exactly one GMGN
# skill is wrapped — never the trading/cooking skills.
GMGN_API_KEY = _env("GMGN_API_KEY")
GMGN_ENRICH_ENABLED = _env_bool(
    "GMGN_ENRICH_ENABLED",
    True
)

# OpenTwitter / 6551 (sources/opentwitter.py): CA-mention search. Inert until
# TWITTER_TOKEN is set (https://6551.io/mcp).
TWITTER_TOKEN = _env("TWITTER_TOKEN")
OPENTWITTER_ENRICH_ENABLED = _env_bool(
    "OPENTWITTER_ENRICH_ENABLED",
    True
)

# OKX OnchainOS Social Analysis API (sources/okx_vibe.py): token "vibe" hotness
# score (0-100, X/Twitter-derived). Inert until the AK trio is set in .env and
# OKX_VIBE_ENABLED=true. Key/secret/passphrase from the OKX dev portal
# (https://web3.okx.com/onchain-os/dev-portal).
OKX_API_KEY = _env("OKX_API_KEY")
OKX_API_SECRET = _env("OKX_API_SECRET")
OKX_API_PASSPHRASE = _env("OKX_API_PASSPHRASE")
OKX_VIBE_ENABLED = _env_bool("OKX_VIBE_ENABLED", False)
# Per-token smart-money/KOL/whale buy-flow confirmation on alerts
# (sources/okx_signal.py, OKX DEX Signal API). Same AK creds as vibe.
OKX_SIGNAL_ENABLED = _env_bool("OKX_SIGNAL_ENABLED", False)

# GMGN TRADING provider (2026-06-12, replaces Definitive Flash per operator):
# swaps execute through gmgn-cli `swap` against the wallet bound to the API
# key (same self-custody funder 6DaR…Vafa). Gated like Flash was: master
# LIVE_EXECUTION_ENABLED + ENABLED + CONFIRM_LIVE, honoring
# LIVE_EXECUTION_DRY_RUN. Sizing/exposure reuse the DEFINITIVE_* caps.
GMGN_TRADING_ENABLED = _env_bool(
    "GMGN_TRADING_ENABLED",
    False
)
GMGN_TRADING_CONFIRM_LIVE = _env_bool(
    "GMGN_TRADING_CONFIRM_LIVE",
    False
)
GMGN_TRADING_WALLET = _env("GMGN_TRADING_WALLET") or DEFINITIVE_FLASH_FUNDER_ADDRESS
GMGN_TRADING_SLIPPAGE_PCT = _env_float(
    "GMGN_TRADING_SLIPPAGE_PCT",
    30
)
GMGN_TRADING_ANTI_MEV = _env_bool(
    "GMGN_TRADING_ANTI_MEV",
    True
)
GMGN_TRADING_PRIORITY_FEE_SOL = _env_float(
    "GMGN_TRADING_PRIORITY_FEE_SOL",
    0.0001
)
GMGN_TRADING_TIP_FEE_SOL = _env_float(
    "GMGN_TRADING_TIP_FEE_SOL",
    0.00001
)

# Resting on-chain TP/SL attached at buy time via --condition-orders
# (semantics per the GMGN skills schema: loss_stop price_scale = DROP percent
# that triggers, e.g. 30 -> -30%; profit_stop price_scale = GAIN percent,
# e.g. 100 -> 2x; trailing variant arms at the gain then sells on a
# drawdown_rate% pullback from peak). GMGN signed order-management auth expects
# its own key format; do not reuse the Definitive Flash Solana base58 key.
GMGN_TRADING_CONDITION_ORDERS_ENABLED = _env_bool(
    "GMGN_TRADING_CONDITION_ORDERS_ENABLED",
    True
)
GMGN_TRADING_STOP_LOSS_PCT = _env_float(
    "GMGN_TRADING_STOP_LOSS_PCT",
    0.30
)
GMGN_TRADING_TAKE_PROFIT_MULTIPLE = _env_float(
    "GMGN_TRADING_TAKE_PROFIT_MULTIPLE",
    2.0
)
GMGN_TRADING_TAKE_PROFIT_SELL_RATIO_PCT = _env_float(
    "GMGN_TRADING_TAKE_PROFIT_SELL_RATIO_PCT",
    50
)
GMGN_TRADING_TP_TRAILING_DRAWDOWN_PCT = _env_float(
    "GMGN_TRADING_TP_TRAILING_DRAWDOWN_PCT",
    0
)
GMGN_PRIVATE_KEY = _env("GMGN_PRIVATE_KEY")

# Per-skill trigger scope: "eligible" = every fresh eligible candidate row;
# "alerted" = only candidates that actually alerted (ignition delivery or
# lattice ENTRY SIGNAL). Twitter defaults to alerted-only because 6551
# points are paid; GMGN stays eligible so the control arm keeps its features.
GMGN_ENRICH_SCOPE = _env("GMGN_ENRICH_SCOPE", "eligible").strip().lower()
OPENTWITTER_ENRICH_SCOPE = _env(
    "OPENTWITTER_ENRICH_SCOPE", "alerted"
).strip().lower()

# GMGN referral code prefixed to gmgn.ai token links in alerts
# (https://gmgn.ai/sol/token/<code>_<MINT>). Read lazily from the env by
# utils.tg_format.gmgn_url; defined here only for documentation/discoverability.
GMGN_REFERRAL_CODE = _env("GMGN_REFERRAL_CODE", "Venerable")

DEFINITIVE_APP_BASE_URL = _env(
    "DEFINITIVE_APP_BASE_URL",
    "https://app.definitive.fi"
)

DEFINITIVE_REFERRAL_CODE = _env(
    "DEFINITIVE_REFERRAL_CODE",
    "VENERABLE"
)

# Telegram bots generally do not trigger other bots. When enabled, the
# scanner can send the TokenScan command from an authenticated Telegram
# user session so @tokenscan sees it as a normal user message.
TOKENSCAN_USER_TRIGGER_ENABLED = False
TOKENSCAN_USER_API_ID = _env_int("TOKENSCAN_USER_API_ID")
TOKENSCAN_USER_API_HASH = _env("TOKENSCAN_USER_API_HASH")
TOKENSCAN_USER_SESSION_FILE = "tokenscan_user"
TOKENSCAN_USER_SESSION_STRING = _env("TOKENSCAN_USER_SESSION_STRING")
TOKENSCAN_USER_CHAT_ID = TELEGRAM_CHAT_ID
TOKENSCAN_COMMAND = "/s@tokenscan"
