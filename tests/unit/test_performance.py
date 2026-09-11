"""scanner/performance — die Nachrechnung der eigenen Signale.

Warum getestet: dieses Modul ist der einzige Ort, an dem das System sich selbst
widerspricht. Wenn es kaputt ist, merkt das niemand — ein falsch gerechneter
Erwartungswert sieht genauso aus wie ein richtiger, nur schöner.

Besonders geschützt wird deshalb die Richtung des Rundens: ``invalidiert`` zählt mit
0 R statt mit dem tatsächlichen (meist leicht negativen) Stand. Diese Großzügigkeit
geht zulasten der eigenen Bilanz, nicht zu ihren Gunsten — und genau so muss sie
bleiben.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from trading_agent.scanner.performance import (
    GENUG,
    IM_PLUS_AB,
    Ergebnis,
    aus_wachliste,
    bericht,
    rechne,
)


def _wache(**kw: Any) -> dict[str, Any]:
    basis = {
        "instrument": "TESTUSDT",
        "klasse": "krypto",
        "note": "A",
        "zustand": "stop",
        "erreicht": [],
        "bestes_r": 0.0,
        "schlechtestes_r": -1.0,
        "zuletzt": "2026-09-01T00:00:00+00:00",
    }
    basis.update(kw)
    return basis


def _liste(*wachen: dict[str, Any]) -> dict[str, Any]:
    return {"wachen": {f"W{i}": w for i, w in enumerate(wachen)}}


# ── Die beiden Ausstiegsregeln ──────────────────────────────────────────────────
def test_stop_ohne_teilgewinn_kostet_ein_r_in_beiden_regeln() -> None:
    e = aus_wachliste(_liste(_wache(zustand="stop")))[0]
    assert e.r_ganz == -1.0
    assert e.r_drittel == -1.0


def test_erstes_ziel_dann_stop_ist_der_entscheidende_unterschied() -> None:
    """Der Fall, der die Trefferquote macht: kurz im Plus, dann voll zurueckgelaufen."""
    e = aus_wachliste(_liste(_wache(zustand="stop", erreicht=["TP1"], bestes_r=1.2)))[0]
    assert e.r_ganz == -1.0, "alles oder nichts sieht hier nur den Stop"
    # Ein Drittel zu 1 R, der Rest am Einstand.
    assert abs(e.r_drittel - 1 / 3) < 1e-9


def test_zwei_ziele_erreicht() -> None:
    e = aus_wachliste(
        _liste(_wache(zustand="ziel_erreicht", erreicht=["TP1", "TP2"], bestes_r=2.4))
    )[0]
    assert e.r_ganz == 2.0
    assert abs(e.r_drittel - (1 / 3 * 1.0 + 1 / 3 * 2.0)) < 1e-9


def test_invalidiert_wird_zugunsten_des_systems_mit_null_bewertet() -> None:
    """Zulasten der eigenen Bilanz runden, nie zu ihren Gunsten."""
    e = aus_wachliste(_liste(_wache(zustand="invalidiert", schlechtestes_r=-0.7)))[0]
    assert e.r_ganz == 0.0
    assert e.r_drittel == 0.0


def test_offene_wachen_zaehlen_nicht_mit() -> None:
    daten = _liste(
        _wache(zustand="aktiv"),
        _wache(zustand="wartet_auf_einstieg"),
        _wache(zustand="stop"),
    )
    ergebnisse = aus_wachliste(daten)
    assert len(ergebnisse) == 1
    b = bericht(daten)
    assert (b.abgeschlossen, b.offen) == (1, 2)


# ── Kennzahlen ──────────────────────────────────────────────────────────────────
def test_kennzahlen_ueber_eine_bekannte_folge() -> None:
    k = rechne([1.0, -1.0, 2.0, -1.0, -1.0])
    assert k.anzahl == 5
    assert k.treffer == 2
    assert abs(k.summe_r - 0.0) < 1e-9
    assert abs(k.erwartungswert - 0.0) < 1e-9
    assert k.profitfaktor == 1.0
    assert k.groesster_gewinn == 2.0
    assert k.groesster_verlust == -1.0
    assert k.verlustserie == 2


def test_rueckgang_misst_ab_dem_bisherigen_hoch() -> None:
    """Nicht ab null: was zaehlt, ist der Weg vom Gipfel ins Tal."""
    k = rechne([3.0, -1.0, -1.0, -1.0])
    assert k.summe_r == 0.0
    assert k.max_rueckgang_r == -3.0


def test_ohne_verlust_kein_profitfaktor() -> None:
    """Durch null zu teilen waere „unendlich" — das ist keine Kennzahl, sondern eine Ausrede."""
    k = rechne([1.0, 2.0])
    assert k.profitfaktor is None


def test_leere_folge_kippt_nicht() -> None:
    k = rechne([])
    assert k.anzahl == 0
    assert k.trefferquote is None
    assert k.erwartungswert is None


def test_belastbarkeit_haengt_an_der_anzahl() -> None:
    assert rechne([1.0] * (GENUG - 1)).belastbar is False
    assert rechne([1.0] * GENUG).belastbar is True


# ── Der Klartext ────────────────────────────────────────────────────────────────
def test_verlust_wird_beim_namen_genannt() -> None:
    daten = _liste(*[_wache(zustand="stop") for _ in range(5)])
    b = bericht(daten)
    text = " ".join(b.saetze)
    assert "verloren" in text
    assert "-1.00 R je Trade" in text or "-1,00" in text


def test_zu_wenige_trades_werden_als_solche_benannt() -> None:
    daten = _liste(*[_wache(zustand="stop") for _ in range(5)])
    b = bericht(daten)
    assert any("weniger als" in s for s in b.saetze)


def test_einstiegsproblem_wird_ausgesprochen() -> None:
    """Wenn die Haelfte der Einstiege nie funktioniert, muss das dastehen."""
    daten = _liste(*[_wache(zustand="stop", bestes_r=0.0) for _ in range(10)])
    b = bericht(daten)
    assert b.nie_im_plus == 1.0
    assert any("beim Einstieg selbst" in s for s in b.saetze)


def test_gute_zahlen_bekommen_trotzdem_den_vorbehalt() -> None:
    """Der Satz zur Reichweite steht immer da — gerade wenn es gut aussieht."""
    daten = _liste(
        *[_wache(zustand="ziel_erreicht", erreicht=["TP1", "TP2"], bestes_r=2.5) for _ in range(40)]
    )
    b = bericht(daten)
    assert b.je_regel["ganz"].summe_r > 0
    assert any("belegt keinen Edge" in s for s in b.saetze)


def test_ohne_trades_gibt_es_nichts_zu_sagen() -> None:
    b = bericht({"wachen": {}})
    assert b.abgeschlossen == 0
    assert b.saetze == ("Noch kein abgeschlossener Trade — es gibt nichts auszuwerten.",)


def test_kaputte_eingabe_wirft_nicht() -> None:
    assert aus_wachliste(None) == []
    assert aus_wachliste({}) == []
    assert aus_wachliste({"wachen": ["kein dict"]}) == []
    assert bericht(None).abgeschlossen == 0


# ── Aufschluesselung ────────────────────────────────────────────────────────────
def test_gruppierung_nach_note_und_klasse() -> None:
    daten = _liste(
        _wache(note="A", klasse="krypto", zustand="stop"),
        _wache(note="A", klasse="krypto", zustand="ziel_erreicht", erreicht=["TP1"]),
        _wache(note="B", klasse="aktien", zustand="stop"),
    )
    b = bericht(daten)
    assert b.je_note["A"].anzahl == 2
    assert b.je_note["B"].anzahl == 1
    assert b.je_klasse["aktien"].summe_r == -1.0
    assert b.je_klasse["krypto"].summe_r == 0.0


def test_setup_gruppe_bleibt_leer_solange_keins_gespeichert_ist() -> None:
    """Alte Wachen kennen noch keinen Setup-Namen — dann steht dort nichts, statt „?"."""
    b = bericht(_liste(_wache(zustand="stop")))
    assert b.je_setup == {}


def test_serialisierung_ist_vollstaendig() -> None:
    d = bericht(_liste(_wache(zustand="stop"))).as_dict()
    for k in ("erzeugt", "abgeschlossen", "offen", "je_regel", "je_note", "saetze", "nie_im_plus"):
        assert k in d, k
    assert set(d["je_regel"]) == {"ganz", "drittel"}


def test_ergebnis_traegt_den_zeitpunkt_fuer_die_reihenfolge() -> None:
    """Der Rueckgang haengt an der Reihenfolge — ohne Sortierung ist er beliebig."""
    daten = _liste(
        _wache(zustand="stop", zuletzt="2026-09-05T00:00:00+00:00"),
        _wache(zustand="ziel_erreicht", erreicht=["TP3"], zuletzt="2026-09-01T00:00:00+00:00"),
    )
    ergebnisse = aus_wachliste(daten)
    assert [e.beendet[:10] for e in ergebnisse] == ["2026-09-01", "2026-09-05"]
    # Gewinn zuerst, dann Verlust: der Rueckgang ist genau der Verlust.
    assert (
        bericht(daten, jetzt=datetime(2026, 9, 10, tzinfo=UTC)).je_regel["ganz"].max_rueckgang_r
        == -1.0
    )


def test_im_plus_schwelle_ist_bewusst_niedrig() -> None:
    """Es geht nicht um Gewinn, sondern darum, ob der Einstieg je funktioniert hat."""
    assert 0.0 < IM_PLUS_AB <= 0.5
    e = Ergebnis(
        instrument="X",
        klasse="krypto",
        note="A",
        setup="",
        zustand="stop",
        erreicht=(),
        mfe=0.29,
        mae=-1.0,
        beendet="",
        r_ganz=-1.0,
        r_drittel=-1.0,
    )
    assert e.mfe < IM_PLUS_AB
