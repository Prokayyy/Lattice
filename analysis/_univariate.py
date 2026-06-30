"""Univariate runner-vs-loser analysis on trades + alerts. Stdlib only."""
import json
import math
from collections import defaultdict

# ---------- helpers ----------
def pct(x, n):
    return f'{100.0 * x / n:.1f}%' if n else 'n/a'


def bucket_table(rows, key_fn, label_fn, outcome_fn, title):
    """Group rows into buckets; report count, runner rate, loser rate, median outcome."""
    groups = defaultdict(list)
    for r in rows:
        k = key_fn(r)
        if k is None:
            continue
        groups[k].append(outcome_fn(r))
    print(f'\n### {title}')
    print(f'{"bucket":<22} {"n":>5} {"runner>=2x":>10} {"loser<0.85x":>11} {"median":>7} {"p90":>7}')
    for k in sorted(groups, key=lambda x: (isinstance(x, str), x)):
        vals = [v for v in groups[k] if v is not None]
        if not vals:
            continue
        vals.sort()
        n = len(vals)
        runners = sum(1 for v in vals if v >= 2.0)
        losers = sum(1 for v in vals if v < 0.85)
        med = vals[n // 2]
        p90 = vals[min(n - 1, int(0.9 * n))]
        print(f'{label_fn(k):<22} {n:>5} {pct(runners, n):>10} {pct(losers, n):>11} {med:>7.2f} {p90:>7.2f}')


def num_bucket(val, edges):
    if val is None:
        return None
    for i, e in enumerate(edges):
        if val < e:
            return i
    return len(edges)


def edge_label(i, edges):
    if i == 0:
        return f'<{edges[0]}'
    if i == len(edges):
        return f'>={edges[-1]}'
    return f'{edges[i-1]}-{edges[i]}'


# ---------- trades ----------
trades = json.load(open('analysis/_trades_dataset.json'))
print('=' * 70)
print(f'TRADES: {len(trades)} closed (deduped across 3 ledgers)')

# outcome: peak multiple while held (entry->peak). exit_multiple = realized.
def t_outcome(t):
    return t.get('peak_multiple_calc') or t.get('peak_multiple')

valid = [t for t in trades if t_outcome(t)]
outs = sorted(t_outcome(t) for t in valid)
n = len(outs)
runners = sum(1 for v in outs if v >= 2.0)
losers = sum(1 for v in outs if v < 0.85)
print(f'with peak outcome: {n} | runners(peak>=2x): {runners} ({pct(runners,n)}) | '
      f'never above 0.85x peak: {losers}')

# realized
rea = sorted(t['exit_multiple'] for t in trades if t.get('exit_multiple'))
nr = len(rea)
print(f'realized exit multiples: n={nr} median={rea[nr//2]:.3f} '
      f'>=2x realized: {sum(1 for v in rea if v>=2.0)} '
      f'wins(>1.0): {sum(1 for v in rea if v>1.0)} ({pct(sum(1 for v in rea if v>1.0),nr)})')

# pnl
pnl = [t.get('pnl_usd') or 0 for t in trades]
print(f'total pnl_usd: {sum(pnl):+.2f} | avg {sum(pnl)/len(pnl):+.2f}')

FEATURES = [
    ('entry_score', [60, 65, 70, 75, 80]),
    ('entry_impulse', [0.9, 1.0, 1.1, 1.2, 1.5]),
    ('entry_pressure', [55, 60, 65, 70, 80]),
    ('entry_liquidity', [5000, 10000, 15000, 25000]),
    ('entry_fdv', [15000, 25000, 40000, 60000]),
    ('entry_volume_1h', [10000, 20000, 40000, 80000]),
    ('entry_volume_multiple', [2, 3, 5, 8]),
    ('entry_volume_liquidity_ratio', [1, 2, 4, 8]),
    ('entry_buy_sell_ratio', [1.0, 1.15, 1.3, 1.5]),
    ('entry_migration_distance_pct', [0.1, 0.3, 0.6, 1.0]),
    ('entry_confirmation_score', [40, 60, 80, 100]),
]
for feat, edges in FEATURES:
    bucket_table(
        valid,
        lambda t, f=feat, e=edges: num_bucket(t.get(f), e),
        lambda i, e=edges: edge_label(i, e),
        t_outcome,
        f'trades by {feat}',
    )

for cat in ('entry_route', 'entry_quality_tier', 'close_reason'):
    bucket_table(valid, lambda t, c=cat: t.get(c) or 'unknown', str, t_outcome, f'trades by {cat}')

# ---------- alerts ----------
alerts = json.load(open('analysis/_alerts_dataset.json'))
print('\n' + '=' * 70)
print(f'ALERTS: {len(alerts)}')

# choose outcome window: lifetime max_multiple on alert row, plus windowed
wl_counts = defaultdict(int)
for a in alerts:
    for w in a['windows']:
        wl_counts[w] += 1
print('windows:', dict(wl_counts))

def a_outcome(a):
    return a.get('max_multiple')

valid_a = [a for a in alerts if a_outcome(a)]
outs = sorted(a_outcome(a) for a in valid_a)
n = len(outs)
print(f'alerts with max_multiple: {n} | >=2x: {sum(1 for v in outs if v>=2.0)} '
      f'({pct(sum(1 for v in outs if v>=2.0),n)}) | >=1.5x: {sum(1 for v in outs if v>=1.5)} '
      f'({pct(sum(1 for v in outs if v>=1.5),n)}) | median: {outs[n//2]:.2f}')

AFEATS = [
    ('score', [60, 65, 70, 75, 80]),
    ('raw_score', [60, 70, 80, 90]),
    ('penalty', [0.01, 5, 10, 20]),
    ('alert_impulse', [0.9, 1.0, 1.1, 1.2, 1.5]),
    ('alert_pressure', [55, 60, 65, 70, 80]),
    ('alert_liquidity', [5000, 10000, 15000, 25000]),
    ('alert_fdv', [15000, 25000, 40000, 60000]),
]
for feat, edges in AFEATS:
    bucket_table(
        valid_a,
        lambda a, f=feat, e=edges: num_bucket(a.get(f), e),
        lambda i, e=edges: edge_label(i, e),
        a_outcome,
        f'alerts by {feat}',
    )

for cat in ('alert_route', 'quality_tag', 'chain_name'):
    bucket_table(valid_a, lambda a, c=cat: a.get(c) or 'unknown', str, a_outcome, f'alerts by {cat}')

# windowed outcomes by route (4h window if present)
def win_outcome(a, label):
    w = a['windows'].get(label)
    return w['max_multiple'] if w else None

for label in sorted(wl_counts):
    rows = [a for a in alerts if win_outcome(a, label)]
    vals = sorted(win_outcome(a, label) for a in rows)
    if not vals:
        continue
    n = len(vals)
    print(f'\nwindow {label}: n={n} >=2x: {pct(sum(1 for v in vals if v>=2.0), n)} '
          f'median: {vals[n//2]:.2f}')
