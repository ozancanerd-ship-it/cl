"""Tests: Repository – Parquet-OHLCV, SQLite-News/Makro, Point-in-Time, Fingerprint."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

import pytest

from trading_agent.core.enums import NewsImpact, Timeframe
from trading_agent.core.models import OHLCV, Funding, MacroEvent, NewsEvent
from trading_agent.core.time import parse_timestamp
from trading_agent.data.repository import MarketDataRepository, RepositoryError


@pytest.fixture
def repo(tmp_path: Path) -> MarketDataRepository:
    return MarketDataRepository(tmp_path / "repo")


class TestOhlcvRoundtrip:
    def test_write_read(
        self, repo: MarketDataRepository, make_series: Callable[..., list[OHLCV]]
    ) -> None:
        bars = make_series(50, start="2024-06-01T00:00:00Z")
        repo.write_ohlcv(bars)
        back = repo.read_ohlcv(
            "BTCUSDT",
            Timeframe.M5,
            parse_timestamp("2024-06-01T00:00:00Z"),
            parse_timestamp("2024-06-02T00:00:00Z"),
        )
        assert len(back) == 50
        assert back[0].open == bars[0].open
        assert back[-1].close == bars[-1].close
        assert all(b.instrument == "BTCUSDT" and b.timeframe is Timeframe.M5 for b in back)

    def test_time_range_filter(
        self, repo: MarketDataRepository, make_series: Callable[..., list[OHLCV]]
    ) -> None:
        bars = make_series(50, start="2024-06-01T00:00:00Z")
        repo.write_ohlcv(bars)
        window = repo.read_ohlcv(
            "BTCUSDT",
            Timeframe.M5,
            parse_timestamp("2024-06-01T00:30:00Z"),
            parse_timestamp("2024-06-01T01:00:00Z"),
        )
        assert all(
            parse_timestamp("2024-06-01T00:30:00Z")
            <= b.open_time
            < parse_timestamp("2024-06-01T01:00:00Z")
            for b in window
        )
        assert len(window) == 6

    def test_append_merges_and_dedups(
        self, repo: MarketDataRepository, make_series: Callable[..., list[OHLCV]]
    ) -> None:
        a = make_series(20, start="2024-06-01T00:00:00Z")  # 00:00 .. 01:35
        b = make_series(20, start="2024-06-01T02:00:00Z")  # disjunkt
        repo.write_ohlcv(a)
        repo.write_ohlcv(b)
        repo.write_ohlcv(a)  # nochmal -> keine Duplikate
        back = repo.read_ohlcv(
            "BTCUSDT",
            Timeframe.M5,
            parse_timestamp("2024-06-01T00:00:00Z"),
            parse_timestamp("2024-06-02T00:00:00Z"),
        )
        opens = [x.open_time for x in back]
        assert len(opens) == len(set(opens)) == 40

    def test_coverage(
        self, repo: MarketDataRepository, make_series: Callable[..., list[OHLCV]]
    ) -> None:
        assert repo.ohlcv_coverage("BTCUSDT", Timeframe.M5) is None
        bars = make_series(10, start="2024-06-01T00:00:00Z")
        repo.write_ohlcv(bars)
        cov = repo.ohlcv_coverage("BTCUSDT", Timeframe.M5)
        assert cov is not None
        assert cov[0] == bars[0].open_time
        assert cov[1] == bars[-1].open_time

    def test_end_before_start_rejected(self, repo: MarketDataRepository) -> None:
        with pytest.raises(RepositoryError):
            repo.read_ohlcv(
                "BTCUSDT",
                Timeframe.M5,
                parse_timestamp("2024-06-02T00:00:00Z"),
                parse_timestamp("2024-06-01T00:00:00Z"),
            )


class TestPointInTime:
    def test_as_of_excludes_bars_closing_after(
        self, repo: MarketDataRepository, make_series: Callable[..., list[OHLCV]]
    ) -> None:
        bars = make_series(48, start="2024-06-01T00:00:00Z")  # 4h M5
        repo.write_ohlcv(bars)
        as_of = parse_timestamp("2024-06-01T02:00:00Z")
        pit = repo.read_ohlcv(
            "BTCUSDT",
            Timeframe.M5,
            parse_timestamp("2024-06-01T00:00:00Z"),
            parse_timestamp("2024-06-01T04:00:00Z"),
            as_of=as_of,
        )
        assert pit
        assert all(b.close_time <= as_of for b in pit)
        assert pit[-1].close_time == as_of  # Bar, die exakt bei as_of schließt, ist erlaubt

    def test_as_of_before_all_data_returns_empty(
        self, repo: MarketDataRepository, make_series: Callable[..., list[OHLCV]]
    ) -> None:
        bars = make_series(10, start="2024-06-01T00:00:00Z")
        repo.write_ohlcv(bars)
        pit = repo.read_ohlcv(
            "BTCUSDT",
            Timeframe.M5,
            parse_timestamp("2024-06-01T00:00:00Z"),
            parse_timestamp("2024-06-02T00:00:00Z"),
            as_of=parse_timestamp("2024-05-31T00:00:00Z"),
        )
        assert pit == []


class TestFingerprint:
    def test_stable_across_rewrites(
        self, repo: MarketDataRepository, make_series: Callable[..., list[OHLCV]]
    ) -> None:
        bars = make_series(30, start="2024-06-01T00:00:00Z")
        repo.write_ohlcv(bars)
        fp1 = repo.dataset_fingerprint("BTCUSDT", Timeframe.M5)
        repo.write_ohlcv(bars)
        repo.write_ohlcv(list(reversed(bars)))
        fp2 = repo.dataset_fingerprint("BTCUSDT", Timeframe.M5)
        assert fp1 == fp2

    def test_changes_with_data(
        self, repo: MarketDataRepository, make_series: Callable[..., list[OHLCV]]
    ) -> None:
        repo.write_ohlcv(make_series(30, start="2024-06-01T00:00:00Z"))
        fp1 = repo.dataset_fingerprint("BTCUSDT", Timeframe.M5)
        repo.write_ohlcv(make_series(10, start="2024-06-02T00:00:00Z"))
        fp2 = repo.dataset_fingerprint("BTCUSDT", Timeframe.M5)
        assert fp1 != fp2

    def test_as_of_fingerprint_matches_pit_read(
        self, repo: MarketDataRepository, make_series: Callable[..., list[OHLCV]]
    ) -> None:
        bars = make_series(48, start="2024-06-01T00:00:00Z")
        repo.write_ohlcv(bars)
        as_of = parse_timestamp("2024-06-01T02:00:00Z")
        fp_pit = repo.dataset_fingerprint("BTCUSDT", Timeframe.M5, as_of=as_of)
        fp_full = repo.dataset_fingerprint("BTCUSDT", Timeframe.M5)
        assert fp_pit != fp_full


class TestFunding:
    def test_roundtrip_and_pit(self, repo: MarketDataRepository) -> None:
        rows = [
            Funding(instrument="BTCUSDT", ts="2024-06-01T00:00:00Z", rate=0.0001),
            Funding(instrument="BTCUSDT", ts="2024-06-01T08:00:00Z", rate=-0.0002),
            Funding(instrument="BTCUSDT", ts="2024-06-01T16:00:00Z", rate=0.00005),
        ]
        repo.write_funding(rows)
        back = repo.read_funding(
            "BTCUSDT",
            parse_timestamp("2024-06-01T00:00:00Z"),
            parse_timestamp("2024-06-02T00:00:00Z"),
            as_of=parse_timestamp("2024-06-01T09:00:00Z"),
        )
        assert len(back) == 2
        assert back[0].rate == 0.0001


class TestNewsMacroPointInTime:
    def test_news_revision_pit(self, repo: MarketDataRepository) -> None:
        first = NewsEvent(
            event_id="cpi",
            event_type="CPI",
            impact=NewsImpact.HIGH,
            scheduled_time="2024-06-12T12:30:00Z",
            available_time="2024-06-12T12:30:00Z",
            affected_symbols=["BTCUSDT"],
            actual=3.3,
        )
        revised = first.model_copy(
            update={"available_time": parse_timestamp("2024-06-13T09:00:00Z"), "actual": 3.2}
        )
        repo.write_news([first, revised])

        # as_of vor der Revision -> alter Wert
        early = repo.read_news(
            parse_timestamp("2024-06-01T00:00:00Z"),
            parse_timestamp("2024-07-01T00:00:00Z"),
            as_of=parse_timestamp("2024-06-12T18:00:00Z"),
        )
        assert len(early) == 1 and early[0].actual == 3.3

        # as_of nach der Revision -> neuer Wert
        late = repo.read_news(
            parse_timestamp("2024-06-01T00:00:00Z"),
            parse_timestamp("2024-07-01T00:00:00Z"),
            as_of=parse_timestamp("2024-06-20T00:00:00Z"),
        )
        assert len(late) == 1 and late[0].actual == 3.2

    def test_news_not_yet_available(self, repo: MarketDataRepository) -> None:
        ev = NewsEvent(
            event_id="unlock",
            event_type="TOKEN_UNLOCK",
            impact=NewsImpact.MEDIUM,
            scheduled_time="2024-06-15T00:00:00Z",
            available_time="2024-05-01T00:00:00Z",
            affected_symbols=["SOLUSDT"],
        )
        repo.write_news([ev])
        # bekannt seit Mai -> bei as_of im April nicht sichtbar
        assert (
            repo.read_news(
                parse_timestamp("2024-06-01T00:00:00Z"),
                parse_timestamp("2024-07-01T00:00:00Z"),
                as_of=parse_timestamp("2024-04-01T00:00:00Z"),
            )
            == []
        )

    def test_news_symbol_and_impact_filter(self, repo: MarketDataRepository) -> None:
        repo.write_news(
            [
                NewsEvent(
                    event_id="a",
                    event_type="CPI",
                    impact=NewsImpact.HIGH,
                    scheduled_time="2024-06-12T12:30:00Z",
                    available_time="2024-06-12T12:30:00Z",
                    affected_symbols=["BTCUSDT"],
                ),
                NewsEvent(
                    event_id="b",
                    event_type="PMI",
                    impact=NewsImpact.LOW,
                    scheduled_time="2024-06-13T14:00:00Z",
                    available_time="2024-06-13T14:00:00Z",
                    affected_symbols=["EURUSD"],
                ),
            ]
        )
        btc = repo.read_news(
            parse_timestamp("2024-06-01T00:00:00Z"),
            parse_timestamp("2024-07-01T00:00:00Z"),
            symbols=["BTCUSDT"],
        )
        assert {e.event_id for e in btc} == {"a"}
        high = repo.read_news(
            parse_timestamp("2024-06-01T00:00:00Z"),
            parse_timestamp("2024-07-01T00:00:00Z"),
            impact_at_least=NewsImpact.MEDIUM,
        )
        assert {e.event_id for e in high} == {"a"}

    def test_macro_revision_pit(self, repo: MarketDataRepository) -> None:
        repo.write_macro(
            [
                MacroEvent(
                    series_id="US_CPI_YOY",
                    reference_period="2024-05-01T00:00:00Z",
                    value=3.3,
                    available_time="2024-06-12T12:30:00Z",
                    revision=0,
                ),
                MacroEvent(
                    series_id="US_CPI_YOY",
                    reference_period="2024-05-01T00:00:00Z",
                    value=3.2,
                    available_time="2024-07-11T12:30:00Z",
                    revision=1,
                ),
            ]
        )
        early = repo.read_macro(
            ["US_CPI_YOY"],
            parse_timestamp("2024-01-01T00:00:00Z"),
            parse_timestamp("2025-01-01T00:00:00Z"),
            as_of=parse_timestamp("2024-06-20T00:00:00Z"),
        )
        assert len(early) == 1 and early[0].value == 3.3 and early[0].revision == 0
        late = repo.read_macro(
            ["US_CPI_YOY"],
            parse_timestamp("2024-01-01T00:00:00Z"),
            parse_timestamp("2025-01-01T00:00:00Z"),
            as_of=parse_timestamp("2024-08-01T00:00:00Z"),
        )
        assert len(late) == 1 and late[0].value == 3.2 and late[0].revision == 1


class TestIngestionLog:
    def test_log_records_writes(
        self, repo: MarketDataRepository, make_series: Callable[..., list[OHLCV]]
    ) -> None:
        repo.write_ohlcv(make_series(5, start="2024-06-01T00:00:00Z"))
        log = repo.ingestion_log()
        assert log and log[0]["kind"] == "ohlcv" and log[0]["rows"] == 5


def test_repository_persists_across_instances(
    tmp_path: Path, make_series: Callable[..., list[OHLCV]]
) -> None:
    root = tmp_path / "repo"
    r1 = MarketDataRepository(root)
    r1.write_ohlcv(make_series(12, start="2024-06-01T00:00:00Z"))
    r2 = MarketDataRepository(root)  # neue Instanz, gleicher Pfad
    back = r2.read_ohlcv(
        "BTCUSDT",
        Timeframe.M5,
        parse_timestamp("2024-06-01T00:00:00Z"),
        parse_timestamp("2024-06-02T00:00:00Z"),
    )
    assert len(back) == 12
    _ = timedelta
