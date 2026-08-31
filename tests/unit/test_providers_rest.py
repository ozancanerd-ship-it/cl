"""Tests: Kraken + Bybit public REST adapters against recorded fixtures (no real network)."""

from __future__ import annotations

import contextlib
import json
from pathlib import Path

import httpx
import pytest
import respx

from trading_agent.core.enums import ProviderHealth, Timeframe
from trading_agent.core.time import parse_timestamp
from trading_agent.data.providers.bybit_public import BybitPublicDataProvider
from trading_agent.data.providers.kraken import KrakenDataProvider

FIX = Path(__file__).parent.parent / "data" / "rest"
START = parse_timestamp("2024-06-01T00:00:00Z")
END = parse_timestamp("2024-06-01T01:00:00Z")


def _load(name: str) -> dict:
    return json.loads((FIX / name).read_text())


@respx.mock
async def test_kraken_ohlcv_normalization() -> None:
    respx.get("https://api.kraken.com/0/public/OHLC").mock(
        return_value=httpx.Response(200, json=_load("kraken_ohlc_btcusdt_m5.json"))
    )
    p = KrakenDataProvider()
    bars = await p.fetch_ohlcv("BTCUSDT", Timeframe.M5, START, END)
    await p.aclose()
    assert len(bars) == 4
    b = bars[0]
    assert b.instrument == "BTCUSDT"
    assert b.open == 60000.0 and b.close == 60080.0
    assert b.open_time == parse_timestamp("2024-06-01T00:00:00Z")
    assert b.close_time == parse_timestamp("2024-06-01T00:05:00Z")
    assert [bar.open_time for bar in bars] == sorted(bar.open_time for bar in bars)
    assert p.status().health is ProviderHealth.HEALTHY


@respx.mock
async def test_kraken_error_payload_marks_unhealthy() -> None:
    respx.get("https://api.kraken.com/0/public/OHLC").mock(
        return_value=httpx.Response(
            200, json={"error": ["EGeneral:Invalid arguments"], "result": {}}
        )
    )
    p = KrakenDataProvider()
    with contextlib.suppress(Exception):
        await p.fetch_ohlcv("BTCUSDT", Timeframe.M5, START, END)
    await p.aclose()
    assert p.status().health is not ProviderHealth.HEALTHY


@respx.mock
async def test_bybit_kline_is_reversed_and_normalized() -> None:
    respx.get("https://api.bybit.com/v5/market/kline").mock(
        return_value=httpx.Response(200, json=_load("bybit_kline_btcusdt_m5.json"))
    )
    p = BybitPublicDataProvider()
    bars = await p.fetch_ohlcv("BTCUSDT", Timeframe.M5, START, END)
    await p.aclose()
    assert len(bars) == 4
    # fixture is newest-first; provider must return oldest-first
    assert bars[0].open_time < bars[-1].open_time
    assert bars[0].open == 60000.0
    assert bars[0].quote_volume == 750000.0


@respx.mock
async def test_bybit_funding_history() -> None:
    respx.get("https://api.bybit.com/v5/market/funding/history").mock(
        return_value=httpx.Response(200, json=_load("bybit_funding_btcusdt.json"))
    )
    p = BybitPublicDataProvider()
    rows = await p.fetch_funding(
        "BTCUSDT",
        parse_timestamp("2024-06-01T00:00:00Z"),
        parse_timestamp("2024-06-02T00:00:00Z"),
    )
    await p.aclose()
    assert len(rows) == 2
    assert rows[0].rate == 0.0001
    assert rows[0].ts == parse_timestamp("2024-06-01T00:00:00Z")


@respx.mock
async def test_bybit_retcode_error() -> None:
    respx.get("https://api.bybit.com/v5/market/kline").mock(
        return_value=httpx.Response(
            200, json={"retCode": 10001, "retMsg": "params error", "result": {}}
        )
    )
    p = BybitPublicDataProvider()
    with contextlib.suppress(Exception):
        await p.fetch_ohlcv("BTCUSDT", Timeframe.M5, START, END)
    await p.aclose()
    assert p.status().health is not ProviderHealth.HEALTHY


@respx.mock
async def test_kraken_fetch_quote_bid_ask() -> None:
    respx.get("https://api.kraken.com/0/public/Ticker").mock(
        return_value=httpx.Response(
            200,
            json={
                "error": [],
                "result": {
                    "XXBTZUSD": {
                        "a": ["78103.60000", "1", "1.000"],
                        "b": ["78103.50000", "2", "2.000"],
                        "c": ["78103.60000", "0.001"],
                    }
                },
            },
        )
    )
    p = KrakenDataProvider()
    q = await p.fetch_quote("BTCUSD")
    await p.aclose()
    assert q.bid == 78103.5 and q.ask == 78103.6
    assert q.ask_size == 1.0 and q.bid_size == 2.0
    assert q.spread == pytest.approx(0.1) and q.instrument == "BTCUSD"
    assert q.source == "kraken"
    assert p.status().health is ProviderHealth.HEALTHY


@respx.mock
async def test_bybit_fetch_quote_bid_ask() -> None:
    respx.get("https://api.bybit.com/v5/market/tickers").mock(
        return_value=httpx.Response(
            200,
            json={
                "retCode": 0,
                "retMsg": "OK",
                "result": {
                    "category": "linear",
                    "list": [
                        {
                            "symbol": "BTCUSDT",
                            "bid1Price": "78074.10",
                            "bid1Size": "3.5",
                            "ask1Price": "78074.20",
                            "ask1Size": "0.4",
                            "lastPrice": "78074.10",
                        }
                    ],
                },
                "time": 1788039024503,
            },
        )
    )
    p = BybitPublicDataProvider()
    q = await p.fetch_quote("BTCUSDT")
    await p.aclose()
    assert q.bid == 78074.1 and q.ask == 78074.2
    assert q.spread == pytest.approx(0.1)
    assert q.ts == parse_timestamp(1788039024503)
    assert q.source == "bybit_public"


@respx.mock
async def test_bybit_fetch_quote_missing_bid_marks_unhealthy() -> None:
    respx.get("https://api.bybit.com/v5/market/tickers").mock(
        return_value=httpx.Response(
            200, json={"retCode": 0, "retMsg": "OK", "result": {"list": []}}
        )
    )
    p = BybitPublicDataProvider()
    with contextlib.suppress(Exception):
        await p.fetch_quote("BTCUSDT")
    await p.aclose()
    assert p.status().health is not ProviderHealth.HEALTHY
