"""Kern-Datenmodelle der Data Foundation (Phase 1).

Alle Modelle sind:

* **unveränderlich** (``frozen=True``) – Records werden nicht nachträglich mutiert,
* **streng** (``extra="forbid"``) – unbekannte Felder sind ein Fehler,
* **versioniert** (``schema_version``),
* **UTC-normalisiert** – jedes Zeitfeld ist tz-aware UTC (naive Werte werden abgelehnt),
* **Point-in-Time-fähig** – jedes Record kennt seine ``available_time`` (ab wann es bekannt war).

``Instrument``, Sessions-Spezifikation, Symbol-Mapping und Corporate Actions liegen in
``trading_agent.refdata.models`` (Referenzdaten). Hier stehen die *Marktdaten*-Records.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Annotated, Protocol, runtime_checkable

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from trading_agent.core.enums import (
    DataKind,
    DataQualityCode,
    DataQualitySeverity,
    NewsImpact,
    SessionName,
    Side,
    Timeframe,
)
from trading_agent.core.time import bar_close_time, ensure_utc, is_aligned, parse_timestamp
from trading_agent.core.version import SCHEMA_VERSION

# --------------------------------------------------------------------------------------------
# Wiederverwendbare Feldtypen
# --------------------------------------------------------------------------------------------

UtcDatetime = Annotated[datetime, BeforeValidator(parse_timestamp)]
"""``datetime`` das jede Eingabe (str/int/float/datetime) zu tz-aware UTC normalisiert.
Naive ``datetime`` und mehrdeutige ISO-Strings ohne Offset werden abgelehnt."""


def _finite(v: float) -> float:
    if not math.isfinite(v):
        raise ValueError(f"nicht endlicher Zahlenwert: {v!r}")
    return float(v)


def _finite_non_negative(v: float) -> float:
    v = _finite(v)
    if v < 0:
        raise ValueError(f"Wert muss >= 0 sein: {v!r}")
    return v


def _finite_positive(v: float) -> float:
    v = _finite(v)
    if v <= 0:
        raise ValueError(f"Wert muss > 0 sein: {v!r}")
    return v


FiniteFloat = Annotated[float, BeforeValidator(_finite)]
NonNegFloat = Annotated[float, BeforeValidator(_finite_non_negative)]
PositiveFloat = Annotated[float, BeforeValidator(_finite_positive)]


@runtime_checkable
class HasAvailableTime(Protocol):
    """Alles, was einen Point-in-Time-Marker hat (ab wann das Record bekannt war)."""

    @property
    def available_time(self) -> datetime: ...


class Record(BaseModel):
    """Basisklasse für alle persistierbaren Datenmodelle.

    Jede Subklasse stellt ``available_time`` bereit – entweder als berechnete Property
    (Marktdaten: aus ``close_time`` bzw. ``ts``) oder als explizites Feld (News/Makro:
    die tatsächliche Veröffentlichungszeit).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = Field(default=SCHEMA_VERSION)


# --------------------------------------------------------------------------------------------
# 1. Marktdaten-Records
# --------------------------------------------------------------------------------------------


class OHLCV(Record):
    """Eine abgeschlossene Kerze. ``open_time`` an der Timeframe-Grenze ausgerichtet,
    ``close_time == open_time + timeframe``. Point-in-Time: bekannt ab ``close_time``."""

    instrument: str
    timeframe: Timeframe
    open_time: UtcDatetime
    close_time: UtcDatetime
    open: FiniteFloat
    high: FiniteFloat
    low: FiniteFloat
    close: FiniteFloat
    volume: NonNegFloat
    quote_volume: NonNegFloat | None = None
    trades: int | None = Field(default=None, ge=0)
    source: str = "unknown"
    ingested_at: UtcDatetime | None = None

    @model_validator(mode="after")
    def _check(self) -> OHLCV:
        if not is_aligned(self.open_time, self.timeframe):
            raise ValueError(
                f"open_time {self.open_time.isoformat()} ist nicht an {self.timeframe} ausgerichtet"
            )
        expected_close = bar_close_time(self.open_time, self.timeframe)
        if self.close_time != expected_close:
            raise ValueError(
                f"close_time {self.close_time.isoformat()} != open_time + {self.timeframe} "
                f"({expected_close.isoformat()})"
            )
        hi, lo = self.high, self.low
        if hi < lo:
            raise ValueError(f"high {hi} < low {lo}")
        if hi < max(self.open, self.close) or lo > min(self.open, self.close):
            raise ValueError(f"OHLC inkonsistent: o={self.open} h={hi} l={lo} c={self.close}")
        return self

    @property
    def available_time(self) -> datetime:
        return self.close_time

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def is_bullish(self) -> bool:
        return self.close > self.open


Candle = OHLCV  # gebräuchliches Synonym


class Quote(Record):
    """Bester Bid/Ask zu einem Zeitpunkt (Top-of-Book)."""

    instrument: str
    ts: UtcDatetime
    bid: PositiveFloat
    ask: PositiveFloat
    bid_size: NonNegFloat | None = None
    ask_size: NonNegFloat | None = None
    source: str = "unknown"
    ingested_at: UtcDatetime | None = None

    @model_validator(mode="after")
    def _check(self) -> Quote:
        if self.ask < self.bid:
            raise ValueError(f"ask {self.ask} < bid {self.bid}")
        return self

    @property
    def available_time(self) -> datetime:
        return self.ts

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0

    @property
    def spread(self) -> float:
        return self.ask - self.bid


class Trade(Record):
    """Ein einzelner ausgeführter Handel (öffentliche Marktdaten, kein Account-Trade)."""

    instrument: str
    ts: UtcDatetime
    price: PositiveFloat
    size: PositiveFloat
    side: Side | None = None
    trade_id: str | None = None
    source: str = "unknown"
    ingested_at: UtcDatetime | None = None

    @property
    def available_time(self) -> datetime:
        return self.ts

    @property
    def notional(self) -> float:
        return self.price * self.size


class OrderbookSnapshot(Record):
    """Ein Orderbuch-Schnappschuss. ``bids`` absteigend, ``asks`` aufsteigend sortiert."""

    instrument: str
    ts: UtcDatetime
    bids: list[tuple[FiniteFloat, FiniteFloat]]
    asks: list[tuple[FiniteFloat, FiniteFloat]]
    source: str = "unknown"
    ingested_at: UtcDatetime | None = None

    @field_validator("bids", "asks")
    @classmethod
    def _levels_valid(cls, levels: list[tuple[float, float]]) -> list[tuple[float, float]]:
        for price, size in levels:
            if price <= 0:
                raise ValueError(f"Orderbook-Preis muss > 0 sein: {price}")
            if size < 0:
                raise ValueError(f"Orderbook-Size muss >= 0 sein: {size}")
        return levels

    @model_validator(mode="after")
    def _check(self) -> OrderbookSnapshot:
        bid_prices = [p for p, _ in self.bids]
        ask_prices = [p for p, _ in self.asks]
        if bid_prices != sorted(bid_prices, reverse=True):
            raise ValueError("bids müssen absteigend nach Preis sortiert sein")
        if ask_prices != sorted(ask_prices):
            raise ValueError("asks müssen aufsteigend nach Preis sortiert sein")
        if bid_prices and ask_prices and bid_prices[0] >= ask_prices[0]:
            raise ValueError(
                f"best bid {bid_prices[0]} >= best ask {ask_prices[0]} (überkreuztes Buch)"
            )
        return self

    @property
    def available_time(self) -> datetime:
        return self.ts

    @property
    def best_bid(self) -> float | None:
        return self.bids[0][0] if self.bids else None

    @property
    def best_ask(self) -> float | None:
        return self.asks[0][0] if self.asks else None


class Funding(Record):
    """Funding-Rate eines Perpetual-Kontrakts zu einem Settlement-Zeitpunkt."""

    instrument: str
    ts: UtcDatetime
    rate: FiniteFloat
    interval_hours: PositiveFloat = 8.0
    next_funding_time: UtcDatetime | None = None
    source: str = "unknown"
    ingested_at: UtcDatetime | None = None

    @property
    def available_time(self) -> datetime:
        return self.ts


class OpenInterest(Record):
    """Offenes Interesse (Open Interest) eines Derivats zu einem Zeitpunkt."""

    instrument: str
    ts: UtcDatetime
    oi: NonNegFloat
    oi_value: NonNegFloat | None = None  # in Quote-Währung, falls bekannt
    source: str = "unknown"
    ingested_at: UtcDatetime | None = None

    @property
    def available_time(self) -> datetime:
        return self.ts


# --------------------------------------------------------------------------------------------
# 2. News / Makro – strikt Point-in-Time
# --------------------------------------------------------------------------------------------


class NewsEvent(Record):
    """Ein terminiertes/erschienenes Nachrichten-Ereignis.

    ``scheduled_time`` = geplanter Zeitpunkt. ``available_time`` = ab wann *dieser* Eintrag
    (inkl. ``actual``) bekannt war. Für Backtests darf **nur** verwendet werden, was zum
    Entscheidungszeitpunkt ``<= as_of`` verfügbar war.
    """

    event_id: str
    event_type: str  # z.B. "CPI", "FOMC_RATE", "TOKEN_UNLOCK"
    impact: NewsImpact
    scheduled_time: UtcDatetime
    available_time: UtcDatetime
    affected_symbols: list[str] = Field(default_factory=list)
    actual: FiniteFloat | None = None
    forecast: FiniteFloat | None = None
    previous: FiniteFloat | None = None
    source: str = "unknown"
    ingested_at: UtcDatetime | None = None

    @model_validator(mode="after")
    def _check(self) -> NewsEvent:
        # actual kann erst ab scheduled_time bekannt sein (kein "known before it happened").
        if self.actual is not None and self.available_time < self.scheduled_time:
            raise ValueError(
                "NewsEvent mit actual-Wert, aber available_time liegt vor scheduled_time "
                "(Future-Information-Leak)"
            )
        return self


class MacroEvent(Record):
    """Ein makroökonomischer Datenpunkt (Zeitreihenwert).

    ``reference_period`` = Zeitraum, auf den sich der Wert bezieht (z.B. Monat der Inflationszahl).
    ``available_time`` = Erstveröffentlichung *dieses* Werts. ``revision`` > 0 markiert
    nachträgliche Korrekturen (im Backtest nie den revidierten Wert vor seiner
    ``available_time`` verwenden).
    """

    series_id: str  # z.B. "US_CPI_YOY"
    reference_period: UtcDatetime
    value: FiniteFloat
    available_time: UtcDatetime
    revision: int = Field(default=0, ge=0)
    unit: str | None = None
    source: str = "unknown"
    ingested_at: UtcDatetime | None = None

    @model_validator(mode="after")
    def _check(self) -> MacroEvent:
        if self.available_time < self.reference_period:
            raise ValueError(
                "MacroEvent available_time liegt vor reference_period "
                "(ein Wert kann nicht vor seinem Bezugszeitraum veröffentlicht sein)"
            )
        return self


# --------------------------------------------------------------------------------------------
# 3. Trading Session (aufgelöstes Fenster)
# --------------------------------------------------------------------------------------------


class SessionWindow(Record):
    """Ein konkret nach UTC aufgelöstes Session-Fenster für einen Kalendertag.

    Die *Spezifikation* (Ortszeit + Zeitzone) liegt in ``refdata``. Dieses Objekt ist das
    Ergebnis der DST-sicheren Auflösung.
    """

    name: SessionName
    start: UtcDatetime
    end: UtcDatetime
    high: FiniteFloat | None = None
    low: FiniteFloat | None = None

    @model_validator(mode="after")
    def _check(self) -> SessionWindow:
        if self.end <= self.start:
            raise ValueError(f"Session-Ende {self.end} <= Start {self.start}")
        return self

    @property
    def available_time(self) -> datetime:
        return self.end

    def contains(self, ts: datetime) -> bool:
        ts = ensure_utc(ts)
        return self.start <= ts < self.end


# --------------------------------------------------------------------------------------------
# 4. Data Quality Status
# --------------------------------------------------------------------------------------------


class DataQualityIssue(BaseModel):
    """Ein einzelner Datenqualitätsbefund."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: DataQualityCode
    severity: DataQualitySeverity
    message: str
    at: UtcDatetime | None = None
    context: dict[str, str | int | float] = Field(default_factory=dict)


class DataQualityStatus(Record):
    """Ergebnis der Qualitätsprüfung einer Datenserie.

    ``blocks_trading`` ist die Schnittstelle zur späteren Strategy Engine: ist es ``True``,
    muss die Engine für dieses Instrument/Timeframe ``NO_TRADE`` erzwingen.
    """

    instrument: str
    kind: DataKind
    timeframe: Timeframe | None = None
    checked_at: UtcDatetime
    as_of: UtcDatetime | None = None
    bars_checked: int = Field(default=0, ge=0)
    issues: list[DataQualityIssue] = Field(default_factory=list)

    @property
    def available_time(self) -> datetime:
        return self.checked_at

    @property
    def worst_severity(self) -> DataQualitySeverity | None:
        if not self.issues:
            return None
        return max((i.severity for i in self.issues), key=lambda s: s.rank)

    @property
    def is_ok(self) -> bool:
        return not self.issues

    @property
    def blocks_trading(self) -> bool:
        return any(i.severity is DataQualitySeverity.CRITICAL for i in self.issues)

    def by_code(self, code: DataQualityCode) -> list[DataQualityIssue]:
        return [i for i in self.issues if i.code is code]


__all__ = [
    "OHLCV",
    "Candle",
    "DataQualityIssue",
    "DataQualityStatus",
    "FiniteFloat",
    "Funding",
    "HasAvailableTime",
    "MacroEvent",
    "NewsEvent",
    "NonNegFloat",
    "OpenInterest",
    "OrderbookSnapshot",
    "PositiveFloat",
    "Quote",
    "Record",
    "SessionWindow",
    "Trade",
    "UtcDatetime",
]
