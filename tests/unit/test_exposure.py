"""scanner/exposure — die Deckel, die vor dem Einstieg greifen.

Warum getestet: dieses Modul beantwortet die Frage, die ein Risikomodell pro Trade
strukturell nicht stellen kann — ob die vierzig halben Prozent, die es gerade
freigegeben hat, nicht dasselbe halbe Prozent sind.

Der Anlass steht in den echten Daten vom 11. September 2026: 40 gleichzeitig aktive
Positionen, 26 davon Krypto, 31 davon long. Am 9. September wurden elf an einem Tag
ausgestoppt. Das war kein Pech in Serie, sondern ein einziger Markttag, elfmal gezaehlt.
"""

from __future__ import annotations

from typing import Any

from trading_agent.scanner.exposure import (
    Entscheidung,
    Grenzen,
    belegung,
    buendel,
    pruefe,
    waehle,
)


def _p(klasse: str = "krypto", richtung: str = "long", name: str = "X") -> dict[str, Any]:
    return {"instrument": name, "klasse": klasse, "richtung": richtung}


def _viele(n: int, klasse: str = "krypto", richtung: str = "long") -> list[dict[str, Any]]:
    return [_p(klasse, richtung, f"{klasse[:3].upper()}{i}") for i in range(n)]


# ── Der Fall, der das Modul ausgeloest hat ──────────────────────────────────────
def test_sechsundzwanzig_krypto_longs_werden_nicht_siebenundzwanzig() -> None:
    """Der 9. September in einer Zeile."""
    offen = _viele(26, "krypto", "long")
    e = pruefe(_p("krypto", "long"), offen)
    assert e.ja is False
    # Der Gesamtdeckel greift zuerst — er ist der grundsaetzlichere.
    assert e.deckel == "max_offen"


def test_buendel_deckel_greift_vor_dem_klassendeckel() -> None:
    """Der Buendel-Deckel benennt das Problem, der Klassendeckel nur ein Symptom."""
    g = Grenzen(
        max_offen=20,
        max_je_klasse=4,
        max_je_buendel=3,
        max_risiko_pct=99.0,
        max_anteil_je_position=0.05,
    )
    offen = _viele(3, "krypto", "long")
    e = pruefe(_p("krypto", "long"), offen, g)
    assert e.ja is False
    assert e.deckel == "max_buendel"
    assert "bewegen sich weitgehend gemeinsam" in e.grund


def test_gegenrichtung_faellt_nicht_unter_denselben_deckel() -> None:
    """Drei Krypto-Longs und ein Krypto-Short sind nicht dieselbe Wette."""
    g = Grenzen(
        max_offen=20,
        max_je_klasse=9,
        max_je_buendel=3,
        max_risiko_pct=99.0,
        max_anteil_je_position=0.05,
    )
    offen = _viele(3, "krypto", "long")
    assert pruefe(_p("krypto", "short"), offen, g).ja is True


def test_aktien_bekommen_keinen_buendeldeckel() -> None:
    """Einzelaktien laufen nicht so eng miteinander wie Kryptowerte."""
    g = Grenzen(
        max_offen=20,
        max_je_klasse=9,
        max_je_buendel=2,
        max_risiko_pct=99.0,
        max_anteil_je_position=0.05,
    )
    offen = _viele(5, "aktien", "long")
    e = pruefe(_p("aktien", "long"), offen, g)
    assert e.ja is True, "nur der Klassendeckel gilt hier, und der ist nicht erreicht"


# ── Die einzelnen Deckel ────────────────────────────────────────────────────────
def test_gesamtzahl() -> None:
    g = Grenzen(max_offen=3, max_risiko_pct=99.0, max_anteil_je_position=0.05)
    assert pruefe(_p(), _viele(2), g).ja is True
    e = pruefe(_p(), _viele(3), g)
    assert e.ja is False and e.deckel == "max_offen"


def test_risikosumme() -> None:
    """Die einzige Zahl, die am Ende zaehlt."""
    g = Grenzen(
        max_offen=99, max_risiko_pct=2.0, risiko_je_trade_pct=0.5, max_anteil_je_position=0.05
    )
    assert pruefe(_p(), _viele(3), g).ja is True  # 1,5 % + 0,5 % = 2,0 %
    e = pruefe(_p(), _viele(4), g)
    assert e.ja is False and e.deckel == "max_risiko"


def test_richtungsdeckel_nennt_die_richtungswette_beim_namen() -> None:
    g = Grenzen(
        max_offen=99,
        max_je_klasse=99,
        max_je_buendel=99,
        max_risiko_pct=99.0,
        max_je_richtung=3,
        max_anteil_je_position=0.02,
    )
    offen = [*_viele(2, "krypto", "long"), *_viele(1, "aktien", "long")]
    e = pruefe(_p("gold", "long"), offen, g)
    assert e.ja is False
    assert "Richtungswette auf den Gesamtmarkt" in e.grund


def test_leeres_depot_laesst_alles_zu() -> None:
    assert pruefe(_p(), []).ja is True


# ── Belegung ────────────────────────────────────────────────────────────────────
def test_belegung_zaehlt_richtig() -> None:
    offen = [*_viele(3, "krypto", "long"), *_viele(2, "aktien", "short")]
    b = belegung(offen)
    assert b.offen == 5
    assert b.je_klasse == {"krypto": 3, "aktien": 2}
    assert b.je_richtung == {"long": 3, "short": 2}
    assert b.je_buendel["krypto-long"] == 3
    assert abs(b.risiko_pct - 2.5) < 1e-9


def test_volles_depot_sagt_das_im_klartext() -> None:
    b = belegung(_viele(8), Grenzen(max_offen=8, max_risiko_pct=99.0))
    assert b.voll is True
    assert any("Warteliste" in s for s in b.saetze)


def test_klumpen_wird_benannt_auch_wenn_noch_platz_ist() -> None:
    g = Grenzen(max_offen=20, max_je_buendel=4, max_risiko_pct=99.0, max_anteil_je_position=0.05)
    b = belegung(_viele(5, "krypto", "long"), g)
    assert b.voll is False
    assert any("mehrfach abgerechnet" in s for s in b.saetze)


def test_belegung_serialisiert_die_grenzen_mit() -> None:
    d = belegung(_viele(2)).as_dict()
    assert d["grenzen"]["max_offen"] == 8
    assert "je_buendel" in d


# ── Auswahl aus einer Rangliste ─────────────────────────────────────────────────
def test_auswahl_nimmt_der_reihe_nach_bis_die_deckel_greifen() -> None:
    g = Grenzen(max_offen=3, max_risiko_pct=99.0, max_je_buendel=99, max_anteil_je_position=0.05)
    kandidaten = _viele(6, "krypto", "long")
    genommen, zurueck = waehle(kandidaten, [], g)
    assert len(genommen) == 3
    assert len(zurueck) == 3
    assert [k["instrument"] for k in genommen] == ["KRY0", "KRY1", "KRY2"]


def test_auswahl_sortiert_nicht_um() -> None:
    """Ein schlechteres Setup vorzuziehen, weil es in eine freie Schublade passt,
    ist genau die Art Optimierung, die Portfolios kaputtmacht."""
    g = Grenzen(
        max_offen=2,
        max_je_buendel=1,
        max_risiko_pct=99.0,
        max_je_klasse=9,
        max_anteil_je_position=0.05,
    )
    kandidaten = [
        _p("krypto", "long", "BESTE"),
        _p("krypto", "long", "ZWEITBESTE"),
        _p("krypto", "short", "DRITTE"),
    ]
    genommen, zurueck = waehle(kandidaten, [], g)
    assert [k["instrument"] for k in genommen] == ["BESTE", "DRITTE"]
    assert zurueck[0][0]["instrument"] == "ZWEITBESTE"
    assert zurueck[0][1].deckel == "max_buendel"


def test_auswahl_beruecksichtigt_was_schon_offen_ist() -> None:
    g = Grenzen(max_offen=4, max_risiko_pct=99.0, max_je_buendel=99, max_anteil_je_position=0.05)
    genommen, zurueckgestellt = waehle(_viele(5), _viele(3, "aktien", "short"), g)
    assert len(genommen) == 1
    assert len(zurueckgestellt) == 4


def test_rechnerisch_moegliche_anzahl_aus_dem_risikodeckel() -> None:
    assert Grenzen(max_risiko_pct=4.0, risiko_je_trade_pct=0.5).rechnerisch_moeglich == 8
    assert Grenzen(max_risiko_pct=3.0, risiko_je_trade_pct=0.25).rechnerisch_moeglich == 12


def test_buendel_schluessel_ist_stabil() -> None:
    assert buendel("Krypto", "LONG") == "krypto-long"
    assert buendel("", "") == "?-?"


def test_entscheidung_serialisiert() -> None:
    d = Entscheidung(True, "ok").as_dict()
    assert d == {"ja": True, "grund": "ok", "deckel": None}


# ── Das Kapital ist bei kleinem Konto der erste Engpass ─────────────────────────
def test_kapital_deckelt_frueher_als_das_risiko() -> None:
    """Acht Positionen zu je einem Viertel des Kapitals waeren zweihundert Prozent."""
    g = Grenzen(max_offen=8, max_anteil_je_position=0.25)
    assert g.bezahlbar == 4
    assert g.rechnerisch_moeglich == 8
    assert g.tatsaechlich_moeglich == 4
    e = pruefe(_p(), _viele(4, "aktien", "long"), g)
    assert e.ja is False and e.deckel == "kapital"
    assert "auf Kredit" in e.grund


def test_kleinere_positionen_lassen_mehr_zu() -> None:
    g = Grenzen(max_offen=8, max_anteil_je_position=0.125, max_je_klasse=9, max_je_buendel=9)
    assert g.bezahlbar == 8
    assert pruefe(_p("aktien"), _viele(4, "aktien", "long"), g).ja is True
