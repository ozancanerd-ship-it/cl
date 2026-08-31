"""Tests: ``analysis.macro`` — PIT-Vintages, Trend-Terme, risk_sentiment, UNKNOWN statt Fake."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from trading_agent.analysis.macro import MacroParams, assess_macro, unknown_macro
from trading_agent.core.enums import (
    MacroRateCycle,
    MacroRiskSentiment,
    MacroTrend,
)
from trading_agent.core.models import MacroEvent

AS_OF = datetime(2025, 6, 1, tzinfo=UTC)


def _macro(
    series: str, ref: datetime, value: float, *, avail: datetime | None = None, rev: int = 0
) -> MacroEvent:
    return MacroEvent(
        series_id=series,
        reference_period=ref,
        value=value,
        available_time=avail or (ref + timedelta(days=14)),
        revision=rev,
    )


def _monthly(series: str, start: datetime, values: list[float]) -> list[MacroEvent]:
    return [_macro(series, start + timedelta(days=31 * i), v) for i, v in enumerate(values)]


def test_rate_cycle_tightening_and_easing() -> None:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    hiking = _monthly("FED_FUNDS_TARGET_UPPER", start, [4.5, 4.75, 5.0, 5.25, 5.5, 5.5, 5.5])
    m = assess_macro({"FED_FUNDS_TARGET_UPPER": hiking}, as_of=AS_OF)
    assert m.rate_cycle is MacroRateCycle.TIGHTENING

    cutting = _monthly("FED_FUNDS_TARGET_UPPER", start, [5.5, 5.5, 5.25, 5.0, 4.75, 4.5, 4.25])
    m2 = assess_macro({"FED_FUNDS_TARGET_UPPER": cutting}, as_of=AS_OF)
    assert m2.rate_cycle is MacroRateCycle.EASING


def test_rate_cycle_hold_when_flat() -> None:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    flat = _monthly("FED_FUNDS_TARGET_UPPER", start, [5.5] * 8)
    m = assess_macro({"FED_FUNDS_TARGET_UPPER": flat}, as_of=AS_OF)
    assert m.rate_cycle is MacroRateCycle.HOLD


def test_inflation_trend_yoy_falling() -> None:
    start = datetime(2023, 1, 1, tzinfo=UTC)
    # Index steigt, aber die YoY-Rate fällt (Disinflation)
    idx = [300.0]
    rates = [0.008] * 12 + [0.005] * 12  # MoM: erst ~9.6% YoY, dann ~6% YoY
    for r in rates:
        idx.append(idx[-1] * (1 + r))
    series = _monthly("US_CORE_CPI", start, idx)
    m = assess_macro({"US_CORE_CPI": series}, as_of=datetime(2025, 3, 1, tzinfo=UTC))
    assert m.inflation_trend is MacroTrend.FALLING


def test_growth_trend_from_nfp_and_unemployment_inverted() -> None:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    nfp_up = _monthly("US_NFP", start, [150000, 158000, 166000, 175000, 185000, 195000])
    m = assess_macro({"US_NFP": nfp_up}, as_of=AS_OF)
    assert m.growth_trend is MacroTrend.RISING

    unrate_up = _monthly("US_UNEMPLOYMENT", start, [3.7, 3.8, 3.9, 4.0, 4.1, 4.3])
    m2 = assess_macro({"US_UNEMPLOYMENT": unrate_up}, as_of=AS_OF)
    assert m2.growth_trend is MacroTrend.FALLING  # invertiert: steigende Arbeitslosigkeit


def test_pit_revision_respected() -> None:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    prior = _monthly("US_NFP", start, [50000.0, 60000.0, 70000.0])  # Jan–Mär, steigend
    ref = datetime(2025, 4, 1, tzinfo=UTC)
    first = _macro("US_NFP", ref, 100000.0, avail=ref + timedelta(days=7), rev=0)  # weiter steigend
    revised = _macro(
        "US_NFP", ref, 20000.0, avail=ref + timedelta(days=40), rev=1
    )  # dreht nach unten
    series = [*prior, first, revised]
    p = MacroParams(min_points_trend=4)
    # as_of vor der Revision → Erstwert 100k ⇒ Wachstum steigt weiter
    early = assess_macro({"US_NFP": series}, as_of=ref + timedelta(days=10), params=p)
    assert early.growth_trend is MacroTrend.RISING
    # as_of nach der Revision → 20k ⇒ Wachstum fällt
    late = assess_macro({"US_NFP": series}, as_of=ref + timedelta(days=60), params=p)
    assert late.growth_trend is MacroTrend.FALLING


def test_risk_sentiment_risk_off_and_risk_on() -> None:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    vix_hi = [_macro("VIX", start + timedelta(days=i), 30.0 + i) for i in range(30)]
    m = assess_macro({"VIX": vix_hi}, as_of=datetime(2025, 3, 1, tzinfo=UTC))
    assert m.risk_sentiment is MacroRiskSentiment.RISK_OFF

    vix_lo = [_macro("VIX", start + timedelta(days=i), 12.0) for i in range(30)]
    m2 = assess_macro({"VIX": vix_lo}, as_of=datetime(2025, 3, 1, tzinfo=UTC))
    assert m2.risk_sentiment is MacroRiskSentiment.RISK_ON


def test_unknown_when_no_data() -> None:
    m = assess_macro({}, as_of=AS_OF)
    assert m.rate_cycle is MacroRateCycle.UNKNOWN
    assert m.inflation_trend is MacroTrend.UNKNOWN
    assert m.growth_trend is MacroTrend.UNKNOWN
    assert m.risk_sentiment is MacroRiskSentiment.UNKNOWN
    assert m.known is False

    u = unknown_macro(AS_OF)
    assert u.known is False and u.cross_asset.as_of is None


def test_deterministic_regardless_of_input_order() -> None:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    s = _monthly("FED_FUNDS_TARGET_UPPER", start, [4.5, 4.75, 5.0, 5.25, 5.5, 5.5])
    a = assess_macro({"FED_FUNDS_TARGET_UPPER": s}, as_of=AS_OF)
    b = assess_macro({"FED_FUNDS_TARGET_UPPER": list(reversed(s))}, as_of=AS_OF)
    assert a == b
