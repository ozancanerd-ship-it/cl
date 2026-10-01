"""Aktien-Trades muessen ein Wochenende ueberleben.

Bis zum 26.09. schloss der Waechter jede offene Wache nach dreissig Stunden ohne Kurs
als „Karteileiche". Fuer Krypto ist das richtig — dort gibt es rund um die Uhr Kurse.
Fuer Aktien war es ein stiller Totalausfall: Freitag 20:00 UTC letzter Kurs, Sonntag
02:00 UTC dreissig Stunden spaeter, geschlossen. Am 20.09. traf das GILD, NEE, WMT, NOW
und GOOGL auf einen Schlag — laufende Trades im Plus, verbucht mit 0 R. Eine Aktie
konnte damit nie ueber ein Wochenende laufen, obwohl genau das ihre Haltedauer ist.
"""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from trading_agent.scanner.watchlist import Wache, Wachliste


def _modul() -> Any:
    pfad = Path(__file__).resolve().parents[2] / "scripts" / "watch_levels.py"
    spec = importlib.util.spec_from_file_location("watch_levels", pfad)
    assert spec and spec.loader
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


FREITAG_ABEND = datetime(2026, 9, 18, 20, 0, tzinfo=UTC)
SONNTAG_NACHT = datetime(2026, 9, 20, 2, 48, tzinfo=UTC)
MONTAG_MITTAG = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
DIENSTAG_ABEND = datetime(2026, 9, 22, 21, 0, tzinfo=UTC)


def _wache(instrument: str, klasse: str, zuletzt: datetime) -> Wache:
    return Wache(
        instrument=instrument,
        klasse=klasse,
        richtung="long",
        note="A",
        einstieg=100.0,
        einstieg_art="sofort",
        stop=95.0,
        tp1=105.0,
        tp2=None,
        tp3=None,
        score=70.0,
        rr=2.0,
        erwartet_pct=5.0,
        zustand="aktiv",
        aufgenommen=zuletzt.isoformat(),
        zuletzt=zuletzt.isoformat(),
    )


def test_wochenende_ist_keine_boersenzeit() -> None:
    m = _modul()
    assert m._boersenstunden(FREITAG_ABEND, SONNTAG_NACHT) == 0.0
    assert m._boersenstunden(FREITAG_ABEND, MONTAG_MITTAG) == 0.0
    # Montag und Dienstag voll gehandelt: zweimal 6,5 Stunden.
    assert m._boersenstunden(FREITAG_ABEND, DIENSTAG_ABEND) == 13.0


def test_aktie_ueberlebt_das_wochenende() -> None:
    m = _modul()
    liste = Wachliste({"GILD": _wache("GILD", "aktien", FREITAG_ABEND)})
    assert m._raeume_zombies(liste, {}, SONNTAG_NACHT) == 0
    assert m._raeume_zombies(liste, {}, MONTAG_MITTAG) == 0
    assert liste.wachen["GILD"].zustand == "aktiv"


def test_aktie_ohne_kurs_ueber_zwei_handelstage_wird_geschlossen() -> None:
    m = _modul()
    liste = Wachliste({"GILD": _wache("GILD", "aktien", FREITAG_ABEND)})
    assert m._raeume_zombies(liste, {}, DIENSTAG_ABEND) == 1
    assert liste.wachen["GILD"].zustand == "abgelaufen"


def test_krypto_bleibt_bei_dreissig_stunden() -> None:
    m = _modul()
    liste = Wachliste({"SOLUSD": _wache("SOLUSD", "krypto", FREITAG_ABEND)})
    assert m._raeume_zombies(liste, {}, SONNTAG_NACHT) == 1


def test_umstieg_uebernimmt_nur_gute_laufende_trades(monkeypatch) -> None:
    """Beim ersten Lauf mit dem Alarm-Tor tragen die alten Wachen kein ``gemeldet``.
    Laufende, die heute durchs Tor kaemen, klingeln weiter; der Rest bleibt in der App.

    Der Umstieg war am 26.09. — mit dem Tor von damals (ohne Erfolgsnachweis-Pflicht)."""
    from trading_agent.scanner import alarm_tor

    monkeypatch.setattr(alarm_tor, "NUR_BEWAEHRT", False)
    m = _modul()
    gut = _wache("AAVEUSD", "krypto", FREITAG_ABEND)
    gut.setup = "Ausbruch aus der Basis"
    gut.rr = 3.0
    schwach = _wache("MONUSD", "krypto", FREITAG_ABEND)
    schwach.note = "B"
    schwach.setup = "Ausbruch aus der Basis"
    liste = Wachliste({"AAVEUSD": gut, "MONUSD": schwach})
    roh = liste.as_dict()
    for v in roh["wachen"].values():
        v.pop("gemeldet")
    assert m._alte_trades_uebernehmen(liste, roh) == 1
    assert liste.wachen["AAVEUSD"].gemeldet == gut.aufgenommen
    assert liste.wachen["MONUSD"].gemeldet == ""
    # Im neuen Format passiert nichts mehr.
    assert m._alte_trades_uebernehmen(liste, liste.as_dict()) == 0
