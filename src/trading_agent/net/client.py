"""Shared async HTTP client for all external market-data / macro / news providers.

Provides, in one place:

* **rate limiting** (token bucket per client),
* **retry with exponential backoff + jitter** on 429 / 5xx / transport errors,
* **circuit breaker** — after N consecutive failures the client refuses requests for a cooldown,
* **redacted logging** — never logs auth headers or key-like query params.

No provider talks to ``httpx`` directly; they go through ``HttpClient``.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from trading_agent.net.ratelimit import TokenBucket
from trading_agent.utils.logging import redact

_log = logging.getLogger("trading_agent.net")


class NetError(RuntimeError):
    pass


class CircuitOpen(NetError):
    pass


class RetryPolicy:
    def __init__(
        self,
        *,
        max_attempts: int = 4,
        base_delay_s: float = 0.5,
        max_delay_s: float = 8.0,
        retry_status: tuple[int, ...] = (429, 500, 502, 503, 504),
    ) -> None:
        self.max_attempts = max_attempts
        self.base_delay_s = base_delay_s
        self.max_delay_s = max_delay_s
        self.retry_status = retry_status

    def delay(self, attempt: int) -> float:
        raw: float = min(self.max_delay_s, self.base_delay_s * (2 ** (attempt - 1)))
        return raw * (0.5 + random.random() / 2.0)  # jitter 50-100 %


class _Circuit:
    def __init__(self, *, threshold: int, cooldown_s: float, now: Callable[[], float]) -> None:
        self.threshold = threshold
        self.cooldown_s = cooldown_s
        self._now = now
        self._failures = 0
        self._opened_at: float | None = None

    def check(self) -> None:
        if self._opened_at is None:
            return
        if self._now() - self._opened_at >= self.cooldown_s:
            self._opened_at = None
            self._failures = 0
        else:
            raise CircuitOpen(
                f"circuit open, retry in {self.cooldown_s - (self._now() - self._opened_at):.0f}s"
            )

    def record_success(self) -> None:
        self._failures = 0
        self._opened_at = None

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self.threshold:
            self._opened_at = self._now()

    @property
    def is_open(self) -> bool:
        return self._opened_at is not None


class HttpClient:
    def __init__(
        self,
        base_url: str = "",
        *,
        name: str = "http",
        rate_per_sec: float = 5.0,
        rate_capacity: float | None = None,
        retry: RetryPolicy | None = None,
        circuit_threshold: int = 5,
        circuit_cooldown_s: float = 60.0,
        timeout_s: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
        headers: dict[str, str] | None = None,
        now: Callable[[], float] | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        import time as _time

        self.name = name
        self._now: Callable[[], float] = now or _time.monotonic
        self._sleep = sleep
        self._retry = retry or RetryPolicy()
        self._bucket = TokenBucket(rate_per_sec, rate_capacity, now=self._now, sleep=sleep)
        self._circuit = _Circuit(
            threshold=circuit_threshold, cooldown_s=circuit_cooldown_s, now=self._now
        )
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout_s,
            transport=transport,
            headers=headers or {},
        )

    async def __aenter__(self) -> HttpClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return await self._request("GET", path, params=params)

    async def _request(
        self, method: str, path: str, *, params: dict[str, Any] | None = None
    ) -> Any:
        self._circuit.check()
        last_exc: Exception | None = None
        for attempt in range(1, self._retry.max_attempts + 1):
            await self._bucket.acquire()
            try:
                resp = await self._client.request(method, path, params=params)
            except httpx.TransportError as exc:
                last_exc = exc
                self._circuit.record_failure()
                _log.warning(
                    "transport error",
                    extra={"provider": self.name, "path": path, "attempt": attempt},
                )
            else:
                if resp.status_code in self._retry.retry_status:
                    last_exc = NetError(f"HTTP {resp.status_code}")
                    self._circuit.record_failure()
                    _log.warning(
                        "retryable status",
                        extra={
                            "provider": self.name,
                            "path": path,
                            "status": resp.status_code,
                            "attempt": attempt,
                        },
                    )
                elif resp.is_error:
                    self._circuit.record_failure()
                    raise NetError(
                        f"{self.name} {method} {path} -> HTTP {resp.status_code}: "
                        f"{redact(resp.text[:300])}"
                    )
                else:
                    self._circuit.record_success()
                    return resp.json()

            if attempt < self._retry.max_attempts:
                await self._sleep(self._retry.delay(attempt))

        self._circuit.record_failure()
        raise NetError(
            f"{self.name} {method} {path} failed after {self._retry.max_attempts} attempts"
        ) from last_exc


__all__ = ["CircuitOpen", "HttpClient", "NetError", "RetryPolicy"]
