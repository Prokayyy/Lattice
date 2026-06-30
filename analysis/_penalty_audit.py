"""Penalty audit on clean candle labels.

Question: the lifetime-label analysis showed penalty 5-10 alerts running MORE
(40.8%) than zero-penalty alerts (29.3%). Is that real on fixed-horizon labels,
and which penalty source drives it?

Penalty sources (main.py calculate_ignition_score):
  +10 data-gap (flow unconfirmed; NOT a signal penalty)   +5 fresh mint <6h
  +8 soft 1h sell flow / extended 6h move                 +15 1h sell flow
  +10 thin activity / extreme VLR no flow / vol no confirm / negative 1h
  +25 migrated fragile  +35 extended cooling  (stacking = sums)
"""
import json
import math
import sqlite3
import time
from collections import defaultdict

con = sqlite3.connect('file:scanner.db?mode=ro', uri=True)
con.row_factory = sqlite3.Row

rows = [dict(r) for r in con.execute("""
    SELECT a.id, a.token_address, a.alert_timestamp AS ts, a.penalty,
        a.raw_score, a.score, a.alert_route, a.quality_tag, a.alert_fdv,
        a.max_multiple AS life_max,
        l.h6_max_multiple AS h6, l.h24_max_multiple AS h24
    FROM ignition_alerts a
    LEFT JOIN alert_candle_labels l
        ON l.subject_type='alert' AND l.subject_id = a.id
    WHERE a.alert_price > 0
    ORDER BY a.alert_timestamp
""")]

# dedup first alert per token per 24h
last = {}
ded = []
for r in rows:
    p = last.get(r['token_address'])
    if p is not None and r['ts'] - p < 24 * 3600:
        continue
    last[r['token_address']] = r['ts']
    ded.append(r)
print(f'alerts {len(rows)} -> deduped {len(ded)}')


def wilson(k, n, z=1.96):
    if n == 0:
        return (0, 0, 0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (p, c - h, c + h)


def rate_table(rows, key_fn, label_fn, title, min_n=8):
    print(f'\n### {title}')
    print(f'{"bucket":<26}{"n":>5} | {"life>=2x":>22} | {"h6>=2x":>22} | {"h24>=2x":>22}')
    groups = defaultdict(list)
    for r in rows:
        k = key_fn(r)
        if k is not None:
            groups[k].append(r)
    for k in sorted(groups, key=lambda x: (isinstance(x, str), x)):
        g = groups[k]
        if len(g) < min_n:
            continue
        cells = []
        for lab in ('life_max', 'h6', 'h24'):
            vals = [x for x in (r.get(lab) for r in g) if x is not None]
            kk = sum(1 for v in vals if v >= 2.0)
            p, lo, hi = wilson(kk, len(vals))
            cells.append(f'{100*p:5.1f}% [{100*lo:4.1f}-{100*hi:5.1f}] n={len(vals):<4}'
                         if vals else f'{"-":>21}')
        print(f'{label_fn(k):<26}{len(g):>5} | ' + ' | '.join(cells))


def band(p):
    p = p or 0
    if p < 0.01:
        return '0'
    if p < 5:
        return '0.01-5'
    if p < 10:
        return '5-10'
    if p < 20:
        return '10-20'
    return '>=20'


rate_table(ded, lambda r: band(r['penalty']), str, 'runner rate by penalty band (deduped)')

# exact penalty values inside 0-12 (composition tells the source)
rate_table([r for r in ded if (r['penalty'] or 0) <= 12],
           lambda r: int(round(r['penalty'] or 0)), lambda k: f'penalty={k}',
           'exact penalty values 0-12')

# per-route control for the 5-10 band vs 0
for route in ('bonding_early_revival', 'bonding_momentum_high_conviction', 'immediate'):
    sub = [r for r in ded if r['alert_route'] == route]
    rate_table(sub, lambda r: band(r['penalty']), str, f'penalty bands within route={route}', min_n=5)

# per-week control (lifetime label only has wide coverage)
def week(r):
    return time.strftime('%m-%d', time.gmtime(r['ts'] - r['ts'] % (7 * 86400)))

for wk in sorted({week(r) for r in ded}):
    sub = [r for r in ded if week(r) == wk]
    g0 = [r for r in sub if band(r['penalty']) == '0']
    g5 = [r for r in sub if band(r['penalty']) == '5-10']
    if len(g0) >= 8 and len(g5) >= 8:
        r0 = sum(1 for r in g0 if (r['life_max'] or 0) >= 2) / len(g0)
        r5 = sum(1 for r in g5 if (r['life_max'] or 0) >= 2) / len(g5)
        print(f'week {wk}: penalty=0 {100*r0:.0f}% (n={len(g0)}) vs 5-10 {100*r5:.0f}% (n={len(g5)})')

# penalty=5 is uniquely the fresh-mint(<6h) penalty: head-to-head within bonding routes
bonding = [r for r in ded if (r['alert_route'] or '').startswith('bonding')]
g5 = [r for r in bonding if int(round(r['penalty'] or 0)) == 5]
g0 = [r for r in bonding if int(round(r['penalty'] or 0)) == 0]
for lab in ('life_max', 'h6', 'h24'):
    v5 = [x for x in (r.get(lab) for r in g5) if x is not None]
    v0 = [x for x in (r.get(lab) for r in g0) if x is not None]
    if v5 and v0:
        p5 = sum(1 for v in v5 if v >= 2) / len(v5)
        p0 = sum(1 for v in v0 if v >= 2) / len(v0)
        print(f'fresh-mint penalty=5 vs 0 (bonding, {lab}): {100*p5:.1f}% (n={len(v5)}) vs {100*p0:.1f}% (n={len(v0)})')
