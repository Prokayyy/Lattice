"""Does the lattice book's conviction (or anything else) rank outcomes?

Reads discovery/trades.jsonl (closed lattice trades) + live_state.json.
Answers: (a) signal->entry conversion, (b) conviction vs PnL/peak,
(c) what's actually binding entries (cash vs gates).
"""
import json
import time
from collections import defaultdict
from pathlib import Path

trades = []
p = Path('discovery/trades.jsonl')
if p.exists():
    for line in p.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                trades.append(json.loads(line))
            except json.JSONDecodeError:
                pass

print(f'closed lattice trades: {len(trades)}')
if trades:
    pnls = [float(t.get('pnl_usd') or 0) for t in trades]
    wins = sum(1 for v in pnls if v > 0)
    print(f'total PnL ${sum(pnls):+.2f} | win rate {wins}/{len(trades)} '
          f'({100*wins/len(trades):.0f}%)')

    # recency: last 7 days vs before
    now = time.time()
    recent = [t for t in trades if float(t.get('exit_ts') or 0) > now - 7 * 86400]
    rp = [float(t.get('pnl_usd') or 0) for t in recent]
    if recent:
        print(f'last 7d: {len(recent)} trades, ${sum(rp):+.2f}, '
              f'win rate {100*sum(1 for v in rp if v>0)/len(rp):.0f}%')

    # conviction buckets vs outcome
    print('\nconviction vs outcome (closed trades):')
    print(f'{"conv bucket":<14}{"n":>4}{"win%":>6}{"avg pnl":>9}{"med peak":>9}{">=1.5x peak":>12}')
    buckets = [(0, 0.20), (0.20, 0.25), (0.25, 0.30), (0.30, 0.40), (0.40, 1.01)]
    for lo, hi in buckets:
        grp = [t for t in trades if lo <= float(t.get('conviction') or 0) < hi]
        if not grp:
            continue
        gp = [float(t.get('pnl_usd') or 0) for t in grp]
        peaks = sorted(float(t.get('peak_mult') or 0) for t in grp)
        big = sum(1 for v in peaks if v >= 1.5)
        print(f'{lo:.2f}-{hi:.2f}    {len(grp):>4}'
              f'{100*sum(1 for v in gp if v>0)/len(grp):>6.0f}'
              f'{sum(gp)/len(grp):>9.2f}{peaks[len(peaks)//2]:>9.2f}'
              f'{100*big/len(grp):>11.0f}%')

    # exit reasons
    print('\nby exit reason:')
    by_r = defaultdict(list)
    for t in trades:
        by_r[str(t.get('reason') or '?')].append(float(t.get('pnl_usd') or 0))
    for r, vals in sorted(by_r.items(), key=lambda kv: sum(kv[1])):
        print(f'  {r:<28} n={len(vals):>3} pnl=${sum(vals):>8.2f}')

# live state: what's binding
ls = Path('discovery/live_state.json')
if ls.exists():
    state = json.loads(ls.read_text())
    open_pos = state.get('open_pos') or {}
    cash = float(state.get('cash') or 0)
    print(f'\nopen positions: {len(open_pos)} | paper cash ${cash:.2f}')
    convs = sorted(float(p.get('conviction') or 0) for p in open_pos.values())
    if convs:
        print(f'open conviction range: {convs[0]:.2f}-{convs[-1]:.2f} '
              f'median {convs[len(convs)//2]:.2f}')
