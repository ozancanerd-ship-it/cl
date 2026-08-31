"""Tests: provider registry + router (capability matching, fallback, health, config)."""

from __future__ import annotations

from pathlib import Path

from trading_agent.core.enums import ProviderHealth
from trading_agent.data.registry import ProviderCapability, ProviderRegistry
from trading_agent.data.router import NoProviderAvailable, ProviderRouter

REPO_PROVIDERS = Path(__file__).parents[2] / "config" / "providers.example.yaml"


def _reg() -> tuple[ProviderRegistry, dict]:
    return ProviderRegistry.from_file(REPO_PROVIDERS)


def test_registry_loads_example_config() -> None:
    reg, _ = _reg()
    assert reg.has("kraken") and reg.has("bybit_public")
    assert reg.get("kraken").role == "primary"


def test_registry_find_ranks_primary_first() -> None:
    reg, _ = _reg()
    found = reg.find("crypto", "ohlcv", "historical", only_enabled=True)
    assert [c.name for c in found][:2] == ["kraken", "bybit_public"]


def test_router_uses_explicit_route() -> None:
    reg, cfg = _reg()
    rt = ProviderRouter(reg, cfg)
    assert rt.candidates("crypto", "ohlcv", "live") == ["kraken", "bybit_public"]
    # funding is explicitly routed to bybit first
    assert rt.candidates("crypto", "funding", "historical")[0] == "bybit_public"


def test_router_falls_back_on_unhealthy_primary() -> None:
    reg, cfg = _reg()
    health = {"kraken": ProviderHealth.UNAVAILABLE, "bybit_public": ProviderHealth.HEALTHY}
    rt = ProviderRouter(reg, cfg, health=lambda n: health.get(n, ProviderHealth.HEALTHY))
    assert rt.resolve("crypto", "ohlcv", "live") == "bybit_public"


def test_router_raises_when_nothing_usable() -> None:
    reg = ProviderRegistry(
        [
            ProviderCapability(
                name="x",
                asset_classes=("crypto",),
                data_kinds=("ohlcv",),
                modes=("live",),
                enabled=False,
            )
        ]
    )
    rt = ProviderRouter(reg, {"router": {"routes": {"crypto": {"live": {"primary": "x"}}}}})
    try:
        rt.resolve("crypto", "ohlcv", "live")
    except NoProviderAvailable:
        pass
    else:  # pragma: no cover
        raise AssertionError("expected NoProviderAvailable")


def test_gold_routes_to_pepperstone_then_dukascopy() -> None:
    reg, cfg = _reg()
    rt = ProviderRouter(reg, cfg)
    assert rt.candidates("gold", "live", "live")[0] == "pepperstone_mt5"
    assert rt.candidates("gold", "historical", "historical")[0] == "dukascopy_import"
