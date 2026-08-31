"""Provider router: pick the best data source for (asset class, data kind, mode).

Order of precedence:
1. explicit ``routes`` entry in the config (primary + fallback chain),
2. otherwise the registry's capability ranking.

Providers that are disabled, or whose health is below the configured threshold, are skipped —
the router walks the fallback chain until it finds a usable one.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from trading_agent.core.enums import AssetClass, DataKind, ProviderHealth
from trading_agent.data.registry import ProviderRegistry, ProviderRoute

HealthLookup = Callable[[str], ProviderHealth]


class NoProviderAvailable(RuntimeError):
    pass


class ProviderRouter:
    def __init__(
        self,
        registry: ProviderRegistry,
        config: dict[str, Any] | None = None,
        *,
        health: HealthLookup | None = None,
    ) -> None:
        self.registry = registry
        cfg = config or {}
        router_cfg = cfg.get("router", cfg) if "router" in cfg else cfg
        self._routes: dict[str, dict[str, ProviderRoute]] = {}
        for asset_class, kinds in (router_cfg.get("routes") or {}).items():
            self._routes[asset_class] = {
                kind: ProviderRoute(**spec) for kind, spec in kinds.items()
            }
        avoid = (router_cfg.get("health") or {}).get("avoid_below", "healthy")
        self._avoid_rank = ProviderHealth(avoid).rank
        self._health = health or (lambda _name: ProviderHealth.HEALTHY)

    def _usable(self, name: str) -> bool:
        if not self.registry.has(name) or not self.registry.get(name).enabled:
            return False
        return self._health(name).rank <= self._avoid_rank

    def candidates(
        self,
        asset_class: AssetClass | str,
        data_kind: DataKind | str,
        mode: str,
    ) -> list[str]:
        """Ordered provider names to try (best first), before health filtering."""
        ac = asset_class.value if isinstance(asset_class, AssetClass) else str(asset_class)
        dk = data_kind.value if isinstance(data_kind, DataKind) else str(data_kind)

        route = self._routes.get(ac, {}).get(dk) or self._routes.get(ac, {}).get(mode)
        if route is not None:
            return [route.primary, *route.fallback]
        return [c.name for c in self.registry.find(ac, dk, mode, only_enabled=False)]

    def resolve(
        self,
        asset_class: AssetClass | str,
        data_kind: DataKind | str,
        mode: str,
    ) -> str:
        """The single provider to use right now (first usable candidate). Raises if none."""
        tried = self.candidates(asset_class, data_kind, mode)
        for name in tried:
            if self._usable(name):
                return name
        raise NoProviderAvailable(
            f"no usable provider for ({asset_class}, {data_kind}, {mode}); tried {tried}"
        )

    def resolve_all(
        self,
        asset_class: AssetClass | str,
        data_kind: DataKind | str,
        mode: str,
    ) -> list[str]:
        return [n for n in self.candidates(asset_class, data_kind, mode) if self._usable(n)]


__all__ = ["HealthLookup", "NoProviderAvailable", "ProviderRouter"]
