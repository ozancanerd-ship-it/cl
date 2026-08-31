"""Integrationstest: kompletter Data-Foundation-Pfad ohne externe Accounts.

Mock/CSV -> Data Quality -> Resampling -> Repository -> Point-in-Time-Lesen.
Enthält die harten Point-in-Time-/Look-ahead-Invarianten für Phase 1.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from trading_agent.config.loader import load_data_foundation_config
from trading_agent.core.clock import SimClock
from trading_agent.core.enums import Timeframe
from trading_agent.core.time import parse_timestamp
from trading_agent.data.providers.csv_provider import CsvMarketDataProvider
from trading_agent.data.providers.mock_provider import MockMarketDataProvider
from trading_agent.data.quality import check_ohlcv_series
from trading_agent.data.repository import MarketDataRepository
from trading_agent.data.resample import resample_ohlcv
from trading_agent.refdata.seed import build_instrument_master, build_symbol_mapper, seed_calendars

pytestmark = pytest.mark.integration

REPO_CONFIG = Path(__file__).parents[2] / "config" / "config.example.yaml"
START = parse_timestamp("2024-06-01T00:00:00Z")
NOW = parse_timestamp("2024-06-04T00:00:00Z")


def test_mvp_btc_eth_pipeline_end_to_end(tmp_path: Path) -> None:
    cfg = load_data_foundation_config(REPO_CONFIG)
    im = build_instrument_master()
    sm = build_symbol_mapper()
    cals = seed_calendars()
    clock = SimClock(NOW)
    provider = MockMarketDataProvider(clock=clock)
    repo = MarketDataRepository(tmp_path / "repo")

    for symbol in cfg.enabled_symbols:  # BTCUSDT, ETHUSDT
        assert im.has(symbol)
        inst = im.get(symbol)
        assert sm.to_provider(symbol, "synthetic") == symbol
        cal = cals[inst.calendar_id]

        # 1) M5 laden
        m5 = provider.get_ohlcv(symbol, Timeframe.M5, START, NOW)
        assert len(m5) == 3 * 288  # 3 Tage M5

        # 2) Qualität prüfen -> sauber, blockt nicht
        status = check_ohlcv_series(
            m5,
            instrument=symbol,
            timeframe=Timeframe.M5,
            now=NOW,
            calendar=cal,
        )
        assert status.is_ok, status.issues
        assert not status.blocks_trading

        # 3) Resampling M5 -> H1 -> H4 -> D1 (jeweils look-ahead-frei)
        h1 = resample_ohlcv(m5, Timeframe.M5, Timeframe.H1)
        h4 = resample_ohlcv(h1, Timeframe.H1, Timeframe.H4)
        d1 = resample_ohlcv(h1, Timeframe.H1, Timeframe.D1)
        assert len(h1) == 72
        assert len(h4) == 18
        assert len(d1) == 3

        # 4) alles ins Repository
        repo.write_ohlcv(m5)
        repo.write_ohlcv(h1)
        repo.write_ohlcv(h4)
        repo.write_ohlcv(d1)

        # 5) zurücklesen
        back = repo.read_ohlcv(symbol, Timeframe.H1, START, NOW)
        assert len(back) == 72
        assert [b.open for b in back] == [b.open for b in h1]

    # Coverage-Metadaten stimmen
    cov = repo.ohlcv_coverage("BTCUSDT", Timeframe.M5)
    assert cov is not None and cov[0] == START


def test_point_in_time_read_never_returns_future_bars(tmp_path: Path) -> None:
    repo = MarketDataRepository(tmp_path / "repo")
    provider = MockMarketDataProvider(clock=SimClock(NOW))
    m5 = provider.get_ohlcv("BTCUSDT", Timeframe.M5, START, NOW)
    repo.write_ohlcv(m5)

    for hours in range(6, 72, 6):
        as_of = START + timedelta(hours=hours)
        pit = repo.read_ohlcv("BTCUSDT", Timeframe.M5, START, NOW, as_of=as_of)
        assert pit, f"keine Daten bei as_of={as_of}"
        assert all(b.close_time <= as_of for b in pit)
        assert pit[-1].close_time <= as_of


def test_resample_is_look_ahead_free_vs_future_data(tmp_path: Path) -> None:
    """Eine H1-Bar, die zum Entscheidungszeitpunkt noch nicht geschlossen ist, darf NICHT
    aus (auch korrekten) späteren M5-Bars 'vorweggenommen' werden."""
    provider = MockMarketDataProvider(clock=SimClock(NOW))
    full_m5 = provider.get_ohlcv("BTCUSDT", Timeframe.M5, START, NOW)

    decision_time = START + timedelta(hours=5, minutes=30)  # mitten in der 6. H1-Bar
    visible_m5 = [b for b in full_m5 if b.close_time <= decision_time]

    h1_visible = resample_ohlcv(visible_m5, Timeframe.M5, Timeframe.H1, horizon=decision_time)
    # nur die 5 abgeschlossenen H1-Bars, nicht die laufende 6.
    assert len(h1_visible) == 5
    assert all(b.close_time <= decision_time for b in h1_visible)

    # dieselbe Rechnung auf ALLEN M5-Bars, aber mit Horizont -> identisches Ergebnis
    h1_with_future = resample_ohlcv(full_m5, Timeframe.M5, Timeframe.H1, horizon=decision_time)
    assert [b.close for b in h1_with_future] == [b.close for b in h1_visible]


def test_csv_pipeline_with_news_and_quality(csv_data_dir: Path, tmp_path: Path) -> None:
    clock = SimClock(parse_timestamp("2024-06-20T00:00:00Z"))
    provider = CsvMarketDataProvider(csv_data_dir, clock=clock)
    repo = MarketDataRepository(tmp_path / "repo")

    bars = provider.get_ohlcv(
        "BTCUSDT",
        Timeframe.M5,
        parse_timestamp("2024-06-01T00:00:00Z"),
        parse_timestamp("2024-06-01T01:00:00Z"),
    )
    repo.write_ohlcv(bars)

    # gappy ETH-Serie -> Qualitätsprüfung erkennt die Lücke
    eth = provider.get_ohlcv(
        "ETHUSDT",
        Timeframe.M5,
        parse_timestamp("2024-06-01T00:00:00Z"),
        parse_timestamp("2024-06-01T01:00:00Z"),
    )
    status = check_ohlcv_series(
        eth,
        instrument="ETHUSDT",
        timeframe=Timeframe.M5,
        now=parse_timestamp("2024-06-01T00:35:00Z"),
        calendar=seed_calendars()["crypto_24_7"],
    )
    from trading_agent.core.enums import DataQualityCode

    assert any(i.code is DataQualityCode.GAP for i in status.issues)

    # News point-in-time: die CPI-Revision ist am 12. noch nicht bekannt
    news_at_release = provider.get_news(
        parse_timestamp("2024-06-01T00:00:00Z"),
        parse_timestamp("2024-07-01T00:00:00Z"),
        as_of=parse_timestamp("2024-06-12T13:00:00Z"),
        symbols=["BTCUSDT"],
    )
    cpi = [e for e in news_at_release if e.event_id == "evt-cpi-2024-06"]
    assert cpi and cpi[0].actual == 3.3


def test_dataset_fingerprint_reproducible(tmp_path: Path) -> None:
    """Gleiche Eingaben -> gleicher Datensatz-Fingerprint (Grundlage für RunManifest, Phase 2)."""
    fps = []
    for _ in range(2):
        repo = MarketDataRepository(tmp_path / f"repo_{_}")
        provider = MockMarketDataProvider(clock=SimClock(NOW))
        repo.write_ohlcv(provider.get_ohlcv("BTCUSDT", Timeframe.M5, START, NOW))
        fps.append(repo.dataset_fingerprint("BTCUSDT", Timeframe.M5))
    assert fps[0] == fps[1]
