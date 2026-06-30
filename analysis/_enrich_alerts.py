"""Enrich analysis/_alerts_dataset.json with scan-time features from signal_snapshots.

Read-only over hot (scanner.db) + warm (scanner_archive.db) via storage.history.open_history().
Stdlib only — run with env/bin/python.

Strategy notes (from PRAGMA probes, also re-checked at runtime below):
  - main.signal_snapshots has idx_signal_snapshots_token_time (token_address, timestamp)
  - archive.signal_snapshots has ONLY a timestamp index (idx_archive_signal_snapshots_timestamp)
    plus a unique id index — NO token index. Per-token queries against the archive would
    full-scan, so instead of batching by token we batch by TIME: merge the per-alert
    query windows [alert_ts-1800, alert_ts+30] into disjoint intervals and run one
    timestamp-indexed query per interval with a token_address IN (...) filter.
  - All alert-era snapshots live in the archive (hot starts 2026-06-02, alerts end
    2026-05-31). Archive starts 2026-05-23, so alerts before that cannot be enriched.

Output: analysis/_alerts_dataset_enriched.json — same rows + 'snap' and 'traj'
sub-objects (null when no snapshot data is available).
"""

import json
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, '.')
from storage.history import open_history  # noqa: E402

ALERTS_PATH = Path('analysis/_alerts_dataset.json')
OUT_PATH = Path('analysis/_alerts_dataset_enriched.json')

SNAP_LOOKBACK = 900     # snap: latest snapshot in [alert_ts-900, alert_ts+30]
SNAP_LOOKAHEAD = 30
TRAJ_LOOKBACK = 1800    # traj: snapshots in [alert_ts-1800, alert_ts] (pre-alert only)

SNAP_COLS = [
    'lifecycle', 'volume_5m', 'volume_1h', 'buys_5m', 'sells_5m', 'buys_1h',
    'sells_1h', 'price_change_5m', 'price_change_1h', 'price_change_6h',
    'pressure', 'impulse', 'volume_liquidity_ratio', 'buy_sell_ratio',
    'h1_volume_liquidity_ratio', 'h1_buy_sell_ratio', 'momentum_score',
    'local_rsi', 'local_rsi_ema', 'local_rsi_bullish', 'local_rsi_entry_ok',
    'local_rsi_candle_count', 'migration_distance_pct', 'fdv', 'liquidity',
    'risk_flags', 'alert_eligible', 'experimental_features',
]
# extra columns needed for trajectory features
QUERY_COLS = ['token_address', 'timestamp', 'price'] + SNAP_COLS


def report_indexes(con):
    print('--- signal_snapshots indexes ---')
    for schema in ('main', 'archive'):
        try:
            rows = con.execute(
                f'PRAGMA {schema}.index_list(signal_snapshots)').fetchall()
        except Exception as exc:  # archive not attached etc.
            print(f'  {schema}: unavailable ({exc})')
            continue
        for r in rows:
            cols = [i[2] for i in con.execute(
                f'PRAGMA {schema}.index_info({r[1]})').fetchall()]
            print(f'  {schema}.{r[1]} unique={r[2]} cols={cols}')


def merge_windows(spans, gap=0.0):
    """Merge overlapping [lo, hi] spans (sorted by lo) into disjoint intervals."""
    merged = []
    for lo, hi in sorted(spans):
        if merged and lo <= merged[-1][1] + gap:
            merged[-1][1] = max(merged[-1][1], hi)
        else:
            merged.append([lo, hi])
    return merged


def flatten_xf(obj, prefix='xf_'):
    """Flatten numeric values from a (possibly nested) experimental_features dict."""
    out = {}
    if not isinstance(obj, dict):
        return out
    for key, val in obj.items():
        name = f'{prefix}{key}'
        if isinstance(val, bool):
            out[name] = int(val)
        elif isinstance(val, (int, float)):
            out[name] = val
        elif isinstance(val, dict):
            out.update(flatten_xf(val, prefix=name + '_'))
        # strings / lists / nulls: skipped
    return out


def build_snap(row):
    snap = {col: row[col] for col in SNAP_COLS}
    snap['snapshot_timestamp'] = row['timestamp']
    raw_xf = snap.pop('experimental_features')
    snap['experimental_features_raw_present'] = bool(raw_xf)
    if raw_xf:
        try:
            snap.update(flatten_xf(json.loads(raw_xf)))
        except (ValueError, TypeError):
            snap['xf_parse_error'] = 1
    return snap


def ratio_slope(first, last):
    if first is None or last is None or first <= 0:
        return None
    return last / first - 1.0


def build_traj(rows, alert_ts):
    """Pre-alert trajectory from snapshots in [alert_ts-1800, alert_ts]."""
    pts = [r for r in rows if alert_ts - TRAJ_LOOKBACK <= r['timestamp'] <= alert_ts]
    if not pts:
        return None
    first, last = pts[0], pts[-1]
    vols = [r['volume_5m'] for r in pts if r['volume_5m'] is not None]
    p_first, p_last = first['pressure'], last['pressure']
    return {
        'traj_count': len(pts),
        'traj_span_seconds': last['timestamp'] - first['timestamp'],
        'traj_price_slope': ratio_slope(first['price'], last['price']),
        'traj_liquidity_slope': ratio_slope(first['liquidity'], last['liquidity']),
        'traj_pressure_slope': ratio_slope(p_first, p_last),
        'traj_pressure_delta': (
            p_last - p_first
            if p_first is not None and p_last is not None else None),
        'traj_max_volume_5m': max(vols) if vols else None,
    }


def main():
    t_start = time.time()
    alerts = json.loads(ALERTS_PATH.read_text())
    print(f'alerts: {len(alerts)}')

    con = open_history()  # read-only, hot + warm attached
    report_indexes(con)

    # Per-alert needed window and token routing
    spans = []
    by_token = defaultdict(list)  # token -> list of alert dicts
    for alert in alerts:
        ts = alert['alert_timestamp']
        spans.append((ts - TRAJ_LOOKBACK, ts + SNAP_LOOKAHEAD))
        by_token[alert['token_address']].append(alert)

    intervals = merge_windows(spans)
    print(f'merged query intervals: {len(intervals)} '
          f'(total span {sum(h - l for l, h in intervals) / 3600.0:.1f} h)')

    # token -> interval membership, so each query only pulls relevant tokens
    interval_tokens = defaultdict(set)
    for alert in alerts:
        ts = alert['alert_timestamp']
        lo = ts - TRAJ_LOOKBACK
        for i, (ilo, ihi) in enumerate(intervals):
            if ilo <= lo <= ihi:
                interval_tokens[i].add(alert['token_address'])
                break

    col_sql = ', '.join(QUERY_COLS)
    rows_by_token = defaultdict(list)
    n_rows = 0
    for i, (lo, hi) in enumerate(intervals):
        tokens = sorted(interval_tokens[i])
        if not tokens:
            continue
        ph = ', '.join('?' for _ in tokens)
        cur = con.execute(
            f'SELECT {col_sql} FROM signal_snapshots_all '
            f'WHERE timestamp >= ? AND timestamp <= ? '
            f'AND token_address IN ({ph})',
            [lo, hi] + tokens,
        )
        for row in cur:
            rows_by_token[row['token_address']].append(row)
            n_rows += 1
    print(f'fetched {n_rows} snapshot rows for {len(rows_by_token)} tokens '
          f'in {time.time() - t_start:.1f}s')

    for rows in rows_by_token.values():
        rows.sort(key=lambda r: r['timestamp'])

    # Enrich
    n_snap = n_traj = 0
    week_total = Counter()
    week_snap = Counter()
    for alert in alerts:
        ts = alert['alert_timestamp']
        week = datetime.fromtimestamp(ts, timezone.utc).strftime('%G-W%V')
        week_total[week] += 1

        tok_rows = rows_by_token.get(alert['token_address'], [])
        window_rows = [
            r for r in tok_rows
            if ts - TRAJ_LOOKBACK <= r['timestamp'] <= ts + SNAP_LOOKAHEAD
        ]

        snap_rows = [
            r for r in window_rows
            if ts - SNAP_LOOKBACK <= r['timestamp'] <= ts + SNAP_LOOKAHEAD
        ]
        if snap_rows:
            alert['snap'] = build_snap(snap_rows[-1])  # latest in window
            n_snap += 1
            week_snap[week] += 1
        else:
            alert['snap'] = None

        traj = build_traj(window_rows, ts)
        alert['traj'] = traj
        if traj:
            n_traj += 1

    OUT_PATH.write_text(json.dumps(alerts, indent=1))
    print(f'\nwrote {OUT_PATH} ({OUT_PATH.stat().st_size / 1e6:.1f} MB)')

    print('\n--- coverage ---')
    print(f'snap: {n_snap}/{len(alerts)} '
          f'({100.0 * n_snap / len(alerts):.1f}%)   traj: {n_traj}/{len(alerts)}')
    print('by ISO week:')
    for week in sorted(week_total):
        tot, got = week_total[week], week_snap[week]
        print(f'  {week}: {got}/{tot} ({100.0 * got / tot:.1f}%)')

    # snap recency: how stale is the chosen snapshot vs alert time?
    lags = [a['alert_timestamp'] - a['snap']['snapshot_timestamp']
            for a in alerts if a['snap']]
    if lags:
        lags.sort()
        print(f'\nsnap lag (alert_ts - snapshot_ts) seconds: '
              f'min={lags[0]:.1f} median={lags[len(lags) // 2]:.1f} '
              f'p90={lags[int(len(lags) * 0.9)]:.1f} max={lags[-1]:.1f}')
        n_future = sum(1 for l in lags if l < 0)
        print(f'snapshots taken after alert (within +30s grace): {n_future}')

    xf_counter = Counter()
    for a in alerts:
        if a['snap']:
            xf_counter.update(k for k in a['snap'] if k.startswith('xf_'))
    print(f'\nxf_ keys seen: {dict(xf_counter) if xf_counter else "none"}')

    print(f'\ntotal runtime {time.time() - t_start:.1f}s')
    con.close()


if __name__ == '__main__':
    main()
