"""Gemeinsame Test-Fixtures – deterministisch, ohne externe Abhängigkeiten."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from trading_agent.core.clock import FixedClock, SimClock
from trading_agent.core.enums import Timeframe
from trading_agent.core.models import OHLCV
from trading_agent.core.time import bar_close_time, parse_timestamp

REF_NOW = parse_timestamp("2024-06-10T00:00:00Z")


@pytest.fixture
def fixed_clock() -> FixedClock:
    return FixedClock(REF_NOW)


@pytest.fixture
def sim_clock() -> SimClock:
    return SimClock(parse_timestamp("2024-06-01T00:00:00Z"))


@pytest.fixture
def make_bar() -> Callable[..., OHLCV]:
    """Baut eine valide OHLCV-Bar; einzelne Felder überschreibbar."""

    def _make(
        open_time: str | datetime = "2024-06-01T00:00:00Z",
        timeframe: Timeframe = Timeframe.M5,
        *,
        instrument: str = "BTCUSDT",
        open: float = 100.0,
        high: float | None = None,
        low: float | None = None,
        close: float = 101.0,
        volume: float = 10.0,
        source: str = "test",
        ingested_at: str | datetime | None = None,
        quote_volume: float | None = None,
        trades: int | None = None,
    ) -> OHLCV:
        ot = parse_timestamp(open_time)
        hi = high if high is not None else max(open, close) + 1.0
        lo = low if low is not None else min(open, close) - 1.0
        return OHLCV(
            instrument=instrument,
            timeframe=timeframe,
            open_time=ot,
            close_time=bar_close_time(ot, timeframe),
            open=open,
            high=hi,
            low=lo,
            close=close,
            volume=volume,
            quote_volume=quote_volume,
            trades=trades,
            source=source,
            ingested_at=parse_timestamp(ingested_at) if ingested_at is not None else None,
        )

    return _make


@pytest.fixture
def make_series(make_bar: Callable[..., OHLCV]) -> Callable[..., list[OHLCV]]:
    """Baut eine lückenlose M5-Serie ab einem Startzeitpunkt."""

    def _make(
        n: int,
        start: str = "2024-06-01T00:00:00Z",
        timeframe: Timeframe = Timeframe.M5,
        *,
        instrument: str = "BTCUSDT",
        start_price: float = 100.0,
        step: float = 0.5,
    ) -> list[OHLCV]:
        t = parse_timestamp(start)
        bars: list[OHLCV] = []
        price = start_price
        for _ in range(n):
            bars.append(
                make_bar(
                    open_time=t,
                    timeframe=timeframe,
                    instrument=instrument,
                    open=price,
                    close=price + step,
                    high=price + step + 0.5,
                    low=price - 0.5,
                    volume=10.0,
                )
            )
            price += step
            t += timedelta(seconds=timeframe.seconds)
        return bars

    return _make


@pytest.fixture(scope="session")
def csv_data_dir() -> Path:
    return Path(__file__).parent / "data" / "csv"
