"""For trades with candle coverage: compare in-hold peak vs 24h-from-entry max.

If candle_max_24h >> in-hold peak, exits are cutting winners before the run;
if similar, losses are an entry-selection problem, not an exit problem.
"""
import json

trades = json.load(open('analysis/_trades_dataset.json'))
rows = [t for t in trades if t.get('candle_max_24h_multiple') and t.get('candle_count_24h', 0) >= 12]
print(f'trades with >=12 candles in 24h after entry: {len(rows)}')


def peak(t):
    return t.get('peak_multiple_calc') or t.get('peak_multiple') or 0


would_run = [t for t in rows if t['candle_max_24h_multiple'] >= 2.0]
held_run = [t for t in rows if peak(t) >= 2.0]
print(f'24h-max >=2x (token ran within 24h of entry): {len(would_run)} '
      f'({100*len(would_run)/len(rows):.0f}%)')
print(f'in-hold peak >=2x (we were still in when it ran): {len(held_run)}')

missed = [t for t in rows if t['candle_max_24h_multiple'] >= 2.0 and peak(t) < 1.5]
print(f'\nMISSED RUNNERS (token did >=2x in 24h but we exited below 1.5x peak): {len(missed)}')
for t in sorted(missed, key=lambda x: -x['candle_max_24h_multiple']):
    print(f"  {t.get('symbol','?'):<12} 24h_max={t['candle_max_24h_multiple']:>6.2f}x "
          f"in_hold_peak={peak(t):>5.2f}x realized={t.get('exit_multiple') or 0:>6.3f} "
          f"close={t.get('close_reason'):<28} pnl=${t.get('pnl_usd') or 0:>7.2f}")

# aggregate: of all 24h>=2x tokens, how many did exits capture >=1.5x realized?
if would_run:
    cap = [t for t in would_run if (t.get('exit_multiple') or 0) >= 1.5]
    print(f'\nof {len(would_run)} tokens that ran >=2x within 24h, realized >=1.5x on: {len(cap)}')
    med = sorted((t.get('exit_multiple') or 0) for t in would_run)
    print(f'median realized on those: {med[len(med)//2]:.3f}')
