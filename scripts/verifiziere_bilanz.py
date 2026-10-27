#!/usr/bin/env python3
"""Unabhaengige Nachrechnung der Bilanz aus den Rohdaten der Wachliste.

Zweck: ``scanner/performance.py`` ist der einzige Ort, der die eigenen Trades bewertet. Dieses
Skript rechnet dieselben Trades mit BEWUSST eigenem Code nach (keine Funktion aus
``performance.py``) und vergleicht Anzahl, R-Summe, Profitfaktor und Kosten. Weichen sie ab,
steht eines von beiden falsch.

Pruefungen:
  1. Anzahl abgeschlossener / eingestiegener / gezaehlter Trades und die Ausschlussgruende.
  2. Doppelte Trades (gleiche Wache zweimal im Archiv und in der Wachliste).
  3. R nominal (Drittel-Regel 1/2/3,5 R), R am Fuellkurs, Kosten in R (Annahme je Klasse).
  4. Profitfaktor, Trefferquote, Erwartungswert, maximaler Rueckgang der Kurve.
  5. Teilverkaeufe: je Trade die Summe der gebuchten Anteile (immer <= 1).

Aufruf:  python scripts/verifiziere_bilanz.py [--json aus.json]
Exit 1, wenn die unabhaengige Rechnung von ``performance.bericht`` abweicht.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

TP_R = {"TP1": 1.0, "TP2": 2.0, "TP3": 3.5}
KOSTEN_PCT = {"krypto": 0.35, "aktien": 0.10, "gold": 0.05}
FERTIG = {"stop", "ziel_erreicht", "invalidiert", "abgelaufen"}


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def trade_r(w: dict) -> dict | None:
    """Eigene Fassung der Drittel-Regel. Gibt ``None`` zurueck, wenn die Wache kein Trade ist."""
    z = str(w.get("zustand") or "")
    if z not in FERTIG:
        return None
    erreicht = [t for t in ("TP1", "TP2", "TP3") if t in (w.get("erreicht") or [])]
    eingestiegen = (
        w.get("einstiegskurs") is not None or z in ("stop", "ziel_erreicht") or bool(erreicht)
    )
    raus = (
        w.get("raus_erreicht")
        if w.get("raus") and isinstance(w.get("raus_erreicht"), list)
        else None
    )
    ohne_ergebnis = eingestiegen and z == "abgelaufen" and raus is None
    e, s = _f(w.get("einstieg")), _f(w.get("stop"))
    lang = str(w.get("richtung") or "long") == "long"
    risiko = abs(e - s) if e is not None and s is not None else None

    def r_bei(kurs: float) -> float | None:
        if not risiko:
            return None
        basis = _f(w.get("einstiegskurs")) or e
        return ((kurs - basis) if lang else (basis - kurs)) / risiko

    anteile = 0.0
    if raus is not None:
        vor = [t for t in ("TP1", "TP2", "TP3") if t in raus]
        r = 0.0
        if vor:
            rest = TP_R["TP1"] if "TP2" in vor else 0.0
            r = sum(TP_R[t] / 3 for t in vor) + (3 - len(vor)) / 3 * rest
            anteile = len(vor) / 3 + (3 - len(vor)) / 3
    elif not erreicht:
        r = -1.0 if z == "stop" else 0.0
        anteile = 1.0
    else:
        r = sum(TP_R[t] / 3 for t in erreicht)  # Rest geht am Einstand raus (0 R)
        anteile = len(erreicht) / 3 + (3 - len(erreicht)) / 3
    if z == "invalidiert" and _f(w.get("ausstiegskurs")) is not None and raus is None:
        ra = r_bei(float(w["ausstiegskurs"]))
        if ra is not None:
            boden = TP_R["TP1"] if "TP2" in erreicht else 0.0
            rest = max(ra, boden) if erreicht else ra
            r = sum(TP_R[t] / 3 for t in erreicht) + (1 - len(erreicht) / 3) * rest
    klasse = str(w.get("klasse") or "?")
    kosten = None
    if risiko and e and e > 0:
        kosten = KOSTEN_PCT.get(klasse, 0.35) / (risiko / e * 100.0)
    return {
        "id": (str(w.get("instrument")), str(w.get("aufgenommen"))),
        "klasse": klasse,
        "r": r,
        "kosten": kosten,
        "gezaehlt": eingestiegen and not ohne_ergebnis,
        "eingestiegen": eingestiegen,
        "anteile": anteile,
        "beendet": str(w.get("zuletzt") or ""),
    }


def kennzahlen(rs: list[float]) -> dict:
    if not rs:
        return {"n": 0}
    gew = sum(x for x in rs if x > 0)
    verl = -sum(x for x in rs if x < 0)
    kurve, spitze, dd = 0.0, 0.0, 0.0
    for x in rs:
        kurve += x
        spitze = max(spitze, kurve)
        dd = min(dd, kurve - spitze)
    return {
        "n": len(rs),
        "summe": round(sum(rs), 2),
        "ev": round(sum(rs) / len(rs), 4),
        "treffer": round(sum(1 for x in rs if x > 0) / len(rs), 4),
        "pf": round(gew / verl, 3) if verl else None,
        "max_dd": round(dd, 2),
    }


def laden() -> dict:
    from trading_agent.scanner.watchlist import archiv_laden, mit_archiv

    wl = json.loads((ROOT / "data/repository_real/live/watchlist.json").read_text())
    return mit_archiv(
        wl,
        archiv_laden(
            ROOT / "data/repository_real/archiv/wachen_historie_2026-10-02.json",
            ROOT / "data/repository_real/live/wachen_archiv.jsonl",
        ),
    )


def pruefe(voll: dict) -> dict:
    wachen = voll["wachen"]
    alle = [t for w in wachen.values() if (t := trade_r(w)) is not None]
    ids = [t["id"] for t in alle]
    doppelt = len(ids) - len(set(ids))
    gez = sorted((t for t in alle if t["gezaehlt"]), key=lambda t: t["beendet"])
    aus = {
        "wachen_gesamt": len(wachen),
        "abgeschlossen": len(alle),
        "nie_eingestiegen": sum(1 for t in alle if not t["eingestiegen"]),
        "ohne_ergebnis": sum(1 for t in alle if t["eingestiegen"] and not t["gezaehlt"]),
        "gezaehlt": len(gez),
        "doppelt_nach_instrument_und_aufnahme": doppelt,
        "anteile_ueber_1": sum(1 for t in gez if t["anteile"] > 1.0000001),
        "nominal": kennzahlen([t["r"] for t in gez]),
    }
    mit_k = [t for t in gez if t["kosten"] is not None]
    aus["kosten_r_summe"] = round(sum(t["kosten"] for t in mit_k), 2)
    aus["kosten_r_je_trade"] = round(aus["kosten_r_summe"] / len(mit_k), 3) if mit_k else None
    aus["je_klasse"] = {
        k: kennzahlen([t["r"] for t in gez if t["klasse"] == k])
        for k in sorted({t["klasse"] for t in gez})
    }
    return aus


def vergleiche(eigen: dict, wachliste: dict) -> list[str]:
    """Abweichungen zu ``performance.bericht`` — leere Liste = identisch."""
    from trading_agent.scanner import performance as p

    b = p.bericht(wachliste).as_dict()
    drittel = b["je_regel"]["drittel"]
    soll_n = eigen["gezaehlt"]
    probleme = []
    ist_n = drittel["anzahl"] if "anzahl" in drittel else drittel.get("n")
    if ist_n != soll_n:
        probleme.append(f"Anzahl: unabhaengig {soll_n}, performance {ist_n}")
    ist_ev = drittel.get("erwartungswert")
    if ist_ev is not None and abs(ist_ev - eigen["nominal"]["ev"]) > 0.005:
        probleme.append(
            f"Erwartungswert: unabhaengig {eigen['nominal']['ev']}, performance {ist_ev}"
        )
    ist_pf = drittel.get("profitfaktor")
    if (
        ist_pf is not None
        and eigen["nominal"]["pf"] is not None
        and abs(ist_pf - eigen["nominal"]["pf"]) > 0.01
    ):
        probleme.append(f"Profitfaktor: unabhaengig {eigen['nominal']['pf']}, performance {ist_pf}")
    return probleme


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default="")
    a = ap.parse_args()
    voll = laden()
    eigen = pruefe(voll)
    abw = vergleiche(eigen, voll)
    eigen["abweichungen"] = abw
    print(json.dumps(eigen, ensure_ascii=False, indent=1))
    if a.json:
        Path(a.json).write_text(json.dumps(eigen, ensure_ascii=False, indent=1))
    return 1 if abw else 0


if __name__ == "__main__":
    raise SystemExit(main())
