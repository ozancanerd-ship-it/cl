"""Trade / tick -> OHLCV bar aggregation.

Feeds the live ingestion service: exchange trade streams become clean, timeframe-aligned bars.
Only **confirmed** bars (``is_final``) are emitted — the still-forming bar is kept internally.
"""

from __future__ import annotations

from datetime import datetime

from trading_agent.core.clock import Clock, SystemClock
from trading_agent.core.enums import Timeframe
from trading_agent.core.models import OHLCV, Trade
from trading_agent.core.time import align_down, bar_close_time, ensure_utc


class BarAggregator:
    def __init__(
        self,
        instrument: str,
        timeframe: Timeframe,
        *,
        source: str = "aggregated",
        clock: Clock | None = None,
    ) -> None:
        self.instrument = instrument.upper()
        self.timeframe = timeframe
        self.source = source
        self._clock = clock or SystemClock()
        self._open_time: datetime | None = None
        self._o = self._h = self._low = self._c = 0.0
        self._v = 0.0
        self._n = 0

    def _start(self, open_time: datetime, price: float) -> None:
        self._open_time = open_time
        self._o = self._h = self._low = self._c = price
        self._v = 0.0
        self._n = 0

    def add_price(self, ts: datetime, price: float, volume: float = 0.0) -> list[OHLCV]:
        ts = ensure_utc(ts)
        slot = align_down(ts, self.timeframe)
        emitted: list[OHLCV] = []

        if self._open_time is None:
            self._start(slot, price)
        elif slot > self._open_time:
            emitted.append(self._finalize())
            self._start(slot, price)

        self._h = max(self._h, price)
        self._low = min(self._low, price)
        self._c = price
        self._v += volume
        self._n += 1
        return emitted

    def add_trade(self, trade: Trade) -> list[OHLCV]:
        return self.add_price(trade.ts, trade.price, trade.size)

    def poll(self, now: datetime | None = None) -> list[OHLCV]:
        """Finalize the current bar if its close time has passed (no trades in the new slot)."""
        moment = ensure_utc(now) if now is not None else self._clock.now()
        if self._open_time is None:
            return []
        if bar_close_time(self._open_time, self.timeframe) <= moment:
            bar = self._finalize()
            self._open_time = None
            return [bar]
        return []

    def _finalize(self) -> OHLCV:
        assert self._open_time is not None
        return OHLCV(
            instrument=self.instrument,
            timeframe=self.timeframe,
            open_time=self._open_time,
            close_time=bar_close_time(self._open_time, self.timeframe),
            open=self._o,
            high=self._h,
            low=self._low,
            close=self._c,
            volume=round(self._v, 8),
            trades=self._n or None,
            source=self.source,
            ingested_at=self._clock.now(),
        )

    @property
    def forming(self) -> OHLCV | None:
        """The still-open bar (never emit this to strategy)."""
        if self._open_time is None:
            return None
        return OHLCV(
            instrument=self.instrument,
            timeframe=self.timeframe,
            open_time=self._open_time,
            close_time=bar_close_time(self._open_time, self.timeframe),
            open=self._o,
            high=self._h,
            low=self._low,
            close=self._c,
            volume=round(self._v, 8),
            source=self.source,
        )


__all__ = ["BarAggregator"]
