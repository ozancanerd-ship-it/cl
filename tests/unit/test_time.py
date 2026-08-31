"""Tests: Timestamp-Normalisierung, Timeframe-Alignment, DST-Auflösung."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from datetime import time as dtime

import pytest

from trading_agent.core.enums import Timeframe
from trading_agent.core.time import (
    TimeError,
    align_down,
    align_up,
    bar_close_time,
    bar_open_time,
    ensure_utc,
    from_epoch_ms,
    is_aligned,
    iter_bar_opens,
    parse_timestamp,
    resolve_local_time,
    to_epoch_ms,
)


class TestParseTimestamp:
    def test_iso_with_z(self) -> None:
        assert parse_timestamp("2024-06-01T12:00:00Z") == datetime(2024, 6, 1, 12, tzinfo=UTC)

    def test_iso_with_offset_converts_to_utc(self) -> None:
        assert parse_timestamp("2024-06-01T14:00:00+02:00") == datetime(2024, 6, 1, 12, tzinfo=UTC)

    def test_date_only_is_midnight_utc(self) -> None:
        assert parse_timestamp("2024-06-01") == datetime(2024, 6, 1, tzinfo=UTC)

    def test_epoch_seconds(self) -> None:
        assert parse_timestamp(1_717_243_200) == datetime(2024, 6, 1, 12, tzinfo=UTC)

    def test_epoch_millis_autodetect(self) -> None:
        assert parse_timestamp(1_717_243_200_000) == datetime(2024, 6, 1, 12, tzinfo=UTC)

    def test_aware_datetime_passthrough(self) -> None:
        dt = datetime(2024, 6, 1, 12, tzinfo=UTC)
        assert parse_timestamp(dt) == dt

    def test_naive_datetime_rejected(self) -> None:
        with pytest.raises(TimeError):
            parse_timestamp(datetime(2024, 6, 1, 12))

    def test_naive_iso_rejected(self) -> None:
        with pytest.raises(TimeError):
            parse_timestamp("2024-06-01T12:00:00")

    def test_bool_rejected(self) -> None:
        with pytest.raises(TimeError):
            parse_timestamp(True)

    def test_garbage_rejected(self) -> None:
        with pytest.raises(TimeError):
            parse_timestamp("not-a-time")

    def test_epoch_roundtrip(self) -> None:
        dt = datetime(2024, 6, 1, 12, 30, 15, tzinfo=UTC)
        assert from_epoch_ms(to_epoch_ms(dt)) == dt


class TestEnsureUtc:
    def test_converts(self) -> None:
        from zoneinfo import ZoneInfo

        dt = datetime(2024, 6, 1, 14, tzinfo=ZoneInfo("Europe/Berlin"))
        assert ensure_utc(dt) == datetime(2024, 6, 1, 12, tzinfo=UTC)

    def test_naive_raises(self) -> None:
        with pytest.raises(TimeError):
            ensure_utc(datetime(2024, 6, 1))


class TestAlignment:
    @pytest.mark.parametrize(
        ("ts", "tf", "expected"),
        [
            ("2024-06-01T00:07:00Z", Timeframe.M5, "2024-06-01T00:05:00Z"),
            ("2024-06-01T00:07:00Z", Timeframe.M15, "2024-06-01T00:00:00Z"),
            ("2024-06-01T13:20:00Z", Timeframe.H4, "2024-06-01T12:00:00Z"),
            ("2024-06-01T13:20:00Z", Timeframe.D1, "2024-06-01T00:00:00Z"),
        ],
    )
    def test_align_down(self, ts: str, tf: Timeframe, expected: str) -> None:
        assert align_down(parse_timestamp(ts), tf) == parse_timestamp(expected)

    def test_align_up_on_boundary_is_noop(self) -> None:
        b = parse_timestamp("2024-06-01T00:05:00Z")
        assert align_up(b, Timeframe.M5) == b

    def test_align_up_advances(self) -> None:
        assert align_up(parse_timestamp("2024-06-01T00:06:00Z"), Timeframe.M5) == parse_timestamp(
            "2024-06-01T00:10:00Z"
        )

    def test_w1_aligns_to_monday(self) -> None:
        # 2024-06-03 ist ein Montag
        assert is_aligned(parse_timestamp("2024-06-03T00:00:00Z"), Timeframe.W1)
        assert not is_aligned(parse_timestamp("2024-06-04T00:00:00Z"), Timeframe.W1)
        assert align_down(parse_timestamp("2024-06-06T12:00:00Z"), Timeframe.W1) == parse_timestamp(
            "2024-06-03T00:00:00Z"
        )

    def test_misaligned_not_aligned(self) -> None:
        assert not is_aligned(parse_timestamp("2024-06-01T00:07:00Z"), Timeframe.M5)

    def test_subsecond_not_aligned(self) -> None:
        assert not is_aligned(datetime(2024, 6, 1, 0, 5, 0, 500_000, tzinfo=UTC), Timeframe.M5)


class TestBarTimes:
    def test_close_and_open(self) -> None:
        o = parse_timestamp("2024-06-01T00:00:00Z")
        c = bar_close_time(o, Timeframe.M5)
        assert c == parse_timestamp("2024-06-01T00:05:00Z")
        assert bar_open_time(c, Timeframe.M5) == o

    def test_iter_bar_opens(self) -> None:
        opens = iter_bar_opens(
            parse_timestamp("2024-06-01T00:00:00Z"),
            parse_timestamp("2024-06-01T00:20:00Z"),
            Timeframe.M5,
        )
        assert len(opens) == 4
        assert opens[0] == parse_timestamp("2024-06-01T00:00:00Z")
        assert opens[-1] == parse_timestamp("2024-06-01T00:15:00Z")


class TestDst:
    def test_uk_spring_gap_is_nonexistent(self) -> None:
        with pytest.raises(TimeError, match="nicht existierende"):
            resolve_local_time(date(2024, 3, 31), dtime(1, 30), "Europe/London")

    def test_uk_fall_back_is_ambiguous(self) -> None:
        with pytest.raises(TimeError, match="mehrdeutige"):
            resolve_local_time(date(2024, 10, 27), dtime(1, 30), "Europe/London")

    def test_us_spring_gap_is_nonexistent(self) -> None:
        with pytest.raises(TimeError, match="nicht existierende"):
            resolve_local_time(date(2024, 3, 10), dtime(2, 30), "America/New_York")

    def test_us_fall_back_is_ambiguous(self) -> None:
        with pytest.raises(TimeError, match="mehrdeutige"):
            resolve_local_time(date(2024, 11, 3), dtime(1, 30), "America/New_York")

    def test_normal_summer_time(self) -> None:
        # 09:30 ET im Sommer = 13:30 UTC (EDT, -04:00)
        assert resolve_local_time(date(2024, 6, 3), dtime(9, 30), "America/New_York") == datetime(
            2024, 6, 3, 13, 30, tzinfo=UTC
        )

    def test_normal_winter_time(self) -> None:
        # 09:30 ET im Winter = 14:30 UTC (EST, -05:00)
        assert resolve_local_time(date(2024, 1, 15), dtime(9, 30), "America/New_York") == datetime(
            2024, 1, 15, 14, 30, tzinfo=UTC
        )

    def test_dst_shift_between_summer_and_winter(self) -> None:
        summer = resolve_local_time(date(2024, 7, 1), dtime(8, 0), "Europe/London")
        winter = resolve_local_time(date(2024, 1, 1), dtime(8, 0), "Europe/London")
        assert summer.hour == 7  # BST
        assert winter.hour == 8  # GMT
        assert winter - summer != timedelta(0)

    def test_unknown_zone_raises(self) -> None:
        with pytest.raises(TimeError):
            resolve_local_time(date(2024, 6, 1), dtime(9, 0), "Mars/Olympus")
