"""scanner/plan — die Ziele und das, was nach dem Einstieg passiert.

Warum getestet: hier entstehen die Zahlen, die in jedem Alarm stehen. Zwei Fehler aus
der Entwicklung sind darin festgehalten:

* TP3 lag unter TP2, weil das Strukturziel schon von TP2 verbraucht war und der
  Rueckfallwert dahinter lag. Ein Plan mit vertauschten Zielen ist wertlos.
* TP2 wurde auf ein Liquiditaetsziel 100 % entfernt gesetzt, weil es das naechste war,
  das die Untergrenze erfuellte. Auf dem Papier ergab das ein CRV von 1:25.
"""

from __future__ import annotations

from trading_agent.scanner.plan import MAX_RISIKO_PCT, baue_plan


def test_ziele_stehen_immer_in_der_richtigen_reihenfolge() -> None:
    p = baue_plan(einstieg=100.0, stop=96.0, lang=True, strukturziele=[101.0, 112.0, 130.0])
    assert p is not None
    assert p.tp1 < p.tp2 < p.tp3, (p.tp1, p.tp2, p.tp3)


def test_ziele_stehen_auch_short_richtig() -> None:
    p = baue_plan(einstieg=100.0, stop=104.0, lang=False, strukturziele=[97.0, 88.0, 70.0])
    assert p is not None
    assert p.tp1 > p.tp2 > p.tp3, (p.tp1, p.tp2, p.tp3)


def test_zu_nahes_strukturziel_wird_nicht_zu_tp1() -> None:
    """Ein „Ziel", das naeher liegt als der Stop weg ist, ist keines."""
    p = baue_plan(einstieg=100.0, stop=96.0, lang=True, strukturziele=[100.5])
    assert p is not None
    assert p.tp1 >= 104.0


def test_zu_fernes_strukturziel_wird_nicht_zu_tp2() -> None:
    """Der Fall, der ein CRV von 1:25 erzeugt hat."""
    p = baue_plan(einstieg=100.0, stop=96.0, lang=True, strukturziele=[200.0])
    assert p is not None
    assert p.crv <= 4.0, p.crv


def test_ohne_strukturziele_traegt_das_risiko_die_ziele() -> None:
    p = baue_plan(einstieg=100.0, stop=96.0, lang=True, strukturziele=[])
    assert p is not None
    assert (p.tp1, p.tp2) == (104.0, 108.0)
    assert p.crv == 2.0


def test_zu_weiter_stop_macht_den_plan_untauglich() -> None:
    p = baue_plan(einstieg=100.0, stop=80.0, lang=True)
    assert p is not None
    assert p.risiko_pct == 20.0
    assert p.untauglich and str(int(MAX_RISIKO_PCT)) in p.untauglich


def test_plan_beschreibt_teilverkauf_einstand_und_trailing() -> None:
    """Die Schritte sind der eigentliche Inhalt — ohne sie ist es nur ein Kursniveau."""
    p = baue_plan(einstieg=100.0, stop=96.0, lang=True, atr=2.0)
    assert p is not None
    text = " ".join(p.schritte).lower()
    assert "drittel" in text
    assert "einstand" in text
    assert "nachziehen" in text
    ausstiege = " ".join(p.ausstiege).lower()
    assert "tagen ohne fortschritt" in ausstiege


def test_stop_gleich_einstieg_gibt_keinen_plan() -> None:
    assert baue_plan(einstieg=100.0, stop=100.0, lang=True) is None
    assert baue_plan(einstieg=0.0, stop=1.0, lang=True) is None


def test_serialisierung_enthaelt_alles_was_die_app_braucht() -> None:
    p = baue_plan(einstieg=100.0, stop=96.0, lang=True)
    assert p is not None
    d = p.as_dict()
    for k in ("einstieg", "stop", "tp1", "tp2", "tp3", "r", "risiko_pct", "crv", "schritte"):
        assert k in d, k
