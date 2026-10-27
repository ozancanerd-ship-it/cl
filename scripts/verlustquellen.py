#!/usr/bin/env python3
"""Verlustquellen der Forward-Paper-Trades (nur Lesen, aendert kein Systemverhalten).

Baut auf trade_analyse.laden() auf (gleiche 695 Trades wie die Bilanz) und stellt die Frage
NEU, die dort fehlt: WO genau entsteht der Verlust, und welcher Teil davon ist ein
Buchungs-/Messartefakt statt eines Handelsverlusts?

Jede Gruppe: n, Summe R, Mittel R mit 95-%-Intervall (Normalnaeherung), Trefferquote, PF.
Gruppen mit n < 30 sind als "n<30" markiert und tragen keine Aussage.

Aufruf: python scripts/verlustquellen.py [--json out.json]
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))
import trade_analyse as ta  # noqa: E402

MIN_N = 30
#: Start == Ende (identischer Zeitstempel): die Wache wurde im selben Lauf aufgenommen und
#: gestoppt. MFE 0, kein einziger Kurs im Plus gesehen.
SOFORT_H = 0.01


def ist_sofort(z: dict) -> bool:
    return z["dauer_h"] is not None and z["dauer_h"] <= SOFORT_H and z["zustand"] == "stop"


def kz(rows: list[dict], key: str = "r") -> dict:
    v = [x[key] for x in rows if x.get(key) is not None]
    n = len(v)
    if n == 0:
        return {"n": 0}
    m = sum(v) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in v) / (n - 1)) if n > 1 else 0.0
    ci = 1.96 * sd / math.sqrt(n) if n > 1 else None
    gew = sum(x for x in v if x > 0)
    verl = -sum(x for x in v if x < 0)
    return {
        "n": n,
        "summe": round(sum(v), 1),
        "mittel": round(m, 3),
        "ci95": round(ci, 3) if ci is not None else None,
        "treffer": round(sum(1 for x in v if x > 0) / n, 3),
        "pf": round(gew / verl, 2) if verl else None,
        "aussage": "n<30" if n < MIN_N else ("Mittel signifikant <0" if m + ci < 0 else "Mittel signifikant >0" if m - ci > 0 else "nicht von 0 unterscheidbar"),
    }


def gruppiere(rows: list[dict], f) -> dict:
    g: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        g[str(f(r))].append(r)
    return {k: kz(v) for k, v in sorted(g.items(), key=lambda kv: -len(kv[1]))}


def kalenderwoche(z: dict) -> str:
    d = datetime.fromisoformat(z["begonnen"])
    j, w, _ = d.isocalendar()
    return f"{j}-KW{w:02d}"


def analyse() -> dict:
    alle = ta.laden()
    sofort = [z for z in alle if ist_sofort(z)]
    rest = [z for z in alle if not ist_sofort(z)]
    out = {
        "alle": kz(alle),
        "sofort_artefakt": {**kz(sofort), "nach_klasse": gruppiere(sofort, lambda z: z["klasse"]),
                            "nach_tag": dict(sorted(gruppiere(sofort, lambda z: z["begonnen"][:10]).items())),
                            "letzter_tag": max(z["begonnen"][:10] for z in sofort) if sofort else None,
                            "gemeldet_aufs_handy": sum(1 for z in sofort if z["gemeldet"])},
        "ohne_sofort": kz(rest),
        "ohne_sofort_netto_summe": ta.netto(rest).get("summe"),
        "klasse": gruppiere(rest, lambda z: z["klasse"]),
        "klasse_x_richtung": gruppiere(rest, lambda z: f"{z['klasse']}/{z['richtung']}"),
        "setup_x_klasse": gruppiere(rest, lambda z: f"{z['setup']} | {z['klasse']}"),
        "setup_x_richtung": gruppiere(rest, lambda z: f"{z['setup']} | {z['richtung']}"),
        "aktien_nach_setup": gruppiere([z for z in rest if z["klasse"] == "aktien"], lambda z: z["setup"]),
        "aktien_nach_richtung": gruppiere([z for z in rest if z["klasse"] == "aktien"], lambda z: z["richtung"]),
        "aktien_nach_einstiegsart": gruppiere([z for z in rest if z["klasse"] == "aktien"], lambda z: z["einstieg_art"]),
        "aktien_nach_ausgang": gruppiere([z for z in rest if z["klasse"] == "aktien"], lambda z: z["zustand"]),
        "ausgang_alle": gruppiere(rest, lambda z: z["zustand"]),
        "rueckeroberung_nach_klasse": gruppiere([z for z in rest if z["setup"].startswith("Rueckeroberung")], lambda z: z["klasse"]),
        "rueckeroberung_nach_woche": dict(sorted(gruppiere([z for z in rest if z["setup"].startswith("Rueckeroberung")], kalenderwoche).items())),
    }
    # Gewinn zurueckgegeben: war der Trade mal >= 1 R im Plus und endete trotzdem im Minus?
    mfe1 = [z for z in rest if (z["mfe"] or 0) >= 1.0]
    out["mfe_ab_1R"] = {**kz(mfe1), "davon_im_minus": sum(1 for z in mfe1 if z["r"] < 0)}
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json")
    a = ap.parse_args()
    res = analyse()
    txt = json.dumps(res, ensure_ascii=False, indent=1)
    if a.json:
        Path(a.json).write_text(txt, encoding="utf-8")
    else:
        print(txt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
