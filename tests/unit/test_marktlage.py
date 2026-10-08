"""Marktlage-Karte: eine live, krypto-eigene Risk-On/Risk-Off-Einschätzung.

WARUM ES DIESEN TEST GIBT

Ozan, 08.10. 20:49, nach dem Krypto-Abverkauf (Liquidationen, ETF-Abflüsse, BTC −4 % in
24 Std.): „falls der komplette Markt wieder sinkt, dass wir jetzt keine Scheiße bauen"
und „wo man reingehen kann, wo man nicht reingehen kann."

Der serverseitige Makro-Regime-Wert (``S.makro.regime``) kommt aus Aktienmärkten (VIX,
S&P 500) und aktualisiert nur einmal am Tag — ein reiner Krypto-Flashcrash zeigt sich
darin erst mit Verzögerung. ``marktLage()`` rechnet deshalb zusätzlich LIVE aus dem
aktuellen Scan, wie viele der gescannten Coins in 24 Std. stark gefallen sind, und zeigt
das jetzt oben in Portfolio UND Rangliste — genau da, wo über neue Käufe entschieden wird.

Festgehalten wird:

1. Fallen mindestens die Hälfte der Coins um mehr als 5 % oder Bitcoin um mindestens 4 %,
   ist die Stufe "risk_off" — mit einem Hinweis, der NICHT zum Kaufen in den Fall rät.
2. Eine ruhige Lage (wenige rote Coins, Bitcoin stabil) bleibt "ruhig", ohne Warnfarbe.
3. Mit weniger als 10 brauchbaren Coin-Zeilen liefert die Funktion nichts (zu wenig für
   eine Aussage), statt eine Zahl aus Rauschen zu behaupten.
4. Die Karte beschreibt die Lage, erfindet aber keine Handelsregel — der Text sagt das
   bei "risk_off" ausdrücklich.

Geprüft wird die echte Funktion ``marktLage``/``marktLageKasten`` aus
``site/template.html``, ausgeführt in Node.
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


def _lauf(gesamt: list[dict], stimmung: dict | None = None, makro: dict | None = None) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node nicht vorhanden")
    roh = VORLAGE.read_text(encoding="utf-8")
    skript = (
        "function esc(s){ return String(s == null ? '' : s); }\n"
        + _funktion(roh, "pct")
        + _funktion(roh, "marktLage")
        + _funktion(roh, "marktLageKasten")
        + f"\nconst S = {{gesamt: {json.dumps(gesamt)}, stimmung: {json.dumps(stimmung)}, "
        + f"makro: {json.dumps(makro)}}};\n"
        + "console.log(JSON.stringify({lage: marktLage(), karte: marktLageKasten()}));\n"
    )
    aus = subprocess.run([node, "-e", skript], capture_output=True, text=True, timeout=20)
    if aus.returncode != 0:
        raise AssertionError(aus.stderr)
    return json.loads(aus.stdout)


def _coins(werte: list[float]) -> list[dict]:
    return [
        {"klasse": "krypto", "instrument": f"COIN{i}USD", "zusatz": {"bewegung_24h_pct": v}}
        for i, v in enumerate(werte)
    ]


def test_breiter_abverkauf_ist_risk_off():
    # 20 Coins, 12 davon (60 %) ueber 5 % im Minus -> risk_off, auch ohne Bitcoin-Zeile.
    werte = [-8.0] * 12 + [-1.0] * 8
    r = _lauf(_coins(werte))
    assert r["lage"]["stufe"] == "risk_off"
    assert "risk_off" not in r["karte"]  # Text, keine Rohdaten erwartet
    assert "Risk-Off" in r["karte"]
    assert "keine neue Regel" in r["karte"]


def test_bitcoin_allein_unter_vier_prozent_reicht_fuer_risk_off():
    werte = [-1.0] * 20
    btc = {"klasse": "krypto", "instrument": "BTCUSD", "zusatz": {"bewegung_24h_pct": -4.5}}
    r = _lauf([*_coins(werte), btc])
    assert r["lage"]["stufe"] == "risk_off"
    assert r["lage"]["btc24h"] == -4.5


def test_ruhige_lage_bleibt_ruhig():
    werte = [0.5, -0.5, 1.0, -1.0, 0.3, -0.3, 0.8, -0.8, 0.2, -0.2, 0.4, -0.4]
    r = _lauf(_coins(werte))
    assert r["lage"]["stufe"] == "ruhig"
    assert "Ruhig" in r["karte"]


def test_zu_wenig_coins_liefert_nichts():
    r = _lauf(_coins([-9.0] * 5))
    assert r["lage"] is None
    assert r["karte"] == ""


def test_angst_gier_text_erscheint_in_der_karte():
    werte = [0.5] * 15
    r = _lauf(_coins(werte), stimmung={"angst_gier": {"wert": 64, "text": "Gier", "vorwoche": 74}})
    assert "64" in r["karte"] and "Gier" in r["karte"]
