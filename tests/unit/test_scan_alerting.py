"""Der Wachposten ueber dem Gesamtmarkt-Scan meldet die Aenderung, nicht den Zustand."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from trading_agent.ops.notify import Severity
from trading_agent.scanner.alerting import (
    AlertBruecke,
    ScanWaechter,
    als_text,
)

T0 = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)


def _chance(
    instrument: str,
    *,
    score: float,
    urteil: str,
    richtung: str = "long",
    klasse: str = "aktien",
) -> dict[str, Any]:
    return {
        "instrument": instrument,
        "richtung": richtung,
        "score": score,
        "urteil": urteil,
        "kurs": 100.0,
        "ziel": 110.0,
        "tp2": 115.0,
        "tp3": 120.0,
        "invalidierung": 95.0,
        "rr": 3.0,
        "bewegung_pct": 10.0,
        "headline": f"{instrument} laeuft",
        "_klasse": klasse,
    }


def _doc(chancen: list[dict[str, Any]], *, fehler: dict[str, str] | None = None) -> dict[str, Any]:
    klassen: dict[str, list[dict[str, Any]]] = {}
    for c in chancen:
        klassen.setdefault(str(c.get("_klasse", "aktien")), []).append(c)
    return {
        "erzeugt": T0.isoformat(),
        "fehler": fehler or {},
        "anzahl": {k: len(v) for k, v in klassen.items()},
        "klassen": klassen,
        "gesamt": sorted(chancen, key=lambda c: -float(c["score"])),
    }


def test_erster_lauf_meldet_nur_die_handelbaren_setups() -> None:
    doc = _doc(
        [
            _chance("MSFT", score=80.8, urteil="A_PLUS"),
            _chance("AMD", score=55.0, urteil="WATCH"),
            _chance("NEE", score=12.0, urteil="NO_TRADE"),
        ]
    )
    alarme, stand = ScanWaechter().pruefen(doc, None, jetzt=T0)

    arten = {(a.art, a.instrument) for a in alarme}
    assert ("NEUES_SETUP", "MSFT") in arten
    # WATCH und NO_TRADE sind kein Alarm — genau das ist die Spam-Bremse.
    assert not any(a.instrument in {"AMD", "NEE"} for a in alarme)
    assert stand["rang1"] == "MSFT"


def test_gleicher_zustand_zweimal_meldet_nichts() -> None:
    doc = _doc([_chance("MSFT", score=80.8, urteil="A_PLUS")])
    w = ScanWaechter()
    _, stand = w.pruefen(doc, None, jetzt=T0)
    alarme, _ = w.pruefen(doc, stand, jetzt=T0 + timedelta(hours=1))
    assert alarme == []


def test_a_plus_setup_ist_kritisch_und_traegt_die_zahlen() -> None:
    doc = _doc([_chance("MSFT", score=80.8, urteil="A_PLUS")])
    alarme, _ = ScanWaechter().pruefen(doc, None, jetzt=T0)
    a = next(a for a in alarme if a.art == "NEUES_SETUP")
    assert a.severity is Severity.CRITICAL
    for stueck in ("110", "115", "120", "95", "1:3.00"):
        assert stueck in a.text


def test_weggebrochenes_setup_wird_gemeldet() -> None:
    w = ScanWaechter()
    _, stand = w.pruefen(_doc([_chance("MSFT", score=80.0, urteil="A_PLUS")]), None, jetzt=T0)
    alarme, _ = w.pruefen(
        _doc([_chance("MSFT", score=30.0, urteil="NO_TRADE")]),
        stand,
        jetzt=T0 + timedelta(hours=6),
    )
    a = next(a for a in alarme if a.art == "ENTFALLEN")
    assert a.instrument == "MSFT"
    assert a.severity is Severity.WARNING


def test_stumme_anlageklasse_erzeugt_keinen_wegbruch() -> None:
    """Der wichtigste Test: eine ausgefallene Datenquelle ist kein verschwundenes Setup."""
    w = ScanWaechter()
    _, stand = w.pruefen(
        _doc([_chance("BTCUSDT", score=75.0, urteil="A_PLUS", klasse="krypto")]),
        None,
        jetzt=T0,
    )
    leer = _doc([], fehler={"krypto": "HTTPError: 451"})
    leer["anzahl"] = {"krypto": 0}
    alarme, neuer = w.pruefen(leer, stand, jetzt=T0 + timedelta(hours=6))

    assert alarme == []
    # Der alte Stand wird weitergetragen, damit der naechste gute Lauf nicht neu meldet.
    assert neuer["instrumente"]["BTCUSDT"]["urteil"] == "A_PLUS"
    assert neuer["stumme_klassen"] == ["krypto"]


def test_richtungswechsel_ist_ein_neues_setup() -> None:
    w = ScanWaechter()
    _, stand = w.pruefen(
        _doc([_chance("MSFT", score=75.0, urteil="A", richtung="long")]), None, jetzt=T0
    )
    alarme, _ = w.pruefen(
        _doc([_chance("MSFT", score=75.0, urteil="A", richtung="short")]),
        stand,
        jetzt=T0 + timedelta(hours=6),
    )
    assert [a.art for a in alarme] == ["NEUES_SETUP"]


def test_schub_meldet_ein_setup_im_entstehen() -> None:
    w = ScanWaechter()
    _, stand = w.pruefen(_doc([_chance("AMD", score=30.0, urteil="NO_TRADE")]), None, jetzt=T0)
    alarme, _ = w.pruefen(
        _doc([_chance("AMD", score=58.0, urteil="WATCH")]), stand, jetzt=T0 + timedelta(hours=3)
    )
    a = next(a for a in alarme if a.art == "SCHUB")
    # INFO — landet im Log, nicht automatisch aufs Telefon.
    assert a.severity is Severity.INFO


def test_neue_nummer_eins_nur_wenn_sie_etwas_taugt() -> None:
    w = ScanWaechter()
    _, stand = w.pruefen(
        _doc(
            [
                _chance("MSFT", score=80.0, urteil="A_PLUS"),
                _chance("NVDA", score=75.0, urteil="A_PLUS"),
            ]
        ),
        None,
        jetzt=T0,
    )

    # Nummer 1 wechselt, aber die neue Spitze ist schwach -> keine Meldung.
    schwach = _doc([_chance("AMD", score=45.0, urteil="WATCH")])
    alarme, _ = w.pruefen(schwach, stand, jetzt=T0 + timedelta(hours=6))
    assert not any(a.art == "RANG1" for a in alarme)

    # NVDA zieht an MSFT vorbei. Beide waren schon A_PLUS, es gibt also kein neues
    # Setup zu melden — nur den Rangwechsel.
    ueberholt = _doc(
        [
            _chance("NVDA", score=88.0, urteil="A_PLUS"),
            _chance("MSFT", score=72.0, urteil="A_PLUS"),
        ]
    )
    alarme2, _ = w.pruefen(ueberholt, stand, jetzt=T0 + timedelta(hours=12))
    assert [(a.art, a.instrument) for a in alarme2] == [("RANG1", "NVDA")]


def test_abkuehlung_verhindert_die_wiederholung() -> None:
    w = ScanWaechter(abkuehlung=timedelta(hours=12))
    doc = _doc([_chance("MSFT", score=80.0, urteil="A_PLUS")])
    leer = _doc([_chance("MSFT", score=20.0, urteil="NO_TRADE")])

    alarme, stand = w.pruefen(doc, None, jetzt=T0)
    assert alarme
    # runter, wieder rauf, innerhalb der Abkuehlzeit -> kein zweiter identischer Alarm
    _, stand = w.pruefen(leer, stand, jetzt=T0 + timedelta(hours=1))
    alarme3, stand = w.pruefen(doc, stand, jetzt=T0 + timedelta(hours=2))
    assert not any(a.dedup_key.startswith("setup:MSFT:A_PLUS") for a in alarme3)

    # Nach der Abkuehlzeit ist dieselbe Meldung wieder erlaubt. (Ein unveraenderter
    # Zustand meldet nie erneut — es braucht einen echten Wechsel.)
    _, stand = w.pruefen(leer, stand, jetzt=T0 + timedelta(hours=13))
    alarme4, _ = w.pruefen(doc, stand, jetzt=T0 + timedelta(hours=14))
    assert any(a.dedup_key.startswith("setup:MSFT:A_PLUS") for a in alarme4)


def test_rangmeldung_wiederholt_kein_frisches_setup() -> None:
    """MSFT wird A+ und ist damit Nummer 1 — das ist eine Nachricht, nicht zwei."""
    doc = _doc([_chance("MSFT", score=88.0, urteil="A_PLUS")])
    alarme, _ = ScanWaechter().pruefen(doc, None, jetzt=T0)
    assert [a.art for a in alarme] == ["NEUES_SETUP"]


def test_als_text_ohne_alarme() -> None:
    assert als_text([]) == "keine Aenderung"


# ------------------------------------------------------------------ AlertBruecke


class _Sammler:
    """Minimaler Notifier-Ersatz."""

    def __init__(self) -> None:
        self.notes: list[Any] = []
        self.active_sinks = ["test"]
        self.deduped = 0
        self.rate_limited = 0

    def notify(self, note: Any) -> bool:
        self.notes.append(note)
        return True


class _Bus:
    def __init__(self) -> None:
        self.handler: Any = None

    def subscribe(self, _typ: Any, handler: Any) -> None:
        self.handler = handler


@pytest.mark.asyncio
async def test_bruecke_schickt_kritische_alerts_und_schluckt_rauschen() -> None:
    from trading_agent.runtime.events import AlertRaised

    sammler = _Sammler()
    bus = _Bus()
    bruecke = AlertBruecke(sammler, min_severity=Severity.WARNING)  # type: ignore[arg-type]
    bruecke.attach(bus)

    await bus.handler(
        AlertRaised(ts=T0, instrument="BTCUSDT", alert_type="buy", message="Entry erreicht")
    )
    await bus.handler(
        AlertRaised(ts=T0, instrument="BTCUSDT", alert_type="signal_weakened", message="etwas")
    )

    assert bruecke.gesehen == 2
    assert bruecke.geschickt == 1
    assert sammler.notes[0].severity is Severity.CRITICAL
    assert "BTCUSDT" in sammler.notes[0].title
