"""Domänen-Modelle für die Portfolio-Intelligence (Masterplan §33–§35).

`Holding` ist die normalisierte Sicht auf **eine** Position in **einem** Account — von jedem
read-only Account-Adapter (Kraken/Bybit/Binance) befüllbar. `PortfolioHub` konsolidiert sie.
Keine Order-Felder, keine Broker-Spezifika.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from trading_agent.core.enums import AssetClass, Direction


class PositionVerdict(StrEnum):
    """Handlungs-Verdikt je offener Position (Masterplan §36)."""

    STRONG_HOLD = "strong_hold"
    HOLD = "hold"
    WATCH = "watch"
    REDUCE = "reduce"
    EXIT = "exit"
    RE_ENTRY_WATCH = "re_entry_watch"  # bereits (teil-)geschlossen, Bedingungen für Wieder-Einstieg


@dataclass(frozen=True, slots=True)
class Holding:
    """Eine Position in einem Account, in Kontowährung normalisiert."""

    instrument: str
    asset_class: AssetClass
    account: str
    direction: Direction
    quantity: float  # Basis-Einheiten (immer > 0; Richtung steckt in ``direction``)
    avg_entry_price: float
    mark_price: float
    opened_at: datetime | None = None
    # optionale Setup-Verknüpfung (falls die Position aus einem Signal des Systems stammt)
    setup_id: str | None = None
    stop_ref: float | None = None  # ursprüngliche Invalidierung, für R-Rechnung

    @property
    def cost_basis(self) -> float:
        return self.quantity * self.avg_entry_price

    @property
    def market_value(self) -> float:
        return self.quantity * self.mark_price

    @property
    def unrealized_pnl(self) -> float:
        sign = 1.0 if self.direction is Direction.LONG else -1.0
        return sign * self.quantity * (self.mark_price - self.avg_entry_price)

    @property
    def unrealized_pct(self) -> float:
        if self.cost_basis == 0:
            return 0.0
        return self.unrealized_pnl / self.cost_basis

    @property
    def unrealized_r(self) -> float | None:
        """Ergebnis in R, falls ``stop_ref`` bekannt ist."""
        if self.stop_ref is None:
            return None
        risk = abs(self.avg_entry_price - self.stop_ref)
        if risk == 0:
            return None
        sign = 1.0 if self.direction is Direction.LONG else -1.0
        return sign * (self.mark_price - self.avg_entry_price) / risk


@dataclass(frozen=True, slots=True)
class AccountPortfolio:
    """Momentaufnahme eines einzelnen (read-only) Accounts."""

    account: str
    as_of: datetime
    cash: float  # freie Kontowährung (Cash / verfügbare Margin)
    holdings: tuple[Holding, ...] = ()
    currency: str = "USD"
    read_only_verified: bool = False

    @property
    def positions_value(self) -> float:
        return sum(h.market_value for h in self.holdings)

    @property
    def equity(self) -> float:
        return self.cash + self.positions_value

    @property
    def unrealized_pnl(self) -> float:
        return sum(h.unrealized_pnl for h in self.holdings)


@dataclass(frozen=True, slots=True)
class ConsolidatedPortfolio:
    """Konsolidierte Sicht über alle Accounts (Masterplan §33)."""

    as_of: datetime
    accounts: tuple[AccountPortfolio, ...]
    # instrument -> netto-Holding über alle Accounts (gleiche Richtung vorausgesetzt/aggregiert)
    net_holdings: tuple[Holding, ...] = ()
    per_account_equity: dict[str, float] = field(default_factory=dict)
    asset_class_value: dict[AssetClass, float] = field(default_factory=dict)

    @property
    def equity(self) -> float:
        return sum(a.equity for a in self.accounts)

    @property
    def cash(self) -> float:
        return sum(a.cash for a in self.accounts)

    @property
    def positions_value(self) -> float:
        return sum(h.market_value for h in self.net_holdings)

    @property
    def unrealized_pnl(self) -> float:
        return sum(h.unrealized_pnl for h in self.net_holdings)

    @property
    def cash_pct(self) -> float:
        eq = self.equity
        return self.cash / eq if eq > 0 else 1.0

    def allocation(self) -> dict[AssetClass, float]:
        """Anteil je Asset-Klasse am Gesamt-Equity (0..1)."""
        eq = self.equity
        if eq <= 0:
            return {}
        return {k: v / eq for k, v in self.asset_class_value.items()}

    def weight_of(self, instrument: str) -> float:
        eq = self.equity
        if eq <= 0:
            return 0.0
        mv = sum(h.market_value for h in self.net_holdings if h.instrument == instrument.upper())
        return mv / eq

    def holding(self, instrument: str) -> Holding | None:
        for h in self.net_holdings:
            if h.instrument == instrument.upper():
                return h
        return None


# ~50 % Aktien / ~50 % Crypto+Gold — Masterplan §43, PROPOSED DEFAULT, NICHT hartkodiert.
DEFAULT_ALLOCATION_TARGET: dict[str, tuple[float, float]] = {
    "equity_bucket": (0.35, 0.65),  # EQUITY (Einzelaktien, nie ETF)
    "crypto_gold_bucket": (0.35, 0.65),  # CRYPTO + ALTCOIN + GOLD
}

__all__ = [
    "DEFAULT_ALLOCATION_TARGET",
    "AccountPortfolio",
    "ConsolidatedPortfolio",
    "Holding",
    "PositionVerdict",
]
