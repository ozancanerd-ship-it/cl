"""Tests: ``analysis.news`` — PIT-Filter, asset-spezifische Relevanz, Blackout, risk_off, kein Fake."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from trading_agent.analysis.news import (
    NewsWindowParams,
    assess_news,
    build_news_context,
    no_feed_context,
)
from trading_agent.core.enums import AssetClass, NewsImpact
from trading_agent.core.models import NewsEvent

T0 = datetime(2025, 3, 12, 12, 30, tzinfo=UTC)  # geplanter CPI-Termin


def _ev(
    eid: str,
    etype: str,
    impact: NewsImpact,
    scheduled: datetime,
    available: datetime,
    *,
    actual: float | None = None,
    forecast: float | None = None,
    symbols: list[str] | None = None,
) -> NewsEvent:
    return NewsEvent(
        event_id=eid,
        event_type=etype,
        impact=impact,
        scheduled_time=scheduled,
        available_time=available,
        actual=actual,
        forecast=forecast,
        affected_symbols=symbols or [],
    )


def test_pit_filter_hides_future_available_events() -> None:
    planned = _ev("cpi-mar", "US_CPI", NewsImpact.HIGH, T0, T0 - timedelta(days=20))
    # Revision mit actual, erst nach dem Termin bekannt
    released = _ev(
        "cpi-mar",
        "US_CPI",
        NewsImpact.HIGH,
        T0,
        T0 + timedelta(minutes=1),
        actual=3.1,
        forecast=3.0,
    )
    cutoff = T0 - timedelta(hours=2)  # vor der Veröffentlichung
    a = assess_news([planned, released], cutoff=cutoff, asset_class=AssetClass.CRYPTO)
    assert len(a.relevant) == 1
    assert a.relevant[0].actual is None  # nur die geplante Revision ist sichtbar
    assert a.context.feed_as_of == cutoff


def test_asset_specific_relevance() -> None:
    ecb = _ev("ecb-1", "ECB_RATE", NewsImpact.HIGH, T0, T0 - timedelta(days=10))
    fomc = _ev("fomc-1", "FOMC_RATE", NewsImpact.HIGH, T0, T0 - timedelta(days=10))
    cutoff = T0 - timedelta(days=1)
    crypto = assess_news([ecb, fomc], cutoff=cutoff, asset_class=AssetClass.CRYPTO)
    fx = assess_news([ecb, fomc], cutoff=cutoff, asset_class=AssetClass.FOREX)
    assert {e.event_type for e in crypto.relevant} == {"FOMC_RATE"}  # Krypto: kein EZB
    assert {e.event_type for e in fx.relevant} == {"ECB_RATE", "FOMC_RATE"}  # FX: beide


def test_instrument_specific_event_is_relevant_even_if_type_not_in_table() -> None:
    unlock = _ev(
        "unlock-1",
        "TOKEN_UNLOCK",
        NewsImpact.MEDIUM,
        T0,
        T0 - timedelta(days=3),
        symbols=["SOL"],
    )
    a = assess_news(
        [unlock], cutoff=T0 - timedelta(days=1), asset_class=AssetClass.CRYPTO, instrument="SOLUSDT"
    )
    assert len(a.relevant) == 1
    b = assess_news(
        [unlock], cutoff=T0 - timedelta(days=1), asset_class=AssetClass.CRYPTO, instrument="BTCUSDT"
    )
    assert len(b.relevant) == 0


def test_blackout_window_sets_blocking_event_id() -> None:
    cpi = _ev("cpi-mar", "US_CPI", NewsImpact.HIGH, T0, T0 - timedelta(days=20))
    p = NewsWindowParams(pre_high_blackout_min=30, post_high_blackout_min=15)
    # 10 min vor dem Termin → Blackout
    a = assess_news(
        [cpi], cutoff=T0 - timedelta(minutes=10), asset_class=AssetClass.CRYPTO, params=p
    )
    assert a.context.blocking_event_id == "cpi-mar"
    assert a.blocks_entry
    # 45 min vorher → kein Blackout, aber minutes_to_next gesetzt
    b = assess_news(
        [cpi], cutoff=T0 - timedelta(minutes=45), asset_class=AssetClass.CRYPTO, params=p
    )
    assert b.context.blocking_event_id is None
    assert b.context.minutes_to_next_high_impact == 45.0
    # 30 min nach dem Termin → wieder frei
    c = assess_news(
        [cpi], cutoff=T0 + timedelta(minutes=30), asset_class=AssetClass.CRYPTO, params=p
    )
    assert c.context.blocking_event_id is None


def test_minutes_to_next_beyond_horizon_is_none() -> None:
    cpi = _ev("cpi-x", "US_CPI", NewsImpact.HIGH, T0, T0 - timedelta(days=20))
    a = assess_news(
        [cpi],
        cutoff=T0 - timedelta(hours=10),
        asset_class=AssetClass.CRYPTO,
        params=NewsWindowParams(watch_horizon_min=240),
    )
    assert a.context.minutes_to_next_high_impact is None
    assert a.upcoming_high and a.upcoming_high[0].minutes_until == 600.0


def test_risk_off_only_from_explicit_event_types() -> None:
    geo = _ev("geo-1", "GEOPOLITICS", NewsImpact.HIGH, T0, T0 - timedelta(minutes=60))
    big_surprise = _ev(
        "nfp-1",
        "US_NFP",
        NewsImpact.HIGH,
        T0 - timedelta(minutes=90),
        T0 - timedelta(minutes=60),
        actual=400.0,
        forecast=150.0,
    )
    a = assess_news([geo], cutoff=T0, asset_class=AssetClass.CRYPTO)
    assert a.context.risk_off is True
    # großer NFP-Surprise allein setzt KEIN risk_off (nur Evidence)
    b = assess_news([big_surprise], cutoff=T0, asset_class=AssetClass.CRYPTO)
    assert b.context.risk_off is False
    assert b.recent_released and b.recent_released[0].surprise_rel is not None


def test_no_feed_context_is_failsafe() -> None:
    c = no_feed_context()
    assert c.feed_as_of is None and c.feed_available is False


def test_build_news_context_shortcut() -> None:
    cpi = _ev("cpi-mar", "US_CPI", NewsImpact.HIGH, T0, T0 - timedelta(days=20))
    ctx = build_news_context(
        [cpi], cutoff=T0 - timedelta(minutes=10), asset_class=AssetClass.CRYPTO
    )
    assert ctx.blocking_event_id == "cpi-mar" and ctx.feed_available


def test_deterministic() -> None:
    evs = [
        _ev("a", "US_CPI", NewsImpact.HIGH, T0, T0 - timedelta(days=5)),
        _ev("b", "FOMC_RATE", NewsImpact.HIGH, T0 + timedelta(days=1), T0 - timedelta(days=5)),
    ]
    cutoff = T0 - timedelta(days=1)
    r1 = assess_news(evs, cutoff=cutoff, asset_class=AssetClass.CRYPTO)
    r2 = assess_news(list(reversed(evs)), cutoff=cutoff, asset_class=AssetClass.CRYPTO)
    assert [e.event_id for e in r1.relevant] == [e.event_id for e in r2.relevant]
    assert r1.context == r2.context
