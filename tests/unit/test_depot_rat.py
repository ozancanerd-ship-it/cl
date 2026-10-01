"""Depot-Rat: ein Short-Setup ist kein Grund zum Nachkaufen.

WARUM ES DIESEN TEST GIBT

Design-Runde Depot, 01.10.: Auf der Karte einer gekauften Mondelez-Position stand
gleichzeitig „die Analyse sieht hier gerade ein Setup nach UNTEN" und „Einstiegsseite:
Score 72 — hier wäre heute sogar ein Zukauf vertretbar". Der Score gehoerte zu einem
SHORT. ``positionsRat`` fragte nur nach der Hoehe des Scores, nicht nach seiner Richtung.

Festgehalten wird:

1. Zeigt das Setup gegen die Position, gibt es keinen Zukauf-Satz.
2. Zeigt es in Richtung der Position und ist der Score hoch, bleibt der Satz.
3. Ein Short-Schein ist „gegen" ein LONG-Setup — und der Satz nennt dann „nach OBEN".

Geprüft wird die echte Funktion aus ``site/template.html``, ausgeführt in Node.
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


def _rat(b: dict) -> dict:
    node = shutil.which("node")
    if not node:  # pragma: no cover - nur auf Rechnern ohne Node
        pytest.skip("node nicht vorhanden")
    roh = VORLAGE.read_text(encoding="utf-8")
    m = re.search(r"const RS_FUEHRER[^\n]*\n", roh)
    assert m
    skript = (
        m.group(0)
        + _funktion(roh, "positionsRat")
        + "\nconst b = JSON.parse(process.argv[1]);\n"
        + "console.log(JSON.stringify(positionsRat(b)));"
    )
    aus = subprocess.run(
        [node, "-e", skript, json.dumps(b)], capture_output=True, text=True, timeout=30, check=True
    )
    return json.loads(aus.stdout)


def _b(richtung: str, score: float = 72.0, **pos: object) -> dict:
    return {
        "row": {
            "richtung": richtung,
            "score": score,
            "note": "A−",
            "klasse": "aktien",
            "warnungen": [],
        },
        "pos": {"sym": "MDLZ", **pos},
        "gvPct": -5.0,
        "halt": {"rs": 50.0, "ren": {"r21": -5.0, "r63": 3.0}},
    }


def test_short_setup_gegen_gekaufte_position_ist_kein_zukauf():
    r = _rat(_b("short"))
    text = " ".join(r["saetze"])
    assert "Setup nach UNTEN" in text
    assert "Zukauf vertretbar" not in text
    assert any("Gegenrichtung" in m for m in r["mehr"])


def test_long_setup_mit_hohem_score_darf_zukauf_nennen():
    r = _rat(_b("long"))
    assert any("Zukauf vertretbar" in s for s in r["saetze"])


def test_short_schein_gegen_long_setup():
    r = _rat(_b("long", hebel_richtung="short"))
    text = " ".join(r["saetze"])
    assert "Setup nach OBEN" in text
    assert "Zukauf vertretbar" not in text
