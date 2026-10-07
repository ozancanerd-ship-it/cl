#!/usr/bin/env python3
"""Mean Reversion bei Coins (1 h) — vorab festgelegte Studie, siehe docs/MEAN-REVERSION-STUDIE-2026-10.md

Regeln VOR dem Lauf:
  Signal Long : 1-h-Schluss unter Bollinger(20, 2) UND RSI(14) < 30.   Short spiegelbildlich (RSI > 70).
  Einstieg    : Eroeffnung der naechsten Kerze.  Stop: 1,5 x ATR(14).  Ziel: Bollinger-Mitte (zum Signal).
  Zeitausstieg: 24 Kerzen.  Je Coin hoechstens eine Position.  Kosten 0,25 % je Runde.
  Variante R  : nur bei Seitwaertsregime, Kaufman-Effizienz(120 h) < 0,15.
  IS bis 2025-12-31, OOS ab 2026-01-01.  Stop und Ziel in einer Kerze -> Stop zuerst.
"""
from __future__ import annotations

import json
import random
import statistics
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

KOSTEN = 0.0025
OOS_MS = int(datetime(2026, 1, 1, tzinfo=UTC).timestamp() * 1000)


def rsi(c, n=14):
    d = np.diff(c, prepend=c[0])
    up, dn = np.clip(d, 0, None), np.clip(-d, 0, None)
    a = np.zeros_like(c); b = np.zeros_like(c)
    a[n] = up[1:n + 1].mean(); b[n] = dn[1:n + 1].mean()
    for i in range(n + 1, len(c)):
        a[i] = (a[i - 1] * (n - 1) + up[i]) / n
        b[i] = (b[i - 1] * (n - 1) + dn[i]) / n
    rs = np.where(b == 0, 100, a / np.where(b == 0, 1, b))
    out = 100 - 100 / (1 + rs)
    out[:n] = 50
    return out


def atr(h, l, c, n=14):
    tr = np.maximum(h - l, np.maximum(abs(h - np.roll(c, 1)), abs(l - np.roll(c, 1))))
    tr[0] = h[0] - l[0]
    a = np.zeros_like(c); a[n - 1] = tr[:n].mean()
    for i in range(n, len(c)):
        a[i] = (a[i - 1] * (n - 1) + tr[i]) / n
    return a


def coin(pfad):
    d = np.load(pfad)
    t, o, h, l, c = (d[k].astype(float) for k in "tohlc")
    n = len(c)
    ma = np.full(n, np.nan); sd = np.full(n, np.nan)
    for i in range(19, n):
        w = c[i - 19:i + 1]; ma[i] = w.mean(); sd[i] = w.std()
    r = rsi(c); a = atr(h, l, c)
    er = np.full(n, np.nan)
    for i in range(120, n):
        pfadlang = np.abs(np.diff(c[i - 120:i + 1])).sum()
        er[i] = abs(c[i] - c[i - 120]) / pfadlang if pfadlang else 1
    res = []
    i = 130
    while i < n - 2:
        for rich in (1, -1):
            sig = (c[i] < ma[i] - 2 * sd[i] and r[i] < 30) if rich > 0 else (c[i] > ma[i] + 2 * sd[i] and r[i] > 70)
            if not sig:
                continue
            e = o[i + 1]; stop = e - rich * 1.5 * a[i]; ziel = ma[i]
            if (ziel - e) * rich <= 0 or (e - stop) * rich <= 0:
                continue
            risiko = abs(e - stop)
            ende = min(n - 1, i + 1 + 24); exit_ = c[ende]; j_end = ende
            for j in range(i + 1, ende + 1):
                hs = l[j] <= stop if rich > 0 else h[j] >= stop
                hz = h[j] >= ziel if rich > 0 else l[j] <= ziel
                if hs: exit_, j_end = stop, j; break
                if hz: exit_, j_end = ziel, j; break
            rr = rich * (exit_ - e) / risiko - KOSTEN * e / risiko
            res.append((int(t[i]), rich, rr, bool(er[i] < 0.15)))
            i = j_end
            break
        i += 1
    return res


def boot(w, n=5000, seed=7):
    m = defaultdict(list)
    for t, _, r, _ in w:
        d = datetime.fromtimestamp(t / 1000, UTC); m[(d.year, d.month)].append(r)
    if len(m) < 3: return None
    ks = list(m.values()); rnd = random.Random(seed); s = []
    for _ in range(n):
        x = [r for _ in ks for r in rnd.choice(ks)]; s.append(statistics.fmean(x))
    s.sort(); return round(s[int(.05 * n)], 3)


def main():
    npz = Path(sys.argv[1] if len(sys.argv) > 1 else "/root/replay/npz")
    alle = []
    for f in sorted(npz.glob("*_1h.npz")):
        alle += coin(f)
    out = {}
    for name, flt in (("alle", lambda x: True), ("long", lambda x: x[1] > 0), ("short", lambda x: x[1] < 0),
                      ("R-alle", lambda x: x[3]), ("R-long", lambda x: x[3] and x[1] > 0)):
        s = [x for x in alle if flt(x)]
        i = [x[2] for x in s if x[0] < OOS_MS]; o = [x[2] for x in s if x[0] >= OOS_MS]
        k = lambda v: {"n": len(v), "E_R": round(statistics.fmean(v), 3) if v else None,
                       "treffer": round(sum(a > 0 for a in v) / len(v), 3) if v else None}
        b = boot([x for x in s if x[0] >= OOS_MS])
        out[name] = {"IS": k(i), "OOS": k(o), "OOS_boot5": b}
        print(f"{name:<8} IS {k(i)}  OOS {k(o)}  boot5%={b}")
    if len(sys.argv) > 2:
        Path(sys.argv[2]).write_text(json.dumps(out, indent=1))


main()
