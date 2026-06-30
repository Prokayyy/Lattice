"""Compute candle-based fixed-horizon labels (6h/24h max multiple) for all alerts."""
import json
import sys
import time
from collections import defaultdict

sys.path.insert(0, '.')
from storage.history import open_history

con = open_history()
alerts = json.load(open('analysis/_alerts_dataset_enriched.json'))
by_token = defaultdict(list)
for a in alerts:
    by_token[a['token_address']].append(a)

t0 = time.time()
have6 = have24 = 0
for i, (token, toks) in enumerate(by_token.items()):
    lo = min(a['alert_timestamp'] for a in toks)
    hi = max(a['alert_timestamp'] for a in toks) + 24 * 3600
    rows = con.execute(
        'select bucket_start, high, close from token_candles_all '
        'where token_address=? and bucket_start>=? and bucket_start<=? order by bucket_start',
        (token, lo, hi)).fetchall()
    for a in toks:
        at, ap = a['alert_timestamp'], a.get('alert_price')
        a['candle_6h_max'] = a['candle_24h_max'] = None
        a['candle_6h_n'] = 0
        if not ap or ap <= 0:
            continue
        h6 = [r[1] for r in rows if at < r[0] <= at + 6 * 3600 and r[1]]
        h24 = [r[1] for r in rows if at < r[0] <= at + 24 * 3600 and r[1]]
        # sustained check: closes
        c6 = [r[2] for r in rows if at < r[0] <= at + 6 * 3600 and r[2]]
        a['candle_6h_n'] = len(h6)
        if len(h6) >= 30:  # require >=30 minutes of coverage
            a['candle_6h_max'] = max(h6) / ap
            a['candle_6h_close_max'] = (max(c6) / ap) if c6 else None
            have6 += 1
        if len(h24) >= 60:
            a['candle_24h_max'] = max(h24) / ap
            have24 += 1
    if i % 200 == 0:
        print(f'  {i}/{len(by_token)} tokens, {time.time()-t0:.0f}s')

print(f'candle 6h labels: {have6}/{len(alerts)}, 24h: {have24} in {time.time()-t0:.0f}s')

# coverage by day + agreement with alert_outcomes 6h window where both exist
daily = defaultdict(lambda: [0, 0])
agree = both = 0
for a in alerts:
    d = time.strftime('%m-%d', time.gmtime(a['alert_timestamp']))
    daily[d][0] += 1
    daily[d][1] += a['candle_6h_max'] is not None
    w = a['windows'].get('6h')
    if a['candle_6h_max'] and w and w.get('complete') and w.get('max_multiple'):
        both += 1
        if (a['candle_6h_max'] >= 2.0) == (w['max_multiple'] >= 2.0):
            agree += 1
print('coverage by day:', {k: f'{v[1]}/{v[0]}' for k, v in sorted(daily.items())})
print(f'label agreement with complete alert_outcomes 6h windows: {agree}/{both}')

# base rates on candle labels
v6 = sorted(a['candle_6h_max'] for a in alerts if a['candle_6h_max'])
k = len(v6)
if k:
    print(f'candle 6h: n={k} >=2x: {sum(1 for v in v6 if v>=2)} ({100*sum(1 for v in v6 if v>=2)/k:.1f}%) '
          f'<1.2x: {100*sum(1 for v in v6 if v<1.2)/k:.1f}% median {v6[k//2]:.2f}')
v24 = sorted(a['candle_24h_max'] for a in alerts if a['candle_24h_max'])
k = len(v24)
if k:
    print(f'candle 24h: n={k} >=2x: {sum(1 for v in v24 if v>=2)} ({100*sum(1 for v in v24 if v>=2)/k:.1f}%)')

json.dump(alerts, open('analysis/_alerts_dataset_enriched.json', 'w'))
print('updated analysis/_alerts_dataset_enriched.json')
