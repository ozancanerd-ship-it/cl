"""Gemeinsame Basis für **Live-Daten-Adapter** — brokerunabhängig, keine Keys im Code.

Jeder Adapter (Kraken, Bybit, MT5/Pepperstone, Equities, News, Cross-Asset, …) deklariert:

* seine **Fähigkeiten** (``ProviderCapability`` — Asset-Klassen, Datenarten, REST/WS, Historie);
* welche **Zugangsdaten** er *bräuchte* (``CredentialSpec`` — nur die Namen der ENV-Variablen,
  **niemals** die Werte). Ohne gesetzte Zugangsdaten meldet ``status()`` ``UNAVAILABLE`` — der
  Adapter ist dann inert, kein Fehler.

**Die Strategy Engine importiert hier nichts.** Adapter füttern das ``MarketDataRepository`` /
den ``MarketContext``-Bau; die Entscheidungslogik bleibt providerfrei.
"""

from __future__ import annotations

import dataclasses
import os
from datetime import UTC, datetime

from trading_agent.core.enums import DataKind, ProviderHealth
from trading_agent.data.interfaces import ProviderStatus


@dataclasses.dataclass(frozen=True, slots=True)
class CredentialSpec:
    """Deklariert benötigte Zugangsdaten — **nur Namen**, keine Werte.

    ``read_only`` markiert Marktdaten-Only-Keys (kein Withdrawal, keine Orders). Alle
    Live-Daten-Adapter dieser Phase sind ``read_only=True``.
    """

    provider: str
    env_vars: tuple[str, ...] = ()
    read_only: bool = True
    note: str = ""

    def present(self) -> bool:
        return all(os.environ.get(v) for v in self.env_vars)

    def missing(self) -> tuple[str, ...]:
        return tuple(v for v in self.env_vars if not os.environ.get(v))


@dataclasses.dataclass(frozen=True, slots=True)
class AdapterInfo:
    name: str
    asset_classes: tuple[str, ...]
    data_kinds: tuple[DataKind, ...]
    modes: tuple[str, ...]  # "historical" | "stream"
    credentials: CredentialSpec
    platform_only: str | None = None  # z. B. "windows" für MT5
    redistribution_allowed: bool = False
    note: str = ""


class LiveDataAdapter:
    """Minimale gemeinsame Oberfläche. Konkrete Adapter implementieren die passenden
    ``data.interfaces``-ABCs zusätzlich (OHLCV/Trade/Orderbook/Funding/News/…)."""

    info: AdapterInfo

    def __init__(self, info: AdapterInfo) -> None:
        self.info = info
        self._last_error = ""
        self._last_success: datetime | None = None

    # ---- Zustand ---------------------------------------------------------------
    def credentials_ok(self) -> bool:
        return not self.info.credentials.env_vars or self.info.credentials.present()

    def platform_ok(self) -> bool:
        if self.info.platform_only is None:
            return True
        import sys

        return sys.platform.startswith(self.info.platform_only[:3])

    def status(self) -> ProviderStatus:
        if not self.platform_ok():
            health = ProviderHealth.UNAVAILABLE
            detail = f"nur auf {self.info.platform_only} verfügbar"
        elif not self.credentials_ok():
            health = ProviderHealth.UNAVAILABLE
            detail = f"Zugangsdaten fehlen: {', '.join(self.info.credentials.missing())}"
        elif self._last_error:
            health = ProviderHealth.DEGRADED
            detail = self._last_error
        else:
            health = ProviderHealth.HEALTHY
            detail = "ok"
        return ProviderStatus(
            provider=self.info.name,
            health=health,
            checked_at=datetime.now(UTC),
            detail=detail,
            last_success_at=self._last_success,
        )

    # ---- Hilfen für Unterklassen --------------------------------------------
    def _ok(self) -> None:
        self._last_error = ""
        self._last_success = datetime.now(UTC)

    def _fail(self, msg: str) -> None:
        self._last_error = msg


__all__ = ["AdapterInfo", "CredentialSpec", "LiveDataAdapter"]
