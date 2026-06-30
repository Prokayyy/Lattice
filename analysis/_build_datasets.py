"""Build trade + alert datasets for runner (>=2x) analysis.

Outputs:
  analysis/_trades_dataset.json   - deduped closed trades with entry features + outcomes
  analysis/_alerts_dataset.json   - ignition alerts with features + windowed outcomes
"""
import json
import sqlite3
import time

# ---------------- trades ----------------
LEDGERS = [
    'data/position_state.paper_backup_20260528.json',
    'data/position_state.json.bak-20260530-183838',
    'data/position_state.json',
]

ENTRY_FIELDS = [
    'entry_score', 'entry_impulse', 'entry_pressure', 'entry_liquidity',
    'entry_fdv', 'entry_volume_1h', 'entry_volume_multiple',
    'entry_volume_liquidity_ratio', 'entry_buy_sell_ratio',
    'entry_buy_volume_5m', 'entry_sell_volume_5m',
    'entry_migration_distance_pct', 'entry_migration_fdv',
    'entry_anchored_vwap_ready', 'entry_confirmation_score',
    'entry_confirmation_ready', 'entry_count_before',
    'entry_quality_tier', 'entry_route', 'entry_price', 'entry_at',
]

seen = set()
trades = []
for path in LEDGERS:
    d = json.load(open(path))
    for t in d.get('closed', []):
        key = (t.get('address'), round(t.get('entry_at', 0)))
        if key in seen:
            continue
        seen.add(key)
        row = {f: t.get(f) for f in ENTRY_FIELDS}
        row['address'] = t.get('address')
        row['symbol'] = t.get('symbol')
        row['close_reason'] = t.get('close_reason')
        row['exit_at'] = t.get('exit_at')
        row['exit_price'] = t.get('exit_price')
        row['pnl_usd'] = t.get('pnl_usd')
        row['pnl_pct'] = t.get('pnl_pct')
        row['peak_price'] = t.get('peak_price')
        row['peak_multiple'] = t.get('peak_multiple')
        row['realized_usd'] = t.get('realized_usd')
        row['entry_notional_usd'] = t.get('entry_notional_usd')
        row['ledger'] = path.split('/')[-1]
        ep = t.get('entry_price') or 0
        pp = t.get('peak_price') or 0
        row['peak_multiple_calc'] = (pp / ep) if ep else None
        xp = t.get('exit_price') or 0
        row['exit_multiple'] = (xp / ep) if ep else None
        trades.append(row)

trades.sort(key=lambda r: r.get('entry_at') or 0)
print(f'trades deduped: {len(trades)}')

# enrich with post-entry max from token_candles (lookahead 24h)
con = sqlite3.connect('file:scanner.db?mode=ro', uri=True)
have_candles = 0
for row in trades:
    ea = row['entry_at']
    if not ea or not row['address']:
        continue
    r = con.execute(
        'select max(high), count(*) from token_candles '
        'where token_address=? and bucket_start>=? and bucket_start<=?',
        (row['address'], ea, ea + 24 * 3600),
    ).fetchone()
    if r and r[0] and row['entry_price']:
        row['candle_max_24h_multiple'] = r[0] / row['entry_price']
        row['candle_count_24h'] = r[1]
        have_candles += 1
    else:
        row['candle_max_24h_multiple'] = None
        row['candle_count_24h'] = 0
print(f'trades with candle enrichment: {have_candles}')

json.dump(trades, open('analysis/_trades_dataset.json', 'w'), indent=1)

# ---------------- alerts ----------------
# ignition_alerts has alert-time features + lifetime max_multiple.
# alert_outcomes has per-window (label) close/max multiples.
alerts = {}
cols = [c[1] for c in con.execute('pragma table_info(ignition_alerts)')]
for r in con.execute('select * from ignition_alerts'):
    a = dict(zip(cols, r))
    alerts[a['id']] = {
        'alert_id': a['id'],
        'token_address': a['token_address'],
        'symbol': a['symbol'],
        'chain_name': a['chain_name'],
        'alert_route': a['alert_route'],
        'quality_tag': a['quality_tag'],
        'score': a['score'],
        'raw_score': a['raw_score'],
        'penalty': a['penalty'],
        'alert_price': a['alert_price'],
        'alert_fdv': a['alert_fdv'],
        'alert_liquidity': a['alert_liquidity'],
        'alert_pressure': a['alert_pressure'],
        'alert_impulse': a['alert_impulse'],
        'alert_timestamp': a['alert_timestamp'],
        'max_multiple': a['max_multiple'],
        'min_multiple': a['min_multiple'],
        'status': a['status'],
        'windows': {},
    }

ocols = [c[1] for c in con.execute('pragma table_info(alert_outcomes)')]
n_out = 0
for r in con.execute('select * from alert_outcomes'):
    o = dict(zip(ocols, r))
    a = alerts.get(o['alert_id'])
    if a is None:
        continue
    a['windows'][o['window_label']] = {
        'close_multiple': o['close_multiple'],
        'max_multiple': o['max_multiple'],
        'min_multiple': o['min_multiple'],
        'time_to_peak_seconds': o['time_to_peak_seconds'],
        'liquidity_change_pct': o['liquidity_change_pct'],
        'snapshot_count': o['snapshot_count'],
        'complete': o['complete'],
    }
    n_out += 1
print(f'alerts: {len(alerts)}, outcome rows joined: {n_out}')

# enrich each alert with the nearest signal_snapshot at/before alert time
# (gives the full feature vector incl. RSI, vol/liq ratios, price changes)
SNAP_FIELDS = [
    'lifecycle', 'volume_5m', 'volume_1h', 'buys_5m', 'sells_5m', 'buys_1h',
    'sells_1h', 'price_change_5m', 'price_change_1h', 'price_change_6h',
    'pressure', 'impulse', 'volume_liquidity_ratio', 'buy_sell_ratio',
    'h1_volume_liquidity_ratio', 'h1_buy_sell_ratio', 'momentum_score',
    'local_rsi', 'local_rsi_ema', 'local_rsi_bullish', 'local_rsi_entry_ok',
    'local_rsi_candle_count', 'migration_distance_pct', 'fdv', 'liquidity',
    'risk_flags', 'alert_eligible',
]
have_snap = 0
for a in alerts.values():
    r = con.execute(
        f"select {', '.join(SNAP_FIELDS)} from signal_snapshots "
        'where token_address=? and timestamp<=? and timestamp>=? '
        'order by timestamp desc limit 1',
        (a['token_address'], a['alert_timestamp'] + 30, a['alert_timestamp'] - 600),
    ).fetchone()
    if r:
        a['snap'] = dict(zip(SNAP_FIELDS, r))
        have_snap += 1
    else:
        a['snap'] = None
print(f'alerts with snapshot features: {have_snap}')

ts = sorted(a['alert_timestamp'] for a in alerts.values() if a['alert_timestamp'])
fmt = lambda x: time.strftime('%Y-%m-%d', time.gmtime(x))
if ts:
    print('alert range:', fmt(ts[0]), '->', fmt(ts[-1]))

json.dump(list(alerts.values()), open('analysis/_alerts_dataset.json', 'w'), indent=1)
print('written analysis/_trades_dataset.json + analysis/_alerts_dataset.json')
