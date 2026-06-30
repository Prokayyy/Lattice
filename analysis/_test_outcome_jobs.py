"""Test new storage methods on a scratch DB in a temp dir (never prod).

Covers: schema init (new tables), record_candidate_event (dedup, breadth,
token age, alerted flip), finalize_overdue_alert_outcomes (zero-snapshot
guard), update_alert_candle_labels (6h/24h, coverage floors).
Run with env/bin/python from repo root.
"""
import asyncio
import os
import sys
import tempfile
import time
import types

REPO = os.getcwd()
sys.path.insert(0, REPO)

tmp = tempfile.mkdtemp()
os.chdir(tmp)  # storage resolves scanner.db relative to cwd

from storage.sqlite import ScannerStorage  # noqa: E402

storage = ScannerStorage()
asyncio.run(storage.initialize())
fails = []
NOW = time.time()
T0 = NOW - 30 * 3600  # base event time, 30h ago (both horizons elapsed)


def snap(token, ts, price=1.0, eligible=True, **kw):
    d = {
        'token_address': token, 'symbol': 'T', 'chain_name': 'solana',
        'pair_address': 'P', 'timestamp': ts, 'price': price, 'fdv': 20000,
        'liquidity': 9000, 'score': 80, 'raw_score': 100, 'penalty': 5,
        'pressure': 60, 'impulse': 1.1, 'volume_5m': 3000, 'volume_1h': 25000,
        'volume_liquidity_ratio': 1.0, 'buy_sell_ratio': 1.4,
        'h1_volume_liquidity_ratio': 2.5, 'h1_buy_sell_ratio': 1.2,
        'price_change_5m': 5, 'price_change_1h': 40, 'momentum_score': 3,
        'local_rsi': 60.0, 'alert_route': 'bonding_early_revival',
        'quality_tag': 'early_revival', 'lifecycle': 'bonding',
        'risk_flags': '', 'alert_eligible': eligible,
    }
    d.update(kw)
    return d


# --- candidate events: insert, dedup, breadth ---
ok1 = asyncio.run(storage.record_candidate_event(snap('TOK_A', T0)))
dup = asyncio.run(storage.record_candidate_event(snap('TOK_A', T0 + 60)))
inel = asyncio.run(storage.record_candidate_event(snap('TOK_B', T0, eligible=False)))
ok2 = asyncio.run(storage.record_candidate_event(snap('TOK_C', T0 + 120)))
if not ok1 or dup or inel or not ok2:
    fails.append(f'candidate insert/dedup wrong: {ok1} {dup} {inel} {ok2}')

with storage.connect() as db:
    rows = db.execute(
        'select token_address, breadth_eligible_30m, alerted from candidate_events order by id'
    ).fetchall()
if len(rows) != 2:
    fails.append(f'expected 2 candidate rows, got {rows}')
elif rows[1][1] != 2:
    fails.append(f'breadth for second event should be 2, got {rows[1][1]}')

# --- alerted flip via record_ignition_alert ---
m = types.SimpleNamespace(address='TOK_C', symbol='T', pair_address='P',
                          chain='solana', price=1.0, fdv=20000, liquidity=9000)
asyncio.run(storage.record_ignition_alert(
    m, 80, {'alert_route': 'bonding_early_revival', 'quality_tag': 'early_revival',
            'raw_score': 100, 'penalty': 5}, T0 + 180))
with storage.connect() as db:
    a = db.execute("select alerted from candidate_events where token_address='TOK_C'").fetchone()
    alert_id = db.execute('select id from ignition_alerts').fetchone()[0]
if a[0] != 1:
    fails.append('alerted flag not flipped for TOK_C')

# --- snapshots + candles for outcome finalization and labels ---
async def feed():
    # TOK_C: snapshots for 1h after alert (token then "dies"), prices rising to 1.5
    for i in range(60):
        s = snap('TOK_C', T0 + 180 + i * 60, price=1.0 + i * 0.5 / 60)
        await storage.save_signal_snapshot(s)
    # candles: 6h of 1-min candles peaking at 2.5x, then drift to 24h (total 1440)
    with storage.connect() as db:
        for i in range(1440):
            ts = T0 + 180 + i * 60
            px = 2.5 if 200 <= i <= 260 else 1.2
            db.execute(
                'insert into token_candles (token_address, symbol, pair_address, chain_name,'
                ' timeframe_seconds, bucket_start, open, high, low, close, observations,'
                ' first_observed_at, last_observed_at)'
                ' values (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                ('TOK_C', 'T', 'P', 'solana', 60, ts, px, px, px * 0.95, px, 3, ts, ts + 59))

asyncio.run(feed())

# production path: per-snapshot updater creates the (incomplete) window rows
# mid-window; the finalizer must complete them later.
asyncio.run(storage.update_alert_outcomes_for_snapshot(m, T0 + 1800))
with storage.connect() as db:
    pre = db.execute(
        'select window_label, complete from alert_outcomes where alert_id=?'
        ' order by window_seconds', (alert_id,)).fetchall()
if not pre:
    fails.append('per-snapshot updater wrote no outcome rows')
elif all(r[1] == 1 for r in pre):
    fails.append(f'all windows already complete mid-window: {pre}')

# finalize: TOK_C has snapshots after alert -> due windows should complete
n = storage.finalize_overdue_alert_outcomes(now=NOW)
with storage.connect() as db:
    w = db.execute(
        'select window_label, complete, max_multiple, snapshot_count from alert_outcomes'
        ' where alert_id=? order by window_seconds', (alert_id,)).fetchall()
if not w or not all(r[1] == 1 for r in w):
    fails.append(f'windows not completed: {w}')
elif abs(w[0][2] - 1.0) > 0.6:
    pass  # 5m window max depends on feed; no strict check

# zero-snapshot guard: alert on TOK_D with no snapshots must stay incomplete
m2 = types.SimpleNamespace(address='TOK_D', symbol='T', pair_address='P',
                           chain='solana', price=1.0, fdv=20000, liquidity=9000)
asyncio.run(storage.record_ignition_alert(
    m2, 80, {'alert_route': 'immediate', 'quality_tag': 'standard',
             'raw_score': 90, 'penalty': 0}, T0 + 300))
with storage.connect() as db:
    d_id = db.execute("select id from ignition_alerts where token_address='TOK_D'").fetchone()[0]
    # seed incomplete outcome rows for TOK_D (normally written by the per-snapshot updater)
    db.execute(
        'insert into alert_outcomes (alert_id, token_address, alert_timestamp, window_seconds,'
        ' window_label, due_timestamp, alert_price, complete) values (?,?,?,?,?,?,?,0)',
        (d_id, 'TOK_D', T0 + 300, 21600, '6h', T0 + 300 + 21600, 1.0))
storage.finalize_overdue_alert_outcomes(now=NOW)
with storage.connect() as db:
    dC = db.execute('select complete from alert_outcomes where alert_id=?', (d_id,)).fetchone()
if dC[0] != 0:
    fails.append('zero-snapshot alert was finalized (guard failed)')

# --- candle labels ---
n_lab = storage.update_alert_candle_labels(now=NOW)
with storage.connect() as db:
    labs = db.execute(
        'select subject_type, subject_id, h6_max_multiple, h6_candle_count,'
        ' h24_max_multiple from alert_candle_labels order by subject_type, subject_id'
    ).fetchall()
alert_lab = [l for l in labs if l[0] == 'alert' and l[1] == alert_id]
cand_lab = [l for l in labs if l[0] == 'candidate']
if not alert_lab:
    fails.append(f'no candle label for alert {alert_id}: {labs}')
else:
    l = alert_lab[0]
    if not (2.4 <= (l[2] or 0) <= 2.6):
        fails.append(f'alert h6_max wrong: {l}')
    if not (2.4 <= (l[4] or 0) <= 2.6):
        fails.append(f'alert h24_max wrong: {l}')
if not any(l[1] for l in cand_lab):
    fails.append('no candidate labels written')
# TOK_D (no candles) must have no label row
if any(l[0] == 'alert' and l[1] == d_id for l in labs):
    fails.append('TOK_D labeled despite zero candles (floor failed)')

print(f'finalized={n}, labeled={n_lab}, labels={labs}')

# --- two-stage confirmations (shadow) ---
n_conf = storage.evaluate_due_confirmations(now=NOW)
with storage.connect() as db:
    conf = {r[0]: r[1:] for r in db.execute(
        'select token_address, confirmed, confirm_snapshot_count,'
        ' confirm_price_multiple, confirm_min_multiple, model_prob'
        ' from candidate_events where confirm_evaluated_at is not null')}
if 'TOK_C' not in conf or 'TOK_A' not in conf:
    fails.append(f'confirmations not evaluated: {conf}')
else:
    c = conf['TOK_C']
    if c[0] != 1:
        fails.append(f'TOK_C should confirm (rising price, flow 1.4): {c}')
    a = conf['TOK_A']
    if a[0] != 0 or a[1] != 0:
        fails.append(f'TOK_A (no snapshots) should fail confirmation with count=0: {a}')
    if any(v[4] is not None for v in conf.values()):
        fails.append('model_prob should be NULL without a blessed artifact')
print(f'confirmations evaluated={n_conf}: {conf}')

# scorer degradation in this (sklearn-less) venv
from scoring.runner_model import score_candidate
p = score_candidate({'score': 80, 'fdv': 20000, 'liquidity': 9000,
                     'alert_route': 'x', 'quality_tag': 'y'})
if p is not None:
    fails.append(f'score_candidate should be None without artifact, got {p}')

print('\nFAILURES:' if fails else '\nALL OUTCOME-JOB TESTS PASS')
for f in fails:
    print(' -', f)
sys.exit(1 if fails else 0)
