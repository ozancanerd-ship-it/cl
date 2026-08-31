"""Symbol-Mapping: kanonisches Symbol <-> quellenspezifische Schreibweise."""

from __future__ import annotations

from collections.abc import Iterable

from trading_agent.refdata.models import SymbolMapping


class SymbolMappingError(KeyError):
    pass


class SymbolMapper:
    """Registry für ``SymbolMapping``-Einträge.

    * ``to_provider(canonical, source)`` – kanonisch -> Quelle
    * ``to_canonical(provider_symbol, source)`` – Quelle -> kanonisch (inkl. Aliase)
    """

    def __init__(self, mappings: Iterable[SymbolMapping] = ()) -> None:
        self._by_canonical: dict[tuple[str, str], SymbolMapping] = {}
        self._by_provider: dict[tuple[str, str], str] = {}
        for m in mappings:
            self.add(m)

    def add(self, mapping: SymbolMapping) -> None:
        key = (mapping.canonical.upper(), mapping.source.lower())
        if key in self._by_canonical:
            raise SymbolMappingError(f"doppeltes Mapping für {key}")
        self._by_canonical[key] = mapping
        for sym in (mapping.provider_symbol, *mapping.aliases):
            self._by_provider[(sym.upper(), mapping.source.lower())] = mapping.canonical.upper()

    def to_provider(self, canonical: str, source: str) -> str:
        try:
            return self._by_canonical[(canonical.upper(), source.lower())].provider_symbol
        except KeyError as exc:
            raise SymbolMappingError(
                f"kein Provider-Symbol für {canonical!r} @ {source!r}"
            ) from exc

    def to_canonical(self, provider_symbol: str, source: str) -> str:
        try:
            return self._by_provider[(provider_symbol.upper(), source.lower())]
        except KeyError as exc:
            raise SymbolMappingError(
                f"kein kanonisches Symbol für {provider_symbol!r} @ {source!r}"
            ) from exc

    def known_canonical(self, source: str | None = None) -> set[str]:
        if source is None:
            return {c for c, _ in self._by_canonical}
        return {c for (c, s) in self._by_canonical if s == source.lower()}

    def __len__(self) -> int:
        return len(self._by_canonical)


__all__ = ["SymbolMapper", "SymbolMappingError"]
