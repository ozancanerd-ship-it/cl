"""Broker adapter contracts + order/fill domain types.

Strict separation:
* ``MarketDataAdapter`` — read-only (klines, quotes, ...); no keys needed for public data.
* ``BrokerAdapter`` — places/cancels orders, reports fills/positions. Keys via ``security/secrets``.

The Strategy Engine never imports these. Only ``execution/router.BrokerRouter`` does, and only
*after* the Risk Engine has approved an ``OrderIntent``.
"""

from __future__ import annotations

import abc
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from trading_agent.core.enums import ProviderHealth, Side
from trading_agent.core.models import UtcDatetime


class OrderType(StrEnum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"


class TimeInForce(StrEnum):
    GTC = "gtc"
    IOC = "ioc"
    FOK = "fok"


class OrderIntent(BaseModel):
    """Broker-neutral order request. Produced by trade management / signal engine,
    approved by risk, executed by a broker adapter or the paper simulator."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    client_order_id: str
    instrument: str
    side: Side
    order_type: OrderType
    qty: float = Field(gt=0)
    limit_price: float | None = None
    stop_price: float | None = None
    tif: TimeInForce = TimeInForce.GTC
    reduce_only: bool = False
    leverage: float = Field(default=1.0, ge=1.0)
    created_at: UtcDatetime | None = None
    trace_id: str | None = None


class Fill(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    client_order_id: str
    instrument: str
    side: Side
    qty: float
    price: float
    fee_ccy: float = 0.0
    ts: UtcDatetime
    is_partial: bool = False
    is_liquidation: bool = False
    trace_id: str | None = None


class BrokerHealth(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    broker: str
    health: ProviderHealth
    checked_at: UtcDatetime
    detail: str = ""


class MarketDataAdapter(abc.ABC):
    name: str = "abstract-md"

    @abc.abstractmethod
    def health(self) -> BrokerHealth: ...


class BrokerAdapter(abc.ABC):
    """Trade-capable broker. Implementations: PaperBroker (now), Kraken/Bybit/Pepperstone (phase 9)."""

    name: str = "abstract-broker"
    #: which canonical instruments this adapter can trade
    instruments: frozenset[str] = frozenset()
    #: True only for real-money adapters; PaperBroker is False
    is_live_capable: bool = False

    @abc.abstractmethod
    def health(self) -> BrokerHealth: ...

    @abc.abstractmethod
    async def submit(self, intent: OrderIntent) -> str:
        """Accept an order intent. Returns the broker/exchange order id (or client id for paper)."""

    @abc.abstractmethod
    async def cancel(self, client_order_id: str) -> None: ...

    async def aclose(self) -> None:  # pragma: no cover - default
        return None


def utcnow_iso(dt: datetime) -> str:  # small helper used by adapters/logs
    return dt.isoformat()


__all__ = [
    "BrokerAdapter",
    "BrokerHealth",
    "Fill",
    "MarketDataAdapter",
    "OrderIntent",
    "OrderType",
    "TimeInForce",
]
