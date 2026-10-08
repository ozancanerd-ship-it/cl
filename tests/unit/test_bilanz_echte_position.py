"""Bilanz-Tabelle: Signal-Ergebnis und echte Depot-Position nicht mehr verwechselbar.

WARUM ES DIESEN TEST GIBT

Ozan, 08.10. 13:30, am Telefon: „Bilanz stimmt auch gar nicht ... weil MET ist im Plus."
MET stand in der Tabelle „Wie die Signale ausgegangen sind" auf Stop/-1 R — das ist aber
das Ergebnis des SYSTEM-EIGENEN Signals (eigener Einstieg/Stop aus dem Scanner), nicht
Ozans echte Depot-Position. Kein Rechenfehler, aber eine Verwechslungsfalle: derselbe
Coin-Name, zwei unabhängige Zahlen.

Festgehalten wird:

1. ``echtePositionJeMuenze()`` liefert den Gewinn/Verlust der echten Depot-Position je
   Münze (Basiswährung, nicht das volle Symbol) — nur für Positionen mit bekanntem Kurs.
2. Bargeld-Positionen (``istBar``) zählen nicht mit.
3. Mehrere Positionen derselben Münze (z. B. zwei MET-Käufe auf verschiedenen Börsen)
   liefern den ERSTEN gefundenen Wert, nicht undefiniert/NaN.
4. Ohne Depot liefert die Funktion ein leeres Objekt, stürzt aber nicht ab.

Geprüft wird die echte Funktion ``echtePositionJeMuenze`` aus ``site/template.html``,
ausgeführt in Node — nicht nur der Text der Tabelle.
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
    m = re.search(rf"const {name}\s*=", roh)
    assert m, f"Konstante {name} nicht gefunden"
    ende = roh.index(";", m.start())
    return roh[m.start() : ende + 1] + "\n"


def _lauf(depotStandRueckgabe: dict | None, muenzen: list[str]) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node nicht vorhanden")
    roh = VORLAGE.read_text(encoding="utf-8")
    skript = (
        _konst(roh, "BAR")
        + _funktion(roh, "istBar")
        + _funktion(roh, "muenzeVon")
        + _funktion(roh, "echtePositionJeMuenze")
        + f"\nfunction depotStand(){{ return {json.dumps(depotStandRueckgabe)}; }}\n"
        + "const karte = echtePositionJeMuenze();\n"
        + f"console.log(JSON.stringify({{karte, werte: {json.dumps(muenzen)}.map(m => karte[m])}}));\n"
    )
    aus = subprocess.run([node, "-e", skript], capture_output=True, text=True, timeout=20)
    if aus.returncode != 0:
        raise AssertionError(aus.stderr)
    return json.loads(aus.stdout)


def _bew(sym: str, gv_pct: float | None, konto: str = "Bybit") -> dict:
    return {"pos": {"sym": sym, "konto": konto, "menge": 1}, "gvPct": gv_pct}


def test_echte_position_wird_je_muenze_gefunden():
    r = _lauf({"bew": [_bew("METUSD", 23.4)]}, ["MET"])
    assert r["werte"] == [23.4]


def test_bargeld_zaehlt_nicht_mit():
    r = _lauf({"bew": [_bew("EUR", 0.0)]}, ["EUR"])
    assert r["werte"] == [None]


def test_ohne_depot_kein_absturz():
    r = _lauf(None, ["MET"])
    assert r["karte"] == {}


def test_verlustposition_bleibt_negativ():
    r = _lauf({"bew": [_bew("ZECUSD", -12.5)]}, ["ZEC"])
    assert r["werte"] == [-12.5]


def test_met_fall_signal_und_echte_position_laufen_unabhaengig():
    """Der konkrete Fall vom 08.10.: Signal „Stop, -1 R", echte Position „+23 %" — beide
    Zahlen sind richtig, sie beschreiben nur verschiedene Dinge."""
    r = _lauf({"bew": [_bew("METUSD", 23.0)]}, ["MET"])
    assert r["werte"] == [23.0]


def test_app_zeigt_die_echte_position_in_der_bilanz_tabelle():
    t = VORLAGE.read_text(encoding="utf-8")
    assert "function echtePositionJeMuenze" in t
    assert "deine Position" in t
    assert "nicht deine echte" in t
