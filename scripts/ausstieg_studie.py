#!/usr/bin/env python3
"""Ausstiegs-Studie — raus bei gedrehter Analyse oder weiter nach Plan?

    python3 scripts/ausstieg_studie.py --lauf DIR --npz DIR [--out DATEI]

Regel und Entscheidung stehen VORAB in docs/AUSSTIEG-STUDIE-2026-10.md. ``--lauf`` ist der
Ausgabeordner der Signal-Studie (mit ``wachen.json`` aus Phase B), ``--npz`` die Kerzen.
"""

from __future__ import annotations

import argparse
import bisect
import json
import math
import random
import sys
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from signal_replay import KOSTEN_PCT, _r_trade

OOS_AB = datetime(2026, 1, 1, tzinfo=UTC)
HALTEN_MAX = timedelta(days=30)
BOOT_N = 5000
BOOT_SEED = 7


def _h1(npz: Path, coin: str) -> dict[str, list[Any]]:
    import numpy as np

    with np.load(npz / f"{coin}_1h.npz") as roh:
        d = {k: roh[k].tolist() for k in ("t", "o", "h", "l", "c")}
    d["zeit"] = [datetime.fromtimestamp(t / 1000.0, tz=UTC) for t in d["t"]]
    return d


def _coin(instrument: str) -> str:
    for q in ("USDT", "USD"):
        if instrument.endswith(q):
            return instrument[: -len(q)]
    return instrument


def halten(w: dict[str, Any], kerzen: dict[str, list[Any]]) -> float | None:
    """Der Plan laeuft ab dem Drehen weiter. Ergebnis in R nach Kosten (wie ``_r_trade``)."""
    lang = w.get("richtung") == "long"
    basis = float(w["einstiegskurs"])
    risiko = abs(float(w["einstieg"]) - float(w["stop"]))
    if risiko <= 0:
        return None

    def r_bei(kurs: float) -> float:
        return ((kurs - basis) if lang else (basis - kurs)) / risiko

    ziele = [(m, w.get(m.lower())) for m in ("TP1", "TP2", "TP3")]
    ziele = [(m, float(v)) for m, v in ziele if v is not None]
    if not ziele:
        return None
    anteil = 1.0 / len(ziele)
    erreicht = [m for m, _ in ziele if m in (w.get("erreicht") or [])]
    gewinn = sum(anteil * r_bei(v) for m, v in ziele if m in erreicht)
    rest = 1.0 - anteil * len(erreicht)
    offen = [(m, v) for m, v in ziele if m not in erreicht]
    # Stop laut Plan: nach Ziel 2 auf Ziel 1, nach Ziel 1 auf Einstand, sonst der Stop.
    tp = dict(ziele)
    if "TP2" in erreicht and "TP1" in tp:
        stop = tp["TP1"]
    elif "TP1" in erreicht:
        stop = basis
    else:
        stop = float(w["stop"])
    kosten = (KOSTEN_PCT / 100.0) * basis / risiko

    ab = datetime.fromisoformat(str(w["zuletzt"]))
    i = bisect.bisect_left(kerzen["zeit"], ab)
    ende = ab + HALTEN_MAX
    letzter = None
    while i < len(kerzen["zeit"]) and rest > 1e-9:
        if kerzen["zeit"][i] >= ende:
            break
        o, h, lo, c = (kerzen[k][i] for k in ("o", "h", "l", "c"))
        letzter = c
        # Stop zuerst (pessimistisch).
        gerissen = (lo <= stop) if lang else (h >= stop)
        if gerissen:
            jenseits = (o <= stop) if lang else (o >= stop)
            preis = o if jenseits else stop
            return gewinn + rest * r_bei(preis) - kosten
        for m, v in list(offen):
            getroffen = (h >= v) if lang else (lo <= v)
            if not getroffen:
                break
            gewinn += anteil * r_bei(v)
            rest -= anteil
            offen.remove((m, v))
            if m == "TP1":
                stop = basis
            elif m == "TP2" and "TP1" in tp:
                stop = tp["TP1"]
        i += 1
    if rest <= 1e-9:
        return gewinn - kosten
    if letzter is None:
        return None
    return gewinn + rest * r_bei(letzter) - kosten


def _monats_bootstrap(paare: list[tuple[str, float]]) -> tuple[float, float]:
    je: dict[str, list[float]] = defaultdict(list)
    for monat, v in paare:
        je[monat].append(v)
    monate = list(je.values())
    if len(monate) < 3:
        return (math.nan, math.nan)
    rnd = random.Random(BOOT_SEED)
    mittel = []
    for _ in range(BOOT_N):
        stich = [monate[rnd.randrange(len(monate))] for _ in monate]
        flach = [v for m in stich for v in m]
        mittel.append(sum(flach) / len(flach))
    mittel.sort()
    return (round(mittel[int(0.05 * BOOT_N)], 3), round(mittel[int(0.95 * BOOT_N)], 3))


def entscheide(e: dict[str, Any]) -> str:
    """Die Entscheidungsregel aus dem Dokument, woertlich."""
    di, do = e["IS"].get("unterschied"), e["OOS"].get("unterschied")
    n = e["OOS"].get("n", 0)
    q05, q95 = e["oos_bootstrap_90"]
    if di is None or do is None:
        return "bleibt — zu wenige Faelle"
    if di > 0 and do > 0 and n >= 30 and not math.isnan(q05) and q05 > 0:
        return "aendern — halten ist besser, Ausstiegs-Alarm bei gedrehter Analyse faellt"
    if di < 0 and do < 0 and not math.isnan(q95) and q95 < 0:
        return "bestaetigt — raus bei gedrehter Analyse ist besser"
    return "bleibt — Unterschied nicht belegbar"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lauf", required=True)
    ap.add_argument("--npz", required=True)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    wachen = json.loads((Path(args.lauf) / "wachen.json").read_text(encoding="utf-8"))
    faelle = [
        w
        for w in wachen
        if w.get("einstiegskurs") is not None
        and w.get("zustand") == "invalidiert"
        and w.get("ausstiegskurs") is not None
        and not w.get("raus")
    ]
    kerzen: dict[str, dict[str, list[Any]]] = {}
    zeilen = []
    for w in faelle:
        coin = _coin(str(w["instrument"]))
        if coin not in kerzen:
            kerzen[coin] = _h1(Path(args.npz), coin)
        r_raus = _r_trade(w, "plan")
        r_halten = halten(w, kerzen[coin])
        if r_raus is None or r_halten is None:
            continue
        auf = datetime.fromisoformat(str(w["aufgenommen"]))
        zeilen.append(
            {
                "instrument": w["instrument"],
                "richtung": w.get("richtung"),
                "aufgenommen": w["aufgenommen"],
                "gedreht": w["zuletzt"],
                "haelfte": "OOS" if auf >= OOS_AB else "IS",
                "r_raus": round(r_raus, 4),
                "r_halten": round(r_halten, 4),
            }
        )

    erg: dict[str, Any] = {"regel": "docs/AUSSTIEG-STUDIE-2026-10.md", "faelle_gesamt": len(faelle)}
    for h in ("IS", "OOS"):
        z = [x for x in zeilen if x["haelfte"] == h]
        if not z:
            erg[h] = {"n": 0}
            continue
        diff = [x["r_halten"] - x["r_raus"] for x in z]
        erg[h] = {
            "n": len(z),
            "raus_mittel": round(sum(x["r_raus"] for x in z) / len(z), 3),
            "halten_mittel": round(sum(x["r_halten"] for x in z) / len(z), 3),
            "unterschied": round(sum(diff) / len(diff), 3),
            "halten_besser_anteil": round(sum(1 for d in diff if d > 0) / len(diff), 3),
        }
    erg["oos_bootstrap_90"] = _monats_bootstrap(
        [
            (x["aufgenommen"][:7], x["r_halten"] - x["r_raus"])
            for x in zeilen
            if x["haelfte"] == "OOS"
        ]
    )
    for richtung in ("long", "short"):
        z = [x for x in zeilen if x["richtung"] == richtung]
        if z:
            erg[f"einordnung_{richtung}"] = {
                "n": len(z),
                "unterschied": round(sum(x["r_halten"] - x["r_raus"] for x in z) / len(z), 3),
            }
    erg["urteil"] = entscheide(erg)
    print(json.dumps(erg, indent=1, ensure_ascii=False))
    if args.out:
        Path(args.out).write_text(
            json.dumps({**erg, "faelle": zeilen}, ensure_ascii=False, indent=1), encoding="utf-8"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
