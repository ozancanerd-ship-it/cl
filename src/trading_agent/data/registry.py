"""Provider registry: which provider can supply what, and at what cost / license / latency."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from trading_agent.config.loader import load_yaml
from trading_agent.core.enums import AssetClass, DataKind


class ProviderCapability(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)

    name: str
    kind: str = "unknown"
    role: str = "secondary"  # primary | secondary
    asset_classes: tuple[str, ...] = ()
    data_kinds: tuple[str, ...] = ()
    modes: tuple[str, ...] = ()
    auth: str = "none"
    platform_only: str | None = None
    historical_depth_days: int = 0
    typical_latency_ms: float | None = None
    cost_tier: str = "free"
    redistribution_allowed: bool = False
    enabled: bool = False
    rest_base: str | None = None
    ws_base: str | None = None

    def supports(self, asset_class: str, data_kind: str, mode: str) -> bool:
        return (
            asset_class in self.asset_classes
            and data_kind in self.data_kinds
            and mode in self.modes
        )


class ProviderRegistry:
    def __init__(self, capabilities: Iterable[ProviderCapability] = ()) -> None:
        self._caps: dict[str, ProviderCapability] = {}
        for cap in capabilities:
            self.add(cap)

    def add(self, cap: ProviderCapability) -> None:
        self._caps[cap.name] = cap

    def get(self, name: str) -> ProviderCapability:
        return self._caps[name]

    def has(self, name: str) -> bool:
        return name in self._caps

    def all(self) -> list[ProviderCapability]:
        return list(self._caps.values())

    def find(
        self,
        asset_class: AssetClass | str,
        data_kind: DataKind | str,
        mode: str,
        *,
        only_enabled: bool = True,
    ) -> list[ProviderCapability]:
        ac = asset_class.value if isinstance(asset_class, AssetClass) else str(asset_class)
        dk = data_kind.value if isinstance(data_kind, DataKind) else str(data_kind)
        matches = [
            c
            for c in self._caps.values()
            if c.supports(ac, dk, mode) and (c.enabled or not only_enabled)
        ]
        # primary before secondary, then lower latency, then free before paid
        matches.sort(
            key=lambda c: (
                0 if c.role == "primary" else 1,
                c.typical_latency_ms if c.typical_latency_ms is not None else 1e9,
                0 if c.cost_tier == "free" else 1,
            )
        )
        return matches

    @classmethod
    def from_config(cls, cfg: dict[str, Any]) -> ProviderRegistry:
        caps = [
            ProviderCapability(name=name, **spec)
            for name, spec in (cfg.get("providers") or {}).items()
        ]
        return cls(caps)

    @classmethod
    def from_file(cls, path: str | Path) -> tuple[ProviderRegistry, dict[str, Any]]:
        cfg = load_yaml(path)
        return cls.from_config(cfg), cfg


class ProviderRoute(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    primary: str
    fallback: tuple[str, ...] = ()


class RouterConfig(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)

    routes: dict[str, dict[str, ProviderRoute]] = Field(default_factory=dict)
    health: dict[str, object] = Field(default_factory=dict)


__all__ = ["ProviderCapability", "ProviderRegistry", "ProviderRoute", "RouterConfig"]
