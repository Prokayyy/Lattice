"""Trade-level outcome decomposition: how much do exits censor runners?

Runner potential = peak multiple while held (and 24h candle max where available).
Loser = realized exit multiple <= 0.95 (lost money incl. fees/slippage).
"""
import json
from collections import defaultdict

trades = json.load(open('analysis/_trades_dataset.json'))


def peak(t):
    return t.get('peak_multiple_calc') or t.get('peak_multiple') or 0


def realized(t):
    return t.get('exit_multiple') or 0


n = len(trades)
print(f'trades: {n}')

# joint distribution: peak potential vs realized
print('\npeak_mult vs realized_mult (counts):')
print(f'{"peak bucket":<14}{"n":>5}{"real>=2x":>9}{"real 1.2-2":>11}{"real 1-1.2":>11}{"real<1":>8}{"med real":>9}{"capture":>9}')
edges = [(0, 1.2), (1.2, 1.5), (1.5, 2.0), (2.0, 3.0), (3.0, 99.0)]
for lo, hi in edges:
    grp = [t for t in trades if lo <= peak(t) < hi]
    if not grp:
        continue
    rs = sorted(realized(t) for t in grp)
    m = len(rs)
    cap = sum(realized(t) - 1 for t in grp) / max(sum(peak(t) - 1 for t in grp), 1e-9)
    print(f'{lo}-{hi:<11} {m:>5}'
          f'{sum(1 for r in rs if r >= 2):>9}'
          f'{sum(1 for r in rs if 1.2 <= r < 2):>11}'
          f'{sum(1 for r in rs if 1.0 <= r < 1.2):>11}'
          f'{sum(1 for r in rs if r < 1.0):>8}'
          f'{rs[m // 2]:>9.3f}{cap:>9.2f}')

# close_reason vs peak>=2 trades: where did runners get cut?
print('\nrunners by potential (peak>=2x): what was the realized result + close reason')
for t in sorted(trades, key=peak, reverse=True):
    if peak(t) >= 2.0:
        print(f"  {t.get('symbol', '?'):<12} peak={peak(t):>6.2f} realized={realized(t):>6.3f} "
              f"pnl=${t.get('pnl_usd') or 0:>7.2f} close={t.get('close_reason')} ledger={t['ledger'][:20]}")

# losers: realized < 0.95
losers = [t for t in trades if realized(t) and realized(t) < 0.95]
print(f'\nlosers (realized<0.95): {len(losers)} '
      f'({100 * len(losers) / n:.0f}%), total pnl ${sum(t.get("pnl_usd") or 0 for t in losers):.2f}')
by_reason = defaultdict(list)
for t in losers:
    by_reason[t.get('close_reason')].append(t)
for r, grp in sorted(by_reason.items(), key=lambda kv: -len(kv[1])):
    pk = sorted(peak(t) for t in grp)
    print(f'  {r:<28} n={len(grp):>3} median_peak={pk[len(pk) // 2]:.2f} '
          f'pnl=${sum(t.get("pnl_usd") or 0 for t in grp):>8.2f}')

# could entry features have excluded losers without excluding runners?
# compare runners (peak>=2) vs losers (realized<0.95 AND peak<1.5) on each feature
runners = [t for t in trades if peak(t) >= 2.0]
hard_losers = [t for t in trades if realized(t) and realized(t) < 0.95 and peak(t) < 1.3]
print(f'\nfeature medians: runners(n={len(runners)}) vs hard losers(n={len(hard_losers)})')
FEATS = ['entry_score', 'entry_impulse', 'entry_pressure', 'entry_liquidity', 'entry_fdv',
         'entry_volume_1h', 'entry_volume_multiple', 'entry_volume_liquidity_ratio',
         'entry_buy_sell_ratio', 'entry_confirmation_score']


def med(rows, f):
    vals = sorted(t[f] for t in rows if t.get(f) is not None)
    return vals[len(vals) // 2] if vals else None


print(f'{"feature":<32}{"runners":>12}{"hard_losers":>12}')
for f in FEATS:
    a, b = med(runners, f), med(hard_losers, f)
    fa = f'{a:.3g}' if a is not None else 'n/a'
    fb = f'{b:.3g}' if b is not None else 'n/a'
    print(f'{f:<32}{fa:>12}{fb:>12}')

# pnl by ledger period (regime drift check)
print('\nby ledger:')
for led in sorted(set(t['ledger'] for t in trades)):
    grp = [t for t in trades if t['ledger'] == led]
    wins = sum(1 for t in grp if (t.get('pnl_usd') or 0) > 0)
    print(f'  {led:<44} n={len(grp):>3} pnl=${sum(t.get("pnl_usd") or 0 for t in grp):>8.2f} '
          f'winrate={100 * wins / len(grp):.0f}% runners_peak={sum(1 for t in grp if peak(t) >= 2)}')
