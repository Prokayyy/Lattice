"""Build the materialised 20d+ snapshot cache for chandelier_sweep.py, FAST + crash-safe.

Direct-attach copy (hot + archive) instead of the slow signal_snapshots_all UNION
view; restricted to a trailing window; atomic temp->rename so a kill never leaves a
corrupt file at the canonical path. Phase timing printed so the bottleneck is visible.

Run:  env/bin/python analysis/_build_snap_cache.py --keep-days 21
"""
import argparse, os, sqlite3, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
import sys
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from storage.history import archive_path_for  # noqa: E402

CACHE_DIR = ROOT / "analysis" / ".cache"
SNAP_DB = CACHE_DIR / "chandelier_snaps_full.db"
HOT = ROOT / "scanner.db"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep-days", type=float, default=21.0)
    ap.add_argument("--token-index", action="store_true",
                    help="also build the (token_address,timestamp) index (slow)")
    args = ap.parse_args()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = SNAP_DB.with_suffix(".building")
    for p in (tmp, tmp.with_suffix(".building-journal"), tmp.with_suffix(".building-wal")):
        if p.exists():
            p.unlink()

    arc = archive_path_for(str(HOT))
    t_all = time.time()

    con = sqlite3.connect(str(tmp))
    con.execute("PRAGMA journal_mode=OFF")
    con.execute("PRAGMA synchronous=OFF")
    # This box has only ~3.8GB RAM and the live scanner is running -- a big page
    # cache / mmap THRASHES and makes the copy CPU-bound. Keep memory small so the
    # copy STREAMS to disk; let the index sort spill to a temp file, not RAM.
    con.execute("PRAGMA temp_store=FILE")
    con.execute("PRAGMA cache_size=-65536")           # ~64MB page cache
    con.execute("PRAGMA mmap_size=0")
    # NB: plain mode=ro, NOT immutable=1. The hot scanner.db is being actively
    # written by the live scanner; immutable=1 makes SQLite skip WAL/locking and
    # read TORN pages -> "database disk image is malformed" + pathological CPU
    # thrash. mode=ro takes a proper consistent WAL read-snapshot.
    con.execute(f"ATTACH DATABASE 'file:{HOT}?mode=ro' AS hot")
    has_arc = arc.exists()
    if has_arc:
        con.execute(f"ATTACH DATABASE 'file:{arc}?mode=ro' AS arc")

    # window cutoff from the newest hot snapshot
    now = con.execute("SELECT MAX(timestamp) FROM hot.signal_snapshots").fetchone()[0]
    cutoff = now - args.keep_days * 86400
    cols = [r[1] for r in con.execute("PRAGMA hot.table_info(signal_snapshots)")]
    collist = ", ".join('"' + c + '"' for c in cols)
    print(f"[build] keep_days={args.keep_days} cutoff={cutoff:.0f} cols={len(cols)} "
          f"archive={'yes' if has_arc else 'NO'}", flush=True)

    t0 = time.time()
    con.execute(
        f"CREATE TABLE signal_snapshots AS "
        f"SELECT {collist} FROM hot.signal_snapshots "
        f"WHERE price>0 AND timestamp>=?", (cutoff,))
    n_hot = con.execute("SELECT COUNT(*) FROM signal_snapshots").fetchone()[0]
    print(f"[build] hot copy: {n_hot:,} rows in {time.time()-t0:.1f}s", flush=True)

    if has_arc:
        t0 = time.time()
        con.execute(
            f"INSERT INTO signal_snapshots ({collist}) "
            f"SELECT {collist} FROM arc.signal_snapshots "
            f"WHERE price>0 AND timestamp>=?", (cutoff,))
        n_tot = con.execute("SELECT COUNT(*) FROM signal_snapshots").fetchone()[0]
        print(f"[build] archive copy: +{n_tot-n_hot:,} rows "
              f"(total {n_tot:,}) in {time.time()-t0:.1f}s", flush=True)

    t0 = time.time()
    con.execute("CREATE INDEX idx_ss_ts ON signal_snapshots(timestamp)")
    print(f"[build] ts index in {time.time()-t0:.1f}s", flush=True)
    if args.token_index:
        t0 = time.time()
        con.execute(
            "CREATE INDEX idx_ss_tok ON signal_snapshots(token_address, timestamp)")
        print(f"[build] token index in {time.time()-t0:.1f}s", flush=True)
    else:
        print("[build] token index SKIPPED (slow string sort; end re-query "
              "full-scans instead -- cheap for a handful of open positions)",
              flush=True)

    n, mn, mx = con.execute(
        "SELECT COUNT(*), MIN(timestamp), MAX(timestamp) FROM signal_snapshots"
    ).fetchone()
    con.close()       # journal_mode=OFF -> no WAL to checkpoint; just close cleanly
    os.replace(tmp, SNAP_DB)      # atomic: canonical path only ever sees a complete DB
    span = (mx - mn) / 86400.0
    print(f"[build] DONE n={n:,} span={span:.1f}d size={SNAP_DB.stat().st_size/1e9:.2f}GB "
          f"total {time.time()-t_all:.1f}s -> {SNAP_DB}", flush=True)


if __name__ == "__main__":
    main()
