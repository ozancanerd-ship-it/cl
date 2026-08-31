"""Tests: token-bucket rate limiter + HttpClient (retry, circuit breaker) — no real network."""

from __future__ import annotations

import httpx
import pytest
import respx

from trading_agent.net.client import CircuitOpen, HttpClient, NetError, RetryPolicy
from trading_agent.net.ratelimit import TokenBucket


class FakeTime:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


async def test_token_bucket_blocks_until_refill() -> None:
    clock = FakeTime()
    slept: list[float] = []

    async def sleep(d: float) -> None:
        slept.append(d)
        clock.t += d

    tb = TokenBucket(2.0, capacity=2.0, now=clock, sleep=sleep)
    await tb.acquire()
    await tb.acquire()  # bucket now empty
    await tb.acquire()  # must wait ~0.5s for 1 token at 2/s
    assert slept and abs(slept[0] - 0.5) < 1e-6


async def test_token_bucket_rejects_over_capacity() -> None:
    tb = TokenBucket(1.0, capacity=1.0)
    with pytest.raises(ValueError):
        await tb.acquire(5.0)


@respx.mock
async def test_http_client_returns_json() -> None:
    respx.get("https://x.test/ping").mock(return_value=httpx.Response(200, json={"ok": 1}))
    async with HttpClient("https://x.test", name="t", rate_per_sec=1000) as c:
        assert await c.get_json("/ping") == {"ok": 1}


@respx.mock
async def test_http_client_retries_then_succeeds() -> None:
    route = respx.get("https://x.test/flaky")
    route.side_effect = [
        httpx.Response(503),
        httpx.Response(429),
        httpx.Response(200, json={"ok": True}),
    ]
    async with HttpClient(
        "https://x.test",
        name="t",
        rate_per_sec=1000,
        retry=RetryPolicy(max_attempts=4, base_delay_s=0.0),
        sleep=_noop_sleep,
    ) as c:
        assert await c.get_json("/flaky") == {"ok": True}
    assert route.call_count == 3


@respx.mock
async def test_http_client_circuit_opens_after_failures() -> None:
    respx.get("https://x.test/down").mock(return_value=httpx.Response(500))
    async with HttpClient(
        "https://x.test",
        name="t",
        rate_per_sec=1000,
        retry=RetryPolicy(max_attempts=1, base_delay_s=0.0),
        circuit_threshold=2,
        circuit_cooldown_s=999,
        sleep=_noop_sleep,
    ) as c:
        for _ in range(2):
            with pytest.raises(NetError):
                await c.get_json("/down")
        with pytest.raises(CircuitOpen):
            await c.get_json("/down")


@respx.mock
async def test_http_client_4xx_not_retried() -> None:
    route = respx.get("https://x.test/bad").mock(return_value=httpx.Response(404))
    async with HttpClient("https://x.test", name="t", rate_per_sec=1000, sleep=_noop_sleep) as c:
        with pytest.raises(NetError):
            await c.get_json("/bad")
    assert route.call_count == 1


async def _noop_sleep(_: float) -> None:
    return None
