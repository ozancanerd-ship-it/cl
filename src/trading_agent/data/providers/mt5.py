"""MetaTrader5 / Pepperstone Adapter — **Vertrag, noch keine Live-Verbindung**.

Für FX-Majors + XAUUSD: Bid/Ask, Tick/M1, Spread, Trading-Sessions, Broker-Symbol-Mapping.

**Voraussetzungen (Phase 9+):** Windows + installiertes ``MetaTrader5``-Python-Paket + laufendes
MT5-Terminal, das mit einem Broker-**Demo/Read-only**-Login verbunden ist. **Keine Keys im Code**,
keine Orderfunktionen hier (Ausführung ist strikt getrennt, Phase 14).

Ohne diese Voraussetzungen meldet ``status()`` ``UNAVAILABLE`` — der Adapter ist inert.
Der ``MetaTrader5``-Import ist bewusst lazy und in ``mypy``-Overrides ignoriert.
"""

from __future__ import annotations

from datetime import datetime

from trading_agent.core.enums import DataKind, Timeframe
from trading_agent.core.models import OHLCV, Quote
from trading_agent.data.providers.adapter_base import AdapterInfo, CredentialSpec, LiveDataAdapter

# kanonisch → MT5/Pepperstone-Symbol (Broker-spezifisch, hier nur die üblichen Majors)
DEFAULT_SYMBOL_MAP: dict[str, str] = {
    "EURUSD": "EURUSD",
    "GBPUSD": "GBPUSD",
    "USDJPY": "USDJPY",
    "AUDUSD": "AUDUSD",
    "USDCHF": "USDCHF",
    "USDCAD": "USDCAD",
    "XAUUSD": "XAUUSD",
}

_MT5_TF: dict[Timeframe, str] = {
    Timeframe.M1: "TIMEFRAME_M1",
    Timeframe.M5: "TIMEFRAME_M5",
    Timeframe.M15: "TIMEFRAME_M15",
    Timeframe.H1: "TIMEFRAME_H1",
    Timeframe.H4: "TIMEFRAME_H4",
    Timeframe.D1: "TIMEFRAME_D1",
}


class MT5Adapter(LiveDataAdapter):
    def __init__(self, *, symbol_map: dict[str, str] | None = None) -> None:
        super().__init__(
            AdapterInfo(
                name="mt5",
                asset_classes=("forex", "gold"),
                data_kinds=(DataKind.OHLCV,),
                modes=("historical", "stream"),
                credentials=CredentialSpec(
                    provider="mt5",
                    env_vars=("MT5_LOGIN", "MT5_PASSWORD", "MT5_SERVER"),
                    read_only=True,
                    note="Broker-Demo/Read-only-Login; MT5-Terminal muss laufen",
                ),
                platform_only="windows",
                redistribution_allowed=False,
                note="Bid/Ask + Tick/M1; Ausführung strikt getrennt (Phase 14)",
            )
        )
        self.symbol_map = {**DEFAULT_SYMBOL_MAP, **(symbol_map or {})}
        self._mt5: object | None = None

    def to_broker_symbol(self, canonical: str) -> str:
        return self.symbol_map.get(canonical.upper(), canonical.upper())

    def _terminal(self) -> object:
        if self._mt5 is None:
            try:
                import MetaTrader5 as mt5
            except ImportError as exc:  # pragma: no cover - nur auf Windows
                self._fail("MetaTrader5-Paket nicht installiert")
                raise RuntimeError("MetaTrader5 nicht verfügbar") from exc
            self._mt5 = mt5
        return self._mt5

    def get_ohlcv(
        self, instrument: str, timeframe: Timeframe, start: datetime, end: datetime
    ) -> list[OHLCV]:  # pragma: no cover - Live-Impl Phase 9+
        raise NotImplementedError("MT5-Live-Fetch: Phase 9+ (Terminal + Read-only-Login nötig)")

    def get_quotes(
        self, instrument: str, start: datetime, end: datetime
    ) -> list[Quote]:  # pragma: no cover
        raise NotImplementedError("MT5-Tick/Quote-Fetch: Phase 9+")


__all__ = ["DEFAULT_SYMBOL_MAP", "MT5Adapter"]
