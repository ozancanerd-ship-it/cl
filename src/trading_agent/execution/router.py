"""BrokerRouter — the only path from trading logic to a broker.

    Strategy  ->  Risk  ->  BrokerRouter  ->  BrokerAdapter  ->  Broker/Platform

Guarantees:
* Routes ``OrderIntent`` to the right adapter per instrument (primary + fallback chain).
* Skips adapters whose ``BrokerHealth`` is below threshold (broker-health veto).
* **Refuses to route to a real-money adapter** unless ``mode`` is an explicit live mode
  (which is disallowed in every phase up to 13) — in ``backtest`` / ``paper`` / ``paper_live``
  only non-live-capable adapters (``PaperBroker``) are allowed.
"""

from __future__ import annotations

from trading_agent.core.enums import ProviderHealth
from trading_agent.execution.brokers.base import BrokerAdapter, OrderIntent

_SIM_MODES = {"backtest", "paper", "paper_live", "development", "research"}
_LIVE_MODES = {"demo", "live"}


class BrokerRoutingError(RuntimeError):
    pass


class NoBrokerAvailable(BrokerRoutingError):
    pass


class LiveOrderBlocked(BrokerRoutingError):
    pass


class BrokerRouter:
    def __init__(
        self,
        *,
        mode: str = "paper_live",
        routes: dict[str, dict[str, list[str] | str]] | None = None,
        avoid_below: ProviderHealth = ProviderHealth.HEALTHY,
    ) -> None:
        self.mode = mode
        self._adapters: dict[str, BrokerAdapter] = {}
        self._routes = routes or {}
        self._avoid_rank = avoid_below.rank

    def register(self, adapter: BrokerAdapter) -> None:
        if self.mode in _SIM_MODES and adapter.is_live_capable:
            raise LiveOrderBlocked(
                f"mode={self.mode!r} forbids registering live-capable adapter {adapter.name!r}"
            )
        self._adapters[adapter.name] = adapter

    def _route_names(self, instrument: str) -> list[str]:
        inst = instrument.upper()
        # exact instrument route, else a "crypto:*" style wildcard, else all adapters that list it
        entry = self._routes.get(inst) or self._routes.get(f"crypto:{inst}")
        if entry is None:
            for key, spec in self._routes.items():
                if key.endswith(":*") and (
                    inst.upper().startswith(key[:-2].upper()) or key.startswith("crypto")
                ):
                    entry = spec
                    break
        if entry is not None:
            primary = entry.get("primary")
            fallback = entry.get("fallback") or []
            names = [primary, *fallback] if isinstance(fallback, list) else [primary, fallback]
            return [n for n in names if isinstance(n, str)]
        return [n for n, a in self._adapters.items() if not a.instruments or inst in a.instruments]

    def adapter_for(self, instrument: str) -> BrokerAdapter:
        tried = self._route_names(instrument)
        for name in tried:
            adapter = self._adapters.get(name)
            if adapter is None:
                continue
            if adapter.health().health.rank <= self._avoid_rank:
                return adapter
        raise NoBrokerAvailable(
            f"no healthy broker for {instrument!r}; tried {tried} (registered: {list(self._adapters)})"
        )

    async def submit(self, intent: OrderIntent) -> str:
        if self.mode in _LIVE_MODES:  # pragma: no cover - live is blocked in every current phase
            raise LiveOrderBlocked(f"live order submission is disabled (mode={self.mode!r})")
        adapter = self.adapter_for(intent.instrument)
        return await adapter.submit(intent)

    async def cancel(self, instrument: str, client_order_id: str) -> None:
        await self.adapter_for(instrument).cancel(client_order_id)


__all__ = [
    "BrokerRouter",
    "BrokerRoutingError",
    "LiveOrderBlocked",
    "NoBrokerAvailable",
]
