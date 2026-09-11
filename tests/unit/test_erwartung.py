"""Die Erwartung darf nicht mehr behaupten, als sie weiß."""

from __future__ import annotations

from typing import Any

from trading_agent.scanner import erwartung


def _trade(note: str, setup: str, erreicht: list[str], zustand: str, r: float) -> dict[str, Any]:
    return {
        "note": note,
        "setup": setup,
        "erreicht": erreicht,
        "zustand": zustand,
        "r_drittel": r,
    }


def _buch(n: int, *, note: str = "A", setup: str = "AUSBRUCH", treffer: int = 0) -> list[Any]:
    aus = []
    for i in range(n):
        if i < treffer:
            aus.append(_trade(note, setup, ["TP1"], "ziel_erreicht", 0.33))
        else:
            aus.append(_trade(note, setup, [], "stop", -1.0))
    return aus


def test_zu_wenige_faelle_ergeben_keine_quote() -> None:
    """Unter der Mindestzahl gibt es keine Prozentangabe — auch keine vorsichtige."""
    tab = erwartung.quoten(_buch(erwartung.MIN_FAELLE - 1))
    e = erwartung.rechne(
        einstieg=100,
        stop=95,
        tp1=105,
        tp3=115,
        lang=True,
        note="A",
        setup="AUSBRUCH",
        tabelle=tab,
    )
    assert e.quote is None
    text = " ".join(e.saetze)
    assert "%" in text  # die gerechnete Strecke steht trotzdem da
    assert "zu wenige Signale" in text


def test_feinste_gruppe_mit_genug_faellen_gewinnt() -> None:
    buch = _buch(20, note="A", setup="AUSBRUCH", treffer=10) + _buch(
        40, note="B", setup="RUECKLAUF", treffer=4
    )
    tab = erwartung.quoten(buch)
    q = erwartung.passende(tab, note="A", setup="AUSBRUCH")
    assert q is not None
    assert q.n == 20
    assert q.tp1 == 0.5


def test_grobere_gruppe_wenn_die_feine_zu_duenn_ist() -> None:
    buch = _buch(4, note="A", setup="SELTEN", treffer=4) + _buch(
        40, note="A", setup="AUSBRUCH", treffer=4
    )
    tab = erwartung.quoten(buch)
    q = erwartung.passende(tab, note="A", setup="SELTEN")
    assert q is not None
    assert q.n == 44  # Note A insgesamt, nicht die vier Ausreisser
    assert q.tp1 < 0.5


def test_negative_vergangenheit_wird_ausgesprochen() -> None:
    """Ein schlechter Schnitt darf nicht in einer Prozentzahl verschwinden."""
    tab = erwartung.quoten(_buch(40, treffer=2))
    e = erwartung.rechne(
        einstieg=100,
        stop=95,
        tp1=105,
        tp3=115,
        lang=True,
        note="A",
        setup="AUSBRUCH",
        tabelle=tab,
    )
    text = " ".join(e.saetze)
    assert "spricht hier also gegen den Trade" in text
    assert "keine Vorhersage" in text


def test_short_rechnet_die_strecke_andersherum() -> None:
    tab = erwartung.quoten(_buch(40, treffer=20))
    e = erwartung.rechne(
        einstieg=100,
        stop=105,
        tp1=95,
        tp3=85,
        lang=False,
        note="A",
        setup="AUSBRUCH",
        tabelle=tab,
    )
    assert e.bis_tp1_pct is not None and e.bis_tp1_pct > 0
    assert e.bis_tp3_pct is not None and e.bis_tp3_pct > e.bis_tp1_pct
    assert e.risiko_pct is not None and e.risiko_pct > 0


def test_ohne_buch_keine_zahl_aber_ein_satz() -> None:
    e = erwartung.rechne(
        einstieg=100,
        stop=95,
        tp1=105,
        tp3=115,
        lang=True,
        note="A",
        setup=None,
        tabelle={},
    )
    assert e.quote is None
    assert e.saetze
