"""Probe snapshot density per time window + alert timestamp distribution."""
import sys, json, time, datetime
sys.path.insert(0, '.')
from storage.history import open_history

con = open_history()

def iso(ts):
    return datetime.datetime.utcfromtimestamp(ts).strftime('%Y-%m-%d %H:%M')

print("hot   range:", iso(1780437593), "..", iso(1781044807))
print("warm  range:", iso(1779546856), "..", iso(1780437593))

alerts = json.load(open('analysis/_alerts_dataset.json'))
print("alerts:", len(alerts))
ts = sorted(a['alert_timestamp'] for a in alerts)
print("alert ts range:", iso(ts[0]), "..", iso(ts[-1]))
ARCHIVE_MIN = 1779546856.11
n_cov = sum(1 for t in ts if t + 30 >= ARCHIVE_MIN)
print(f"alerts with alert_ts+30 >= snapshot history start: {n_cov} / {len(ts)}")

# weekly histogram of alerts
from collections import Counter
wk = Counter(datetime.datetime.utcfromtimestamp(t).strftime('%G-W%V') for t in ts)
for k in sorted(wk):
    print(" ", k, wk[k])

# density probe: rows in a few 1830s windows (archive, timestamp-indexed)
t0 = time.time()
for probe_ts in (1779700000, 1780000000, 1780300000):
    n = con.execute(
        "SELECT COUNT(*) FROM archive.signal_snapshots WHERE timestamp BETWEEN ? AND ?",
        (probe_ts, probe_ts + 1830),
    ).fetchone()[0]
    print(f"archive rows in [{iso(probe_ts)} +30.5min]: {n}")
print("density probes took %.2fs" % (time.time() - t0))

# hot per-token probe speed
t0 = time.time()
sample = [a for a in alerts if a['alert_timestamp'] >= 1780437593][:20]
tot = 0
for a in sample:
    n = con.execute(
        "SELECT COUNT(*) FROM main.signal_snapshots WHERE token_address=? AND timestamp BETWEEN ? AND ?",
        (a['token_address'], a['alert_timestamp'] - 1800, a['alert_timestamp'] + 30),
    ).fetchone()[0]
    tot += n
print(f"hot probe: 20 alerts, {tot} rows, %.2fs" % (time.time() - t0))
con.close()
