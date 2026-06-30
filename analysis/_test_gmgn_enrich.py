"""Candidate-intel test: GMGN smart-money aggregate (real API) + OpenTwitter
gating + scratch-DB updates for both feature sets."""
import asyncio
import os
import sys
import tempfile
import time

REPO = os.getcwd()
sys.path.insert(0, REPO)

from sources.gmgn import gmgn_client  # noqa: E402
from sources.opentwitter import opentwitter_client  # noqa: E402

BONK = 'DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263'

print('gmgn enabled:', gmgn_client.enabled())
features = asyncio.run(gmgn_client.candidate_features(BONK))
assert features, 'no smart-money features returned'
print('smart-money:', {k: v for k, v in features.items() if k != 'raw'})
assert features.get('smart_count') is not None

t0 = time.time()
asyncio.run(gmgn_client.candidate_features(BONK))
assert time.time() - t0 < 0.05, 'cache miss on repeat call'
print('cache: OK')

# opentwitter must be cleanly inert without a token
print('opentwitter enabled (no token yet):', opentwitter_client.enabled())
assert not opentwitter_client.enabled() or os.environ.get('TWITTER_TOKEN')

# scratch DB updates for both feature families
tmp = tempfile.mkdtemp()
os.chdir(tmp)
from storage.sqlite import ScannerStorage  # noqa: E402

storage = ScannerStorage()
asyncio.run(storage.initialize())
snap = {
    'token_address': BONK, 'symbol': 'Bonk', 'chain_name': 'solana',
    'pair_address': 'P', 'timestamp': time.time(), 'price': 1.0,
    'fdv': 1, 'liquidity': 1, 'score': 80, 'raw_score': 100, 'penalty': 0,
    'pressure': 60, 'impulse': 1.1, 'volume_5m': 1, 'volume_1h': 1,
    'volume_liquidity_ratio': 1, 'buy_sell_ratio': 1,
    'h1_volume_liquidity_ratio': 1, 'h1_buy_sell_ratio': 1,
    'price_change_5m': 5, 'price_change_1h': 40, 'momentum_score': 1,
    'local_rsi': 50.0, 'alert_route': 'r', 'quality_tag': 'q',
    'lifecycle': 'l', 'risk_flags': '', 'alert_eligible': True,
}
assert asyncio.run(storage.record_candidate_event(snap))
assert asyncio.run(storage.update_candidate_gmgn(BONK, features))
fake_tw = {'mentions': 7, 'authors': 5, 'top_followers': 120000,
           'first_mention_ts': time.time() - 600, 'raw': '{}'}
assert asyncio.run(storage.update_candidate_twitter(BONK, fake_tw))
with storage.connect() as db:
    row = db.execute(
        'select gmgn_smart_money, gmgn_smart_share_pct, tw_mentions, '
        'tw_top_followers from candidate_events where token_address=?',
        (BONK,)).fetchone()
print('db row:', row)
assert row[0] is not None and row[2] == 7 and row[3] == 120000
print('\nCANDIDATE INTEL TEST PASS')
