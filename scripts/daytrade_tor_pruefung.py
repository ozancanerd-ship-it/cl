#!/usr/bin/env python3
"""Nachtrag 12.10.2026: Pruefung des "Lohnt sich"-Tors fuer Tageshandel-Alarme.

Ozan, 12.10.: "Benachrichtigung fuer Entry ... aber nur wenn es sich lohnt". Teilt die
Welle-Trades (M2B) nach Kosten in R, Ueberhang ueber dem 48-Std.-Hoch und Wellenbreite auf,
jeweils IS/OOS. Ergebnis: docs/DAYTRADE-STUDIE-2026-10.md Abschnitt "Lohnt-sich-Tor".

    python3 scripts/daytrade_tor_pruefung.py   (braucht den Cache /tmp/bv1h der Studie)
"""

# ruff: noqa: E402
from __future__ import annotations

import bisect
import statistics
import sys
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

_W = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_W / "scripts"))
sys.path.insert(0, str(_W / "src"))

import daytrade_studie as st

from trading_agent.strategy.setups import daytrade_ausbruch as m

cache = Path("/tmp/bv1h")
daten = {s: st.lade(s, cache) for s in [*st.COINS, st.GOLD]}
btc = daten["BTC"]
e_btc = m.ema([x[4] for x in btc], 200)
btc_ok = {x[0]: (e_btc[i] is not None and x[4] > e_btc[i]) for i, x in enumerate(btc)}
roh, kand = [], []
for s, k in daten.items():
    gold = s == st.GOLD
    e = m.ema([x[4] for x in k], 200)
    a = m.atr(k, 24)
    frei = 0
    kost = st.KOSTEN["gold" if gold else "coin"]
    for i in range(200, len(k) - 1):
        sig = m.ist_signal(k, i, e, markt_ok=None if gold else btc_ok.get(k[i][0], False))
        if sig and not gold:
            roh.append((k[i][0], s))
        if i < frei or not sig or not a[i]:
            continue
        ein = k[i + 1][1]
        stop = ein - 2 * a[i]
        res = m.verfolge(k, i + 1, stop, kosten=kost)
        if res["status"] != "geschlossen":
            continue
        hoch48 = max(x[2] for x in k[i - 48 : i])
        kand.append(
            dict(
                t=k[i][0],
                s=s,
                r=res["r"],
                risk=(ein - stop) / ein,
                kostR=kost * ein / (ein - stop),
                ueber=(k[i][4] - hoch48) / a[i],
                gold=gold,
            )
        )
        frei = res["i_aus"] + 1
roh.sort()
zeiten = [t for t, _ in roh]


def breite(t):
    lo = bisect.bisect_left(zeiten, t - timedelta(hours=24))
    hi = bisect.bisect_right(zeiten, t)
    return len({x for _, x in roh[lo:hi]})


for c in kand:
    c["welle"] = breite(c["t"]) if not c["gold"] else None


def zeig(titel, liste):
    for teil, f in (("IS", lambda c: c["t"] < st.OOS), ("OOS", lambda c: c["t"] >= st.OOS)):
        x = [c["r"] for c in liste if f(c)]
        print(
            f"  {titel:34s} {teil:3s} n={len(x):5d} schnitt={statistics.mean(x) if x else float('nan'):+.3f}R"
        )


W = [c for c in kand if not c["gold"] and c["welle"] >= 12]
G = [c for c in kand if c["gold"]]
print("WELLE (>=12) nach Kosten in R (= Risiko-Abstand)")
for lo, hi in ((0, 0.1), (0.1, 0.2), (0.2, 0.35), (0.35, 9)):
    zeig(f"KostenR {lo}-{hi}", [c for c in W if lo <= c["kostR"] < hi])
print("WELLE nach Ueberhang ueber 48h-Hoch (in ATR)")
for lo, hi in ((0, 0.25), (0.25, 0.5), (0.5, 1), (1, 99)):
    zeig(f"ueber {lo}-{hi} ATR", [c for c in W if lo <= c["ueber"] < hi])
print("WELLE nach Breite")
for lo, hi in ((12, 16), (16, 20), (20, 25)):
    zeig(f"welle {lo}-{hi}", [c for c in W if lo <= c["welle"] < hi])
print("GOLD (PAXG) gesamt / nach KostenR")
zeig("gold alle", G)
for lo, hi in ((0, 0.1), (0.1, 0.2), (0.2, 9)):
    zeig(f"gold KostenR {lo}-{hi}", [c for c in G if lo <= c["kostR"] < hi])
zeig("WELLE alle", W)
print("\n==== Vertiefung Ueberhang-Tor")
for schw in (0.1, 0.15, 0.25, 0.35, 0.5):
    zeig(f"WELLE + ueber>={schw}", [c for c in W if c["ueber"] >= schw])
F = [c for c in W if c["ueber"] >= 0.25]
oos = [c["r"] for c in F if c["t"] >= st.OOS]
print(
    "bootstrap5 OOS gefiltert",
    st.bootstrap_5(oos),
    "ungefiltert",
    st.bootstrap_5([c["r"] for c in W if c["t"] >= st.OOS]),
)

je = defaultdict(list)
for c in F:
    if c["t"] >= st.OOS:
        je[c["s"]].append(c["r"])
print(
    "coins OOS im plus gefiltert:",
    sum(1 for v in je.values() if statistics.mean(v) > 0),
    "von",
    len(je),
)
jahr = defaultdict(list)
jahr0 = defaultdict(list)
for c in F:
    jahr[c["t"].year].append(c["r"])
for c in W:
    jahr0[c["t"].year].append(c["r"])
for y in sorted(jahr):
    print(
        y,
        f"gefiltert n={len(jahr[y])} {statistics.mean(jahr[y]):+.3f}  alle n={len(jahr0[y])} {statistics.mean(jahr0[y]):+.3f}",
    )
print("GOLD nach Ueberhang")
for lo, hi in ((0, 0.25), (0.25, 99)):
    zeig(f"gold ueber {lo}-{hi}", [c for c in G if lo <= c["ueber"] < hi])
print("ALLE coin-Ausbrueche OHNE Welle nach Ueberhang (Gegenprobe)")
ohne = [c for c in kand if not c["gold"] and c["welle"] < 12]
for lo, hi in ((0, 0.25), (0.25, 99)):
    zeig(f"ohneWelle ueber {lo}-{hi}", [c for c in ohne if lo <= c["ueber"] < hi])
