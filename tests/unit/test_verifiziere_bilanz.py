"""Die unabhaengige Nachrechnung (scripts/verifiziere_bilanz.py) muss mit performance.py
uebereinstimmen — auf kleinen, von Hand gerechneten Faellen UND auf den echten Daten."""

from __future__ import annotations

import pytest
from scripts import verifiziere_bilanz as v

from trading_agent.scanner import performance as p


def _w(**kw):
    basis = {
        "instrument": "XUSDT",
        "klasse": "krypto",
        "zustand": "stop",
        "erreicht": [],
        "einstieg": 100.0,
        "einstiegskurs": 100.0,
        "stop": 95.0,
        "richtung": "long",
        "aufgenommen": "2026-10-01T00:00:00+00:00",
        "zuletzt": "2026-10-02T00:00:00+00:00",
    }
    basis.update(kw)
    return basis


@pytest.mark.parametrize(
    ("kw", "erwartet"),
    [
        ({"zustand": "stop"}, -1.0),
        ({"zustand": "stop", "erreicht": ["TP1"]}, 1 / 3),  # TP1 genommen, Rest am Einstand
        ({"zustand": "ziel_erreicht", "erreicht": ["TP1", "TP2", "TP3"]}, (1 + 2 + 3.5) / 3),
        ({"zustand": "abgelaufen", "erreicht": ["TP1", "TP2"]}, 1.0),
        # invalidiert bei Kurs 97 ohne Ziel: (97-100)/5 = -0.6 R
        ({"zustand": "invalidiert", "ausstiegskurs": 97.0}, -0.6),
        # Short wird gespiegelt: Ausstieg bei 103 = -0.6 R
        (
            {"zustand": "invalidiert", "ausstiegskurs": 103.0, "richtung": "short", "stop": 105.0},
            -0.6,
        ),
    ],
)
def test_eigene_drittel_regel_stimmt_mit_der_handrechnung(kw, erwartet) -> None:
    t = v.trade_r(_w(**kw))
    assert t is not None and t["r"] == pytest.approx(erwartet)
    # ... und performance.py rechnet dasselbe.
    e = p.aus_wachliste({"wachen": {"a": {**_w(**kw), "klasse": "krypto"}}})
    assert e and e[0].r_drittel == pytest.approx(erwartet, abs=1e-9)


def test_nie_eingestiegen_und_ohne_ergebnis_werden_nicht_gezaehlt() -> None:
    nie = v.trade_r(_w(zustand="abgelaufen", einstiegskurs=None))
    assert nie is not None and nie["eingestiegen"] is False and nie["gezaehlt"] is False
    ohne = v.trade_r(_w(zustand="abgelaufen"))
    assert ohne is not None and ohne["eingestiegen"] and not ohne["gezaehlt"]


def test_kosten_in_r_sind_annahme_durch_stopabstand() -> None:
    t = v.trade_r(_w())  # Stop 5 % -> 0,35 % / 5 % = 0,07 R
    assert t is not None and t["kosten"] == pytest.approx(0.07)


def test_teilverkaeufe_summieren_nie_ueber_100_prozent() -> None:
    for erreicht in ([], ["TP1"], ["TP1", "TP2"], ["TP1", "TP2", "TP3"]):
        t = v.trade_r(_w(zustand="ziel_erreicht", erreicht=erreicht))
        assert t is not None and t["anteile"] <= 1.0 + 1e-9


def test_echte_daten_beide_rechnungen_identisch() -> None:
    voll = v.laden()
    eigen = v.pruefe(voll)
    assert eigen["doppelt_nach_instrument_und_aufnahme"] == 0
    assert eigen["anteile_ueber_1"] == 0
    assert v.vergleiche(eigen, voll) == []
