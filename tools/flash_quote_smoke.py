"""Flash API quote-only smoke test (READ-ONLY — places no order, moves no funds).

Validates that the live Definitive Flash API accepts our request shapes —
especially the `triggers` array fix for stop-loss orders — by calling
`POST /quote` and printing the outgoing body + the API response. It NEVER calls
`POST /order`, so it cannot submit, sign-and-send, or touch the wallet.

Run with the project venv (has aiohttp / base58 / cryptography):

    env/bin/python -m tools.flash_quote_smoke
    env/bin/python -m tools.flash_quote_smoke --mint <SOL_MINT> --qty 1000000 --trigger-usd 0.00001
    env/bin/python -m tools.flash_quote_smoke --no-market   # only the stop quote
    env/bin/python -m tools.flash_quote_smoke --no-stop     # only a market sell quote

Requires DEFINITIVE_FLASH_API_KEY (a Flash-type key) in .env. funderAddress is
optional for a quote; it is included if DEFINITIVE_FLASH_FUNDER_ADDRESS is set.
"""

import argparse
import asyncio
import json

from config import (
    DEFINITIVE_FLASH_API_BASE_URL,
    DEFINITIVE_FLASH_API_KEY,
    DEFINITIVE_FLASH_FUNDER_ADDRESS,
    DEFINITIVE_FLASH_ONCHAIN_STOP_ORDER_TYPE,
    DEFINITIVE_SOLANA_CONTRA_ASSET,
)
from trading.execution import LiveExecutionManager


# A deep-liquidity Solana mint used as a default so the quote actually routes.
# (BONK — change with --mint for your own token.)
DEFAULT_MINT = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"


def _dump(label, obj):
    print(f"\n----- {label} -----")
    print(json.dumps(obj, indent=2, default=str))


async def _run_quote(manager, label, body):
    _dump(f"{label}: REQUEST BODY (POST /quote)", body)
    result = await manager.flash.quote(body)
    ok = result.get("ok")
    print(f"\n{label}: HTTP {result.get('status')}  ok={ok}")
    if ok:
        raw = result.get("raw_response", {}) or {}
        # Surface the fields we actually consume downstream.
        svm = raw.get("svm") if isinstance(raw.get("svm"), dict) else None
        print(f"  quoteId        : {raw.get('quoteId')}")
        print(f"  orderType echo : {raw.get('orderType')}")
        if svm is not None:
            print(f"  svm.orderMessage present : {bool(svm.get('orderMessage'))}")
            print(f"  svm.nonce / deadline     : {svm.get('nonce')} / {svm.get('deadline')}")
            print(f"  svm.sponsoredDelegateTx  : {bool(svm.get('sponsoredDelegateTx'))}")
        else:
            print("  (no svm payload on this quote)")
    _dump(f"{label}: RAW RESPONSE", result.get("raw_response"))
    return result


async def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mint", default=DEFAULT_MINT, help="Solana token mint to sell")
    ap.add_argument("--qty", default="1000000", help="token units to sell (decimal string)")
    ap.add_argument("--trigger-usd", default="0.0000001",
                    help="USD trigger price for the stop (kept far below market)")
    ap.add_argument("--order-type", default=DEFINITIVE_FLASH_ONCHAIN_STOP_ORDER_TYPE,
                    help="orderType for the stop quote (default from config)")
    ap.add_argument("--no-stop", action="store_true", help="skip the stop-loss quote")
    ap.add_argument("--no-market", action="store_true", help="skip the market-sell quote")
    args = ap.parse_args()

    print("Definitive Flash quote smoke test — READ-ONLY, no order is submitted.")
    print(f"  base url   : {DEFINITIVE_FLASH_API_BASE_URL}")
    print(f"  api key set: {bool(DEFINITIVE_FLASH_API_KEY)}")
    print(f"  funder set : {bool(DEFINITIVE_FLASH_FUNDER_ADDRESS)}")
    print(f"  contra     : {DEFINITIVE_SOLANA_CONTRA_ASSET}")
    print(f"  mint       : {args.mint}")

    if not DEFINITIVE_FLASH_API_KEY:
        print("\nERROR: DEFINITIVE_FLASH_API_KEY is not set in .env — cannot quote.")
        return

    manager = LiveExecutionManager()

    qty = float(args.qty)
    trigger_usd = float(args.trigger_usd)
    event = {"chain": "solana", "address": args.mint}

    # 1) Market-sell quote: confirms auth + routing independent of triggers.
    if not args.no_market:
        market_body = manager.flash_stop_quote_body(event, qty=qty, trigger_usd=trigger_usd)
        market_body["orderType"] = "market"
        market_body.pop("triggers", None)
        await _run_quote(manager, "MARKET SELL", market_body)

    # 2) Stop-loss quote: confirms the `triggers` array shape is accepted.
    if not args.no_stop:
        stop_body = manager.flash_stop_quote_body(event, qty=qty, trigger_usd=trigger_usd)
        stop_body["orderType"] = args.order_type
        await _run_quote(manager, "STOP-LOSS SELL", stop_body)

    print("\nDone. No order was submitted (POST /order was never called).")


if __name__ == "__main__":
    asyncio.run(main())
