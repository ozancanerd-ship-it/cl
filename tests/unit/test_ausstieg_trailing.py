"""Rechenweg der Ausstiegs-Studie 2 auf einem kuenstlichen Kursweg."""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

_P = Path(__file__).resolve().parents[2] / "scripts" / "ausstieg_trailing_studie.py"
_spec = importlib.util.spec_from_file_location("ats", _P)
assert _spec is not None and _spec.loader is not None
ats = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ats)

T0 = datetime(2026, 1, 5, tzinfo=UTC)
KOSTEN = 0.5 / 100 * 100 / 10  # 0,5 % vom Einsatz bei 10 Punkten Risiko


def _kerzen(wege: list[tuple[float, float, float, float]]) -> object:
    k = object.__new__(ats.Kerzen)
    k.t = np.array(
        [int((T0 + timedelta(minutes=15 * i)).timestamp() * 1000) for i in range(len(wege))]
    )
    k.o, k.h, k.l, k.c = (np.array([w[j] for w in wege], dtype=float) for j in range(4))
    return k


W = {
    "einstiegskurs": 100.0,
    "einstieg": 100.0,
    "stop": 90.0,
    "tp1": 110.0,
    "tp2": 120.0,
    "tp3": 135.0,
    "richtung": "long",
    "einstieg_um": T0.isoformat(),
}


def test_lauf_bis_130_dann_zurueck() -> None:
    k = _kerzen(
        [(100, 111, 99, 110), (110, 121, 109, 120), (120, 130, 119, 129), (129, 129, 95, 96)]
    )
    # Plan: Ziel 1 (+1 R) und Ziel 2 (+2 R) je ein Drittel, Rest am Stop auf Ziel 1 (+1 R).
    assert ats.simuliere(W, k, "X0") == pytest.approx((1 + 2 + 1) / 3 - KOSTEN)
    # Trail 1 R: Spitze 130 -> Stop 120 -> zwei Drittel bei +2 R.
    assert ats.simuliere(W, k, "X2") == pytest.approx(1 / 3 + (2 / 3) * 2 - KOSTEN)


def test_stop_zuerst() -> None:
    k = _kerzen([(100, 112, 89, 100)])
    for v in ats.VARIANTEN:
        assert ats.simuliere(W, k, v) == pytest.approx(-1 - KOSTEN)


def test_short_spiegelbildlich() -> None:
    w = {**W, "richtung": "short", "stop": 110.0, "tp1": 90.0, "tp2": 80.0, "tp3": 65.0}
    k = _kerzen([(100, 101, 89, 90), (90, 91, 79, 80), (80, 81, 70, 71), (71, 105, 70, 104)])
    assert ats.simuliere(w, k, "X2") == pytest.approx(1 / 3 + (2 / 3) * 2 - KOSTEN)
