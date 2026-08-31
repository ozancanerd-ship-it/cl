"""Live ingestion service.

    Provider/Source  ->  Ingestion  ->  Normalization  ->  Data Quality  ->  Event Bus + Repository

Sources yield already-normalized confirmed bars (WS clients + BarAggregator do that upstream, or
a SyntheticLiveSource replays the repository). The service runs a rolling quality check; if it
finds a blocking (CRITICAL) issue it publishes a ``DataQualityAlert`` and marks the instrument
blocked instead of publishing the bar — the later Strategy Engine turns that into ``NO_TRADE``.
"""

from __future__ import annotations

import logging
from collections import defaultdict, deque
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Protocol

from trading_agent.core.clock import Clock, SystemClock
from trading_agent.core.enums import Timeframe
from trading_agent.core.models import OHLCV
from trading_agent.data.quality import DEFAULT_POLICY, QualityPolicy, check_ohlcv_series
from trading_agent.data.repository import MarketDataRepository
from trading_agent.ops.health import SystemHealth
from trading_agent.ops.metrics import MetricsRegistry
from trading_agent.runtime.bus import EventBus
from trading_agent.runtime.events import BarClosed, DataQualityAlert

_log = logging.getLogger("trading_agent.data.ingestion")


class LiveSource(Protocol):
    name: str

    def stream(self) -> AsyncIterator[OHLCV]: ...

    async def stop(self) -> None: ...


class IngestionService:
    def __init__(
        self,
        source: LiveSource,
        repository: MarketDataRepository,
        bus: EventBus,
        *,
        health: SystemHealth | None = None,
        metrics: MetricsRegistry | None = None,
        quality_policy: QualityPolicy | None = None,
        quality_window: int = 60,
        clock: Clock | None = None,
        on_bar: Callable[[OHLCV], Awaitable[None] | None] | None = None,
    ) -> None:
        self.source = source
        self.repo = repository
        self.bus = bus
        self.health = health or SystemHealth()
        self.metrics = metrics or MetricsRegistry()
        self.policy = quality_policy or DEFAULT_POLICY
        self._window = quality_window
        self._clock = clock or SystemClock()
        self._on_bar = on_bar
        self._history: dict[tuple[str, Timeframe], deque[OHLCV]] = defaultdict(
            lambda: deque(maxlen=quality_window)
        )
        self.bars_ingested = 0
        self.bars_blocked = 0
        self._stopped = False

    async def run(self, *, max_bars: int | None = None) -> int:
        async for bar in self.source.stream():
            if self._stopped:
                break
            await self._handle(bar)
            if max_bars is not None and self.bars_ingested + self.bars_blocked >= max_bars:
                break
        return self.bars_ingested

    async def stop(self) -> None:
        self._stopped = True
        await self.source.stop()

    async def _handle(self, bar: OHLCV) -> None:
        key = (bar.instrument.upper(), bar.timeframe)
        hist = self._history[key]
        hist.append(bar)
        # "now" from the system's perspective at ingestion is the bar's close: it just arrived.
        # (works for both real live feeds and synthetic replay; keeps the staleness check honest.)
        now = bar.close_time

        status = check_ohlcv_series(
            list(hist),
            instrument=bar.instrument.upper(),
            timeframe=bar.timeframe,
            now=now,
            policy=self.policy,
        )
        self.metrics.incr(
            "bars_ingested_total",
            labels={"provider": self.source.name, "instrument": bar.instrument.upper()},
        )

        if status.blocks_trading:
            self.bars_blocked += 1
            self.health.set_data_block(bar.instrument, True)
            self.metrics.incr(
                "data_quality_blocks_total", labels={"instrument": bar.instrument.upper()}
            )
            await self.bus.publish(
                DataQualityAlert(
                    ts=bar.close_time,
                    instrument=bar.instrument.upper(),
                    timeframe=bar.timeframe,
                    status=status,
                )
            )
            _log.warning(
                "data quality blocks trading",
                extra={
                    "instrument": bar.instrument.upper(),
                    "issues": [i.code.value for i in status.issues],
                },
            )
            return

        self.health.set_data_block(bar.instrument, False)
        self.repo.write_ohlcv([bar])
        self.bars_ingested += 1
        await self.bus.publish(
            BarClosed(
                ts=bar.close_time,
                instrument=bar.instrument.upper(),
                timeframe=bar.timeframe,
                bar=bar,
            )
        )
        if self._on_bar is not None:
            res = self._on_bar(bar)
            if res is not None:
                await res


__all__ = ["IngestionService", "LiveSource"]
