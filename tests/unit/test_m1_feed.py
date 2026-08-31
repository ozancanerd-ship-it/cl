"""Phase 3 · Schritt 8 — native M1-Zufuhr für die Confirmation (``strategy.m1_feed``).

PIT-Filter (nur ``close_time <= as_of``) · Fenster ab §7-Strukturbruch minus Puffer ·
kein Fake bei fehlender Historie (``NullM1Source`` → leeres Fenster) · deterministisch.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import tests.unit.test_gates as gt
from trading_agent.core.enums import Timeframe
from trading_agent.core.models import OHLCV
from trading_agent.core.time import bar_close_time
from trading_agent.strategy.m1_feed import (
    InlineM1Source,
    M1FeedParams,
    NullM1Source,
    confirmation_window,
)

M1 = Timeframe.M1
START = datetime(2024, 6, 3, 4, 0, tzinfo=UTC)


def _m1(i: int, *, instrument: str = "BTCUSD") -> OHLCV:
    ot = START + timedelta(minutes=i)
    return OHLCV(
        instrument=instrument,
        timeframe=M1,
        open_time=ot,
        close_time=bar_close_time(ot, M1),
        open=100.0,
        high=100.5,
        low=99.5,
        close=100.0,
        volume=1.0,
        source="t",
    )


def _armed_candidate():
    mtf, cand = gt._long_setup()
    assert cand.is_armed and cand.structure_break is not None
    return mtf, cand


def test_null_source_returns_empty() -> None:
    _mtf, cand = _armed_candidate()
    out = confirmation_window(NullM1Source(), cand, information_cutoff=_mtf.information_cutoff)
    assert out == ()


def test_inline_source_pit_filtered() -> None:
    _mtf, cand = _armed_candidate()
    cutoff = _mtf.information_cutoff
    bars = [_m1(i) for i in range(0, 240)]  # 4 h M1 rund um den Bruch
    src = InlineM1Source(bars)
    out = confirmation_window(src, cand, information_cutoff=cutoff)
    assert out  # etwas im Fenster
    assert all(b.close_time <= cutoff for b in out)  # kein Look-ahead
    assert all(b.timeframe is M1 for b in out)
    # Fensterstart = Strukturbruch - Puffer
    p = M1FeedParams()
    lo = cand.structure_break.break_bar_timestamp - p.lookback_before_break
    assert all(b.open_time >= lo for b in out)


def test_wrong_instrument_ignored() -> None:
    _mtf, cand = _armed_candidate()
    bars = [_m1(i, instrument="ETHUSD") for i in range(0, 120)]
    out = confirmation_window(
        InlineM1Source(bars), cand, information_cutoff=_mtf.information_cutoff
    )
    assert out == ()


def test_max_bars_cap() -> None:
    _mtf, cand = _armed_candidate()
    bars = [_m1(i) for i in range(0, 600)]
    out = confirmation_window(
        InlineM1Source(bars),
        cand,
        information_cutoff=_mtf.information_cutoff,
        params=M1FeedParams(max_bars=10),
    )
    assert len(out) <= 10


def test_deterministic() -> None:
    _mtf, cand = _armed_candidate()
    src = InlineM1Source([_m1(i) for i in range(0, 240)])
    a = confirmation_window(src, cand, information_cutoff=_mtf.information_cutoff)
    b = confirmation_window(src, cand, information_cutoff=_mtf.information_cutoff)
    assert a == b
