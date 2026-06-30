"""Final adversarial checks: (1) FDV claim robustness; (2) moon-bag exit counterfactual."""
import json
import time
from collections import defaultdict

alerts = json.load(open('analysis/_alerts_dataset_enriched.json'))

print('=== CHECK 1: FDV monotonicity robustness (lifetime label) ===')
# dedup first alert per token
seen = set()
ded = []
for a in sorted(alerts, key=lambda x: x['alert_timestamp']):
    if a['token_address'] in seen:
        continue
    seen.add(a['token_address'])
    ded.append(a)
print(f'deduped alerts: {len(ded)}')


def fdv_bucket(a):
    f = a.get('alert_fdv')
    if not f:
        return None
    if f < 15000:
        return '<15k'
    if f < 25000:
        return '15-25k'
    if f < 60000:
        return '25-60k'
    return '>=60k'


def table(rows, label_fn, title):
    g = defaultdict(lambda: [0, 0])
    for a in rows:
        b = fdv_bucket(a)
        y = label_fn(a)
        if b is None or y is None:
            continue
        g[b][0] += 1
        g[b][1] += y
    print(f'\n{title}')
    for b in ('<15k', '15-25k', '25-60k', '>=60k'):
        n, r = g[b]
        if n:
            print(f'  {b:<8} n={n:>4} runner={100*r/n:.1f}%')


table(ded, lambda a: 1 if (a.get('max_multiple') or 0) >= 2 else 0, 'lifetime label, deduped:')
table(ded, lambda a: (1 if a['candle_6h_max'] >= 2 else 0) if a.get('candle_6h_max') else None,
      'candle 6h label, deduped:')
table(ded, lambda a: (1 if a['candle_24h_max'] >= 2 else 0) if a.get('candle_24h_max') else None,
      'candle 24h label, deduped:')

# per-week lifetime label
weeks = defaultdict(list)
for a in ded:
    wk = time.strftime('%m-%d', time.gmtime(a['alert_timestamp'] - a['alert_timestamp'] % (7 * 86400)))
    weeks[wk].append(a)
for wk in sorted(weeks):
    table(weeks[wk], lambda a: 1 if (a.get('max_multiple') or 0) >= 2 else 0, f'week {wk} (lifetime label):')

# tracking-horizon confound: lifetime label vs tracked hours by fdv bucket
print('\ntracking horizon by fdv bucket (could confound lifetime label):')
g = defaultdict(list)
import sqlite3
con = sqlite3.connect('file:scanner.db?mode=ro', uri=True)
hz = {r[0]: (r[2] or r[1]) - r[1] for r in con.execute(
    'select id, alert_timestamp, last_timestamp from ignition_alerts')}
for a in ded:
    b = fdv_bucket(a)
    if b and a['alert_id'] in hz:
        g[b].append(hz[a['alert_id']] / 3600)
for b in ('<15k', '15-25k', '25-60k', '>=60k'):
    v = sorted(g[b])
    if v:
        print(f'  {b:<8} median tracked {v[len(v)//2]:.0f}h')

print('\n=== CHECK 2: moon-bag exit counterfactual (candle-covered trades) ===')
trades = json.load(open('analysis/_trades_dataset.json'))
rows = [t for t in trades if t.get('candle_max_24h_multiple') and t.get('candle_count_24h', 0) >= 12]
con2 = sqlite3.connect('file:scanner.db?mode=ro', uri=True)

# policy: at actual exit, sell only 50%; hold rest with stop = max(0.70*entry, actual_exit*0.65),
# sell at first candle low breaching stop, else at best of (2x target hit) or 24h close.
base_pnl = mb_pnl = 0.0
details = []
for t in rows:
    ep, ea, xp = t['entry_price'], t['entry_at'], t.get('exit_price') or 0
    notional = t.get('entry_notional_usd') or 20.0
    realized = t.get('exit_multiple') or (xp / ep if ep else 0)
    exit_at = t.get('exit_at') or ea
    base = notional * (realized - 1)
    base_pnl += base
    cs = con2.execute(
        'select bucket_start, high, low, close from token_candles '
        'where token_address=? and bucket_start>? and bucket_start<=? order by bucket_start',
        (t['address'], exit_at, ea + 24 * 3600)).fetchall()
    half = notional / 2
    pnl1 = half * (realized - 1)  # half sold at actual exit
    if not cs:
        pnl2 = half * (realized - 1)
    else:
        stop = max(0.70, realized * 0.65)
        target = 2.0
        out_mult = None
        for b0, h, l, c in cs:
            if l and l / ep <= stop:
                out_mult = stop
                break
            if h and h / ep >= target:
                out_mult = target
                break
        if out_mult is None:
            out_mult = (cs[-1][3] or xp) / ep
        pnl2 = half * (out_mult - 1)
    mb = pnl1 + pnl2
    mb_pnl += mb
    details.append((t.get('symbol'), realized, t['candle_max_24h_multiple'], base, mb))

print(f'n={len(rows)} trades with candle coverage')
print(f'actual exits PnL:    ${base_pnl:+.2f}')
print(f'moon-bag policy PnL: ${mb_pnl:+.2f}  (sell 50% at actual exit, hold 50% w/ stop max(0.70x entry, 0.65x exit-mult), 2x target, 24h horizon)')
wins = sum(1 for d in details if d[4] > d[3] + 0.01)
loss = sum(1 for d in details if d[4] < d[3] - 0.01)
print(f'trades where moon-bag beat actual: {wins}, worse: {loss}')
details.sort(key=lambda d: d[4] - d[3])
print('worst 5 deltas:', [(d[0], round(d[4] - d[3], 2)) for d in details[:5]])
print('best 5 deltas: ', [(d[0], round(d[4] - d[3], 2)) for d in details[-5:]])
