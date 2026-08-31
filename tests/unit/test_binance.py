"""Binance public market-data adapter (REST) — Normalisierung, Futures-only-Guards, WS-Parser."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest
import respx

from trading_agent.core.enums import ProviderHealth, Timeframe
from trading_agent.core.time import parse_timestamp
from trading_agent.data.providers.binance import BinancePublicDataProvider
from trading_agent.data.providers.exchange_ws import BinanceWSSource

START = parse_timestamp("2024-06-03T00:00:00Z")
END = parse_timestamp("2024-06-03T01:00:00Z")

_KLINES = [
    [
        1717372800000,
        "4400.0",
        "4410.0",
        "4395.0",
        "4405.0",
        "12.5",
        1717373099999,
        "55000.0",
        40,
        "6.0",
        "26400.0",
        "0",
    ],
    [
        1717373100000,
        "4405.0",
        "4408.0",
        "4390.0",
        "4392.0",
        "9.1",
        1717373399999,
        "40000.0",
        33,
        "4.0",
        "17600.0",
        "0",
    ],
]


@respx.mock
async def test_klines_normalization_futures() -> None:
    respx.get("https://fapi.binance.com/fapi/v1/klines").mock(
        return_value=httpx.Response(200, json=_KLINES)
    )
    p = BinancePublicDataProvider(market="futures_usdm")
    bars = await p.fetch_ohlcv("XAUUSDT", Timeframe.M5, START, END)
    await p.aclose()
    assert len(bars) == 2
    b = bars[0]
    assert b.instrument == "XAUUSDT" and b.open == 4400.0 and b.close == 4405.0
    assert b.open_time == parse_timestamp(1717372800000)
    assert b.close_time == parse_timestamp(
        "2024-06-03T00:05:00Z"
    )  # open + tf, nicht Binance-closeTime
    assert b.trades == 40 and b.quote_volume == 55000.0


@respx.mock
async def test_book_ticker_quote() -> None:
    respx.get("https://fapi.binance.com/fapi/v1/ticker/bookTicker").mock(
        return_value=httpx.Response(
            200,
            json={
                "symbol": "XAUUSDT",
                "bidPrice": "4483.71",
                "bidQty": "11.7",
                "askPrice": "4483.72",
                "askQty": "22.5",
                "time": 1788113253838,
            },
        )
    )
    p = BinancePublicDataProvider(market="futures_usdm")
    q = await p.fetch_quote("XAUUSDT")
    await p.aclose()
    assert q.bid == 4483.71 and q.ask == 4483.72
    assert q.spread == pytest.approx(0.01)
    assert q.ts == parse_timestamp(1788113253838)


@respx.mock
async def test_mark_price_and_funding_and_oi() -> None:
    respx.get("https://fapi.binance.com/fapi/v1/premiumIndex").mock(
        return_value=httpx.Response(
            200,
            json={
                "symbol": "XAUUSDT",
                "markPrice": "4483.72",
                "indexPrice": "4483.50",
                "lastFundingRate": "0.00000000",
                "nextFundingTime": 1788120000000,
                "time": 1788113253838,
            },
        )
    )
    respx.get("https://fapi.binance.com/fapi/v1/fundingRate").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"symbol": "XAUUSDT", "fundingTime": 1717372800000, "fundingRate": "0.0001"},
                {"symbol": "XAUUSDT", "fundingTime": 1717373100000, "fundingRate": "0.0"},
            ],
        )
    )
    respx.get("https://fapi.binance.com/futures/data/openInterestHist").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"symbol": "XAUUSDT", "sumOpenInterest": "90000.0", "timestamp": 1717372800000},
                {"symbol": "XAUUSDT", "sumOpenInterest": "90500.0", "timestamp": 1717373100000},
            ],
        )
    )
    p = BinancePublicDataProvider(market="futures_usdm")
    mp = await p.fetch_mark_price("XAUUSDT")
    fund = await p.fetch_funding("XAUUSDT", START, END)
    oi = await p.fetch_open_interest("XAUUSDT", START, END)
    await p.aclose()
    assert mp["mark_price"] == 4483.72 and mp["last_funding_rate"] == 0.0
    assert [f.rate for f in fund] == [0.0001, 0.0]
    assert [o.oi for o in oi] == [90000.0, 90500.0]


async def test_futures_only_guards_on_spot() -> None:
    p = BinancePublicDataProvider(market="spot")
    for coro in (
        p.fetch_mark_price("PAXGUSDT"),
        p.fetch_funding("PAXGUSDT", START, END),
        p.fetch_open_interest("PAXGUSDT", START, END),
    ):
        with pytest.raises(Exception, match="USD-M-Futures"):
            await coro
    await p.aclose()


@respx.mock
async def test_list_symbols_filters_trading_and_quote() -> None:
    respx.get("https://fapi.binance.com/fapi/v1/exchangeInfo").mock(
        return_value=httpx.Response(
            200,
            json={
                "symbols": [
                    {"symbol": "XAUUSDT", "status": "TRADING", "quoteAsset": "USDT"},
                    {"symbol": "OLDCOINUSDT", "status": "BREAK", "quoteAsset": "USDT"},
                    {"symbol": "BTCUSDC", "status": "TRADING", "quoteAsset": "USDC"},
                ]
            },
        )
    )
    p = BinancePublicDataProvider(market="futures_usdm")
    syms = await p.list_symbols(quote="USDT")
    assert await p.has_symbol("XAUUSDT") is True
    await p.aclose()
    assert syms == ["XAUUSDT"]
    assert p.status().health is not ProviderHealth.UNAVAILABLE


def test_binance_ws_parses_aggtrade() -> None:
    ws = BinanceWSSource(["XAUUSDT"], Timeframe.M5)
    ev = {
        "e": "aggTrade",
        "E": 1788113253000,
        "s": "XAUUSDT",
        "p": "4483.5",
        "q": "1.5",
        "T": 1788113252000,
        "m": True,
    }
    trades = ws._parse(json.dumps({"stream": "xauusdt@aggTrade", "data": ev}))
    assert len(trades) == 1
    t = trades[0]
    assert t.instrument == "XAUUSDT" and t.price == 4483.5 and t.size == 1.5
    assert t.ts == datetime.fromtimestamp(1788113252, tz=UTC)
    # unbekanntes Symbol / falscher Event-Typ → leer
    assert (
        ws._parse(json.dumps({"e": "aggTrade", "s": "ETHUSDT", "p": "1", "q": "1", "T": 1})) == []
    )
    assert ws._parse(json.dumps({"e": "depthUpdate", "s": "XAUUSDT"})) == []
    assert "xauusdt@aggTrade" in ws._subscribe_payload()
