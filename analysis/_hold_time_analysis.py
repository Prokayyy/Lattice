"""Where does winner PnL resolve in time? Pick a max-hold cut from data."""
import json
import time
from collections import defaultdict
from pathlib import Path

trades = []
for line in Path('discovery/trades.jsonl').read_text().splitlines():
    line = line.strip()
    if line:
        try:
            trades.append(json.loads(line))
        except json.JSONDecodeError:
            pass

rows = []
for t in trades:
    e, x = float(t.get('entry_ts') or 0), float(t.get('exit_ts') or 0)
    if e > 0 and x > e:
        rows.append({
            'hold_h': (x - e) / 3600,
            'pnl': float(t.get('pnl_usd') or 0),
            'reason': str(t.get('reason') or '?'),
            'peak': float(t.get('peak_mult') or 0),
        })
print(f'closed trades with hold time: {len(rows)}')

# winners: when do they exit?
wins = sorted((r for r in rows if r['pnl'] > 0), key=lambda r: r['hold_h'])
if wins:
    tot = sum(r['pnl'] for r in wins)
    print(f'\nwinners n={len(wins)} total +${tot:.2f}; PnL captured by exit time:')
    for cap in (2, 4, 6, 8, 12, 18, 24, 36, 48):
        sub = [r for r in wins if r['hold_h'] <= cap]
        print(f'  <={cap:>2}h: {len(sub):>3} wins, ${sum(r["pnl"] for r in sub):>8.2f} '
              f'({100*sum(r["pnl"] for r in sub)/tot:.0f}% of win PnL)')

# losers: hold time distribution
loss = [r for r in rows if r['pnl'] <= 0]
print(f'\nlosers n={len(loss)} total ${sum(r["pnl"] for r in loss):.2f}')
for cap in (6, 12, 24, 48):
    sub = [r for r in loss if r['hold_h'] > cap]
    print(f'  held >{cap:>2}h: {len(sub):>3} losers, ${sum(r["pnl"] for r in sub):>8.2f}')

# max_hold exits specifically: what did they tie up?
mh = [r for r in rows if r['reason'] == 'max_hold']
if mh:
    hs = sorted(r['hold_h'] for r in mh)
    print(f'\nmax_hold exits n={len(mh)} pnl ${sum(r["pnl"] for r in mh):.2f} '
          f'| hold p50={hs[len(hs)//2]:.0f}h '
          f'| slot-days consumed: {sum(r["hold_h"] for r in mh)/24:.0f}')

# no-progress stop usage
np_ = [r for r in rows if 'no_progress' in r['reason']]
print(f'no_progress exits n={len(np_)} pnl ${sum(r["pnl"] for r in np_):.2f}')

# if max_hold had been H, how many slot-hours would have been freed
# (only counts trades that actually held longer than H and exited <= flat)
print('\nslot-hours freed by cutting max_hold (stagnant trades only, pnl<=+$1):')
stag = [r for r in rows if r['pnl'] <= 1.0]
for cap in (6, 8, 12, 18, 24):
    freed = sum(max(r['hold_h'] - cap, 0) for r in stag)
    print(f'  cap {cap:>2}h: ~{freed/24:.0f} slot-days freed')

# winners that needed long holds (what a tight cap would cost)
print('\nwinners by hold bucket (what >cap winners look like):')
g = defaultdict(list)
for r in wins:
    b = '<=6h' if r['hold_h'] <= 6 else '6-12h' if r['hold_h'] <= 12 else \
        '12-24h' if r['hold_h'] <= 24 else '>24h'
    g[b].append(r)
for b in ('<=6h', '6-12h', '12-24h', '>24h'):
    v = g.get(b, [])
    if v:
        pk = sorted(r['peak'] for r in v)
        print(f'  {b:<7} n={len(v):>3} pnl ${sum(r["pnl"] for r in v):>8.2f} '
              f'median peak {pk[len(pk)//2]:.2f}x')
