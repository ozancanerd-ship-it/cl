#!/usr/bin/env python3
"""Gold-Tageshandel (PAXG/USDT als Goldspur) — vorab festgelegte Studie.

Regeln stehen fest, BEVOR gerechnet wird (docs/GOLD-DAYTRADING-STUDIE-2026-10.md):
  Sessions (UTC): Asien 00–07, London 07–12, New York 12–21. Zeitausstieg 20:00.
  G1 Asien-Range-Ausbruch: erste 15-Min-Kerze in London (07–12), die ausserhalb der
     Asien-Range schliesst. Stop = Mitte der Range, Ziel 2 R.
  G2 Vortages-Sweep: 15-Min-Kerze (07–17) sticht ueber Vortageshoch (unter Vortagestief)
     und schliesst wieder innerhalb. Einstieg Schluss, Stop = Extrem der Kerze, Ziel 2 R.
  Je Tag, Setup und Richtung hoechstens ein Trade. Kosten 0,08 % je Runde (Gebuehr+Spread).
  Wochenende ausgeschlossen. IS bis 2025-06-30, OOS ab 2025-07-01.
  Annahme: Stop und Ziel in derselben Kerze -> Stop zuerst (pessimistisch).
Kein Parameter wird nach dem Ergebnis angepasst.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import random
import statistics
import zipfile
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

KOSTEN = 0.0008
OOS = datetime(2025, 7, 1, tzinfo=UTC)


def lade(pfad: Path) -> list[tuple[datetime, float, float, float, float]]:
    out = []
    for z in sorted(pfad.glob("PAXGUSDT-5m-*.zip")):
        with zipfile.ZipFile(z) as zf:
            for name in zf.namelist():
                for r in csv.reader(io.TextIOWrapper(zf.open(name))):
                    t = int(r[0])
                    if t > 10**14:
                        t //= 1000
                    out.append((datetime.fromtimestamp(t / 1000, UTC), float(r[1]), float(r[2]), float(r[3]), float(r[4])))
    out.sort()
    return out


def zu15(k5):
    b = defaultdict(list)
    for k in k5:
        t = k[0].replace(minute=k[0].minute // 15 * 15, second=0, microsecond=0)
        b[t].append(k)
    res = []
    for t in sorted(b):
        g = b[t]
        if len(g) < 3:
            continue
        res.append((t, g[0][1], max(x[2] for x in g), min(x[3] for x in g), g[-1][4]))
    return res


def trade(k, i, richtung, einstieg, stop, ziel_r=2.0):
    """Gibt R nach Kosten zurueck. Wertet ab Kerze i+1 bis 20:00 UTC desselben Tages."""
    risiko = abs(einstieg - stop)
    if risiko <= 0 or risiko / einstieg < 0.0008:
        return None
    ziel = einstieg + richtung * ziel_r * risiko
    tag = k[i][0].date()
    letzter = einstieg
    for j in range(i + 1, len(k)):
        t, o, h, l, c = k[j]
        if t.date() != tag or t.hour >= 20:
            break
        letzter = c
        hit_stop = l <= stop if richtung > 0 else h >= stop
        hit_ziel = h >= ziel if richtung > 0 else l <= ziel
        if hit_stop:
            r = -1.0
            return r - KOSTEN * einstieg / risiko
        if hit_ziel:
            return ziel_r - KOSTEN * einstieg / risiko
    r = richtung * (letzter - einstieg) / risiko
    return r - KOSTEN * einstieg / risiko


def studie(k):
    nach_tag = defaultdict(list)
    for idx, x in enumerate(k):
        nach_tag[x[0].date()].append(idx)
    tage = sorted(nach_tag)
    res = {"G1": [], "G2": []}
    for n, d in enumerate(tage):
        if d.weekday() >= 5 or n == 0:
            continue
        vor = tage[n - 1]
        ids = nach_tag[d]
        vor_ids = nach_tag[vor]
        if len(vor_ids) < 80 or len(ids) < 80:
            continue
        vh = max(k[i][2] for i in vor_ids)
        vl = min(k[i][3] for i in vor_ids)
        asien = [i for i in ids if k[i][0].hour < 7]
        if len(asien) < 20:
            continue
        ah, al = max(k[i][2] for i in asien), min(k[i][3] for i in asien)
        mitte = (ah + al) / 2
        gemacht = set()
        for i in ids:
            t, o, h, l, c = k[i]
            if 7 <= t.hour < 12 and ("G1", 1) not in gemacht and c > ah:
                gemacht.add(("G1", 1))
                r = trade(k, i, 1, c, mitte)
                if r is not None:
                    res["G1"].append((t, 1, r))
            if 7 <= t.hour < 12 and ("G1", -1) not in gemacht and c < al:
                gemacht.add(("G1", -1))
                r = trade(k, i, -1, c, mitte)
                if r is not None:
                    res["G1"].append((t, -1, r))
            if 7 <= t.hour < 17:
                if ("G2", -1) not in gemacht and h > vh and c < vh:
                    gemacht.add(("G2", -1))
                    r = trade(k, i, -1, c, h)
                    if r is not None:
                        res["G2"].append((t, -1, r))
                if ("G2", 1) not in gemacht and l < vl and c > vl:
                    gemacht.add(("G2", 1))
                    r = trade(k, i, 1, c, l)
                    if r is not None:
                        res["G2"].append((t, 1, r))
    return res


def kenn(werte):
    if not werte:
        return {"n": 0}
    return {"n": len(werte), "E_R": round(statistics.fmean(werte), 3), "trefferquote": round(sum(w > 0 for w in werte) / len(werte), 3)}


def boot(liste, n=5000, seed=7):
    monate = defaultdict(list)
    for t, _, r in liste:
        monate[(t.year, t.month)].append(r)
    if len(monate) < 3:
        return None
    ks = list(monate.values())
    rnd = random.Random(seed)
    m = []
    for _ in range(n):
        s = [r for _ in ks for r in rnd.choice(ks)]
        m.append(statistics.fmean(s))
    m.sort()
    return round(m[int(0.05 * n)], 3)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--daten", default="/root/gold")
    ap.add_argument("--json", default="")
    a = ap.parse_args()
    k = zu15(lade(Path(a.daten)))
    print(f"{len(k)} Kerzen (15 Min), {k[0][0]:%Y-%m-%d} bis {k[-1][0]:%Y-%m-%d}")
    res = studie(k)
    aus = {}
    for name, liste in res.items():
        for teil, f in (("alle", lambda x: True), ("long", lambda x: x[1] > 0), ("short", lambda x: x[1] < 0)):
            sub = [x for x in liste if f(x)]
            i = [x[2] for x in sub if x[0] < OOS]
            o = [x[2] for x in sub if x[0] >= OOS]
            b = boot([x for x in sub if x[0] >= OOS])
            aus[f"{name}-{teil}"] = {"IS": kenn(i), "OOS": kenn(o), "OOS_boot5": b}
            print(f"{name}-{teil:<6} IS {kenn(i)}  OOS {kenn(o)}  boot5%={b}")
    if a.json:
        Path(a.json).write_text(json.dumps(aus, indent=1))


if __name__ == "__main__":
    main()
