"""Die Notenskala — sie soll Chancen finden, nicht sie wegdefinieren."""

from __future__ import annotations

import pytest

from trading_agent.scanner.grading import (
    HANDELBAR,
    NOTEN,
    Profil,
    benote,
    confidence,
)


def _n(score: float, rr: float, move: float, profil: Profil = Profil.AGGRESSIV) -> str:
    return benote(score=score, rr=rr, move_pct=move, hat_invalidierung=True, profil=profil).note


def test_ohne_invalidierung_gibt_es_nie_eine_handelbare_note() -> None:
    """Der eine Punkt, den auch das aggressivste Profil nicht aufweicht."""
    for p in Profil:
        b = benote(score=95, rr=8.0, move_pct=40.0, hat_invalidierung=False, profil=p)
        assert b.note == "NO_TRADE"
        assert "Stop" in b.begruendung or "Invalidierung" in b.begruendung


def test_die_leiter_ist_monoton() -> None:
    """Besser in allen drei Groessen darf nie zu einer schlechteren Note fuehren."""
    reihe = [(80, 3.2, 14), (70, 2.5, 9), (60, 2.0, 6), (50, 1.6, 4), (42, 1.3, 2.5)]
    noten = [NOTEN.index(_n(*r)) for r in reihe]
    assert noten == sorted(noten)


def test_aggressiv_laesst_mehr_durch_als_konservativ() -> None:
    fall = (58.0, 1.9, 4.0)
    agg = NOTEN.index(_n(*fall, Profil.AGGRESSIV))
    aus = NOTEN.index(_n(*fall, Profil.AUSGEWOGEN))
    kon = NOTEN.index(_n(*fall, Profil.KONSERVATIV))
    assert agg < aus <= kon


def test_kein_binaeres_urteil_mehr() -> None:
    """Ozans Kritik: 'A oder A+ = Trade, alles andere = NO TRADE' war zu streng."""
    mittelfeld = [(58, 1.9, 4.0), (52, 1.6, 3.0), (45, 1.35, 2.2), (40, 1.25, 1.9)]
    noten = [_n(*fall) for fall in mittelfeld]
    for fall, note in zip(mittelfeld, noten, strict=True):
        assert note in HANDELBAR, f"{fall} -> {note}"
    # Und sie fallen nicht alle auf dieselbe Note — die Abstufung muss etwas tun.
    assert len(set(noten)) >= 3


def test_grosser_move_hebt_die_note() -> None:
    """'Ein Setup mit moeglichem Move von +2 % ist nicht automatisch interessant.'"""
    ohne = _n(40.0, 2.4, 5.0)
    mit = _n(40.0, 2.4, 22.0)
    assert NOTEN.index(mit) < NOTEN.index(ohne)


def test_winziger_move_deckelt_auf_beobachten() -> None:
    """Sauberer Chart, aber nur 0,5 % Luft — fuer einen Swing uninteressant."""
    assert _n(85.0, 5.0, 0.5) == "WATCH"


def test_schlechtes_chance_risiko_bleibt_kein_trade() -> None:
    """Mehr Risikobereitschaft heisst nicht, ein schlechtes Verhaeltnis zu akzeptieren."""
    b = benote(score=75, rr=0.6, move_pct=3.0, hat_invalidierung=True, profil=Profil.AGGRESSIV)
    assert b.note not in HANDELBAR
    assert "CRV" in (b.bremse or "")


def test_begruendung_nennt_die_bremse() -> None:
    b = benote(score=62, rr=1.9, move_pct=4.0, hat_invalidierung=True)
    assert b.note in HANDELBAR
    assert b.bremse and ("Score" in b.bremse or "CRV" in b.bremse or "Bewegung" in b.bremse)


@pytest.mark.parametrize("profil", list(Profil))
def test_jedes_profil_erreicht_a_plus_bei_einem_sehr_guten_chart(profil: Profil) -> None:
    assert _n(90.0, 4.0, 20.0, profil) == "A_PLUS"


def test_confidence_steigt_mit_einigkeit_und_score() -> None:
    tief = confidence(einigkeit=0.2, score=35, faktoren_erfuellt=0.2, daten_ok=True)
    hoch = confidence(einigkeit=1.0, score=85, faktoren_erfuellt=0.8, daten_ok=True)
    assert 0.0 < tief < hoch <= 0.97


def test_confidence_faellt_bei_warnungen_und_schlechten_daten() -> None:
    basis = confidence(einigkeit=0.8, score=70, faktoren_erfuellt=0.6, daten_ok=True)
    mit_warnung = confidence(
        einigkeit=0.8, score=70, faktoren_erfuellt=0.6, daten_ok=True, warnungen=3
    )
    schlecht = confidence(einigkeit=0.8, score=70, faktoren_erfuellt=0.6, daten_ok=False)
    assert mit_warnung < basis
    assert schlecht < basis
