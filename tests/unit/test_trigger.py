"""scanner/trigger — die Bestaetigung, die zwischen Umkehr und Durchfall unterscheidet.

Warum getestet: dieses Modul entstand aus einem gemessenen Befund, nicht aus einer
Idee. Die Auswertung der echten Wachliste ergab, dass die Haelfte aller Trades nie auch
nur 0,3 R ins Plus kam — sie liefen ab der ersten Minute gegen uns. Ursache war, dass
eingestiegen wurde, sobald der Kurs eine Marke *beruehrt*. Ein Wasserfall beruehrt jede
Marke unter sich.

Die Tests halten deshalb genau die Faelle fest, die vorher nicht unterschieden wurden:
Antippen und drehen gegen Antippen und durchfallen.
"""

from __future__ import annotations

from dataclasses import dataclass

from trading_agent.scanner.trigger import (
    MAX_UNTERSCHREITUNG_ATR,
    VERFALL_KERZEN,
    pruefe_einstieg,
)


@dataclass(frozen=True)
class K:
    """Eine geschlossene Kerze."""

    open: float
    high: float
    low: float
    close: float


def _ruhig(n: int, um: float = 105.0) -> list[K]:
    """Kerzen deutlich ueber der Marke — sie beruehren sie nicht."""
    return [K(um, um + 1, um - 1, um) for _ in range(n)]


# ── Die Marke muss ueberhaupt erreicht sein ─────────────────────────────────────
def test_ohne_beruehrung_kein_einstieg() -> None:
    b = pruefe_einstieg(_ruhig(6), einstieg=100.0, lang=True)
    assert b.ja is False and b.beruehrt is False
    assert "noch nicht erreicht" in b.grund


def test_zu_wenige_kerzen_geben_kein_ja() -> None:
    """Im Zweifel nicht einsteigen — das ist die Variante, die nichts kostet."""
    b = pruefe_einstieg([K(100, 101, 99, 100)], einstieg=100.0, lang=True)
    assert b.ja is False
    assert "zu wenige" in b.grund


# ── Der Kern: antippen und drehen gegen antippen und durchfallen ────────────────
def test_antippen_und_drehen_wird_bestaetigt() -> None:
    kerzen = [
        *_ruhig(4),
        K(open=103, high=103.5, low=99.5, close=102.9),  # tippt die Marke an, dreht, schliesst oben
    ]
    b = pruefe_einstieg(kerzen, einstieg=100.0, lang=True, atr=2.0)
    assert b.ja is True
    assert b.beruehrt is True
    assert b.kurs == 102.9


def test_antippen_und_am_tief_schliessen_wird_abgelehnt() -> None:
    """Genau der Fall, der die Haelfte der alten Trades ausgemacht hat."""
    kerzen = [
        *_ruhig(4),
        K(open=103, high=103.2, low=99.5, close=99.7),
    ]
    b = pruefe_einstieg(kerzen, einstieg=100.0, lang=True, atr=2.0)
    assert b.ja is False
    assert b.beruehrt is True
    assert "schliesst noch unter" in b.grund


def test_schluss_im_schwachen_teil_reicht_nicht() -> None:
    """Ueber der Marke, aber am unteren Rand der eigenen Spanne: ein Docht."""
    kerzen = [
        *_ruhig(4),
        K(open=100.05, high=104.0, low=99.0, close=100.4),
    ]
    b = pruefe_einstieg(kerzen, einstieg=100.0, lang=True, atr=2.0)
    assert b.ja is False
    assert "Docht" in b.grund


def test_rote_umkehrkerze_wird_akzeptiert() -> None:
    """Die klassische Umkehrkerze am Tief ist oft rot — und trotzdem das Signal.

    Waere hier eine gruene Kerze gefordert, wuerde das Modul genau den Fall ablehnen,
    fuer den es gebaut wurde: langer Docht nach unten, Schluss oben, Koerper egal.
    """
    kerzen = [
        *_ruhig(4),
        K(open=101.5, high=101.6, low=99.5, close=100.9),
    ]
    b = pruefe_einstieg(kerzen, einstieg=100.0, lang=True, atr=2.0)
    assert b.ja is True, "rot, aber Schluss ueber der Marke und im oberen Teil"
    assert b.kurs == 100.9


# ── Durchfall macht die Idee hinfaellig, nicht nur unbestaetigt ─────────────────
def test_durch_die_marke_gefallen_macht_den_plan_hinfaellig() -> None:
    kerzen = [
        *_ruhig(4),
        K(open=103, high=103.2, low=96.0, close=97.0),  # 4 ATR unter die Marke
    ]
    b = pruefe_einstieg(kerzen, einstieg=100.0, lang=True, atr=1.0)
    assert b.ja is False
    assert b.hinfaellig is not None
    assert "Bruch" in b.hinfaellig


def test_knapp_unterschritten_ist_noch_kein_bruch() -> None:
    tiefe = MAX_UNTERSCHREITUNG_ATR * 1.0 * 0.5
    kerzen = [
        *_ruhig(4),
        K(open=103, high=103.5, low=100.0 - tiefe, close=102.9),
    ]
    b = pruefe_einstieg(kerzen, einstieg=100.0, lang=True, atr=1.0)
    assert b.hinfaellig is None
    assert b.ja is True


def test_ohne_atr_wird_nicht_auf_bruch_geprueft() -> None:
    """Ohne Massstab fuer „weit" gibt es kein Urteil ueber „zu weit"."""
    kerzen = [*_ruhig(4), K(open=103, high=103.5, low=80.0, close=102.9)]
    b = pruefe_einstieg(kerzen, einstieg=100.0, lang=True, atr=0.0)
    assert b.hinfaellig is None


# ── Der Zug faehrt ab ───────────────────────────────────────────────────────────
def test_nie_bestaetigte_marke_verfaellt() -> None:
    kerzen = [
        K(open=101, high=101.5, low=99.5, close=100.5),  # Beruehrung ganz am Anfang
        *[K(103, 103.5, 102.5, 103) for _ in range(VERFALL_KERZEN)],
    ]
    b = pruefe_einstieg(kerzen, einstieg=100.0, lang=True, atr=2.0)
    assert b.ja is False
    assert b.hinfaellig is not None
    assert "nie bestaetigt" in b.hinfaellig


def test_frische_beruehrung_verfaellt_nicht() -> None:
    kerzen = [
        *_ruhig(4),
        K(open=103, high=103.5, low=99.5, close=102.9),
    ]
    assert pruefe_einstieg(kerzen, einstieg=100.0, lang=True, atr=2.0).hinfaellig is None


# ── Die strenge Variante wird mitgefuehrt, nicht erzwungen ──────────────────────
def test_schluss_ueber_dem_vorkerzenhoch_gilt_als_stark() -> None:
    kerzen = [
        *_ruhig(3),
        K(open=101, high=101.2, low=99.5, close=100.9),
        K(open=100.9, high=102.5, low=100.0, close=102.4),  # ueber 101.2
    ]
    b = pruefe_einstieg(kerzen, einstieg=100.0, lang=True, atr=2.0)
    assert b.ja is True
    assert b.stark is True


def test_bestaetigung_ohne_vorkerzenhoch_ist_nicht_stark_aber_gueltig() -> None:
    kerzen = [
        *_ruhig(3),
        K(open=101, high=104.0, low=99.5, close=100.6),
        K(open=100.6, high=102.0, low=100.2, close=101.9),  # unter 104
    ]
    b = pruefe_einstieg(kerzen, einstieg=100.0, lang=True, atr=2.0)
    assert b.ja is True
    assert b.stark is False


# ── Short spiegelverkehrt ───────────────────────────────────────────────────────
def test_short_antippen_und_drehen() -> None:
    kerzen = [
        *[K(95, 96, 94, 95) for _ in range(4)],
        K(open=97, high=100.5, low=96.5, close=97.1),  # tippt 100 an, schliesst tief
    ]
    b = pruefe_einstieg(kerzen, einstieg=100.0, lang=False, atr=2.0)
    assert b.ja is True and b.kurs == 97.1


def test_short_durchbruch_nach_oben_macht_hinfaellig() -> None:
    kerzen = [
        *[K(95, 96, 94, 95) for _ in range(4)],
        K(open=97, high=104.0, low=96.5, close=103.0),
    ]
    b = pruefe_einstieg(kerzen, einstieg=100.0, lang=False, atr=1.0)
    assert b.ja is False
    assert b.hinfaellig is not None


def test_short_kerze_schliesst_ueber_der_marke() -> None:
    kerzen = [
        *[K(95, 96, 94, 95) for _ in range(4)],
        K(open=99, high=100.5, low=98.5, close=100.3),
    ]
    b = pruefe_einstieg(kerzen, einstieg=100.0, lang=False, atr=2.0)
    assert b.ja is False
    assert "schliesst noch ueber" in b.grund


# ── Serialisierung ──────────────────────────────────────────────────────────────
def test_urteil_ist_vollstaendig_serialisierbar() -> None:
    kerzen = [*_ruhig(4), K(open=103, high=103.5, low=99.5, close=102.9)]
    d = pruefe_einstieg(kerzen, einstieg=100.0, lang=True, atr=2.0).as_dict()
    assert set(d) == {"ja", "beruehrt", "stark", "hinfaellig", "grund", "kurs"}
    assert d["ja"] is True


def test_mittiger_schluss_gilt_nicht_als_umkehr() -> None:
    """Der Grenzfall, der beim Bauen der Tests zuerst durchrutschte.

    Eine Kerze, die genau in der Mitte ihrer Spanne schliesst, sagt nichts: Kaeufer
    und Verkaeufer haben sich die Waage gehalten. Dass die Schwelle knapp ueber der
    Haelfte liegt, ist deshalb kein willkuerlicher Wert, sondern genau diese Aussage.
    """
    mittig = [*_ruhig(4), K(open=103, high=103.5, low=99.5, close=101.5)]
    assert pruefe_einstieg(mittig, einstieg=100.0, lang=True, atr=2.0).ja is False
    oben = [*_ruhig(4), K(open=103, high=103.5, low=99.5, close=102.9)]
    assert pruefe_einstieg(oben, einstieg=100.0, lang=True, atr=2.0).ja is True
