"""Provider-Health-Tracking.

Verwandelt eine Folge von Erfolgen/Fehlern eines Providers in einen der drei Zustände
``HEALTHY`` / ``DEGRADED`` / ``UNAVAILABLE`` – mit Hysterese, damit einzelne Aussetzer nicht
sofort umschalten.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta

from trading_agent.core.clock import Clock, SystemClock
from trading_agent.core.enums import ProviderHealth
from trading_agent.data.interfaces import ProviderStatus


class HealthPolicy:
    """Schwellen für die Zustandsableitung. Bewusst konservativ."""

    def __init__(
        self,
        *,
        window: int = 20,
        degraded_error_rate: float = 0.20,
        unavailable_error_rate: float = 0.60,
        unavailable_after_consecutive: int = 3,
        min_samples_for_rate: int = 5,
        stale_after: timedelta = timedelta(minutes=15),
    ) -> None:
        self.window = window
        self.degraded_error_rate = degraded_error_rate
        self.unavailable_error_rate = unavailable_error_rate
        self.unavailable_after_consecutive = unavailable_after_consecutive
        self.min_samples_for_rate = min_samples_for_rate
        self.stale_after = stale_after


class HealthTracker:
    """Führt die Health-Historie **eines** Providers."""

    def __init__(
        self,
        provider: str,
        *,
        clock: Clock | None = None,
        policy: HealthPolicy | None = None,
    ) -> None:
        self.provider = provider
        self._clock = clock or SystemClock()
        self._policy = policy or HealthPolicy()
        self._outcomes: deque[bool] = deque(maxlen=self._policy.window)
        self._latencies: deque[float] = deque(maxlen=self._policy.window)
        self._consecutive_failures = 0
        self._last_success: datetime | None = None
        self._last_detail = ""
        self._total = 0

    def record_success(self, *, latency_ms: float | None = None) -> None:
        self._outcomes.append(True)
        if latency_ms is not None:
            self._latencies.append(float(latency_ms))
        self._consecutive_failures = 0
        self._last_success = self._clock.now()
        self._last_detail = ""
        self._total += 1

    def record_failure(self, detail: str = "") -> None:
        self._outcomes.append(False)
        self._consecutive_failures += 1
        self._last_detail = detail
        self._total += 1

    @property
    def error_rate(self) -> float:
        if not self._outcomes:
            return 0.0
        return sum(1 for ok in self._outcomes if not ok) / len(self._outcomes)

    def _latency_p50(self) -> float | None:
        if not self._latencies:
            return None
        ordered = sorted(self._latencies)
        return ordered[len(ordered) // 2]

    def _derive_health(self, now: datetime) -> tuple[ProviderHealth, str]:
        p = self._policy
        if self._total == 0:
            return ProviderHealth.DEGRADED, "noch keine Abfrage"

        if self._consecutive_failures >= p.unavailable_after_consecutive:
            return (
                ProviderHealth.UNAVAILABLE,
                f"{self._consecutive_failures} Fehler in Folge: {self._last_detail}".strip(),
            )

        # Fehlerquoten-Regeln erst ab genug Stichprobe (kleine Fenster sind zu verrauscht).
        rate_meaningful = len(self._outcomes) >= p.min_samples_for_rate
        if rate_meaningful and self.error_rate >= p.unavailable_error_rate:
            return ProviderHealth.UNAVAILABLE, f"Fehlerquote {self.error_rate:.0%}"

        if self._last_success is None:
            return ProviderHealth.DEGRADED, "noch kein Erfolg"
        # laufende Fehlerserie (unter der UNAVAILABLE-Schwelle) -> mindestens DEGRADED
        if self._consecutive_failures >= 1:
            return (
                ProviderHealth.DEGRADED,
                f"{self._consecutive_failures} Fehler in Folge: {self._last_detail}".strip(),
            )
        age = now - self._last_success
        if age > p.stale_after:
            return ProviderHealth.DEGRADED, f"letzter Erfolg vor {age}"
        if rate_meaningful and self.error_rate >= p.degraded_error_rate:
            return ProviderHealth.DEGRADED, f"Fehlerquote {self.error_rate:.0%}"

        return ProviderHealth.HEALTHY, ""

    def status(self) -> ProviderStatus:
        now = self._clock.now()
        health, detail = self._derive_health(now)
        return ProviderStatus(
            provider=self.provider,
            health=health,
            checked_at=now,
            detail=detail,
            last_success_at=self._last_success,
            error_rate=self.error_rate,
            latency_ms_p50=self._latency_p50(),
            consecutive_failures=self._consecutive_failures,
        )


class HealthRegistry:
    """Bündelt die Tracker mehrerer Provider (für Monitoring/Übersicht)."""

    def __init__(self, *, clock: Clock | None = None, policy: HealthPolicy | None = None) -> None:
        self._clock = clock or SystemClock()
        self._policy = policy or HealthPolicy()
        self._trackers: dict[str, HealthTracker] = {}

    def tracker(self, provider: str) -> HealthTracker:
        if provider not in self._trackers:
            self._trackers[provider] = HealthTracker(
                provider, clock=self._clock, policy=self._policy
            )
        return self._trackers[provider]

    def all_status(self) -> list[ProviderStatus]:
        return [t.status() for t in self._trackers.values()]

    def worst(self) -> ProviderHealth:
        statuses = self.all_status()
        if not statuses:
            return ProviderHealth.DEGRADED
        return max((s.health for s in statuses), key=lambda h: h.rank)


__all__ = ["HealthPolicy", "HealthRegistry", "HealthTracker"]
