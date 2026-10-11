"""Zurueck aus dem Hintergrund muss sofort neu laden — nicht erst beim naechsten Tick.

WARUM ES DIESEN TEST GIBT

Ozan, 11.10. 17:18: „die App läuft nicht so gut ... ich glaube, die ist heute hängen
geblieben ... denn da sind die gleichen Alarme noch einen Tag lang." CI-Protokoll und
Backend wurden geprueft und waren an diesem Tag durchgehend gesund (keine Lauf-Luecke
> 15 Minuten), und der Service Worker cached bewusst nichts (siehe ``site/sw.js``) —
die Ursache liegt also im Frontend: nirgendwo in der Seite wurde je geprueft, ob sie
noch sichtbar ist. Mobile Browser (vor allem iOS-Safari-PWAs) drosseln ``setInterval``
und die Kraken-WebSocket-Verbindung, solange die Seite im Hintergrund liegt — ohne
einen Haken, der beim Zurueckholen sofort neu laedt, zeigt die App bis zum naechsten
(eventuell selbst gedrosselten) Fuenf-Minuten-Tick den eingefrorenen Stand von vor der
Pause.

Festgehalten wird:

1. Wird die Seite sichtbar (``document.visibilityState === 'visible'``), wird sofort
   ``ladeScan()`` aufgerufen — das holt alle Dateien frisch und baut am Ende die
   WebSocket-Verbindung neu auf.
2. Ist die Seite NICHT sichtbar (z.B. das Ereignis feuert beim Verstecken, nicht nur
   beim Zeigen), wird NICHT geladen — kein unnoetiger Netzwerkzugriff waehrend die
   Seite ohnehin gerade in den Hintergrund geht.
3. Feuern mehrere Ereignisse kurz hintereinander (genau das passiert in echten Browsern:
   ``visibilitychange``, ``pageshow`` und ``focus`` koennen alle fuer dasselbe
   Zurueckkommen ausloesen), wird trotzdem nur EINMAL geladen — keine doppelten/dreifachen
   Ladevorgaenge, die sich gegenseitig ueberholen.
4. Liegt das letzte sichtbarkeitsbedingte Laden schon lange genug zurueck, loest ein
   erneutes Sichtbarwerden wieder ganz normal einen neuen Ladevorgang aus — die Sperre
   ist nur ein kurzes Entprellen, keine Dauerblockade.

Geprüft wird die echte Funktion ``beiSichtbarkeitRueckkehr`` aus ``site/template.html``,
ausgeführt in Node mit einem minimalen DOM-Double.
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


def _roh() -> str:
    return VORLAGE.read_text(encoding="utf-8")


def _aufrufe(aufrufe: list[dict], *, letzte_ladung_vor_ms: int | None = None) -> int:
    """Fuehrt ``beiSichtbarkeitRueckkehr`` fuer jeden Eintrag in ``aufrufe`` hintereinander
    aus (jeder Eintrag: {"sichtbar": bool, "danach_ms": int} — ``danach_ms`` Millisekunden
    seit dem vorherigen Aufruf, per Zeit-Offset simuliert) und gibt zurueck, wie oft
    ``ladeScan`` dabei insgesamt aufgerufen wurde."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node nicht vorhanden")
    roh = _roh()

    schritte = []
    for a in aufrufe:
        zustand = "visible" if a["sichtbar"] else "hidden"
        schritte.append(
            f"_jetzt += {a['danach_ms']};\n"
            f"globalThis.document.visibilityState = {json.dumps(zustand)};\n"
            "beiSichtbarkeitRueckkehr();\n"
        )

    vorlauf = "" if letzte_ladung_vor_ms is None else (
        "_letzteSichtbarkeitsLadung = _jetzt;\n"
        f"_jetzt += {letzte_ladung_vor_ms};\n"
    )

    skript = (
        "let _jetzt = 1000000;\n"
        "Date.now = () => _jetzt;\n"
        "let geladen = 0;\n"
        "function ladeScan(){ geladen++; }\n"
        "globalThis.document = { visibilityState: 'visible' };\n"
        "let _letzteSichtbarkeitsLadung = 0;\n"
        + _funktion(roh, "beiSichtbarkeitRueckkehr")
        + "\n"
        + vorlauf
        + "".join(schritte)
        + "console.log(JSON.stringify({geladen}));\n"
    )
    aus = subprocess.run([node, "-e", skript], capture_output=True, text=True, timeout=20)
    if aus.returncode != 0:
        raise AssertionError(aus.stderr)
    return json.loads(aus.stdout)["geladen"]


def test_sichtbar_werden_laedt_sofort_neu():
    n = _aufrufe([{"sichtbar": True, "danach_ms": 0}])
    assert n == 1


def test_unsichtbar_werden_laedt_nicht():
    n = _aufrufe([{"sichtbar": False, "danach_ms": 0}])
    assert n == 0


def test_mehrere_ereignisse_fuer_dasselbe_zurueckkommen_laden_nur_einmal():
    # visibilitychange, pageshow UND focus feuern in echten Browsern oft alle drei fuer
    # dasselbe "die App ist wieder da" — hier simuliert durch drei Aufrufe in derselben
    # Millisekunde.
    n = _aufrufe(
        [
            {"sichtbar": True, "danach_ms": 0},
            {"sichtbar": True, "danach_ms": 0},
            {"sichtbar": True, "danach_ms": 0},
        ]
    )
    assert n == 1


def test_nach_der_kurzen_sperre_laedt_ein_erneutes_sichtbarwerden_wieder():
    n = _aufrufe(
        [
            {"sichtbar": True, "danach_ms": 0},
            {"sichtbar": True, "danach_ms": 6000},
        ]
    )
    assert n == 2


def test_lange_zurueckliegende_vorherige_ladung_blockiert_nichts():
    n = _aufrufe(
        [{"sichtbar": True, "danach_ms": 0}],
        letzte_ladung_vor_ms=999_000,
    )
    assert n == 1
