"""ChatGPTs Urteil muss als Abzeichen auftauchen — und nur, wenn es wirklich eines gibt.

WARUM ES DIESEN TEST GIBT

Ozan, 11.10. 17:18: "du hast deine eigene These und er seine eigene These, ihr solltet
aber zusammenarbeiten." Damit man nicht erst den ganzen ChatGPT-Satz lesen muss, um zu
merken, ob er zustimmt, bekommt ``chatgptZeile`` jetzt ein Abzeichen direkt neben "🤖
ChatGPT:" — gespeist aus ``ZM.urteil_position``/``ZM.urteil_chance`` (von
``chatgpt_zweitmeinung.py`` geschrieben, siehe ``zweitmeinung.py``-Tests fuer die
Erzeugung dieser Felder).

Festgehalten wird:

1. Ein gueltiges Urteil ("stimmt_zu") erscheint als Abzeichen neben dem Text.
2. Ein fehlendes Urteil (altes Format, oder das Modell hat keins geliefert) zeigt
   weiterhin den Text, aber OHNE Abzeichen — kein erfundenes "stimmt zu".
3. Ein ungueltiges/unbekanntes Wort im Urteilsfeld wird ignoriert, nicht angezeigt.
4. Ohne Text ueberhaupt (kein Eintrag fuer den Schluessel) bleibt die Zeile leer — auch
   wenn zufaellig ein Urteil ohne Text vorhanden waere.
5. ``ZM.aktiv === false`` unterdrueckt alles, Text wie Abzeichen.
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


def _lauf(zm: dict | None, schluessel: str) -> str:
    node = shutil.which("node")
    if not node:
        pytest.skip("node nicht vorhanden")
    roh = VORLAGE.read_text(encoding="utf-8")
    skript = (
        "function esc(s){ return String(s == null ? '' : s); }\n"
        + f"let ZM = {json.dumps(zm)};\n"
        + _funktion(roh, "chatgptMeinungFuer")
        + _funktion(roh, "chatgptUrteilFuer")
        + _konst(roh, "CHATGPT_URTEIL_ANZEIGE")
        + _funktion(roh, "chatgptZeile")
        + f"console.log(JSON.stringify({{html: chatgptZeile({json.dumps(schluessel)})}}));\n"
    )
    aus = subprocess.run([node, "-e", skript], capture_output=True, text=True, timeout=20)
    if aus.returncode != 0:
        raise AssertionError(aus.stderr)
    return json.loads(aus.stdout)["html"]


def test_gueltiges_urteil_erscheint_als_abzeichen():
    html = _lauf(
        {"aktiv": True, "je_position": {"AAPL": "Sieht stark aus."},
         "urteil_position": {"AAPL": "stimmt_zu"}},
        "AAPL",
    )
    assert "Sieht stark aus." in html
    assert "stimmt zu" in html


def test_fehlendes_urteil_zeigt_nur_den_text():
    html = _lauf(
        {"aktiv": True, "je_position": {"AAPL": "Alter Freitext ohne Urteil."}},
        "AAPL",
    )
    assert "Alter Freitext ohne Urteil." in html
    assert "stimmt zu" not in html
    assert "teilweise" not in html
    assert "widerspricht" not in html


def test_ungueltiges_urteil_wird_ignoriert():
    html = _lauf(
        {"aktiv": True, "je_position": {"AAPL": "Text da."},
         "urteil_position": {"AAPL": "voellig_anderes_wort"}},
        "AAPL",
    )
    assert "Text da." in html
    assert "voellig_anderes_wort" not in html


def test_ohne_text_bleibt_die_zeile_leer():
    html = _lauf(
        {"aktiv": True, "urteil_position": {"AAPL": "stimmt_zu"}},
        "AAPL",
    )
    assert html == ""


def test_inaktive_zweitmeinung_unterdrueckt_alles():
    html = _lauf(
        {"aktiv": False, "je_position": {"AAPL": "Text da."},
         "urteil_position": {"AAPL": "stimmt_zu"}},
        "AAPL",
    )
    assert html == ""
