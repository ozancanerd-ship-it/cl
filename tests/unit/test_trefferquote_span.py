"""Depot-Karte: historische Trefferquote statt einer erfundenen Steig/Fall-Wahrscheinlichkeit.

WARUM ES DIESEN TEST GIBT

Ozan, 08.10. 16:51: „mir fehlt noch ... so ne Skala wie viel Prozent die Wahrscheinlichkeit
ist, dass es jetzt noch steigt und wie viel es noch sinkt." Eine echte Richtungs-
Wahrscheinlichkeit aus dem Kursverlauf kann niemand berechnen — keine der Einstiegs-Studien
fand einen Filter, der die nächste Richtung vorhersagt (EINSTIEG-FAKTOREN-STUDIE-2026-10).
Was es ehrlich gibt: wie oft eine VERGLEICHBARE Setup-Art in der Vergangenheit ihr erstes
Ziel erreicht hat statt im Stop zu enden (``erwartung.quote``, dieselbe Zahl wie bei neuen
Scan-Chancen) — jetzt auch bei bereits gehaltenen Depot-Positionen sichtbar.

Festgehalten wird:

1. Die Beschriftung heißt „Trefferquote", nicht „Wahrscheinlichkeit" — eine Zählung aus der
   Vergangenheit ist etwas anderes als eine Vorhersage für diesen einen Trade.
2. Unter 5 Fällen (``n < 5``) erscheint gar nichts — eine Quote aus 2 Fällen ist Rauschen,
   keine Information.
3. Ohne ``erwartung``/``quote`` am Scan-Row (z. B. ein Wert ohne Setup) erscheint nichts,
   kein Absturz.
4. Der Hinweistext (Tooltip) sagt ausdrücklich „keine Vorhersage für diesen Trade".

Geprüft wird die echte Funktion ``trefferquoteSpan`` aus ``site/template.html``, ausgeführt
in Node.
"""

from __future__ import annotations

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


def _lauf(ausdruck: str) -> str:
    node = shutil.which("node")
    if not node:
        pytest.skip("node nicht vorhanden")
    roh = VORLAGE.read_text(encoding="utf-8")
    skript = _funktion(roh, "trefferquoteSpan") + f"\nconsole.log(JSON.stringify({ausdruck}));\n"
    aus = subprocess.run([node, "-e", skript], capture_output=True, text=True, timeout=20)
    if aus.returncode != 0:
        raise AssertionError(aus.stderr)
    return aus.stdout.strip().strip('"')


def test_genug_faelle_zeigt_die_quote():
    out = _lauf(
        "trefferquoteSpan({erwartung:{quote:{n:36, tp1:0.444, stop:0.583, belastbar:true}}})"
    )
    assert "44" in out and "58" in out and "n=36" in out
    assert "Trefferquote" in out


def test_beschriftung_ist_keine_wahrscheinlichkeit():
    out = _lauf(
        "trefferquoteSpan({erwartung:{quote:{n:36, tp1:0.444, stop:0.583, belastbar:true}}})"
    )
    assert "Wahrscheinlichkeit" not in out
    assert "keine Vorhersage" in out


def test_zu_wenig_faelle_bleibt_still():
    assert _lauf("trefferquoteSpan({erwartung:{quote:{n:2, tp1:0.5, stop:0.5}}})") == ""


def test_ohne_erwartung_kein_absturz():
    assert _lauf("trefferquoteSpan(null)") == ""
    assert _lauf("trefferquoteSpan({})") == ""
    assert _lauf("trefferquoteSpan({erwartung:{}})") == ""


def test_nicht_belastbar_wird_markiert():
    out = _lauf(
        "trefferquoteSpan({erwartung:{quote:{n:6, tp1:0.3, stop:0.6, belastbar:false}}})"
    )
    assert "wenig Faelle" in out


def test_app_zeigt_die_trefferquote_bei_gehaltenen_positionen():
    t = VORLAGE.read_text(encoding="utf-8")
    assert "function trefferquoteSpan" in t
    assert "trefferquoteSpan(r)" in t
