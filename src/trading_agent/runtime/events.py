"""Event types for the internal event bus.

Events are lightweight, immutable, and carry their own ``ts`` (event time — *when the event is
about*, not when it was dispatched). The bus preserves publication order; a handler that
publishes a new event enqueues it after the current one.

Backtest and live use the *same* event types and the *same* subscribers — only the *producer*
(``BacktestDriver`` vs ``LiveDriver``) differs.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime

from trading_agent.core.enums import Timeframe
from trading_agent.core.models import (
    OHLCV,
    DataQualityStatus,
    Funding,
    NewsEvent,
    OpenInterest,
    Quote,
    Trade,
)


@dataclass(frozen=True, slots=True)
class Event:
    """Base event. ``ts`` is the event time (UTC)."""

    ts: datetime
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex, kw_only=True)


# ---------------------------------------------------------------------------- market data


@dataclass(frozen=True, slots=True)
class BarClosed(Event):
    """A confirmed (``is_final``) OHLCV bar. The primary trigger for analysis/strategy."""

    instrument: str = ""
    timeframe: Timeframe = Timeframe.M5
    bar: OHLCV | None = None


@dataclass(frozen=True, slots=True)
class QuoteUpdate(Event):
    instrument: str = ""
    quote: Quote | None = None


@dataclass(frozen=True, slots=True)
class TradeTick(Event):
    instrument: str = ""
    trade: Trade | None = None


@dataclass(frozen=True, slots=True)
class FundingUpdate(Event):
    instrument: str = ""
    funding: Funding | None = None


@dataclass(frozen=True, slots=True)
class OpenInterestUpdate(Event):
    instrument: str = ""
    open_interest: OpenInterest | None = None


@dataclass(frozen=True, slots=True)
class NewsReceived(Event):
    news: NewsEvent | None = None


@dataclass(frozen=True, slots=True)
class DataQualityAlert(Event):
    """Emitted when a quality check on a series produced a blocking (CRITICAL) issue."""

    instrument: str = ""
    timeframe: Timeframe | None = None
    status: DataQualityStatus | None = None


# ---------------------------------------------------------------------------- strategy / paper

# Diese Events tragen absichtlich lose typisierte Nutzlast (``object``), damit ``runtime`` nicht
# von ``strategy`` abhängt (Import-Richtung). Die UI/Consumer casten selbst.


@dataclass(frozen=True, slots=True)
class DecisionMade(Event):
    """Ergebnis eines ``strategy.evaluate``-Laufs auf einem (Live-)``MarketContext``."""

    instrument: str = ""
    decision_type: str = ""  # BUY / SELL / WAIT / NO_TRADE
    setup_state: str = ""
    score: float | None = None
    confidence: float | None = None
    result: object = None  # EvaluationResult


@dataclass(frozen=True, slots=True)
class SignalRevised(Event):
    instrument: str = ""
    signal_id: str = ""
    state: str = ""
    change: str = ""  # STRENGTHENED / WEAKENED / ENTRY_CHANGED / ...
    signal: object = None  # SignalUpdate


@dataclass(frozen=True, slots=True)
class AlertRaised(Event):
    instrument: str = ""
    alert_type: str = ""
    message: str = ""
    delivered: bool = True
    alert: object = None  # AlertEvent


@dataclass(frozen=True, slots=True)
class PaperPositionChanged(Event):
    instrument: str = ""
    change: str = ""  # OPENED / FILLED / TP1 / SL / CLOSED / EXIT_REQUIRED / ...
    realized_r: float | None = None
    position: object = None  # PaperPosition


# ---------------------------------------------------------------------------- lifecycle


@dataclass(frozen=True, slots=True)
class Heartbeat(Event):
    component: str = "supervisor"


@dataclass(frozen=True, slots=True)
class ShutdownRequested(Event):
    reason: str = ""


@dataclass(frozen=True, slots=True)
class MarketObserved(Event):
    """Phase 2B scanner-shell marker: 'the market was looked at, no strategy yet'."""

    instrument: str = ""
    timeframe: Timeframe = Timeframe.M5
    note: str = ""


__all__ = [
    "AlertRaised",
    "BarClosed",
    "DataQualityAlert",
    "DecisionMade",
    "Event",
    "FundingUpdate",
    "Heartbeat",
    "MarketObserved",
    "NewsReceived",
    "OpenInterestUpdate",
    "PaperPositionChanged",
    "QuoteUpdate",
    "ShutdownRequested",
    "SignalRevised",
    "TradeTick",
]
