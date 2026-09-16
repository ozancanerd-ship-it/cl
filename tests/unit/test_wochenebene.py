"""Die Wochenebene — nur fuer Aktien, nie erfunden, nie falsch ausgerichtet.

Ozan: "fuer die Aktien reicht nicht nur M, H und D, sondern du brauchst alle — Wochen
und Jahr, also max; fuer Aktien ist das wichtig, fuer Krypto eher weniger."

Diese Tests halten die drei Zusagen fest, die dahinterstehen:
  1. Wochenkerzen liegen auf Montag 00:00 UTC (der naive Epoch-Modulo legt sie auf
     Donnerstag — genau dieser Fehler steckte im Yahoo-Adapter).
  2. Ohne echte Wochenbars gibt es keine Wochenebene. Aus M5 hochgerechnete Wochen
     waeren eine erfundene Zahl.
  3. Krypto bleibt unveraendert: fehlt die Ebene, faellt sie auch aus dem Nenner der
     Zeitebenen-Einigkeit heraus.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from trading_agent.analysis.mtf import MIN_OPTIONAL_BARS, build_mtf_context
from trading_agent.core.enums import AssetClass, Timeframe
from trading_agent.core.models import OHLCV
from trading_agent.core.time import align_down, is_aligned
from trading_agent.data.providers.yahoo_finance import _align_down
from trading_agent.scanner.chart_score import TF_GEWICHT, _bias


def test_wochenkerzen_liegen_auf_montag_nicht_donnerstag() -> None:
    """Der 1.1.1970 war ein Donnerstag — eine reine Modulo-Rechnung erbt das."""
    # Yahoo stempelt die Wochenkerze auf den Handelsbeginn, hier Montag 13:30 UTC.
    montag = datetime(2026, 9, 7, 13, 30, tzinfo=UTC)
    assert _align_down(montag, Timeframe.W1) == datetime(2026, 9, 7, tzinfo=UTC)
    assert is_aligned(_align_down(montag, Timeframe.W1), Timeframe.W1)


def test_feiertag_am_wochenanfang_verschiebt_die_kerze_nicht() -> None:
    """Faellt der Montag aus, stempelt Yahoo auf Dienstag. Die Woche bleibt dieselbe."""
    dienstag = datetime(2026, 9, 8, 13, 30, tzinfo=UTC)
    assert _align_down(dienstag, Timeframe.W1) == datetime(2026, 9, 7, tzinfo=UTC)


def _bars(tf: Timeframe, n: int, *, start: datetime, schritt: timedelta) -> list[OHLCV]:
    raus = []
    kurs = 100.0
    t = align_down(start, tf)
    for _i in range(n):
        kurs *= 1.004
        raus.append(
            OHLCV(
                instrument="TEST",
                timeframe=tf,
                open_time=t,
                close_time=t + schritt,
                open=kurs,
                high=kurs * 1.01,
                low=kurs * 0.99,
                close=kurs,
                volume=1000.0,
                source="test",
            )
        )
        t = t + schritt
    return raus


def _m5(n: int = 4000) -> list[OHLCV]:
    start = datetime(2026, 1, 5, tzinfo=UTC)
    return _bars(Timeframe.M5, n, start=start, schritt=timedelta(minutes=5))


def test_ohne_gelieferte_wochenbars_gibt_es_keine_wochenebene() -> None:
    """Krypto bekommt keine Wochenbars — dann darf auch keine erfunden werden."""
    m5 = _m5()
    mtf = build_mtf_context(
        m5, instrument="TEST", asset_class=AssetClass.CRYPTO, now=m5[-1].close_time
    )
    assert Timeframe.W1 not in mtf.per_tf


def test_zu_wenige_wochenbars_zaehlen_nicht_als_ebene() -> None:
    """Zehn Wochenkerzen sind kein Wochentrend, sondern ein Ausschnitt."""
    m5 = _m5()
    wenige = _bars(
        Timeframe.W1,
        MIN_OPTIONAL_BARS - 1,
        start=datetime(2024, 1, 1, tzinfo=UTC),
        schritt=timedelta(weeks=1),
    )
    mtf = build_mtf_context(
        m5,
        instrument="TEST",
        asset_class=AssetClass.EQUITY,
        now=m5[-1].close_time,
        native_higher={Timeframe.W1: wenige},
    )
    assert Timeframe.W1 not in mtf.per_tf


def test_mit_genug_wochenbars_laeuft_die_ebene_mit() -> None:
    m5 = _m5()
    viele = _bars(
        Timeframe.W1,
        MIN_OPTIONAL_BARS + 60,
        start=datetime(2023, 1, 2, tzinfo=UTC),
        schritt=timedelta(weeks=1),
    )
    mtf = build_mtf_context(
        m5,
        instrument="TEST",
        asset_class=AssetClass.EQUITY,
        now=m5[-1].close_time,
        native_higher={Timeframe.W1: viele},
    )
    assert Timeframe.W1 in mtf.per_tf
    for b in mtf.per_tf[Timeframe.W1].bars:
        assert is_aligned(b.open_time, Timeframe.W1)


def test_fehlende_wochenebene_veraendert_die_einigkeit_nicht() -> None:
    """Der Nenner zaehlt nur vorhandene Ebenen — sonst haette das Hinzufuegen der Woche
    jede Kryptobewertung mit veraendert, ohne dass dort etwas dazugekommen waere."""

    class _Reg:
        def __init__(self, d: str) -> None:
            self.directional = type("V", (), {"value": d})()

    class _Ctx:
        def __init__(self, d: str) -> None:
            self.regime = _Reg(d)

    ohne = {
        Timeframe.D1: _Ctx("trend_up"),
        Timeframe.H4: _Ctx("trend_up"),
        Timeframe.H1: _Ctx("range"),
        Timeframe.M15: _Ctx("range"),
    }
    richtung_o, einigkeit_o, _ = _bias(dict(ohne))
    assert richtung_o is not None
    # Nenner = 0.40 + 0.30 + 0.20 + 0.10 = 1.0, dafuer 0.70 in Richtung
    assert einigkeit_o == pytest.approx(0.70)

    mit = {**ohne, Timeframe.W1: _Ctx("trend_up")}
    richtung_m, einigkeit_m, _ = _bias(dict(mit))
    assert richtung_m is richtung_o
    # Jetzt zaehlt die Woche mit: (0.70 + 0.35) / 1.35
    assert einigkeit_m == pytest.approx(
        (0.70 + TF_GEWICHT[Timeframe.W1]) / (1.0 + TF_GEWICHT[Timeframe.W1])
    )
    assert einigkeit_m > einigkeit_o


def test_woche_gegen_die_richtung_senkt_die_einigkeit() -> None:
    """Der eigentliche Zweck: eine Aktie gegen ihren Wochentrend soll es schwerer haben."""

    class _Reg:
        def __init__(self, d: str) -> None:
            self.directional = type("V", (), {"value": d})()

    class _Ctx:
        def __init__(self, d: str) -> None:
            self.regime = _Reg(d)

    basis = {
        Timeframe.D1: _Ctx("trend_up"),
        Timeframe.H4: _Ctx("trend_up"),
        Timeframe.H1: _Ctx("trend_up"),
        Timeframe.M15: _Ctx("trend_up"),
    }
    _, einigkeit_ohne, _ = _bias(dict(basis))
    _, einigkeit_gegen, _ = _bias({**basis, Timeframe.W1: _Ctx("trend_down")})
    assert einigkeit_ohne == pytest.approx(1.0)
    assert einigkeit_gegen < einigkeit_ohne
