"""Yahoo Finance Chart API — **keyless**, aber nur *indikativ / verzögert*.

``https://query1.finance.yahoo.com/v8/finance/chart/{symbol}`` — öffentlich, kein API-Key,
liefert OHLCV für FX (``EURUSD=X``), Metalle (``GC=F``) u. a.

**WOFÜR DIESE QUELLE TAUGT — UND WOFÜR NICHT.**

* ✅ Pipeline-Durchstich / Struktur-Validierung: „fließen Bars durch MarketContext → MTF →
  Decision?"
* ✅ grobe MTF-Historie zum Aufwärmen, wenn *nichts anderes* verfügbar ist.
* ❌ **Kein echtes Bid/Ask** — die API gibt keine Top-of-Book-Quote. Spread ist nicht bekannt.
* ❌ **~15 min verzögert**, konsolidierter Feed, nicht broker-genau.
* ❌ **nicht handelsqualitätstauglich** — niemals für echte Entry/Exit-Entscheidungen.

Jede erzeugte ``OHLCV`` trägt ``source="yahoo_indicative"`` und ``quote_volume=None``. Der
Adapter simuliert nichts: fehlt ein Feld, wird die Bar übersprungen und protokolliert.

Für produktive FX/Gold-Live-Daten: cTrader Open API oder OANDA v20 (siehe
``docs/GOLD-FX-DATA-SOURCES.md``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from trading_agent.core.clock import Clock, SystemClock
from trading_agent.core.enums import DataKind, Timeframe
from trading_agent.core.models import OHLCV
from trading_agent.core.time import bar_close_time, ensure_utc, is_aligned
from trading_agent.data.health import HealthTracker
from trading_agent.data.interfaces import AsyncOHLCVSource, ProviderStatus
from trading_agent.data.quality import sort_ohlcv
from trading_agent.net.client import HttpClient, NetError
from trading_agent.utils.logging import get_logger

_log = get_logger("yahoo_finance")

_INTERVAL: dict[Timeframe, str] = {
    Timeframe.M1: "1m",
    Timeframe.M5: "5m",
    Timeframe.M15: "15m",
    Timeframe.M30: "30m",
    Timeframe.H1: "60m",
    Timeframe.D1: "1d",
}

# kanonisch → Yahoo-Symbol
DEFAULT_SYMBOL_MAP: dict[str, str] = {
    "XAUUSD": "GC=F",
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "JPY=X",
    "AUDUSD": "AUDUSD=X",
    "USDCHF": "CHF=X",
}

_UA = "Mozilla/5.0 (compatible; trading-agent/0.1; research)"


class YahooFinanceError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class IndicativePrice:
    """Letzter *indikativer* Preis (kein Bid/Ask). Nur für Anzeige / Pipeline-Validierung."""

    instrument: str
    price: float
    ts: datetime
    source: str = "yahoo_indicative"


class YahooFinanceProvider(AsyncOHLCVSource):
    """Keyless OHLCV für FX/Gold — **indikativ, verzögert, nicht handelsqualitätstauglich**."""

    name = "yahoo_indicative"
    provides = frozenset({DataKind.OHLCV})

    def __init__(
        self,
        *,
        symbol_map: dict[str, str] | None = None,
        client: HttpClient | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._clock = clock or SystemClock()
        self.symbol_map = {**DEFAULT_SYMBOL_MAP, **(symbol_map or {})}
        self._client = client or HttpClient(
            "https://query1.finance.yahoo.com",
            name=self.name,
            rate_per_sec=2.0,
            transport=transport,
            headers={"User-Agent": _UA},
        )
        self._health = HealthTracker(self.name, clock=self._clock)

    def to_provider_symbol(self, canonical: str) -> str:
        return self.symbol_map.get(canonical.upper(), canonical.upper())

    def status(self) -> ProviderStatus:
        return self._health.status()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _chart(self, symbol: str, interval: str, *, range_: str = "5d") -> dict[str, Any]:
        params = {"interval": interval, "range": range_, "includePrePost": "false"}
        try:
            payload = await self._client.get_json(f"/v8/finance/chart/{symbol}", params)
        except Exception as exc:
            self._health.record_failure(str(exc))
            raise
        chart = payload.get("chart") if isinstance(payload, dict) else None
        if not isinstance(chart, dict):
            self._health.record_failure("yahoo: kein chart-Objekt")
            raise NetError("yahoo: unerwartete Antwort (kein chart)")
        if chart.get("error"):
            self._health.record_failure(str(chart["error"]))
            raise NetError(f"yahoo error: {chart['error']}")
        results = chart.get("result") or []
        if not results:
            self._health.record_failure("yahoo: leeres result")
            raise NetError(f"yahoo: kein result für {symbol}")
        return dict(results[0])

    async def fetch_ohlcv(
        self, instrument: str, timeframe: Timeframe, start: datetime, end: datetime
    ) -> list[OHLCV]:
        interval = _INTERVAL.get(timeframe)
        if interval is None:
            raise YahooFinanceError(f"yahoo: Timeframe {timeframe} nicht unterstützt")
        start, end = ensure_utc(start), ensure_utc(end)
        symbol = self.to_provider_symbol(instrument)
        span_days = max(1, (end - start).days + 2)
        range_ = _range_for(timeframe, span_days)
        result = await self._chart(symbol, interval, range_=range_)

        stamps = result.get("timestamp") or []
        quote_block = (result.get("indicators", {}).get("quote") or [{}])[0]
        opens = quote_block.get("open") or []
        highs = quote_block.get("high") or []
        lows = quote_block.get("low") or []
        closes = quote_block.get("close") or []
        vols = quote_block.get("volume") or []
        now = ensure_utc(self._clock.now())

        out: list[OHLCV] = []
        skipped = 0
        for i, ts_s in enumerate(stamps):
            open_time = datetime.fromtimestamp(int(ts_s), tz=UTC)
            # Yahoo-Intraday-Stamps sind i. d. R. an der Grenze; sonst abrunden.
            if timeframe is not Timeframe.D1 and not is_aligned(open_time, timeframe):
                open_time = _align_down(open_time, timeframe)
            close_time = bar_close_time(open_time, timeframe)
            if close_time > now:  # noch formende Bar
                continue
            if not (start <= open_time < end):
                continue
            try:
                o, h, low, c = (
                    float(opens[i]),
                    float(highs[i]),
                    float(lows[i]),
                    float(closes[i]),
                )
                v = float(vols[i]) if i < len(vols) and vols[i] is not None else 0.0
            except (TypeError, ValueError, IndexError):
                skipped += 1
                continue
            out.append(
                OHLCV(
                    instrument=instrument.upper(),
                    timeframe=timeframe,
                    open_time=open_time,
                    close_time=close_time,
                    open=o,
                    high=max(h, o, c),
                    low=min(low, o, c),
                    close=c,
                    volume=max(0.0, v),
                    quote_volume=None,
                    source="yahoo_indicative",
                    ingested_at=now,
                )
            )
        if skipped:
            _log.warning("yahoo: bars skipped (fehlende Felder)", extra={"n": skipped})
        self._health.record_success(latency_ms=1.0)
        return sort_ohlcv(out)

    async def latest_indicative(self, instrument: str) -> IndicativePrice:
        """Letzter *indikativer* Preis + Zeitstempel aus ``meta``. **Kein Bid/Ask.**"""
        symbol = self.to_provider_symbol(instrument)
        result = await self._chart(symbol, "5m", range_="1d")
        meta = result.get("meta") or {}
        px = meta.get("regularMarketPrice")
        t = meta.get("regularMarketTime")
        if px is None or t is None:
            self._health.record_failure("yahoo meta: kein regularMarketPrice")
            raise NetError(f"yahoo: kein indikativer Preis für {instrument}")
        self._health.record_success(latency_ms=1.0)
        return IndicativePrice(
            instrument=instrument.upper(),
            price=float(px),
            ts=datetime.fromtimestamp(int(t), tz=UTC),
        )


def _align_down(ts: datetime, tf: Timeframe) -> datetime:
    epoch = int(ts.timestamp())
    return datetime.fromtimestamp(epoch - (epoch % tf.seconds), tz=UTC)


def _range_for(tf: Timeframe, span_days: int) -> str:
    if tf is Timeframe.M1:
        return "7d"  # Yahoo begrenzt 1m-Historie hart auf 7 Tage
    if tf in (Timeframe.M5, Timeframe.M15, Timeframe.M30):
        return "1mo" if span_days <= 30 else "60d"
    if tf is Timeframe.H1:
        return "3mo" if span_days <= 90 else "730d"
    return "2y" if span_days <= 730 else "10y"


__all__ = [
    "DEFAULT_SYMBOL_MAP",
    "IndicativePrice",
    "YahooFinanceError",
    "YahooFinanceProvider",
]
