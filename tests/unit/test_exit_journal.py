"""Rückblick auf Verkaufshinweise: Aufnahme, Messung, keine Zukunfts-Nachträge."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from trading_agent.portfolio_intel.depot_stops import Ereignis, ScanIndex, pruefe_depot
from trading_agent.portfolio_intel.exit_journal import (
    GENUG,
    fortschreiben,
    gegen_pct,
    zusammenfassung,
)

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def _ev(art: str = "ziel", marke: str = "TP1", kurs: float = 100.0, lang: bool = True) -> Ereignis:
    e = Ereignis(art, "SOLUSD|", "SOL", "text", True, marke=marke)
    e.instrument, e.kurs, e.lang = "SOLUSD", kurs, lang
    return e


def _idx(kurs: float | None, jetzt: datetime, alter_min: int = 5) -> ScanIndex:
    scan: dict = {"erzeugt": (jetzt - timedelta(minutes=alter_min)).isoformat(), "gesamt": []}
    if kurs is not None:
        scan["gesamt"].append({"instrument": "SOLUSD", "kurs": kurs, "klasse": "krypto"})
    return ScanIndex(scan, jetzt)


def test_hinweis_wird_einmal_aufgenommen_und_nicht_doppelt() -> None:
    j = fortschreiben([], [_ev()], _idx(100.0, T0), T0)
    assert len(j) == 1 and j[0]["kurs"] == 100.0
    j = fortschreiben(
        j, [_ev()], _idx(100.0, T0 + timedelta(minutes=10)), T0 + timedelta(minutes=10)
    )
    assert len(j) == 1


def test_nur_verkaufsarten_und_nur_mit_messpunkt() -> None:
    nachkauf = _ev("nachkauf")
    ohne_kurs = _ev()
    ohne_kurs.kurs = None
    ohne_richtung = _ev(marke="TP2")
    ohne_richtung.lang = None
    assert fortschreiben([], [nachkauf, ohne_kurs, ohne_richtung], _idx(100.0, T0), T0) == []


def test_horizonte_werden_zum_faelligen_zeitpunkt_gemessen() -> None:
    j = fortschreiben([], [_ev()], _idx(100.0, T0), T0)
    t1 = T0 + timedelta(days=1, hours=1)
    j = fortschreiben(j, [], _idx(90.0, t1), t1)
    assert j[0]["h"] == {"1": 90.0}
    assert gegen_pct(j[0], 1) == pytest.approx(-10.0)  # Kurs fiel -> der Hinweis war richtig


def test_verpasster_horizont_wird_nie_aus_der_zukunft_nachgetragen() -> None:
    j = fortschreiben([], [_ev()], _idx(100.0, T0), T0)
    spaet = T0 + timedelta(days=1, hours=30)  # weit nach dem Fenster
    j = fortschreiben(j, [], _idx(150.0, spaet), spaet)
    assert j[0]["h"]["1"] == "x"
    assert gegen_pct(j[0], 1) is None
    # Auch ein spaeterer Lauf fuellt ihn nicht mehr.
    j = fortschreiben(j, [], _idx(80.0, spaet + timedelta(hours=1)), spaet + timedelta(hours=1))
    assert j[0]["h"]["1"] == "x"


def test_alter_scan_misst_nicht() -> None:
    j = fortschreiben([], [_ev()], _idx(100.0, T0), T0)
    t1 = T0 + timedelta(days=1, hours=1)
    j = fortschreiben(j, [], _idx(90.0, t1, alter_min=240), t1)
    assert "1" not in j[0]["h"]  # nicht gemessen, aber auch nicht als verpasst markiert


def test_short_wird_gespiegelt() -> None:
    j = fortschreiben([], [_ev(lang=False)], _idx(100.0, T0), T0)
    t1 = T0 + timedelta(days=1, hours=1)
    j = fortschreiben(j, [], _idx(90.0, t1), t1)
    assert gegen_pct(j[0], 1) == pytest.approx(
        10.0
    )  # Short: Kurs fiel = lief fuer die Position weiter


def test_zusammenfassung_markiert_kleine_stichproben_als_nicht_belastbar() -> None:
    j = fortschreiben([], [_ev()], _idx(100.0, T0), T0)
    t3 = T0 + timedelta(days=3, hours=1)
    j = fortschreiben(j, [], _idx(110.0, t3), t3)
    z = zusammenfassung(j)
    h3 = z["gruppen"]["teilverkauf"]["h"]["3"]
    assert z["n"] == 1 and h3["n"] == 1 and h3["belastbar"] is False
    assert h3["weiter_gelaufen"] == 1.0 and h3["median_pct"] == pytest.approx(10.0)
    assert GENUG == 30


def test_pruefe_depot_nimmt_hinweis_ins_journal_und_oeffentlich_nur_zaehler() -> None:
    pos = [{"sym": "SOLUSD", "menge": 1, "plan": {"stop": 100.0}}]
    scan = {
        "erzeugt": T0.isoformat(),
        "eurusd": 1.1,
        "gesamt": [
            {
                "instrument": "SOLUSD",
                "kurs": 95.0,
                "klasse": "krypto",
                "waehrung": "USD",
                "name": "SOL",
            }
        ],
    }
    stand, ev, ue = pruefe_depot(pos, scan, {}, jetzt=T0)
    assert [e.art for e in ev] == ["stop"]
    assert stand["exit_journal"][0]["inst"] == "SOLUSD"
    # Oeffentlich: nur Zaehler, keine Namen oder Mengen.
    assert ue["exit_bilanz"]["gruppen"]["stop"]["n"] == 1
    assert "SOLUSD" not in str(ue)
