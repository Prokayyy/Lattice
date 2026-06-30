"""Shared config + archive-aware reader for the append-only discovery JSONL logs.

These files (participation_log, discovery_outcomes, ai_shadow_decisions,
trade_policy_log, ai_advice) are DATA the bot reads back, not disposable logs,
so they are excluded from logrotate (copytruncate would drop rows). Instead a
data-aware roller (tools/jsonl_roll.py) keeps a generous recent window in the
live file and moves older lines into a compressed sibling archive:

    participation_log.jsonl            <- live (recent, appended by the bot)
    participation_log.archive.jsonl.gz <- cold (older lines, gzip members)

gzip members concatenate, so each roll appends one new member instead of
rewriting the whole archive's logical content. This module is the single source
of truth for:

  * which files are rolled and by which timestamp field (TARGETS), and
  * how to read a logical stream back in order (iter_records) spanning the
    archive's gzip members then the live file.

Stdlib only (no scanner runtime deps) so both the roller tool and the analysis
readers (discovery.eval_outcomes, discovery.retrain_eval) can import it cheaply.
See ops/STORAGE.md for the storage-tier design this complements.
"""
from __future__ import annotations

import gzip
import json
import os
from dataclasses import dataclass

DISCOVERY_DIR = os.path.dirname(os.path.abspath(__file__))


@dataclass(frozen=True)
class Target:
    """A rolled JSONL file.

    name:        basename under discovery/.
    ts_fields:   JSON keys that hold epoch-second timestamps. A record's
                 recency is the MAX of whichever are present — so a record is
                 only ever "old" once it is old by EVERY timestamp it carries
                 (e.g. discovery_outcomes uses max(recorded_at, alert_ts), so an
                 outcome can never be archived before the participation entry
                 that produced it). Empty -> fall back to keep-last-N.
    keep_days:   keep records newer than this in the live file (generous, so the
                 daily consumers never lose rows they have not processed yet).
    writer:      how the live writer holds the file, which decides roll safety:
                   "reopen"  - opens O_APPEND per line (live_runner, ai_advisor);
                               an atomic-rename roll + held-fd drain is safe.
                   "cron"    - a scheduled job holds the handle open across a
                               whole loop (discovery.outcomes at 06:00); the roll
                               must not overlap it, enforced by a quiet-guard.
                   "none"    - no current writer (trade_policy_log); always safe.
    fallback_keep_lines: when ts_fields are absent/unparesable, keep this many
                 trailing lines instead of rolling by time.
    """

    name: str
    ts_fields: tuple = ()
    keep_days: float = 30.0
    writer: str = "reopen"
    fallback_keep_lines: int = 200_000


# Inspected against the real records on 2026-06-05 (head+tail of each file) —
# do NOT assume field names; these are the keys actually present:
#   participation_log   : {"ts": <epoch>, ...}
#   discovery_outcomes  : {"alert_ts": <epoch>, "recorded_at": <epoch>, ...}
#   ai_shadow_decisions : {"ts": <epoch>, ...}
#   trade_policy_log    : {"ts": <epoch>, ...}   (no live writer/reader anymore)
#   ai_advice           : {"ts": <epoch>, ...}
# trades.jsonl is intentionally NOT here: it is the small position ledger, read
# by several tail-readers and restore logic, and is not a disk-pressure source.
TARGETS: tuple = (
    Target("participation_log.jsonl", ("ts",), 30.0, "reopen"),
    Target("discovery_outcomes.jsonl", ("recorded_at", "alert_ts"), 30.0, "cron"),
    Target("ai_shadow_decisions.jsonl", ("ts",), 30.0, "reopen"),
    Target("trade_policy_log.jsonl", ("ts",), 30.0, "none"),
    Target("ai_advice.jsonl", ("ts",), 30.0, "reopen"),
)

TARGETS_BY_NAME = {t.name: t for t in TARGETS}


def live_path(target_or_name) -> str:
    name = target_or_name.name if isinstance(target_or_name, Target) else target_or_name
    return os.path.join(DISCOVERY_DIR, name)


def archive_path_for(live: str) -> str:
    """participation_log.jsonl -> participation_log.archive.jsonl.gz"""
    base = live[:-len(".jsonl")] if live.endswith(".jsonl") else live
    return base + ".archive.jsonl.gz"


def state_path_for(live: str) -> str:
    """Roller bookkeeping (watermark, archive size) sits beside the live file."""
    return live + ".roll_state.json"


def record_recency(obj, ts_fields):
    """Max of the present timestamp fields, or None if none are numeric.

    None means "cannot date this record" — the roller treats that as a hard
    keep boundary so an undatable (or partially written) line is never archived.
    """
    best = None
    for key in ts_fields:
        val = obj.get(key)
        if val is None:
            continue
        try:
            num = float(val)
        except (TypeError, ValueError):
            continue
        if best is None or num > best:
            best = num
    return best


def iter_lines(live, include_archive=True):
    """Yield raw (newline-stripped) text lines for a logical stream: the
    archive's gzip members first (oldest), then the live file (newest), so the
    order matches what the unrolled file would have produced.

    gzip.open transparently decodes all concatenated members. A roll commits the
    archive atomically (temp + os.replace) before shrinking the live file, so a
    reader only ever observes complete files; the worst a concurrent roll can do
    is briefly show a handful of in-flight lines in BOTH streams (a harmless
    duplicate), never drop them. We still tolerate a truncated final gzip member
    defensively and stop at it rather than raising.
    """
    if include_archive:
        archive = archive_path_for(live)
        if os.path.exists(archive):
            try:
                with gzip.open(archive, "rt", encoding="utf-8", errors="replace") as fh:
                    for line in fh:
                        yield line.rstrip("\n\r")
            except (OSError, EOFError, gzip.BadGzipFile):
                # Only an uncommitted trailing member can be bad; committed
                # members already yielded above. Fall through to the live file.
                pass
    if os.path.exists(live):
        with open(live, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                yield line.rstrip("\n\r")


def iter_records(live, include_archive=True):
    """iter_lines + json.loads, skipping blanks and undecodable lines — matching
    the tolerance every existing reader already applies to these logs."""
    for line in iter_lines(live, include_archive=include_archive):
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue
