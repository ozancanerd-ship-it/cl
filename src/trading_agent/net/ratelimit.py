"""Token-bucket rate limiter (async).

One bucket per provider/endpoint. ``acquire`` blocks until enough tokens are available.
Time and sleep are injectable so tests run without real waiting.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable


class TokenBucket:
    def __init__(
        self,
        rate_per_sec: float,
        capacity: float | None = None,
        *,
        now: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if rate_per_sec <= 0:
            raise ValueError("rate_per_sec must be > 0")
        self.rate = rate_per_sec
        self.capacity = capacity if capacity is not None else max(1.0, rate_per_sec)
        self._tokens = self.capacity
        self._now = now
        self._sleep = sleep
        self._last = now()
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        t = self._now()
        elapsed = t - self._last
        if elapsed > 0:
            self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
            self._last = t

    async def acquire(self, tokens: float = 1.0) -> None:
        if tokens > self.capacity:
            raise ValueError(f"requested {tokens} tokens > capacity {self.capacity}")
        async with self._lock:
            while True:
                self._refill()
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return
                deficit = tokens - self._tokens
                await self._sleep(deficit / self.rate)

    @property
    def available(self) -> float:
        self._refill()
        return self._tokens


__all__ = ["TokenBucket"]
