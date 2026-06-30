"""Close ALL open lattice paper positions at their last marked price.

Run ONLY while lattice-runner-supervisor.service is stopped (the runner
holds state in memory and would overwrite). Appends proper trade records to
discovery/trades.jsonl, returns proceeds to paper cash, updates the rolling
24h realized window, and leaves balance_sol untouched so the runner's
startup top-up mechanism credits the configured +3 SOL on restart.
"""
import json
import os
import time

STATE = 'discovery/live_state.json'
LEDGER = 'discovery/trades.jsonl'

d = json.load(open(STATE))
open_pos = d.get('open_pos') or {}
print(f"open positions: {len(open_pos)} | cash ${d.get('cash', 0):.2f} "
      f"| balance_sol {d.get('balance_sol')}")

now = time.time()
records = []
warnings = []
proceeds_total = 0.0
pnl_total = 0.0

for token, pos in list(open_pos.items()):
    filled = float(pos.get('live_execution_entry_filled_target_amount') or 0)
    if (pos.get('live_execution_entry_submitted') and filled > 0
            and not pos.get('live_execution_closed')):
        warnings.append(f"{pos.get('symbol')} ({token}): live fill "
                        f"{filled:.0f} tok may remain ON-CHAIN — paper side "
                        "closed here, chain side needs manual reconcile")

    price = float(pos.get('last_price') or pos.get('entry_price') or 0)
    remaining = float(pos.get('remaining') or 0)
    add = remaining * price
    proceeds = float(pos.get('proceeds') or 0) + add
    cost = float(pos.get('cost_usd') or 0)
    pnl = proceeds - cost
    entry_price = float(pos.get('entry_price') or 0)
    peak = float(pos.get('peak') or price or 0)

    rec = {
        'exit_ts': now, 'entry_ts': pos.get('entry_ts'),
        'token': token, 'symbol': pos.get('symbol'),
        'conviction': pos.get('conviction'),
        'entry_price': entry_price, 'exit_price': price,
        'peak_mult': round(peak / entry_price, 4) if entry_price else 0,
        'reason': 'manual_close_all', 'cost_usd': cost,
        'proceeds': round(proceeds, 6), 'pnl_usd': round(pnl, 4),
    }
    records.append(rec)
    proceeds_total += add
    pnl_total += pnl
    print(f"  close {pos.get('symbol', '?'):<12} @ {price:.3e} "
          f"remaining={remaining:,.0f} pnl=${pnl:+.2f}")

with open(LEDGER, 'a') as f:
    for rec in records:
        f.write(json.dumps(rec, default=str) + '\n')

d['cash'] = float(d.get('cash') or 0) + proceeds_total
d['realized'] = round(float(d.get('realized') or 0) + pnl_total, 4)
d['n_trades'] = int(d.get('n_trades') or 0) + len(records)
recent = [r for r in (d.get('recent_realized') or []) if now - float(r[0]) <= 24 * 3600]
recent.extend([[now, r['pnl_usd']] for r in records])
d['recent_realized'] = recent
d['open_pos'] = {}

tmp = STATE + '.tmp'
with open(tmp, 'w') as f:
    json.dump(d, f, default=str)
    f.flush()
    os.fsync(f.fileno())
os.replace(tmp, STATE)

print(f"\nclosed {len(records)} positions | proceeds +${proceeds_total:.2f} "
      f"| pnl ${pnl_total:+.2f}")
print(f"new cash ${d['cash']:.2f} (balance_sol still {d.get('balance_sol')}; "
      "+3 SOL credits on restart)")
for w in warnings:
    print('⚠️ ', w)
if not warnings:
    print('no live on-chain exposure detected on any closed position')
