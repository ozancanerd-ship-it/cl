#!/usr/bin/env python3
"""Walk-Forward-Pruefung von Filterregeln auf den Forward-Paper-Trades (nur Lesen).

Grundsaetze (gegen Look-ahead und Leakage):
* Entschieden wird zum ZEITPUNKT DER AUFNAHME (``begonnen``). Als Wissen zaehlen nur Trades,
  die zu diesem Zeitpunkt schon BEENDET waren (``beendet <= begonnen``). Ein Trade, der
  noch laeuft, ist unbekannt — wer sein Ergebnis kennt, schaut in die Zukunft.
* Merkmale, die erst durch den Ausgang entstehen, sind keine Regel: Haltedauer, MFE, MAE,
  "Zustand". (Die Roh-Auswertung zeigt, dass lange Trades gewinnen — sie sind es aber
  GEWORDEN, weil sie nicht ausgestoppt wurden. Das ist Survivorship, kein Filter.)
* Feste Regeln werden auf einem fruehen Abschnitt (Train) definiert/gezaehlt und auf dem
  spaeteren (Test) geprueft. Adaptive Regeln lernen laufend nur aus der Vergangenheit.
* Kosten sind eine ANNAHME (``trade_analyse.KOSTEN_PCT``), mit Empfindlichkeit.
* Unsicherheit: Bootstrap-Intervall fuer den Erwartungswert (Trades unabhaengig gezogen —
  das ist optimistisch, weil gleichzeitige Trades korreliert sind).

Keine der Regeln wurde an diesen Daten optimiert; es sind vorab gesetzte, wirtschaftlich
begruendbare Kandidaten. Ergebnis: Hinweis, kein Beleg.
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from trade_analyse import KOSTEN_PCT, laden  # noqa: E402

MIN_N = 8


def netto_r(t: dict, faktor: float = 1.0) -> float | None:
    if t.get("kosten_r") is None:
        return None
    return t["r"] - t["kosten_r"] * faktor


def kennzahl(werte: list[float]) -> dict:
    if not werte:
        return {"n": 0}
    gew = [w for w in werte if w > 0]
    verl = [-w for w in werte if w < 0]
    rng = random.Random(7)
    boots = sorted(sum(rng.choices(werte, k=len(werte))) / len(werte) for _ in range(2000))
    return {
        "n": len(werte),
        "summe": round(sum(werte), 1),
        "ev": round(sum(werte) / len(werte), 3),
        "treffer": round(len(gew) / len(werte), 3),
        "pf": round(sum(gew) / sum(verl), 2) if verl else None,
        "ev_ci90": [round(boots[100], 3), round(boots[1899], 3)],
    }


def sortiert(trades: list[dict]) -> list[dict]:
    return sorted(
        [t for t in trades if t.get("begonnen") and t.get("beendet")], key=lambda t: t["begonnen"]
    )


def adaptiv(
    trades: list[dict], schluessel, min_n: int, faktor: float = 1.0, warmup: int = 100
) -> dict:
    """Sperre eine Gruppe, wenn ihr bisheriger Netto-Erwartungswert (nur BEENDETE Trades
    zum Aufnahmezeitpunkt) negativ ist und sie mindestens ``min_n`` Faelle hat."""
    ts = sortiert(trades)
    genommen, alle_oos = [], []
    for i, t in enumerate(ts):
        r = netto_r(t, faktor)
        if r is None:
            continue
        hist = [netto_r(h, faktor) for h in ts[:i] if h["beendet"] <= t["begonnen"] and h is not t]
        k = schluessel(t)
        gruppe = [
            netto_r(h, faktor)
            for h in ts[:i]
            if h["beendet"] <= t["begonnen"]
            and schluessel(h) == k
            and netto_r(h, faktor) is not None
        ]
        gesperrt = len(gruppe) >= min_n and sum(gruppe) / len(gruppe) < 0
        if i >= warmup:
            alle_oos.append(r)
            if not gesperrt:
                genommen.append(r)
        del hist
    return {
        "basis_oos": kennzahl(alle_oos),
        "mit_regel_oos": kennzahl(genommen),
        "verworfen": len(alle_oos) - len(genommen),
    }


def fest(trades: list[dict], regel, schnitt: str, faktor: float = 1.0) -> dict:
    """Regel auf Test-Abschnitt (ab ``schnitt``) anwenden; Train zeigt, woher die Idee kam."""
    ts = sortiert(trades)
    train = [
        netto_r(t, faktor) for t in ts if t["begonnen"] < schnitt and netto_r(t, faktor) is not None
    ]
    test_alle = [t for t in ts if t["begonnen"] >= schnitt and netto_r(t, faktor) is not None]
    test_regel = [netto_r(t, faktor) for t in test_alle if regel(t)]
    train_regel = [
        netto_r(t, faktor)
        for t in ts
        if t["begonnen"] < schnitt and regel(t) and netto_r(t, faktor) is not None
    ]
    return {
        "train_alle": kennzahl(train),
        "train_regel": kennzahl(train_regel),
        "test_alle": kennzahl([netto_r(t, faktor) for t in test_alle]),
        "test_regel": kennzahl(test_regel),
    }


REGELN = {
    "nur benanntes Setup": lambda t: t["setup"] != "ohne",
    "ohne Ruecksetzer im Trend": lambda t: t["setup"] != "Ruecksetzer im Trend",
    "nur Einstieg 'sofort'": lambda t: t["einstieg_art"] == "sofort",
    "keine Aktien-Shorts": lambda t: not (t["klasse"] == "aktien" and t["richtung"] == "short"),
    "nur Krypto": lambda t: t["klasse"] == "krypto",
    "benanntes Setup UND (sofort ODER Krypto)": lambda t: (
        t["setup"] != "ohne" and (t["einstieg_art"] == "sofort" or t["klasse"] == "krypto")
    ),
    "Stop-Abstand 2-6 %": lambda t: t["risiko_pct"] is not None and 2 <= t["risiko_pct"] < 6,
    "nur Alarm-Trades (Tor offen)": lambda t: t["gemeldet"],
}

SCHLUESSEL = {
    "Setup": lambda t: t["setup"],
    "Klasse|Setup": lambda t: f"{t['klasse']}|{t['setup']}",
    "Klasse|Richtung": lambda t: f"{t['klasse']}|{t['richtung']}",
    "Klasse|Richtung|Setup": lambda t: f"{t['klasse']}|{t['richtung']}|{t['setup']}",
    "Klasse|Entry-Art": lambda t: f"{t['klasse']}|{t['einstieg_art']}",
}


def haupt() -> dict:
    z = [t for t in laden() if t["begonnen"] >= "2026-09-18"]  # Regeln seit 12./17.09. in Kraft
    ts = sortiert(z)
    schnitt = ts[len(ts) * 6 // 10]["begonnen"][:10]
    out = {
        "trades": len(ts),
        "zeitraum": [ts[0]["begonnen"][:10], ts[-1]["begonnen"][:10]],
        "schnitt_train_test": schnitt,
        "kosten_annahme_pct": KOSTEN_PCT,
    }
    out["basis"] = {
        "brutto": kennzahl([t["r"] for t in ts]),
        "netto": kennzahl([netto_r(t) for t in ts if netto_r(t) is not None]),
    }
    out["kosten_empfindlichkeit"] = {
        f"x{f}": kennzahl([netto_r(t, f) for t in ts if netto_r(t, f) is not None])
        for f in (0.0, 0.5, 1.0, 1.5)
    }
    out["fest"] = {n: fest(ts, r, schnitt) for n, r in REGELN.items()}
    out["adaptiv"] = {
        f"{sn} | min_n={m}": adaptiv(ts, sf, m, warmup=80)
        for sn, sf in SCHLUESSEL.items()
        for m in (8, 15)
    }
    return out


if __name__ == "__main__":
    res = haupt()
    p = sys.argv[1] if len(sys.argv) > 1 else None
    (
        Path(p).write_text(json.dumps(res, ensure_ascii=False, indent=1))
        if p
        else print(json.dumps(res, ensure_ascii=False, indent=1))
    )
