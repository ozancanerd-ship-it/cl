"""Handelskosten je Trade — aus Einstiegspreis, Stop-Distanz und ATR statt pauschal.

Hintergrund: ``docs/INDEPENDENT-METHOD-AUDIT-2026-09-03.md``, Befund F4. Die vorherige
Forschung zog eine flache Konstante von ``0.03 R`` fuer alle Symbole ab. Real sind es
0.41 R fuer XAUUSDT auf H4 und 0.22 R fuer BTCUSDT — Faktor 13 beziehungsweise 7.

Der Grund fuer den Fehler ist strukturell: Kosten fallen in *Preiseinheiten* an, das
Risiko wird in *R* gemessen. Eine feste Zahl in R unterstellt eine feste Stop-Distanz.
Sobald sich Timeframe oder Volatilitaet aendern, ist sie falsch — und zwar umso mehr,
je enger der Stop ist. Deshalb rechnet dieses Modul je Trade:

    Kosten je Seite = entry * fee_pct/100 + max(entry * min_slippage_pct/100,
                                                slippage_atr_frac * atr)
    cost_r          = 2 * Kosten je Seite / r_unit

Konfiguration in ``config/costs.yaml``. Die Werte sind bewusst Worst-Case gewaehlt:
ein Setup, das nach diesen Kosten noch steht, ist ein echtes Setup.

``tradeable`` trennt handelbare Symbole von indikativen Reihen (Yahoo-Proxies). Die
bisherige OOS-Edge ruhte stark auf Letzteren — Reports sollen das ausweisen koennen.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

_DEFAULT_PATH = "config/costs.yaml"


@dataclass(frozen=True, slots=True)
class SymbolCosts:
    """Aufgeloeste Kostenparameter eines Symbols."""

    symbol: str
    cls: str
    taker_fee_pct: float
    slippage_atr_frac: float
    min_slippage_pct: float
    tradeable: bool

    def cost_quote(self, *, entry: float, atr: float) -> float:
        """Kosten fuer Ein- UND Ausstieg, in Preiseinheiten."""
        fee = entry * (self.taker_fee_pct / 100.0)
        slip = max(entry * (self.min_slippage_pct / 100.0), self.slippage_atr_frac * atr)
        return 2.0 * (fee + slip)

    def cost_r(self, *, entry: float, atr: float, r_unit: float) -> float:
        """Kosten als Anteil der Risikoeinheit. ``0.0`` wenn ``r_unit`` unbrauchbar."""
        if r_unit <= 0:
            return 0.0
        return self.cost_quote(entry=entry, atr=atr) / r_unit


class CostModel:
    """Laedt ``config/costs.yaml`` und loest Symbole auf Klassen + Overrides auf."""

    def __init__(self, doc: dict[str, Any]) -> None:
        self._classes: dict[str, dict[str, Any]] = dict(doc.get("classes") or {})
        self._symbols: dict[str, dict[str, Any]] = dict(doc.get("symbols") or {})
        self._fallback: dict[str, Any] = dict(doc.get("fallback") or {"class": "crypto_spot"})
        if not self._classes:
            raise ValueError("costs.yaml enthaelt keine 'classes'")
        self._cache: dict[str, SymbolCosts] = {}

    @classmethod
    def load(cls, path: str | Path = _DEFAULT_PATH) -> CostModel:
        import yaml

        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"Kosten-Konfiguration fehlt: {p}")
        return cls(yaml.safe_load(p.read_text(encoding="utf-8")) or {})

    def for_symbol(self, symbol: str) -> SymbolCosts:
        hit = self._cache.get(symbol)
        if hit is not None:
            return hit
        entry = dict(self._symbols.get(symbol) or self._fallback)
        cls_name = str(entry.pop("class", "crypto_spot"))
        base = dict(self._classes.get(cls_name) or {})
        if not base:
            raise KeyError(f"unbekannte Kostenklasse {cls_name!r} fuer {symbol!r}")
        base.update(entry)  # Symbol-Overrides schlagen die Klasse
        out = SymbolCosts(
            symbol=symbol,
            cls=cls_name,
            taker_fee_pct=float(base.get("taker_fee_pct", 0.0)),
            slippage_atr_frac=float(base.get("slippage_atr_frac", 0.0)),
            min_slippage_pct=float(base.get("min_slippage_pct", 0.0)),
            tradeable=bool(base.get("tradeable", True)),
        )
        self._cache[symbol] = out
        return out

    def cost_r(self, symbol: str, *, entry: float, atr: float, r_unit: float) -> float:
        return self.for_symbol(symbol).cost_r(entry=entry, atr=atr, r_unit=r_unit)

    def is_tradeable(self, symbol: str) -> bool:
        return self.for_symbol(symbol).tradeable


@lru_cache(maxsize=4)
def load_cost_model(path: str = _DEFAULT_PATH) -> CostModel:
    """Gecachter Loader — Research-Skripte rufen das je Trade auf."""
    return CostModel.load(path)


__all__ = ["CostModel", "SymbolCosts", "load_cost_model"]
