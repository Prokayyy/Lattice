#!/usr/bin/env python3
"""Export the conviction-ranker training set to CSV (for Colab / offline ML).

Same labeling as discovery/train_ranker.py — alert_outcomes joined to the
decision-time signal_snapshots row — but reads via storage.history.open_history
so it spans BOTH the hot DB and the archive (after the retention trim, older
decision snapshots live in scanner_archive.db; without this the dataset would
silently shrink to the last few days).

Output CSV columns: the FEATURE_NAMES vector, then label, plus token_address /
max_multiple / alert_timestamp for time-based (walk-forward) splits in Colab.

Run: env/bin/python tools/export_training_data.py [--window 1h] [--run-mult 2.0]
                                                  [--out discovery/models/training_data.csv]
"""

import argparse
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from discovery import features as F          # noqa: E402
from storage.history import open_history     # noqa: E402

SNAP_BEFORE = 300   # seconds before the alert to find the decision snapshot
SNAP_AFTER = 10


def build_rows(window, run_mult):
    con = open_history(read_only=True)
    outs = con.execute(
        "SELECT alert_id, token_address, alert_timestamp, max_multiple "
        "FROM alert_outcomes WHERE window_label = ? AND complete = 1 "
        "AND max_multiple IS NOT NULL",
        (window,),
    ).fetchall()

    rows = []
    pos = 0
    for o in outs:
        snap = con.execute(
            "SELECT * FROM signal_snapshots_all WHERE token_address = ? "
            "AND timestamp BETWEEN ? AND ? ORDER BY timestamp DESC LIMIT 1",
            (
                o["token_address"],
                o["alert_timestamp"] - SNAP_BEFORE,
                o["alert_timestamp"] + SNAP_AFTER,
            ),
        ).fetchone()
        if snap is None:
            continue
        feats = F.extract(dict(snap))
        label = 1 if (o["max_multiple"] or 0) >= run_mult else 0
        pos += label
        rows.append(
            feats
            + [label, o["token_address"], o["max_multiple"], o["alert_timestamp"]]
        )
    con.close()
    return rows, len(outs), pos


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--window", default="1h")
    ap.add_argument("--run-mult", type=float, default=2.0)
    ap.add_argument(
        "--out",
        default=os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "discovery", "models", "training_data.csv",
        ),
    )
    args = ap.parse_args()

    rows, n_out, pos = build_rows(args.window, args.run_mult)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    header = F.FEATURE_NAMES + [
        "label", "token_address", "max_multiple", "alert_timestamp",
    ]
    with open(args.out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)

    n = len(rows)
    base = (100 * pos / n) if n else 0.0
    print(f"alert_outcomes({args.window}, complete) = {n_out}")
    print(f"usable rows (matched a decision snapshot) = {n}")
    print(f"  label >= {args.run_mult}x positives = {pos} ({base:.1f}% base rate)")
    print(f"wrote {args.out}")
    if n < 40 or pos < 8:
        print("WARNING: small dataset — model will be noisy. Collect more outcomes.")


if __name__ == "__main__":
    main()
