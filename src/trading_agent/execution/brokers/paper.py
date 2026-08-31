"""PaperBroker — simulated execution. NEVER sends a real-money order.

Used by the backtest engine and by PAPER_LIVE (real live data, simulated fills). Fills come
from ``execution/simulation`` and have the exact same ``Fill`` shape a real adapter would emit.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from trading_agent.core.clock import Clock, SystemClock
from trading_agent.core.enums import ProviderHealth, Side
from trading_agent.core.models import OHLCV
from trading_agent.execution.brokers.base import (
    BrokerAdapter,
    BrokerHealth,
    Fill,
    OrderIntent,
)
from trading_agent.execution.simulation import (
    CostModel,
    FillModel,
    FillParams,
    LiquidationModel,
    MarginModel,
    PendingOrder,
    SimPosition,
)
from trading_agent.refdata.instruments import InstrumentMaster

FillCallback = Callable[[Fill], None]


class PaperBroker(BrokerAdapter):
    name = "paper"
    is_live_capable = False

    def __init__(
        self,
        instruments: InstrumentMaster,
        *,
        starting_equity: float = 1000.0,
        fill_params: FillParams | None = None,
        clock: Clock | None = None,
        on_fill: FillCallback | None = None,
    ) -> None:
        self._im = instruments
        self._clock = clock or SystemClock()
        self._on_fill = on_fill
        self.starting_equity = starting_equity
        self.realized_pnl = 0.0
        self.fees_paid = 0.0
        self.funding_paid = 0.0
        self.positions: dict[str, SimPosition] = {}
        self._pending: dict[str, list[PendingOrder]] = {}
        self._bar_index: dict[str, int] = {}
        self._cost: dict[str, CostModel] = {}
        self._margin: dict[str, MarginModel] = {}
        self._liq: dict[str, LiquidationModel] = {}
        self._fill_models: dict[str, FillModel] = {}
        self._fill_params = fill_params or FillParams()
        self.all_fills: list[Fill] = []

    # ------------------------------------------------------------------ BrokerAdapter

    def health(self) -> BrokerHealth:
        return BrokerHealth(
            broker=self.name, health=ProviderHealth.HEALTHY, checked_at=self._clock.now()
        )

    async def submit(self, intent: OrderIntent) -> str:
        self._ensure_models(intent.instrument)
        idx = self._bar_index.get(intent.instrument.upper(), 0)
        self._pending.setdefault(intent.instrument.upper(), []).append(
            PendingOrder(intent=intent, remaining_qty=intent.qty, submitted_bar_index=idx)
        )
        return intent.client_order_id

    async def cancel(self, client_order_id: str) -> None:
        for orders in self._pending.values():
            orders[:] = [o for o in orders if o.intent.client_order_id != client_order_id]

    # ------------------------------------------------------------------ driving the sim

    def _ensure_models(self, instrument: str) -> None:
        key = instrument.upper()
        if key in self._cost:
            return
        inst = self._im.get(key)
        self._cost[key] = CostModel(inst)
        self._margin[key] = MarginModel(inst)
        self._liq[key] = LiquidationModel(self._margin[key])
        self._fill_models[key] = FillModel(self._cost[key], self._fill_params)

    def on_bar(
        self, bar: OHLCV, *, atr: float | None = None, spread: float | None = None
    ) -> list[Fill]:
        key = bar.instrument.upper()
        self._ensure_models(key)
        self._bar_index[key] = self._bar_index.get(key, 0) + 1
        idx = self._bar_index[key]
        produced: list[Fill] = []

        # 1) liquidation check on any open position (before new fills)
        pos = self.positions.get(key)
        if pos is not None and LiquidationModel.hit(
            side=pos.side, liq_price=pos.liq_price, bar=bar
        ):
            produced.append(
                self._close_position(key, pos.liq_price, bar.close_time, liquidation=True)
            )

        # 2) process pending orders
        fm = self._fill_models[key]
        for order in list(self._pending.get(key, [])):
            fills = fm.try_fill(order, bar, idx, atr=atr, spread=spread)
            for f in fills:
                self._apply_fill(f, bar.close_time)
                produced.append(f)
            if order.remaining_qty <= 1e-12:
                self._pending[key].remove(order)

        for f in produced:
            self.all_fills.append(f)
            if self._on_fill:
                self._on_fill(f)
        return produced

    def apply_funding(self, instrument: str, funding_rate: float) -> float:
        key = instrument.upper()
        pos = self.positions.get(key)
        if pos is None:
            return 0.0
        pay = self._cost[key].funding_payment(
            notional=pos.notional, funding_rate=funding_rate, side=pos.side
        )
        pos.funding_paid += pay
        self.funding_paid += pay
        self.realized_pnl -= pay
        return pay

    # ------------------------------------------------------------------ position math

    def _apply_fill(self, fill: Fill, ts: datetime) -> None:
        key = fill.instrument.upper()
        self.fees_paid += fill.fee_ccy
        self.realized_pnl -= fill.fee_ccy
        pos = self.positions.get(key)

        if pos is None:
            lev = max(1.0, self._pending_leverage(key, fill))
            notional = fill.qty * fill.price
            self.positions[key] = SimPosition(
                instrument=key,
                side=fill.side,
                qty=fill.qty,
                entry_price=fill.price,
                leverage=lev,
                margin=self._margin[key].required_margin(notional, lev),
                liq_price=self._liq[key].liquidation_price(
                    entry=fill.price, side=fill.side, leverage=lev, notional=notional
                ),
                opened_at=ts,
                fees_paid=fill.fee_ccy,
            )
            return

        if fill.side is pos.side:  # add to position (weighted avg)
            total = pos.qty + fill.qty
            pos.entry_price = (pos.entry_price * pos.qty + fill.price * fill.qty) / total
            pos.qty = total
            pos.fees_paid += fill.fee_ccy
        else:  # reduce / close
            closed = min(fill.qty, pos.qty)
            d = fill.price - pos.entry_price
            pnl = d * closed if pos.side is Side.BUY else -d * closed
            self.realized_pnl += pnl
            pos.qty -= closed
            if pos.qty <= 1e-12:
                del self.positions[key]

    def _pending_leverage(self, key: str, fill: Fill) -> float:
        for o in self._pending.get(key, []):
            if o.intent.client_order_id == fill.client_order_id:
                return o.intent.leverage
        return 1.0

    def _close_position(self, key: str, price: float, ts: datetime, *, liquidation: bool) -> Fill:
        pos = self.positions.pop(key)
        exit_side = Side.SELL if pos.side is Side.BUY else Side.BUY
        d = price - pos.entry_price
        pnl = d * pos.qty if pos.side is Side.BUY else -d * pos.qty
        self.realized_pnl += pnl
        fee = self._cost[key].fee(pos.qty * price, is_maker=False)
        self.fees_paid += fee
        self.realized_pnl -= fee
        return Fill(
            client_order_id=f"close-{key}-{int(ts.timestamp())}",
            instrument=key,
            side=exit_side,
            qty=pos.qty,
            price=price,
            fee_ccy=fee,
            ts=ts,
            is_liquidation=liquidation,
        )

    # ------------------------------------------------------------------ readouts

    @property
    def equity(self) -> float:
        return self.starting_equity + self.realized_pnl

    def open_position(self, instrument: str) -> SimPosition | None:
        return self.positions.get(instrument.upper())


__all__ = ["FillCallback", "PaperBroker"]
