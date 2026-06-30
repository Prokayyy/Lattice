"""Relay public-copy parsing must work for BOTH alert format generations."""
import sys

sys.path.insert(0, '.')
sys.path.insert(0, 'tools')

from tools.lattice_user_relay import (
    public_entry_message,
    should_relay,
)

NEW = """💎 [LATTICE] 🎯 ENTRY SIGNAL — $MERRY CAT
P(≥2x) 22%
🎯 zone  $0.0₄1331 → $0.0₄1398
🛑 stop  $0.0₅9317 (-30% from zone low)
🧷 🟠 bundle 12.3% held · 5 wallets · insiders 4.2%
📊 revival 0.70 · lattice 0.76 · breadth +0.21
      buyers -0.02 · top-holder conc 28%
🧠 1 smart wallet · 0.8% of supply · $102 · 1 in profit
🐦 5 mentions · search CA on X
🧷 cluster: 7Xab…9PqR 12.3% · 4Cde…1FgH 0.0%
📰 narrative: NAME MENTIONS | 6 news, top Maxx0l | News
⛔ not entered; insufficient paper cash 15.47<20.00
q8gxXxDi4NK6W4NAJkNjwn52DBUgFkK9MazVDMHpump"""

OLD = """💎 [LATTICE] 🎯 ENTRY SIGNAL
$MERRY CAT  conviction P(≥2x): 0.22
entry zone: 1.331e-05 – 1.398e-05
invalidation: 9.317e-06
revival 0.70 | lattice 0.76 | breadth +0.21
q8gxXxDi4NK6W4NAJkNjwn52DBUgFkK9MazVDMHpump"""

fails = []
for label, text in (('NEW', NEW), ('OLD', OLD)):
    if not should_relay(text):
        fails.append(f'{label}: should_relay False')
    msg = public_entry_message(text)
    print(f'--- {label} public copy ---')
    print(msg or '(EMPTY!)')
    print()
    if not msg:
        fails.append(f'{label}: public message empty')
        continue
    # NEW header carries the full symbol; the OLD line-anchored regex always
    # truncated at the first space (pre-existing behavior, format retired).
    want = 'MERRY CAT' if label == 'NEW' else 'MERRY'
    if want not in msg:
        fails.append(f'{label}: symbol missing')
    if 'q8gxXxDi' not in msg:
        fails.append(f'{label}: CA missing')
    if label == 'NEW':
        first_line = msg.splitlines()[0]
        if not all(part in first_line for part in ('Ticker:', 'Name:', 'CA:')):
            fails.append('NEW: first line is not bot-parseable ticker/name/CA')
        if '0.00001331' not in msg or '0.00001398' not in msg:
            fails.append('NEW: zone decimals not expanded')
        if '0.000009317' not in msg:
            fails.append('NEW: invalidation decimal not expanded')
        if '(' in msg.split('Invalidation:')[-1]:
            fails.append('NEW: stop annotation not stripped')
        if 'Smart wallets:' not in msg or '1 smart wallet' not in msg:
            fails.append('NEW: smart-wallet context missing')
        if 'search CA on X' not in msg or 'https://x.com/search' not in msg:
            fails.append('NEW: X search link missing')
        if 'Narrative:' not in msg or 'NAME MENTIONS' not in msg:
            fails.append('NEW: narrative context missing')
        if 'Bundle:' not in msg or 'bundle 12.3% held' not in msg:
            fails.append('NEW: bundle context missing')
        if 'cluster:' in msg:
            fails.append('NEW: cluster wallet line should not be relayed')

print('FAILURES:' if fails else 'RELAY PARSE TESTS PASS')
for f in fails:
    print(' -', f)
sys.exit(1 if fails else 0)
