"""Portfolio darf nicht mehr alle paar Sekunden das ganze Bild unter den Füßen wegreißen.

WARUM ES DIESEN TEST GIBT

Ozan, 11.10. 15:11: „Glaube die App hängt ... das darf nie wieder passieren, riesen
Fehler." Ursache: seit „Ausführliche Analyse" und „Mehr zur Analyse" offen bleiben
(09.10., TA_OFFEN/MEHR_OFFEN), reißt jeder Live-Kurs-Tick (alle paar Sekunden) das
KOMPLETTE Portfolio-HTML neu auf — bei vielen geöffneten, langen Analyse-Kästen auf dem
Handy spürbar: der Bildschirm springt/hängt mitten im Lesen, weil ``#portfolio`` jedes
Mal komplett ersetzt wird (Scrollposition und Layout fliegen mit).

Festgehalten wird:

1. Ist irgendwo im Portfolio-Tab ein ``<details open>`` (eine aufgeklappte Analyse),
   wird NICHT neu gezeichnet — niemand wird mitten im Lesen unterbrochen.
2. Ohne offenen Kasten, aber innerhalb der letzten 20 Sekunden seit der letzten
   Zeichnung, wird ebenfalls nicht neu gezeichnet (der alte Takt war 5 Sekunden).
3. Ohne offenen Kasten und nach Ablauf der 20 Sekunden wird ganz normal neu gezeichnet.
4. Mitten im Tippen (ein ``<input>`` hat den Fokus) wird weiterhin nie neu gezeichnet —
   das bestehende Verhalten darf durch die neue Prüfung nicht verloren gehen.

Geprüft wird die echte Funktion ``aktualisierePortfolioKurse`` aus
``site/template.html``, ausgeführt in Node mit einem minimalen DOM-Double.
"""

from __future__ import annotations

import json
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


def _lauf(*, p3_hidden: bool, offene_details: bool, aktiv_input: bool, ms_seit_letztem: int) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node nicht vorhanden")
    roh = VORLAGE.read_text(encoding="utf-8")
    skript = (
        "const $ = id => document.getElementById(id);\n"
        + "\nlet gezeichnet = 0;\n"
        + "function zeichnePortfolio(){ gezeichnet++; }\n"
        + f"\nconst _p3 = {{hidden: {str(p3_hidden).lower()}}};\n"
        + f"const _hatOffen = {str(offene_details).lower()};\n"
        + "const _portfolio = {querySelector: (sel) => _hatOffen && sel === 'details[open]' ? {} : null};\n"
        + "globalThis.document = {\n"
        + "  getElementById: (id) => id === 'p3' ? _p3 : id === 'portfolio' ? _portfolio : null,\n"
        + '  activeElement: {tagName: ' + json.dumps("INPUT" if aktiv_input else "BODY") + "},\n"
        + "};\n"
        + _funktion(roh, "aktualisierePortfolioKurse")
        + f"\n_pfZuletzt = Date.now() - {ms_seit_letztem};\n"
        + "aktualisierePortfolioKurse();\n"
        + "console.log(JSON.stringify({gezeichnet}));\n"
    )
    # ``_pfZuletzt`` ist im Original mit ``let`` deklariert — dieser Test deklariert es
    # hier selbst (als Teil der extrahierten Funktion fehlt die Deklaration sonst).
    skript = "let _pfZuletzt = 0;\n" + skript
    aus = subprocess.run([node, "-e", skript], capture_output=True, text=True, timeout=20)
    if aus.returncode != 0:
        raise AssertionError(aus.stderr)
    return json.loads(aus.stdout)


def test_aufgeklappte_analyse_verhindert_die_neuzeichnung():
    r = _lauf(p3_hidden=False, offene_details=True, aktiv_input=False, ms_seit_letztem=999_000)
    assert r["gezeichnet"] == 0


def test_innerhalb_von_20_sekunden_wird_nicht_neu_gezeichnet():
    r = _lauf(p3_hidden=False, offene_details=False, aktiv_input=False, ms_seit_letztem=5_000)
    assert r["gezeichnet"] == 0


def test_ohne_offenen_kasten_nach_20_sekunden_wird_neu_gezeichnet():
    r = _lauf(p3_hidden=False, offene_details=False, aktiv_input=False, ms_seit_letztem=21_000)
    assert r["gezeichnet"] == 1


def test_waehrend_des_tippens_wird_nicht_neu_gezeichnet():
    r = _lauf(p3_hidden=False, offene_details=False, aktiv_input=True, ms_seit_letztem=999_000)
    assert r["gezeichnet"] == 0
