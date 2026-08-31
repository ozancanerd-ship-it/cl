"""BacktestDriver — replays historical bars from the repository as bus events.

Same events, same subscribers as live. The only difference: a ``SimClock`` advanced bar by bar,
and the source is ``data/repository`` (point-in-time) instead of a live feed.
"""

from __future__ import annotations

from datetime import datetime

from trading_agent.core.clock import SimClock
from trading_agent.core.enums import Timeframe
from trading_agent.core.models import OHLCV
from trading_agent.data.repository import MarketDataRepository
from trading_agent.runtime.bus import EventBus
from trading_agent.runtime.events import BarClosed


class BacktestDriver:
    def __init__(self, bus: EventBus, repository: MarketDataRepository) -> None:
        self.bus = bus
        self.repo = repository
        self.clock: SimClock | None = None
        self.bars_published = 0

    def load(
        self, instrument: str, timeframe: Timeframe, start: datetime, end: datetime
    ) -> list[OHLCV]:
        return self.repo.read_ohlcv(instrument, timeframe, start, end)

    async def run(
        self,
        instrument: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> int:
        bars = self.load(instrument, timeframe, start, end)
        if not bars:
            return 0
        self.clock = SimClock(bars[0].open_time)
        for bar in bars:
            self.clock.set(bar.close_time)
            await self.bus.publish(
                BarClosed(
                    ts=bar.close_time,
                    instrument=instrument.upper(),
                    timeframe=timeframe,
                    bar=bar,
                )
            )
            self.bars_published += 1
        return self.bars_published


__all__ = ["BacktestDriver"]
