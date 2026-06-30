"""Split penalty=8 alerts by source: soft 1h sell flow vs extended 6h move.

Extended-move -8 fires only for bonding lifecycle with price_change_6h >= 150;
the enriched snapshots (May 23-31) carry price_change_6h at alert time.
"""
import json
import sqlite3

alerts = json.load(open('analysis/_alerts_dataset_enriched.json'))
con = sqlite3.connect('file:scanner.db?mode=ro', uri=True)
labels = {r[0]: (r[1], r[2]) for r in con.execute(
    "select subject_id, h6_max_multiple, h24_max_multiple "
    "from alert_candle_labels where subject_type='alert'")}

# dedup first per token
seen = set()
groups = {'soft_sell_flow': [], 'extended_6h': [], 'both?': [], 'unknown': []}
for a in sorted(alerts, key=lambda x: x['alert_timestamp']):
    if a['token_address'] in seen:
        continue
    seen.add(a['token_address'])
    if int(round(a.get('penalty') or 0)) != 8:
        continue
    snap = a.get('snap')
    lab = labels.get(a['alert_id'])
    if not snap or not lab:
        continue
    p6 = snap.get('price_change_6h')
    lc = snap.get('lifecycle') or ''
    ext = p6 is not None and p6 >= 150 and 'bonding' in lc
    groups['extended_6h' if ext else 'soft_sell_flow'].append((a, lab))

for g, rows in groups.items():
    if not rows:
        continue
    for i, lab_name in ((0, 'h6'), (1, 'h24')):
        vals = [l[i] for _, l in rows if l[i] is not None]
        if vals:
            k = sum(1 for v in vals if v >= 2)
            print(f'{g:<16} {lab_name}: {100*k/len(vals):5.1f}% runners (n={len(vals)})')

# also lifetime on full deduped set using h1 sell-flow reconstruction not possible
# pre-enrichment; report what we have.
