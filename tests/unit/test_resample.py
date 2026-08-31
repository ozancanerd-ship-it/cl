"""Tests: OHLCV-Resampling – Aggregation, Vollständigkeit, Look-ahead-Schutz."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from trading_agent.core.enums import Timeframe
from trading_agent.core.models import OHLCV
from trading_agent.core.time import parse_timestamp
from trading_agent.data.resample import ResampleError, resample_ohlcv


class TestResample:
    def test_m5_to_h1_counts(self, make_series: Callable[..., list[OHLCV]]) -> None:
        bars = make_series(120)  # 120 M5 = 10 volle H1
        h1 = resample_ohlcv(bars, Timeframe.M5, Timeframe.H1)
        assert len(h1) == 10
        assert all(b.timeframe is Timeframe.H1 for b in h1)

    def test_ohlc_aggregation(self, make_bar: Callable[..., OHLCV]) -> None:
        b1 = make_bar(open_time="2024-06-01T00:00:00Z", open=100, high=105, low=99, close=101)
        b2 = make_bar(open_time="2024-06-01T00:05:00Z", open=101, high=110, low=100, close=108)
        b3 = make_bar(open_time="2024-06-01T00:10:00Z", open=108, high=109, low=95, close=97)
        m15 = resample_ohlcv([b1, b2, b3], Timeframe.M5, Timeframe.M15)
        assert len(m15) == 1
        bar = m15[0]
        assert bar.open == 100
        assert bar.close == 97
        assert bar.high == 110
        assert bar.low == 95
        assert bar.volume == b1.volume + b2.volume + b3.volume
        assert bar.open_time == parse_timestamp("2024-06-01T00:00:00Z")
        assert bar.close_time == parse_timestamp("2024-06-01T00:15:00Z")

    def test_incomplete_target_bar_dropped(self, make_series: Callable[..., list[OHLCV]]) -> None:
        bars = make_series(4)  # 4 M5 = 1 volle M15 (3 Bars) + 1 übrige (unvollständige zweite M15)
        m15 = resample_ohlcv(bars, Timeframe.M5, Timeframe.M15)
        assert len(m15) == 1  # nur die vollständige

    def test_incomplete_allowed_when_flag_set(
        self, make_series: Callable[..., list[OHLCV]]
    ) -> None:
        bars = make_series(4)
        m15 = resample_ohlcv(bars, Timeframe.M5, Timeframe.M15, require_complete=False)
        assert len(m15) == 2

    def test_missing_inner_bar_drops_group(self, make_series: Callable[..., list[OHLCV]]) -> None:
        bars = make_series(3)  # eine M15-Gruppe braucht 3 M5-Bars
        m15 = resample_ohlcv([bars[0], bars[2]], Timeframe.M5, Timeframe.M15)
        assert m15 == []  # Gruppe unvollständig -> nicht ausgegeben

    def test_horizon_drops_future_target_bars(
        self, make_series: Callable[..., list[OHLCV]]
    ) -> None:
        bars = make_series(60, start="2024-06-01T00:00:00Z")  # 12 H1... nein: 60 M5 = 5 H1
        horizon = parse_timestamp("2024-06-01T03:00:00Z")  # erst 3 H1 abgeschlossen
        h1 = resample_ohlcv(bars, Timeframe.M5, Timeframe.H1, horizon=horizon)
        assert len(h1) == 3
        assert all(b.close_time <= horizon for b in h1)

    def test_m5_to_d1(self, make_series: Callable[..., list[OHLCV]]) -> None:
        bars = make_series(288, start="2024-06-01T00:00:00Z")  # 288 M5 = 1 D1
        d1 = resample_ohlcv(bars, Timeframe.M5, Timeframe.D1)
        assert len(d1) == 1
        assert d1[0].open_time == parse_timestamp("2024-06-01T00:00:00Z")

    def test_target_smaller_rejected(self, make_series: Callable[..., list[OHLCV]]) -> None:
        bars = make_series(10, timeframe=Timeframe.H1)
        with pytest.raises(ResampleError):
            resample_ohlcv(bars, Timeframe.H1, Timeframe.M5)

    def test_wrong_source_timeframe_rejected(self, make_series: Callable[..., list[OHLCV]]) -> None:
        bars = make_series(4, timeframe=Timeframe.M15)
        with pytest.raises(ResampleError):
            resample_ohlcv(bars, Timeframe.M5, Timeframe.H1)
