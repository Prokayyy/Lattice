"""Coverage + label-integrity checks on the enriched alert dataset."""
import json
import sqlite3
import time
from collections import defaultdict

alerts = json.load(open('analysis/_alerts_dataset_enriched.json'))
n = len(alerts)
have_snap = sum(1 for a in alerts if a.get('snap'))
have_traj = sum(1 for a in alerts if a.get('traj'))
print(f'alerts: {n}, snap: {have_snap}, traj: {have_traj}')
if alerts and alerts[0].get('snap') is not None or True:
    # sample snap keys
    for a in alerts:
        if a.get('snap'):
            print('snap keys:', sorted(a['snap'].keys()))
            break
        if a.get('traj'):
            print('traj keys:', sorted(a['traj'].keys()))

# coverage by day
day = defaultdict(lambda: [0, 0])
for a in alerts:
    d = time.strftime('%m-%d', time.gmtime(a['alert_timestamp']))
    day[d][0] += 1
    day[d][1] += bool(a.get('snap'))
print('coverage by day:', {k: f'{v[1]}/{v[0]}' for k, v in sorted(day.items())})

# ---- label integrity ----
print('\n--- LABEL CHECKS ---')
hot = sqlite3.connect('file:scanner.db?mode=ro', uri=True)
rows = hot.execute(
    'select alert_timestamp, last_timestamp, status, max_multiple from ignition_alerts').fetchall()
hz = sorted(((r[1] or r[0]) - r[0]) / 3600 for r in rows)
m = len(hz)
print(f'tracking horizon hours: p10={hz[int(0.1*m)]:.2f} p50={hz[m//2]:.2f} p90={hz[int(0.9*m)]:.2f}')
short = [r for r in rows if ((r[1] or r[0]) - r[0]) < 6 * 3600]
short_nr = [r for r in short if (r[3] or 0) < 2.0]
print(f'tracked <6h: {len(short)} ({100*len(short)/m:.0f}%); of those max<2 (censored FN risk): {len(short_nr)}')
st = defaultdict(int)
for r in rows:
    st[r[2]] += 1
print('status counts:', dict(st))

cnt = defaultdict(int)
for a in alerts:
    cnt[a['token_address']] += 1
dist = defaultdict(int)
for c in cnt.values():
    dist[min(c, 5)] += 1
print('alerts-per-token (5=5+):', dict(sorted(dist.items())))

bad = n6 = comp = 0
for a in alerts:
    w = a['windows'].get('6h')
    if w and w.get('max_multiple') is not None and a.get('max_multiple') is not None:
        n6 += 1
        comp += bool(w.get('complete'))
        if w['max_multiple'] > a['max_multiple'] + 1e-9:
            bad += 1
print(f'6h windows: {n6}, complete: {comp}, window>lifetime violations: {bad}')

# 6h label base rates
y6 = [a['windows']['6h']['max_multiple'] for a in alerts
      if a['windows'].get('6h', {}).get('max_multiple') is not None
      and a['windows']['6h'].get('complete')]
y6.sort()
k = len(y6)
print(f'complete-6h alerts: {k} | >=2x: {sum(1 for v in y6 if v>=2)} ({100*sum(1 for v in y6 if v>=2)/k:.1f}%) '
      f'| <1.2x (loser): {sum(1 for v in y6 if v<1.2)} ({100*sum(1 for v in y6 if v<1.2)/k:.1f}%)')

# min_multiple drawdown before peak among 6h runners
dd = [a['windows']['6h']['min_multiple'] for a in alerts
      if a['windows'].get('6h', {}).get('complete')
      and (a['windows']['6h'].get('max_multiple') or 0) >= 2
      and a['windows']['6h'].get('min_multiple') is not None]
dd.sort()
if dd:
    print(f'6h runners min_multiple: p10={dd[int(0.1*len(dd))]:.2f} p50={dd[len(dd)//2]:.2f} '
          f'| frac dipping <0.7 first: {100*sum(1 for v in dd if v<0.7)/len(dd):.0f}%')

# alert price sanity
bad_p = sum(1 for a in alerts if not a.get('alert_price') or a['alert_price'] <= 0)
no_max = sum(1 for a in alerts if a.get('max_multiple') is None)
print(f'bad alert_price: {bad_p}, null lifetime max_multiple: {no_max}')
