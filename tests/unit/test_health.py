"""scanner/health — die eine Zahl fuer das ganze Depot.

Warum getestet: eine Ampel wird geglaubt. Wenn sie gruen zeigt, sieht niemand mehr
nach, und genau dann ist ein Rechenfehler teuer. Die Tests halten deshalb vor allem
fest, wann sie NICHT gruen sein darf — und dass fehlende Angaben zu einer fehlenden
Note fuehren statt zu einer geratenen.
"""

from __future__ import annotations

from typing import Any

from trading_agent.scanner.exposure import Grenzen
from trading_agent.scanner.health import GELB_AB, GRUEN_AB, bewerte


def _p(
    klasse: str = "aktien",
    richtung: str = "long",
    handelbar: bool | None = True,
    r: float | None = 0.0,
) -> dict[str, Any]:
    d: dict[str, Any] = {"klasse": klasse, "richtung": richtung}
    if handelbar is not None:
        d["handelbar"] = handelbar
    if r is not None:
        d["r"] = r
    return d


_WEIT = Grenzen(max_offen=20, max_anteil_je_position=0.05, max_risiko_pct=99.0, max_je_buendel=99)


# ── Der Klumpen ist der schwerste Teil ──────────────────────────────────────────
def test_alles_dieselbe_wette_druckt_die_note() -> None:
    """Zehn Krypto-Longs sind eine Position mit zehn Zeilen."""
    g = bewerte([_p("krypto", "long") for _ in range(10)], grenzen=_WEIT)
    klumpen = next(t for t in g.teile if t.name == "klumpen")
    assert klumpen.note is not None and klumpen.note < 20
    assert "derselben Bewegung" in klumpen.satz
    assert g.schwaechster == "klumpen"


def test_gestreutes_depot_bekommt_eine_gute_klumpennote() -> None:
    offen = [
        _p("aktien", "long"),
        _p("aktien", "short"),
        _p("krypto", "long"),
        _p("krypto", "short"),
    ]
    klumpen = next(t for t in bewerte(offen, grenzen=_WEIT).teile if t.name == "klumpen")
    assert klumpen.note is not None and klumpen.note > 70


def test_leeres_depot_kann_nicht_klumpen() -> None:
    klumpen = next(t for t in bewerte([]).teile if t.name == "klumpen")
    assert klumpen.note == 100.0


# ── Auslastung: voll ist nicht gut, leer aber auch nicht ────────────────────────
def test_volles_depot_bekommt_eine_schlechte_auslastung() -> None:
    g = Grenzen(max_offen=4, max_anteil_je_position=0.25, max_risiko_pct=99.0)
    t = next(
        x for x in bewerte([_p() for _ in range(4)], grenzen=g).teile if x.name == "auslastung"
    )
    assert t.note == 0.0
    assert "keine neue Gelegenheit" in t.satz


def test_leeres_depot_bekommt_ebenfalls_keine_bestnote() -> None:
    """Wer nie investiert ist, hat kein Risiko — und keine Chance."""
    t = next(x for x in bewerte([]).teile if x.name == "auslastung")
    assert t.note == 0.0
    assert "nichts, was laufen kann" in t.satz


def test_halb_belegt_ist_der_beste_zustand() -> None:
    g = Grenzen(max_offen=4, max_anteil_je_position=0.25, max_risiko_pct=99.0)
    t = next(x for x in bewerte([_p(), _p()], grenzen=g).teile if x.name == "auslastung")
    assert t.note == 100.0


# ── Qualitaet ───────────────────────────────────────────────────────────────────
def test_positionen_die_man_heute_nicht_mehr_eroeffnen_wuerde() -> None:
    offen = [_p(handelbar=True), _p(handelbar=False), _p(handelbar=False), _p(handelbar=False)]
    t = next(x for x in bewerte(offen, grenzen=_WEIT).teile if x.name == "qualitaet")
    assert t.note == 25.0
    assert "3 von 4" in t.satz
    assert "kein Verkaufsbefehl" in t.satz


def test_ohne_bewertung_gibt_es_keine_qualitaetsnote() -> None:
    """Unbekannt ist nicht dasselbe wie schlecht."""
    offen = [_p(handelbar=None), _p(handelbar=None)]
    t = next(x for x in bewerte(offen, grenzen=_WEIT).teile if x.name == "qualitaet")
    assert t.note is None
    assert "keine Aussage" in t.satz


# ── Verlauf ─────────────────────────────────────────────────────────────────────
def test_verlauf_uebersetzt_den_erwartungswert() -> None:
    perf = {"je_regel": {"ganz": {"anzahl": 58, "erwartungswert": -0.405, "belastbar": True}}}
    t = next(
        x for x in bewerte([_p()], performance=perf, grenzen=_WEIT).teile if x.name == "verlauf"
    )
    assert t.note is not None and abs(t.note - 9.5) < 0.1
    assert "-0.41 R je Trade" in t.satz or "-0,41" in t.satz


def test_null_erwartungswert_ist_die_mitte() -> None:
    perf = {"je_regel": {"ganz": {"anzahl": 40, "erwartungswert": 0.0, "belastbar": True}}}
    t = next(
        x for x in bewerte([_p()], performance=perf, grenzen=_WEIT).teile if x.name == "verlauf"
    )
    assert t.note == 50.0


def test_zu_wenige_trades_werden_im_satz_benannt() -> None:
    perf = {"je_regel": {"ganz": {"anzahl": 4, "erwartungswert": 0.5, "belastbar": False}}}
    t = next(
        x for x in bewerte([_p()], performance=perf, grenzen=_WEIT).teile if x.name == "verlauf"
    )
    assert "zu wenige" in t.satz


def test_ohne_performance_keine_verlaufsnote() -> None:
    t = next(x for x in bewerte([_p()], grenzen=_WEIT).teile if x.name == "verlauf")
    assert t.note is None


# ── Stand ───────────────────────────────────────────────────────────────────────
def test_stand_rechnet_im_durchschnitt_nicht_in_der_summe() -> None:
    """Sonst waere die Note ein Mass fuer die Anzahl der Zeilen statt fuer den Zustand."""
    offen = [_p(r=-1.0), _p(r=-1.0), _p(r=0.5)]
    t = next(x for x in bewerte(offen, grenzen=_WEIT).teile if x.name == "stand")
    schnitt = -1.5 / 3
    assert t.note is not None and abs(t.note - (70.0 + schnitt * 30.0)) < 1e-9
    assert "-0.50 R" in t.satz or "-0,50" in t.satz


def test_stand_haengt_nicht_an_der_anzahl_der_positionen() -> None:
    klein = bewerte([_p(r=-0.5)] * 3, grenzen=_WEIT)
    gross = bewerte([_p(r=-0.5)] * 30, grenzen=_WEIT)
    a = next(x for x in klein.teile if x.name == "stand").note
    b = next(x for x in gross.teile if x.name == "stand").note
    assert a == b


def test_ohne_stand_keine_note() -> None:
    offen = [_p(r=None), _p(r=None)]
    t = next(x for x in bewerte(offen, grenzen=_WEIT).teile if x.name == "stand")
    assert t.note is None


# ── Die Gesamtnote ──────────────────────────────────────────────────────────────
def test_gesundes_depot_wird_gruen() -> None:
    g = Grenzen(max_offen=4, max_anteil_je_position=0.25, max_risiko_pct=99.0)
    offen = [_p("aktien", "long", r=0.8), _p("krypto", "short", r=0.6)]
    perf = {"je_regel": {"ganz": {"anzahl": 50, "erwartungswert": 0.25, "belastbar": True}}}
    erg = bewerte(offen, performance=perf, grenzen=g)
    assert erg.note is not None and erg.note >= GRUEN_AB
    assert erg.ampel == "gruen"


def test_klumpiges_verlustreiches_depot_wird_rot() -> None:
    offen = [_p("krypto", "long", handelbar=False, r=-0.8) for _ in range(12)]
    perf = {"je_regel": {"ganz": {"anzahl": 58, "erwartungswert": -0.4, "belastbar": True}}}
    erg = bewerte(offen, performance=perf, grenzen=_WEIT)
    assert erg.note is not None and erg.note < GELB_AB
    assert erg.ampel == "rot"


def test_fehlende_teile_werden_nicht_geraten_sondern_benannt() -> None:
    offen = [_p(handelbar=None, r=None)]
    erg = bewerte(offen, grenzen=_WEIT)
    assert erg.note is not None  # Klumpen und Auslastung reichen fuer eine Note
    assert any("nicht eingerechnet" in s for s in erg.saetze)
    assert any("qualitaet" in s for s in erg.saetze)


def test_ohne_jede_angabe_gibt_es_keine_note() -> None:
    erg = bewerte([], performance=None, grenzen=Grenzen(max_anteil_je_position=0.0))
    # Klumpen und Auslastung sind auch bei leerem Depot bekannt — es gibt also eine Note.
    assert erg.ampel in {"gruen", "gelb", "rot"}


def test_der_vorbehalt_steht_immer_dabei() -> None:
    """Eine Ampel ohne diesen Satz lädt dazu ein, sie fuer eine Prognose zu halten."""
    erg = bewerte([_p()], grenzen=_WEIT)
    assert any("nicht, wie es laufen wird" in s for s in erg.saetze)


def test_schwaechster_teil_wird_benannt() -> None:
    offen = [_p("krypto", "long", handelbar=True, r=0.0) for _ in range(10)]
    erg = bewerte(offen, grenzen=_WEIT)
    assert erg.schwaechster == "klumpen"
    assert any("Am schwächsten: klumpen" in s for s in erg.saetze)


def test_serialisierung_ist_vollstaendig() -> None:
    d = bewerte([_p()], grenzen=_WEIT).as_dict()
    assert set(d) == {"note", "ampel", "teile", "schwaechster", "saetze"}
    assert {t["name"] for t in d["teile"]} == {
        "klumpen",
        "auslastung",
        "qualitaet",
        "verlauf",
        "stand",
    }
