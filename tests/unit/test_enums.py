"""Tests: Enumerationen."""

from __future__ import annotations

from trading_agent.core.enums import (
    DataQualitySeverity,
    ProviderHealth,
    Timeframe,
    TradingPriority,
)


def test_timeframe_seconds() -> None:
    assert Timeframe.M1.seconds == 60
    assert Timeframe.H4.seconds == 14400
    assert Timeframe.D1.seconds == 86400
    assert Timeframe.W1.seconds == 604800


def test_timeframe_ordered() -> None:
    ordered = Timeframe.ordered()
    assert ordered[0] is Timeframe.M1
    assert ordered[-1] is Timeframe.W1
    assert [t.seconds for t in ordered] == sorted(t.seconds for t in ordered)


def test_timeframe_is_intraday() -> None:
    assert Timeframe.M15.is_intraday
    assert not Timeframe.D1.is_intraday


def test_str_enum_value_roundtrip() -> None:
    assert Timeframe("M5") is Timeframe.M5
    assert str(Timeframe.M5) == "M5"


def test_severity_rank_monotonic() -> None:
    assert (
        DataQualitySeverity.INFO.rank
        < DataQualitySeverity.WARNING.rank
        < DataQualitySeverity.CRITICAL.rank
    )


def test_provider_health_rank() -> None:
    assert (
        ProviderHealth.HEALTHY.rank < ProviderHealth.DEGRADED.rank < ProviderHealth.UNAVAILABLE.rank
    )


def test_trading_priority_values() -> None:
    assert {p.value for p in TradingPriority} == {"tier_1", "tier_2", "tier_3"}
