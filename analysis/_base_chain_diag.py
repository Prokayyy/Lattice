"""Why do Base pairs never alert? Funnel analysis from signal_snapshots."""
import sqlite3
import time
from collections import Counter

con = sqlite3.connect('file:scanner.db?mode=ro', uri=True)
con.row_factory = sqlite3.Row
since = time.time() - 48 * 3600

print('=== snapshots by chain (48h) ===')
for r in con.execute(
        'select chain_name, count(*) n, count(distinct token_address) toks, '
        'sum(alert_eligible) elig, max(score) mx_score, avg(score) avg_score '
        'from signal_snapshots where timestamp>? group by chain_name', (since,)):
    print(dict(r))

print('\n=== base: score distribution + lifecycle (48h) ===')
for r in con.execute(
        "select lifecycle, count(*) n, max(score) mx, max(raw_score) mx_raw, "
        "avg(penalty) avg_pen from signal_snapshots "
        "where chain_name='base' and timestamp>? group by lifecycle", (since,)):
    print(dict(r))

print('\n=== base: top missing-field combos (48h) ===')
miss = Counter()
for r in con.execute(
        "select missing from signal_snapshots where chain_name='base' "
        'and timestamp>?', (since,)):
    m = r['missing'] or ''
    for part in m.split(',') if m else ['<none>']:
        miss[part.strip() or '<empty>'] += 1
for k, v in miss.most_common(12):
    print(f'  {k}: {v}')

print('\n=== base: route/quality assignment (48h) ===')
for r in con.execute(
        "select alert_route, quality_tag, count(*) n, max(score) mx "
        "from signal_snapshots where chain_name='base' and timestamp>? "
        'group by alert_route, quality_tag order by n desc limit 10', (since,)):
    print(dict(r))

print('\n=== base: best 5 snapshots by score (48h) ===')
for r in con.execute(
        "select symbol, score, raw_score, penalty, alert_eligible, alert_route, "
        "quality_tag, missing, volume_1h, liquidity, fdv, buys_5m, sells_5m "
        "from signal_snapshots where chain_name='base' and timestamp>? "
        'order by score desc limit 5', (since,)):
    print(dict(r))

print('\n=== compare: solana top-5 (48h) for reference ===')
for r in con.execute(
        "select symbol, score, raw_score, penalty, alert_eligible, alert_route "
        "from signal_snapshots where chain_name='solana' and timestamp>? "
        'order by score desc limit 5', (since,)):
    print(dict(r))
