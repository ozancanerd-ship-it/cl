"""Unified execution simulation — used identically by backtest AND paper.

Four models, all deterministic:

* ``CostModel``        — maker/taker fees, spread, slippage, funding accrual.
* ``FillModel``        — limit / market / stop fills against a bar, partial fills, latency.
* ``MarginModel``      — required margin, dynamic leverage bounds (Bybit/Kraken linear perp, isolated).
* ``LiquidationModel`` — isolated-margin liquidation price + intrabar liquidation check.

The real broker adapters (phase 9) must produce fills in the *same* ``Fill`` shape so that
backtest = paper = live on the execution side.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from trading_agent.core.enums import Side
from trading_agent.core.models import OHLCV
from trading_agent.execution.brokers.base import Fill, OrderIntent, OrderType
from trading_agent.refdata.models import Instrument

# --------------------------------------------------------------------------------------------
# Cost model
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CostParams:
    slippage_atr: float = 0.05  # slippage as fraction of ATR, per side
    slippage_spread_mult: float = 0.5  # + this * spread, per side
    min_slippage_bps: float = 0.5  # floor when ATR/spread unknown


class CostModel:
    def __init__(self, instrument: Instrument, params: CostParams | None = None) -> None:
        self.inst = instrument
        self.p = params or CostParams()

    def fee(self, notional: float, *, is_maker: bool) -> float:
        bps = self.inst.fees.maker_bps if is_maker else self.inst.fees.taker_bps
        return abs(notional) * bps / 10_000.0

    def slippage(self, *, atr: float | None, spread: float | None, ref_price: float) -> float:
        """Absolute price slippage applied to the *unfavourable* side."""
        if atr is not None or spread is not None:
            return (atr or 0.0) * self.p.slippage_atr + (
                spread or 0.0
            ) * self.p.slippage_spread_mult
        return ref_price * self.p.min_slippage_bps / 10_000.0

    def funding_payment(self, *, notional: float, funding_rate: float, side: Side) -> float:
        """Positive = we PAY. Long pays when funding_rate > 0; short receives."""
        sign = 1.0 if side is Side.BUY else -1.0
        return sign * abs(notional) * funding_rate


# --------------------------------------------------------------------------------------------
# Margin + liquidation
# --------------------------------------------------------------------------------------------


class MarginModel:
    def __init__(self, instrument: Instrument) -> None:
        self.inst = instrument

    def maintenance_margin_rate(self, notional: float) -> float:
        rate = self.inst.margin_tiers[0].maintenance_margin_rate
        for tier in self.inst.margin_tiers:
            if abs(notional) >= tier.notional_floor:
                rate = tier.maintenance_margin_rate
        return rate

    def max_leverage(self, notional: float) -> float:
        lev = self.inst.margin_tiers[0].max_leverage
        for tier in self.inst.margin_tiers:
            if abs(notional) >= tier.notional_floor:
                lev = tier.max_leverage
        return min(lev, self.inst.max_leverage)

    def required_margin(self, notional: float, leverage: float) -> float:
        return abs(notional) / max(leverage, 1.0)

    def leverage_for(self, notional: float, margin_budget: float) -> float:
        if margin_budget <= 0:
            raise ValueError("margin_budget must be > 0")
        needed = abs(notional) / margin_budget
        return max(1.0, min(needed, self.max_leverage(notional)))


class LiquidationModel:
    def __init__(self, margin: MarginModel) -> None:
        self.margin = margin

    def liquidation_price(
        self, *, entry: float, side: Side, leverage: float, notional: float
    ) -> float:
        mmr = self.margin.maintenance_margin_rate(notional)
        inv = 1.0 / max(leverage, 1.0)
        if side is Side.BUY:
            return entry * (1.0 - inv + mmr)
        return entry * (1.0 + inv - mmr)

    @staticmethod
    def hit(*, side: Side, liq_price: float, bar: OHLCV) -> bool:
        if side is Side.BUY:
            return bar.low <= liq_price
        return bar.high >= liq_price


# --------------------------------------------------------------------------------------------
# Fill model
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FillParams:
    partial_fills: bool = False
    max_participation: float = 0.10  # max fraction of bar volume fillable per bar
    latency_bars: int = 0  # market orders fill this many bars later (0 = next bar open)
    sl_before_tp_same_bar: bool = True


@dataclass(slots=True)
class PendingOrder:
    intent: OrderIntent
    remaining_qty: float
    submitted_bar_index: int
    fills: list[Fill] = field(default_factory=list)


class FillModel:
    def __init__(self, cost: CostModel, params: FillParams | None = None) -> None:
        self.cost = cost
        self.p = params or FillParams()

    def try_fill(
        self,
        order: PendingOrder,
        bar: OHLCV,
        bar_index: int,
        *,
        atr: float | None = None,
        spread: float | None = None,
    ) -> list[Fill]:
        """Attempt to (partially) fill ``order`` against ``bar``. Returns fills produced this bar."""
        intent = order.intent
        if order.remaining_qty <= 0:
            return []

        want = order.remaining_qty
        if self.p.partial_fills and bar.volume > 0:
            want = min(want, bar.volume * self.p.max_participation)
        want = min(want, order.remaining_qty)

        px = self._match_price(intent, bar, bar_index, order.submitted_bar_index, atr, spread)
        if px is None:
            return []

        notional = want * px
        fee = self.cost.fee(notional, is_maker=intent.order_type is OrderType.LIMIT)
        is_partial = want < order.remaining_qty - 1e-12
        fill = Fill(
            client_order_id=intent.client_order_id,
            instrument=intent.instrument,
            side=intent.side,
            qty=round(want, 12),
            price=px,
            fee_ccy=fee,
            ts=bar.close_time,
            is_partial=is_partial,
            trace_id=intent.trace_id,
        )
        order.remaining_qty = round(order.remaining_qty - want, 12)
        order.fills.append(fill)
        return [fill]

    def _match_price(
        self,
        intent: OrderIntent,
        bar: OHLCV,
        bar_index: int,
        submitted_index: int,
        atr: float | None,
        spread: float | None,
    ) -> float | None:
        buy = intent.side is Side.BUY
        slip = self.cost.slippage(atr=atr, spread=spread, ref_price=bar.open)

        if intent.order_type is OrderType.MARKET:
            if bar_index < submitted_index + max(1, self.p.latency_bars):
                return None
            return bar.open + slip if buy else bar.open - slip

        if intent.order_type is OrderType.LIMIT:
            lp = intent.limit_price
            assert lp is not None
            if buy and bar.low <= lp:
                return min(lp, bar.open)  # gap-through fills at (better) open, else limit
            if not buy and bar.high >= lp:
                return max(lp, bar.open)
            return None

        # STOP
        sp = intent.stop_price
        assert sp is not None
        if buy and bar.high >= sp:
            return max(sp, bar.open) + slip
        if not buy and bar.low <= sp:
            return min(sp, bar.open) - slip
        return None


# --------------------------------------------------------------------------------------------
# Simple simulated position (used by PaperBroker + backtest)
# --------------------------------------------------------------------------------------------


@dataclass(slots=True)
class SimPosition:
    instrument: str
    side: Side
    qty: float
    entry_price: float
    leverage: float
    margin: float
    liq_price: float
    opened_at: datetime
    fees_paid: float = 0.0
    funding_paid: float = 0.0

    @property
    def notional(self) -> float:
        return self.qty * self.entry_price

    def unrealized_pnl(self, mark: float) -> float:
        d = mark - self.entry_price
        return d * self.qty if self.side is Side.BUY else -d * self.qty


__all__ = [
    "CostModel",
    "CostParams",
    "FillModel",
    "FillParams",
    "LiquidationModel",
    "MarginModel",
    "PendingOrder",
    "SimPosition",
]
