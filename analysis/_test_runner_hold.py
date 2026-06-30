"""Behavioral test of the runner-hold exit leg (temp state file, no prod data).

Scenarios:
  1. soft exit (strict_early) -> partial sell, hold leg survives -> 2.4x release
  2. hold leg breaches 0.70x floor -> runner_hold_floor_exit (confirmed ticks)
  3. hold leg times out after POSITION_RUNNER_HOLD_MAX_HOURS
  4. hard_stop_loss is NOT intercepted -> full close
Run with env/bin/python.
"""
import os
import sys
import tempfile
import types

sys.path.insert(0, '.')
os.environ['POSITION_RUNNER_HOLD_ENABLED'] = 'true'

import config
from trading.position_engine import PositionEngine

assert config.POSITION_RUNNER_HOLD_ENABLED


def metrics(price, addr='TOKADDR', sells_vol=1800.0):
    m = types.SimpleNamespace()
    m.address = addr
    m.chain = 'solana'
    m.pair_address = 'PAIRADDR'
    m.symbol = 'TEST'
    m.name = 'Test'
    m.price = price
    m.liquidity = 10000.0
    m.fdv = 20000.0
    m.migration_fdv = 0.0
    m.migration_distance_usd = 0.0
    m.migration_distance_pct = 0.0
    m.buys_5m = 50
    m.sells_5m = 40
    m.buys_1h = 300
    m.sells_1h = 240
    m.volume_5m = 4000.0
    m.volume_1h = 30000.0
    m.buy_volume_5m = 2000.0
    m.sell_volume_5m = sells_vol
    m.buy_volume_1h = 12000.0
    m.sell_volume_1h = 11000.0
    m.txns_5m = 90
    m.txns_1h = 540
    return m


def fresh_position(now, addr='TOKADDR'):
    return {
        'address': addr, 'chain': 'solana', 'symbol': 'TEST', 'name': 'Test',
        'pair_address': 'PAIRADDR', 'status': 'open', 'entry_at': now,
        'reentry': False, 'entry_count_before': 0,
        'entry_size_sol': 0.25, 'entry_sol_usd': 80.0, 'entry_price': 1.0,
        'entry_notional_usd': 20.0, 'entry_size_tokens': 20.0,
        'remaining_tokens': 20.0, 'realized_usd': 0, 'scaled_out_pct': 0,
        'take_profit_filled': False, 'entry_pressure': 60.0,
        'peak_pressure': 60.0, 'last_pressure': 60.0, 'entry_score': 80,
        'entry_impulse': 1.1, 'peak_price': 1.0, 'peak_multiple': 1,
        'last_price': 1.0, 'entry_liquidity': 10000.0, 'entry_fdv': 20000.0,
        'entry_migration_fdv': 0.0, 'entry_migration_distance_usd': 99999.0,
        'entry_migration_distance_pct': 1.0, 'entry_volume_1h': 30000.0,
        'entry_volume_multiple': 5.0, 'entry_quality_tier': 'high_volume',
        'entry_volume_liquidity_ratio': 1.0, 'entry_buy_sell_ratio': 1.5,
        'entry_buy_sell_volume_ratio': 1.5, 'events': [],
        'trailing_stop_price': 0.70, 'trailing_stop_mode': 'standard',
        'missing_pair_count': 0,
    }


def make_engine(tag):
    path = os.path.join(tempfile.mkdtemp(), f'pos_{tag}.json')
    eng = PositionEngine(state_file=path)
    return eng


def run_scan(eng, pos, price, pressure, now, weak=False):
    ign = {
        'volume_liquidity_ratio': 0.3 if weak else 1.2,
        'buy_sell_ratio': 0.5 if weak else 1.5,
        'flow_buy_sell_ratio': 0.5 if weak else 1.5,
        'price_jump': 1.0,
    }
    return eng.manage_position(pos, metrics(price), ign, pressure, now)


T0 = 1_800_000_000.0
fails = []

# --- scenario 1: partial then release ---
eng = make_engine('s1')
state = eng.load_state()
pos = fresh_position(T0)
state['open'][pos['address']] = pos
eng.save_state()
# two weak scans at 0.93 -> strict early fires on 2nd
ev1 = run_scan(eng, pos, 0.93, 30.0, T0 + 60, weak=True)
ev2 = run_scan(eng, pos, 0.93, 30.0, T0 + 120, weak=True)
got_scale = any(e and e.get('type') == 'scale_out' and 'runner_hold_scale' in e.get('reason', '')
                for e in (ev1 + ev2))
if not got_scale:
    fails.append(f's1: expected runner_hold_scale event, got {[e.get("reason") for e in ev1+ev2 if e]}')
if not pos.get('runner_hold_active'):
    fails.append('s1: runner_hold_active not set')
if abs(pos['remaining_tokens'] - 10.0) > 1e-6:
    fails.append(f's1: remaining_tokens {pos["remaining_tokens"]} != 10.0')
if pos['status'] != 'open':
    fails.append('s1: position closed but should be holding')
# weak scans while holding must NOT close
ev3 = run_scan(eng, pos, 0.90, 20.0, T0 + 300, weak=True)
if pos['status'] != 'open' or ev3:
    fails.append(f's1: hold leg closed by soft exit: {[e.get("reason") for e in ev3 if e]}')
# price runs to 2.4 -> release
ev4 = run_scan(eng, pos, 2.4, 70.0, T0 + 600)
if not pos.get('runner_hold_released'):
    fails.append('s1: not released at 2.4x')
if pos.get('runner_hold_active'):
    fails.append('s1: still active after release')
if pos['status'] != 'open':
    fails.append(f's1: closed at release scan ({pos.get("close_reason")})')
# crash after release -> normal trailing closes it eventually (2-tick confirm)
ev5 = run_scan(eng, pos, 1.55, 50.0, T0 + 660)
ev6 = run_scan(eng, pos, 1.55, 50.0, T0 + 720)
if pos['status'] != 'closed':
    fails.append('s1: trailing did not manage after release (still open at 1.55 after peak 2.4)')
else:
    print(f"s1 close: {pos['close_reason']} pnl=${pos['pnl_usd']:.2f} (realized {pos['realized_usd']:.2f})")

# --- scenario 2: floor breach ---
eng = make_engine('s2')
state = eng.load_state()
pos = fresh_position(T0, addr='TOK2')
state['open'][pos['address']] = pos
eng.save_state()
run_scan(eng, pos, 0.93, 30.0, T0 + 60, weak=True)
run_scan(eng, pos, 0.93, 30.0, T0 + 120, weak=True)
if not pos.get('runner_hold_active'):
    fails.append('s2: hold not active')
run_scan(eng, pos, 0.65, 20.0, T0 + 300, weak=True)
run_scan(eng, pos, 0.65, 20.0, T0 + 360, weak=True)
run_scan(eng, pos, 0.65, 20.0, T0 + 420, weak=True)
if pos['status'] != 'closed' or pos.get('close_reason') != 'runner_hold_floor_exit':
    fails.append(f"s2: expected runner_hold_floor_exit, got status={pos['status']} reason={pos.get('close_reason')}")
else:
    print(f"s2 close: {pos['close_reason']} pnl=${pos['pnl_usd']:.2f}")

# --- scenario 3: timeout ---
eng = make_engine('s3')
state = eng.load_state()
pos = fresh_position(T0, addr='TOK3')
state['open'][pos['address']] = pos
eng.save_state()
run_scan(eng, pos, 0.93, 30.0, T0 + 60, weak=True)
run_scan(eng, pos, 0.93, 30.0, T0 + 120, weak=True)
run_scan(eng, pos, 1.1, 50.0, T0 + 25 * 3600)
if pos['status'] != 'closed' or pos.get('close_reason') != 'runner_hold_timeout_exit':
    fails.append(f"s3: expected runner_hold_timeout_exit, got {pos.get('close_reason')}")
else:
    print(f"s3 close: {pos['close_reason']} pnl=${pos['pnl_usd']:.2f}")

# --- scenario 4: hard stop not intercepted ---
eng = make_engine('s4')
state = eng.load_state()
pos = fresh_position(T0, addr='TOK4')
state['open'][pos['address']] = pos
eng.save_state()
run_scan(eng, pos, 0.69, 30.0, T0 + 60)
run_scan(eng, pos, 0.69, 30.0, T0 + 120)
if pos['status'] != 'closed':
    fails.append(f"s4: hard stop did not close (status={pos['status']})")
elif 'hard_stop' not in pos.get('close_reason', '') and 'trailing' not in pos.get('close_reason', ''):
    fails.append(f"s4: unexpected close reason {pos.get('close_reason')}")
elif pos.get('runner_hold_active'):
    fails.append('s4: hold leg activated on hard exit')
else:
    print(f"s4 close: {pos['close_reason']} (full close, remaining={pos['remaining_tokens']})")

print('\nFAILURES:' if fails else '\nALL SCENARIOS PASS')
for f in fails:
    print(' -', f)
sys.exit(1 if fails else 0)
