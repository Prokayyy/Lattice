import sqlite3
import time

con = sqlite3.connect('file:scanner.db?mode=ro', uri=True)
rows = con.execute(
    "select date(timestamp, 'unixepoch') d, count(*) n from candidate_events "
    'group by d order by d').fetchall()
for d, n in rows:
    print(f'{d}: {n} eligible candidates')
total_days = max(len(rows), 1)
total = sum(n for _, n in rows)
print(f'avg/day: {total/total_days:.0f}  -> twitter searches/month ~ {30*total/total_days:.0f}')
