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
5. Der Hinweis "Experimentelle Alarme" steht IMMER oben, auch wenn alles andere fehlt
   (Ozan, 20:49: "will auch dafuer alarme bekommen" — Alarme ja, aber nie ohne 🧪).
6. Ein laufender Paper-Trade zeigt Einstieg, aktuellen Stop (nach TP1: Einstand) und R.
7. Die Forward-Bilanz zeigt den Fortschritt Richtung 100 Trades und warnt bei kleiner Zahl.
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


def _lauf(gd: dict | None, dtw: dict | None = None) -> str:
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
        + f"let DTW = {json.dumps(dtw)};\n"
        + "const _el = {_html: '', set innerHTML(v){this._html=v;}, get innerHTML(){return this._html;}};\n"
        + "const $ = id => id === 'daytrading' ? _el : null;\n"
        + _konst(roh, "DT_ZUSTAND_TEXT")
        + _konst(roh, "DT_ZUSTAND_KLASSE")
        + _konst(roh, "DT_TREND_TEXT")
        + _konst(roh, "DT_RICHTUNG_TEXT")
        + _konst(roh, "DT_ART_TEXT")
        + _funktion(roh, "dtR")
        + _funktion(roh, "dtOffenerTrade")
        + _funktion(roh, "dtForward")
        + _konst(roh, "DTW_GRUND")
        + _funktion(roh, "dtKrypto")
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
    assert "Experimentelle Alarme" in html


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


def test_laufender_trade_mit_stop_auf_einstand_nach_tp1():
    gd = {
        "aktiv": True,
        "gold": {"zustand": "scanning", "hinweis": "x"},
        "offen": {
            "signal_ts": "2026-10-11T12:00:00+00:00", "richtung": "long",
            "entry": 4200.0, "sl": 4180.0, "tp1": 4230.0, "tp2": 4260.0,
            "gemeldet": ["NEU", "TP1"], "r_offen": 1.1, "kurs": 4222.0,
        },
        "forward": {"anzahl": 0, "ziel": 100, "summe_r": 0, "letzte": []},
        "studien": _STUDIEN,
    }
    html = _lauf(gd)
    assert "Laufender Paper-Trade" in html
    assert "Long (steigend)" in html
    # Stop aktuell = Einstieg, weil TP1 schon da war
    stop_zeile = html.split("Stop aktuell")[1].split("</div>")[0]
    assert "4.200,00" in stop_zeile
    assert "+1,10 R" in html
    assert "TP1" in html


def test_forward_bilanz_mit_fortschritt_und_warnung():
    gd = {
        "aktiv": True,
        "gold": {"zustand": "scanning", "hinweis": "x"},
        "offen": None,
        "forward": {
            "anzahl": 2, "ziel": 100, "summe_r": 1.25, "schnitt_r": 0.625, "trefferquote": 0.5,
            "letzte": [
                {"signal_ts": "2026-10-01T12:00:00+00:00", "richtung": "short", "r": -1.0,
                 "ausstieg": {"art": "STOP"}},
            ],
        },
        "studien": _STUDIEN,
    }
    html = _lauf(gd)
    assert "2 von 100" in html
    assert "+1,25 R" in html
    assert "50 %" in html
    assert "-1,00 R" in html
    assert "sagt die Bilanz noch nichts" in html
    assert "Laufender Paper-Trade" not in html


def test_kraken_marken_im_laufenden_trade():
    gd = {
        "aktiv": True,
        "paxg_kurs": 4189.6,
        "gold": {"zustand": "scanning", "hinweis": "x"},
        "offen": {
            "signal_ts": "2026-10-11T12:00:00+00:00", "richtung": "long",
            "entry": 4200.0, "sl": 4180.0, "tp1": 4230.0, "tp2": 4260.0,
            "gemeldet": ["NEU"], "r_offen": 0.2, "kraken_faktor": 0.99,
        },
        "forward": {"anzahl": 0, "ziel": 100, "summe_r": 0, "letzte": []},
        "studien": _STUDIEN,
    }
    html = _lauf(gd)
    assert "PAXG/USD" in html
    assert "4.158,00" in html  # 4200 * 0,99
    assert "4.189,60" in html  # aktueller PAXG-Kurs im Zustand


# ------------------------------------------------------------------ Krypto-Ausbruchswelle
# Ozan, 11.10. 22:33: "ich brauche dafuer Alarme ... fuer Krypto und fuer Gold".

_DTW = {
    "aktiv": True, "welle": 14, "min_welle": 12, "btc_ueber_ema200": True,
    "letzte_kerze": "2026-10-11T20:00:00+00:00",
    "offen": [{"sym": "SOL", "klasse": "coin", "signal_ts": "2026-10-11T18:00:00+00:00",
               "einstieg": 150.0, "stop": 146.0, "ausstieg_marke": 147.5, "r_offen": 0.4}],
    "bilanz_krypto": {"anzahl": 3, "ziel": 100, "summe_r": 2.5, "schnitt_r": 0.833,
                      "trefferquote": 0.333, "letzte": [{"sym": "ETH", "signal_ts": "2026-10-10T10:00:00+00:00",
                                                          "r": 3.4, "grund": "AUSSTIEG"}]},
    "bilanz_gold": {"anzahl": 0, "ziel": 100, "summe_r": 0, "letzte": []},
    "studie": {"krypto": {"regel": "Welle-Regel", "status": "KANDIDAT", "geld": "OOS +9,7 %",
                          "kosten": "Bybit-Spot"}, "gold": {"status": "EXPERIMENTELL"}},
}


def test_krypto_welle_mit_offenem_trade_und_bilanz():
    html = _lauf({"aktiv": False, "studien": []}, _DTW)
    assert "Krypto-Ausbruchswelle" in html
    assert "14 von 24" in html and "Welle läuft" in html
    assert "SOL" in html and "+0,40 R" in html and "raus unter" in html
    assert "3 von 100" in html and "+3,40 R" in html
    assert "KANDIDAT" in html and "Bybit-Spot" in html and "OOS +9,7 %" in html


def test_krypto_ohne_welle_sagt_ab_wann():
    dtw = dict(_DTW, welle=5, offen=[])
    html = _lauf({"aktiv": False, "studien": []}, dtw)
    assert "ab 12 Alarm" in html
    assert "Gerade kein offener Tageshandel" in html


def test_krypto_ohne_daten_ehrlich():
    html = _lauf({"aktiv": False, "studien": []}, {"aktiv": False, "fehler": ["Kraken weg"]})
    assert "Kraken weg" in html
