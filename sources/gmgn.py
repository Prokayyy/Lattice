"""GMGN data-only client.

Scope (updated 2026-06-13): three GMGN data skills, all data-only — execution
stays on Definitive Flash:
  1. smart-money holders   `gmgn-cli token holders --tag smart_degen`
  2. accurate pool liquidity `gmgn-cli token pool` — DexScreener under/over-reports
     liquidity for pre-migration (bonding-curve) tokens; GMGN reads real pool
     reserves. Use for sizing / liquidity-aware decisions.
  3. OHLCV candles         `gmgn-cli market kline` — real candles for entry
     timing (blow-off / fade detection), better than DexScreener snapshot
     price_change_* fields.
No trading/cooking endpoints.

Called only for alert-ELIGIBLE candidates (the hook rides
record_candidate_event, which requires alert_eligible and dedups per
token/24h) and always as a background task — the scan loop never blocks.
"""

import asyncio
import json
import os
import shutil
import time
from pathlib import Path

from config import GMGN_API_KEY, GMGN_ENRICH_ENABLED

_CACHE_TTL_SECONDS = 900
_SUBPROCESS_TIMEOUT = 25


def _f(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class GmgnClient:

    def __init__(self):
        self._cache = {}
        self._semaphore = asyncio.Semaphore(2)
        self._cli = self._find_cli()

    @staticmethod
    def _find_cli():
        for candidate in (
            shutil.which("gmgn-cli"),
            str(Path.home() / ".npm-global" / "bin" / "gmgn-cli"),
        ):
            if candidate and Path(candidate).exists():
                return candidate
        return ""

    def enabled(self):
        return bool(
            GMGN_ENRICH_ENABLED
            and GMGN_API_KEY
            and self._cli
        )

    async def _run(self, *args):
        env = dict(os.environ, GMGN_API_KEY=GMGN_API_KEY)
        proc = await asyncio.create_subprocess_exec(
            self._cli,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=_SUBPROCESS_TIMEOUT,
            )
        except asyncio.TimeoutError:
            proc.kill()
            return None
        text = stdout.decode(errors="replace").strip()
        start = text.find("{")
        if start < 0:
            return None
        try:
            return json.loads(text[start:])
        except json.JSONDecodeError:
            return None

    async def _cached(self, kind, address, args):
        key = (kind, address)
        hit = self._cache.get(key)
        now = time.monotonic()
        if hit and now - hit[0] < _CACHE_TTL_SECONDS:
            return hit[1]
        async with self._semaphore:
            hit = self._cache.get(key)
            if hit and time.monotonic() - hit[0] < _CACHE_TTL_SECONDS:
                return hit[1]
            data = await self._run(*args)
        self._cache[key] = (time.monotonic(), data)
        if len(self._cache) > 500:
            oldest = sorted(self._cache.items(), key=lambda kv: kv[1][0])
            for k, _ in oldest[:100]:
                self._cache.pop(k, None)
        return data

    async def smart_money_holders(self, address, chain="sol"):
        return await self._cached(
            "smart", address,
            (
                "token", "holders", "--chain", chain, "--address", address,
                "--tag", "smart_degen",
                "--order-by", "amount_percentage", "--direction", "desc",
            ),
        )

    async def candidate_features(self, address, chain="sol"):
        """Aggregate smart-money positioning for one candidate, or None when
        the call failed. Per-wallet payload (verified live 2026-06-12):
        amount_percentage (fraction), usd_value, profit, avg_cost, is_new,
        is_suspicious, start_holding_at, wallet_tag_v2."""

        data = await self.smart_money_holders(address, chain)
        if data is None:
            return None

        holders = data.get("list") or []
        share = sum((_f(h.get("amount_percentage")) or 0.0) for h in holders)
        features = {
            "smart_count": len(holders),
            "smart_share_pct": share * 100.0,
            "smart_usd": sum((_f(h.get("usd_value")) or 0.0) for h in holders),
            "smart_profit_n": sum(
                1 for h in holders if (_f(h.get("profit")) or 0.0) > 0
            ),
            "smart_fresh_n": sum(1 for h in holders if h.get("is_new")),
            "smart_suspicious_n": sum(
                1 for h in holders if h.get("is_suspicious")
            ),
        }
        features["raw"] = json.dumps({
            "n": len(holders),
            "top": [
                {
                    "addr": str(h.get("address", ""))[:12],
                    "share": h.get("amount_percentage"),
                    "usd": h.get("usd_value"),
                    "profit": h.get("profit"),
                    "tag": h.get("wallet_tag_v2"),
                    "new": h.get("is_new"),
                    "suspicious": h.get("is_suspicious"),
                    "since": h.get("start_holding_at"),
                }
                for h in holders[:5]
            ],
        }, default=str)
        return features

    # ------------------------------------------------------------------ #
    # Skill 2: accurate pool liquidity (token pool)                       #
    # ------------------------------------------------------------------ #
    async def token_pool(self, address, chain="sol"):
        return await self._cached(
            "pool", address,
            ("token", "pool", "--chain", chain, "--address", address, "--raw"),
        )

    async def pool_features(self, address, chain="sol"):
        """Real on-chain pool liquidity for one token, or None on failure.
        Authoritative for pre-migration / bonding-curve tokens where
        DexScreener liquidity is a formula estimate. Fields verified live
        2026-06-13: liquidity (USD), base_reserve, quote_reserve,
        quote_reserve_value (USD), exchange, pool_address."""
        data = await self.token_pool(address, chain)
        if not data:
            return None
        return {
            "gmgn_liquidity_usd": _f(data.get("liquidity")),
            "gmgn_quote_reserve_value_usd": _f(data.get("quote_reserve_value")),
            "gmgn_base_reserve": _f(data.get("base_reserve")),
            "gmgn_quote_reserve": _f(data.get("quote_reserve")),
            "gmgn_exchange": data.get("exchange"),
            "gmgn_pool_address": data.get("pool_address"),
        }

    # ------------------------------------------------------------------ #
    # token info — scan-time backfill for absent/zero DexScreener data    #
    # ------------------------------------------------------------------ #
    async def token_info(self, address, chain="sol"):
        return await self._cached(
            "info", address,
            ("token", "info", "--chain", chain, "--address", address, "--raw"),
        )

    async def token_info_features(self, address, chain="sol"):
        """Backfill fields for tokens whose DexScreener liquidity/volume/
        price_change are absent or zero. Verified live 2026-06-13: top-level
        liquidity / launchpad_status (0=none,1=live,2=migrated) /
        launchpad_progress / migration_market_cap; price.{price, price_5m,
        price_1h, volume_5m, volume_1h}. price_change is derived from the price
        snapshots (GMGN does not return % change directly)."""
        data = await self.token_info(address, chain)
        if not data:
            return None
        price = data.get("price") or {}
        p = _f(price.get("price"))
        p5 = _f(price.get("price_5m"))
        p1 = _f(price.get("price_1h"))
        total_supply = _f(data.get("total_supply"))
        circ_supply = _f(data.get("circulating_supply"))
        return {
            "gmgn_liquidity_usd": _f(data.get("liquidity")),
            # FDV = price x total supply; market cap = price x circulating.
            "gmgn_fdv_usd": (p * total_supply) if (p and total_supply) else None,
            "gmgn_market_cap_usd": (p * circ_supply) if (p and circ_supply) else None,
            "gmgn_volume_5m": _f(price.get("volume_5m")),
            "gmgn_volume_1h": _f(price.get("volume_1h")),
            "gmgn_price_change_5m": ((p / p5 - 1.0) * 100.0)
            if (p and p5) else None,
            "gmgn_price_change_1h": ((p / p1 - 1.0) * 100.0)
            if (p and p1) else None,
            "gmgn_launchpad_status": data.get("launchpad_status"),
            "gmgn_launchpad_progress": _f(data.get("launchpad_progress")),
            "gmgn_migration_market_cap": _f(data.get("migration_market_cap")),
        }

    # ------------------------------------------------------------------ #
    # all holders (untagged) — for bundle / cluster analysis              #
    # ------------------------------------------------------------------ #
    async def top_holders(self, address, chain="sol", limit=100):
        """Raw top-holder list (ALL wallets, by current supply share) for
        bundle/cluster analysis. Each item has: address, amount_percentage,
        start_holding_at (first acquire ts), buy_amount_cur (tokens bought),
        buy_volume_cur (USD), native_transfer (funding origin, often null),
        maker_token_tags (incl. 'bundler')."""
        data = await self._cached(
            ("holders_all", int(limit)), address,
            ("token", "holders", "--chain", chain, "--address", address,
             "--limit", str(int(limit)), "--order-by", "amount_percentage",
             "--direction", "desc", "--raw"),
        )
        return (data or {}).get("list") or []

    # ------------------------------------------------------------------ #
    # token security — honeypot / renounced / tax / concentration veto    #
    # ------------------------------------------------------------------ #
    async def token_security(self, address, chain="sol"):
        return await self._cached(
            "security", address,
            ("token", "security", "--chain", chain, "--address", address,
             "--raw"),
        )

    async def security_features(self, address, chain="sol"):
        """Token safety signals (skill `token security`). SOL-reliable fields
        verified live 2026-06-13: renounced_mint, renounced_freeze_account,
        top_10_holder_rate, buy_tax, sell_tax, can_sell/can_not_sell,
        is_honeypot (EVM), is_blacklist, burn_status. (rug_ratio / sniper /
        wash come back null on SOL — use token info `stat` for those.)"""
        data = await self.token_security(address, chain)
        if not data:
            return None

        def _b(v):
            return bool(v) if v is not None else None

        return {
            "sec_renounced_mint": _b(data.get("renounced_mint")),
            "sec_renounced_freeze": _b(data.get("renounced_freeze_account")),
            "sec_top_10_holder_rate": _f(data.get("top_10_holder_rate")),
            "sec_buy_tax": _f(data.get("buy_tax")),
            "sec_sell_tax": _f(data.get("sell_tax")),
            "sec_can_sell": _b(data.get("can_sell")),
            "sec_cannot_sell": _b(data.get("can_not_sell")),
            "sec_is_honeypot": _b(data.get("is_honeypot")),
            "sec_is_blacklist": _b(data.get("is_blacklist")),
            "sec_burn_status": data.get("burn_status"),
        }

    # ------------------------------------------------------------------ #
    # Skill 3: OHLCV candles (market kline) — entry timing               #
    # ------------------------------------------------------------------ #
    async def token_kline(self, address, resolution, frm, to, chain="sol"):
        # Not cached: entry timing needs fresh candles. Bounded by the
        # semaphore so concurrent calls stay polite to the rate limiter.
        async with self._semaphore:
            return await self._run(
                "market", "kline", "--chain", chain, "--address", address,
                "--resolution", resolution,
                "--from", str(int(frm)), "--to", str(int(to)), "--raw",
            )

    async def kline_features(self, address, chain="sol", resolution="5m",
                             lookback_s=3600):
        """Entry-timing features derived from real OHLCV candles, or None.
        Aimed at the fade-entry problem: detect blow-off tops and tokens
        already rolling over from a local high before we buy."""
        now = int(time.time())
        data = await self.token_kline(address, resolution, now - int(lookback_s),
                                      now, chain)
        candles = (data or {}).get("list") or []
        closes = [_f(c.get("close")) for c in candles]
        closes = [c for c in closes if c is not None]
        if not candles or not closes:
            return None
        first, last = closes[0], closes[-1]
        hi = max((_f(c.get("high")) or 0.0) for c in candles)
        greens = sum(
            1 for c in candles
            if (_f(c.get("close")) or 0.0) >= (_f(c.get("open")) or 0.0)
        )
        lastc = candles[-1]
        o = _f(lastc.get("open")) or 0.0
        cl = _f(lastc.get("close")) or 0.0
        h = _f(lastc.get("high")) or 0.0
        lo = _f(lastc.get("low")) or 0.0
        rng = (h - lo) or 1e-18
        return {
            "kl_n": len(candles),
            "kl_change_pct": (last / first - 1.0) * 100.0 if first else None,
            "kl_drawdown_from_high_pct": (last / hi - 1.0) * 100.0 if hi else None,
            "kl_green_ratio": greens / len(candles),
            # big upper wick on the latest candle = blow-off / rejection
            "kl_last_upper_wick_ratio": (h - max(o, cl)) / rng,
            "kl_last_green": cl >= o,
            "kl_volume_usd": sum((_f(c.get("volume")) or 0.0) for c in candles),
        }


gmgn_client = GmgnClient()
