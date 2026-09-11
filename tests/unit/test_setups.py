"""scanner/setups — die Frage „was ist das hier fuer ein Setup?".

Warum getestet: dieses Modul entscheidet, ob ein Chart ueberhaupt handelbar heisst.
Faellt die Erkennung stumm aus — etwa weil ein Attribut anders heisst als erwartet —
bekaeme jeder Wert den Deckel „kein Setup mit Namen", und die Rangliste waere
schlagartig leer, ohne dass irgendwo ein Fehler auftauchte. Genau diese Klasse
stiller Ausfaelle hat das Projekt schon zweimal gekostet.

Die Kerzenreihen hier sind gebaut, nicht gezogen: nur so laesst sich pruefen, ob die
Erkennung genau das findet, was drin ist — und genau das NICHT findet, was nicht
drin ist.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from trading_agent.core.enums import Direction, Timeframe
from trading_agent.scanner.setups import (
    Setup,
    _kontraktion,
    _schlusskraft,
    _volumen_quote,
    alle_setups,
    erkenne_setup,
)


@dataclass(frozen=True)
class _Bar:
    open: float
    high: float
    low: float
    close: float
    volume: float = 1000.0


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
class _Tfc:
    bars: tuple = ()
    regime: Any = None
    atr: float = 2.0
    structure_breaks: tuple = ()
    fvgs: tuple = ()
    order_blocks: tuple = ()


def _reihe(preise: list[float], *, spanne: float = 2.0, vol: float = 1000.0) -> list[_Bar]:
    """Kerzen um eine Preislinie herum, mit konstanter Spanne."""
    aus = []
    vor = preise[0]
    for p in preise:
        aus.append(
            _Bar(
                open=vor,
                high=max(vor, p) + spanne / 2,
                low=min(vor, p) - spanne / 2,
                close=p,
                volume=vol,
            )
        )
        vor = p
    return aus


def _trend_hoch(n: int = 60, start: float = 100.0, steigung: float = 0.6) -> list[float]:
    return [start + i * steigung for i in range(n)]


def _per_tf(h4: list[_Bar], *, d1_trend: str = "trend_up", h4_trend: str = "trend_up") -> dict:
    return {
        Timeframe.D1: _Tfc(bars=tuple(h4), regime=_Regime(_Pol(d1_trend))),
        Timeframe.H4: _Tfc(bars=tuple(h4), regime=_Regime(_Pol(h4_trend))),
        Timeframe.H1: _Tfc(bars=tuple(h4), regime=_Regime(_Pol(h4_trend))),
    }


# ── Bausteine ───────────────────────────────────────────────────────────────────
def test_kontraktion_erkennt_engere_spannen() -> None:
    breit = _reihe([100.0] * 8, spanne=6.0)
    eng = _reihe([100.0] * 8, spanne=1.5)
    assert (_kontraktion(breit + eng) or 9) < 0.5


def test_kontraktion_bleibt_bei_gleichen_spannen_neutral() -> None:
    gleich = _reihe([100.0] * 16, spanne=3.0)
    k = _kontraktion(gleich)
    assert k is not None and 0.85 < k < 1.15


def test_volumen_quote_erkennt_versiegen() -> None:
    laut = _reihe([100.0] * 20, vol=5000.0)
    leise = _reihe([100.0] * 5, vol=800.0)
    q = _volumen_quote(laut + leise)
    assert q is not None and q < 0.3


def test_schlusskraft_unterscheidet_docht_von_schluss() -> None:
    stark = _Bar(open=100, high=110, low=99, close=109.5)
    schwach = _Bar(open=100, high=110, low=99, close=100.2)
    assert (_schlusskraft(stark, True) or 0) > 0.9
    assert (_schlusskraft(schwach, True) or 1) < 0.2
    # Bei Short dreht sich die Bewertung um.
    assert (_schlusskraft(schwach, False) or 0) > 0.8


# ── Ruecksetzer im Trend ────────────────────────────────────────────────────────
def test_ruecksetzer_im_aufwaertstrend_wird_erkannt() -> None:
    """Trend laeuft, dann kommt der Kurs zurueck — das Brot-und-Butter-Setup."""
    preise = [*_trend_hoch(50), 135.0, 132.0, 129.0, 127.5]
    bars = _reihe(preise)
    per_tf = _per_tf(bars)
    per_tf[Timeframe.H4].structure_breaks = (_Bruch(_Pol("bos"), _Pol("bullish"), 120.0),)
    per_tf[Timeframe.H4].fvgs = (_Zone(_Pol("bullish"), _Pol("unmitigated"), 125.0, 127.0),)
    s = erkenne_setup(per_tf, 127.5, Direction.LONG, atr=2.0)
    assert s is not None
    assert s.art == "TREND_PULLBACK"
    assert s.trigger  # ein Setup ohne Ausloeser waere nur ein Kursniveau
    assert any("zurueckgekommen" in x for x in s.erfuellt)


def test_gegen_den_tagestrend_gibt_es_keinen_ruecksetzer() -> None:
    """Long, waehrend der Tagestrend faellt: das ist kein Ruecksetzer, das ist dagegen."""
    bars = _reihe([*_trend_hoch(50), 135.0, 130.0])
    per_tf = _per_tf(bars, d1_trend="trend_down", h4_trend="trend_down")
    s = erkenne_setup(per_tf, 130.0, Direction.LONG, atr=2.0)
    assert s is None or s.art != "TREND_PULLBACK"


def test_ueberdehnter_kurs_wird_im_setup_vermerkt() -> None:
    """Senkrecht gelaufen: das Setup darf entstehen, aber die Ueberdehnung muss dranstehen."""
    bars = _reihe([*_trend_hoch(50, steigung=0.4), *range(140, 200, 6)])
    per_tf = _per_tf(bars)
    per_tf[Timeframe.H4].structure_breaks = (_Bruch(_Pol("bos"), _Pol("bullish"), 150.0),)
    s = erkenne_setup(per_tf, 196.0, Direction.LONG, atr=2.0)
    if s is not None and s.art == "TREND_PULLBACK":
        assert any("ueberdehnt" in x for x in s.fehlt), s.fehlt


# ── Ausbruch ────────────────────────────────────────────────────────────────────
def test_ausbruch_aus_enger_basis_mit_volumen() -> None:
    basis = _reihe([100.0, 100.6, 99.8, 100.3] * 6, spanne=3.0, vol=4000.0)
    eng = _reihe([100.1, 100.3, 99.9, 100.2] * 4, spanne=0.8, vol=900.0)
    ausbruch = [_Bar(open=100.2, high=103.0, low=100.0, close=102.9, volume=9000.0)]
    bars = basis + eng + ausbruch
    per_tf = _per_tf(bars)
    s = erkenne_setup(per_tf, 102.9, Direction.LONG, atr=1.0)
    assert s is not None and s.art == "AUSBRUCH", s
    assert any("Volumenschub" in x for x in s.erfuellt)
    assert any("Schluss im starken Drittel" in x for x in s.erfuellt)


def test_ausbruch_ohne_volumen_wird_als_fehlend_vermerkt() -> None:
    basis = _reihe([100.0, 100.6, 99.8, 100.3] * 6, spanne=3.0, vol=4000.0)
    eng = _reihe([100.1, 100.3, 99.9, 100.2] * 4, spanne=0.8, vol=900.0)
    lustlos = [_Bar(open=100.2, high=103.0, low=100.0, close=102.9, volume=600.0)]
    per_tf = _per_tf(basis + eng + lustlos)
    s = erkenne_setup(per_tf, 102.9, Direction.LONG, atr=1.0)
    assert s is not None and s.art == "AUSBRUCH"
    assert any("ohne Volumen" in x for x in s.fehlt), s.fehlt


# ── Rueckeroberung ──────────────────────────────────────────────────────────────
def test_rueckeroberung_nach_liquiditaetsgriff() -> None:
    """Das Tief wird geholt, der Kurs kommt sofort zurueck — die klassische Stelle."""
    vorher = _reihe([100.0, 101.0, 99.5, 100.5] * 6, spanne=1.5)
    griff = [
        _Bar(open=99.0, high=99.4, low=97.0, close=98.0, volume=3000.0),
        _Bar(open=98.0, high=100.5, low=97.8, close=100.4, volume=4000.0),
        _Bar(open=100.4, high=101.8, low=100.2, close=101.7, volume=3500.0),
    ]
    per_tf = _per_tf(vorher + griff)
    treffer = alle_setups(per_tf, 101.7, Direction.LONG, atr=1.0)
    assert any(s.art == "RUECKEROBERUNG" for s in treffer), [s.art for s in treffer]


def test_ohne_griff_keine_rueckeroberung() -> None:
    ruhig = _reihe([100.0, 100.4, 99.8, 100.2] * 9, spanne=1.0)
    per_tf = _per_tf(ruhig)
    treffer = alle_setups(per_tf, 100.2, Direction.LONG, atr=1.0)
    assert not any(s.art == "RUECKEROBERUNG" for s in treffer)


# ── Grenzfaelle ─────────────────────────────────────────────────────────────────
def test_ohne_richtung_kein_setup() -> None:
    assert erkenne_setup(_per_tf(_reihe(_trend_hoch(60))), 100.0, None, atr=2.0) is None


def test_zu_wenig_historie_gibt_kein_setup_statt_zu_krachen() -> None:
    kurz = _reihe([100.0, 101.0, 102.0])
    assert erkenne_setup(_per_tf(kurz), 102.0, Direction.LONG, atr=1.0) is None


def test_kaputte_daten_werfen_nicht() -> None:
    """Ein Provider, der ploetzlich anders aussieht, darf den Scan nicht abbrechen."""

    class Kaputt:
        bars = "keine Kerzen"  # falscher Typ, absichtlich

        def __getattr__(self, _n: str) -> Any:
            raise AttributeError("weg")

    per_tf = {Timeframe.D1: Kaputt(), Timeframe.H4: Kaputt()}
    assert erkenne_setup(per_tf, 100.0, Direction.LONG, atr=1.0) is None


def test_setup_serialisiert_vollstaendig() -> None:
    s = Setup(
        art="AUSBRUCH",
        name="Ausbruch aus der Basis",
        erfuellt=("a", "b"),
        fehlt=("c",),
        trigger="warten auf Schluss",
        qualitaet="brauchbar",
        these="darum",
    )
    d = s.as_dict()
    assert d["art"] == "AUSBRUCH"
    assert d["punkte"] == round(2 / 3, 2)
    assert set(d) == {"art", "name", "erfuellt", "fehlt", "trigger", "qualitaet", "these", "punkte"}
