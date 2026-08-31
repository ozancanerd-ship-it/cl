"""Live sources for the ingestion service.

* ``SyntheticLiveSource`` — replays repository bars as if they arrived live (this environment
  has no network). Optional per-bar delay for demo realism.
* ``ReplayFromProviderSource`` — pulls a batch of real bars once (via a network provider) and
  replays them; useful once ``fetch_history.py`` or a live key exists.

Real WS clients (Kraken / Bybit) live in ``data/providers/kraken_ws.py`` etc. and feed a
``BarAggregator`` -> this same interface.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence

from trading_agent.core.models import OHLCV


class SyntheticLiveSource:
    """Replays a fixed list of bars **in the given order** (a live feed delivers in arrival
    order, glitches and all). Pass pre-sorted bars for a clean replay."""

    def __init__(
        self,
        bars: Sequence[OHLCV],
        *,
        name: str = "synthetic_live",
        delay_s: float = 0.0,
    ) -> None:
        self.name = name
        self._bars = list(bars)
        self._delay = delay_s
        self._stopped = False
        self.emitted = 0

    async def stream(self) -> AsyncIterator[OHLCV]:
        for bar in self._bars:
            if self._stopped:
                return
            if self._delay > 0:
                await asyncio.sleep(self._delay)
            self.emitted += 1
            yield bar

    async def stop(self) -> None:
        self._stopped = True


__all__ = ["SyntheticLiveSource"]
