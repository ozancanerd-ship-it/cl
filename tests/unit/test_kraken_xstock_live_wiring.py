"""Kraken-xStocks: der Live-Tick muss bei der RICHTIGEN Aktie ankommen.

WARUM ES DIESEN TEST GIBT

Die Zuordnung Instrument <-> Kraken-Paarname laeuft in zwei Richtungen durch zwei
verschiedene Funktionen: ``krakenPaar`` beim ABONNIEREN ("AAPL" -> "AAPLx/USD", damit
die App das richtige Symbol bei Kraken anmeldet) und ``ausKrakenPaar`` beim EINORDNEN
eines eingehenden Live-Tickers ("AAPLx/USD" -> "AAPL", damit der Kurs in der ``L``-Map
unter dem Namen landet, den der Rest der App kennt). Beide muessen fuer dieselbe Aktie
dasselbe Paar benutzen — klaffen sie auseinander, bekommt entweder keine Aktie einen
Live-Kurs, oder schlimmer: ein Tick landet unter dem falschen Instrumentnamen und zeigt
dort einen falschen Kurs.

Die bisherige Krypto-Logik erkennt ein Paar an seiner Waehrungs-Endung (USD, EUR, USDT,
USDC) — "AAPL" hat keine davon, und "AAPLXUSD" (aus "AAPLx/USD" flachgedrueckt) sieht
wie Krypto-Muell aus. Ohne eine eigene xStock-Tabelle wuerde beides ins Leere laufen
oder, schlimmer, zufaellig auf ein falsches Krypto-Symbol matchen.

Festgehalten wird:

1. ``kryptoSymbole()`` fuellt ``XSTOCK_PAAR``/``XSTOCK_PAAR_RUECK`` aus den
   ``kraken_live``-Feldern im Scan UND nimmt die Aktie selbst in die Abonnementliste auf.
2. ``krakenPaar("AAPL")`` liefert danach exakt den Scan-Paarnamen ("AAPL" -> "AAPLx/USD"),
   nicht den generischen Krypto-Rateversuch.
3. ``ausKrakenPaar("AAPLX/USD")`` liefert wieder "AAPL" — der volle Hin-und-Rueck-Weg.
4. Eine Aktie OHNE ``kraken_live`` taucht nicht in der Abonnementliste auf und liefert
   bei ``krakenPaar`` weiterhin ``null`` — kein falscher Treffer durch Verwechslung mit
   einem Krypto-Muster.
5. Krypto-Paare funktionieren unveraendert weiter (keine Regression durch die neue
   xStock-Pruefung VOR der alten Endungs-Pruefung).
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
    m = re.search(rf"let {name}\s*=|const {name}\s*=", roh)
    assert m, f"Deklaration {name} nicht gefunden"
    ende = roh.index(";", m.start())
    return roh[m.start() : ende + 1] + "\n"


def _skript(scan_gesamt: list[dict], depot_positionen: list[dict]) -> str:
    roh = VORLAGE.read_text(encoding="utf-8")
    return (
        f"let S = {json.dumps({'gesamt': scan_gesamt})};\n"
        + _konst(roh, "XSTOCK_PAAR")
        + _konst(roh, "XSTOCK_PAAR_RUECK")
        + _konst(roh, "KRYPTO_NAMEN_LIVE")
        + f"function ladePositionen(){{ return {json.dumps(depot_positionen)}; }}\n"
        + "function istSchein(p){ return false; }\n"
        + "function scanRow(sym){ return (S.gesamt||[]).find(o => o.instrument === String(sym||'').toUpperCase()) || null; }\n"
        + _funktion(roh, "kryptoSymbole")
        + _funktion(roh, "depotKryptoSymbole")
        + _funktion(roh, "krakenPaar")
        + _funktion(roh, "ausKrakenPaar")
    )


def _lauf(skript_rumpf: str, scan_gesamt: list[dict], depot_positionen: list[dict] | None = None) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node nicht vorhanden")
    skript = _skript(scan_gesamt, depot_positionen or []) + skript_rumpf
    aus = subprocess.run([node, "-e", skript], capture_output=True, text=True, timeout=20)
    if aus.returncode != 0:
        raise AssertionError(aus.stderr)
    return json.loads(aus.stdout)


_SCAN_MIT_XSTOCK = [
    {"instrument": "AAPL", "klasse": "aktien", "kraken_live": "AAPLx/USD"},
    {"instrument": "MSFT", "klasse": "aktien"},  # kein kraken_live: post_only bei Kraken
    {"instrument": "BTCUSDT", "klasse": "krypto"},
]


def test_aktie_mit_kraken_live_landet_in_der_abonnementliste():
    r = _lauf(
        "const syms = kryptoSymbole();"
        "console.log(JSON.stringify({syms}));",
        _SCAN_MIT_XSTOCK,
    )
    assert "AAPL" in r["syms"]
    assert "BTCUSDT" in r["syms"]


def test_krakenpaar_fuer_aktie_mit_live_markt():
    r = _lauf(
        "kryptoSymbole();"  # befuellt XSTOCK_PAAR als Seiteneffekt
        "const p = krakenPaar('AAPL');"
        "console.log(JSON.stringify({p}));",
        _SCAN_MIT_XSTOCK,
    )
    assert r["p"] == "AAPLx/USD"


def test_hin_und_zurueck_landet_wieder_beim_instrumentnamen():
    r = _lauf(
        "kryptoSymbole();"
        "const paar = krakenPaar('AAPL');"
        "const zurueck = ausKrakenPaar(paar);"
        "console.log(JSON.stringify({paar, zurueck}));",
        _SCAN_MIT_XSTOCK,
    )
    assert r["paar"] == "AAPLx/USD"
    assert r["zurueck"] == "AAPL"


def test_aktie_ohne_kraken_live_bleibt_aussen_vor():
    r = _lauf(
        "const syms = kryptoSymbole();"
        "const p = krakenPaar('MSFT');"
        "console.log(JSON.stringify({syms, p}));",
        _SCAN_MIT_XSTOCK,
    )
    assert "MSFT" not in r["syms"]
    assert r["p"] is None


def test_krypto_paare_funktionieren_weiterhin():
    r = _lauf(
        "kryptoSymbole();"
        "const p = krakenPaar('BTCUSDT');"
        "console.log(JSON.stringify({p}));",
        _SCAN_MIT_XSTOCK,
    )
    assert r["p"] == "BTC/USD"
