import sys

sys.path.insert(0, '.')
import config
from trading.execution import LiveExecutionManager

m = LiveExecutionManager()
print('flash client configured (creds+funder+key):', m.flash.configured())
print('preferred_live_provider:', m.preferred_live_provider())
print('flash_ordering_enabled:', m.flash_ordering_enabled())
print('flash_live_submit_enabled:', m.flash_live_submit_enabled())
print('LIVE_EXECUTION_ENABLED:', config.LIVE_EXECUTION_ENABLED)
print('entry notional min/max:', config.DEFINITIVE_MIN_ENTRY_NOTIONAL_USD,
      config.DEFINITIVE_MAX_ENTRY_NOTIONAL_USD)
print('exposure cap:', config.DEFINITIVE_MAX_ACCOUNT_EXPOSURE_USD)
print('flash slip/impact:', config.DEFINITIVE_FLASH_MAX_SLIPPAGE,
      config.DEFINITIVE_FLASH_MAX_PRICE_IMPACT)
print('confirm/attempts/delay:', config.DEFINITIVE_FLASH_CONFIRM_FILL_SECONDS,
      config.DEFINITIVE_FLASH_SUBMIT_MAX_ATTEMPTS,
      config.DEFINITIVE_FLASH_SUBMIT_RETRY_DELAY_SECONDS)
