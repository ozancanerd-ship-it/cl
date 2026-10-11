"""``zeichneDaytrading`` — die neue "Gold Day"-Seite darf nie etwas erfinden.

WARUM ES DIESEN TEST GIBT

Ozan, 11.10. 17:18/17:41: "mach ... extra seite mit gold day trade auf kraken oder
crypto". Die Seite liest ``day_trading.json`` (von ``scripts/gold_daytrading_status.py``,
siehe dessen eigene Tests) und darf dabei NIE einen Alarm, eine Kaufempfehlung oder einen
erfundenen Zustand zeigen — nur den ehrlichen Beobachtungsstand von S9
(``SETUP-BREAKOUT-RETEST-01``, ``IN_VALIDATION``) und die beiden Studien ohne Edge.

Festgehalten wird:

1. Ohne Daten (``GD`` null oder ``aktiv:false``) steht ein ehrlicher "kein Stand"-Kasten,
   kein erfundener Zustand.
2. Ein Nicht-ARMED-Zustand zeigt Zustand/Trend/Text, aber KEINE Entry/SL/TP-Zeilen.
3. ARMED zeigt die volle Geometrie UND eine deutliche "nur Beobachtung"-Warnung —
   niemals ohne diese Warnung.
4. Die Studien-Zusammenfassung erscheint immer, unabhaengig vom S9-Zustand.
5. Der "Kein Alarmkanal"-Hinweis steht IMMER oben, auch wenn alles andere fehlt.
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


def _lauf(gd: dict | None) -> str:
    node = shutil.which("node")
    if not node:
        pytest.skip("node nicht vorhanden")
    roh = VORLAGE.read_text(encoding="utf-8")
    skript = (
        "const esc = s => String(s == null ? '' : s).replace(/[&<>\"']/g, c => "
        "({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',\"'\":'&#39;'}[c]));\n"
        + _funktion(roh, "digits")
        + _funktion(roh, "num")
        + _funktion(roh, "zeit")
        + f"let GD = {json.dumps(gd)};\n"
        + "const _el = {_html: '', set innerHTML(v){this._html=v;}, get innerHTML(){return this._html;}};\n"
        + "const $ = id => id === 'daytrading' ? _el : null;\n"
        + _konst(roh, "DT_ZUSTAND_TEXT")
        + _konst(roh, "DT_ZUSTAND_KLASSE")
        + _konst(roh, "DT_TREND_TEXT")
        + _konst(roh, "DT_RICHTUNG_TEXT")
        + _funktion(roh, "zeichneDaytrading")
        + "zeichneDaytrading();\n"
        + "console.log(JSON.stringify({html: _el.innerHTML}));\n"
    )
    aus = subprocess.run([node, "-e", skript], capture_output=True, text=True, timeout=20)
    if aus.returncode != 0:
        raise AssertionError(aus.stderr)
    return json.loads(aus.stdout)["html"]


_STUDIEN = [
    {
        "titel": "Gold Intraday: Asien-Range-Ausbruch (G1) + Vortages-Sweep (G2)",
        "zeitraum": "PAXG/USDT 15-Min, 01.2023–09.2026",
        "ergebnis": "Kein Edge. G1 OOS −0,06 R, G2 OOS −0,57 R.",
        "doc": "docs/GOLD-DAYTRADING-STUDIE-2026-10.md",
    },
    {
        "titel": "Krypto Mean-Reversion (Bollinger + RSI, stuendlich)",
        "zeitraum": "24 Coins, 1 Std., 01.2025–10.2026",
        "ergebnis": "Kein Edge in keiner Variante.",
        "doc": "docs/MEAN-REVERSION-STUDIE-2026-10.md",
    },
]


def test_ohne_daten_kein_erfundener_zustand():
    html = _lauf(None)
    assert "Kein aktueller Stand" in html
    assert "armed" not in html.lower()
    assert "Kein Alarmkanal" in html


def test_ohne_daten_zeigt_fehlertext_wenn_vorhanden():
    html = _lauf({"aktiv": False, "fehler": "Yahoo nicht erreichbar", "studien": _STUDIEN})
    assert "Yahoo nicht erreichbar" in html
    # Studien stehen trotzdem da, auch ohne aktuellen Kurs.
    assert "GOLD-DAYTRADING-STUDIE-2026-10.md" in html


def test_nicht_armed_zustand_ohne_geometrie():
    gd = {
        "aktiv": True,
        "geprueft": "2026-10-11T18:00:00+00:00",
        "gold": {
            "zustand": "scanning",
            "d1_trend": "trend_down",
            "richtung": None,
            "text": "Kein Breakout-Retest-Muster erkennbar.",
            "hinweis": "SETUP-BREAKOUT-RETEST-01 ... IN_VALIDATION ...",
        },
        "studien": _STUDIEN,
    }
    html = _lauf(gd)
    assert "Kein Muster erkennbar" in html
    assert "Abwärts" in html
    assert "trend_down" not in html  # kein Rohschluessel auf der Seite
    assert "Entry" not in html
    assert "IN_VALIDATION" in html


def test_armed_zeigt_geometrie_und_warnung():
    gd = {
        "aktiv": True,
        "geprueft": "2026-10-11T18:00:00+00:00",
        "gold": {
            "zustand": "armed",
            "richtung": "long",
            "d1_trend": "trend_up",
            "entry": 102.6,
            "sl": 100.5,
            "tp1": 105.75,
            "tp2": 108.9,
            "rr_bis_tp2": 2.9,
            "confidence": 0.81,
            "warnung": "Nur Beobachtung — S9 ist nicht alarmfähig.",
            "text": "Konsolidierung → Ausbruch long → Retest hält",
            "hinweis": "SETUP-BREAKOUT-RETEST-01 ... IN_VALIDATION ...",
        },
        "studien": _STUDIEN,
    }
    html = _lauf(gd)
    assert "102,60" in html or "102.6" in html
    assert "Nur Beobachtung" in html
    assert "Long (steigend)" in html
    assert "Gold-Future" in html  # Hinweis, dass die Marken nicht 1:1 Kraken-Kurse sind


def test_studien_stehen_immer_da():
    gd = {
        "aktiv": True,
        "geprueft": "2026-10-11T18:00:00+00:00",
        "gold": {"zustand": "unbekannt", "text": "Nicht genug Historie.", "hinweis": "x"},
        "studien": _STUDIEN,
    }
    html = _lauf(gd)
    for s in _STUDIEN:
        assert s["doc"] in html
        assert s["ergebnis"] in html
