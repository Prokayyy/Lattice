"""Forward validation of the deployed pnl conviction model (run biweekly).

Reads discovery/discovery_outcomes.jsonl (sent-alert candidates + realized PnL
under the live exit engine), restricted to the deployed model's era, and checks:
  1. Are the alerts we send actually profitable?  (total / mean-per-alert / win%)
  2. Does the deployed model's conviction score still RANK realized PnL on the
     live sent set?  (top vs bottom conviction quartile)
Writes a dated JSON summary, prints a PASS / WARN verdict, and — on WARN — pings
Telegram so you don't have to read the log. Read-only re: the live model/runner.

Run:   env/bin/python -u analysis/pnl_model_live_validation.py [--days 14]
Test:  env/bin/python -u analysis/pnl_model_live_validation.py --test   # send a test ping

Telegram: defaults to the IGNITION_SUMMARY chat (not the relayed main alert chat).
Override with env LATTICE_VALIDATION_CHAT_ID. Set LATTICE_VALIDATION_NOTIFY_PASS=1
to also ping on PASS.
"""
import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)          # so `import config` works regardless of cwd
OUT = os.path.join(ROOT, "discovery", "discovery_outcomes.jsonl")
MODEL = os.path.join(ROOT, "discovery", "models", "conviction_ranker.json")
SUMMARY = os.path.join(ROOT, "analysis", "pnl_model_validation_summary.jsonl")
COOLDOWN_S = 2 * 3600          # approximate live ENTRY SIGNAL per-token dedup


def _f(v, d=0.0):
    try:
        return float(v if v is not None else d)
    except (TypeError, ValueError):
        return d


def send_telegram(text):
    """Sync send to the Bot API. Chat defaults to IGNITION_SUMMARY (not the main
    alert chat, which is relayed publicly). Returns True on success."""
    try:
        import config
        token = (getattr(config, "TELEGRAM_BOT_TOKEN", "") or "").strip()
        chat = (os.environ.get("LATTICE_VALIDATION_CHAT_ID")
                or str(getattr(config, "IGNITION_SUMMARY_CHAT_ID", "") or "")
                or str(getattr(config, "TELEGRAM_CHAT_ID", "") or "")).strip()
        if not token or not chat:
            print("telegram: not configured (no token/chat) — skipped")
            return False
        data = urllib.parse.urlencode({
            "chat_id": chat, "text": text, "disable_web_page_preview": "true",
        }).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage", data=data)
        with urllib.request.urlopen(req, timeout=15) as r:
            ok = (r.status == 200)
        print(f"telegram: sent to {chat} (ok={ok})")
        return ok
    except Exception as e:
        print(f"telegram: send failed: {e}")
        return False


def load_sent_alerts(since_ts):
    rows = []
    with open(OUT) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            if r.get("no_data") or _f(r.get("alert_ts")) < since_ts:
                continue
            rows.append({"token": r.get("token"), "ts": _f(r.get("alert_ts")),
                         "conv": _f(r.get("conviction")), "pnl": _f(r.get("realized_pnl")),
                         "mm1h": _f(r.get("max_mult_1h"))})
    rows.sort(key=lambda x: x["ts"])
    last, kept = {}, []
    for r in rows:                       # 2h per-token cooldown -> ~sent alerts
        if r["token"] in last and r["ts"] - last[r["token"]] < COOLDOWN_S:
            continue
        last[r["token"]] = r["ts"]
        kept.append(r)
    return kept


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=14.0)
    ap.add_argument("--test", action="store_true",
                    help="send a test Telegram nudge and exit")
    args = ap.parse_args()

    if args.test:
        ok = send_telegram("🔧 Lattice pnl-model validation: test nudge — wiring OK, "
                           "ignore. (Biweekly WARN alerts will arrive here.)")
        print("test ping ok" if ok else "test ping FAILED")
        return

    deploy_ts, label = 0.0, "unknown"
    try:
        m = json.load(open(MODEL))
        label = m.get("label", "unknown")
        if m.get("trained_at"):
            deploy_ts = datetime.fromisoformat(m["trained_at"]).timestamp()
    except Exception:
        pass
    since = max(time.time() - args.days * 86400, deploy_ts)
    alerts = load_sent_alerts(since)
    n = len(alerts)
    now_iso = datetime.now(timezone.utc).isoformat()

    if n < 20:
        verdict = "INSUFFICIENT"
        summary = {"iso": now_iso, "model": label, "n": n, "verdict": verdict}
        line = f"{now_iso} model={label} n={n} verdict={verdict} (need >=20)"
    else:
        pnls = [a["pnl"] for a in alerts]
        total = sum(pnls)
        mean = total / n
        win = 100 * sum(1 for p in pnls if p > 0) / n
        ge2 = 100 * sum(1 for a in alerts if a["mm1h"] >= 2.0) / n
        s = sorted(alerts, key=lambda a: a["conv"])
        q = len(s) // 4
        bot_mean = sum(a["pnl"] for a in s[:q]) / max(q, 1)
        top_mean = sum(a["pnl"] for a in s[-q:]) / max(q, 1)
        verdict = "PASS" if (mean > 0 and top_mean >= bot_mean) else "WARN"
        summary = {"iso": now_iso, "model": label, "days": args.days, "n": n,
                   "total_pnl": round(total, 2), "mean_pnl": round(mean, 3),
                   "win_pct": round(win, 1), "ge2_1h_pct": round(ge2, 1),
                   "conv_top_q_mean": round(top_mean, 3),
                   "conv_bot_q_mean": round(bot_mean, 3), "verdict": verdict}
        line = (f"{now_iso} model={label} n={n} mean=${mean:+.3f} win={win:.1f}% "
                f"top_q=${top_mean:+.3f} bot_q=${bot_mean:+.3f} -> {verdict}")

    with open(SUMMARY, "a") as fh:
        fh.write(json.dumps(summary) + "\n")
    print(line)

    notify_pass = os.environ.get("LATTICE_VALIDATION_NOTIFY_PASS", "").lower() in ("1", "true", "yes")
    if verdict == "WARN":
        send_telegram(
            "⚠️ Lattice pnl-model validation: WARN\n"
            f"{summary['n']} sent alerts / {int(args.days)}d are not net-profitable "
            "and/or conviction no longer ranks PnL.\n"
            f"mean ${summary['mean_pnl']:+}/alert · win {summary['win_pct']}% · "
            f"top-q ${summary['conv_top_q_mean']:+} vs bot-q ${summary['conv_bot_q_mean']:+}\n"
            "Action: re-check uncensored deployable_pnl; consider raising "
            "min_conviction (0.34≈2x, 0.36≈1.6x vol) or rolling back.")
    elif verdict == "PASS" and notify_pass:
        send_telegram(
            f"✅ Lattice pnl-model validation: PASS — {summary['n']} alerts/{int(args.days)}d, "
            f"mean ${summary['mean_pnl']:+}/alert, win {summary['win_pct']}%.")


if __name__ == "__main__":
    main()
