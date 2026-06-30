import sqlite3
import time

con = sqlite3.connect('file:scanner.db?mode=ro', uri=True)
now = time.time()
print('ignition alerts per day (last 14d):')
rows = con.execute(
    "select date(alert_timestamp, 'unixepoch') d, count(*) n, "
    'count(distinct token_address) toks from ignition_alerts '
    'where alert_timestamp > ? group by d order by d', (now - 14 * 86400,)).fetchall()
for d, n, t in rows:
    print(f'  {d}: {n} alerts, {t} unique tokens')
if not rows:
    print('  (none in 14d)')
total = con.execute(
    'select count(*), count(distinct token_address), '
    "max(date(alert_timestamp, 'unixepoch')) from ignition_alerts").fetchone()
print(f'all-time: {total[0]} alerts, {total[1]} tokens, last alert date: {total[2]}')
