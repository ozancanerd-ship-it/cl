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

Nachtrag 02.10. (Depot-Check mit Test-Positionen): „Zukauf vertretbar" stand auch bei
Note WATCH — ohne handelbares Setup — auf Karten, die zwei Sätze vorher „Schlusslicht …
nicht nachkaufen" sagten (Bitcoin, Dash, Zcash). Dazu: „der Moment, in dem ein Stop im
Depot stehen sollte" bei Positionen, deren Stop längst stand, und ein direkter Short, der
mit 43 % Gewinn als „43 % im Minus" bewertet wurde. Festgehalten wird zusätzlich:

4. Ein Zukauf-Satz nur mit handelbarem Setup in Positionsrichtung UND bestandenem
   Alarm-Tor (``alarm.ja``) — dieselbe Prüfung wie fürs Handy.
5. Wo kein Zukauf erlaubt ist, steht „nicht nachkaufen" — nie beides.
6. Steht ein Stop, nennt der Gegen-Satz ihn, statt einen zu verlangen.
7. Bei einem direkten Short wird die relative Stärke gespiegelt.

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
    schein = re.search(r"const SCHEIN_MUSTER[^\n]*\n", roh)
    assert m and schein
    skript = (
        m.group(0)
        + schein.group(0)
        + "".join(
            _funktion(roh, f) + "\n"
            for f in (
                "digits",
                "num",
                "waehrungsZeichen",
                "istSchein",
                "istKurzDirekt",
                "haltUrteil",
            )
        )
        + _funktion(roh, "positionsRat")
        + "\nconst b = JSON.parse(process.argv[1]);\n"
        + "console.log(JSON.stringify(positionsRat(b)));"
    )
    aus = subprocess.run(
        [node, "-e", skript, json.dumps(b)], capture_output=True, text=True, timeout=30, check=True
    )
    return json.loads(aus.stdout)


def _b(
    richtung: str,
    score: float = 72.0,
    *,
    handelbar: bool = True,
    alarm_ja: bool = True,
    rs: float = 50.0,
    **pos: object,
) -> dict:
    return {
        "row": {
            "richtung": richtung,
            "score": score,
            "note": "A−",
            "klasse": "aktien",
            "warnungen": [],
            "handelbar": handelbar,
            "alarm": {"ja": alarm_ja, "grund": "Setup-Art bei Aktien bisher im Minus"},
            "setup": {"name": "Ausbruch aus der Basis"},
            "rs": rs,
            "zusatz": {"renditen": {"r21": -5.0, "r63": 3.0}},
        },
        "pos": {"sym": "MDLZ", **pos},
        "gvPct": -5.0,
        "w": "EUR",
        "halt": {"rs": rs, "ren": {"r21": -5.0, "r63": 3.0}},
    }


def test_short_setup_gegen_gekaufte_position_ist_kein_zukauf():
    r = _rat(_b("short"))
    text = " ".join(r["saetze"])
    assert "Setup nach UNTEN" in text
    assert "vertretbar" not in text
    assert any("Gegenrichtung" in m for m in r["mehr"])


def test_long_setup_mit_hohem_score_darf_zukauf_nennen():
    r = _rat(_b("long"))
    assert any("vertretbar" in s for s in r["saetze"])


def test_short_schein_gegen_long_setup():
    r = _rat(_b("long", hebel_richtung="short"))
    text = " ".join(r["saetze"])
    assert "Setup nach OBEN" in text
    assert "vertretbar" not in text


def test_watch_ohne_handelbares_setup_ist_kein_zukauf():
    r = _rat(_b("long", 80.0, handelbar=False, alarm_ja=False))
    assert "vertretbar" not in " ".join(r["saetze"])
    assert any("kein handelbares Setup" in m for m in r["mehr"])


def test_handelbar_aber_alarm_tor_zu_ist_kein_zukauf():
    r = _rat(_b("long", alarm_ja=False))
    assert "vertretbar" not in " ".join(r["saetze"])
    assert any("kein Alarm" in m and "im Minus" in m for m in r["mehr"])


def test_schlusslicht_sagt_nicht_nachkaufen_und_nie_zukauf():
    r = _rat(_b("long", 70.0, handelbar=False, alarm_ja=False, rs=10.0))
    text = " ".join(r["saetze"])
    assert "Nicht nachkaufen" in text
    assert "Zukauf" not in text


def test_nachzuegler_mit_bewaehrtem_setup_widerspricht_sich_nicht():
    r = _rat(_b("long", rs=35.0))
    text = " ".join(r["saetze"])
    assert "vertretbar" in text
    assert "nicht nachkaufen" not in text.lower()
    assert "Nachkaufen nur über das bewährte Setup" in text


def test_gegen_satz_nennt_den_stehenden_stop():
    r = _rat(_b("short", plan={"stop": 50.97, "ebene": "position"}))
    text = " ".join(r["saetze"])
    assert "dein Stop bei 50,97 €" in text
    assert "stehen sollte" not in text
    ohne = " ".join(_rat(_b("short"))["saetze"])
    assert "stehen sollte" in ohne


def test_direkter_short_spiegelt_die_relative_staerke():
    b = _b("short", rs=10.0, plan={"richtung": "short", "stop": 130.0, "ebene": "position"})
    b.pop("halt")  # die Funktion rechnet das Urteil selbst — gespiegelt
    r = _rat(b)
    text = " ".join(r["saetze"])
    assert "Für deinen Short läuft es: schwächer als 90 %" in text
    assert r["stufe"] == "gut"
    assert "vertretbar" in text  # Short-Setup in Richtung des Shorts, Tor offen
