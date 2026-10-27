from datetime import UTC, datetime

from trading_agent.scanner import alarm_tor as at
from trading_agent.scanner.watchlist import Wache, Wachliste

JETZT = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)


def _w(aufg):
    return Wache(instrument="XUSDT", klasse="krypto", richtung="long", note="B", einstieg=100.0,
                 einstieg_art="sofort", stop=95.0, tp1=105.0, tp2=None, tp3=None, score=60, rr=2,
                 erwartet_pct=5, zustand="aktiv", aufgenommen=aufg.isoformat(), zuletzt=aufg.isoformat())


def test_neue_wache_wird_nicht_von_kursen_vor_aufnahme_gestoppt():
    liste = Wachliste()
    liste.wachen["XUSDT"] = _w(JETZT)
    ev = liste.pruefen({"XUSDT": {"hoch": 101, "tief": 90, "letzter": 101}}, jetzt=JETZT)
    assert liste.wachen["XUSDT"].zustand == "aktiv"
    assert not [e for e in ev if e.art == "STOP"]


def test_aeltere_wache_wird_weiter_gestoppt():
    liste = Wachliste()
    liste.wachen["XUSDT"] = _w(datetime(2026, 10, 27, 10, 0, tzinfo=UTC))
    liste.pruefen({"XUSDT": {"hoch": 101, "tief": 90, "letzter": 101}}, jetzt=JETZT)
    assert liste.wachen["XUSDT"].zustand == "stop"


def test_gate_zaehlt_sofortstop_nicht():
    d = {"instrument": "X", "klasse": "krypto", "richtung": "long", "setup": "S", "zustand": "stop",
         "einstiegskurs": 100.0, "aufgenommen": "t", "zuletzt": "t", "bestes_r": 0.0}
    assert "klasse_richtung:krypto|long" not in at.bilanz([d])
    d2 = {**d, "zuletzt": "t2"}
    assert at.bilanz([d2])["klasse_richtung:krypto|long"].anzahl == 1
