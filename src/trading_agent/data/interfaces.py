"""Provider-Schnittstellen (ABCs) für Marktdaten.

Alle Provider sind **read-only Marktdaten** – **keine** privaten Account-APIs, **keine**
API-Keys, **keine** Orderfunktionen (das kommt frühestens in Phase 9, und dann strikt getrennt).

Jeder Provider MUSS ``status()`` liefern und darin einen der drei Zustände
``HEALTHY`` / ``DEGRADED`` / ``UNAVAILABLE`` melden.

Fähigkeiten sind in einzelne ABCs aufgeteilt; ein konkreter Provider implementiert nur die,
die er anbietet (z. B. Mock: OHLCV/Quote/Trade/Funding/OI; CSV: OHLCV/News/Macro).
"""

from __future__ import annotations

import abc
from collections.abc import Iterator
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from trading_agent.core.enums import DataKind, ProviderHealth, Timeframe
from trading_agent.core.models import (
    OHLCV,
    Funding,
    MacroEvent,
    NewsEvent,
    OpenInterest,
    OrderbookSnapshot,
    Quote,
    Trade,
    UtcDatetime,
)


class ProviderStatus(BaseModel):
    """Gesundheitszustand eines Providers zu einem Zeitpunkt."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str
    health: ProviderHealth
    checked_at: UtcDatetime
    detail: str = ""
    last_success_at: UtcDatetime | None = None
    error_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    latency_ms_p50: float | None = None
    consecutive_failures: int = Field(default=0, ge=0)

    @property
    def is_usable(self) -> bool:
        """DEGRADED ist nutzbar (mit Vorsicht), UNAVAILABLE nicht."""
        return self.health is not ProviderHealth.UNAVAILABLE


class MarketDataProvider(abc.ABC):
    """Basisklasse aller Marktdaten-Provider."""

    #: kurzer, stabiler Bezeichner der Quelle, z. B. "synthetic", "csv", "bybit"
    name: str = "abstract"

    #: welche Datenarten dieser Provider liefert
    provides: frozenset[DataKind] = frozenset()

    @abc.abstractmethod
    def status(self) -> ProviderStatus:
        """Aktueller Gesundheitszustand."""


class HistoricalOHLCVProvider(MarketDataProvider):
    @abc.abstractmethod
    def get_ohlcv(
        self,
        instrument: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> list[OHLCV]:
        """Abgeschlossene Kerzen mit ``start <= open_time < end`` (aufsteigend, dedupliziert)."""


class LiveOHLCVProvider(MarketDataProvider):
    @abc.abstractmethod
    def stream_ohlcv(self, instrument: str, timeframe: Timeframe) -> Iterator[OHLCV]:
        """Liefert fortlaufend **abgeschlossene** Kerzen (nur ``is_final``-Bars)."""


class HistoricalQuoteProvider(MarketDataProvider):
    @abc.abstractmethod
    def get_quotes(self, instrument: str, start: datetime, end: datetime) -> list[Quote]: ...


class LiveQuoteProvider(MarketDataProvider):
    @abc.abstractmethod
    def stream_quotes(self, instrument: str) -> Iterator[Quote]: ...


class HistoricalTradeProvider(MarketDataProvider):
    @abc.abstractmethod
    def get_trades(self, instrument: str, start: datetime, end: datetime) -> list[Trade]: ...


class OrderbookProvider(MarketDataProvider):
    @abc.abstractmethod
    def get_orderbook(self, instrument: str, at: datetime) -> OrderbookSnapshot | None: ...


class FundingProvider(MarketDataProvider):
    @abc.abstractmethod
    def get_funding(self, instrument: str, start: datetime, end: datetime) -> list[Funding]: ...


class OpenInterestProvider(MarketDataProvider):
    @abc.abstractmethod
    def get_open_interest(
        self, instrument: str, start: datetime, end: datetime
    ) -> list[OpenInterest]: ...


class NewsProvider(MarketDataProvider):
    @abc.abstractmethod
    def get_news(
        self,
        start: datetime,
        end: datetime,
        *,
        as_of: datetime | None = None,
        symbols: list[str] | None = None,
    ) -> list[NewsEvent]:
        """Nachrichten mit ``start <= scheduled_time < end``.

        ``as_of`` erzwingt Point-in-Time: nur Einträge mit ``available_time <= as_of``.
        """


class MacroProvider(MarketDataProvider):
    @abc.abstractmethod
    def get_macro(
        self,
        series_ids: list[str],
        start: datetime,
        end: datetime,
        *,
        as_of: datetime | None = None,
    ) -> list[MacroEvent]:
        """Makro-Zeitreihenwerte. ``as_of`` erzwingt Point-in-Time (nur Erstveröffentlichungen
        bzw. Revisionen, die zum Zeitpunkt ``as_of`` bereits bekannt waren)."""


# --------------------------------------------------------------------------------------------
# Async network providers (REST). Distinct from the sync ABCs above, which are for local
# in-process sources (mock, csv). Network adapters go through net/HttpClient and are async.
# --------------------------------------------------------------------------------------------


class AsyncMarketDataSource(abc.ABC):
    """Base for network-backed market data providers (Kraken, Bybit, ...)."""

    name: str = "async-abstract"
    provides: frozenset[DataKind] = frozenset()

    @abc.abstractmethod
    def status(self) -> ProviderStatus:
        """Current health."""

    async def aclose(self) -> None:  # pragma: no cover - default no-op
        return None


class AsyncOHLCVSource(AsyncMarketDataSource):
    @abc.abstractmethod
    async def fetch_ohlcv(
        self, instrument: str, timeframe: Timeframe, start: datetime, end: datetime
    ) -> list[OHLCV]:
        """Confirmed bars with ``start <= open_time < end`` (ascending, deduplicated)."""


class AsyncTradeSource(AsyncMarketDataSource):
    @abc.abstractmethod
    async def fetch_trades(
        self, instrument: str, start: datetime, end: datetime
    ) -> list[Trade]: ...


class AsyncQuoteSource(AsyncMarketDataSource):
    @abc.abstractmethod
    async def fetch_quote(self, instrument: str) -> Quote:
        """Aktueller bester Bid/Ask (Top-of-Book). Kein historischer Bereich — nur „jetzt"."""


class AsyncFundingSource(AsyncMarketDataSource):
    @abc.abstractmethod
    async def fetch_funding(
        self, instrument: str, start: datetime, end: datetime
    ) -> list[Funding]: ...


class AsyncOpenInterestSource(AsyncMarketDataSource):
    @abc.abstractmethod
    async def fetch_open_interest(
        self, instrument: str, start: datetime, end: datetime
    ) -> list[OpenInterest]: ...


__all__ = [
    "AsyncFundingSource",
    "AsyncMarketDataSource",
    "AsyncOHLCVSource",
    "AsyncOpenInterestSource",
    "AsyncQuoteSource",
    "AsyncTradeSource",
    "FundingProvider",
    "HistoricalOHLCVProvider",
    "HistoricalQuoteProvider",
    "HistoricalTradeProvider",
    "LiveOHLCVProvider",
    "LiveQuoteProvider",
    "MacroProvider",
    "MarketDataProvider",
    "NewsProvider",
    "OpenInterestProvider",
    "OrderbookProvider",
    "ProviderStatus",
]
