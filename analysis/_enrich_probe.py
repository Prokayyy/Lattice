"""Probe signal_snapshots schema + indexes in hot and warm DBs (read-only)."""
import sys
sys.path.insert(0, '.')
from storage.history import open_history

con = open_history()

for schema in ("main", "archive"):
    print(f"=== {schema} ===")
    try:
        cols = [r[1] for r in con.execute(f"PRAGMA {schema}.table_info(signal_snapshots)").fetchall()]
    except Exception as e:
        print("  table_info failed:", e)
        continue
    print(f"  columns ({len(cols)}):", ", ".join(cols))
    idx = con.execute(f"PRAGMA {schema}.index_list(signal_snapshots)").fetchall()
    for row in idx:
        name = row[1]
        info = con.execute(f"PRAGMA {schema}.index_info({name})").fetchall()
        icols = [i[2] for i in info]
        print(f"  index {name} unique={row[2]} cols={icols}")

# timestamp range probes via indexed lookups (cheap if indexed on timestamp)
for schema in ("main", "archive"):
    try:
        lo = con.execute(f"SELECT timestamp FROM {schema}.signal_snapshots ORDER BY timestamp LIMIT 1").fetchone()
        hi = con.execute(f"SELECT timestamp FROM {schema}.signal_snapshots ORDER BY timestamp DESC LIMIT 1").fetchone()
        print(f"{schema}: ts range {lo[0] if lo else None} .. {hi[0] if hi else None}")
    except Exception as e:
        print(f"{schema}: range probe failed: {e}")

con.close()
