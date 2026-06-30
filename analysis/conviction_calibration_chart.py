"""Render the conviction-vs-actual-2x calibration as a bar chart + send to TG.

Bars = actual >=2x rate per conviction decile; dashed line = base rate. Flat
bars (no rise across deciles) = the conviction has no discriminating power.

  env/bin/python analysis/conviction_calibration_chart.py [--no-send] [--out path]
"""
import argparse
import asyncio
import io
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from analysis.conviction_vs_2x import DB, auc, load, peak_after  # noqa: E402

POST = 86400
RUNNER = 2.0
NBINS = 10
BG = (13, 17, 23)
GRID = (30, 36, 44)
UP = (61, 214, 140)
DOWN = (240, 95, 95)
POCC = (242, 201, 92)
TEXT = (150, 162, 176)
TITLE = (236, 241, 246)


def compute():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    data = []
    for ts, token, ep, conv in load():
        if ep <= 0:
            continue
        peak = peak_after(con, token, ts, POST)
        if peak is None or peak <= 0:
            continue
        data.append((conv, 1 if peak / ep >= RUNNER else 0))
    con.close()
    data.sort(key=lambda d: d[0])
    n = len(data)
    base = sum(d[1] for d in data) / n
    au = auc([d[0] for d in data], [d[1] for d in data])
    bins = []
    for q in range(NBINS):
        seg = data[q * n // NBINS:(q + 1) * n // NBINS]
        bins.append((sum(s[0] for s in seg) / len(seg),
                     sum(s[1] for s in seg) / len(seg), len(seg)))
    return bins, base, n, au


def _font(name, size):
    from PIL import ImageFont
    try:
        return ImageFont.truetype(
            f"/usr/share/fonts/truetype/dejavu/{name}.ttf", size)
    except Exception:
        return ImageFont.load_default()


def render(bins, base, n, au):
    from PIL import Image, ImageDraw
    W, H = 900, 480
    ML, MR, MT, MB = 56, 18, 58, 52
    pl, pr, pt, pb = ML, W - MR, MT, H - MB
    pw, ph = pr - pl, pb - pt
    ymax = max(max(b[1] for b in bins), base) * 1.18 or 0.1
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    ft, fl, fs = (_font("DejaVuSans-Bold", 21), _font("DejaVuSans", 13),
                  _font("DejaVuSans", 11))

    def yy(r):
        return pt + ph * (1 - r / ymax)

    for k in range(5):
        gy = pt + ph * k / 4
        d.line([(pl, gy), (pr, gy)], fill=GRID, width=1)
        d.text((6, gy - 7), f"{ymax * (1 - k / 4) * 100:.0f}%", font=fs, fill=TEXT)

    bw = pw / len(bins)
    for i, (cm, rate, cnt) in enumerate(bins):
        x0, x1 = pl + i * bw + bw * 0.16, pl + (i + 1) * bw - bw * 0.16
        col = UP if rate >= base else DOWN
        d.rectangle([x0, yy(rate), x1, pb], fill=col)
        d.text(((x0 + x1) / 2 - 11, yy(rate) - 15), f"{rate * 100:.0f}%",
               font=fs, fill=col)
        d.text(((x0 + x1) / 2 - 13, pb + 6), f"{cm * 100:.0f}%", font=fs, fill=TEXT)

    by = yy(base)
    x = pl
    while x < pr:
        d.line([(x, by), (min(x + 9, pr), by)], fill=POCC, width=2)
        x += 16
    d.text((pr - 96, by - 17), f"base {base * 100:.1f}%", font=fl, fill=POCC)

    d.text((ML, 12), "Conviction vs actual ≥2x", font=ft, fill=TITLE)
    tx = ML + d.textlength("Conviction vs actual ≥2x", font=ft) + 14
    d.text((tx, 18), f"AUC {au:.3f}  ≈ coin flip", font=fl, fill=DOWN)
    d.text((pl, H - 20),
           f"conviction decile, low → high  ·  bar = actual ≥2x rate  ·  n={n}",
           font=fs, fill=TEXT)
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


async def send(png, base, au, n):
    from discovery.notify import LatticeNotifier
    cap = (f"📊 <b>Conviction vs actual ≥2x</b>\n"
           f"AUC <b>{au:.3f}</b> (0.50 = coin flip) · base ≥2x rate "
           f"<b>{base * 100:.1f}%</b> · n={n}\n"
           f"Bars don't rise with conviction → the P(≥2x) score has "
           f"<b>no ranking power</b>; use it as a rough gate, not for sizing.")
    return await LatticeNotifier()._send_photo(png, cap)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-send", action="store_true")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    bins, base, n, au = compute()
    png = render(bins, base, n, au)
    if args.out:
        open(args.out, "wb").write(png)
        print("saved", args.out, len(png), "bytes")
    if not args.no_send:
        sent = asyncio.run(send(png, base, au, n))
        print("sent to", sent, "chat(s)")


if __name__ == "__main__":
    main()
