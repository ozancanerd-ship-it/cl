"""Mustererkennung — die Faehigkeit, nicht die Strategie."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from trading_agent.core.enums import Direction
from trading_agent.scanner.patterns import Muster, erkenne_muster

T0 = datetime(2026, 1, 1, tzinfo=UTC)


@dataclass
class B:
    open_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 1000.0


@dataclass
class Sw:
    bar_index: int
    price: float
    is_high: bool
    timestamp: datetime = T0


class Tfc:
    def __init__(self, bars: list[B], swings: list[Sw], atr: float) -> None:
        self.bars = tuple(bars)
        self.swings = tuple(swings)
        self.atr = atr


def _bars(preise: list[float], spanne: float = 1.0) -> list[B]:
    return [B(T0 + timedelta(hours=i), p, p + spanne, p - spanne, p) for i, p in enumerate(preise)]


def _finde(muster: list[Muster], name: str) -> Muster | None:
    return next((m for m in muster if m.name == name), None)


def test_ohne_atr_kein_muster() -> None:
    """Ohne Massstab gibt es keine Toleranz — dann lieber nichts behaupten."""
    assert erkenne_muster(Tfc(_bars([1] * 60), [], 0.0)) == []


def test_doppeltop() -> None:
    sw = [Sw(10, 110.0, True), Sw(15, 100.0, False), Sw(20, 110.2, True)]
    m = _finde(erkenne_muster(Tfc(_bars([100] * 60), sw, 2.0), zeitebene="H4"), "Doppeltop")
    assert m is not None
    assert m.richtung is Direction.SHORT
    assert m.ausloeser == 100.0
    assert m.guete > 0.8


def test_doppelboden() -> None:
    sw = [Sw(10, 90.0, False), Sw(15, 100.0, True), Sw(20, 90.3, False)]
    m = _finde(erkenne_muster(Tfc(_bars([95] * 60), sw, 2.0), zeitebene="H4"), "Doppelboden")
    assert m is not None
    assert m.richtung is Direction.LONG
    assert m.ausloeser == 100.0


def test_zwei_hochs_zu_weit_auseinander_sind_kein_doppeltop() -> None:
    sw = [Sw(10, 110.0, True), Sw(15, 100.0, False), Sw(20, 125.0, True)]
    assert _finde(erkenne_muster(Tfc(_bars([100] * 60), sw, 2.0)), "Doppeltop") is None


def test_kopf_schulter() -> None:
    sw = [
        Sw(5, 108.0, True),
        Sw(8, 100.0, False),
        Sw(12, 120.0, True),
        Sw(15, 101.0, False),
        Sw(20, 108.4, True),
    ]
    m = _finde(erkenne_muster(Tfc(_bars([105] * 60), sw, 3.0), zeitebene="D1"), "Kopf-Schulter")
    assert m is not None
    assert m.richtung is Direction.SHORT
    assert m.ausloeser is not None and 100.0 <= m.ausloeser <= 101.0


def test_aufsteigendes_dreieck() -> None:
    sw = [
        Sw(2, 110.0, True),
        Sw(4, 90.0, False),
        Sw(6, 110.1, True),
        Sw(8, 95.0, False),
        Sw(10, 110.0, True),
        Sw(12, 100.0, False),
    ]
    m = _finde(
        erkenne_muster(Tfc(_bars([105] * 60), sw, 3.0), zeitebene="H4"), "Aufsteigendes Dreieck"
    )
    assert m is not None
    assert m.richtung is Direction.LONG


def test_seitwaertsspanne() -> None:
    sw = [
        Sw(2, 110.0, True),
        Sw(4, 100.0, False),
        Sw(6, 110.1, True),
        Sw(8, 100.1, False),
        Sw(10, 109.9, True),
        Sw(12, 100.0, False),
    ]
    m = _finde(erkenne_muster(Tfc(_bars([105] * 60), sw, 3.0)), "Seitwaertsspanne")
    assert m is not None
    assert m.richtung is None  # eine Spanne zeigt in keine Richtung


def test_kompression_wenn_die_schwankung_einschlaeft() -> None:
    weit = _bars([100] * 20, spanne=5.0)
    eng = _bars([100] * 20, spanne=0.6)
    tfc = Tfc(weit + eng, [], 2.0)
    m = _finde(erkenne_muster(tfc), "Kompression")
    assert m is not None
    assert m.richtung is None
    assert "Schwankung" in m.beschreibung


def test_ausbruch_nach_oben() -> None:
    ruhig = _bars([100] * 22, spanne=1.0)
    raus = _bars([106, 107, 108], spanne=1.0)
    for i, b in enumerate(raus):
        b.open_time = T0 + timedelta(hours=22 + i)
    m = _finde(
        erkenne_muster(Tfc(ruhig + raus, [Sw(1, 101, True), Sw(2, 99, False)], 1.5)),
        "Ausbruch nach oben",
    )
    assert m is not None
    assert m.richtung is Direction.LONG


def test_fehlausbruch_oben_dreht_die_richtung() -> None:
    ruhig = _bars([100] * 22, spanne=1.0)
    falle = [
        B(T0 + timedelta(hours=22), 101, 108, 100, 101),
        B(T0 + timedelta(hours=23), 101, 102, 99, 100),
        B(T0 + timedelta(hours=24), 100, 101, 99, 99.5),
    ]
    m = _finde(
        erkenne_muster(Tfc(ruhig + falle, [Sw(1, 101, True), Sw(2, 99, False)], 1.5)),
        "Fehlausbruch oben",
    )
    assert m is not None
    assert m.richtung is Direction.SHORT


def test_as_dict_ist_serialisierbar() -> None:
    sw = [Sw(10, 110.0, True), Sw(15, 100.0, False), Sw(20, 110.2, True)]
    m = erkenne_muster(Tfc(_bars([100] * 60), sw, 2.0), zeitebene="H4")[0]
    d: dict[str, Any] = m.as_dict()
    assert set(d) >= {"name", "richtung", "guete", "beschreibung", "linien", "zeitebene"}
    assert isinstance(d["linien"], list)
