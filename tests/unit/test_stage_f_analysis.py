"""Stufe F — Market Breadth, Stock Fundamentals, Earnings Engine (Masterplan §19–§21)."""

from __future__ import annotations

from datetime import timedelta

from trading_agent.analysis.breadth import BreadthRegime, compute_market_breadth
from trading_agent.analysis.earnings import EarningsEvent, EarningsState, assess_earnings
from trading_agent.analysis.fundamentals import (
    FundamentalVerdict,
    StockFundamentals,
    assess_fundamentals,
    unknown_fundamentals,
)
from trading_agent.core.enums import Timeframe
from trading_agent.core.models import OHLCV
from trading_agent.core.time import bar_close_time, parse_timestamp

_AS_OF = parse_timestamp("2026-08-31T00:00:00Z")


# --------------------------------------------------------------------------- Breadth


def _daily(inst: str, step: float, n: int = 60) -> list[OHLCV]:
    t = parse_timestamp("2026-06-01T00:00:00Z")
    price = 100.0
    bars: list[OHLCV] = []
    for _ in range(n):
        nxt = price + step
        bars.append(
            OHLCV(
                instrument=inst,
                timeframe=Timeframe.D1,
                open_time=t,
                close_time=bar_close_time(t, Timeframe.D1),
                open=price,
                high=max(price, nxt) + 0.5,
                low=min(price, nxt) - 0.5,
                close=nxt,
                volume=10.0,
                source="test",
            )
        )
        price = nxt
        t += timedelta(days=1)
    return bars


def test_breadth_risk_on_when_most_advance() -> None:
    series = {f"S{i}": _daily(f"S{i}", step=0.8) for i in range(8)}
    b = compute_market_breadth(series, as_of=_AS_OF)
    assert b.regime is BreadthRegime.RISK_ON
    assert b.advancers == 8 and b.decliners == 0
    assert b.pct_above_sma20 == 1.0
    assert b.breadth_score > 0.25


def test_breadth_risk_off_when_most_decline() -> None:
    series = {f"S{i}": _daily(f"S{i}", step=-0.8) for i in range(8)}
    b = compute_market_breadth(series, as_of=_AS_OF)
    assert b.regime is BreadthRegime.RISK_OFF
    assert b.decliners == 8
    assert b.breadth_score < -0.25


def test_breadth_unknown_when_too_few_instruments() -> None:
    series = {
        "AAA": _daily("AAA", step=0.5),
        "BBB": _daily("BBB", step=0.5),
    }
    b = compute_market_breadth(series, as_of=_AS_OF, min_instruments=5)
    assert b.regime is BreadthRegime.UNKNOWN
    assert b.evaluated == 2


def test_breadth_is_point_in_time() -> None:
    series = {f"S{i}": _daily(f"S{i}", step=0.8, n=60) for i in range(6)}
    early = compute_market_breadth(series, as_of=parse_timestamp("2026-06-10T00:00:00Z"))
    late = compute_market_breadth(series, as_of=_AS_OF)
    assert early.evaluated == 6
    # frühere Sicht kennt weniger Bars → SMA50 noch nicht verfügbar
    assert early.pct_above_sma50 is None and late.pct_above_sma50 is not None


# --------------------------------------------------------------------------- Fundamentals


def test_fundamentals_strong_growth_quality() -> None:
    f = StockFundamentals(
        symbol="NVDA",
        as_of_report=parse_timestamp("2026-08-01T00:00:00Z"),
        forward_pe=28.0,
        peg=1.1,
        revenue_growth_yoy=0.45,
        eps_growth_yoy=0.50,
        gross_margin=0.72,
        operating_margin=0.40,
        roe=0.35,
        fcf_margin=0.30,
        net_debt_to_ebitda=0.2,
        current_ratio=3.0,
        interest_coverage=40.0,
    )
    ctx = assess_fundamentals(f, as_of=_AS_OF)
    assert ctx.verdict in (FundamentalVerdict.STRONG, FundamentalVerdict.SOLID)
    assert ctx.growth is not None and ctx.growth > 0.8
    assert ctx.quality is not None and ctx.quality > 0.8
    assert ctx.known


def test_fundamentals_weak_when_unprofitable_and_expensive() -> None:
    f = StockFundamentals(
        symbol="XYZ",
        as_of_report=parse_timestamp("2026-08-01T00:00:00Z"),
        forward_pe=90.0,
        price_to_sales=20.0,
        revenue_growth_yoy=-0.10,
        eps_growth_yoy=-0.40,
        operating_margin=-0.15,
        roe=-0.05,
        net_debt_to_ebitda=8.0,
        current_ratio=0.8,
    )
    ctx = assess_fundamentals(f, as_of=_AS_OF)
    assert ctx.verdict is FundamentalVerdict.WEAK


def test_fundamentals_unknown_when_no_metrics() -> None:
    f = StockFundamentals(symbol="AAA", as_of_report=parse_timestamp("2026-08-01T00:00:00Z"))
    ctx = assess_fundamentals(f, as_of=_AS_OF)
    assert ctx.verdict is FundamentalVerdict.UNKNOWN
    assert not ctx.known
    assert unknown_fundamentals("AAA", _AS_OF).composite is None


# --------------------------------------------------------------------------- Earnings


def test_earnings_blackout_blocks_new_entry() -> None:
    events = [EarningsEvent(symbol="AAPL", when=_AS_OF + timedelta(days=3), confirmed=True)]
    ctx = assess_earnings("AAPL", events, as_of=_AS_OF)
    assert ctx.state is EarningsState.BLACKOUT
    assert ctx.blocks_new_entry
    assert ctx.days_until is not None and ctx.days_until <= 5


def test_earnings_clear_when_far_away() -> None:
    events = [EarningsEvent(symbol="AAPL", when=_AS_OF + timedelta(days=40))]
    ctx = assess_earnings("AAPL", events, as_of=_AS_OF)
    assert ctx.state is EarningsState.CLEAR and not ctx.blocks_new_entry


def test_earnings_post_report_drift_bias() -> None:
    events = [
        EarningsEvent(
            symbol="AAPL",
            when=_AS_OF - timedelta(days=2),
            confirmed=True,
            eps_estimate=1.00,
            eps_actual=1.20,
        )
    ]
    ctx = assess_earnings("AAPL", events, as_of=_AS_OF)
    assert ctx.state is EarningsState.JUST_REPORTED
    assert ctx.drift_bias == 1
    assert ctx.surprise_pct == 0.2


def test_earnings_unknown_without_calendar() -> None:
    ctx = assess_earnings("AAPL", [], as_of=_AS_OF)
    assert ctx.state is EarningsState.UNKNOWN and not ctx.blocks_new_entry
