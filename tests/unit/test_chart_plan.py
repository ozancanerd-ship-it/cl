"""Chart: welcher Plan eingezeichnet wird, und die Plan-Leiste darunter.

WARUM ES DIESEN TEST GIBT

Morgen-Check 01.10.: Der Chart zeigte bei einem laufenden Trade (Mondelez Short) die
frischen Marken des letzten Scans — einen anderen Stop und andere Ziele als den Vertrag,
gegen den der Trade läuft und den Alarme und Bilanz zählen. Den Einstieg zeigte er gar
nicht, obwohl der Schalter „Entry/SL/TP" heisst. Und das Kursschild an der Achse war im
dunklen Thema leer: Text in ``--surface`` (3,5 % Weiss) auf Türkis.

Festgehalten wird:

1. Im laufenden Trade (Wache ``aktiv``) gilt der Vertrag: Einstieg = tatsächlicher
   Einstiegskurs, Stop = Schutz-Stop falls gezogen, R gemessen am ursprünglichen Risiko.
2. Vor dem Einstieg gilt der Vorschlag des Scans (wie in ``ausRanking``).
3. Ohne Richtung kein Plan — der Chart erfindet keine Marken.
4. Die Plan-Leiste rechnet R und Abstand richtig, auch für Shorts.

Geprüft wird der echte Code aus ``site/template.html``, ausgeführt in Node.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

VORLAGE = Path(__file__).resolve().parents[2] / "site" / "template.html"


def _node() -> str:
    node = shutil.which("node")
    if not node:  # pragma: no cover - nur auf Rechnern ohne Node
        pytest.skip("node nicht vorhanden")
    return node


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


def _lauf(code: str, eingabe: dict) -> dict:
    roh = VORLAGE.read_text(encoding="utf-8")
    teile = "\n".join(
        _funktion(roh, n) for n in ("digits", "num", "pct", "chartPlan", "planLeiste")
    )
    skript = (
        "let WL = null;\n" + teile + "\nconst E = JSON.parse(process.argv[1]);\n"
        "WL = E.wl || null;\n" + code
    )
    aus = subprocess.run(
        [_node(), "-e", skript, json.dumps(eingabe)],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    return json.loads(aus.stdout)


_CHANCE = {
    "richtung": "short",
    "kurs": 57.82,
    "einstieg": 58.9,
    "invalidierung": 59.95,
    "ziel": 56.5,
    "tp2": 55.9,
    "tp3": 55.0,
    "handelbar": True,
}
_WACHE = {
    "instrument": "MDLZ",
    "zustand": "aktiv",
    "richtung": "short",
    "einstieg": 59.55,
    "einstiegskurs": 59.55,
    "stop": 60.68,
    "tp1": 57.78,
    "tp2": 55.83,
    "tp3": 55.26,
    "erreicht": [],
    "schutz": None,
    "aufgenommen": "2026-09-29T13:52:44+00:00",
}


def test_laufender_trade_zeigt_den_vertrag_nicht_den_scan():
    p = _lauf(
        "console.log(JSON.stringify(chartPlan('MDLZ', E.c)));",
        {"c": _CHANCE, "wl": {"wachen": [_WACHE]}},
    )
    assert p["quelle"] == "vertrag"
    assert p["stop"] == 60.68 and p["einstieg"] == 59.55
    assert (p["tp1"], p["tp2"], p["tp3"]) == (57.78, 55.83, 55.26)
    assert abs(p["risiko"] - 1.13) < 1e-9


def test_schutz_stop_ersetzt_den_stop_r_bleibt_am_urspruenglichen_risiko():
    w = {**_WACHE, "schutz": 59.55, "erreicht": ["TP1"], "einstiegskurs": 59.6}
    p = _lauf(
        "console.log(JSON.stringify(chartPlan('MDLZ', E.c)));",
        {"c": _CHANCE, "wl": {"wachen": [w]}},
    )
    assert p["stop"] == 59.55 and p["schutz"] is True
    assert p["einstieg"] == 59.6  # tatsaechlicher Einstiegskurs
    assert abs(p["risiko"] - 1.13) < 1e-9  # geplanter Einstieg bis urspruenglicher Stop
    assert p["erreicht"] == ["TP1"]


def test_vor_dem_einstieg_gilt_der_scan():
    w = {**_WACHE, "zustand": "wartet_auf_einstieg"}
    p = _lauf(
        "console.log(JSON.stringify(chartPlan('MDLZ', E.c)));",
        {"c": _CHANCE, "wl": {"wachen": [w]}},
    )
    assert p["quelle"] == "scan"
    assert p["einstieg"] == 58.9 and p["stop"] == 59.95 and p["tp1"] == 56.5


def test_ohne_richtung_kein_plan():
    p = _lauf(
        "console.log(JSON.stringify(chartPlan('X', E.c)));",
        {"c": {**_CHANCE, "richtung": None}, "wl": None},
    )
    assert p is None


def test_plan_leiste_rechnet_r_und_abstand_fuer_einen_short():
    html = _lauf(
        "console.log(JSON.stringify(planLeiste(chartPlan('MDLZ', E.c), 57.82)));",
        {"c": _CHANCE, "wl": {"wachen": [_WACHE]}},
    )
    # Stop: -1,0 R; TP1 (57,78) = (59,55-57,78)/1,13 = +1,6 R; TP3 = +3,8 R
    assert "−1,0 R" in html
    assert "+1,6 R" in html and "+3,8 R" in html
    # Im Trade steht beim Einstieg der Stand: (59,55-57,82)/1,13 = +1,5 R
    assert "im Trade" in html and "jetzt +1,5 R" in html
    # Abstand des Stops zum Kurs: (60,68-57,82)/57,82 = +4,9 %
    assert "+4,9 %" in html


def test_plan_leiste_vor_dem_einstieg_zeigt_limit_und_restweg():
    c = {
        **_CHANCE,
        "richtung": "long",
        "kurs": 203.59,
        "einstieg": 197.55,
        "invalidierung": 193.76,
        "ziel": 208.26,
        "tp2": 211.45,
        "tp3": 214.89,
    }
    html = _lauf(
        "console.log(JSON.stringify(planLeiste(chartPlan('ANET', E.c), 203.59)));",
        {"c": c, "wl": None},
    )
    assert "Limit" in html and "noch -3,0 %" in html
    assert "+2,8 R" in html  # (208,26-197,55)/3,79


def test_kursschild_und_wischen():
    roh = VORLAGE.read_text(encoding="utf-8")
    block = roh[
        roh.index("const chart = (()=>{") : roh.index(
            "/* ============================================================ MARKT / RADAR"
        )
    ]
    # Kein Text mehr in der halb durchsichtigen Flaechenfarbe
    assert "--surface" not in block
    # Senkrecht wischen scrollt die Seite
    assert "touch-action:pan-y" in roh
