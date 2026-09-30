"""Trend-Swing: Studie und Vorwaertslauf rechnen mit derselben Regel — und ohne Pfadfehler."""

from __future__ import annotations

import random
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "scripts"))

import trend_swing_forward as fw  # noqa: E402
import trend_swing_studie as ts  # noqa: E402


def _reihe(n: int, seed: int = 3, start: datetime | None = None) -> dict:
    """Zufallspfad mit Trendphasen — genug, dass Einstiege, Stops und Zeit-Ausstiege vorkommen."""
    rnd = random.Random(seed)
    start = start or datetime(2026, 1, 1, tzinfo=UTC)
    c, o, h, lo = [], [], [], []
    kurs = 100.0
    drift = 0.004
    for i in range(n):
        if i % 60 == 0:
            drift = rnd.choice((0.006, -0.004, 0.0))
        auf = kurs
        kurs = max(1.0, kurs * (1 + drift + rnd.gauss(0, 0.025)))
        o.append(auf)
        c.append(kurs)
        h.append(max(auf, kurs) * (1 + abs(rnd.gauss(0, 0.01))))
        lo.append(min(auf, kurs) * (1 - abs(rnd.gauss(0, 0.01))))
    return {
        "t": [0] * n,
        "o": o,
        "h": h,
        "l": lo,
        "c": c,
        "schluss": [start + timedelta(days=i + 1) for i in range(n)],
    }


def _kuerzen(d: dict, n: int) -> dict:
    return {k: v[:n] for k, v in d.items()}


def test_regel_erzeugt_stop_und_zeit_ausstiege() -> None:
    d = _reihe(600)
    trades = ts.simuliere(d, "X", {})
    gruende = {t.grund for t in trades}
    assert "stop" in gruende
    # Jeder Verlust am Stop ohne Kurs-Luecke ist genau -1 R minus Kosten.
    for t in trades:
        assert t.tage <= ts.MAX_TAGE
        if t.grund == "stop" and t.ausstieg == t.stop0:
            kosten = (ts.KOSTEN_PCT / 100) * t.einstieg / t.risiko
            assert abs(t.r - (-1.0 - kosten)) < 1e-9


def test_stop_sinkt_nie() -> None:
    d = _reihe(600, seed=11)
    ind = ts.indikatoren(d)
    pos = None
    letzter_stop = None
    for i in range(ts.ERSTE_KERZE, len(d["c"])):
        vorher = pos
        pos, _fertig = ts.kerze(d, ind, i, pos, "X")
        if pos is not None and vorher is pos and letzter_stop is not None:
            assert pos.stop >= letzter_stop
        letzter_stop = pos.stop if pos is not None else None


def test_vorwaerts_in_stuecken_gleich_wie_am_stueck(monkeypatch) -> None:
    """Der Kern: taeglich ein Stueck verarbeiten ergibt dasselbe wie alles auf einmal."""
    d = _reihe(500, seed=5)
    monkeypatch.setattr(fw, "START", d["schluss"][150])
    jetzt = datetime(2027, 1, 1, tzinfo=UTC)

    am_stueck = fw.fortschreiben({}, {"X": d}, jetzt)

    journal: dict = {}
    for n in range(200, 501, 7):
        journal = fw.fortschreiben(journal, {"X": _kuerzen(d, n)}, jetzt)
    journal = fw.fortschreiben(journal, {"X": d}, jetzt)

    def kern(j: dict) -> list:
        return [(t["einstieg_t"], t["grund"], round(t["r"], 9)) for t in j["trades"]]

    assert kern(journal) == kern(am_stueck)
    assert journal["positionen"] == am_stueck["positionen"]
    assert am_stueck["abgeschlossen"] > 0


def test_vorwaerts_zaehlt_nichts_vor_dem_start(monkeypatch) -> None:
    d = _reihe(400, seed=9)
    monkeypatch.setattr(fw, "START", d["schluss"][300])
    j = fw.fortschreiben({}, {"X": d}, datetime(2027, 1, 1, tzinfo=UTC))
    for t in j["trades"]:
        assert datetime.fromisoformat(t["einstieg_t"]) >= d["schluss"][300]


def test_fehlender_coin_behaelt_seine_position(monkeypatch) -> None:
    d = _reihe(400, seed=5)
    monkeypatch.setattr(fw, "START", d["schluss"][100])
    jetzt = datetime(2027, 1, 1, tzinfo=UTC)
    j = fw.fortschreiben({}, {"X": d, "Y": d}, jetzt)
    j2 = fw.fortschreiben(j, {"X": d}, jetzt)  # Y liefert heute nichts
    assert j2["positionen"]["Y"] == j["positionen"]["Y"]
    assert j2["zuletzt"]["Y"] == j["zuletzt"]["Y"]


def test_entscheidungsregel_woertlich() -> None:
    def erg(vp: float, is_: float, oos: float, n: int, unten: float) -> dict:
        return {
            "haupt": {
                "VP": {"erwartung_r": vp},
                "IS": {"erwartung_r": is_},
                "OOS": {"erwartung_r": oos, "n": n},
            },
            "oos_bootstrap_90": (unten, 1.0),
        }

    assert ts.entscheide(erg(0.1, 0.1, 0.1, 30, 0.01))[0].startswith("A")
    assert ts.entscheide(erg(0.1, 0.1, 0.1, 30, -0.2))[0].startswith("B")
    assert ts.entscheide(erg(0.1, -0.1, 0.1, 30, 0.01))[0].startswith("C")
    assert ts.entscheide(erg(0.1, 0.1, 0.1, 29, 0.01))[0].startswith("C")
