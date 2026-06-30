import argparse
import asyncio

from sources.dexscreener import DexScreenerClient
from sources.token_lineage import build_ticker_lineage_section


async def run(ticker, focus_address=None):

    client = DexScreenerClient()
    await client.start()

    try:
        text = await build_ticker_lineage_section(
            client,
            ticker,
            focus_address=focus_address
        )
        print(text)
    finally:
        await client.close()


def main():

    parser = argparse.ArgumentParser(
        description=(
            "Find same-ticker Solana tokens and rank them "
            "oldest to newest by mint transaction time."
        )
    )
    parser.add_argument(
        "ticker",
        help="Ticker symbol to search, e.g. AURAPEPE"
    )
    parser.add_argument(
        "--focus",
        default=None,
        help="Optional contract address to mark as the ignition token"
    )
    args = parser.parse_args()

    asyncio.run(
        run(
            args.ticker,
            args.focus
        )
    )


if __name__ == "__main__":
    main()
