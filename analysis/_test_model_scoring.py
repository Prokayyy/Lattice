"""Serve-path test for the shadow model scorer (run with ~/ml-venv/bin/python).

1. Build a --force artifact (not blessed) -> score_candidate must return None.
2. Flip blessed=True on a copy -> score_candidate must return a probability.
3. Parity: trainer's featurize and serve vector produce identical columns.
"""
import os
import pickle
import subprocess
import sys
import tempfile

sys.path.insert(0, '.')
from scoring.runner_model import score_candidate, vector  # noqa: E402

fails = []

# train a forced artifact against the real DB (246 rows, will not pass the bar)
r = subprocess.run(
    [sys.executable, 'tools/train_runner_model.py', '--min-rows', '100', '--force'],
    capture_output=True, text=True)
print(r.stdout.strip().splitlines()[-2:])
if not os.path.exists('models/runner_model.pkl'):
    fails.append('forced artifact not written')
else:
    art = pickle.load(open('models/runner_model.pkl', 'rb'))
    if art.get('blessed'):
        fails.append('forced artifact must not be blessed')

    row = {
        'score': 80, 'raw_score': 100, 'penalty': 5, 'pressure': 60,
        'impulse': 1.1, 'fdv': 20000, 'liquidity': 9000, 'volume_5m': 3000,
        'volume_1h': 25000, 'volume_liquidity_ratio': 1.0,
        'buy_sell_ratio': 1.4, 'h1_volume_liquidity_ratio': 2.5,
        'h1_buy_sell_ratio': 1.2, 'price_change_5m': 5,
        'price_change_1h': 40, 'momentum_score': 3, 'local_rsi': 60.0,
        'token_age_seconds': 7200, 'breadth_eligible_30m': 4,
        'gmgn_smart_money': 3, 'gmgn_smart_share_pct': 18.5,
        'gmgn_smart_usd': 2500.0, 'gmgn_smart_profit_n': 2,
        'gmgn_smart_fresh_n': 1, 'gmgn_smart_suspicious_n': 0,
        'alert_route': 'bonding_early_revival', 'quality_tag': 'early_revival',
    }

    # not blessed -> None
    p = score_candidate(row, model_path='models/runner_model.pkl')
    if p is not None:
        fails.append(f'unblessed artifact served a probability: {p}')

    # blessed copy -> probability
    art['blessed'] = True
    bp = os.path.join(tempfile.mkdtemp(), 'blessed.pkl')
    pickle.dump(art, open(bp, 'wb'))
    p2 = score_candidate(row, model_path=bp)
    if p2 is None or not (0.0 <= p2 <= 1.0):
        fails.append(f'blessed artifact did not serve: {p2}')
    else:
        print(f'blessed-copy probability: {p2:.3f}')

    # vector length parity with artifact feature names
    v, names = vector(row, art['encoders']['routes'], art['encoders']['qtags'])
    if names != art['feature_names']:
        fails.append('feature name order mismatch between trainer and server')
    if len(v) != len(art['encoders']['median']):
        fails.append('vector length != artifact median length')
    for smart_name in ('gmgn_smart_money', 'log_gmgn_smart_usd',
                       'm_gmgn_smart_suspicious_n'):
        if smart_name not in names:
            fails.append(f'missing smart-wallet feature: {smart_name}')

    # cleanup the forced artifact so the scanner never sees it
    os.remove('models/runner_model.pkl')

print('\nFAILURES:' if fails else '\nMODEL SCORING TESTS PASS')
for f in fails:
    print(' -', f)
sys.exit(1 if fails else 0)
