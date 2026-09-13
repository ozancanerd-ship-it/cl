"""Die Haltedauer wird gemessen, nicht geschaetzt.

WARUM ES DIESEN TEST GIBT

Ozan fragt seit Tagen dasselbe: „wie lange laeuft so ein Trade?" Beantwortbar war das
nicht, weil nirgends stand, wie lange ein Signal tatsaechlich offen war. Geschaetzt haette
man es koennen — Abstand zum Ziel geteilt durch eine Durchschnittsbewegung. Das waere eine
Zahl mit Nachkommastelle und ohne Deckung gewesen.

Gemessen wird jetzt von der Aufnahme auf die Wachliste bis zum Ausgang. Dieser Test haelt
drei Dinge fest, die dabei leicht kaputtgehen:

1. Der Median, nicht der Durchschnitt. Ein einziges Signal, das drei Wochen offen stand,
   verschiebt einen Durchschnitt so weit, dass er nichts Typisches mehr beschreibt.
2. Zu wenige Faelle -> gar keine Zahl. Ein Median aus drei Trades ist eine Anekdote.
3. Krumme oder fehlende Zeitstempel kippen die Auswertung nicht.
"""

from __future__ import annotations

from trading_agent.scanner.performance import (
    DAUER_MIN_FAELLE,
    KURZ_BIS_H,
    _stunden,
    bericht,
    median,
)


def _liste(stunden: list[float], zustand: str = "stop") -> dict[str, object]:
    from datetime import UTC, datetime, timedelta

    start = datetime(2026, 9, 1, tzinfo=UTC)
    wachen = {}
    for i, h in enumerate(stunden):
        a = start + timedelta(days=i)
        wachen[f"w{i}"] = {
            "instrument": f"X{i}USD",
            "klasse": "krypto",
            "note": "B",
            "zustand": zustand,
            "erreicht": [],
            "bestes_r": 0.0,
            "schlechtestes_r": -1.0,
            "aufgenommen": a.isoformat(),
            "zuletzt": (a + timedelta(hours=h)).isoformat(),
        }
    return {"wachen": wachen}


def test_stunden_rechnet_und_verweigert_unsinn() -> None:
    assert _stunden("2026-09-01T00:00:00+00:00", "2026-09-01T06:00:00+00:00") == 6.0
    assert _stunden("", "2026-09-01T06:00:00+00:00") is None
    assert _stunden("2026-09-01T00:00:00+00:00", "kein Datum") is None
    # Rueckwaerts laufende Zeit gibt es nicht — lieber keine Zahl als eine negative.
    assert _stunden("2026-09-02T00:00:00+00:00", "2026-09-01T00:00:00+00:00") is None


def test_median_statt_durchschnitt() -> None:
    werte = [10.0] * 9 + [10_000.0]
    assert median(werte) == 10.0
    assert sum(werte) / len(werte) > 1000.0  # Gegenprobe: der Durchschnitt waere unbrauchbar


def test_ein_ausreisser_verschiebt_die_haltedauer_nicht() -> None:
    normal = [12.0] * 11
    b = bericht(_liste([*normal, 3000.0]))
    assert b.dauer["median_h"] == 12.0, (
        "Ein einziges Signal, das monatelang offen stand, darf die typische Haltedauer "
        "nicht verschieben — sonst steht auf jeder Karte eine Zahl, die fuer keinen "
        "einzigen Trade gilt."
    )


def test_zu_wenige_faelle_bekommen_keine_zahl() -> None:
    b = bericht(_liste([12.0] * (DAUER_MIN_FAELLE - 1)))
    assert b.dauer.get("belastbar") is False
    assert "median_h" not in b.dauer, "Aus zu wenigen Faellen darf keine Haltedauer entstehen"


def test_kurz_anteil_zaehlt_an_der_richtigen_grenze() -> None:
    b = bericht(_liste([KURZ_BIS_H - 1] * 6 + [KURZ_BIS_H + 1] * 6))
    assert b.dauer["kurz_anteil"] == 0.5
    assert b.dauer["kurz_bis_h"] == KURZ_BIS_H


def test_kaputte_zeitstempel_kippen_die_auswertung_nicht() -> None:
    daten = _liste([12.0] * 10)
    daten["wachen"]["w0"]["aufgenommen"] = "irgendwann"  # type: ignore[index]
    daten["wachen"]["w1"]["zuletzt"] = ""  # type: ignore[index]
    b = bericht(daten)
    assert b.abgeschlossen == 10
    assert b.dauer["faelle"] == 8, "nur die messbaren Faelle zaehlen"


def test_dauer_landet_an_jedem_trade() -> None:
    b = bericht(_liste([6.0, 30.0, 54.0] * 4))
    werte = sorted({t["dauer_h"] for t in b.trades})
    assert werte == [6.0, 30.0, 54.0]
