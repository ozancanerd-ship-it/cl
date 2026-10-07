#!/usr/bin/env python3
"""Ausstiegs-Studie 2 — Plan gegen Trailing-Stop auf denselben Kurswegen.

    python3 scripts/ausstieg_trailing_studie.py --lauf DIR --npz DIR [--json OUT]

Regeln, Varianten und Entscheidung stehen VORAB in docs/AUSSTIEG-TRAILING-STUDIE-2026-10.md.
Eingabe ist ``wachen.json`` aus ``signal_replay.py wachliste`` (mit ``einstieg_um``).
"""

from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np

KOSTEN_PCT = 0.5
HORIZONT = timedelta(days=10)
OOS_AB = datetime(2026, 1, 1, tzinfo=UTC)
VARIANTEN = ("X0", "X2", "X3", "X4")


class Kerzen:
    def __init__(self, f: Path) -> None:
        z = np.load(f)
        self.t = z["t"].astype(np.int64)
        self.o, self.h, self.l, self.c = z["o"], z["h"], z["l"], z["c"]

    def ab(self, start: datetime, ende: datetime) -> range:
        a = int(np.searchsorted(self.t, int(start.timestamp() * 1000), side="left"))
        b = int(np.searchsorted(self.t, int(ende.timestamp() * 1000), side="left"))
        return range(a, b)


def simuliere(w: dict[str, Any], k: Kerzen, variante: str) -> float | None:
    """Ergebnis in R nach Kosten. Long und Short ueber das Vorzeichen ``s``."""
    if w.get("einstiegskurs") is None or not w.get("einstieg_um"):
        return None
    lang = w.get("richtung") == "long"
    s = 1.0 if lang else -1.0
    e = float(w["einstiegskurs"])
    stop0 = float(w["stop"])
    risiko = abs(float(w["einstieg"]) - stop0)
    if risiko <= 0:
        return None
    tp = [w.get("tp1"), w.get("tp2"), w.get("tp3")]
    tp = [float(x) for x in tp if x is not None]
    if not tp:
        return None
    kosten = (KOSTEN_PCT / 100.0) * e / risiko

    def r(kurs: float) -> float:
        return s * (kurs - e) / risiko

    t0 = datetime.fromisoformat(w["einstieg_um"])
    idx = k.ab(t0, t0 + HORIZONT)
    if len(idx) == 0:
        return None

    rest = 1.0
    ergebnis = 0.0
    stop = stop0
    bestes = e
    ziel1 = False
    # Feste Ziele je Variante: Liste (Kurs, Anteil vom Anfang)
    if variante == "X0":
        anteil = 1.0 / len(tp)
        ziele = [(x, anteil) for x in tp]
        trail = None
    elif variante in ("X2", "X3"):
        ziele = [(tp[0], 1.0 / 3.0)]
        trail = 1.0 if variante == "X2" else 1.5
    elif variante == "X4":
        ziele = [(tp[0], 0.5)]
        trail = 1.0
    else:
        raise ValueError(variante)
    offen_ziele = list(ziele)

    for i in idx:
        o, h, lo = float(k.o[i]), float(k.h[i]), float(k.l[i])
        guenstig = h if lang else lo  # bester Kurs der Kerze
        unguenstig = lo if lang else h
        # 1. Stop zuerst (pessimistisch), auch wenn die Kerze jenseits eroeffnet.
        if s * (unguenstig - stop) <= 0:
            ausfuehrung = o if s * (o - stop) <= 0 else stop
            ergebnis += rest * r(ausfuehrung)
            return ergebnis - kosten
        # 2. Ziele
        while offen_ziele and s * (guenstig - offen_ziele[0][0]) >= 0:
            kurs, teil = offen_ziele.pop(0)
            teil = min(teil, rest)
            ergebnis += teil * r(kurs)
            rest -= teil
            if not ziel1:
                ziel1 = True
                stop = e if s * (e - stop) > 0 else stop  # auf Einstand
            elif variante == "X0":
                # nach Ziel 2 auf Ziel 1
                stop = tp[0]
        if rest <= 1e-9:
            return ergebnis - kosten
        # 3. Trailing (erst nach Ziel 1), auf Basis des besten Kurses inkl. dieser Kerze —
        #    wirkt ab der naechsten Kerze.
        if s * (guenstig - bestes) > 0:
            bestes = guenstig
        if trail is not None and ziel1:
            neu = bestes - s * trail * risiko
            if s * (neu - stop) > 0:
                stop = neu
    # Horizont erreicht: Rest zum letzten Schluss
    ergebnis += rest * r(float(k.c[idx[-1]]))
    return ergebnis - kosten


def _monats_bootstrap(diffs: list[tuple[str, float]], n: int = 5000, seed: int = 7) -> tuple[float, float]:
    je_monat: dict[str, list[float]] = defaultdict(list)
    for m, d in diffs:
        je_monat[m].append(d)
    monate = list(je_monat)
    if len(monate) < 2:
        return (float("nan"), float("nan"))
    rnd = random.Random(seed)
    mittel = []
    for _ in range(n):
        werte: list[float] = []
        for _ in monate:
            werte.extend(je_monat[monate[rnd.randrange(len(monate))]])
        mittel.append(sum(werte) / len(werte))
    mittel.sort()
    return (mittel[int(0.05 * n)], mittel[int(0.95 * n)])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--lauf", required=True)
    ap.add_argument("--npz", required=True)
    ap.add_argument("--json")
    args = ap.parse_args()

    wachen = json.loads((Path(args.lauf) / "wachen.json").read_text(encoding="utf-8"))
    kerzen: dict[str, Kerzen] = {}
    faelle = []
    for w in wachen:
        if w.get("einstiegskurs") is None or not w.get("einstieg_um"):
            continue
        coin = str(w["instrument"]).removesuffix("USD")
        if coin not in kerzen:
            f = Path(args.npz) / f"{coin}_15m.npz"
            if not f.exists():
                continue
            kerzen[coin] = Kerzen(f)
        res = {v: simuliere(w, kerzen[coin], v) for v in VARIANTEN}
        if any(x is None for x in res.values()):
            continue
        auf = datetime.fromisoformat(w["aufgenommen"])
        faelle.append(
            {
                "instrument": w["instrument"],
                "aufgenommen": w["aufgenommen"],
                "monat": w["aufgenommen"][:7],
                "haelfte": "OOS" if auf >= OOS_AB else "IS",
                "richtung": w.get("richtung"),
                "setup": w.get("setup") or "",
                "note": w.get("note"),
                **res,
            }
        )

    def zeile(teil: list[dict[str, Any]], v: str) -> dict[str, Any]:
        n = len(teil)
        if not n:
            return {"n": 0}
        x = [f[v] for f in teil]
        d = [f[v] - f["X0"] for f in teil]
        gew = [y for y in x if y > 0]
        verl = [y for y in x if y < 0]
        return {
            "n": n,
            "E": round(sum(x) / n, 3),
            "summe": round(sum(x), 1),
            "treffer": round(len(gew) / n, 3),
            "pf": round(sum(gew) / abs(sum(verl)), 2) if verl else None,
            "diff": round(sum(d) / n, 3),
            "besser_in": round(sum(1 for y in d if y > 1e-9) / n, 3),
        }

    aus: dict[str, Any] = {"faelle": len(faelle), "tabellen": {}}
    gruppen = {
        "alle": lambda f: True,
        "long_setup": lambda f: f["richtung"] == "long" and bool(f["setup"]),
    }
    for gname, filt in gruppen.items():
        for h in ("IS", "OOS"):
            teil = [f for f in faelle if f["haelfte"] == h and filt(f)]
            aus["tabellen"][f"{gname}|{h}"] = {v: zeile(teil, v) for v in VARIANTEN}
    oos = [f for f in faelle if f["haelfte"] == "OOS"]
    aus["bootstrap_oos"] = {
        v: [round(x, 3) for x in _monats_bootstrap([(f["monat"], f[v] - f["X0"]) for f in oos])]
        for v in VARIANTEN
        if v != "X0"
    }

    # Entscheidung nach der vorab festgelegten Regel
    urteil = {}
    for v in VARIANTEN[1:]:
        t = aus["tabellen"]
        ok = (
            t["alle|IS"][v]["diff"] > 0
            and t["alle|OOS"][v]["diff"] > 0
            and t["alle|OOS"][v]["n"] >= 100
            and aus["bootstrap_oos"][v][0] > 0
            and t["long_setup|IS"][v].get("diff", -1) >= 0
            and t["long_setup|OOS"][v].get("diff", -1) >= 0
        )
        urteil[v] = ok
    sieger = [v for v, ok in urteil.items() if ok]
    sieger.sort(key=lambda v: -aus["tabellen"]["alle|OOS"][v]["diff"])
    aus["urteil"] = urteil
    aus["sieger"] = sieger[0] if sieger else None

    for k, tab in aus["tabellen"].items():
        print(f"\n== {k}")
        for v, z in tab.items():
            print(f"  {v}: {z}")
    print("\nBootstrap OOS (5 %, 95 %) des Unterschieds:", aus["bootstrap_oos"])
    print("Urteil:", urteil, "→ Sieger:", aus["sieger"])
    if args.json:
        Path(args.json).write_text(json.dumps({**aus, "liste": faelle}, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
