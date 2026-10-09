"""ChatGPTs Meinung direkt auf der Buy-/Sell-Karte — nicht nur in einem Sammelkasten.

WARUM ES DIESEN TEST GIBT

Ozan, 09.10. 10:33: „integrier chatgpt mehr in der app, sehe auch seine meinungen bei
den buy und sell alarms ... deine und seine will ich sehen und arbeitest mit ihm zsm."

Die Zweitmeinung-Karte (``zweitmeinungKasten``) zeigte bisher nur EINEN Fliesstext,
losgeloest von der einzelnen Position oder Chance. ``chatgptMeinungFuer`` holt jetzt den
Satz, den ChatGPT fuer GENAU diesen Schluessel geschrieben hat (``sym`` einer Position
oder ``instrument`` einer Chance — identisch mit dem ``data-sym`` der jeweiligen Karte),
und ``chatgptZeile`` haengt ihn als kurze, erkennbare Zeile direkt an die Karte.

Festgehalten wird:

1. Ist ChatGPT nicht aktiv (kein Key, Fehler, oder ``ZM`` fehlt ganz), liefert
   ``chatgptMeinungFuer`` nichts — kein erfundener Text.
2. Mit aktiver Zweitmeinung UND einem Treffer im passenden Dict kommt genau dieser Satz
   zurueck.
3. Ein Schluessel, zu dem ChatGPT nichts geschrieben hat (aeltere/kleinere Antwort, oder
   das Modell hat das Feld ausgelassen), liefert ebenfalls nichts — still, ohne Fehler.
4. ``chatgptZeile`` rendert den Text erkennbar als ChatGPT-Zeile (🤖) und escaped ihn;
   ohne Text liefert sie nichts (keine leere Linie auf der Karte).
5. Fehlt ``je_position``/``je_chance`` ganz (altes ``chatgpt_meinung.json`` ohne die neuen
   Felder), bleibt die App unberührt — kein Crash, einfach kein Treffer.

Geprüft werden die echten Funktionen aus ``site/template.html``, ausgeführt in Node.
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


def _lauf(zm: dict | None, schluessel: str) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node nicht vorhanden")
    roh = VORLAGE.read_text(encoding="utf-8")
    skript = (
        "function esc(s){ return String(s == null ? '' : s)"
        ".replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }\n"
        + _funktion(roh, "chatgptMeinungFuer")
        + _funktion(roh, "chatgptZeile")
        + f"\nconst ZM = {json.dumps(zm)};\n"
        + f"const schluessel = {json.dumps(schluessel)};\n"
        + "const text = chatgptMeinungFuer(schluessel);\n"
        + "console.log(JSON.stringify({text, karte: chatgptZeile(text)}));\n"
    )
    aus = subprocess.run([node, "-e", skript], capture_output=True, text=True, timeout=20)
    if aus.returncode != 0:
        raise AssertionError(aus.stderr)
    return json.loads(aus.stdout)


def test_ohne_zm_kommt_nichts():
    r = _lauf(None, "METUSD")
    assert r["text"] == ""
    assert r["karte"] == ""


def test_inaktive_zweitmeinung_liefert_nichts_trotz_passendem_eintrag():
    # aktiv: false — z. B. kein Key eingerichtet. Ein evtl. noch vorhandenes altes
    # je_position darf trotzdem nicht angezeigt werden.
    zm = {"aktiv": False, "je_position": {"METUSD": "Veraltete Meinung."}}
    r = _lauf(zm, "METUSD")
    assert r["text"] == ""


def test_treffer_in_je_position_erscheint_auf_der_karte():
    zm = {"aktiv": True, "je_position": {"METUSD": "Stop zu weit weg, Rückgang ernst nehmen."}}
    r = _lauf(zm, "METUSD")
    assert r["text"] == "Stop zu weit weg, Rückgang ernst nehmen."
    assert "🤖" in r["karte"]
    assert "ChatGPT" in r["karte"]
    assert "Stop zu weit weg" in r["karte"]


def test_treffer_in_je_chance_erscheint_auf_der_karte():
    zm = {"aktiv": True, "je_chance": {"NVDA": "Score plausibel, Volumen schwach."}}
    r = _lauf(zm, "NVDA")
    assert r["text"] == "Score plausibel, Volumen schwach."


def test_schluessel_ohne_treffer_bleibt_still():
    zm = {"aktiv": True, "je_position": {"METUSD": "..."}, "je_chance": {}}
    r = _lauf(zm, "SOLUSD")
    assert r["text"] == ""
    assert r["karte"] == ""


def test_fehlende_je_felder_crashen_nicht():
    zm = {"aktiv": True, "text": "Nur der alte Sammeltext."}
    r = _lauf(zm, "METUSD")
    assert r["text"] == ""


def test_text_wird_auf_der_karte_escaped():
    zm = {"aktiv": True, "je_position": {"METUSD": "<script>alert(1)</script>"}}
    r = _lauf(zm, "METUSD")
    assert "<script>" not in r["karte"]
    assert "&lt;script&gt;" in r["karte"]
