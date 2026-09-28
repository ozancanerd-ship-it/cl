#!/usr/bin/env python3
"""Funding-Studie — vorab registriert in docs/FUNDING-STUDIE-2026-09.md.

Erwartet je Coin ``$BT/funding/<COIN>.json`` (Kraken historicalfundingrates, Feld
``rates``) und ``$BT/npz/<COIN>_1d.npz``. Aufruf aus ``$BT/funding``."""
import json, glob, os
import numpy as np
from datetime import datetime, timezone, timedelta, date
COINS = "BTC ETH SOL XRP ADA DOGE LINK AVAX DOT LTC BCH ATOM NEAR UNI AAVE INJ SEI ARB OP SUI FET HBAR XLM TAO".split()
# Finanzierung je Tag (Mittel der Stundensaetze des Tages), aufs Jahr
fund = {}
for c in COINS:
    je_tag = {}
    for r in json.load(open(f"{c}.json")):
        t = datetime.fromisoformat(r["timestamp"].replace("Z", "+00:00"))
        je_tag.setdefault(t.date(), []).append(float(r["relativeFundingRate"]))
    fund[c] = {d: np.mean(v) * 8760 * 100 for d, v in je_tag.items() if len(v) >= 12}
# Schlusskurse
kurs = {}
for c in COINS:
    z = np.load(os.environ.get("BT", "/tmp/claude-0/bt") + f"/npz/{c}_1d.npz")
    kurs[c] = {datetime.fromtimestamp(t / 1000, tz=timezone.utc).date(): float(cl) for t, cl in zip(z["t"], z["c"])}
tage = sorted(set.intersection(*[set(fund[c]) for c in COINS]) & set.intersection(*[set(kurs[c]) for c in COINS]))
tage = [d for d in tage]
print("Tage mit allen Daten:", len(tage), tage[0], tage[-1])
def f7(c, d):
    w = [fund[c].get(d - timedelta(days=i)) for i in range(7)]
    w = [x for x in w if x is not None]
    return np.mean(w) if len(w) >= 5 else None
def ren(c, d, h):
    a, b = kurs[c].get(d), kurs[c].get(d + timedelta(days=h))
    return (b / a - 1) * 100 if a and b else None
stichtage = [d for i, d in enumerate(tage) if i >= 7 and i % 7 == 0 and d + timedelta(days=14) <= tage[-1]]
mitte = len(stichtage) // 2
erg = {}
for H in (7, 14):
    for teil, liste in (("IS", stichtage[:mitte]), ("OOS", stichtage[mitte:])):
        q = {k: [] for k in range(5)}; heiss, rest = [], []
        for d in liste:
            werte = [(c, f7(c, d), ren(c, d, H)) for c in COINS]
            werte = [(c, f, r) for c, f, r in werte if f is not None and r is not None]
            if len(werte) < 15: continue
            m = np.mean([r for _, _, r in werte])
            werte.sort(key=lambda x: x[1])
            n = len(werte)
            for i, (c, f, r) in enumerate(werte):
                q[min(4, i * 5 // n)].append(r - m)
                (heiss if f >= 50 else rest).append(r - m)
        erg[(H, teil)] = {"n_tage": len(liste), "quintile": [round(float(np.mean(q[k])), 2) for k in range(5)],
                          "spanne": round(float(np.mean(q[4]) - np.mean(q[0])), 2),
                          "heiss_n": len(heiss), "heiss": round(float(np.mean(heiss)), 2) if heiss else None,
                          "rest": round(float(np.mean(rest)), 2) if rest else None}
        print(H, teil, erg[(H, teil)])
ok = all(erg[(7, t)]["spanne"] <= -1.0 and erg[(7, t)]["quintile"][4] < 0 and erg[(7, t)]["n_tage"] >= 30 for t in ("IS", "OOS"))
print("Entscheidung:", "UEBERNEHMEN" if ok else "NICHT uebernehmen (nur Kontext)")
json.dump({f"{h}_{t}": v for (h, t), v in erg.items()} | {"entscheidung": ok}, open("ergebnis.json", "w"), indent=1)
