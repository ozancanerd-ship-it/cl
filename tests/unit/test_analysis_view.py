"""Zeichnung, MTF-Tabelle, Kommentar — jeder Satz haengt an einer berechneten Zahl."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from trading_agent.core.enums import Direction, Timeframe
from trading_agent.scanner.analysis_view import kommentar, mtf_tabelle, zeichnung

T0 = datetime(2026, 3, 1, tzinfo=UTC)


class E:
    def __init__(self, v: str) -> None:
        self.value = v


@dataclass
class Bar:
    open_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 5.0


@dataclass
class Swing:
    price: float
    timestamp: datetime
    is_high: bool
    label: Any = None
    leg_size_atr: float = 1.2


@dataclass
class Bruch:
    kind: Any
    direction: Any
    broken_level_price: float
    break_bar_timestamp: datetime
    break_close: float
    break_distance_atr: float = 1.0


@dataclass
class Level:
    price: float
    type: Any
    side: Any
    strength: float
    touch_count: int
    state: Any


@dataclass
class Zone:
    zone_low: float
    zone_high: float
    direction: Any
    state: Any
    fill_fraction: float = 0.0
    age_bars: int = 3
    created_bar: datetime = T0


@dataclass
class PD:
    range_low: float
    range_high: float
    pd_position: float
    zone: Any

    @property
    def equilibrium(self) -> float:
        return self.range_low + 0.5 * (self.range_high - self.range_low)


@dataclass
class Regime:
    directional: Any
    directional_score: float = 0.7
    volatility: Any = None
    volatility_pct: float = 40.0
    phase: Any = None


@dataclass
class Tfc:
    timeframe: Timeframe
    bars: tuple = ()
    swings: tuple = ()
    structure_breaks: tuple = ()
    liquidity: tuple = ()
    fvgs: tuple = ()
    order_blocks: tuple = ()
    premium_discount: Any = None
    regime: Any = None
    atr: float = 2.0
    data_confidence: float = 0.9


@dataclass
class Mtf:
    per_tf: dict = field(default_factory=dict)


def _tfc(tf: Timeframe, richtung: str = "trend_up") -> Tfc:
    bars = [Bar(T0 + timedelta(days=i), 100 + i, 102 + i, 98 + i, 101 + i) for i in range(5)]
    return Tfc(
        timeframe=tf,
        bars=tuple(bars),
        swings=(
            Swing(112.0, bars[1].open_time, True, E("hh")),
            Swing(96.0, bars[2].open_time, False, E("hl")),
        ),
        structure_breaks=(Bruch(E("bos"), E("bullish"), 108.0, bars[3].open_time, 109.0),),
        liquidity=(
            Level(120.0, E("equal_highs"), E("buy"), 0.8, 3, E("unswept")),
            Level(92.0, E("swing_low"), E("sell"), 0.5, 2, E("unswept")),
            Level(88.0, E("swing_low"), E("sell"), 0.4, 1, E("swept")),
        ),
        fvgs=(Zone(103.0, 105.0, E("bullish"), E("unmitigated")),),
        order_blocks=(Zone(97.0, 99.0, E("bullish"), E("partial")),),
        premium_discount=PD(90.0, 120.0, 0.35, E("discount")),
        regime=Regime(E(richtung), volatility=E("normal"), phase=E("expansion")),
    )


def _mtf(richtungen: dict[Timeframe, str] | None = None) -> Mtf:
    r = richtungen or dict.fromkeys(
        (Timeframe.D1, Timeframe.H4, Timeframe.H1, Timeframe.M15), "trend_up"
    )
    return Mtf(per_tf={tf: _tfc(tf, v) for tf, v in r.items()})


class Chance:
    def __init__(self, **kw: Any) -> None:
        self.instrument = "TEST"
        self.richtung = Direction.LONG
        self.kurs = 105.0
        self.ziel = 112.0
        self.tp2 = 120.0
        self.tp3 = None
        self.invalidierung = 96.0
        self.rr = 2.1
        self.erwartete_bewegung_pct = 14.3
        self.faktoren = ()
        self.zusatz: dict[str, Any] = {}
        self.__dict__.update(kw)


# ------------------------------------------------------------------ Zeichnung


def test_zeichnung_liefert_je_zeitebene_alles_zum_malen() -> None:
    z = zeichnung(_mtf())
    assert set(z) == {"D1", "H4", "H1", "M15"}
    d1 = z["D1"]
    assert set(d1) >= {"kerzen", "swings", "brueche", "liquiditaet", "zonen", "pd", "atr"}
    assert d1["kerzen"][0][1:] == [100.0, 102.0, 98.0, 101.0, 5.0]
    assert d1["swings"][0]["label"] == "HH"
    assert d1["brueche"][0]["art"] == "BOS"
    assert d1["pd"]["mitte"] == 105.0


def test_zeichnung_zeigt_nur_offene_zonen() -> None:
    """Eine abgearbeitete Zone auf den Chart zu malen waere irrefuehrend."""
    m = _mtf()
    m.per_tf[Timeframe.D1].fvgs = (Zone(103.0, 105.0, E("bullish"), E("mitigated")),)
    z = zeichnung(m)
    arten = [x["art"] for x in z["D1"]["zonen"]]
    assert "FVG" not in arten
    assert "OB" in arten


def test_zeichnung_sortiert_liquiditaet_nach_staerke() -> None:
    z = zeichnung(_mtf())
    st = [x["staerke"] for x in z["D1"]["liquiditaet"]]
    assert st == sorted(st, reverse=True)


# ------------------------------------------------------------------ MTF-Tabelle


def test_mtf_tabelle_hat_je_zeitebene_eine_zeile_mit_satz() -> None:
    zeilen = mtf_tabelle(_mtf(), 105.0)
    assert [z["tf"] for z in zeilen] == ["D1", "H4", "H1", "M15"]
    for z in zeilen:
        assert z["regime"] == "Aufwaertstrend"
        assert z["letzter_bruch"]["art"] == "BOS"
        assert z["satz"].endswith(".")
        assert "BOS" in z["satz"]


def test_mtf_tabelle_nennt_die_naechste_liquiditaet_beidseitig() -> None:
    z = mtf_tabelle(_mtf(), 105.0)[0]
    assert z["liquiditaet_oben"] == 120.0
    assert z["liquiditaet_unten"] == 92.0  # 88 ist abgeraeumt und zaehlt nicht


# ------------------------------------------------------------------ Kommentar


def test_kommentar_beantwortet_alle_vier_fragen() -> None:
    m = _mtf()
    zeilen = mtf_tabelle(m, 105.0)
    k = kommentar(Chance(), zeilen, [])
    assert set(k) == {"was_ich_sehe", "warum_jetzt", "erwartung", "was_waere_falsch"}
    assert len(k["was_ich_sehe"]) == 4
    assert any("112" in s for s in k["erwartung"])
    assert any("96" in s for s in k["was_waere_falsch"])


def test_kommentar_nennt_widersprechende_zeitebenen() -> None:
    m = _mtf({Timeframe.D1: "trend_down", Timeframe.H4: "trend_up"})
    zeilen = mtf_tabelle(m, 105.0)
    k = kommentar(Chance(), zeilen, [])
    assert any("D1" in s and "andersherum" in s for s in k["was_waere_falsch"])


def test_kommentar_nennt_ein_gegenlaeufiges_muster() -> None:
    from trading_agent.scanner.patterns import Muster

    m = _mtf()
    zeilen = mtf_tabelle(m, 105.0)
    gegen = Muster("Doppeltop", Direction.SHORT, 0.9, "zwei Hochs", zeitebene="D1")
    k = kommentar(Chance(), zeilen, [gegen])
    assert any("Doppeltop" in s for s in k["was_waere_falsch"])


def test_kommentar_ohne_ziel_erfindet_keine_erwartung() -> None:
    m = _mtf()
    zeilen = mtf_tabelle(m, 105.0)
    k = kommentar(Chance(ziel=None, tp2=None), zeilen, [])
    assert any("Kein Ziel" in s for s in k["erwartung"])
