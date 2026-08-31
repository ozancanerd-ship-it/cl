"""Instrument-Master: Registry für ``Instrument``-Referenzdaten."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from trading_agent.core.enums import AssetClass, TradingPriority
from trading_agent.core.time import ensure_utc
from trading_agent.refdata.models import Instrument


class InstrumentNotFound(KeyError):
    pass


class InstrumentMaster:
    """In-Memory-Registry der bekannten Instrumente (kanonisches Symbol -> ``Instrument``).

    Multi-Asset: Crypto, Altcoins, Gold, Forex, Aktien, ETFs. Der spätere autonome Scanner
    priorisiert über ``trading_priority`` (Tier 1/2/3).
    """

    def __init__(self, instruments: Iterable[Instrument] = ()) -> None:
        self._by_symbol: dict[str, Instrument] = {}
        for inst in instruments:
            self.add(inst)

    def add(self, instrument: Instrument) -> None:
        key = instrument.canonical_symbol.upper()
        if key in self._by_symbol:
            raise ValueError(f"Instrument {key} bereits registriert")
        self._by_symbol[key] = instrument

    def get(self, canonical_symbol: str) -> Instrument:
        try:
            return self._by_symbol[canonical_symbol.upper()]
        except KeyError as exc:
            raise InstrumentNotFound(canonical_symbol) from exc

    def has(self, canonical_symbol: str) -> bool:
        return canonical_symbol.upper() in self._by_symbol

    def all(self) -> list[Instrument]:
        return list(self._by_symbol.values())

    def by_asset_class(self, asset_class: AssetClass) -> list[Instrument]:
        return [i for i in self._by_symbol.values() if i.asset_class is asset_class]

    def by_priority(self, priority: TradingPriority) -> list[Instrument]:
        return [i for i in self._by_symbol.values() if i.trading_priority is priority]

    def scan_universe(self, at: datetime | None = None) -> list[Instrument]:
        """Instrumente für den autonomen Scanner: Tier 1 + Tier 2, nur zum Zeitpunkt ``at``
        handelbar (Point-in-Time gegen Survivorship-Bias). Sortiert Tier 1 vor Tier 2."""
        moment = ensure_utc(at) if at is not None else None
        result = [
            i
            for i in self._by_symbol.values()
            if i.trading_priority in (TradingPriority.TIER_1, TradingPriority.TIER_2)
            and (moment is None or i.is_tradeable_at(moment))
        ]
        result.sort(key=lambda i: (i.trading_priority.value, i.canonical_symbol))
        return result

    def __len__(self) -> int:
        return len(self._by_symbol)

    def __contains__(self, symbol: object) -> bool:
        return isinstance(symbol, str) and self.has(symbol)


__all__ = ["InstrumentMaster", "InstrumentNotFound"]
