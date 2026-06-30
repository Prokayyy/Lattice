import asyncio
import sys

sys.path.insert(0, '.')
from config import DEFINITIVE_FLASH_FUNDER_ADDRESS
from trading.execution import LiveExecutionManager


async def main():
    m = LiveExecutionManager()
    sol = await m.solana_sol_balance(DEFINITIVE_FLASH_FUNDER_ADDRESS)
    print(f'funder {DEFINITIVE_FLASH_FUNDER_ADDRESS[:6]}...{DEFINITIVE_FLASH_FUNDER_ADDRESS[-4:]}: {sol} SOL')

asyncio.run(main())
