"""setup_research — Forschungs-Werkbank für Setup-Typen. Sichert die Look-ahead-Freiheit ab.

Das Skript liegt unter ``scripts/`` — hier via importlib geladen. Getestet wird der Kern:
der Trade-Simulator darf nur ab ``sig.at_index + 1`` lesen, Exits nur vorwärts.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timedelta
from pathlib import Path

from trading_agent.core.enums import Timeframe
from trading_agent.core.models import OHLCV
from trading_agent.core.time import bar_close_time

_SPEC = importlib.util.spec_from_file_location(
    "setup_research", Path(__file__).resolve().parents[2] / "scripts" / "setup_research.py"
)
assert _SPEC and _SPEC.loader
sr = importlib.util.module_from_spec(_SPEC)
sys.modules["setup_research"] = sr  # dataclass(slots=True) braucht das Modul in sys.modules
_SPEC.loader.exec_module(sr)


def _bars(
    prices: list[tuple[float, float, float, float]], start: str = "2026-01-01T00:00:00Z"
) -> list[OHLCV]:
    t = datetime.fromisoformat(start.replace("Z", "+00:00"))
    out: list[OHLCV] = []
    for o, h, low, c in prices:
        out.append(
            OHLCV(
                instrument="TEST",
                timeframe=Timeframe.H4,
                open_time=t,
                close_time=bar_close_time(t, Timeframe.H4),
                open=o,
                high=h,
                low=low,
                close=c,
                volume=1.0,
                source="test",
            )
        )
        t += timedelta(hours=4)
    return out


def test_simulate_enters_next_bar_open_no_lookahead() -> None:
    # Signal an Index 2. Bar 3 = Entry-Bar. Bar 4 trifft TP. Bar 2 low ist irrelevant.
    bars = _bars(
        [
            (100, 101, 99, 100),
            (100, 101, 99, 100),
            (100, 101, 90, 100),  # at_index=2: tiefes Low — darf NICHT als Entry/Exit zählen
            (100, 100, 100, 100),  # entry bar, open=100
            (100, 115, 100, 110),  # trifft TP (long, RR2 → 100 + 2*1 = ... siehe stop)
        ]
    )
    sig = sr.Signal(at_index=2, direction=1, stop=99.0, reason="x")
    tr = sr._simulate(bars, sig, rr=2.0, cost_r=0.0, symbol="TEST", setup="S")
    assert tr is not None
    assert tr.entry_price == 100.0  # bar[3].open
    assert tr.entry_ts == bars[3].open_time
    assert tr.exit_reason == "target"
    assert tr.realized_r == 2.0


def test_simulate_stop_before_target_worst_case() -> None:
    bars = _bars(
        [
            (100, 101, 99, 100),
            (100, 101, 99, 100),
            (100, 101, 99, 100),
            (100, 100, 100, 100),  # entry
            (100, 110, 97, 100),  # Bar trifft SL (98) UND TP (102) → worst case = SL
        ]
    )
    sig = sr.Signal(at_index=2, direction=1, stop=98.0, reason="x")
    tr = sr._simulate(bars, sig, rr=2.0, cost_r=0.0, symbol="TEST", setup="S")
    assert tr is not None and tr.exit_reason == "stop" and tr.realized_r == -1.0


def test_ctx_swings_are_point_in_time() -> None:
    # aufsteigende Serie mit einem Zacken → mindestens ein bestätigter Swing;
    # swings_at(i) darf nie einen Swing zeigen, der erst nach bar i bestätigt wird.
    prices = []
    p = 100.0
    for k in range(80):
        p += 1.0 if (k // 5) % 2 == 0 else -0.6
        prices.append((p, p + 0.5, p - 0.5, p))
    h4 = _bars(prices)
    d1 = h4[::6]  # grobe D1-Näherung reicht für den PIT-Check
    ctx = sr.build_ctx("TEST", h4, list(d1))
    for i in range(30, len(h4)):
        for s in ctx.swings_at(i):
            assert s.confirmed_at <= h4[i].close_time
        for b in ctx.breaks_at(i):
            assert b.break_bar_timestamp <= h4[i].close_time


def test_detectors_only_fire_with_valid_geometry() -> None:
    prices = [
        (100 + i * 0.1, 100 + i * 0.1 + 0.5, 100 + i * 0.1 - 0.5, 100 + i * 0.1) for i in range(120)
    ]
    h4 = _bars(prices)
    d1 = list(h4[::6])
    ctx = sr.build_ctx("TEST", h4, d1)
    for det in sr.DETECTORS.values():
        for i in range(30, len(h4) - 1):
            sig = det(ctx, i)
            if sig is not None:
                assert sig.at_index == i
                assert sig.direction in (-1, 1)
                assert sig.stop > 0
