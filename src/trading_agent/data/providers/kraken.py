"""Kraken Pro public market data adapter (REST).

Primary crypto data source (per user decision 2026-08-28). Public endpoints, **no API key**.

Endpoints used:
* ``/0/public/OHLC``   -> confirmed candles  (row: [time, open, high, low, close, vwap, volume, count])
* ``/0/public/Trades`` -> public trades      (row: [price, volume, time, side, ord_type, misc, id])

Kraken pair naming is irregular (``XXBTZUSD`` for BTC/USD spot). We map canonical symbols and,
on the way back, take the single non-``last`` key from ``result``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from trading_agent.core.clock import Clock, SystemClock
from trading_agent.core.enums import DataKind, Side, Timeframe
from trading_agent.core.models import OHLCV, Quote, Trade
from trading_agent.core.time import bar_close_time, ensure_utc, parse_timestamp
from trading_agent.data.health import HealthTracker
from trading_agent.data.interfaces import (
    AsyncOHLCVSource,
    AsyncQuoteSource,
    AsyncTradeSource,
    ProviderStatus,
)
from trading_agent.data.quality import sort_ohlcv
from trading_agent.net.client import HttpClient, NetError

_INTERVAL_MIN: dict[Timeframe, int] = {
    Timeframe.M1: 1,
    Timeframe.M5: 5,
    Timeframe.M15: 15,
    Timeframe.M30: 30,
    Timeframe.H1: 60,
    Timeframe.H4: 240,
    Timeframe.D1: 1440,
    Timeframe.W1: 10080,
}

# canonical symbol -> Kraken pair
_PAIR: dict[str, str] = {
    "BTCUSDT": "XBTUSDT",
    "ETHUSDT": "ETHUSDT",
    "SOLUSDT": "SOLUSDT",
    "BTCUSD": "XXBTZUSD",
    "ETHUSD": "XETHZUSD",
}


class KrakenDataProvider(AsyncOHLCVSource, AsyncTradeSource, AsyncQuoteSource):
    name = "kraken"
    provides = frozenset({DataKind.OHLCV, DataKind.TRADE, DataKind.QUOTE})

    def __init__(
        self,
        *,
        client: HttpClient | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._clock = clock or SystemClock()
        self._client = client or HttpClient(
            "https://api.kraken.com",
            name=self.name,
            rate_per_sec=1.0,
            transport=transport,
        )
        self._health = HealthTracker(self.name, clock=self._clock)

    def status(self) -> ProviderStatus:
        return self._health.status()

    async def aclose(self) -> None:
        await self._client.aclose()

    def _pair(self, instrument: str) -> str:
        return _PAIR.get(instrument.upper(), instrument.upper())

    @staticmethod
    def _unwrap(payload: dict[str, Any]) -> dict[str, Any]:
        errors = payload.get("error") or []
        if errors:
            raise NetError(f"kraken error: {errors}")
        result: dict[str, Any] = payload.get("result", {})
        return result

    async def fetch_ohlcv(
        self, instrument: str, timeframe: Timeframe, start: datetime, end: datetime
    ) -> list[OHLCV]:
        if timeframe not in _INTERVAL_MIN:
            raise ValueError(f"kraken: unsupported timeframe {timeframe}")
        start = ensure_utc(start)
        end = ensure_utc(end)
        params = {
            "pair": self._pair(instrument),
            "interval": _INTERVAL_MIN[timeframe],
            "since": int(start.timestamp()) - 1,
        }
        try:
            result = self._unwrap(await self._client.get_json("/0/public/OHLC", params))
        except Exception as exc:
            self._health.record_failure(str(exc))
            raise
        rows: list[Any] = next((v for k, v in result.items() if k != "last"), [])
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
                    volume=float(r[6]),
                    quote_volume=float(r[5]) * float(r[6]) if len(r) > 5 else None,
                    trades=int(r[7]) if len(r) > 7 else None,
                    source=self.name,
                    ingested_at=self._clock.now(),
                )
            )
        self._health.record_success(latency_ms=1.0)
        return sort_ohlcv(out)

    async def fetch_quote(self, instrument: str) -> Quote:
        """Bester Bid/Ask über ``/0/public/Ticker``. Kraken liefert keinen Zeitstempel im
        Ticker ⇒ ``ts`` = Empfangszeit (``clock.now()``). Keine Zukunftsdaten möglich."""
        params = {"pair": self._pair(instrument)}
        try:
            result = self._unwrap(await self._client.get_json("/0/public/Ticker", params))
        except Exception as exc:
            self._health.record_failure(str(exc))
            raise
        row = next(iter(result.values()), None)
        if not row or "a" not in row or "b" not in row:
            self._health.record_failure("kraken ticker: kein a/b im Ergebnis")
            raise NetError(f"kraken ticker ohne Bid/Ask für {instrument}")
        now = ensure_utc(self._clock.now())
        self._health.record_success(latency_ms=1.0)
        return Quote(
            instrument=instrument.upper(),
            ts=now,
            bid=float(row["b"][0]),
            ask=float(row["a"][0]),
            bid_size=float(row["b"][2]) if len(row["b"]) > 2 else None,
            ask_size=float(row["a"][2]) if len(row["a"]) > 2 else None,
            source=self.name,
            ingested_at=now,
        )

    async def fetch_trades(self, instrument: str, start: datetime, end: datetime) -> list[Trade]:
        start = ensure_utc(start)
        end = ensure_utc(end)
        params = {"pair": self._pair(instrument), "since": int(start.timestamp() * 1_000_000_000)}
        try:
            result = self._unwrap(await self._client.get_json("/0/public/Trades", params))
        except Exception as exc:
            self._health.record_failure(str(exc))
            raise
        rows: list[Any] = next((v for k, v in result.items() if k != "last"), [])
        out: list[Trade] = []
        for i, r in enumerate(rows):
            ts = parse_timestamp(float(r[2]))
            if not (start <= ts < end):
                continue
            out.append(
                Trade(
                    instrument=instrument.upper(),
                    ts=ts,
                    price=float(r[0]),
                    size=float(r[1]),
                    side=Side.BUY if r[3] == "b" else Side.SELL,
                    trade_id=str(r[6]) if len(r) > 6 else f"{instrument}-{i}",
                    source=self.name,
                    ingested_at=self._clock.now(),
                )
            )
        self._health.record_success()
        return out


__all__ = ["KrakenDataProvider"]
