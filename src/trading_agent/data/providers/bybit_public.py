"""Bybit v5 public market data adapter (REST).

Secondary crypto source / fallback / richer funding + OI (per user decision 2026-08-28).
Public endpoints, **no API key**.

Endpoints used:
* ``/v5/market/kline``            -> candles   (row: [startMs, open, high, low, close, volume, turnover])
* ``/v5/market/funding/history``  -> funding   ({symbol, fundingRate, fundingRateTimestamp})
* ``/v5/market/open-interest``    -> OI        ({openInterest, timestamp})

Bybit ``list`` is newest-first — we reverse it.
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

_INTERVAL: dict[Timeframe, str] = {
    Timeframe.M1: "1",
    Timeframe.M5: "5",
    Timeframe.M15: "15",
    Timeframe.M30: "30",
    Timeframe.H1: "60",
    Timeframe.H4: "240",
    Timeframe.D1: "D",
    Timeframe.W1: "W",
}
_OI_INTERVAL: dict[Timeframe, str] = {
    Timeframe.M5: "5min",
    Timeframe.M15: "15min",
    Timeframe.M30: "30min",
    Timeframe.H1: "1h",
    Timeframe.H4: "4h",
    Timeframe.D1: "1d",
}


class BybitPublicDataProvider(
    AsyncOHLCVSource, AsyncFundingSource, AsyncOpenInterestSource, AsyncQuoteSource
):
    name = "bybit_public"
    provides = frozenset({DataKind.OHLCV, DataKind.FUNDING, DataKind.OPEN_INTEREST, DataKind.QUOTE})

    def __init__(
        self,
        *,
        category: str = "linear",
        client: HttpClient | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._clock = clock or SystemClock()
        self._category = category
        self._client = client or HttpClient(
            "https://api.bybit.com",
            name=self.name,
            rate_per_sec=5.0,
            transport=transport,
        )
        self._health = HealthTracker(self.name, clock=self._clock)

    def status(self) -> ProviderStatus:
        return self._health.status()

    async def aclose(self) -> None:
        await self._client.aclose()

    @staticmethod
    def _unwrap(payload: dict[str, Any]) -> dict[str, Any]:
        if payload.get("retCode") not in (0, None):
            raise NetError(f"bybit retCode={payload.get('retCode')}: {payload.get('retMsg')}")
        result: dict[str, Any] = payload.get("result", {})
        return result

    async def fetch_ohlcv(
        self, instrument: str, timeframe: Timeframe, start: datetime, end: datetime
    ) -> list[OHLCV]:
        if timeframe not in _INTERVAL:
            raise ValueError(f"bybit: unsupported timeframe {timeframe}")
        start = ensure_utc(start)
        end = ensure_utc(end)
        params = {
            "category": self._category,
            "symbol": instrument.upper(),
            "interval": _INTERVAL[timeframe],
            "start": to_epoch_ms(start),
            "end": to_epoch_ms(end) - 1,
            "limit": 1000,
        }
        try:
            result = self._unwrap(await self._client.get_json("/v5/market/kline", params))
        except Exception as exc:
            self._health.record_failure(str(exc))
            raise
        rows: list[Any] = list(reversed(result.get("list", [])))  # bybit newest-first
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
                    quote_volume=float(r[6]) if len(r) > 6 else None,
                    source=self.name,
                    ingested_at=self._clock.now(),
                )
            )
        self._health.record_success(latency_ms=1.0)
        return sort_ohlcv(out)

    async def fetch_quote(self, instrument: str) -> Quote:
        """Bester Bid/Ask über ``/v5/market/tickers`` (``bid1Price`` / ``ask1Price``).
        Zeitstempel = ``time`` aus der Antwort (ms, Server). Keine Zukunftsdaten."""
        params = {"category": self._category, "symbol": instrument.upper()}
        try:
            payload = await self._client.get_json("/v5/market/tickers", params)
            result = self._unwrap(payload)
        except Exception as exc:
            self._health.record_failure(str(exc))
            raise
        rows = result.get("list", [])
        if not rows or "bid1Price" not in rows[0]:
            self._health.record_failure("bybit tickers: kein bid1Price")
            raise NetError(f"bybit tickers ohne Bid/Ask für {instrument}")
        row = rows[0]
        ts_ms = payload.get("time") or result.get("ts")
        ts = parse_timestamp(int(ts_ms)) if ts_ms else ensure_utc(self._clock.now())
        self._health.record_success(latency_ms=1.0)
        return Quote(
            instrument=instrument.upper(),
            ts=ts,
            bid=float(row["bid1Price"]),
            ask=float(row["ask1Price"]),
            bid_size=float(row["bid1Size"]) if row.get("bid1Size") else None,
            ask_size=float(row["ask1Size"]) if row.get("ask1Size") else None,
            source=self.name,
            ingested_at=ensure_utc(self._clock.now()),
        )

    # ---- Universum: Symbolliste und 24-h-Ticker ----------------------
    #
    # Damit kann Bybit die Quelle des Krypto-Universums sein. Es ist die bevorzugte:
    # mehr Paare als Kraken und 0,10 % statt 0,40 % Gebuehr. Bybit sperrt die API
    # allerdings aus mehreren Laendern per CloudFront (HTTP 403) — deshalb wird es im
    # Scan versucht und nicht vorausgesetzt, mit Kraken als Rueckfallebene.

    async def list_symbol_info(self, *, quote: str | None = None) -> list[dict[str, Any]]:
        """Handelbare Spot-Paare mit Basis und Quote.

        Bybit blaettert ueber ``nextPageCursor``; ohne die Schleife kaemen nur die ersten
        1000 Symbole — was heute reicht und morgen still abschneidet.
        """
        aus: list[dict[str, Any]] = []
        cursor: str | None = None
        for _ in range(10):  # harte Grenze gegen eine Schleife ohne Ende
            params: dict[str, Any] = {"category": "spot", "limit": 1000}
            if cursor:
                params["cursor"] = cursor
            try:
                result = self._unwrap(
                    await self._client.get_json("/v5/market/instruments-info", params)
                )
            except Exception as exc:
                self._health.record_failure(str(exc))
                raise
            for row in result.get("list", []):
                if str(row.get("status") or "Trading") != "Trading":
                    continue
                q = str(row.get("quoteCoin") or "").upper()
                if quote and q != quote.upper():
                    continue
                aus.append(
                    {
                        "instrument": str(row.get("symbol") or "").upper(),
                        "basis": str(row.get("baseCoin") or "").upper(),
                        "quote": q,
                        "spot": True,
                    }
                )
            cursor = result.get("nextPageCursor") or None
            if not cursor:
                break
        self._health.record_success(latency_ms=1.0)
        return aus

    async def fetch_ticker_24h_all(self) -> list[dict[str, Any]]:
        """Alle Spot-Ticker in einem Aufruf.

        **Was Bybit nicht liefert: die Zahl der Abschluesse.** Binance gab ``count`` mit,
        und der Universumsfilter hat damit „viel Umsatz aus wenigen Grossorders"
        aussortiert. Diese Pruefung faellt hier weg — sie laesst sich nicht erfinden.
        Ausgeglichen wird das ueber eine hoehere Umsatzschwelle im Aufrufer; ``trades``
        bleibt 0, damit nirgends eine Zahl steht, die es nicht gibt.
        """
        try:
            result = self._unwrap(
                await self._client.get_json("/v5/market/tickers", {"category": "spot"})
            )
        except Exception as exc:
            self._health.record_failure(str(exc))
            raise
        aus: list[dict[str, Any]] = []
        for row in result.get("list", []):
            try:
                aus.append(
                    {
                        "instrument": str(row["symbol"]).upper(),
                        "last": float(row["lastPrice"]),
                        "high": float(row["highPrice24h"]),
                        "low": float(row["lowPrice24h"]),
                        "quote_volume": float(row["turnover24h"]),
                        # Bybit gibt den Tagesertrag als Anteil (0,0123), nicht in Prozent.
                        "price_change_pct": float(row.get("price24hPcnt") or 0.0) * 100.0,
                        "trades": 0,
                    }
                )
            except (KeyError, TypeError, ValueError):
                continue
        self._health.record_success(latency_ms=1.0)
        return aus

    async def fetch_funding(self, instrument: str, start: datetime, end: datetime) -> list[Funding]:
        start = ensure_utc(start)
        end = ensure_utc(end)
        params = {
            "category": self._category,
            "symbol": instrument.upper(),
            "startTime": to_epoch_ms(start),
            "endTime": to_epoch_ms(end) - 1,
            "limit": 200,
        }
        try:
            result = self._unwrap(await self._client.get_json("/v5/market/funding/history", params))
        except Exception as exc:
            self._health.record_failure(str(exc))
            raise
        out: list[Funding] = []
        for row in result.get("list", []):
            ts = parse_timestamp(int(row["fundingRateTimestamp"]))
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
        self._health.record_success()
        return sorted(out, key=lambda f: f.ts)

    async def fetch_open_interest(
        self, instrument: str, start: datetime, end: datetime
    ) -> list[OpenInterest]:
        start = ensure_utc(start)
        end = ensure_utc(end)
        params = {
            "category": self._category,
            "symbol": instrument.upper(),
            "intervalTime": _OI_INTERVAL.get(Timeframe.H1, "1h"),
            "startTime": to_epoch_ms(start),
            "endTime": to_epoch_ms(end) - 1,
            "limit": 200,
        }
        try:
            result = self._unwrap(await self._client.get_json("/v5/market/open-interest", params))
        except Exception as exc:
            self._health.record_failure(str(exc))
            raise
        out: list[OpenInterest] = []
        for row in result.get("list", []):
            ts = parse_timestamp(int(row["timestamp"]))
            if not (start <= ts < end):
                continue
            out.append(
                OpenInterest(
                    instrument=instrument.upper(),
                    ts=ts,
                    oi=float(row["openInterest"]),
                    source=self.name,
                    ingested_at=self._clock.now(),
                )
            )
        self._health.record_success()
        return sorted(out, key=lambda o: o.ts)


__all__ = ["BybitPublicDataProvider"]
