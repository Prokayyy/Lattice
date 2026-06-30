import sqlite3
import time

con = sqlite3.connect('file:scanner.db?mode=ro', uri=True)
now = time.time()
for label, since in (('since 06-10', now - 2 * 86400), ('last 6h', now - 6 * 3600)):
    n = con.execute(
        'select count(*), sum(alert_eligible) from signal_snapshots '
        'where timestamp > ?', (since,)).fetchone()
    print(f'{label}: snapshots={n[0]:,} eligible={n[1] or 0}')

n = con.execute('select count(*) from candidate_events').fetchone()[0]
print('candidate_events rows:', n)

# eligible snapshot sample: what does the alert_eligible column hold?
r = con.execute(
    'select alert_eligible, count(*) from signal_snapshots '
    'where timestamp > ? group by alert_eligible', (now - 2 * 86400,)).fetchall()
print('alert_eligible value distribution (2d):', r)
