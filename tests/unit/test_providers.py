"""Tests: Provider-Interfaces, Mock-Provider (Determinismus), CSV-Provider (PIT, Timezone)."""

from __future__ import annotations

from pathlib import Path

import pytest

from trading_agent.core.clock import FixedClock, SimClock
from trading_agent.core.enums import DataKind, ProviderHealth, Timeframe
from trading_agent.core.time import parse_timestamp
from trading_agent.data.interfaces import (
    HistoricalOHLCVProvider,
    MarketDataProvider,
    NewsProvider,
    ProviderStatus,
)
from trading_agent.data.providers.csv_provider import CsvMarketDataProvider, CsvProviderError
from trading_agent.data.providers.mock_provider import MockMarketDataProvider

START = parse_timestamp("2024-06-01T00:00:00Z")
END = parse_timestamp("2024-06-01T06:00:00Z")


class TestMockProvider:
    def test_is_a_provider(self) -> None:
        mp = MockMarketDataProvider(clock=FixedClock(START))
        assert isinstance(mp, MarketDataProvider)
        assert isinstance(mp, HistoricalOHLCVProvider)
        assert DataKind.OHLCV in mp.provides

    def test_deterministic(self) -> None:
        a = MockMarketDataProvider(clock=FixedClock(START)).get_ohlcv(
            "BTCUSDT", Timeframe.M5, START, END
        )
        b = MockMarketDataProvider(clock=FixedClock(START)).get_ohlcv(
            "BTCUSDT", Timeframe.M5, START, END
        )
        assert len(a) == len(b) == 72
        assert [(x.open, x.high, x.low, x.close, x.volume) for x in a] == [
            (y.open, y.high, y.low, y.close, y.volume) for y in b
        ]

    def test_different_instruments_differ(self) -> None:
        clk = FixedClock(START)
        btc = MockMarketDataProvider(clock=clk).get_ohlcv("BTCUSDT", Timeframe.M5, START, END)
        eth = MockMarketDataProvider(clock=clk).get_ohlcv("ETHUSDT", Timeframe.M5, START, END)
        assert btc[0].open != eth[0].open

    def test_bars_are_valid_and_aligned(self) -> None:
        from datetime import timedelta

        from trading_agent.core.time import is_aligned

        bars = MockMarketDataProvider(clock=FixedClock(START)).get_ohlcv(
            "BTCUSDT", Timeframe.M15, START, END
        )
        assert all(b.timeframe is Timeframe.M15 for b in bars)
        assert all(b.close_time - b.open_time == timedelta(minutes=15) for b in bars)
        assert all(is_aligned(b.open_time, Timeframe.M15) for b in bars)
        for i in range(1, len(bars)):
            assert bars[i].open_time > bars[i - 1].open_time

    def test_status_reports_health(self) -> None:
        mp = MockMarketDataProvider(clock=FixedClock(START))
        assert mp.status().health is ProviderHealth.DEGRADED  # noch keine Abfrage
        mp.get_ohlcv("BTCUSDT", Timeframe.M5, START, END)
        st = mp.status()
        assert isinstance(st, ProviderStatus)
        assert st.health is ProviderHealth.HEALTHY

    def test_forced_health(self) -> None:
        mp = MockMarketDataProvider(
            clock=FixedClock(START), force_health=ProviderHealth.UNAVAILABLE
        )
        assert mp.status().health is ProviderHealth.UNAVAILABLE
        assert not mp.status().is_usable

    def test_quotes_trades_funding_oi(self) -> None:
        mp = MockMarketDataProvider(clock=FixedClock(START))
        q = mp.get_quotes("BTCUSDT", START, parse_timestamp("2024-06-01T00:30:00Z"))
        assert q and all(x.ask >= x.bid for x in q)
        tr = mp.get_trades("BTCUSDT", START, parse_timestamp("2024-06-01T00:10:00Z"))
        assert tr
        fu = mp.get_funding("BTCUSDT", START, parse_timestamp("2024-06-02T00:00:00Z"))
        assert len(fu) == 3  # 00:00, 08:00, 16:00
        oi = mp.get_open_interest("BTCUSDT", START, parse_timestamp("2024-06-01T03:00:00Z"))
        assert oi
        ob = mp.get_orderbook("BTCUSDT", parse_timestamp("2024-06-01T01:00:00Z"))
        assert ob is not None and ob.best_bid < ob.best_ask

    def test_empty_range(self) -> None:
        mp = MockMarketDataProvider(clock=FixedClock(START))
        assert mp.get_ohlcv("BTCUSDT", Timeframe.M5, START, START) == []

    def test_stream_yields_final_bars(self) -> None:
        mp = MockMarketDataProvider(clock=FixedClock(parse_timestamp("2024-06-01T05:00:00Z")))
        bars = list(mp.stream_ohlcv("BTCUSDT", Timeframe.M5))
        assert bars
        assert all(b.close_time <= parse_timestamp("2024-06-01T05:00:00Z") for b in bars)


class TestCsvProvider:
    def test_reads_ohlcv(self, csv_data_dir: Path) -> None:
        p = CsvMarketDataProvider(csv_data_dir, clock=FixedClock(START))
        assert isinstance(p, HistoricalOHLCVProvider)
        bars = p.get_ohlcv("BTCUSDT", Timeframe.M5, START, parse_timestamp("2024-06-01T01:00:00Z"))
        assert len(bars) == 6
        assert bars[0].open == 60000.0
        assert bars[0].close_time == parse_timestamp("2024-06-01T00:05:00Z")
        assert p.status().health is ProviderHealth.HEALTHY

    def test_time_window(self, csv_data_dir: Path) -> None:
        p = CsvMarketDataProvider(csv_data_dir, clock=FixedClock(START))
        bars = p.get_ohlcv(
            "BTCUSDT",
            Timeframe.M5,
            parse_timestamp("2024-06-01T00:10:00Z"),
            parse_timestamp("2024-06-01T00:20:00Z"),
        )
        assert [b.open_time.isoformat() for b in bars] == [
            "2024-06-01T00:10:00+00:00",
            "2024-06-01T00:15:00+00:00",
        ]

    def test_missing_file_raises(self, csv_data_dir: Path) -> None:
        p = CsvMarketDataProvider(csv_data_dir, clock=FixedClock(START))
        with pytest.raises(CsvProviderError):
            p.get_ohlcv("NOSUCH", Timeframe.M5, START, END)
        assert p.status().health is ProviderHealth.DEGRADED

    def test_naive_timestamp_rejected(self, csv_data_dir: Path) -> None:
        p = CsvMarketDataProvider(csv_data_dir, clock=FixedClock(START))
        with pytest.raises(CsvProviderError, match="Timestamp"):
            p.get_ohlcv("BADTZ", Timeframe.M5, START, END)

    def test_news_point_in_time(self, csv_data_dir: Path) -> None:
        p = CsvMarketDataProvider(csv_data_dir, clock=FixedClock(START))
        assert isinstance(p, NewsProvider)
        window_start = parse_timestamp("2024-06-01T00:00:00Z")
        window_end = parse_timestamp("2024-07-01T00:00:00Z")

        # vor der Veröffentlichung -> Event nicht sichtbar
        before = p.get_news(window_start, window_end, as_of=parse_timestamp("2024-06-12T12:00:00Z"))
        assert all(e.event_id != "evt-cpi-2024-06" for e in before)

        # nach der Erstveröffentlichung -> actual = 3.3 (nicht die spätere Revision 3.2)
        at_release = p.get_news(
            window_start, window_end, as_of=parse_timestamp("2024-06-12T13:00:00Z")
        )
        cpi = [e for e in at_release if e.event_id == "evt-cpi-2024-06"]
        assert cpi and cpi[0].actual == 3.3

        # nach der Revision -> actual = 3.2
        after_rev = p.get_news(
            window_start, window_end, as_of=parse_timestamp("2024-06-14T00:00:00Z")
        )
        cpi2 = [e for e in after_rev if e.event_id == "evt-cpi-2024-06"]
        assert cpi2 and cpi2[0].actual == 3.2

    def test_news_symbol_filter(self, csv_data_dir: Path) -> None:
        p = CsvMarketDataProvider(csv_data_dir, clock=FixedClock(START))
        sol = p.get_news(
            parse_timestamp("2024-06-01T00:00:00Z"),
            parse_timestamp("2024-07-01T00:00:00Z"),
            symbols=["SOLUSDT"],
        )
        assert {e.event_id for e in sol} == {"evt-unlock-sol"}

    def test_macro_revision_pit(self, csv_data_dir: Path) -> None:
        p = CsvMarketDataProvider(csv_data_dir, clock=FixedClock(START))
        early = p.get_macro(
            ["US_CPI_YOY"],
            parse_timestamp("2024-01-01T00:00:00Z"),
            parse_timestamp("2025-01-01T00:00:00Z"),
            as_of=parse_timestamp("2024-06-20T00:00:00Z"),
        )
        assert len(early) == 1 and early[0].value == 3.3
        late = p.get_macro(
            ["US_CPI_YOY"],
            parse_timestamp("2024-01-01T00:00:00Z"),
            parse_timestamp("2025-01-01T00:00:00Z"),
            as_of=parse_timestamp("2024-08-01T00:00:00Z"),
        )
        assert len(late) == 1 and late[0].value == 3.2

    def test_clock_injection_sets_ingested_at(self, csv_data_dir: Path) -> None:
        clk = SimClock(parse_timestamp("2024-06-05T00:00:00Z"))
        p = CsvMarketDataProvider(csv_data_dir, clock=clk)
        bars = p.get_ohlcv("BTCUSDT", Timeframe.M5, START, END)
        assert all(b.ingested_at == parse_timestamp("2024-06-05T00:00:00Z") for b in bars)
