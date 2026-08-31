"""Phase 2B integration: the 24/7 paper-live backbone.

Provider(synthetic) -> Ingestion -> Normalization -> Data Quality -> Event Bus -> Scanner shell.
Asserts: bars flow, quality veto blocks bad bars, scanner observes continuously, metrics tick,
supervisor lifecycle + graceful shutdown, and **no order is ever sent**.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from trading_agent.core.clock import FixedClock
from trading_agent.core.enums import DataQualityCode, Timeframe
from trading_agent.core.time import parse_timestamp
from trading_agent.data.ingestion.service import IngestionService
from trading_agent.data.ingestion.sources import SyntheticLiveSource
from trading_agent.data.providers.mock_provider import MockMarketDataProvider
from trading_agent.data.repository import MarketDataRepository
from trading_agent.ops.health import SystemHealth
from trading_agent.ops.metrics import MetricsRegistry
from trading_agent.runtime.bus import EventBus
from trading_agent.runtime.events import (
    BarClosed,
    DataQualityAlert,
    MarketObserved,
    ShutdownRequested,
)
from trading_agent.runtime.supervisor import Supervisor
from trading_agent.scanner.scanner import ScannerShell

pytestmark = pytest.mark.integration

START = parse_timestamp("2024-06-01T00:00:00Z")
END = parse_timestamp("2024-06-04T00:00:00Z")


def _bars(n_days: int = 3):
    mp = MockMarketDataProvider(clock=FixedClock(END), volatility=0.005)
    return mp.get_ohlcv("BTCUSDT", Timeframe.M15, START, END)


async def test_full_paper_live_pipeline(tmp_path: Path) -> None:
    repo = MarketDataRepository(tmp_path / "repo")
    bus = EventBus(raise_on_handler_error=True)
    health = SystemHealth()
    metrics = MetricsRegistry()

    bars = _bars()
    source = SyntheticLiveSource(bars)
    ingestion = IngestionService(source, repo, bus, health=health, metrics=metrics)
    scanner = ScannerShell(bus, metrics=metrics, priority={"BTCUSDT": 1})

    observed: list[str] = []
    bus.subscribe(MarketObserved, lambda e: observed.append(e.instrument))
    shutdowns: list[str] = []
    bus.subscribe(ShutdownRequested, lambda e: shutdowns.append(e.reason))

    supervisor = Supervisor(bus, ingestion, health=health, metrics=metrics)
    await supervisor.run()

    # bars flowed end to end
    assert ingestion.bars_ingested == len(bars)
    assert scanner.observations == len(bars)
    assert observed and all(s == "BTCUSDT" for s in observed)
    # persisted to the repository
    assert len(repo.read_ohlcv("BTCUSDT", Timeframe.M15, START, END)) == len(bars)
    # metrics ticked
    assert metrics.counter_value(
        "bars_ingested_total", {"provider": "synthetic_live", "instrument": "BTCUSDT"}
    ) == len(bars)
    assert metrics.counter_value(
        "market_observed_total", {"instrument": "BTCUSDT", "tier": "1"}
    ) == len(bars)
    # lifecycle + fail-safe
    assert supervisor.started and supervisor.stopped
    assert shutdowns == ["ingestion finished"]
    assert health.kill_switch_engaged is False  # released after startup checks
    # THE invariant: no order was ever sent
    assert supervisor.orders_sent == 0
    assert supervisor.status()["orders_sent"] == 0


async def test_data_quality_veto_blocks_bad_bar(tmp_path: Path) -> None:
    repo = MarketDataRepository(tmp_path / "repo")
    bus = EventBus()
    health = SystemHealth()

    good = _bars()[:20]
    # a glitchy feed delivers a bar out of order at the end -> OUT_OF_ORDER is CRITICAL
    out_of_order = good[5].model_copy(update={"close": good[5].close + 1.0})
    source = SyntheticLiveSource([*good, out_of_order])
    ingestion = IngestionService(source, repo, bus, health=health)

    alerts: list[DataQualityAlert] = []
    bus.subscribe(DataQualityAlert, lambda e: alerts.append(e))
    published_bars: list[BarClosed] = []
    bus.subscribe(BarClosed, lambda e: published_bars.append(e))

    await ingestion.run()

    assert ingestion.bars_blocked >= 1
    assert alerts
    assert any(
        i.code in {DataQualityCode.OUT_OF_ORDER, DataQualityCode.DUPLICATE_BAR, DataQualityCode.GAP}
        for a in alerts
        for i in (a.status.issues if a.status else [])
    )
    # the bad bar was NOT published as a tradeable BarClosed
    assert len(published_bars) == ingestion.bars_ingested
    assert ingestion.bars_ingested == len(good)


async def test_supervisor_graceful_early_shutdown(tmp_path: Path) -> None:
    repo = MarketDataRepository(tmp_path / "repo")
    bus = EventBus()
    source = SyntheticLiveSource(_bars())
    ingestion = IngestionService(source, repo, bus)
    supervisor = Supervisor(bus, ingestion)

    await supervisor.run(max_bars=10)
    assert ingestion.bars_ingested == 10
    assert supervisor.stopped
    assert supervisor.orders_sent == 0


async def test_no_broker_adapter_is_live_capable_in_paper_live() -> None:
    from trading_agent.execution.brokers.paper import PaperBroker
    from trading_agent.execution.router import BrokerRouter, LiveOrderBlocked
    from trading_agent.refdata.seed import build_instrument_master

    router = BrokerRouter(mode="paper_live")
    router.register(PaperBroker(build_instrument_master()))  # ok

    class _Live(PaperBroker):
        is_live_capable = True

    with pytest.raises(LiveOrderBlocked):
        router.register(_Live(build_instrument_master()))
    _ = timedelta
