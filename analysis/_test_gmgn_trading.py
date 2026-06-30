"""GMGN trading provider verification — NO swaps are executed.

Covers: provider routing, arming gates, swap-arg construction for buy/sell,
sell-percent mapping, condition-order TP/SL shape, and optional live read-only
auth checks.
"""
import asyncio
import os
import sys

sys.path.insert(0, '.')

import config
from trading.execution import GmgnTradingClient, LiveExecutionManager

BONK = 'DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263'
fails = []

m = LiveExecutionManager()
print('gmgn configured:', m.gmgn.configured())
print('gmgn_trading_enabled:', m.gmgn_trading_enabled())
print('gmgn_live_submit_enabled:', m.gmgn_live_submit_enabled())
print('preferred_live_provider:', m.preferred_live_provider())
print('flash_ordering_enabled:', m.flash_ordering_enabled())

if m.preferred_live_provider() != 'gmgn':
    fails.append('provider routing not gmgn')
if not m.gmgn_live_submit_enabled():
    fails.append('gmgn submit not armed')

# swap-arg construction (pure, nothing executed)
buy_args = m.gmgn.build_swap_args(
    input_token=GmgnTradingClient.SOL_TOKEN, output_token=BONK,
    amount=31_250_000,
)
sell_args = m.gmgn.build_swap_args(
    input_token=BONK, output_token=GmgnTradingClient.SOL_TOKEN,
    percent=50,
)
print('\nbuy args: ', ' '.join(buy_args))
print('sell args:', ' '.join(sell_args))
for check, args in (
    (['--from', config.GMGN_TRADING_WALLET], buy_args),
    (['--amount', '31250000'], buy_args),
    (['--anti-mev'], buy_args),
    (['--percent', '50'], sell_args),
    (['--slippage', '30'], sell_args),
):
    joined = ' '.join(args)
    if ' '.join(check) not in joined and check[0] not in joined:
        fails.append(f'missing {check} in args')

# condition-orders construction (resting TP/SL)
conditions = m.gmgn.entry_condition_orders()
print('\ncondition orders:', conditions)
if not conditions or len(conditions) != 2:
    fails.append(f'expected 2 condition orders, got {conditions}')
else:
    sl = next((c for c in conditions if c['order_type'] == 'loss_stop'), None)
    tp = next((c for c in conditions if 'profit' in c['order_type']), None)
    if not sl or sl['price_scale'] != '30' or sl['sell_ratio'] != '100':
        fails.append(f'stop-loss wrong: {sl}')
    if not tp or tp['price_scale'] != '100' or tp['sell_ratio'] != '50':
        fails.append(f'take-profit wrong: {tp}')

buy_with_cond = m.gmgn.build_swap_args(
    input_token=m.gmgn.SOL_TOKEN, output_token=BONK, amount=1,
    condition_orders=conditions,
)
joined = ' '.join(buy_with_cond)
if '--condition-orders' not in joined or 'loss_stop' not in joined:
    fails.append('condition orders missing from swap args')
if '--sell-ratio-type buy_amount' not in joined:
    fails.append('sell-ratio-type missing')
if '--tip-fee 0.00001' not in joined:
    fails.append('condition-order tip-fee missing')

sample_response = {
    'data': {
        'hash': 'swap_hash',
        'strategy_order_id': 'strategy_id',
    }
}
if m.gmgn.response_value(sample_response, 'hash') != 'swap_hash':
    fails.append('hash extraction failed')
if (
    m.gmgn.response_value(
        sample_response,
        'strategy_order_id',
        'strategyOrderId',
        'strategy_id'
    )
    != 'strategy_id'
):
    fails.append('strategy_order_id extraction failed')

# canonical wSOL mint (the ...111 pseudo-address must be gone)
if not m.gmgn.SOL_TOKEN.endswith('112'):
    fails.append(f'SOL mint not canonical: {m.gmgn.SOL_TOKEN}')


class FlatAfterInsufficientGmgn:
    SOL_TOKEN = GmgnTradingClient.SOL_TOKEN

    def __init__(self):
        self.cancelled_first = False
        self.sell_percent = None

    def configured(self):
        return True

    async def cancel_open_strategies_for_token(self, token):
        self.cancelled_first = self.sell_percent is None
        return {'ok': True, 'found': 1, 'cancelled': 1}

    async def swap_sell_percent(self, token, percent):
        self.sell_percent = percent
        return {
            'ok': False,
            'error': (
                '[gmgn-cli] POST /v1/trade/swap failed: '
                'message=InsufficientBalanceErr'
            )
        }

    async def token_balance(self, token):
        return 0.0

    insufficient_balance_error = staticmethod(
        GmgnTradingClient.insufficient_balance_error
    )


flat_mgr = LiveExecutionManager()
flat_mgr.gmgn = FlatAfterInsufficientGmgn()
flat_result = asyncio.run(flat_mgr.execute_position_event(
    {
        'type': 'close',
        'chain': 'solana',
        'address': BONK,
        'live_execution_sell_tokens': 100,
        'live_execution_remaining_tokens_estimated': 100,
    },
    has_live_position=True,
))
print('flat-after-insufficient result:', flat_result.get('reason'))
if not flat_mgr.gmgn.cancelled_first:
    fails.append('full-close strategy cleanup did not run before sell')
if not flat_result.get('submitted'):
    fails.append('flat-after-insufficient close not treated as submitted')
if 'gmgn_sell_already_flat_after_insufficient_balance' not in flat_result.get('reason', ''):
    fails.append(f'flat-after-insufficient reason wrong: {flat_result}')

if os.getenv('GMGN_TEST_SKIP_LIVE', '').lower() not in {'1', 'true', 'yes'}:
    # live read-only call through the trading client
    balance = asyncio.run(m.gmgn.token_balance(BONK))
    print('live token_balance(BONK):', balance)
    if balance is None:
        fails.append('token_balance returned None (auth/parsing problem)')

    # live signed-auth read: open strategy orders (proves private-key auth works)
    if config.GMGN_PRIVATE_KEY:
        listing = asyncio.run(m.gmgn.strategy_list_open())
        print('strategy_list_open ok:', listing.get('ok'),
              '| error:', listing.get('error', ''))
        if not listing.get('ok'):
            fails.append(f'strategy list failed: {listing.get("error", "")}')
    else:
        print('strategy_list_open skipped: GMGN_PRIVATE_KEY not configured')
else:
    print('live GMGN reads skipped by GMGN_TEST_SKIP_LIVE')

# routing result for a dry skipped event (no live position -> sell skips)
result = asyncio.run(m.execute_position_event(
    {'type': 'close', 'chain': 'solana', 'address': BONK},
    has_live_position=False,
))
print('\nsell-without-position result:', result.get('provider'), result.get('reason'))
if result.get('provider') != 'gmgn' or result.get('reason') != 'no_live_entry_for_position':
    fails.append(f'routing/skip wrong: {result.get("provider")} {result.get("reason")}')

print('\nFAILURES:' if fails else '\nGMGN TRADING TESTS PASS')
for f in fails:
    print(' -', f)
sys.exit(1 if fails else 0)
