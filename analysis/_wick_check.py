"""Are the 24h post-entry highs real, sustained moves or single-bucket wicks?

For each missed runner: count buckets where close (not just high) >= 1.5x entry,
and >= 2x entry, plus minutes spent above those levels and liquidity at peak.
"""
import json
import sqlite3

trades = json.load(open('analysis/_trades_dataset.json'))
rows = [t for t in trades
        if t.get('candle_max_24h_multiple') and t.get('candle_count_24h', 0) >= 12
        and t['candle_max_24h_multiple'] >= 2.0]

con = sqlite3.connect('file:scanner.db?mode=ro', uri=True)
print(f'{"symbol":<12}{"24h_max":>8}{"tf(s)":>6}{"n_cnd":>6}{"cl>=1.5x":>9}{"cl>=2x":>8}{"min>=1.5x":>10}{"liq@peak":>10}')
sustained = 0
for t in sorted(rows, key=lambda x: -x['candle_max_24h_multiple']):
    ep, ea = t['entry_price'], t['entry_at']
    cs = con.execute(
        'select timeframe_seconds, bucket_start, high, close, liquidity from token_candles '
        'where token_address=? and bucket_start>=? and bucket_start<=? order by bucket_start',
        (t['address'], ea, ea + 24 * 3600)).fetchall()
    if not cs:
        continue
    tf = cs[0][0]
    n15 = sum(1 for c in cs if c[3] and c[3] / ep >= 1.5)
    n20 = sum(1 for c in cs if c[3] and c[3] / ep >= 2.0)
    mins15 = n15 * tf / 60
    peak_c = max(cs, key=lambda c: c[2] or 0)
    liq = peak_c[4]
    if n15 >= 3:
        sustained += 1
    print(f"{(t.get('symbol') or '?'):<12}{t['candle_max_24h_multiple']:>8.2f}{tf:>6}{len(cs):>6}"
          f"{n15:>9}{n20:>8}{mins15:>10.0f}{(liq or 0):>10.0f}")
print(f'\nsustained (>=3 closes above 1.5x): {sustained}/{len(rows)}')
