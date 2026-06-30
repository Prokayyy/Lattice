"""Read-only PoC: PnL-based labels vs touch-2x labels vs the existing `score`.

For each labelable alert, computes the realized PnL a position WOULD have earned
under the live `new` exit engine by replaying the token's forward price path
through `manager.manage` (engine="new"). Then trains a profit-prediction model
out-of-sample and compares the **top-K realized PnL** of three selectors:
  - pnl-model   : logistic ranker trained on (profit>0) labels
  - touch-model : the current approach, trained on (max_multiple>=2) labels
  - score       : the existing production score at decision time (baseline)

This answers the only question that matters: does selecting alerts on a
PnL-trained model make MORE MONEY than the current score? Touches nothing live.

Run: env/bin/python -m discovery.pnl_label_poc
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from discovery import features as F
from discovery.ranker import ConvictionRanker, roc_auc
from discovery.train_ranker import DB, WINDOW, RUN_MULT, SNAP_BEFORE, SNAP_AFTER, kfold_oos
from discovery.manager import manage

SIZE_USD = float(getattr(config, "POSITION_POSITION_SIZE_USD", 20) or 20)
HORIZON_S = 6 * 3600     # forward window to evaluate the trade over
MAX_HOLD_S = 3 * 3600    # engine max-hold (live default), runners >=3x exempt


def forward_pnl(con, token, alert_ts, entry_price, entry_liq, outcomes_max):
    """Replay the token's forward path through the new engine; return realized PnL.

    Raw signal_snapshots prices carry glitch ticks (dust/MEV swaps that spike or
    crater price). We reject them BEFORE they reach the engine, anchored on the
    scanner's own sanity-checked `alert_outcomes.max_multiple`:
      - hard ceiling at entry x max(max_multiple,1) x 3  (a 6h sim must not exceed
        the trusted 1h peak by a large factor) -> kills 9,776x-vs-1.06x glitches;
      - low guard: drop any tick < 1/10 the rolling median of recent accepted
        prices (near-zero crater glitches that would fake-trigger a stop).
    """
    if entry_price <= 0:
        return None, 0
    rows = con.execute(
        "SELECT * FROM signal_snapshots WHERE token_address=? AND timestamp>? "
        "AND timestamp<=? AND price>0 ORDER BY timestamp ASC",
        (token, alert_ts, alert_ts + HORIZON_S),
    ).fetchall()
    if len(rows) < 2:
        return None, len(rows)
    ceiling = entry_price * max(float(outcomes_max or 0), 1.0) * 3.0
    pos = {
        "token": token, "symbol": "", "entry_ts": alert_ts,
        "entry_price": entry_price, "remaining": SIZE_USD / entry_price,
        "peak": entry_price, "proceeds": 0.0, "scaled": False,
        "levels_done": set(), "cost_usd": SIZE_USD,
        "entry_liquidity": entry_liq, "peak_liquidity": entry_liq,
    }
    accepted = [entry_price]
    last_price = entry_price
    for r in rows:
        row = dict(r)
        price = float(row.get("price") or 0)
        if price <= 0:
            continue
        window = accepted[-7:]
        ref = sorted(window)[len(window) // 2]   # rolling median of recent accepted
        if price > ceiling or price < ref / 10.0:
            continue                              # glitch tick — skip
        accepted.append(price)
        last_price = price
        manage(pos, price, float(row.get("timestamp") or 0),
               max_hold_s=MAX_HOLD_S, features=row, engine="new")
        if pos.get("closed"):
            break
    if not pos.get("closed"):  # mark-to-last at horizon end
        pos["proceeds"] += pos["remaining"] * last_price
        pos["remaining"] = 0.0
    return pos["proceeds"] - SIZE_USD, len(rows)


def build(con):
    outs = con.execute(
        "SELECT token_address, alert_timestamp, max_multiple FROM alert_outcomes "
        "WHERE window_label=? AND complete=1 AND max_multiple IS NOT NULL",
        (WINDOW,),
    ).fetchall()
    X, pnl, touch, score = [], [], [], []
    n_match = n_label = 0
    for o in outs:
        snap = con.execute(
            "SELECT * FROM signal_snapshots WHERE token_address=? "
            "AND timestamp BETWEEN ? AND ? ORDER BY timestamp DESC LIMIT 1",
            (o["token_address"], o["alert_timestamp"] - SNAP_BEFORE,
             o["alert_timestamp"] + SNAP_AFTER),
        ).fetchone()
        if snap is None:
            continue
        n_match += 1
        row = dict(snap)
        ep = float(row.get("price") or 0)
        eliq = float(row.get("liquidity") or row.get("raw_liquidity") or 0)
        p, _nfwd = forward_pnl(con, o["token_address"],
                               float(o["alert_timestamp"]), ep, eliq,
                               o["max_multiple"])
        if p is None:
            continue
        n_label += 1
        X.append(F.extract(row))
        pnl.append(p)
        touch.append(1 if (o["max_multiple"] or 0) >= RUN_MULT else 0)
        score.append(float(row.get("score") or 0))
    return X, pnl, touch, score, n_match, n_label


def topk(rank_scores, pnl, frac):
    order = sorted(range(len(rank_scores)), key=lambda i: rank_scores[i], reverse=True)
    k = max(1, int(len(rank_scores) * frac))
    top = order[:k]
    tot = sum(pnl[i] for i in top)
    wins = sum(1 for i in top if pnl[i] > 0)
    return k, tot, tot / k, 100 * wins / k


def main():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    X, pnl, touch, score, n_match, n_label = build(con)
    n = len(X)
    if n < 40:
        print(f"not enough labelable rows (n={n}); stopping.")
        return
    y_profit = [1 if p > 0 else 0 for p in pnl]
    print(f"matched-snapshot alerts: {n_match} | PnL-labelable (has forward path): {n_label}")
    print(f"profitable under new engine: {sum(y_profit)}/{n} "
          f"({100*sum(y_profit)/n:.1f}%) | touched 2x: {sum(touch)}/{n} "
          f"({100*sum(touch)/n:.1f}%)")
    print(f"take-ALL realized PnL: ${sum(pnl):.2f}  (mean ${sum(pnl)/n:+.2f}/alert, "
          f"size ${SIZE_USD:.0f})")

    oos_pnl = kfold_oos(X, y_profit)   # PnL-trained (predict profit), OOS
    oos_touch = kfold_oos(X, touch)    # current touch-2x model, OOS

    print(f"\nOOS AUC for predicting PROFIT: "
          f"pnl-model {roc_auc(oos_pnl, y_profit):.3f} | "
          f"touch-model {roc_auc(oos_touch, y_profit):.3f} | "
          f"score {roc_auc(score, y_profit):.3f}")

    print("\nThe money test — top-K realized PnL by selector:")
    header = f"{'selection':>12} | {'pnl-model':>20} | {'touch-model':>20} | {'score':>20}"
    print(header)
    print("-" * len(header))
    for frac in (0.10, 0.20, 0.30, 0.50):
        kp, tp, ap, wp = topk(oos_pnl, pnl, frac)
        _, tt, at, wt = topk(oos_touch, pnl, frac)
        _, ts, a_s, ws = topk(score, pnl, frac)
        tag = f"top {int(frac*100)}% (n={kp})"
        print(f"{tag:>12} | ${tp:>7.2f} ({ap:+.2f},{wp:.0f}%w) | "
              f"${tt:>7.2f} ({at:+.2f},{wt:.0f}%w) | ${ts:>7.2f} ({a_s:+.2f},{ws:.0f}%w)")
    print("\n($ = total realized PnL of the selected alerts; "
          "(/pick, win%) in parens. Higher = the selector picks more profitable alerts.)")


if __name__ == "__main__":
    main()
