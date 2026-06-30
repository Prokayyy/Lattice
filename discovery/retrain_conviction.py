"""Retrain the conviction ranker on the discovery system's OWN forward outcomes
and DEPLOY only if it clears a quality bar AND doesn't regress vs the last
deployed model. Designed to run from cron.

Data source x label (choose with --source / --label):
  --source censored   : discovery_outcomes.jsonl (rows that PASSED the live gates;
                        biased -> a model trained here fails on the full live
                        population. AUC ~0.45 on full pop). Legacy default.
  --source uncensored : replay raw signal_snapshots through the cheap gates (the
                        population the live conviction gate actually scores).
  --label  twox       : did it reach >=2x within 1h (legacy objective).
  --label  pnl        : realized PnL > 0 under the live exit engine. For
                        uncensored this REPLAYS discovery.manager.manage over each
                        candidate's forward path (~7 min, one ordered snapshot
                        pass). THIS IS THE PNL-ALIGNED, VALIDATED OBJECTIVE: the
                        2x AUC win does NOT convert to PnL; the pnl model does
                        (time-OOS AUC ~0.60, lifts realized PnL at equal volume).

Model: standardized logistic regression in the JSON format ConvictionRanker.load()
reads (feature_names, mean, std, w, b) + harmless metadata (label, source,
recommended_min_conviction). The live pipeline picks it up with no code change.

Deploy guard (so a bad retrain can't degrade the live alert gate):
  deploy IFF  new_oos_auc >= MIN_AUC  AND  new_oos_auc >= last_deployed_auc - AUC_REGRESS_MARGIN
(last_deployed_auc compared WITHIN the same label, so a pnl AUC isn't judged
against a twox AUC). Backs up the old model before swapping; logs every run.

Run:
  # legacy cron default (censored twox) — UNCHANGED:
  env/bin/python -m discovery.retrain_conviction --restart
  # recommended / validated model (eval only, never swaps the live model):
  env/bin/python -m discovery.retrain_conviction --source uncensored --label pnl --no-deploy
The pnl model's validated entry cutoff is PNL_MODEL_MIN_CONVICTION (live
min_conviction must be set to it when this model is deployed; the new score scale
is NOT the old 0.18).
"""
import argparse
import bisect
import json
import os
import shutil
import sqlite3
import sys
import time
from datetime import datetime, timezone

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from discovery import features as F

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLOG = os.path.join(ROOT, "discovery", "participation_log.jsonl")
OUT = os.path.join(ROOT, "discovery", "discovery_outcomes.jsonl")
MODEL = os.path.join(ROOT, "discovery", "models", "conviction_ranker.json")
LOG = os.path.join(ROOT, "discovery", "retrain_log.jsonl")

LABEL = "max_mult_1h"          # 2x within 1h — matches the deployed model
RUN_MULT = 2.0
MIN_AUC = 0.58                 # never deploy a model weaker than this
AUC_REGRESS_MARGIN = 0.03      # allow this much CV noise below the last deploy
MIN_ROWS = 500
MIN_POS = 40
RUNNER_UNIT = "lattice-runner-supervisor.service"

# Uncensored training: discovery_outcomes.jsonl only contains rows that PASSED
# the live gates (conviction>=0.18, lattice floor), so a model trained on it is
# blind to the population it actually faces at floor 0 and fails to generalise
# (measured OOS AUC ~0.45 on the full population vs ~0.73 retrained uncensored).
# The uncensored builder replays raw signal_snapshots through the cheap gates and
# computes forward labels from each token's own price path. Breadth is NOT in raw
# snapshots, so this trains breadth-blind (participation_breadth weight ~0).
UNCENSORED_DAYS = 20.0
UNCENSORED_MAX_PC24H = 300.0
_LABEL_WINDOW_S = {"max_mult_5m": 300, "max_mult_15m": 900,
                   "max_mult_1h": 3600, "max_mult_6h": 21600}

# Exit-engine PnL labels (deployable objective). build_uncensored_pnl_dataset
# replays discovery.manager.manage(engine="new") with look-ahead-safe ATR candles
# over each candidate's forward path, conviction-gate OFF, 6h per-token cooldown.
PNL_SIZE_USD = float(getattr(config, "POSITION_POSITION_SIZE_USD", 20) or 20)
PNL_COOLDOWN_S = 6 * 3600
PNL_MAX_HOLD_S = float(getattr(config, "LATTICE_MAX_HOLD_H", 12) or 12) * 3600
# Validated entry cutoff on the pnl model's score (see analysis: equal-volume
# time-OOS keeps PnL while cutting ~27% of alerts; plateau 0.27-0.32). When the
# pnl model is deployed, the live min_conviction must be set to this.
PNL_MODEL_MIN_CONVICTION = 0.30
LEGACY_MIN_CONVICTION = 0.18
_UNCENSORED_COLS = [
    "token_address", "timestamp", "price", "price_change_5m", "price_change_1h",
    "price_change_24h", "volume_1h", "volume_liquidity_ratio",
    "h1_volume_liquidity_ratio", "buy_sell_ratio", "h1_buy_sell_ratio",
    "buy_volume_5m", "sell_volume_5m", "impulse", "pressure", "liquidity",
    "score", "liquidity_change_pct",
]


def _deglitch_reject(price, accepted):
    """Match discovery/outcomes.py: reject ticks far from the rolling median."""
    window = accepted[-9:]
    ref = sorted(window)[len(window) // 2] if window else price
    return price > 4.0 * ref or price < ref / 10.0


def build_uncensored_dataset(label=LABEL, days=UNCENSORED_DAYS,
                             max_pc24h=UNCENSORED_MAX_PC24H):
    """Replay raw signal_snapshots (hot+archive) through the cheap entry gates and
    label each eligible row with forward `label` >= RUN_MULT, computed from the
    token's own forward price path. Returns (X, y, score_only) like build_dataset.
    No SQL ORDER BY (root fs may be full): paths are grouped per token in RAM."""
    from discovery.lattice import lattice_verdict
    from discovery.pipeline import ConvictionPipeline
    from storage.history import open_history

    win = _LABEL_WINDOW_S.get(label, 3600)
    con = open_history()
    con.row_factory = sqlite3.Row
    have = {d[1] for d in con.execute("PRAGMA table_info('signal_snapshots_all')")}
    cols = [c for c in _UNCENSORED_COLS if c in have]
    end = con.execute("SELECT MAX(timestamp) FROM signal_snapshots_all WHERE price>0").fetchone()[0]
    start = end - days * 86400
    pipe = ConvictionPipeline(min_conviction=0.0, min_lattice=0.0,
                              max_price_change_24h=max_pc24h)

    paths, cands = {}, []
    sql = (f"SELECT {','.join(cols)} FROM signal_snapshots_all "
           "WHERE price>0 AND price_change_5m IS NOT NULL AND timestamp>=?")
    for r in con.execute(sql, (start,)):
        row = dict(r)
        tok = row.get("token_address") or ""
        try:
            ts = float(row.get("timestamp") or 0)
            price = float(row.get("price") or 0)
        except (TypeError, ValueError):
            continue
        if not tok or price <= 0:
            continue
        paths.setdefault(tok, []).append((ts, price))
        if not pipe.universe(row):
            continue
        try:
            if float(row.get("price_change_24h") or 0) > max_pc24h:
                continue
        except (TypeError, ValueError):
            pass
        sv = lattice_verdict(row, participation=None,
                             liquidity_change_pct=row.get("liquidity_change_pct"))
        if not sv["passed"]:
            continue
        cands.append((tok, ts, F.extract(row, participation=None),
                      float(row.get("score") or 0)))
    con.close()

    for tok in paths:
        paths[tok].sort(key=lambda x: x[0])

    X, y, score_only = [], [], []
    for tok, ts, fv, score in cands:
        path = paths.get(tok)
        if not path:
            continue
        tss = [p[0] for p in path]
        lo = bisect.bisect_right(tss, ts)
        if lo >= len(path) or tss[lo] > ts + win:
            continue
        ip = bisect.bisect_left(tss, ts)
        entry = path[ip][1] if ip < len(path) and tss[ip] == ts else path[lo - 1][1]
        if not entry or entry <= 0:
            continue
        accepted, mx, j = [entry], 1.0, lo
        while j < len(path) and tss[j] <= ts + win:
            pj = path[j][1]
            if pj > 0 and not _deglitch_reject(pj, accepted):
                accepted.append(pj)
                if pj / entry > mx:
                    mx = pj / entry
            j += 1
        X.append(fv)
        y.append(1.0 if mx >= RUN_MULT else 0.0)
        score_only.append(score)
    return np.array(X, float), np.array(y, float), np.array(score_only, float)


def _install_history_candles(con):
    """Point manager._recent_candles_for_atr at hot+archive candles, look-ahead
    safe (bucket_start <= as_of_ts). Cached. Mirrors analysis/lattice_entry_sweep."""
    import discovery.manager as manager
    cache, cap = {}, 20_000
    period = int(getattr(config, "POSITION_ATR_STOP_PERIOD", 14) or 14)
    timeframe = int(getattr(config, "POSITION_ATR_STOP_TIMEFRAME_SECONDS", 300) or 300)
    limit = period * 4 + 10

    def recent(address, as_of_ts=None):
        bucket = None if as_of_ts is None else int(float(as_of_ts) // timeframe)
        key = (str(address), bucket)
        if key in cache:
            return cache[key]
        if as_of_ts is None:
            rows = con.execute(
                "SELECT bucket_start, high, low, close FROM token_candles_all "
                "WHERE token_address=? AND timeframe_seconds=? "
                "ORDER BY bucket_start DESC LIMIT ?", (str(address), timeframe, limit),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT bucket_start, high, low, close FROM token_candles_all "
                "WHERE token_address=? AND timeframe_seconds=? AND bucket_start<=? "
                "ORDER BY bucket_start DESC LIMIT ?",
                (str(address), timeframe, float(as_of_ts), limit),
            ).fetchall()
        value = [{"bucket_start": r[0], "high": r[1], "low": r[2], "close": r[3]}
                 for r in reversed(rows) if r[1] and r[2] and r[3]]
        if len(cache) >= cap:
            for old in list(cache)[: cap // 4]:
                del cache[old]
        cache[key] = value
        return value

    manager._recent_candles_for_atr = recent


def build_uncensored_pnl_dataset(days=UNCENSORED_DAYS, max_pc24h=UNCENSORED_MAX_PC24H):
    """Replay raw signal_snapshots through the cheap gates (conviction OFF), enter
    every eligible candidate (6h per-token cooldown), and run the REAL exit engine
    over each forward path. Label = realized_pnl > 0. Returns (X, y, score_only).
    Heavier than the 2x builder (one ORDER BY timestamp pass + per-tick manage)."""
    import discovery.manager as manager
    from discovery.lattice import lattice_verdict
    from discovery.pipeline import ConvictionPipeline
    from storage.history import open_history

    con = open_history()
    con.row_factory = sqlite3.Row
    _install_history_candles(con)
    end = con.execute("SELECT MAX(timestamp) FROM signal_snapshots_all WHERE price>0").fetchone()[0]
    start = end - days * 86400
    pipe = ConvictionPipeline(min_conviction=0.0, min_lattice=0.0,
                              max_price_change_24h=max_pc24h)

    open_pos, cooldown, last_tick, trades = {}, {}, {}, []

    def _num(row, k):
        try:
            v = row.get(k)
            return float(v) if v is not None else 0.0
        except (TypeError, ValueError):
            return 0.0

    for r in con.execute(
        "SELECT * FROM signal_snapshots_all WHERE price>0 AND price_change_5m IS NOT NULL "
        "AND timestamp>=? ORDER BY timestamp", (start,)
    ):
        row = dict(r)
        tok = row.get("token_address") or ""
        price, ts = _num(row, "price"), _num(row, "timestamp")
        if not tok or price <= 0:
            continue
        last_tick[tok] = (ts, price)
        pos = open_pos.get(tok)
        if pos is not None:
            manager.manage(pos, price, ts, max_hold_s=PNL_MAX_HOLD_S, features=row, engine="new")
            if pos.get("closed"):
                trades.append((pos["entry_feats"], pos["proceeds"] - PNL_SIZE_USD, pos["entry_score"]))
                del open_pos[tok]
                cooldown[tok] = ts + PNL_COOLDOWN_S
            continue
        if ts < cooldown.get(tok, 0) or not pipe.universe(row):
            continue
        if max_pc24h and _num(row, "price_change_24h") > max_pc24h:
            continue
        sv = lattice_verdict(row, participation=None,
                             liquidity_change_pct=row.get("liquidity_change_pct"))
        if not sv["passed"]:
            continue
        liq = _num(row, "liquidity") or _num(row, "raw_liquidity")
        open_pos[tok] = {
            "token": tok, "symbol": row.get("symbol", ""), "entry_ts": ts,
            "entry_price": price, "remaining": PNL_SIZE_USD / price, "peak": price,
            "proceeds": 0.0, "scaled": False, "levels_done": set(),
            "cost_usd": PNL_SIZE_USD, "entry_liquidity": liq, "peak_liquidity": liq,
            "entry_feats": F.extract(row, participation=None),
            "entry_score": _num(row, "score"),
        }

    for tok, pos in list(open_pos.items()):
        _ts, price = last_tick.get(tok, (pos["entry_ts"], pos["entry_price"]))
        pos["proceeds"] += pos["remaining"] * price
        trades.append((pos["entry_feats"], pos["proceeds"] - PNL_SIZE_USD, pos["entry_score"]))
    con.close()

    X = np.array([t[0] for t in trades], float)
    y = np.array([1.0 if t[1] > 0 else 0.0 for t in trades], float)
    score_only = np.array([t[2] for t in trades], float)
    return X, y, score_only


def _iter_jsonl(path):
    if not os.path.exists(path):
        return
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def load_features():
    feats = {}
    for r in _iter_jsonl(PLOG):
        tok, ts, row = r.get("token"), r.get("ts"), r.get("row") or {}
        if tok and ts and row:
            feats[(tok, int(float(ts)))] = (row, r.get("breadth"))
    return feats


def build_dataset(feats, label="twox"):
    """Censored dataset from discovery_outcomes.jsonl. label 'twox' -> max_mult_1h
    >= 2; label 'pnl' -> realized_pnl > 0 (uses the recorder's exit-engine PnL)."""
    key = "realized_pnl" if label == "pnl" else "max_mult_1h"
    X, y, score_only = [], [], []
    for o in _iter_jsonl(OUT):
        if o.get("no_data") or o.get(key) is None:
            continue
        fr = feats.get((o.get("token"), int(float(o.get("alert_ts") or 0))))
        if fr is None:
            continue
        row, breadth = fr
        val = float(o[key])
        yi = 1.0 if (val > 0 if label == "pnl" else val >= RUN_MULT) else 0.0
        X.append(F.extract(row, participation=breadth))
        y.append(yi)
        score_only.append(float(row.get("score") or 0))
    return np.array(X, float), np.array(y, float), np.array(score_only, float)


def _sigmoid(z):
    return 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))


def fit(X, y, lr=0.1, epochs=4000, l2=2.0):
    """Standardized logistic regression — identical math to ConvictionRanker.fit,
    vectorized. Returns (mean, std, w, b)."""
    mean = X.mean(0)
    std = X.std(0, ddof=1)
    std[std < 1e-9] = 1.0
    Xs = (X - mean) / std
    n, d = Xs.shape
    w = np.zeros(d)
    b = 0.0
    for _ in range(epochs):
        err = _sigmoid(Xs @ w + b) - y
        w -= lr * ((Xs.T @ err) / n + l2 * w / n)
        b -= lr * (err.sum() / n)
    return mean, std, w, b


def proba(X, model):
    mean = np.array(model["mean"])
    std = np.array(model["std"])
    w = np.array(model["w"])
    return _sigmoid(((X - mean) / std) @ w + float(model["b"]))


def auc(scores, y):
    scores = np.asarray(scores, float)
    y = np.asarray(y, float)
    n_pos = int(y.sum())
    n_neg = len(y) - n_pos
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    order = np.argsort(scores)
    ranks = np.empty(len(scores))
    ranks[order] = np.arange(1, len(scores) + 1)
    return (ranks[y == 1].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg)


def kfold_oos(X, y, k=5, seed=13):
    rng = np.random.RandomState(seed)
    idx = rng.permutation(len(X))
    oos = np.zeros(len(X))
    for f in range(k):
        test = idx[f::k]
        train = np.setdiff1d(idx, test)
        m = fit(X[train], y[train])
        oos[test] = proba(X[test], dict(zip(("mean", "std", "w", "b"),
                                            (m[0], m[1], m[2], m[3]))))
    return oos


def last_deployed_auc(label=None):
    """Most recent deployed OOS AUC for the SAME label (a pnl AUC must not be
    judged against a twox AUC). Legacy rows have no 'label' -> treated as twox."""
    best = None
    for rec in _iter_jsonl(LOG):
        if not (rec.get("deployed") and rec.get("oos_auc") is not None):
            continue
        if label is not None and rec.get("label", "twox") != label:
            continue
        best = float(rec["oos_auc"])
    return best


def write_log(rec):
    rec = dict(rec)
    rec["iso"] = datetime.now(timezone.utc).isoformat()
    with open(LOG, "a") as fh:
        fh.write(json.dumps(rec) + "\n")


def restart_runner():
    env = dict(os.environ)
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    import subprocess
    return subprocess.call(
        ["systemctl", "--user", "restart", RUNNER_UNIT], env=env
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-deploy", action="store_true",
                    help="evaluate only, never swap the live model")
    ap.add_argument("--restart", action="store_true",
                    help="restart the runner after a successful deploy")
    # Defaults are the LEGACY cron behaviour (censored twox) so the weekly cron
    # is UNCHANGED. The validated model is --source uncensored --label pnl.
    ap.add_argument("--source", choices=("censored", "uncensored"),
                    default="censored",
                    help="censored: discovery_outcomes.jsonl (biased, legacy); "
                         "uncensored: replay raw snapshots (the live population)")
    ap.add_argument("--label", choices=("twox", "pnl"), default="twox",
                    help="twox: >=2x within 1h (legacy); pnl: realized_pnl>0 "
                         "(validated PnL-aligned objective)")
    args = ap.parse_args()

    if args.source == "uncensored" and args.label == "pnl":
        X, y, score_only = build_uncensored_pnl_dataset()
    elif args.source == "uncensored":
        X, y, score_only = build_uncensored_dataset()
    else:
        X, y, score_only = build_dataset(load_features(), label=args.label)
    n, pos = len(X), int(y.sum())
    print(f"dataset n={n} | positives={pos} ({100 * pos / max(n, 1):.1f}%) | "
          f"source={args.source} label={args.label}")

    if n < MIN_ROWS or pos < MIN_POS:
        write_log({"n": n, "positives": pos, "deployed": False,
                   "reason": "insufficient_data", "source": args.source, "label": args.label})
        print(f"insufficient data (need n>={MIN_ROWS}, pos>={MIN_POS}) — abort")
        return

    cutoff = PNL_MODEL_MIN_CONVICTION if args.label == "pnl" else LEGACY_MIN_CONVICTION
    new_auc = float(auc(kfold_oos(X, y), y))
    base_auc = float(auc(score_only, y))
    prev = last_deployed_auc(label=args.label)
    print(f"OOS AUC: new ranker {new_auc:.3f} | baseline `score` {base_auc:.3f}"
          f" | last deployed ({args.label}) {prev if prev is not None else 'n/a'}")
    print(f"recommended live min_conviction for this model: {cutoff}")

    floor = max(MIN_AUC, (prev - AUC_REGRESS_MARGIN) if prev is not None else MIN_AUC)
    if args.no_deploy:
        deploy, reason = False, "no_deploy_flag"
    elif new_auc < floor:
        deploy, reason = False, f"auc_{new_auc:.3f}<floor_{floor:.3f}"
    else:
        deploy, reason = True, "passed_guard"

    backup = ""
    if deploy:
        mean, std, w, b = fit(X, y)
        if os.path.exists(MODEL):
            backup = MODEL + ".bak-" + time.strftime("%Y%m%d-%H%M%S")
            shutil.copy2(MODEL, backup)
        payload = {"feature_names": F.FEATURE_NAMES, "mean": mean.tolist(),
                   "std": std.tolist(), "w": w.tolist(), "b": float(b),
                   "label": args.label, "source": args.source,
                   "recommended_min_conviction": cutoff,
                   "trained_at": datetime.now(timezone.utc).isoformat()}
        tmp = MODEL + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(payload, fh, indent=2)
        os.replace(tmp, MODEL)
        print(f"DEPLOYED new model -> {MODEL} (backup: {backup or 'none'})")
        if args.label == "pnl":
            print(f"REMINDER: set live min_conviction to {cutoff} (new score scale "
                  f"!= old {LEGACY_MIN_CONVICTION}); rescale regime-guard bumps too.")
        if args.restart:
            rc = restart_runner()
            print(f"runner restart exit={rc}")
    else:
        print(f"NOT deployed ({reason})")

    write_log({"n": n, "positives": pos, "oos_auc": round(new_auc, 4),
               "baseline_score_auc": round(base_auc, 4), "prev_deployed_auc": prev,
               "deployed": deploy, "reason": reason, "backup": backup,
               "source": args.source, "label": args.label,
               "recommended_min_conviction": cutoff})


if __name__ == "__main__":
    main()
