"""SystemHealth — one aggregate view of provider health, broker health, data quality,
kill-switch state and the last heartbeat. Feeds the dashboard and the broker/data-health veto.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from trading_agent.core.clock import Clock, SystemClock
from trading_agent.core.enums import ProviderHealth


@dataclass
class SystemHealth:
    clock: Clock = field(default_factory=SystemClock)
    stale_heartbeat_after: timedelta = timedelta(seconds=60)

    _provider_health: dict[str, ProviderHealth] = field(default_factory=dict)
    _broker_health: dict[str, ProviderHealth] = field(default_factory=dict)
    _last_heartbeat: datetime | None = None
    _data_blocks: set[str] = field(default_factory=set)
    kill_switch_engaged: bool = True  # fail-safe: engaged until the supervisor releases it

    # ---------------------------------------------------------------- updates

    def set_provider_health(self, name: str, health: ProviderHealth) -> None:
        self._provider_health[name] = health

    def set_broker_health(self, name: str, health: ProviderHealth) -> None:
        self._broker_health[name] = health

    def heartbeat(self) -> None:
        self._last_heartbeat = self.clock.now()

    def set_data_block(self, instrument: str, blocked: bool) -> None:
        if blocked:
            self._data_blocks.add(instrument.upper())
        else:
            self._data_blocks.discard(instrument.upper())

    # ---------------------------------------------------------------- queries

    @property
    def heartbeat_stale(self) -> bool:
        if self._last_heartbeat is None:
            return True
        return self.clock.now() - self._last_heartbeat > self.stale_heartbeat_after

    def worst_provider(self) -> ProviderHealth:
        vals = list(self._provider_health.values())
        return max(vals, key=lambda h: h.rank) if vals else ProviderHealth.DEGRADED

    def worst_broker(self) -> ProviderHealth:
        vals = list(self._broker_health.values())
        return max(vals, key=lambda h: h.rank) if vals else ProviderHealth.DEGRADED

    def data_blocked(self, instrument: str) -> bool:
        return instrument.upper() in self._data_blocks

    @property
    def ok(self) -> bool:
        return (
            not self.kill_switch_engaged
            and not self.heartbeat_stale
            and self.worst_provider() is not ProviderHealth.UNAVAILABLE
        )

    def snapshot(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "kill_switch_engaged": self.kill_switch_engaged,
            "heartbeat_stale": self.heartbeat_stale,
            "last_heartbeat": self._last_heartbeat.isoformat() if self._last_heartbeat else None,
            "providers": {k: v.value for k, v in self._provider_health.items()},
            "brokers": {k: v.value for k, v in self._broker_health.items()},
            "data_blocked": sorted(self._data_blocks),
        }


HealthProvider = Callable[[], SystemHealth]

__all__ = ["HealthProvider", "SystemHealth"]
