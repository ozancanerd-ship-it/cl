"""Tests: injizierbare Zeitquellen."""

from __future__ import annotations

from datetime import timedelta

import pytest

from trading_agent.core.clock import Clock, FixedClock, SimClock, SystemClock
from trading_agent.core.time import parse_timestamp


def test_systemclock_is_utc_and_advances() -> None:
    c = SystemClock()
    a = c.now()
    b = c.now()
    assert a.tzinfo is not None
    assert b >= a
    assert isinstance(c, Clock)


def test_simclock_controlled() -> None:
    start = parse_timestamp("2024-06-01T00:00:00Z")
    c = SimClock(start)
    assert c.now() == start
    c.advance(timedelta(minutes=5))
    assert c.now() == parse_timestamp("2024-06-01T00:05:00Z")
    c.set(parse_timestamp("2024-06-01T01:00:00Z"))
    assert c.now() == parse_timestamp("2024-06-01T01:00:00Z")


def test_simclock_no_backwards() -> None:
    c = SimClock(parse_timestamp("2024-06-01T01:00:00Z"))
    with pytest.raises(ValueError):
        c.set(parse_timestamp("2024-06-01T00:00:00Z"))
    with pytest.raises(ValueError):
        c.advance(timedelta(seconds=-1))


def test_fixedclock_constant() -> None:
    m = parse_timestamp("2024-06-01T00:00:00Z")
    c = FixedClock(m)
    assert c.now() == m == c.now()


def test_simclock_rejects_naive_start() -> None:
    from datetime import datetime

    with pytest.raises(Exception):
        SimClock(datetime(2024, 6, 1))
