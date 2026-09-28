"""Neue Daten seit 27.09.: Terminmarkt, Stimmung, Quartalszahlen, Sektoren.

Was hier festgehalten wird:
  * Die Antworten der Anbieter werden richtig gelesen (Einheiten!) — Kraken rechnet die
    Finanzierung stuendlich und als Betrag je Kontrakt, nicht als Prozentsatz.
  * Quartalszahlen in den naechsten drei Tagen sperren den Einstiegs-Alarm, in den
    naechsten zwei Wochen stehen sie als Warnung da.
  * Sektoren mit weniger als drei Aktien werden nicht bewertet.
  * Nichts davon veraendert den Score — es ist Kontext.
"""

from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path
from typing import Any

from trading_agent.data import markt_extras as mx
from trading_agent.data.providers import nasdaq_info as nq
from trading_agent.portfolio_intel.depot_stops import pruefe_depot
from trading_agent.scanner import alarm_tor


def _bsd() -> Any:
    pfad = Path(__file__).resolve().parents[2] / "scripts" / "build_scan_data.py"
    spec = importlib.util.spec_from_file_location("build_scan_data", pfad)
    assert spec and spec.loader
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# --------------------------------------------------------------------------- Terminmarkt


def _ticker(sym: str, mark: float, fr: float, oi: float = 1000.0) -> dict:
    return {
        "symbol": sym,
        "tag": "perpetual",
        "markPrice": mark,
        "fundingRate": fr,
        "fundingRatePrediction": fr,
        "openInterest": oi,
        "indexPrice": mark,
        "volumeQuote": 1_000_000.0,
    }


def test_finanzierung_wird_aus_betrag_und_stunde_gerechnet() -> None:
    # 0,0001 je Stunde bei Markpreis 1 → 0,08 % je 8 h, 87,6 % im Jahr
    t = mx.derivate_tabelle({"tickers": [_ticker("PF_SOLUSD", 1.0, 0.0001)]})
    assert abs(t["SOL"]["funding_8h_pct"] - 0.08) < 1e-9
    assert abs(t["SOL"]["funding_jahr_pct"] - 87.6) < 1e-6
    assert t["SOL"]["oi_usd"] == 1000.0


def test_bitcoin_heisst_am_terminmarkt_xbt() -> None:
    t = mx.derivate_tabelle({"tickers": [_ticker("PF_XBTUSD", 84000.0, 1.0)]})
    assert "BTC" in t and "XBT" not in t
    assert mx.basis_aus_future("PF_ETHUSD") == "ETH"
    assert mx.basis_aus_future("FI_XBTUSD_260925") is None  # kein Perpetual


def test_nur_extreme_finanzierung_wird_zur_warnung() -> None:
    assert mx.derivate_satz({"funding_jahr_pct": 10.0}) is None
    assert "Longs zahlen" in (mx.derivate_satz({"funding_jahr_pct": 80.0}) or "")
    assert "Leerverkäufer" in (mx.derivate_satz({"funding_jahr_pct": -40.0}) or "")
    assert mx.derivate_satz(None) is None


def test_angst_und_gier() -> None:
    daten = [{"value": str(70 - i), "value_classification": "Greed"} for i in range(31)]
    fng = mx.angst_gier_aus({"data": daten})
    assert fng == {"wert": 70.0, "text": "Gier", "vorwoche": 63.0, "vormonat": 40.0}
    assert mx.angst_gier_aus({}) is None


# --------------------------------------------------------------------------- Nasdaq


def test_nasdaq_kalender_und_zusammenfassung() -> None:
    kal = {
        "data": {
            "rows": [
                {
                    "symbol": "TSM",
                    "time": "time-pre-market",
                    "epsForecast": "$4.45",
                    "noOfEsts": "6",
                    "lastYearEPS": "$2.92",
                }
            ]
        }
    }
    z = nq.kalender_zeilen(kal, date(2026, 10, 15))
    assert z == [
        {
            "symbol": "TSM",
            "datum": "2026-10-15",
            "zeit": "vor Börsenbeginn",
            "eps_prognose": 4.45,
            "analysten": 6,
            "eps_vorjahr": 2.92,
        }
    ]
    summ = {
        "data": {
            "summaryData": {
                "Sector": {"value": "Technology"},
                "Industry": {"value": "Semiconductors"},
                "OneYrTarget": {"value": "$315.00"},
                "FiftTwoWeekHighLow": {"value": "$236.54/$164.27"},
                "MarketCap": {"value": "5,424,187,000,000"},
                "Yield": {"value": "0.45%"},
            }
        }
    }
    s = nq.zusammenfassung(summ)
    assert s["sektor"] == "Technologie" and s["hoch52"] == 236.54 and s["tief52"] == 164.27
    assert s["marktwert"] == 5.424187e12 and s["dividende_pct"] == 0.45


def test_kursziel_und_seine_veraenderung() -> None:
    hist = [{"x": 1_000_000 + i * 2_600_000, "y": 100.0 + i * 10} for i in range(6)]
    k = nq.kursziele(
        {
            "data": {
                "consensusOverview": {"priceTarget": 324.32, "buy": 31, "hold": 2, "sell": 0},
                "historicalConsensus": [{"x": h["x"], "y": h["y"]} for h in hist],
            }
        }
    )
    assert k["kursziel"] == 324.32
    assert k["analysten"] == {"kaufen": 31, "halten": 2, "verkaufen": 0}
    assert k["kursziel_3m_pct"] == round((150 / 120 - 1) * 100, 1)


# --------------------------------------------------------------------------- Scan


def test_zahlen_sperren_den_einstieg_und_warnen_vorher() -> None:
    m = _bsd()
    heute = date(2026, 10, 12)
    nah: dict[str, Any] = {"warnungen": ["Makro: irgendwas"]}
    m.aktien_info_anhaengen(
        nah,
        {"sektor": "Technologie", "zahlen": {"datum": "2026-10-15", "zeit": "vor Börsenbeginn"}},
        heute,
    )
    assert nah["termin_sperre"].startswith("Quartalszahlen")
    assert nah["warnungen"][0].startswith("Quartalszahlen") and len(nah["warnungen"]) == 2
    assert nah["info"]["zahlen"]["tage"] == 3
    fern: dict[str, Any] = {}
    m.aktien_info_anhaengen(fern, {"zahlen": {"datum": "2026-10-22"}}, heute)
    assert "termin_sperre" not in fern and fern["warnungen"][0].startswith("Quartalszahlen")
    weit: dict[str, Any] = {}
    m.aktien_info_anhaengen(weit, {"zahlen": {"datum": "2026-11-30"}}, heute)
    assert "termin_sperre" not in weit and "warnungen" not in weit


def test_termin_sperre_haelt_das_alarm_tor_zu() -> None:
    zeile = {
        "note": "A",
        "setup": {"name": "Ruecksetzer im Trend"},
        "klasse": "aktien",
        "richtung": "long",
        "rr": 3.0,
        "einstieg": 100.0,
        "ziel": 104.0,
    }
    ohne = alarm_tor.pruefe_zeile(zeile, {})
    mit = alarm_tor.pruefe_zeile({**zeile, "termin_sperre": "Quartalszahlen Do 15.10."}, {})
    assert not mit.ja
    assert "Quartalszahlen" in mit.grund
    assert any(p.name == "termin" for p in mit.punkte)
    assert not any(p.name == "termin" for p in ohne.punkte)


def test_sektoren_brauchen_mindestens_drei_werte() -> None:
    m = _bsd()

    def z(inst: str, sek: str, rs: float) -> dict:
        return {
            "instrument": inst,
            "klasse": "aktien",
            "rs": rs,
            "info": {"sektor": sek},
            "zusatz": {"renditen": {"r21": rs / 10, "r63": rs / 5}},
        }

    zeilen = [
        z("A", "Energie", 80),
        z("B", "Energie", 70),
        z("C", "Energie", 90),
        z("D", "Technologie", 20),
        z("E", "Technologie", 30),
        z("F", "Technologie", 40),
        z("G", "Versorger", 99),
    ]
    sk = m.sektoren_rechnen(zeilen)
    assert [s["sektor"] for s in sk] == ["Energie", "Technologie"]
    assert sk[0]["rs"] == 80.0 and sk[0]["fuehrer"][0] == "C"
    assert zeilen[0]["info"]["sektor_rang"] == 1 and zeilen[0]["info"]["sektor_von"] == 2
    assert "sektor_rang" not in zeilen[6]["info"]


# --------------------------------------------------------------------------- Depot


def test_quartalszahlen_melden_sich_bei_der_position() -> None:
    scan = {
        "eurusd": 1.1,
        "gesamt": [
            {
                "instrument": "TSM",
                "kurs": 300.0,
                "klasse": "aktien",
                "waehrung": "USD",
                "name": "TSMC",
                "info": {"zahlen": {"datum": "2026-10-15", "tage": 1}},
            },
            {
                "instrument": "NVDA",
                "kurs": 200.0,
                "klasse": "aktien",
                "waehrung": "USD",
                "name": "NVIDIA",
                "info": {"zahlen": {"datum": "2026-10-15", "tage": 2}},
            },
        ],
    }
    turbo = {
        "sym": "TURBO-TSM-LONG",
        "basis": "TSM",
        "ko": 270.0,
        "hebel_richtung": "long",
        "menge": 1,
        "konto": "Trade Republic",
    }
    aktie = {"sym": "NVDA", "menge": 1, "konto": "Trade Republic"}
    _, ev, _ = pruefe_depot([turbo, aktie], scan, {})
    arten = {e.key.split("|")[0]: e.art for e in ev}
    assert arten["TURBO-TSM-LONG"] == "zahlen_ko"  # 10 % Puffer < 13 % (jeder zehnte Termin)
    assert arten["NVDA"] == "zahlen"
