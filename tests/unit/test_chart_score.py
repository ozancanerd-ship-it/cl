"""scanner/chart_score — die Bewertung des Chartzustands.

Warum getestet: dieser Score entscheidet, worauf Ozan schaut. Beim ersten Lauf waren
drei von sechs Faktoren ausnahmslos null, weil die Primitives Richtung als
``Polarity`` (bullish/bearish) fuehren und der Score gegen ``Direction`` (long/short)
verglich. Der Vergleich war immer False, und das Ergebnis sah aus wie ein ruhiger
Markt statt wie ein Fehler. Genau diese Klasse Fehler faengt dieser Test.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from trading_agent.core.enums import Direction, Timeframe
from trading_agent.scanner.chart_score import MAX_PUNKTE, _passt, bewerte_chart


@dataclass
class _Pol:
    value: str


@dataclass
class _Regime:
    directional: Any


@dataclass
class _Bruch:
    kind: Any
    direction: Any
    broken_level_price: float


@dataclass
class _Zone:
    direction: Any
    state: Any
    zone_low: float
    zone_high: float


@dataclass
class _Level:
    price: float
    strength: float
    state: Any
    type: Any


@dataclass
class _Pd:
    pd_position: float
    zone: Any


@dataclass
class _Swing:
    price: float


@dataclass
class _Struktur:
    last_swing_low: Any = None
    last_swing_high: Any = None


@dataclass
class _Tfc:
    regime: Any = None
    atr: float = 10.0
    structure_breaks: tuple = ()
    fvgs: tuple = ()
    order_blocks: tuple = ()
    displacements: tuple = ()
    liquidity: tuple = ()
    premium_discount: Any = None
    structure: Any = None


@dataclass
class _Mtf:
    per_tf: dict


def _tfc_long(**kw: Any) -> _Tfc:
    kw.setdefault("structure", _Struktur(_Swing(80.0)))
    return _Tfc(regime=_Regime(_Pol("trend_up")), **kw)


# ── der Fehler, der das Ganze stumm machte ───────────────────────────────────────
def test_polarity_und_direction_werden_zusammengebracht() -> None:
    assert _passt(_Pol("bullish"), Direction.LONG)
    assert _passt(_Pol("long"), Direction.LONG)
    assert _passt(_Pol("bearish"), Direction.SHORT)
    assert not _passt(_Pol("bearish"), Direction.LONG)
    assert not _passt(_Pol("bullish"), Direction.SHORT)


def test_bullisher_bruch_zaehlt_auch_als_polarity() -> None:
    """Der Regressionstest zum Befund: 'bullish' muss fuer eine LONG-Wette zaehlen."""
    per_tf = {
        tf: _tfc_long(
            structure_breaks=(_Bruch(_Pol("bos"), _Pol("bullish"), 95.0),),
        )
        for tf in (Timeframe.D1, Timeframe.H4, Timeframe.H1, Timeframe.M15)
    }
    c = bewerte_chart("TEST", _Mtf(per_tf), 100.0)
    struktur = next(f for f in c.faktoren if f.name == "struktur_frisch")
    assert struktur.punkte > 0, "bullisher Bruch muss fuer LONG zaehlen"


def test_alle_timeframes_einig_gibt_volle_punkte() -> None:
    per_tf = dict.fromkeys((Timeframe.D1, Timeframe.H4, Timeframe.H1, Timeframe.M15), _tfc_long())
    c = bewerte_chart("TEST", _Mtf(per_tf), 100.0)
    aus = next(f for f in c.faktoren if f.name == "mtf_ausrichtung")
    assert aus.punkte == MAX_PUNKTE["mtf_ausrichtung"]
    assert c.richtung is Direction.LONG


def test_uneinige_timeframes_geben_weniger() -> None:
    per_tf = {
        Timeframe.D1: _tfc_long(),
        Timeframe.H4: _Tfc(regime=_Regime(_Pol("trend_down"))),
        Timeframe.H1: _Tfc(regime=_Regime(_Pol("unclear"))),
        Timeframe.M15: _Tfc(regime=_Regime(_Pol("unclear"))),
    }
    c = bewerte_chart("TEST", _Mtf(per_tf), 100.0)
    aus = next(f for f in c.faktoren if f.name == "mtf_ausrichtung")
    assert 0 < aus.punkte < MAX_PUNKTE["mtf_ausrichtung"]


def test_kein_trend_gibt_keine_richtung_und_no_trade() -> None:
    per_tf = dict.fromkeys((Timeframe.D1, Timeframe.H4), _Tfc(regime=_Regime(_Pol("unclear"))))
    c = bewerte_chart("TEST", _Mtf(per_tf), 100.0)
    assert c.richtung is None
    assert c.urteil == "NO_TRADE"
    assert c.score < 5


def test_premium_discount_wird_gelesen() -> None:
    """pd_position, nicht position — der zweite stumme Fehler."""
    per_tf = {
        Timeframe.D1: _tfc_long(),
        Timeframe.H4: _tfc_long(premium_discount=_Pd(0.10, _Pol("discount"))),
    }
    c = bewerte_chart("TEST", _Mtf(per_tf), 100.0)
    lage = next(f for f in c.faktoren if f.name == "lage")
    assert lage.punkte > MAX_PUNKTE["lage"] * 0.8, "Long im Discount muss gut bewertet sein"


def test_long_im_premium_wird_abgewertet() -> None:
    per_tf = {
        Timeframe.D1: _tfc_long(),
        Timeframe.H4: _tfc_long(premium_discount=_Pd(0.95, _Pol("premium"))),
    }
    c = bewerte_chart("TEST", _Mtf(per_tf), 100.0)
    lage = next(f for f in c.faktoren if f.name == "lage")
    assert lage.punkte < MAX_PUNKTE["lage"] * 0.2


def test_ziel_ist_die_naechste_unberuehrte_liquiditaet() -> None:
    liq = (
        _Level(105.0, 0.3, _Pol("unswept"), _Pol("pdh")),
        _Level(120.0, 0.3, _Pol("unswept"), _Pol("swing_high")),
        _Level(102.0, 0.3, _Pol("swept"), _Pol("swing_high")),  # erledigt, zaehlt nicht
        _Level(90.0, 0.3, _Pol("unswept"), _Pol("swing_low")),  # falsche Seite
    )
    per_tf = {
        Timeframe.D1: _tfc_long(liquidity=liq),
        Timeframe.H4: _tfc_long(liquidity=liq),
    }
    c = bewerte_chart("TEST", _Mtf(per_tf), 100.0)
    assert c.ziel == 105.0
    assert c.ziel_art == "pdh"


def test_invalidierung_haelt_mindestabstand() -> None:
    """Ein Stop 0,2 % unter dem Kurs ergibt ein R:R von 1:17 und ist in Wahrheit Rauschen."""
    per_tf = {
        Timeframe.H4: _Tfc(
            regime=_Regime(_Pol("trend_up")),
            atr=10.0,
            structure=_Struktur(last_swing_low=_Swing(99.8)),  # nur 0,2 % entfernt
        )
    }
    c = bewerte_chart("TEST", _Mtf(per_tf), 100.0)
    assert c.invalidierung is not None
    assert 100.0 - c.invalidierung >= 1.5 * 10.0 - 1e-9


def test_urteil_braucht_score_UND_chance_risiko() -> None:
    """Ein schoener Chart, dessen Ziel naeher liegt als der Stop, ist kein Trade."""
    per_tf = dict.fromkeys(
        (Timeframe.D1, Timeframe.H4, Timeframe.H1, Timeframe.M15),
        _tfc_long(
            atr=1.0,
            liquidity=(_Level(100.5, 0.3, _Pol("unswept"), _Pol("pdh")),),
            structure=_Struktur(last_swing_low=_Swing(90.0)),
        ),
    )
    c = bewerte_chart("TEST", _Mtf(per_tf), 100.0)
    assert c.rr is not None and c.rr < 1.0
    assert c.urteil in ("WATCH", "NO_TRADE")


def test_score_bleibt_in_der_skala() -> None:
    per_tf = dict.fromkeys(
        (Timeframe.D1, Timeframe.H4, Timeframe.H1, Timeframe.M15),
        _tfc_long(
            structure_breaks=(_Bruch(_Pol("bos"), _Pol("bullish"), 95.0),),
            fvgs=(_Zone(_Pol("bullish"), _Pol("unmitigated"), 98.0, 99.0),),
            displacements=(type("D", (), {"direction": _Pol("bullish"), "net_move_atr": 9.9})(),),
            liquidity=(_Level(200.0, 0.5, _Pol("unswept"), _Pol("pdh")),),
            premium_discount=_Pd(0.0, _Pol("discount")),
            structure=_Struktur(last_swing_low=_Swing(50.0)),
        ),
    )
    c = bewerte_chart("TEST", _Mtf(per_tf), 100.0)
    assert 0.0 <= c.score <= 100.0
    assert sum(MAX_PUNKTE.values()) == 100.0
