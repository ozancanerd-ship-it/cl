"""Tests: BarAggregator (trades -> bars) and WS sources (parse + reconnect, fake connection)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import pytest

from trading_agent.core.clock import FixedClock, SimClock
from trading_agent.core.enums import Side, Timeframe
from trading_agent.core.models import OHLCV, Trade
from trading_agent.core.time import parse_timestamp
from trading_agent.data.aggregator import BarAggregator
from trading_agent.data.providers.exchange_ws import BybitWSSource, KrakenWSSource, _WSBase


def _trade(ts: str, price: float, size: float = 1.0) -> Trade:
    return Trade(
        instrument="BTCUSDT", ts=parse_timestamp(ts), price=price, size=size, side=Side.BUY
    )


async def _collect(src: _WSBase, n: int, *, max_iters: int = 200) -> list[OHLCV]:
    out: list[OHLCV] = []
    i = 0
    async for bar in src.stream():
        out.append(bar)
        i += 1
        if len(out) >= n or i >= max_iters:
            await src.stop()
            break
    return out


class TestBarAggregator:
    def test_emits_completed_bar_on_slot_change(self) -> None:
        agg = BarAggregator(
            "BTCUSDT", Timeframe.M5, clock=FixedClock(parse_timestamp("2024-06-01T00:20:00Z"))
        )
        assert agg.add_trade(_trade("2024-06-01T00:00:10Z", 100.0)) == []
        assert agg.add_trade(_trade("2024-06-01T00:02:00Z", 105.0)) == []
        assert agg.add_trade(_trade("2024-06-01T00:04:30Z", 95.0)) == []
        bars = agg.add_trade(_trade("2024-06-01T00:05:10Z", 101.0))  # crosses into next M5
        assert len(bars) == 1
        b = bars[0]
        assert b.open == 100.0 and b.high == 105.0 and b.low == 95.0 and b.close == 95.0
        assert b.open_time == parse_timestamp("2024-06-01T00:00:00Z")
        assert b.volume == 3.0 and b.trades == 3

    def test_poll_finalizes_stale_bar(self) -> None:
        agg = BarAggregator("BTCUSDT", Timeframe.M5)
        agg.add_price(parse_timestamp("2024-06-01T00:01:00Z"), 100.0, 1.0)
        assert agg.poll(parse_timestamp("2024-06-01T00:04:00Z")) == []
        bars = agg.poll(parse_timestamp("2024-06-01T00:06:00Z"))
        assert len(bars) == 1 and bars[0].close == 100.0

    def test_forming_bar_not_emitted(self) -> None:
        agg = BarAggregator("BTCUSDT", Timeframe.M5)
        agg.add_price(parse_timestamp("2024-06-01T00:01:00Z"), 100.0)
        assert agg.forming is not None and agg.forming.close == 100.0


class _FakeConn:
    def __init__(self, messages: list[str], *, fail_first: bool = False) -> None:
        self._messages = messages
        self._fail_first = fail_first
        self.sent: list[str] = []

    async def __aenter__(self) -> _FakeConn:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def send(self, data: str) -> None:
        self.sent.append(data)

    async def __aiter__(self) -> AsyncIterator[str]:
        if self._fail_first:
            self._fail_first = False
            raise ConnectionError("boom")
        for m in self._messages:
            yield m


class TestKrakenWS:
    async def test_parses_trades_into_bars(self) -> None:
        msg = json.dumps(
            {
                "channel": "trade",
                "data": [
                    {
                        "symbol": "BTCUSDT",
                        "price": "100.0",
                        "qty": "1.0",
                        "side": "buy",
                        "timestamp": "2024-06-01T00:00:10Z",
                    },
                    {
                        "symbol": "BTCUSDT",
                        "price": "102.0",
                        "qty": "2.0",
                        "side": "sell",
                        "timestamp": "2024-06-01T00:30:00Z",
                    },
                    {
                        "symbol": "BTCUSDT",
                        "price": "99.0",
                        "qty": "1.0",
                        "side": "buy",
                        "timestamp": "2024-06-01T01:05:00Z",
                    },
                ],
            }
        )
        conn = _FakeConn([msg])
        src = KrakenWSSource(
            ["BTCUSDT"],
            Timeframe.H1,
            connect_fn=lambda url: conn,
            clock=SimClock(parse_timestamp("2024-06-01T02:00:00Z")),
        )
        bars = await _collect(src, 1)
        assert conn.sent and "subscribe" in conn.sent[0]
        assert bars[0].open == 100.0 and bars[0].high == 102.0
        assert bars[0].open_time == parse_timestamp("2024-06-01T00:00:00Z")

    async def test_reconnects_then_streams(self) -> None:
        msg = json.dumps(
            {
                "channel": "trade",
                "data": [
                    {
                        "symbol": "BTCUSDT",
                        "price": "100.0",
                        "qty": "1.0",
                        "side": "buy",
                        "timestamp": "2024-06-01T00:00:00Z",
                    }
                ],
            }
        )
        conn = _FakeConn([msg], fail_first=True)
        src = KrakenWSSource(
            ["BTCUSDT"],
            Timeframe.M1,
            connect_fn=lambda url: conn,
            backoff_base_s=0.0,
            clock=SimClock(parse_timestamp("2024-06-01T02:00:00Z")),
        )
        bars = await _collect(src, 1)
        assert src.reconnects == 1
        assert bars and bars[0].open == 100.0


class TestKrakenSymbolMapping:
    def test_kraken_ws_name_maps_usdt_to_liquid_usd_pair(self) -> None:
        from trading_agent.data.providers.exchange_ws import kraken_ws_name

        assert kraken_ws_name("BTCUSDT") == "BTC/USD"
        assert kraken_ws_name("ETHUSDT") == "ETH/USD"
        assert kraken_ws_name("DOGEUSDT") == "XDG/USD"
        assert kraken_ws_name("ADAUSDT") == "ADA/USD"  # Heuristik-Fallback
        assert kraken_ws_name("XBTEUR") == "XBT/EUR"

    async def test_subscribe_uses_ws_names_and_parse_maps_back(self) -> None:
        src = KrakenWSSource(["BTCUSDT", "ETHUSDT"])
        assert '"BTC/USD"' in src._subscribe_payload()
        assert '"ETH/USD"' in src._subscribe_payload()
        msg = json.dumps(
            {
                "channel": "trade",
                "data": [
                    {
                        "symbol": "BTC/USD",  # Kraken-v2-Format
                        "price": "78000.0",
                        "qty": "0.01",
                        "side": "buy",
                        "timestamp": "2026-08-29T21:50:00Z",
                    }
                ],
            }
        )
        trades = src._parse(msg)
        assert len(trades) == 1
        assert trades[0].instrument == "BTCUSDT"  # kanonisch zurückgemappt
        assert trades[0].price == 78000.0


class TestBybitWS:
    async def test_parses_public_trade(self) -> None:
        msg = json.dumps(
            {
                "topic": "publicTrade.BTCUSDT",
                "data": [
                    {"s": "BTCUSDT", "p": "100.0", "v": "1.0", "S": "Buy", "T": 1717200000000},
                    {"s": "BTCUSDT", "p": "103.0", "v": "1.0", "S": "Sell", "T": 1717203600000},
                ],
            }
        )
        conn = _FakeConn([msg])
        src = BybitWSSource(
            ["BTCUSDT"],
            Timeframe.H1,
            connect_fn=lambda url: conn,
            clock=SimClock(parse_timestamp("2024-06-01T05:00:00Z")),
        )
        bars = await _collect(src, 1)
        assert bars[0].open == 100.0


def _keep(x: object) -> None:
    _ = pytest
