"""Ausstiegs-Studie: der Plan laeuft nach dem Drehen richtig weiter (Ziele, Schutz-Stop, Luecken)."""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import ausstieg_studie as a

T0 = datetime(2025, 5, 1, tzinfo=UTC)
KOSTEN = 0.1  # 0,5 % von 100 bei 5 Punkten Risiko


def _k(rows: list[tuple[float, float, float, float]]) -> dict:
    return {
        "zeit": [T0 + timedelta(hours=i) for i in range(len(rows))],
        "o": [r[0] for r in rows],
        "h": [r[1] for r in rows],
        "l": [r[2] for r in rows],
        "c": [r[3] for r in rows],
    }


def _w(**kw) -> dict:
    w = {
        "richtung": "long",
        "einstiegskurs": 100.0,
        "einstieg": 100.0,
        "stop": 95.0,
        "tp1": 105.0,
        "tp2": 110.0,
        "tp3": 120.0,
        "erreicht": [],
        "zuletzt": T0.isoformat(),
    }
    w.update(kw)
    return w


def test_alle_ziele_in_dritteln() -> None:
    r = a.halten(_w(), _k([(100, 106, 99, 105), (105, 111, 104, 110), (110, 121, 109, 120)]))
    assert abs(r - ((1 + 2 + 4) / 3 - KOSTEN)) < 1e-9


def test_nach_ziel_1_rest_auf_einstand() -> None:
    r = a.halten(_w(), _k([(100, 106, 99, 105), (105, 105, 99, 99)]))
    assert abs(r - (1 / 3 - KOSTEN)) < 1e-9


def test_stop_und_ziel_in_einer_kerze_zaehlt_der_stop() -> None:
    r = a.halten(_w(), _k([(100, 106, 94, 100)]))
    assert abs(r - (-1 - KOSTEN)) < 1e-9


def test_luecke_unter_den_stop_zum_eroeffnungskurs() -> None:
    r = a.halten(_w(), _k([(90, 91, 89, 90)]))
    assert abs(r - (-2 - KOSTEN)) < 1e-9


def test_schon_erreichtes_ziel_bleibt_und_schutz_gilt() -> None:
    """TP1 vor dem Drehen erreicht: Stop steht auf Einstand, das erste Drittel ist sicher."""
    r = a.halten(_w(erreicht=["TP1"]), _k([(101, 102, 99.5, 100)]))
    assert abs(r - (1 / 3 - KOSTEN)) < 1e-9


def test_short_gespiegelt() -> None:
    w = _w(richtung="short", stop=105.0, tp1=95.0, tp2=90.0, tp3=80.0)
    r = a.halten(w, _k([(100, 101, 94, 95), (95, 96, 89, 90), (90, 106, 89, 100)]))
    assert abs(r - (1 / 3 + 2 / 3 + 1 / 3 - KOSTEN)) < 1e-9


def test_entscheidung_woertlich() -> None:
    def e(di, do, n, q05, q95):
        return {
            "IS": {"unterschied": di},
            "OOS": {"unterschied": do, "n": n},
            "oos_bootstrap_90": (q05, q95),
        }

    assert a.entscheide(e(0.1, 0.1, 30, 0.01, 0.3)).startswith("aendern")
    assert a.entscheide(e(-0.1, -0.1, 30, -0.3, -0.01)).startswith("bestaetigt")
    assert a.entscheide(e(0.04, -0.001, 76, -0.2, 0.2)).startswith("bleibt")
