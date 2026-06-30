"""Render the redesigned Telegram messages with the screenshot's real data.

Prints raw HTML and a plain-text approximation. Run with env/bin/python.
"""
import asyncio
import re
import sys
import time
import types

sys.path.insert(0, '.')

from discovery.notify import LatticeNotifier
from utils.tg_format import fmt_token_price


def plain(text):
    return re.sub(r'<[^>]+>', '', text)


print('=== price formatter spot checks ===')
for p in (1.331e-05, 1.398e-05, 9.317e-06, 6.481e-06, 5.838e-06, 0.0421, 1.23, 0):
    print(f'  {p!r:<12} -> {fmt_token_price(p)}')

n = LatticeNotifier(dry_run=True)

alert = types.SimpleNamespace(
    symbol='MERRY CAT',
    conviction=0.22,
    entry_zone=(1.331e-05, 1.398e-05),
    invalidation_price=9.317e-06,
    revival_score=0.70,
    lattice_composite=0.76,
    evidence={'breadth': 0.21, 'buyers_sig': -0.02, 'concentration': 0.28},
    narrative_context={},
    token_address='q8gxXxDi4NK6W4NAJkNjwn52DBUgFkK9MazVDMHpump',
)
INTEL = {
    'smart_count': 3, 'smart_share_pct': 4.2, 'smart_usd': 1234.5,
    'smart_profit_n': 2, 'tw_mentions': 5, 'tw_authors': 3,
    'tw_top_followers': 12400,
}
print('\n=== ENTRY SIGNAL (with intel) ===')
print(plain(n.fmt_signal(
    alert,
    entry_status='not entered; insufficient paper cash 15.47<20.00',
    intel=INTEL,
)))
print('\n=== ENTRY SIGNAL (no intel — link only) ===')
print(plain(n.fmt_signal(alert, entry_status='entered legacy fixed-size')))

now = time.time()
pos_sell = {
    'symbol': 'OIIAOIIA', 'reason': 'max_hold', 'peak_mult': 1.55,
    'pnl_usd': -3.35, 'cost_usd': 20.0, 'proceeds': 16.65,
    'entry_ts': now - 25.4 * 3600, 'exit_ts': now,
}
print('\n=== PAPER SELL ===')
print(plain(n.fmt_paper_exit(pos_sell, 32.12)))

pos_buy = {
    'symbol': 'MERRY CAT', 'cost_usd': 20.0, 'entry_price': 1.36e-05,
    'conviction': 0.22, 'entry_fdv_usd': 13600.0,
}
print('\n=== PAPER BUY ===')
print(plain(n.fmt_paper_entry(pos_buy, 12.12)))

pos_scale = {
    'symbol': 'NEPE', 'entry_price': 1.0e-05, 'cost_usd': 20.0,
    'remaining': 700000.0, 'peak': 2.1e-05, 'conviction': 0.31,
}
print('\n=== SCALE-OUT ===')
print(plain(n.fmt_paper_scale_out(pos_scale, 'scale_2x', 650000, 2.0e-05, 45.10, sold_cum=0.65)))

# ---- positions list (real method, fake self) ----
from agents.telegram_agent import TelegramCommandAgent

state = {
    'cash': 15.47, 'realized': -12.30, 'sol_usd': 82.7,
    'last_seen': now - 12,
    'open_pos': {
        'tokElon': {
            'symbol': 'Elondoge', 'token': '4op6yJqGawX1d8e1kWyTAbCdEfGhdKpump',
            'entry_price': 6.481e-06, 'last_price': 5.838e-06,
            'remaining': 18.02 / 5.838e-06, 'proceeds': 0.0, 'cost_usd': 20.0,
            'conviction': 0.23, 'entry_ts': now - 9 * 3600,
        },
        'tokOiia': {
            'symbol': 'OIIAOIIA', 'token': '3f65KQabcdefghijklmnopqrstuvgQpump',
            'entry_price': 7.278e-05, 'last_price': 6.06e-05,
            'remaining': 16.65 / 6.06e-05, 'proceeds': 0.0, 'cost_usd': 20.0,
            'conviction': 0.25, 'entry_ts': now - 26 * 3600,
        },
        'tokWin': {
            'symbol': 'NEPE', 'token': 'Hziw01abcdefghijklmnopqrstuvSipump',
            'entry_price': 1.0e-05, 'last_price': 1.62e-05,
            'remaining': 1_300_000.0, 'proceeds': 6.5, 'cost_usd': 20.0,
            'conviction': 0.31, 'entry_ts': now - 3 * 3600,
            'live_execution_entry_submitted': True,
            'live_execution_entry_filled_target_amount': 1_300_000.0,
        },
        'tokFresh': {  # <1h-old position: regression for the "<1h" HTML bug
            'symbol': 'FRESH', 'token': 'Fr3sh0abcdefghijklmnopqrstuvWXpump',
            'entry_price': 2.0e-06, 'last_price': 2.1e-06,
            'remaining': 10_000_000.0, 'proceeds': 0.0, 'cost_usd': 20.0,
            'conviction': 0.27, 'entry_ts': now - 600,
        },
    },
}


def assert_valid_telegram_html(text, label):
    """Telegram rejects the entire message on any stray '<' that does not
    open an allowed tag — exactly how the '<1h' bug killed /positions."""
    stripped = re.sub(r'</?(b|i|u|s|a|code|pre)(\s[^>]*)?>', '', text)
    if '<' in stripped:
        at = stripped.index('<')
        raise SystemExit(
            f'INVALID HTML in {label!r} near: {stripped[max(0, at-30):at+20]!r}'
        )


class FakeExec:
    async def solana_sol_balance(self, addr):
        return 0.058


fake = types.SimpleNamespace(
    load_live_runner_state=lambda: state,
    live_position_last_price=lambda p: float(p.get('last_price') or 0),
    live_execution=FakeExec(),
)

msgs = asyncio.run(TelegramCommandAgent.live_positions_messages(fake))
print('\n=== /POSITIONS (LATTICE) ===')
for m in msgs:
    assert_valid_telegram_html(m, '/positions')
    print(plain(m))

# split math check on the scaled-out NEPE position:
# cost $20 @ 1e-05 (2M tokens), 1.3M remaining, $6.50 banked, last 1.62e-05
# -> uPnL = 21.06 - 13.00 = +8.06 ; realized = 6.50 - 7.00 = -0.50 ; total +7.56
joined = '\n'.join(msgs)
assert 'uPnL +$8.06' in joined and 'realized -$0.50' in joined, \
    'uPnL/realized split math mismatch in /positions render'
print('uPnL/realized split math: OK')
for label, text in [
    ('signal', n.fmt_signal(alert, entry_status='not entered; cash 15.47<20.00', intel=INTEL)),
    ('signal-bare', n.fmt_signal(alert)),
    ('sell', n.fmt_paper_exit(pos_sell, 32.12)),
    ('buy', n.fmt_paper_entry(pos_buy, 12.12)),
]:
    assert_valid_telegram_html(text, label)
print('\nHTML validity: all messages OK')
