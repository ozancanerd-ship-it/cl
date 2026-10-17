"""Kerzenlesen: erkennt, was da ist — und erfindet nichts."""

from __future__ import annotations

from dataclasses import dataclass

from trading_agent.analysis.candles import erkenne_kerzen, kerzen_urteil


@dataclass
class K:
    open: float
    high: float
    low: float
    close: float


def ruhig(n=10, basis=100.0):
    return [K(basis, basis + 0.5, basis - 0.5, basis + 0.1) for _ in range(n)]


def arten(m):
    return {(x.art, x.richtung) for x in m}


def test_pin_am_tief_ist_long_mit_ortsbonus():
    bars = [*ruhig(), K(100.0, 100.3, 97.0, 100.1)]
    am_tief = erkenne_kerzen(bars, 1.0, tiefs=[97.2])
    frei = erkenne_kerzen(bars, 1.0)
    pin_t = next(x for x in am_tief if x.art == "pin")
    pin_f = next(x for x in frei if x.art == "pin")
    assert pin_t.richtung == "long" and pin_t.ort == "am_tief"
    assert pin_t.staerke > pin_f.staerke  # derselbe Docht ohne Marke ist schwaecher


def test_verschlingen_und_impuls():
    bars = [*ruhig(), K(100.4, 100.5, 99.6, 99.8), K(99.6, 101.9, 99.5, 101.8)]
    m = erkenne_kerzen(bars, 1.0)
    assert ("verschlingen", "long") in arten(m)
    assert ("impuls", "long") in arten(m)


def test_sweep_mit_rueckschluss():
    bars = [*ruhig(), K(99.6, 99.8, 98.5, 99.7)]
    m = erkenne_kerzen(bars, 1.0, tiefs=[99.0])
    assert ("sweep", "long") in arten(m)
    # kein Rueckschluss -> kein Sweep
    bars2 = [*ruhig(), K(99.6, 99.7, 98.5, 98.7)]
    assert ("sweep", "long") not in arten(erkenne_kerzen(bars2, 1.0, tiefs=[99.0]))


def test_inside_ohne_richtung_und_nichts_erfunden():
    bars = [*ruhig(), K(100, 101, 99, 100.2), K(100.1, 100.6, 99.4, 100.2)]
    m = erkenne_kerzen(bars, 1.0)
    assert any(x.art == "inside" and x.richtung is None for x in m)
    assert erkenne_kerzen(ruhig(), 1.0) == []  # ruhige Kerzen: kein Muster
    assert erkenne_kerzen(ruhig(2), 1.0) == []  # zu wenig Daten
    assert erkenne_kerzen(ruhig(), 0.0) == []  # ohne ATR keine Aussage


def test_urteil_gegenmuster_wird_gewarnt():
    bars = [*ruhig(), K(100.0, 103.0, 99.9, 100.1)]  # Docht oben = Ablehnung
    m = erkenne_kerzen(bars, 1.0, hochs=[102.5])
    anteil_long, _, warn = kerzen_urteil(m, "long")
    anteil_short, text, _ = kerzen_urteil(m, "short")
    assert anteil_short > anteil_long == 0.0
    assert warn and "gegen die Idee" in warn[0]
    assert "am Hoch" in text


def test_gewinnschutz_bekommt_kerzensignal():
    from trading_agent.portfolio_intel.gewinnschutz import signale_fuer

    # (o,h,l,c,v): ruhiger Anstieg, dann grosse baerische Kerze nahe am Hoch
    bars = [
        (100 + i * 0.3, 100.6 + i * 0.3, 99.6 + i * 0.3, 100.4 + i * 0.3, 10.0) for i in range(45)
    ]
    top = bars[-1][3]
    bars.append((top, top + 0.2, top - 2.6, top - 2.4, 10.0))
    sig, _, _ = signale_fuer(bars, tf="H4")
    assert any(x.name == "kerze_gegen" for x in sig)
    ruhig_bars = bars[:-1]
    sig2, _, _ = signale_fuer(ruhig_bars, tf="H4")
    assert not any(x.name == "kerze_gegen" for x in sig2)


def test_zeichnung_enthaelt_nur_berechnete_muster():
    from trading_agent.scanner.analysis_view import _kerzenmuster

    @dataclass
    class T:
        bars: tuple
        atr: float
        swings: tuple = ()

    bars = tuple([*ruhig(), K(100.0, 100.3, 97.0, 100.1)])
    assert [m["art"] for m in _kerzenmuster(T(bars, 1.0))] == ["pin"]
    assert _kerzenmuster(T(tuple(ruhig()), 1.0)) == []
