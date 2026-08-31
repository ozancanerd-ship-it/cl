"""Tests: execution simulation (cost/fill/margin/liquidation), PaperBroker, BrokerRouter."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from trading_agent.core.enums import ProviderHealth, Side
from trading_agent.core.models import OHLCV
from trading_agent.core.time import parse_timestamp
from trading_agent.execution.brokers.base import BrokerAdapter, BrokerHealth, OrderIntent, OrderType
from trading_agent.execution.brokers.paper import PaperBroker
from trading_agent.execution.router import BrokerRouter, LiveOrderBlocked
from trading_agent.execution.simulation import (
    CostModel,
    FillModel,
    FillParams,
    LiquidationModel,
    MarginModel,
    PendingOrder,
)
from trading_agent.refdata.seed import build_instrument_master

IM = build_instrument_master()
BTC = IM.get("BTCUSDT")


# ---------------------------------------------------------------- cost / margin / liquidation


class TestCostModel:
    def test_taker_fee(self) -> None:
        cm = CostModel(BTC)
        # BTC taker 5.5 bps -> 0.055 %
        assert cm.fee(10_000.0, is_maker=False) == pytest.approx(5.5)
        assert cm.fee(10_000.0, is_maker=True) == pytest.approx(2.0)

    def test_funding_direction(self) -> None:
        cm = CostModel(BTC)
        # positive funding: long pays, short receives
        assert cm.funding_payment(notional=10_000.0, funding_rate=0.0001, side=Side.BUY) > 0
        assert cm.funding_payment(notional=10_000.0, funding_rate=0.0001, side=Side.SELL) < 0


class TestMarginLiquidation:
    def test_required_margin_and_leverage(self) -> None:
        mm = MarginModel(BTC)
        assert mm.required_margin(1000.0, 10.0) == pytest.approx(100.0)
        lev = mm.leverage_for(300.0, 50.0)  # need 6x
        assert 5.9 < lev < 6.1

    def test_liq_price_long_below_short_above(self) -> None:
        mm = MarginModel(BTC)
        lm = LiquidationModel(mm)
        long_liq = lm.liquidation_price(entry=100.0, side=Side.BUY, leverage=10.0, notional=1000.0)
        short_liq = lm.liquidation_price(
            entry=100.0, side=Side.SELL, leverage=10.0, notional=1000.0
        )
        assert long_liq < 100.0 < short_liq
        assert 88.0 < long_liq < 92.0  # ~ entry*(1 - 0.1 + mmr)

    def test_hit_detection(self) -> None:
        bar = _bar("2024-06-01T00:00:00Z", 100, 101, 89, 95)
        assert LiquidationModel.hit(side=Side.BUY, liq_price=90.0, bar=bar)
        assert not LiquidationModel.hit(side=Side.SELL, liq_price=110.0, bar=bar)


# ---------------------------------------------------------------- fill model


def _bar(t: str, o: float, h: float, low: float, c: float, v: float = 100.0) -> OHLCV:
    from trading_agent.core.enums import Timeframe
    from trading_agent.core.time import bar_close_time

    ot = parse_timestamp(t)
    return OHLCV(
        instrument="BTCUSDT",
        timeframe=Timeframe.M5,
        open_time=ot,
        close_time=bar_close_time(ot, Timeframe.M5),
        open=o,
        high=h,
        low=low,
        close=c,
        volume=v,
    )


class TestFillModel:
    def test_market_fills_next_bar_open_with_slippage(self) -> None:
        fm = FillModel(CostModel(BTC), FillParams())
        order = PendingOrder(
            OrderIntent(
                client_order_id="1",
                instrument="BTCUSDT",
                side=Side.BUY,
                order_type=OrderType.MARKET,
                qty=1.0,
            ),
            remaining_qty=1.0,
            submitted_bar_index=0,
        )
        assert fm.try_fill(order, _bar("2024-06-01T00:00:00Z", 100, 100, 100, 100), 0) == []
        fills = fm.try_fill(order, _bar("2024-06-01T00:05:00Z", 100, 101, 99, 100), 1, atr=1.0)
        assert len(fills) == 1 and fills[0].price > 100.0  # buy pays up

    def test_limit_fills_only_when_price_traded_through(self) -> None:
        fm = FillModel(CostModel(BTC))
        order = PendingOrder(
            OrderIntent(
                client_order_id="2",
                instrument="BTCUSDT",
                side=Side.BUY,
                order_type=OrderType.LIMIT,
                qty=1.0,
                limit_price=99.0,
            ),
            remaining_qty=1.0,
            submitted_bar_index=0,
        )
        assert fm.try_fill(order, _bar("2024-06-01T00:00:00Z", 100, 101, 99.5, 100), 1) == []
        fills = fm.try_fill(order, _bar("2024-06-01T00:05:00Z", 100, 101, 98.0, 100), 2)
        assert fills and fills[0].price == pytest.approx(99.0)

    def test_partial_fill_by_participation(self) -> None:
        fm = FillModel(CostModel(BTC), FillParams(partial_fills=True, max_participation=0.10))
        order = PendingOrder(
            OrderIntent(
                client_order_id="3",
                instrument="BTCUSDT",
                side=Side.BUY,
                order_type=OrderType.MARKET,
                qty=100.0,
            ),
            remaining_qty=100.0,
            submitted_bar_index=0,
        )
        fills = fm.try_fill(order, _bar("2024-06-01T00:05:00Z", 100, 100, 100, 100, v=50.0), 1)
        assert fills[0].qty == pytest.approx(5.0)  # 10 % of 50 volume
        assert fills[0].is_partial
        assert order.remaining_qty == pytest.approx(95.0)


# ---------------------------------------------------------------- paper broker


class TestPaperBroker:
    async def test_open_and_close_with_pnl_and_fees(self) -> None:
        pb = PaperBroker(IM, starting_equity=10_000.0)
        await pb.submit(
            OrderIntent(
                client_order_id="e1",
                instrument="BTCUSDT",
                side=Side.BUY,
                order_type=OrderType.MARKET,
                qty=1.0,
            )
        )
        pb.on_bar(_bar("2024-06-01T00:00:00Z", 100, 100, 100, 100))  # nothing (market waits)
        pb.on_bar(_bar("2024-06-01T00:05:00Z", 100, 100, 100, 100))  # fills at ~100
        assert pb.open_position("BTCUSDT") is not None
        assert pb.fees_paid > 0

        await pb.submit(
            OrderIntent(
                client_order_id="x1",
                instrument="BTCUSDT",
                side=Side.SELL,
                order_type=OrderType.MARKET,
                qty=1.0,
                reduce_only=True,
            )
        )
        pb.on_bar(_bar("2024-06-01T00:10:00Z", 110, 110, 110, 110))
        assert pb.open_position("BTCUSDT") is None
        # +10 price move on 1 unit = +10, minus fees
        assert 8.0 < pb.realized_pnl < 10.0

    async def test_liquidation_closes_position(self) -> None:
        pb = PaperBroker(IM, starting_equity=100.0)
        await pb.submit(
            OrderIntent(
                client_order_id="e2",
                instrument="BTCUSDT",
                side=Side.BUY,
                order_type=OrderType.MARKET,
                qty=1.0,
                leverage=20.0,
            )
        )
        pb.on_bar(_bar("2024-06-01T00:00:00Z", 100, 100, 100, 100))
        pb.on_bar(_bar("2024-06-01T00:05:00Z", 100, 100, 100, 100))  # fill @100, liq ~95.3
        fills = pb.on_bar(_bar("2024-06-01T00:10:00Z", 100, 100, 80, 85))
        assert any(f.is_liquidation for f in fills)
        assert pb.open_position("BTCUSDT") is None

    async def test_funding_reduces_equity_for_long(self) -> None:
        pb = PaperBroker(IM, starting_equity=10_000.0)
        await pb.submit(
            OrderIntent(
                client_order_id="e3",
                instrument="BTCUSDT",
                side=Side.BUY,
                order_type=OrderType.MARKET,
                qty=1.0,
            )
        )
        pb.on_bar(_bar("2024-06-01T00:00:00Z", 100, 100, 100, 100))
        pb.on_bar(_bar("2024-06-01T00:05:00Z", 100, 100, 100, 100))
        eq_before = pb.equity
        pb.apply_funding("BTCUSDT", 0.001)  # long pays
        assert pb.equity < eq_before


# ---------------------------------------------------------------- broker router


class _FakeBroker(BrokerAdapter):
    def __init__(
        self, name: str, *, live: bool = False, health: ProviderHealth = ProviderHealth.HEALTHY
    ) -> None:
        self.name = name
        self.is_live_capable = live
        self._h = health
        self.submitted: list[OrderIntent] = []

    def health(self) -> BrokerHealth:
        return BrokerHealth(
            broker=self.name, health=self._h, checked_at=parse_timestamp("2024-06-01T00:00:00Z")
        )

    async def submit(self, intent: OrderIntent) -> str:
        self.submitted.append(intent)
        return intent.client_order_id

    async def cancel(self, client_order_id: str) -> None:
        return None


def _intent() -> OrderIntent:
    return OrderIntent(
        client_order_id="o",
        instrument="BTCUSDT",
        side=Side.BUY,
        order_type=OrderType.MARKET,
        qty=1.0,
    )


class TestBrokerRouter:
    async def test_routes_to_primary(self) -> None:
        r = BrokerRouter(
            mode="paper_live", routes={"crypto:*": {"primary": "kraken", "fallback": ["bybit"]}}
        )
        k, b = _FakeBroker("kraken"), _FakeBroker("bybit")
        r.register(k)
        r.register(b)
        await r.submit(_intent())
        assert k.submitted and not b.submitted

    async def test_falls_back_when_primary_unhealthy(self) -> None:
        r = BrokerRouter(
            mode="paper_live", routes={"crypto:*": {"primary": "kraken", "fallback": ["bybit"]}}
        )
        r.register(_FakeBroker("kraken", health=ProviderHealth.UNAVAILABLE))
        b = _FakeBroker("bybit")
        r.register(b)
        await r.submit(_intent())
        assert b.submitted

    def test_refuses_to_register_live_adapter_in_sim_mode(self) -> None:
        r = BrokerRouter(mode="paper_live")
        with pytest.raises(LiveOrderBlocked):
            r.register(_FakeBroker("real", live=True))

    async def test_paper_broker_is_not_live_capable(self) -> None:
        assert PaperBroker(IM).is_live_capable is False


def _unused(x: Callable[..., object]) -> None:  # keep import used for mypy strictness in helpers
    return None
