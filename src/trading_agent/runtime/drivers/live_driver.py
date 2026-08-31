"""LiveDriver — wires a live ingestion source into the event bus (Phase 2B).

Mirror image of ``BacktestDriver``: same events, same subscribers. The difference is the source
(real / synthetic live feed) and the clock (``SystemClock``). Fills, when a strategy eventually
exists, come from ``PaperBroker`` in PAPER_LIVE — **never** a real-money order.
"""

from __future__ import annotations

from trading_agent.data.ingestion.service import IngestionService, LiveSource
from trading_agent.data.repository import MarketDataRepository
from trading_agent.ops.health import SystemHealth
from trading_agent.ops.metrics import MetricsRegistry
from trading_agent.runtime.bus import EventBus


class LiveDriver:
    def __init__(
        self,
        bus: EventBus,
        source: LiveSource,
        repository: MarketDataRepository,
        *,
        health: SystemHealth | None = None,
        metrics: MetricsRegistry | None = None,
    ) -> None:
        self.bus = bus
        self.ingestion = IngestionService(source, repository, bus, health=health, metrics=metrics)

    async def run(self, *, max_bars: int | None = None) -> int:
        return await self.ingestion.run(max_bars=max_bars)

    async def stop(self) -> None:
        await self.ingestion.stop()


__all__ = ["LiveDriver"]
