"""Depot-Zuordnung (17.10.): ISIN als Identitaet, keine Doppelten, nie einen Basiswert raten,
Platzhalter-Stueckzahl, S&P 500 aus der Makro-Messung, gemeinsames Risiko (Knock-out-Rechnung)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from trading_agent.portfolio_intel.depot_stops import (
    ScanIndex,
    basis_von,
    depot_bereinigen,
    isin_norm,
    lage_fuer,
    schein_produkt,
)

TEMPLATE = (Path(__file__).resolve().parents[2] / "site" / "template.html").read_text(encoding="utf-8")


def _node(js: str) -> object:
    out = subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def test_isin_pruefziffer_python_und_app_gleich() -> None:
    assert isin_norm("us0378331005") == "US0378331005"
    assert isin_norm("US0378331006") == ""
    assert isin_norm("DE123") == ""
    js = TEMPLATE[TEMPLATE.index("function isinNorm"):TEMPLATE.index("function ausDepotCode")] + (
        "console.log(JSON.stringify([isinNorm('us0378331005'), isinNorm('US0378331006'), isinNorm('DE123')]));"
    )
    assert _node(js) == ["US0378331005", "", ""]


def test_doppelte_werden_nicht_doppelt_gezaehlt() -> None:
    a = {"sym": "TURBO-X-LONG", "konto": "Trade Republic", "menge": 1, "isin": "US0378331005"}
    b = {"sym": "ANDERER-NAME-LONG", "konto": "Trade Republic", "menge": 2, "isin": "US0378331005"}
    c = {"sym": "NVDA", "konto": "Trade Republic", "menge": 3}
    c2 = {"sym": "nvda", "konto": "trade republic", "menge": 4}
    liste, z = depot_bereinigen([a, b, c, c2])
    assert z == {"doppelt_schluessel": 1, "doppelt_isin": 1}
    assert [p["menge"] for p in liste] == [2, 4]  # der spaetere Eintrag gewinnt
    # Zwei verschiedene Scheine (andere ISIN) auf demselben Basiswert bleiben zwei Posten.
    s1 = {"sym": "S1-LONG", "konto": "TR", "basis": "NVDA", "isin": "US0378331005"}
    s2 = {"sym": "S2-LONG", "konto": "TR", "basis": "NVDA", "isin": "DE000BASF111"}
    assert len(depot_bereinigen([s1, s2])[0]) == 2


def test_basiswert_wird_nie_geraten_wenn_offen() -> None:
    assert basis_von({"sym": "TURBO-NETFLIX-LONG"}) is not None  # alter Weg bleibt fuer bekannte Namen
    assert basis_von({"sym": "TURBO-NETFLIX-LONG", "basis_offen": True}) is None
    assert basis_von({"sym": "X-LONG", "basis_offen": True, "basis": "NVDA"}) == ("NVDA", "long")
    j = "function basisVon" in TEMPLATE
    assert j and "pos.basis_offen && !pos.basis" in TEMPLATE


def test_platzhalter_menge_leitet_kein_verhaeltnis_ab() -> None:
    scan = {"eurusd": 1.12, "gesamt": [{"instrument": "NVDA", "kurs": 200.0, "name": "NVIDIA", "klasse": "aktien"}]}
    pos = {"sym": "T-LONG", "basis": "NVDA", "ko": 150.0, "menge": 1, "wert_manuell": 60.0,
           "konto": "Trade Republic", "waehrung": "EUR"}
    idx = ScanIndex(scan)
    mit = schein_produkt(pos, lage_fuer(pos, idx))
    assert mit["kurs"] is not None  # alter Weg: aus eingetragenem Wert abgeleitet
    ph = {**pos, "platzhalter": True}
    ohne = schein_produkt(ph, lage_fuer(ph, idx))
    assert ohne["kurs"] is None and ohne["fehlt"]


def test_sp500_aus_makro_messung_mit_knockout_puffer() -> None:
    scan = {"eurusd": 1.12, "gesamt": [],
            "makro": {"werte": [{"schluessel": "spx", "wert": 7659.44, "stand": "2026-10-16"}]}}
    pos = {"sym": "TR-SP500-LONG", "basis": "SPX", "ko": 6183.07, "konto": "Trade Republic"}
    lg = lage_fuer(pos, ScanIndex(scan))
    assert lg.kurs == 7659.44 and lg.ko_puffer_pct and abs(lg.ko_puffer_pct - 19.28) < 0.05
    # ohne Makro-Messung: kein erfundener Kurs
    lg2 = lage_fuer(pos, ScanIndex({"gesamt": []}))
    assert lg2.kurs is None


def test_gemeinsames_risiko_rechnet_knockout_aus() -> None:
    """Wert × (K(1+s) − KO)/(K − KO): bei −10 % und 10 % Puffer ist der Schein weg."""
    js = TEMPLATE[TEMPLATE.index("const GR_SCHOCKS"):TEMPLATE.index("function gemeinsamesRisikoKasten")].replace("const GR_SCHOCKS", "var GR_SCHOCKS") + """
function f(K,KO,s){ return Math.max(0,(K*(1+s)-KO)/(K-KO)); }
console.log(JSON.stringify([GR_SCHOCKS, f(100,90,-0.05), f(100,90,-0.10), f(100,80,-0.10)]));"""
    schocks, a, b, c = _node(js)  # type: ignore[misc]
    assert schocks[0] == -5 and abs(a - 0.5) < 1e-9 and b == 0 and abs(c - 0.5) < 1e-9
    assert "(t.K * (1 + s) - t.KO) / (t.K - t.KO)" in TEMPLATE


def test_echte_tr_daten_sp500_schein_rendite_am_scheinpreis() -> None:
    """3 Stück, Einstand 13,74 €, Wert 39,39 €: Rendite −4,4 % AM SCHEIN (nicht am Index);
    das Bezugsverhältnis wird aus dem eingetragenen Wert abgeleitet und als solches gekennzeichnet."""
    scan = {"eurusd": 1.1213, "gesamt": [],
            "makro": {"werte": [{"schluessel": "spx", "wert": 7659.44, "stand": "2026-10-16"}]}}
    pos = {"sym": "TR-SP500-6183-LONG", "basis": "SPX", "ko": 6183.07, "menge": 3, "einstieg": 13.74,
           "wert_manuell": 39.39, "platzhalter": False, "konto": "Trade Republic"}
    p = schein_produkt(pos, lage_fuer(pos, ScanIndex(scan)))
    assert p["kurs"] is not None and abs(p["kurs"] - 13.13) < 0.01
    assert "abgeleitet" in p["quelle"] and abs(p["ratio"] - 100.3) < 0.2
    assert abs((p["kurs"] / 13.74 - 1) * 100 - (-4.45)) < 0.1
    # Index-Kerzen kommen im Waechter ueber Yahoo ^GSPC
    import importlib.util

    spec = importlib.util.spec_from_file_location("dw", Path(__file__).resolve().parents[2] / "scripts" / "depot_wache.py")
    dw = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(dw)  # type: ignore[union-attr]
    assert dw.YAHOO_INDEX["SPX"] == "^GSPC"


def test_ungeklaerter_basiswert_bleibt_ohne_rechnung() -> None:
    scan = {"eurusd": 1.12, "gesamt": [{"instrument": "AMZN", "kurs": 250.0, "name": "Amazon", "klasse": "aktien"}]}
    pos = {"sym": "TR-A-194-LONG", "ko": 194.36, "basis_offen": True, "basis_vorschlag": "AMZN",
           "menge": 13, "einstieg": 5.60, "wert_manuell": 65.0, "konto": "Trade Republic"}
    lg = lage_fuer(pos, ScanIndex(scan))
    assert lg.kurs is None  # der Vorschlag fliesst nirgends in die Rechnung
