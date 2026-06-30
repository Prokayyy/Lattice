from dataclasses import dataclass


@dataclass
class TokenMetrics:

    address: str
    symbol: str

    pair_address: str

    liquidity: float
    fdv: float

    price: float

    volume_5m: float

    volume_1h: float

    buys_5m: int
    sells_5m: int

    buys_1h: int
    sells_1h: int

    price_change_5m: float
    price_change_1h: float
    price_change_6h: float

    age_hours: float

    price_change_24h: float = 0

    buy_volume_5m: float = 0
    sell_volume_5m: float = 0
    buy_volume_1h: float = 0
    sell_volume_1h: float = 0
    buy_sell_volume_source_5m: str = ""
    buy_sell_volume_source_1h: str = ""
    raw_base_reserve: float = 0
    raw_quote_reserve: float = 0

    age_source: str = "mint_tx"

    chain: str = "solana"

    source: str = "unknown"

    lifecycle: str = "unknown"

    raw_liquidity: float = 0

    liquidity_source: str = "dexscreener"

    migration_fdv: float = 0

    migration_distance_usd: float = 0

    migration_distance_pct: float = 0

    migration_fdv_source: str = ""

    name: str = ""
