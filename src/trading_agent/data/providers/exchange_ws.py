"""WebSocket market-data sources for Kraken and Bybit (Phase 2B).

Both implement the ``LiveSource`` interface (``stream()`` yields confirmed ``OHLCV`` bars via a
``BarAggregator``). The connection is injectable so tests run without a network:

    async with connect_fn(url) as conn:
        await conn.send(subscribe_json)
        async for message in conn:
            ...

Reconnect with capped exponential backoff; on reconnect the caller (ingestion) backfills gaps
via the REST provider.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator, Callable
from typing import Any, Protocol

from trading_agent.core.clock import Clock, SystemClock
from trading_agent.core.enums import Side, Timeframe
from trading_agent.core.models import OHLCV, Trade
from trading_agent.core.time import parse_timestamp
from trading_agent.data.aggregator import BarAggregator

_log = logging.getLogger("trading_agent.data.ws")

ConnectFn = Callable[[str], "AsyncConn"]


class AsyncConn(Protocol):  # structural type for an async WS connection
    async def __aenter__(self) -> AsyncConn: ...  # pragma: no cover
    async def __aexit__(self, *exc: object) -> None: ...  # pragma: no cover
    async def send(self, data: str) -> None: ...  # pragma: no cover
    def __aiter__(self) -> AsyncIterator[str]: ...  # pragma: no cover


class _WSBase:
    url = ""
    name = "ws"

    def __init__(
        self,
        instruments: list[str],
        timeframe: Timeframe = Timeframe.M1,
        *,
        connect_fn: ConnectFn | None = None,
        clock: Clock | None = None,
        max_reconnects: int = 5,
        backoff_base_s: float = 0.5,
    ) -> None:
        self.instruments = [s.upper() for s in instruments]
        self.timeframe = timeframe
        self._clock = clock or SystemClock()
        self._connect = connect_fn or self._default_connect
        self._max_reconnects = max_reconnects
        self._backoff = backoff_base_s
        self._agg = {
            s: BarAggregator(s, timeframe, source=self.name, clock=self._clock)
            for s in self.instruments
        }
        self._stopped = False
        self.reconnects = 0
        self.messages_seen = 0

    @staticmethod
    def _default_connect(url: str) -> AsyncConn:  # pragma: no cover - needs network
        import websockets

        return websockets.connect(url)  # type: ignore[return-value]

    def _subscribe_payload(self) -> str:  # pragma: no cover - overridden
        raise NotImplementedError

    def _parse(self, message: str) -> list[Trade]:  # pragma: no cover - overridden
        raise NotImplementedError

    async def stop(self) -> None:
        self._stopped = True

    async def stream(self) -> AsyncIterator[OHLCV]:
        attempt = 0
        while not self._stopped:
            try:
                async with self._connect(self.url) as conn:
                    await conn.send(self._subscribe_payload())
                    attempt = 0
                    async for message in conn:
                        if self._stopped:
                            return
                        self.messages_seen += 1
                        for trade in self._parse(message):
                            for bar in self._agg[trade.instrument].add_trade(trade):
                                yield bar
                        for agg in self._agg.values():
                            for bar in agg.poll():
                                yield bar
            except Exception as exc:
                if self._stopped:
                    return
                attempt += 1
                self.reconnects += 1
                if attempt > self._max_reconnects:
                    _log.error("ws giving up", extra={"provider": self.name, "err": str(exc)})
                    return
                delay = min(30.0, self._backoff * (2 ** (attempt - 1)))
                _log.warning(
                    "ws reconnect",
                    extra={"provider": self.name, "attempt": attempt, "delay": delay},
                )
                with contextlib.suppress(asyncio.CancelledError):
                    await asyncio.sleep(delay)


# kanonisch → Kraken-v2-WS-Name (die v2-API nutzt "BASE/QUOTE", nicht "XXBTZUSD"/"XBTUSDT").
# WICHTIG: Kraken-Liquidität liegt fast vollständig in den **USD**-Paaren. "BTC/USDT" existiert,
# handelt aber kaum (0 Trades im Test) ⇒ wir mappen die kanonischen *USDT*-Symbole hier bewusst
# auf die liquiden Kraken-*USD*-Paare (USDT≈USD für Krypto-Spot-Marktdaten).
_KRAKEN_WS_NAME: dict[str, str] = {
    "BTCUSDT": "BTC/USD",
    "BTCUSD": "BTC/USD",
    "ETHUSDT": "ETH/USD",
    "ETHUSD": "ETH/USD",
    "SOLUSDT": "SOL/USD",
    "SOLUSD": "SOL/USD",
    "XRPUSD": "XRP/USD",
    "XRPUSDT": "XRP/USD",
    "DOGEUSDT": "XDG/USD",  # Kraken nennt Dogecoin "XDG"
    "DOGEUSD": "XDG/USD",
    # tokenisiertes Gold (Tether Gold) als XAUUSD-Live-Proxy auf Kraken
    "XAUUSD": "XAUT/USD",
    "XAUTUSD": "XAUT/USD",
    "XAUTUSDT": "XAUT/USD",
}
_KRAKEN_QUOTES = ("USDT", "USDC", "USD", "EUR", "GBP", "BTC", "ETH")


def kraken_ws_name(canonical: str) -> str:
    """``BTCUSDT`` → ``BTC/USD`` (liquides Kraken-Paar). Fällt auf eine Base/Quote-Heuristik
    zurück und normalisiert die Quote-Währung USDT→USD."""
    c = canonical.upper().replace("/", "")
    if c in _KRAKEN_WS_NAME:
        return _KRAKEN_WS_NAME[c]
    for q in _KRAKEN_QUOTES:
        if c.endswith(q) and len(c) > len(q):
            quote = "USD" if q in ("USDT", "USDC") else q
            return f"{c[: -len(q)]}/{quote}"
    return canonical.upper()


class KrakenWSSource(_WSBase):
    url = "wss://ws.kraken.com/v2"
    name = "kraken_ws"

    def __init__(self, instruments: list[str], *args: Any, **kwargs: Any) -> None:
        super().__init__(instruments, *args, **kwargs)
        # kanonisch (agg-Schlüssel) ↔ Kraken-v2-WS-Name
        self._ws_name = {c: kraken_ws_name(c) for c in self.instruments}
        self._from_ws = {v: k for k, v in self._ws_name.items()}

    def _subscribe_payload(self) -> str:
        return json.dumps(
            {
                "method": "subscribe",
                "params": {"channel": "trade", "symbol": list(self._ws_name.values())},
            }
        )

    def _parse(self, message: str) -> list[Trade]:
        try:
            data: dict[str, Any] = json.loads(message)
        except json.JSONDecodeError:
            return []
        if data.get("channel") != "trade" or "data" not in data:
            return []
        out: list[Trade] = []
        for row in data["data"]:
            raw = str(row["symbol"]).upper().replace(" ", "")
            # Kraken v2 sendet "BTC/USDT"; wir akzeptieren auch das kanonische "BTCUSDT".
            canonical = self._from_ws.get(raw)
            if canonical is None:
                stripped = raw.replace("/", "")
                canonical = stripped if stripped in self._agg else None
            if canonical is None or canonical not in self._agg:
                continue
            out.append(
                Trade(
                    instrument=canonical,
                    ts=parse_timestamp(row["timestamp"]),
                    price=float(row["price"]),
                    size=float(row["qty"]),
                    side=Side.BUY if row.get("side") == "buy" else Side.SELL,
                    source=self.name,
                    ingested_at=self._clock.now(),
                )
            )
        return out


class BybitWSSource(_WSBase):
    url = "wss://stream.bybit.com/v5/public/linear"
    name = "bybit_ws"

    def _subscribe_payload(self) -> str:
        return json.dumps(
            {"op": "subscribe", "args": [f"publicTrade.{s}" for s in self.instruments]}
        )

    def _parse(self, message: str) -> list[Trade]:
        try:
            data: dict[str, Any] = json.loads(message)
        except json.JSONDecodeError:
            return []
        topic = str(data.get("topic", ""))
        if not topic.startswith("publicTrade.") or "data" not in data:
            return []
        out: list[Trade] = []
        for row in data["data"]:
            sym = str(row["s"]).upper()
            if sym not in self._agg:
                continue
            out.append(
                Trade(
                    instrument=sym,
                    ts=parse_timestamp(int(row["T"])),
                    price=float(row["p"]),
                    size=float(row["v"]),
                    side=Side.BUY if row.get("S") == "Buy" else Side.SELL,
                    source=self.name,
                    ingested_at=self._clock.now(),
                )
            )
        return out


class BinanceWSSource(_WSBase):
    """Binance USD-M-Futures ``@aggTrade``-Stream → confirmed Bars via ``BarAggregator``.
    Für Spot: ``url='wss://stream.binance.com:9443/ws'`` beim Erzeugen überschreiben."""

    url = "wss://fstream.binance.com/ws"
    name = "binance_ws"

    def _subscribe_payload(self) -> str:
        return json.dumps(
            {
                "method": "SUBSCRIBE",
                "params": [f"{s.lower()}@aggTrade" for s in self.instruments],
                "id": 1,
            }
        )

    def _parse(self, message: str) -> list[Trade]:
        try:
            data: dict[str, Any] = json.loads(message)
        except json.JSONDecodeError:
            return []
        # kombinierter Stream: {"stream": "...", "data": {...}} — sonst direkt das Event
        ev = data.get("data", data)
        if ev.get("e") != "aggTrade":
            return []
        sym = str(ev.get("s", "")).upper()
        if sym not in self._agg:
            return []
        return [
            Trade(
                instrument=sym,
                ts=parse_timestamp(int(ev["T"])),
                price=float(ev["p"]),
                size=float(ev["q"]),
                side=Side.SELL if ev.get("m") else Side.BUY,  # m=True ⇒ Buyer ist Maker
                source=self.name,
                ingested_at=self._clock.now(),
            )
        ]


__all__ = ["BinanceWSSource", "BybitWSSource", "ConnectFn", "KrakenWSSource"]
