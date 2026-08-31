"""Binance public market-data adapter (REST) — **kein API-Key**, read-only.

Zwei Märkte über ``market``:

* ``"spot"``         → ``https://api.binance.com``  (``/api/v3/*``)
* ``"futures_usdm"`` → ``https://fapi.binance.com`` (``/fapi/v1/*``) — Perpetuals, Mark Price,
  Funding, Open Interest.

Ergänzt den bereits vorhandenen ``binance_vision``-Adapter (das ist der **Bulk-Datei-Import**
für tiefe Historie); dieser hier ist der **Live-/REST-API**-Adapter (aktuelle Ticker, Bid/Ask,
Klines, Mark Price, Funding, OI, WebSocket-Symbolauflösung).

**Gold:** Binance listet ``XAUUSDT`` **nur auf USD-M-Futures** (`TRADIFI_PERPETUAL`), nicht Spot.
Spot hat stattdessen ``PAXGUSDT`` (PAX Gold). Für XAUUSDT ⇒ ``market="futures_usdm"``.

Kline-Zeile: ``[openTime, open, high, low, close, volume, closeTime, quoteVolume, trades,
takerBuyBase, takerBuyQuote, ignore]`` — Epoch **ms**, UTC. ``close_time`` wird auf die
Projekt-Konvention ``open_time + timeframe`` gesetzt.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from trading_agent.core.clock import Clock, SystemClock
from trading_agent.core.enums import DataKind, Timeframe
from trading_agent.core.models import OHLCV, Funding, OpenInterest, Quote
from trading_agent.core.time import bar_close_time, ensure_utc, parse_timestamp, to_epoch_ms
from trading_agent.data.health import HealthTracker
from trading_agent.data.interfaces import (
    AsyncFundingSource,
    AsyncOHLCVSource,
    AsyncOpenInterestSource,
    AsyncQuoteSource,
    ProviderStatus,
)
from trading_agent.data.quality import sort_ohlcv
from trading_agent.net.client import HttpClient, NetError

_SPOT_BASE = "https://api.binance.com"
_FUTURES_USDM_BASE = "https://fapi.binance.com"

_INTERVAL: dict[Timeframe, str] = {
    Timeframe.M1: "1m",
    Timeframe.M5: "5m",
    Timeframe.M15: "15m",
    Timeframe.M30: "30m",
    Timeframe.H1: "1h",
    Timeframe.H4: "4h",
    Timeframe.D1: "1d",
    Timeframe.W1: "1w",
}
_OI_PERIOD: dict[Timeframe, str] = {
    Timeframe.M5: "5m",
    Timeframe.M15: "15m",
    Timeframe.M30: "30m",
    Timeframe.H1: "1h",
    Timeframe.H4: "4h",
    Timeframe.D1: "1d",
}


class BinancePublicDataProvider(
    AsyncOHLCVSource, AsyncQuoteSource, AsyncFundingSource, AsyncOpenInterestSource
):
    name = "binance"
    provides = frozenset({DataKind.OHLCV, DataKind.QUOTE, DataKind.FUNDING, DataKind.OPEN_INTEREST})

    def __init__(
        self,
        *,
        market: str = "futures_usdm",
        client: HttpClient | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Clock | None = None,
    ) -> None:
        if market not in ("spot", "futures_usdm"):
            raise ValueError(f"binance: unbekannter market {market!r}")
        self._clock = clock or SystemClock()
        self.market = market
        self._futures = market == "futures_usdm"
        base = _FUTURES_USDM_BASE if self._futures else _SPOT_BASE
        self._prefix = "/fapi/v1" if self._futures else "/api/v3"
        self._client = client or HttpClient(
            base, name=f"{self.name}_{market}", rate_per_sec=10.0, transport=transport
        )
        self._health = HealthTracker(self.name, clock=self._clock)

    def status(self) -> ProviderStatus:
        return self._health.status()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        try:
            return await self._client.get_json(path, params or {})
        except Exception as exc:
            self._health.record_failure(str(exc))
            raise

    def _require_futures(self, what: str) -> None:
        if not self._futures:
            raise NetError(f"binance {what}: nur auf USD-M-Futures (market='futures_usdm')")

    # ---- Zeit / Symbole ---------------------------------------------------
    async def server_time(self) -> tuple[datetime, float]:
        payload = await self._get(f"{self._prefix}/time")
        server = parse_timestamp(int(payload["serverTime"]))
        skew = (server - ensure_utc(self._clock.now())).total_seconds()
        self._health.record_success(latency_ms=1.0)
        return server, skew

    async def list_symbols(self, *, quote: str | None = None) -> list[str]:
        payload = await self._get(f"{self._prefix}/exchangeInfo")
        out: list[str] = []
        for s in payload.get("symbols", []):
            if s.get("status") not in ("TRADING", None):
                continue
            if quote and s.get("quoteAsset") != quote.upper():
                continue
            out.append(str(s["symbol"]).upper())
        self._health.record_success(latency_ms=1.0)
        return sorted(out)

    async def has_symbol(self, instrument: str) -> bool:
        return instrument.upper() in set(await self.list_symbols())

    # ---- OHLCV ----------------------------------------------------------
    async def fetch_ohlcv(
        self, instrument: str, timeframe: Timeframe, start: datetime, end: datetime
    ) -> list[OHLCV]:
        interval = _INTERVAL.get(timeframe)
        if interval is None:
            raise ValueError(f"binance: unsupported timeframe {timeframe}")
        start, end = ensure_utc(start), ensure_utc(end)
        params = {
            "symbol": instrument.upper(),
            "interval": interval,
            "startTime": to_epoch_ms(start),
            "endTime": to_epoch_ms(end) - 1,
            "limit": 1500 if self._futures else 1000,
        }
        rows = await self._get(f"{self._prefix}/klines", params)
        out: list[OHLCV] = []
        for r in rows:
            open_time = parse_timestamp(int(r[0]))
            close_time = bar_close_time(open_time, timeframe)
            if not (start <= open_time < end) or close_time > end:
                continue
            o, h, low, c = float(r[1]), float(r[2]), float(r[3]), float(r[4])
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
                    volume=float(r[5]),
                    quote_volume=float(r[7]) if len(r) > 7 else None,
                    trades=int(r[8]) if len(r) > 8 else None,
                    source=self.name,
                    ingested_at=self._clock.now(),
                )
            )
        self._health.record_success(latency_ms=1.0)
        return sort_ohlcv(out)

    # ---- Quote / Ticker ----------------------------------------------
    async def fetch_quote(self, instrument: str) -> Quote:
        """Bester Bid/Ask über ``ticker/bookTicker``."""
        payload = await self._get(
            f"{self._prefix}/ticker/bookTicker", {"symbol": instrument.upper()}
        )
        if "bidPrice" not in payload:
            self._health.record_failure("binance bookTicker: kein bidPrice")
            raise NetError(f"binance: kein Bid/Ask für {instrument}")
        ts_ms = payload.get("time")
        ts = parse_timestamp(int(ts_ms)) if ts_ms else ensure_utc(self._clock.now())
        self._health.record_success(latency_ms=1.0)
        return Quote(
            instrument=instrument.upper(),
            ts=ts,
            bid=float(payload["bidPrice"]),
            ask=float(payload["askPrice"]),
            bid_size=float(payload["bidQty"]) if payload.get("bidQty") else None,
            ask_size=float(payload["askQty"]) if payload.get("askQty") else None,
            source=self.name,
            ingested_at=ensure_utc(self._clock.now()),
        )

    async def fetch_ticker_24h(self, instrument: str) -> dict[str, Any]:
        payload = await self._get(f"{self._prefix}/ticker/24hr", {"symbol": instrument.upper()})
        self._health.record_success(latency_ms=1.0)
        return {
            "last": float(payload["lastPrice"]),
            "high": float(payload["highPrice"]),
            "low": float(payload["lowPrice"]),
            "volume": float(payload["volume"]),
            "quote_volume": float(payload["quoteVolume"]),
            "price_change_pct": float(payload["priceChangePercent"]),
        }

    # ---- Futures-only: Mark Price / Funding / OI ---------------------
    async def fetch_mark_price(self, instrument: str) -> dict[str, Any]:
        self._require_futures("mark price")
        payload = await self._get("/fapi/v1/premiumIndex", {"symbol": instrument.upper()})
        self._health.record_success(latency_ms=1.0)
        return {
            "mark_price": float(payload["markPrice"]),
            "index_price": float(payload["indexPrice"]) if payload.get("indexPrice") else None,
            "last_funding_rate": float(payload["lastFundingRate"])
            if payload.get("lastFundingRate") not in (None, "")
            else None,
            "next_funding_time": parse_timestamp(int(payload["nextFundingTime"]))
            if payload.get("nextFundingTime")
            else None,
            "ts": parse_timestamp(int(payload["time"])) if payload.get("time") else None,
        }

    async def fetch_funding(self, instrument: str, start: datetime, end: datetime) -> list[Funding]:
        self._require_futures("funding")
        start, end = ensure_utc(start), ensure_utc(end)
        rows = await self._get(
            "/fapi/v1/fundingRate",
            {
                "symbol": instrument.upper(),
                "startTime": to_epoch_ms(start),
                "endTime": to_epoch_ms(end) - 1,
                "limit": 1000,
            },
        )
        out: list[Funding] = []
        for row in rows:
            ts = parse_timestamp(int(row["fundingTime"]))
            if not (start <= ts < end):
                continue
            out.append(
                Funding(
                    instrument=instrument.upper(),
                    ts=ts,
                    rate=float(row["fundingRate"]),
                    interval_hours=8.0,
                    source=self.name,
                    ingested_at=self._clock.now(),
                )
            )
        self._health.record_success(latency_ms=1.0)
        return sorted(out, key=lambda f: f.ts)

    async def fetch_open_interest(
        self, instrument: str, start: datetime, end: datetime
    ) -> list[OpenInterest]:
        self._require_futures("open interest")
        start, end = ensure_utc(start), ensure_utc(end)
        rows = await self._get(
            "/futures/data/openInterestHist",
            {
                "symbol": instrument.upper(),
                "period": _OI_PERIOD.get(Timeframe.H1, "1h"),
                "startTime": to_epoch_ms(start),
                "endTime": to_epoch_ms(end) - 1,
                "limit": 500,
            },
        )
        out: list[OpenInterest] = []
        for row in rows:
            ts = parse_timestamp(int(row["timestamp"]))
            if not (start <= ts < end):
                continue
            out.append(
                OpenInterest(
                    instrument=instrument.upper(),
                    ts=ts,
                    oi=float(row["sumOpenInterest"]),
                    source=self.name,
                    ingested_at=self._clock.now(),
                )
            )
        self._health.record_success(latency_ms=1.0)
        return sorted(out, key=lambda o: o.ts)


__all__ = ["BinancePublicDataProvider"]
