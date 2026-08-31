"""ScannerShell — the 24/7 autonomous market observer (Phase 2B).

Subscribes to ``BarClosed``, keeps a rolling per-instrument history, and calls ``evaluate()``.
In Phase 2B ``evaluate()`` is a **placeholder** that only logs and counts — the real Strategy
Engine (``strategy.evaluate(MarketContext) -> Decision``) slots in at Phase 3 via the same hook.

**Never** produces an order. It exists to prove the pipeline observes the market continuously
while the user does nothing.
"""

from __future__ import annotations

import logging
from collections import defaultdict, deque
from collections.abc import Callable

from trading_agent.core.enums import Timeframe
from trading_agent.core.models import OHLCV
from trading_agent.ops.metrics import MetricsRegistry
from trading_agent.runtime.bus import EventBus
from trading_agent.runtime.events import BarClosed, MarketObserved

_log = logging.getLogger("trading_agent.scanner")

# Phase 3 replaces this with the real strategy.evaluate. Returns None here by contract.
EvaluateHook = Callable[[list[OHLCV]], None]


def _placeholder_evaluate(history: list[OHLCV]) -> None:
    return None


class ScannerShell:
    def __init__(
        self,
        bus: EventBus,
        *,
        history_len: int = 300,
        evaluate: EvaluateHook | None = None,
        metrics: MetricsRegistry | None = None,
        priority: dict[str, int] | None = None,
    ) -> None:
        self.bus = bus
        self._history: dict[tuple[str, Timeframe], deque[OHLCV]] = defaultdict(
            lambda: deque(maxlen=history_len)
        )
        self._evaluate = evaluate or _placeholder_evaluate
        self.metrics = metrics or MetricsRegistry()
        self._priority = priority or {}
        self.observations = 0
        bus.subscribe(BarClosed, self._on_bar)

    async def _on_bar(self, ev: BarClosed) -> None:
        bar = ev.bar
        assert bar is not None
        key = (bar.instrument.upper(), bar.timeframe)
        self._history[key].append(bar)
        hist = list(self._history[key])

        self._evaluate(hist)  # placeholder in 2B; real engine in 3

        self.observations += 1
        tier = self._priority.get(bar.instrument.upper(), 3)
        self.metrics.incr(
            "market_observed_total",
            labels={"instrument": bar.instrument.upper(), "tier": str(tier)},
        )
        self.metrics.gauge(
            "scanner_history_len",
            float(len(hist)),
            labels={"instrument": bar.instrument.upper()},
        )
        await self.bus.publish(
            MarketObserved(
                ts=bar.close_time,
                instrument=bar.instrument.upper(),
                timeframe=bar.timeframe,
                note="placeholder-evaluate (strategy engine arrives phase 3)",
            )
        )
        if self.observations % 200 == 0:
            _log.info(
                "market observed",
                extra={"count": self.observations, "instrument": bar.instrument.upper()},
            )


__all__ = ["EvaluateHook", "ScannerShell"]
