"""Tests: Kern-Datenmodelle inkl. OHLCV-Validierung und Point-in-Time-Marker."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from trading_agent.core.enums import (
    DataKind,
    DataQualityCode,
    DataQualitySeverity,
    NewsImpact,
    SessionName,
    Side,
    Timeframe,
)
from trading_agent.core.models import (
    OHLCV,
    DataQualityIssue,
    DataQualityStatus,
    Funding,
    MacroEvent,
    NewsEvent,
    OpenInterest,
    OrderbookSnapshot,
    Quote,
    SessionWindow,
    Trade,
)
from trading_agent.core.time import parse_timestamp


def _ohlcv(**kw: object) -> OHLCV:
    base = dict(
        instrument="BTCUSDT",
        timeframe=Timeframe.M5,
        open_time="2024-06-01T00:00:00Z",
        close_time="2024-06-01T00:05:00Z",
        open=100.0,
        high=110.0,
        low=95.0,
        close=105.0,
        volume=1.0,
    )
    base.update(kw)
    return OHLCV(**base)  # type: ignore[arg-type]


class TestOHLCV:
    def test_valid(self) -> None:
        bar = _ohlcv()
        assert bar.available_time == parse_timestamp("2024-06-01T00:05:00Z")
        assert bar.range == 15.0
        assert bar.is_bullish is True
        assert bar.schema_version >= 1

    def test_frozen(self) -> None:
        bar = _ohlcv()
        with pytest.raises(ValidationError):
            bar.close = 999.0  # type: ignore[misc]

    def test_extra_field_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            _ohlcv(bogus=1)

    def test_high_below_low_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _ohlcv(high=90.0, low=95.0)

    def test_high_below_body_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _ohlcv(open=100.0, close=105.0, high=104.0, low=95.0)

    def test_low_above_body_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _ohlcv(open=100.0, close=105.0, high=110.0, low=101.0)

    def test_negative_volume_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _ohlcv(volume=-1.0)

    def test_nan_price_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _ohlcv(close=float("nan"))

    def test_misaligned_open_time_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _ohlcv(open_time="2024-06-01T00:02:00Z", close_time="2024-06-01T00:07:00Z")

    def test_wrong_close_time_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _ohlcv(close_time="2024-06-01T00:10:00Z")

    def test_naive_time_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _ohlcv(open_time=datetime(2024, 6, 1))


class TestQuote:
    def test_valid(self) -> None:
        q = Quote(instrument="BTCUSDT", ts="2024-06-01T00:00:00Z", bid=100.0, ask=100.5)
        assert q.mid == 100.25
        assert q.spread == 0.5
        assert q.available_time == parse_timestamp("2024-06-01T00:00:00Z")

    def test_crossed_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Quote(instrument="X", ts="2024-06-01T00:00:00Z", bid=101.0, ask=100.0)


class TestTrade:
    def test_valid(self) -> None:
        t = Trade(
            instrument="BTCUSDT", ts="2024-06-01T00:00:00Z", price=100.0, size=0.5, side=Side.BUY
        )
        assert t.notional == 50.0


class TestOrderbook:
    def test_valid(self) -> None:
        ob = OrderbookSnapshot(
            instrument="BTCUSDT",
            ts="2024-06-01T00:00:00Z",
            bids=[(99.0, 1.0), (98.0, 2.0)],
            asks=[(101.0, 1.0), (102.0, 3.0)],
        )
        assert ob.best_bid == 99.0
        assert ob.best_ask == 101.0

    def test_unsorted_bids_rejected(self) -> None:
        with pytest.raises(ValidationError):
            OrderbookSnapshot(
                instrument="X",
                ts="2024-06-01T00:00:00Z",
                bids=[(98.0, 1.0), (99.0, 1.0)],
                asks=[(101.0, 1.0)],
            )

    def test_crossed_book_rejected(self) -> None:
        with pytest.raises(ValidationError):
            OrderbookSnapshot(
                instrument="X",
                ts="2024-06-01T00:00:00Z",
                bids=[(101.0, 1.0)],
                asks=[(100.0, 1.0)],
            )


class TestFundingOI:
    def test_funding(self) -> None:
        f = Funding(instrument="BTCUSDT", ts="2024-06-01T00:00:00Z", rate=-0.0001)
        assert f.available_time == parse_timestamp("2024-06-01T00:00:00Z")

    def test_open_interest(self) -> None:
        oi = OpenInterest(instrument="BTCUSDT", ts="2024-06-01T00:00:00Z", oi=1234.5)
        assert oi.oi == 1234.5


class TestNewsMacroPointInTime:
    def test_news_available_time_is_field(self) -> None:
        ev = NewsEvent(
            event_id="e1",
            event_type="CPI",
            impact=NewsImpact.HIGH,
            scheduled_time="2024-06-12T12:30:00Z",
            available_time="2024-06-12T12:30:00Z",
            actual=3.2,
        )
        assert ev.available_time == parse_timestamp("2024-06-12T12:30:00Z")

    def test_news_actual_before_schedule_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Future-Information-Leak"):
            NewsEvent(
                event_id="e1",
                event_type="CPI",
                impact=NewsImpact.HIGH,
                scheduled_time="2024-06-12T12:30:00Z",
                available_time="2024-06-10T00:00:00Z",
                actual=3.2,
            )

    def test_news_announcement_without_actual_may_precede(self) -> None:
        ev = NewsEvent(
            event_id="unlock",
            event_type="TOKEN_UNLOCK",
            impact=NewsImpact.MEDIUM,
            scheduled_time="2024-06-15T00:00:00Z",
            available_time="2024-05-01T00:00:00Z",
        )
        assert ev.actual is None

    def test_macro_value_before_reference_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MacroEvent(
                series_id="US_CPI_YOY",
                reference_period="2024-05-01T00:00:00Z",
                value=3.2,
                available_time="2024-04-01T00:00:00Z",
            )

    def test_macro_revision(self) -> None:
        ev = MacroEvent(
            series_id="US_CPI_YOY",
            reference_period="2024-05-01T00:00:00Z",
            value=3.2,
            available_time="2024-07-11T12:30:00Z",
            revision=1,
        )
        assert ev.revision == 1


class TestSessionWindow:
    def test_valid_and_contains(self) -> None:
        w = SessionWindow(
            name=SessionName.LONDON,
            start="2024-06-03T07:00:00Z",
            end="2024-06-03T15:30:00Z",
        )
        assert w.contains(parse_timestamp("2024-06-03T10:00:00Z"))
        assert not w.contains(parse_timestamp("2024-06-03T15:30:00Z"))  # Ende exklusiv

    def test_end_before_start_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SessionWindow(
                name=SessionName.LONDON,
                start="2024-06-03T15:00:00Z",
                end="2024-06-03T07:00:00Z",
            )


class TestDataQualityStatus:
    def test_ok_status(self) -> None:
        st = DataQualityStatus(
            instrument="BTCUSDT",
            kind=DataKind.OHLCV,
            timeframe=Timeframe.M5,
            checked_at="2024-06-10T00:00:00Z",
        )
        assert st.is_ok
        assert not st.blocks_trading
        assert st.worst_severity is None

    def test_critical_blocks_trading(self) -> None:
        st = DataQualityStatus(
            instrument="BTCUSDT",
            kind=DataKind.OHLCV,
            timeframe=Timeframe.M5,
            checked_at="2024-06-10T00:00:00Z",
            issues=[
                DataQualityIssue(
                    code=DataQualityCode.STALE_DATA,
                    severity=DataQualitySeverity.CRITICAL,
                    message="stale",
                ),
                DataQualityIssue(
                    code=DataQualityCode.GAP,
                    severity=DataQualitySeverity.WARNING,
                    message="gap",
                ),
            ],
        )
        assert st.blocks_trading
        assert st.worst_severity is DataQualitySeverity.CRITICAL
        assert len(st.by_code(DataQualityCode.STALE_DATA)) == 1


def test_all_records_have_available_time() -> None:
    """Jeder Record-Typ muss einen Point-in-Time-Marker liefern."""
    samples = [
        _ohlcv(),
        Quote(instrument="X", ts="2024-06-01T00:00:00Z", bid=1.0, ask=1.1),
        Trade(instrument="X", ts="2024-06-01T00:00:00Z", price=1.0, size=1.0),
        Funding(instrument="X", ts="2024-06-01T00:00:00Z", rate=0.0),
        OpenInterest(instrument="X", ts="2024-06-01T00:00:00Z", oi=1.0),
    ]
    for s in samples:
        assert isinstance(s.available_time, datetime)
        assert s.available_time.tzinfo == UTC
