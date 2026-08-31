"""Tests: Data Quality Engine – Gaps, Duplikate, Reihenfolge, stale data, Zukunft, DST."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta

from trading_agent.core.enums import DataQualityCode, DataQualitySeverity, Timeframe
from trading_agent.core.models import OHLCV
from trading_agent.core.time import parse_timestamp
from trading_agent.data.quality import (
    QualityPolicy,
    check_ohlcv_series,
    check_session_resolution,
    deduplicate_ohlcv,
    sort_ohlcv,
)
from trading_agent.refdata.seed import seed_calendars, seed_sessions

NOW = parse_timestamp("2024-06-10T00:00:00Z")
CRYPTO_CAL = seed_calendars()["crypto_24_7"]


def _codes(status) -> set[DataQualityCode]:
    return {i.code for i in status.issues}


class TestCleanSeries:
    def test_clean_series_has_no_issues(self, make_series: Callable[..., list[OHLCV]]) -> None:
        bars = make_series(30)
        st = check_ohlcv_series(
            bars,
            instrument="BTCUSDT",
            timeframe=Timeframe.M5,
            now=bars[-1].close_time + timedelta(minutes=1),
            calendar=CRYPTO_CAL,
        )
        assert st.is_ok
        assert not st.blocks_trading
        assert st.bars_checked == 30


class TestEmpty:
    def test_empty_is_critical(self) -> None:
        st = check_ohlcv_series([], instrument="BTCUSDT", timeframe=Timeframe.M5, now=NOW)
        assert DataQualityCode.EMPTY_SERIES in _codes(st)
        assert st.blocks_trading


class TestGaps:
    def test_missing_bars_flagged(self, make_series: Callable[..., list[OHLCV]]) -> None:
        bars = make_series(10)
        gapped = bars[:3] + bars[6:]  # 3 fehlende M5-Bars
        st = check_ohlcv_series(
            gapped, instrument="BTCUSDT", timeframe=Timeframe.M5, now=NOW, calendar=CRYPTO_CAL
        )
        gap_issues = [i for i in st.issues if i.code is DataQualityCode.GAP]
        assert gap_issues and gap_issues[0].context["missing_bars"] == 3
        assert gap_issues[0].severity is DataQualitySeverity.WARNING

    def test_large_gap_is_critical(self, make_series: Callable[..., list[OHLCV]]) -> None:
        bars = make_series(40)
        gapped = bars[:5] + bars[25:]  # 20 fehlende Bars
        st = check_ohlcv_series(
            gapped,
            instrument="BTCUSDT",
            timeframe=Timeframe.M5,
            now=NOW,
            calendar=CRYPTO_CAL,
            policy=QualityPolicy(gap_critical_bars=12),
        )
        assert any(
            i.code is DataQualityCode.GAP and i.severity is DataQualitySeverity.CRITICAL
            for i in st.issues
        )
        assert st.blocks_trading

    def test_gap_during_market_close_is_not_flagged(self, make_bar: Callable[..., OHLCV]) -> None:
        # US-Equity: Freitag 15:55 -> Montag 09:35 ist KEINE Lücke (Markt zu).
        us = seed_calendars()["us_equity"]
        fri = make_bar(
            open_time="2024-06-07T19:55:00Z",  # 15:55 ET Freitag
            instrument="AAPL",
            timeframe=Timeframe.M5,
        )
        mon = make_bar(
            open_time="2024-06-10T13:30:00Z",  # 09:30 ET Montag (erster Handels-Slot)
            instrument="AAPL",
            timeframe=Timeframe.M5,
        )
        st = check_ohlcv_series(
            [fri, mon],
            instrument="AAPL",
            timeframe=Timeframe.M5,
            now=parse_timestamp("2024-06-10T13:36:00Z"),
            calendar=us,
        )
        assert DataQualityCode.GAP not in _codes(st)


class TestDuplicatesAndOrder:
    def test_identical_duplicate_is_warning(self, make_series: Callable[..., list[OHLCV]]) -> None:
        bars = make_series(5)
        st = check_ohlcv_series(
            [*bars, bars[2]], instrument="BTCUSDT", timeframe=Timeframe.M5, now=NOW
        )
        dups = [i for i in st.issues if i.code is DataQualityCode.DUPLICATE_BAR]
        assert dups and dups[0].severity is DataQualitySeverity.WARNING

    def test_conflicting_duplicate_is_critical(
        self, make_series: Callable[..., list[OHLCV]], make_bar: Callable[..., OHLCV]
    ) -> None:
        bars = make_series(5)
        conflict = make_bar(open_time=bars[2].open_time, close=999.0, high=1000.0, low=1.0)
        st = check_ohlcv_series(
            [*bars, conflict], instrument="BTCUSDT", timeframe=Timeframe.M5, now=NOW
        )
        assert any(
            i.code is DataQualityCode.DUPLICATE_BAR and i.severity is DataQualitySeverity.CRITICAL
            for i in st.issues
        )
        assert st.blocks_trading

    def test_out_of_order_flagged(self, make_series: Callable[..., list[OHLCV]]) -> None:
        bars = make_series(5)
        shuffled = [bars[0], bars[2], bars[1], bars[3], bars[4]]
        st = check_ohlcv_series(shuffled, instrument="BTCUSDT", timeframe=Timeframe.M5, now=NOW)
        assert DataQualityCode.OUT_OF_ORDER in _codes(st)
        assert st.blocks_trading


class TestStale:
    def test_stale_when_market_open_is_critical(
        self, make_series: Callable[..., list[OHLCV]]
    ) -> None:
        bars = make_series(10, start="2024-06-01T00:00:00Z")
        st = check_ohlcv_series(
            bars,
            instrument="BTCUSDT",
            timeframe=Timeframe.M5,
            now=parse_timestamp("2024-06-01T02:00:00Z"),  # weit nach der letzten Bar
            calendar=CRYPTO_CAL,
        )
        stale = [i for i in st.issues if i.code is DataQualityCode.STALE_DATA]
        assert stale and stale[0].severity is DataQualitySeverity.CRITICAL
        assert st.blocks_trading

    def test_fresh_series_not_stale(self, make_series: Callable[..., list[OHLCV]]) -> None:
        bars = make_series(10, start="2024-06-01T00:00:00Z")
        last_close = bars[-1].close_time
        st = check_ohlcv_series(
            bars,
            instrument="BTCUSDT",
            timeframe=Timeframe.M5,
            now=last_close + timedelta(minutes=1),
            calendar=CRYPTO_CAL,
        )
        assert DataQualityCode.STALE_DATA not in _codes(st)


class TestPointInTimeInSeries:
    def test_bar_closing_after_as_of_is_future(
        self, make_series: Callable[..., list[OHLCV]]
    ) -> None:
        bars = make_series(20, start="2024-06-01T00:00:00Z")
        as_of = bars[10].close_time
        st = check_ohlcv_series(
            bars,
            instrument="BTCUSDT",
            timeframe=Timeframe.M5,
            now=parse_timestamp("2024-06-10T00:00:00Z"),
            as_of=as_of,
        )
        assert DataQualityCode.TIMESTAMP_IN_FUTURE in _codes(st)
        assert st.blocks_trading

    def test_series_within_as_of_is_clean(self, make_series: Callable[..., list[OHLCV]]) -> None:
        bars = make_series(20, start="2024-06-01T00:00:00Z")
        st = check_ohlcv_series(
            bars,
            instrument="BTCUSDT",
            timeframe=Timeframe.M5,
            now=parse_timestamp("2024-06-10T00:00:00Z"),
            as_of=bars[-1].close_time,
            calendar=CRYPTO_CAL,
        )
        assert DataQualityCode.TIMESTAMP_IN_FUTURE not in _codes(st)


class TestMismatch:
    def test_symbol_mismatch(self, make_series: Callable[..., list[OHLCV]]) -> None:
        bars = make_series(3, instrument="ETHUSDT")
        st = check_ohlcv_series(bars, instrument="BTCUSDT", timeframe=Timeframe.M5, now=NOW)
        assert DataQualityCode.SYMBOL_MISMATCH in _codes(st)
        assert st.blocks_trading

    def test_timeframe_mismatch(self, make_series: Callable[..., list[OHLCV]]) -> None:
        bars = make_series(3, timeframe=Timeframe.M15)
        st = check_ohlcv_series(bars, instrument="BTCUSDT", timeframe=Timeframe.M5, now=NOW)
        assert DataQualityCode.TIMEFRAME_MISMATCH in _codes(st)


class TestHelpers:
    def test_sort_and_dedup(self, make_series: Callable[..., list[OHLCV]]) -> None:
        bars = make_series(5)
        shuffled = [bars[3], bars[0], bars[3], bars[2], bars[1], bars[4]]
        cleaned, conflicts = deduplicate_ohlcv(shuffled)
        assert len(cleaned) == 5
        assert not conflicts
        assert [b.open_time for b in cleaned] == [b.open_time for b in sort_ohlcv(bars)]

    def test_dedup_reports_conflicts(
        self, make_series: Callable[..., list[OHLCV]], make_bar: Callable[..., OHLCV]
    ) -> None:
        bars = make_series(3)
        conflict = make_bar(open_time=bars[1].open_time, close=500.0, high=600.0, low=1.0)
        _cleaned, conflicts = deduplicate_ohlcv([*bars, conflict])
        assert len(conflicts) == 1


class TestSessionResolutionCheck:
    def test_dst_gap_flagged(self) -> None:
        from datetime import time as dtime

        from trading_agent.core.enums import SessionName
        from trading_agent.refdata.models import SessionSpec

        spec = SessionSpec(
            name=SessionName.LONDON, tz="Europe/London", start=dtime(1, 30), end=dtime(9, 0)
        )
        issues = check_session_resolution([spec], date(2024, 3, 31), now=NOW)
        assert issues and issues[0].code is DataQualityCode.DST_AMBIGUOUS

    def test_normal_day_clean(self) -> None:
        issues = check_session_resolution(seed_sessions(), date(2024, 6, 3), now=NOW)
        assert not issues
