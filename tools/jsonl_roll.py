#!/usr/bin/env python3
"""Data-aware roller for the append-only discovery JSONL logs.

These files (participation_log, discovery_outcomes, ai_shadow_decisions,
trade_policy_log, ai_advice) are DATA the bot reads back, so they are excluded
from logrotate — copytruncate would silently drop rows the live runner / outcome
jobs depend on. This rolls them instead: keep a generous recent window in the
live file, move OLDER lines into a compressed sibling archive. Nothing is ever
deleted; old lines only change location (live -> .archive.jsonl.gz), exactly like
the DB hot/warm/cold tiers (see ops/STORAGE.md).

WHAT MAKES IT SAFE (this is a LIVE TRADING system):

  * Generous window. Default 30 days, keyed off the timestamp field actually
    present in each record (inspected, not assumed — see discovery/jsonl_archive
    TARGETS). The only daily consumer, discovery.outcomes (06:00), records a
    participation entry within ~1 day of its ts and dedups, so a 30-day window
    can never strip a row it has not processed. discovery_outcomes is dated by
    max(recorded_at, alert_ts), so an outcome is never archived before the
    participation entry that produced it.

  * Never deletes. Old lines are gzip-appended to the archive (gzip members
    concatenate) BEFORE the live file is shrunk. A crash between the two leaves
    the lines in BOTH places (a harmless duplicate the ts-watermark removes next
    run), never in NEITHER.

  * Crash-safe + idempotent. Archive append and live shrink each go through a
    temp file + fsync + atomic os.replace, and a watermark in <file>.roll_state
    .json repairs a torn trailing member and prevents re-archiving on rerun.
    Running it twice in a row is a no-op the second time.

  * Concurrent-append safe. The live writers (live_runner, ai_advisor) reopen
    the file O_APPEND per line, so an atomic rename plus a held-fd drain of the
    old inode preserves any line appended mid-roll. The one writer that holds a
    handle across a loop (discovery.outcomes) is a scheduled job; a quiet-guard
    skips its file if it was touched in the last few minutes, and the cron slot
    is chosen clear of it.

Readers get the full logical stream (archive members + live) via
discovery.jsonl_archive.iter_records; the analysis tools that train on full
history already use it.

Usage (run via the repo venv):
    env/bin/python tools/jsonl_roll.py              # roll all targets (cron)
    env/bin/python tools/jsonl_roll.py --dry-run    # report, write nothing
    env/bin/python tools/jsonl_roll.py --status     # live/archive sizes + watermarks
    env/bin/python tools/jsonl_roll.py --selftest   # synthetic safety tests, no real files
    env/bin/python tools/jsonl_roll.py --window-days 7   # override the keep window
"""
import argparse
import gzip
import json
import os
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from discovery.jsonl_archive import (  # noqa: E402
    TARGETS,
    archive_path_for,
    live_path,
    record_recency,
    state_path_for,
)

QUIET_SECONDS = 180        # skip a "cron"-writer file touched this recently
DRAIN_TRIES = 6            # passes draining appends off the old inode post-swap
DRAIN_SETTLE_S = 0.05      # settle between drain passes


def _log(msg):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] jsonl_roll: {msg}", flush=True)


# --------------------------------------------------------------------------- #
# durable primitives                                                          #
# --------------------------------------------------------------------------- #
def _fsync_dir(path):
    """Persist a rename/create in the containing directory. Best-effort."""
    try:
        fd = os.open(os.path.dirname(path) or ".", os.O_DIRECTORY)
    except (OSError, AttributeError):
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _atomic_write_bytes(path, data):
    """Write data to path atomically: temp + fsync + os.replace + fsync dir."""
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    _fsync_dir(path)


def _load_state(live):
    try:
        with open(state_path_for(live), "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(live, state):
    _atomic_write_bytes(
        state_path_for(live),
        (json.dumps(state, separators=(",", ":")) + "\n").encode("utf-8"),
    )


# --------------------------------------------------------------------------- #
# line scan                                                                   #
# --------------------------------------------------------------------------- #
class _Line:
    __slots__ = ("start", "end", "recency")

    def __init__(self, start, end, recency):
        self.start = start
        self.end = end
        self.recency = recency


def _scan(data, ts_fields):
    """Split data strictly on b'\\n' (jsonl is newline-delimited; json.dumps
    escapes any embedded newline), tracking byte offsets and each record's
    recency = max(present ts_fields). recency is None for a blank, non-JSON, or
    partially-written line — which the splitter then treats as a hard KEEP
    boundary, so such a line is never archived."""
    lines = []
    n = len(data)
    start = 0
    while start < n:
        nl = data.find(b"\n", start)
        end = n if nl == -1 else nl + 1
        text = data[start:end].strip()
        recency = None
        if text:
            try:
                obj = json.loads(text)
                if isinstance(obj, dict):
                    recency = record_recency(obj, ts_fields)
            except (json.JSONDecodeError, UnicodeDecodeError):
                recency = None
        lines.append(_Line(start, end, recency))
        start = end
    return lines


def _split_index(lines, cutoff, ts_mode, fallback_keep_lines):
    """Index of the first line to KEEP. lines[:i] are the contiguous old prefix
    to archive; lines[i:] stay. In ts mode we stop at the first line that is
    recent OR undatable; in fallback mode we keep the last N lines."""
    if ts_mode:
        for i, ln in enumerate(lines):
            if ln.recency is None or ln.recency >= cutoff:
                return i
        return len(lines)
    return max(0, len(lines) - fallback_keep_lines)


# --------------------------------------------------------------------------- #
# archive (append a gzip member, crash-safe via the size watermark)           #
# --------------------------------------------------------------------------- #
def _repair_archive(archive, committed_bytes):
    """Roll back a torn/uncommitted trailing member left by a crash mid-append.
    Returns the trusted on-disk size after repair."""
    if not os.path.exists(archive):
        return 0
    actual = os.path.getsize(archive)
    if committed_bytes is None or committed_bytes < 0 or committed_bytes > actual:
        # State missing or impossible -> trust the file as-is (don't truncate a
        # good archive); the caller recovers the ts watermark by scanning it.
        return actual
    if actual > committed_bytes:
        with open(archive, "r+b") as f:
            f.truncate(committed_bytes)
            f.flush()
            os.fsync(f.fileno())
        _fsync_dir(archive)
    return committed_bytes


def _recover_through_ts(archive, ts_fields):
    """Max recency across an existing archive — used only when the state file is
    missing, so we don't re-archive lines the archive already holds."""
    best = None
    try:
        with gzip.open(archive, "rt", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict):
                    r = record_recency(obj, ts_fields)
                    if r is not None and (best is None or r > best):
                        best = r
    except (OSError, EOFError, gzip.BadGzipFile):
        pass
    return best


def _append_member(archive, member_bytes, committed_bytes):
    """Append one gzip member (deterministic, mtime=0) to the archive and return
    the new committed size. The member is fsync'd onto the end; the caller then
    records the new size in state, after which it is the committed watermark."""
    with open(archive, "ab") as f:
        f.write(member_bytes)
        f.flush()
        os.fsync(f.fileno())
    _fsync_dir(archive)
    return committed_bytes + len(member_bytes)


# --------------------------------------------------------------------------- #
# live shrink (atomic rename + drain the old inode for concurrent appends)     #
# --------------------------------------------------------------------------- #
def _shrink_live(live, data, cut_byte, eof0, keep_fd):
    """Replace the live file with bytes[cut_byte:] then re-attach anything a
    reopen-per-append writer wrote to the (now unlinked) old inode during the
    swap. keep_fd is the still-open read handle on the original inode."""
    _atomic_write_bytes(live, data[cut_byte:eof0])
    pos = eof0
    drained = 0
    for attempt in range(DRAIN_TRIES):
        keep_fd.seek(pos)
        extra = keep_fd.read()
        if extra:
            with open(live, "ab") as f:
                f.write(extra)
                f.flush()
                os.fsync(f.fileno())
            pos += len(extra)
            drained += len(extra)
        elif attempt > 0:
            break  # a clean empty pass after a settle => no writer in flight
        time.sleep(DRAIN_SETTLE_S)
    return eof0 - cut_byte, drained


# --------------------------------------------------------------------------- #
# roll one file                                                               #
# --------------------------------------------------------------------------- #
def roll_one(live, ts_fields, keep_days, writer="reopen", *, now,
             fallback_keep_lines=200_000, window_days=None, dry_run=False,
             quiet_seconds=QUIET_SECONDS, log=_log):
    name = os.path.basename(live)
    ts_mode = bool(ts_fields)
    keep_days = float(window_days) if window_days is not None else float(keep_days)
    cutoff = now - keep_days * 86400.0
    res = {"name": name, "status": "noop", "archived_lines": 0,
           "archived_bytes": 0, "kept_bytes": 0, "drained_bytes": 0}

    if not os.path.exists(live):
        res["status"] = "missing"
        return res
    if os.path.getsize(live) == 0:
        res["status"] = "empty"
        return res

    # Quiet-guard only the writer that holds a handle across a loop: never roll
    # its file while it might be mid-write. Reopen-per-append writers are handled
    # by the drain, so they are not gated (they would otherwise be skipped most
    # of the time while the bot is active).
    if writer == "cron" and (now - os.path.getmtime(live)) < quiet_seconds:
        res["status"] = "skipped_quiet"
        return res

    state = _load_state(live)
    through_ts = state.get("archived_through_ts")
    committed = state.get("archive_bytes")
    archive = archive_path_for(live)

    # Reconcile archive with state before touching anything.
    if not dry_run:
        committed = _repair_archive(archive, committed)
    if os.path.exists(archive):
        if through_ts is None:               # state lost but archive present
            through_ts = _recover_through_ts(archive, ts_fields)
        if committed is None:
            committed = os.path.getsize(archive)
    else:
        committed = 0
    if through_ts is None:
        through_ts = float("-inf")

    keep_fd = open(live, "rb")
    try:
        data = keep_fd.read()
        eof0 = len(data)
        lines = _scan(data, ts_fields)
        cut_index = _split_index(lines, cutoff, ts_mode, fallback_keep_lines)
        if cut_index <= 0:
            res["status"] = "noop"
            return res

        cut_byte = lines[cut_index].start if cut_index < len(lines) else eof0

        # Fresh = old lines not already archived (ts mode dedups by watermark;
        # fallback archives the whole old prefix since it has no usable ts).
        if ts_mode:
            k = 0
            while (k < cut_index and lines[k].recency is not None
                   and lines[k].recency <= through_ts):
                k += 1
        else:
            k = 0
        fresh_start = lines[k].start if k < cut_index else cut_byte
        member_src = data[fresh_start:cut_byte]
        fresh_lines = cut_index - k
        new_through = through_ts
        if ts_mode:
            for j in range(k, cut_index):
                r = lines[j].recency
                if r is not None and (new_through == float("-inf") or r > new_through):
                    new_through = r

        if dry_run:
            res["status"] = "dry_run"
            res["archived_lines"] = fresh_lines
            res["archived_bytes"] = len(member_src)
            res["kept_bytes"] = eof0 - cut_byte
            res["would_remove_old_lines"] = cut_index
            return res

        # ---- archive FIRST (so a crash can only duplicate, never drop) ----
        if member_src:
            member = gzip.compress(member_src, mtime=0)
            new_size = _append_member(archive, member, committed)
            state["archived_through_ts"] = (
                new_through if new_through != float("-inf") else through_ts
            )
            state["archive_bytes"] = new_size
            state["archived_lines_total"] = (
                int(state.get("archived_lines_total", 0)) + fresh_lines
            )
            _save_state(live, state)  # COMMIT POINT for the archive

        # ---- then shrink the live file, preserving concurrent appends ----
        kept, drained = _shrink_live(live, data, cut_byte, eof0, keep_fd)
        res["kept_bytes"] = kept
        res["drained_bytes"] = drained
        res["archived_lines"] = fresh_lines if member_src else 0
        res["archived_bytes"] = len(member_src)
        res["status"] = "rolled" if member_src else "recovered"

        state["rolls"] = int(state.get("rolls", 0)) + 1
        state["last_roll_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
        state.setdefault("archive_bytes", committed)
        state.setdefault(
            "archived_through_ts",
            through_ts if through_ts != float("-inf") else None,
        )
        _save_state(live, state)
        return res
    finally:
        keep_fd.close()


def roll_all(now=None, window_days=None, dry_run=False, log=_log):
    now = time.time() if now is None else now
    results = []
    for t in TARGETS:
        try:
            r = roll_one(
                live_path(t), t.ts_fields, t.keep_days, t.writer, now=now,
                fallback_keep_lines=t.fallback_keep_lines,
                window_days=window_days, dry_run=dry_run, log=log,
            )
        except Exception as exc:  # one bad file must not abort the rest
            r = {"name": t.name, "status": f"error:{type(exc).__name__}",
                 "error": str(exc)}
        results.append(r)
        verb = "would archive" if dry_run else "archived"
        if r["status"] in ("rolled", "dry_run", "recovered"):
            log(f"{r['name']}: {r['status']} — {verb} {r.get('archived_lines', 0)} "
                f"lines / {r.get('archived_bytes', 0)} B, live tail {r.get('kept_bytes', 0)} B"
                + (f", drained {r['drained_bytes']} B" if r.get("drained_bytes") else ""))
        else:
            log(f"{r['name']}: {r['status']}")
    return results


# --------------------------------------------------------------------------- #
# status                                                                      #
# --------------------------------------------------------------------------- #
def _human(n):
    size = float(n)
    for unit in ("B", "K", "M", "G"):
        if size < 1024 or unit == "G":
            return f"{size:.0f}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}G"


def status(log=print):
    log(f"{'file':28s} {'live':>10s} {'archive':>10s} {'rolls':>6s}  archived_through")
    for t in TARGETS:
        live = live_path(t)
        arc = archive_path_for(live)
        lsz = os.path.getsize(live) if os.path.exists(live) else 0
        asz = os.path.getsize(arc) if os.path.exists(arc) else 0
        st = _load_state(live)
        thr = st.get("archived_through_ts")
        thr_s = "-"
        if isinstance(thr, (int, float)):
            thr_s = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(thr))
        log(f"{t.name:28s} {_human(lsz):>10s} {_human(asz):>10s} "
            f"{int(st.get('rolls', 0)):>6d}  {thr_s}")


# --------------------------------------------------------------------------- #
# self-test (synthetic data in a temp dir; no real files touched)             #
# --------------------------------------------------------------------------- #
def _selftest():
    import shutil
    import tempfile
    import threading
    from discovery.jsonl_archive import iter_records

    failures = []

    def check(cond, msg):
        print(("  ok  " if cond else "  FAIL ") + msg)
        if not cond:
            failures.append(msg)

    work = tempfile.mkdtemp(prefix="jsonl_roll_selftest_")
    try:
        now = 1_780_000_000.0
        day = 86400.0
        live = os.path.join(work, "participation_log.jsonl")

        # 40 old (80..41 days, all > 30) + 10 recent (9..0 days). recency monotonic.
        old, recent = [], []
        with open(live, "w", encoding="utf-8") as f:
            for i in range(40):
                ts = now - (80 - i) * day
                rec = {"ts": ts, "token": f"OLD{i}", "row": {"v": i}}
                old.append(rec)
                f.write(json.dumps(rec) + "\n")
            for i in range(10):
                ts = now - (9 - i) * day
                rec = {"ts": ts, "token": f"NEW{i}", "row": {"v": i}}
                recent.append(rec)
                f.write(json.dumps(rec) + "\n")
        original = old + recent

        # ---- 1. basic roll: 30-day window archives the 40 old, keeps 10 ----
        r = roll_one(live, ("ts",), 30.0, "reopen", now=now)
        check(r["status"] == "rolled", f"status rolled (got {r['status']})")
        check(r["archived_lines"] == 40, f"archived 40 (got {r['archived_lines']})")
        live_recs = list(iter_records(live, include_archive=False))
        check(len(live_recs) == 10, f"live keeps 10 (got {len(live_recs)})")
        check(all(x["token"].startswith("NEW") for x in live_recs), "live has only NEW")
        full = list(iter_records(live))
        check([x["ts"] for x in full] == [x["ts"] for x in original],
              "archive+live == original (order + no loss)")
        check(os.path.exists(archive_path_for(live)), "archive .gz created")

        # ---- 2. idempotent: second roll is a no-op ----
        r2 = roll_one(live, ("ts",), 30.0, "reopen", now=now)
        check(r2["status"] == "noop", f"2nd roll noop (got {r2['status']})")
        check(len(list(iter_records(live))) == 50, "still 50 records after rerun")

        # ---- 3. concurrent appends during a roll survive ----
        live2 = os.path.join(work, "ai_advice.jsonl")
        with open(live2, "w", encoding="utf-8") as f:
            for i in range(30):
                f.write(json.dumps({"ts": now - (40 - i) * day, "i": i}) + "\n")
        appended = []
        orig_shrink = _shrink_live

        def racing_shrink(lp, data, cut_byte, eof0, keep_fd):
            # simulate a reopen-per-append writer firing mid-roll, twice:
            # once before the swap (old inode) and once after (new inode).
            def w(tag):
                with open(lp, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps({"ts": now, "appended": tag}) + "\n")
            w("pre")
            out = orig_shrink(lp, data, cut_byte, eof0, keep_fd)
            w("post")
            appended.extend(["pre", "post"])
            return out

        globals()["_shrink_live"] = racing_shrink
        try:
            roll_one(live2, ("ts",), 30.0, "reopen", now=now)
        finally:
            globals()["_shrink_live"] = orig_shrink
        recs2 = list(iter_records(live2))
        tags = [x.get("appended") for x in recs2 if x.get("appended")]
        check("pre" in tags and "post" in tags,
              f"both mid-roll appends preserved (got {tags})")
        check(sum(1 for x in recs2 if "i" in x) == 30, "all 30 originals preserved")

        # ---- 4. crash recovery: archive committed, live NOT shrunk -> no dup --
        live3 = os.path.join(work, "crash.jsonl")
        with open(live3, "w", encoding="utf-8") as f:
            for i in range(20):
                f.write(json.dumps({"ts": now - (40 - i) * day, "i": i}) + "\n")
        # do a real roll, then forcibly "un-shrink" the live file by restoring
        # the original (archive + state remain) to mimic a crash before shrink.
        shutil.copyfile(live3, live3 + ".orig")
        roll_one(live3, ("ts",), 30.0, "reopen", now=now)
        archived_after = os.path.getsize(archive_path_for(live3))
        shutil.copyfile(live3 + ".orig", live3)  # live now holds old lines again
        r4 = roll_one(live3, ("ts",), 30.0, "reopen", now=now)
        check(os.path.getsize(archive_path_for(live3)) == archived_after,
              "crash-recovery re-roll does NOT grow the archive (no dup)")
        full3 = list(iter_records(live3))
        check(len(full3) == 20 and len({x["i"] for x in full3}) == 20,
              f"crash-recovery keeps exactly 20 unique records (got {len(full3)})")

        # ---- 5. fallback (no ts field): keep last N by line count ----
        live4 = os.path.join(work, "nots.jsonl")
        with open(live4, "w", encoding="utf-8") as f:
            for i in range(100):
                f.write(json.dumps({"seq": i, "note": "no timestamp here"}) + "\n")
        r5 = roll_one(live4, (), 30.0, "reopen", now=now, fallback_keep_lines=10)
        check(r5["status"] == "rolled", f"fallback rolled (got {r5['status']})")
        live4_recs = list(iter_records(live4, include_archive=False))
        check(len(live4_recs) == 10 and live4_recs[0]["seq"] == 90,
              f"fallback keeps last 10 (got {len(live4_recs)})")
        check([x["seq"] for x in iter_records(live4)] == list(range(100)),
              "fallback archive+live == original")

        # ---- 6. quiet-guard skips a freshly-touched cron-writer file ----
        live5 = os.path.join(work, "discovery_outcomes.jsonl")
        with open(live5, "w", encoding="utf-8") as f:
            for i in range(20):
                f.write(json.dumps({"recorded_at": now - (40 - i) * day,
                                    "alert_ts": now - (41 - i) * day}) + "\n")
        os.utime(live5, (now, now))  # mtime = now (looks freshly written)
        r6 = roll_one(live5, ("recorded_at", "alert_ts"), 30.0, "cron",
                      now=now + 5, quiet_seconds=180)
        check(r6["status"] == "skipped_quiet", f"cron quiet-skip (got {r6['status']})")
        r6b = roll_one(live5, ("recorded_at", "alert_ts"), 30.0, "cron",
                       now=now + 10_000, quiet_seconds=180)
        check(r6b["status"] == "rolled", f"cron rolls once quiet (got {r6b['status']})")

        # ---- 7. multi-member archive: two staged rolls (as the daily cron does)
        #         append two gzip members that both read back, in order ----
        live6 = os.path.join(work, "multi.jsonl")
        with open(live6, "w", encoding="utf-8") as f:
            for i in range(30):  # ages 100..71 days (all old)
                f.write(json.dumps({"ts": now - (100 - i) * day, "i": i}) + "\n")
        roll_one(live6, ("ts",), 90.0, "reopen", now=now)   # member 1: i0..9
        roll_one(live6, ("ts",), 80.0, "reopen", now=now)   # member 2: i10..19
        live_only = [r["i"] for r in iter_records(live6, include_archive=False)]
        full = [r["i"] for r in iter_records(live6)]
        check(live_only == list(range(20, 30)),
              f"two-member: live keeps last 10 (got {live_only})")
        check(full == list(range(30)),
              "two-member: archive(2 members)+live == original, in order")
    finally:
        shutil.rmtree(work, ignore_errors=True)

    print()
    if failures:
        print(f"SELFTEST FAILED: {len(failures)} check(s) failed")
        return 1
    print("SELFTEST PASSED")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would be archived; write nothing")
    ap.add_argument("--status", action="store_true",
                    help="show live/archive sizes and watermarks")
    ap.add_argument("--selftest", action="store_true",
                    help="run synthetic safety tests (no real files touched)")
    ap.add_argument("--window-days", type=float, default=None,
                    help="override the keep window (default: per-file, 30d)")
    args = ap.parse_args()

    if args.selftest:
        sys.exit(_selftest())
    if args.status:
        status()
        return
    _log("start" + (" (dry-run)" if args.dry_run else ""))
    roll_all(window_days=args.window_days, dry_run=args.dry_run)
    _log("done")


if __name__ == "__main__":
    main()
