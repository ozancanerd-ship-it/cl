"""Nacht-Plan: ein gesetzter Stop muss als echte Order erscheinen, nicht als Vorschlag.

WARUM ES DIESEN TEST GIBT

Der Nacht-Plan (07.10., Ozans Wunsch: „dass ich in Ruhe schlafen gehen kann") wurde beim
Bauen nur gegen ein Test-Depot ohne gesetzte Stops geprüft — dort griff bei jeder Position
der Vorschlags-Zweig (Halte-Stop). Der Pflicht-Zweig für Positionen MIT eigenem Stop
(``pl.stop != null``, gesetzt über „Stop setzen" im Portfolio-Reiter) blieb ungetestet.

Festgehalten wird:

1. Mit gesetztem Stop steht „Stop: …" (die eigene Marke), nicht „Stop (Vorschlag): …".
2. Der Verlust bei Auslösung wird korrekt in Euro berechnet (Richtung long/short beachtet).
3. Eine Short-Position bekommt den Verlust bei STEIGENDEM Kurs, nicht bei fallendem.
4. Mit offenem TP wird das nächste Ziel als Limit-Order mit korrekter Stückzahl genannt —
   bei bereits erledigtem TP1 ist das die halbe (nicht die volle) Reststückzahl.
5. Ohne jeden Stop bleibt der alte Vorschlags-Zweig intakt (Regressionsschutz).

Geprüft wird die echte Funktion ``nachtZeile`` aus ``site/template.html``, ausgeführt in Node.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

VORLAGE = Path(__file__).resolve().parents[2] / "site" / "template.html"


def _funktion(roh: str, name: str) -> str:
    start = roh.index(f"function {name}(")
    tiefe, i = 0, roh.index("{", start)
    anfang = i
    while True:
        if roh[i] == "{":
            tiefe += 1
        elif roh[i] == "}":
            tiefe -= 1
            if tiefe == 0:
                break
        i += 1
    return roh[start:anfang] + roh[anfang : i + 1]


def _konst(roh: str, name: str) -> str:
    m = re.search(rf"const {name}\s*=\s*\{{", roh)
    assert m, f"Konstante {name} nicht gefunden"
    tiefe, i = 0, roh.index("{", m.start())
    anfang = i
    while True:
        if roh[i] == "{":
            tiefe += 1
        elif roh[i] == "}":
            tiefe -= 1
            if tiefe == 0:
                break
        i += 1
    return roh[m.start() : i + 1] + ";\n"


def _b(**pos: object) -> dict:
    return {
        "pos": {"sym": "NVDA", "konto": "Trade Republic", "menge": 3, **pos},
        "kurs": 180.0,
        "w": "EUR",
        "wert": 540.0,
        "row": {"klasse": "aktien", "name": "NVDA"},
    }


def _lauf(b: dict) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node nicht vorhanden")
    roh = VORLAGE.read_text(encoding="utf-8")
    skript = (
        "function glNameFuer(b){ return null; }\n"
        "function glVola(n){ return null; }\n"
        "function name(sym){ return sym; }\n"
        "function esc(s){ return String(s == null ? '' : s); }\n"
        + "".join(
            _funktion(roh, f) + "\n"
            for f in (
                "digits",
                "num",
                "waehrungsZeichen",
                "istBar",
                "posName",
                "kontoNorm",
                "zieleGelten",
                "scanRow",
            )
        )
        + _konst(roh, "NACHT_BROKER")
        + _funktion(roh, "nachtSd")
        + _funktion(roh, "scheinBei")
        + _funktion(roh, "nachtZeile")
        + "\nconst b = JSON.parse(process.argv[1]);\n"
        + "const stand = { inEuro: (bb, wert) => wert };\n"
        + "console.log(JSON.stringify(nachtZeile(b, stand)));"
    )
    aus = subprocess.run(
        [node, "-e", skript, json.dumps(b)], capture_output=True, text=True, timeout=30
    )
    if aus.returncode != 0:
        raise AssertionError(aus.stderr)
    return json.loads(aus.stdout)


def test_gesetzter_stop_ist_eine_order_kein_vorschlag():
    b = _b(plan={"stop": 160.0, "richtung": "long"})
    r = _lauf(b)
    text = " ".join(r["zeilen"])
    assert "Stop:" in text and "Stop (Vorschlag)" not in text
    assert "160" in text
    # 3 Stück * 20 € Abstand = 60 € Verlust bei Auslösung
    assert r["verlustStop"] == pytest.approx(60.0, abs=0.5)
    assert r["warn"] == []


def test_short_position_verliert_bei_steigendem_kurs():
    b = _b(plan={"stop": 200.0, "richtung": "short"}, hebel_richtung="short")
    r = _lauf(b)
    # Stop über dem aktuellen Kurs (180) ist für einen Short korrekt — Verlust, wenn
    # der Kurs STEIGT, nicht faellt.
    assert r["verlustStop"] is not None
    assert r["verlustStop"] > 0


def test_naechstes_ziel_nach_tp1_ist_die_halbe_restmenge():
    b = _b(
        plan={"stop": 160.0, "tp1": 190.0, "tp2": 200.0, "tp3": 210.0, "quelle": "selbst"},
        erledigt=["TP1"],
    )
    r = _lauf(b)
    text = " ".join(r["zeilen"])
    assert "TP2" in text
    # Reststueckzahl: (3 - 0 erledigt von TP2/TP3 betrachtet) -> 2 offene Ziele je 1 Stueck's Rest
    # 3 Stueck, TP1 erledigt -> 2 Ziele offen (TP2,TP3) -> je 1,5, abgerundet "1"
    assert "1 Stück" in text or "1,5 Stück" in text


def test_ohne_stop_bleibt_der_vorschlags_zweig():
    b = _b(plan={})
    r = _lauf(b)
    assert r["stufe"] == "bad"
    assert any("Kein Stop eingetragen" in w for w in r["warn"])


def test_turbo_stop_auf_basiswert_wird_in_schein_preis_umgerechnet():
    """Stop-Marke in Basiswert-Preis (pl.ebene='basis') — Nacht-Plan muss sie in den
    Schein-Preis umrechnen, denn beim Broker steht eine Order auf den Schein, nicht
    auf den Basiswert. Ausserdem ein Hinweis, dass der Preis umgerechnet ist."""
    b = {
        "pos": {
            "sym": "TURBO-NVDA-LONG", "konto": "Trade Republic", "menge": 2,
            "ko": 100.0,
            "plan": {"ebene": "basis", "stop": 120.0, "richtung": "long"},
        },
        "kurs": 20.0,  # aktueller Schein-Preis
        "w": "EUR",
        "wert": 40.0,
        "row": {"klasse": "aktien", "name": "Turbo NVDA"},
        "hebel": {"ko": 100.0, "lang": True, "ratio": 1, "schein": 20.0,
                  "basisKurs": 150.0, "ausgeknockt": False, "basisName": "NVDA"},
    }
    r = _lauf(b)
    text = " ".join(r["zeilen"])
    assert "Stop:" in text
    # scheinBei(120) = (120-100)/1 * (20 / ((150-100)/1)) = 20 * 0.4 = 8
    assert "8," in text or "8 " in text
    assert "umgerechnet" in text
    assert "120" in text  # der Basiswert-Preis steht als Erklaerung dabei
