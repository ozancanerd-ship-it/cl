"""„Ausführliche Analyse öffnen" darf sich nicht von selbst wieder schliessen.

WARUM ES DIESEN TEST GIBT

Ozan, 09.10. 22:30: „wenn ich auf weitere drücke, ist das Fenster 2 Sekunden offen und
danach schließt es sich automatisch ... ich kann mir gar nicht alles in Ruhe anschauen."

Portfolio wird bei jedem Live-Kurs-Tick komplett neu gezeichnet (``aktualisierePortfolio
Kurse`` -> ``zeichnePortfolio``, höchstens alle 5 Sekunden). ``mehrKasten`` hatte dafür
schon ein Gedächtnis (``MEHR_OFFEN``), das aufklappbare ``<details>`` der „Ausführlichen
Analyse" (``tiefeAnalyse``) aber nicht — es klappte bei jeder Neuzeichnung wieder zu, egal
wie lange der Nutzer es gerade offen hatte.

Festgehalten wird:

1. Ohne vorherigen Besuch ist das Detail zu (kein ``open``).
2. Steht der Positions-Schlüssel in ``TA_OFFEN`` (so, wie ihn der echte Toggle-Listener
   in ``zeichnePortfolio`` pflegt), bleibt es über eine erneute Zeichnung hinweg offen —
   genau das Verhalten, das bei „Mehr zur Analyse" schon funktionierte.
3. Jede Position bekommt ihren EIGENEN Schlüssel (``data-ta``, aus ``posSchluessel``) —
   das Öffnen einer Karte klappt nicht versehentlich eine andere Position mit auf.

Geprüft wird die echte Funktion ``tiefeAnalyse`` aus ``site/template.html``, ausgeführt
in Node.
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


def _lauf(b: dict, *, ta_offen: list[str] | None = None) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node nicht vorhanden")
    roh = VORLAGE.read_text(encoding="utf-8")
    skript = (
        "function esc(s){ return String(s == null ? '' : s); }\n"
        "let NB = null;\n"
        + _konst(roh, "posSchluessel")
        + _funktion(roh, "digits")
        + _funktion(roh, "num")
        + _funktion(roh, "pct")
        + _konst(roh, "BAR")
        + _funktion(roh, "istBar")
        + _funktion(roh, "zieleGelten")
        + _konst(roh, "TA_OFFEN")
        + _funktion(roh, "tiefeAnalyse")
        + f"\nfor (const k of {json.dumps(ta_offen or [])}) TA_OFFEN.add(k);\n"
        + f"const b = {json.dumps(b)};\n"
        + "const stand = {inEuro: (b, w) => w};\n"
        + "const html = tiefeAnalyse(b, stand);\n"
        + "console.log(JSON.stringify({html}));\n"
    )
    aus = subprocess.run([node, "-e", skript], capture_output=True, text=True, timeout=20)
    if aus.returncode != 0:
        raise AssertionError(aus.stderr)
    return json.loads(aus.stdout)


def _pos(sym="METUSD", konto="Bybit"):
    return {
        "pos": {"sym": sym, "konto": konto, "einstieg": 100.0, "plan": {"stop": 90.0}},
        "row": {
            "instrument": sym,
            "name": "Metaplex",
            "kurs": 110.0,
            "waehrung": "USD",
            "headline": "Testlage",
            "urteil": "halten",
            "note": "B",
        },
        "wert": 100.0,
    }


def test_ohne_vorherigen_besuch_ist_es_zu():
    r = _lauf(_pos())
    assert "<details" in r["html"]
    assert " open" not in r["html"].split(">", 1)[0] + ">"  # erstes Tag hat kein "open"


def test_gemerkter_schluessel_haelt_es_offen_ueber_eine_neue_zeichnung():
    b = _pos()
    schl = "METUSD|bybit"  # posSchluessel: sym gross, konto klein
    r = _lauf(b, ta_offen=[schl])
    erstes_tag = r["html"].split(">", 1)[0] + ">"
    assert " open" in erstes_tag
    assert f'data-ta="{schl}"' in r["html"]


def test_andere_position_bleibt_unberuehrt():
    # TA_OFFEN enthaelt nur den Schluessel einer ANDEREN Position — diese hier bleibt zu.
    b = _pos(sym="SOLUSD", konto="Kraken")
    r = _lauf(b, ta_offen=["METUSD|bybit"])
    erstes_tag = r["html"].split(">", 1)[0] + ">"
    assert " open" not in erstes_tag
