"""Kein benutzter Bezeichner in der App darf undeklariert sein.

WARUM ES DIESEN TEST GIBT

Am 11. September stand in ``makroKasten()`` ein ``REGIME_TEXT[...]``, und die Tabelle
``REGIME_TEXT`` gab es nirgends. Das Ergebnis war kein fehlendes Wort, sondern eine
``ReferenceError`` mitten in ``zeichneStatus()`` — und weil die Aufbauschritte in
``ladeScan()`` hintereinander in einer Zeile standen, starben alle folgenden mit:
Rangliste, Bilanz, Alarme. Die App zeigte „Scan wird geladen …" und blieb dabei stehen.

Gemerkt hat es niemand, weil eine ``ReferenceError`` in einem ``async``-Aufruf lautlos
ist. Ein fehlender Bezeichner ist aber genau die Art Fehler, die sich mechanisch finden
lässt — also wird sie das ab jetzt.

WIE GROB DAS IST — UND WARUM ABSICHTLICH

Der Test schaut ausschließlich auf Namen in der Form ``GROSS_MIT_UNTERSTRICH``. Das ist
die Schreibweise der Konstanten-Tabellen, um die es geht, und sie kommt in deutschen
Anzeigetexten praktisch nicht vor — der Test braucht deshalb keinen JavaScript-Parser
und keine Liste von Ausnahmen, die bei jedem neuen Satz wächst.

Es ist bewusst **kein** Ersatz für einen Linter. Er fängt eine einzige Fehlerart, die
teuer war, und tut das zuverlässig. Die Gegenprobe steht als zweiter Test daneben:
wenn man die Deklaration von heute wieder entfernt, muss der Test anschlagen — sonst
prüft er nichts und wiegt nur in Sicherheit.
"""

from __future__ import annotations

import re
from pathlib import Path

VORLAGE = Path(__file__).resolve().parents[2] / "site" / "template.html"

#: Namen, die nur in Anzeigetexten oder als Objektschlüssel vorkommen und deshalb keine
#: Bezeichner im Geltungsbereich sind. Kurz halten — wächst die Liste, stimmt etwas
#: nicht mit dem Test.
AUS_TEXTEN = frozenset(
    {
        "A_PLUS",
        "A_MINUS",
        "B_PLUS",
        "NO_TRADE",
        "PUSH_ABOS",
        "VAPID_PRIVATE_KEY",
    }
)

NAME = r"[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+"


def _skript() -> str:
    roh = VORLAGE.read_text(encoding="utf-8")
    bloecke = re.findall(r"<script(?![^>]*src=)[^>]*>(.*?)</script>", roh, re.S)
    assert bloecke, "kein Skriptblock in der Vorlage gefunden"
    return bloecke[-1]


def _fehlende(code: str) -> list[str]:
    benutzt = {m.group(0) for m in re.finditer(NAME, code)}
    deklariert = {
        m.group(1)
        for m in re.finditer(rf"(?:\b(?:const|let|var|function|class)\s+|,\s*)({NAME})\b", code)
    }
    return sorted(benutzt - deklariert - AUS_TEXTEN)


def test_keine_undeklarierten_konstanten() -> None:
    fehlend = _fehlende(_skript())
    assert not fehlend, (
        "Diese Namen werden in site/template.html benutzt, aber nirgends deklariert — "
        f"im Browser gibt das eine ReferenceError: {fehlend}"
    )


def test_der_fehler_von_heute_wuerde_auffallen() -> None:
    """Gegenprobe: ohne sie wäre der Test oben nur Dekoration."""
    kaputt = _skript().replace("const REGIME_TEXT = {", "const NICHTMEHRDA = {", 1)
    assert "REGIME_TEXT" in _fehlende(kaputt)
