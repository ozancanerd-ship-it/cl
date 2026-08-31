"""Tests: Provider-Health – HEALTHY / DEGRADED / UNAVAILABLE mit Hysterese."""

from __future__ import annotations

from datetime import timedelta

from trading_agent.core.clock import SimClock
from trading_agent.core.enums import ProviderHealth
from trading_agent.core.time import parse_timestamp
from trading_agent.data.health import HealthPolicy, HealthRegistry, HealthTracker


def _clock() -> SimClock:
    return SimClock(parse_timestamp("2024-06-10T00:00:00Z"))


def test_initial_state_is_degraded() -> None:
    t = HealthTracker("p", clock=_clock())
    assert t.status().health is ProviderHealth.DEGRADED


def test_success_makes_healthy() -> None:
    t = HealthTracker("p", clock=_clock())
    t.record_success(latency_ms=5.0)
    st = t.status()
    assert st.health is ProviderHealth.HEALTHY
    assert st.last_success_at is not None
    assert st.latency_ms_p50 == 5.0


def test_consecutive_failures_make_unavailable() -> None:
    t = HealthTracker("p", clock=_clock(), policy=HealthPolicy(unavailable_after_consecutive=3))
    t.record_success()
    t.record_failure("boom")
    t.record_failure("boom")
    assert t.status().health is ProviderHealth.DEGRADED
    t.record_failure("boom")
    st = t.status()
    assert st.health is ProviderHealth.UNAVAILABLE
    assert st.consecutive_failures == 3


def test_recovery_after_success() -> None:
    t = HealthTracker("p", clock=_clock(), policy=HealthPolicy(unavailable_after_consecutive=2))
    t.record_failure("x")
    t.record_failure("x")
    assert t.status().health is ProviderHealth.UNAVAILABLE
    t.record_success()
    assert t.status().health in (ProviderHealth.HEALTHY, ProviderHealth.DEGRADED)


def test_error_rate_degrades() -> None:
    t = HealthTracker("p", clock=_clock(), policy=HealthPolicy(window=10, degraded_error_rate=0.2))
    for _ in range(7):
        t.record_success()
    for _ in range(3):
        t.record_failure("x")
    t.record_success()  # bricht die Serie, aber Fehlerquote im Fenster ~0.27
    assert t.status().health is ProviderHealth.DEGRADED


def test_staleness_degrades() -> None:
    clk = _clock()
    t = HealthTracker("p", clock=clk, policy=HealthPolicy(stale_after=timedelta(minutes=10)))
    t.record_success()
    assert t.status().health is ProviderHealth.HEALTHY
    clk.advance(timedelta(minutes=20))
    assert t.status().health is ProviderHealth.DEGRADED


def test_registry_worst() -> None:
    reg = HealthRegistry(clock=_clock(), policy=HealthPolicy(unavailable_after_consecutive=1))
    reg.tracker("good").record_success()
    reg.tracker("bad").record_failure("down")
    assert reg.worst() is ProviderHealth.UNAVAILABLE
    assert len(reg.all_status()) == 2
