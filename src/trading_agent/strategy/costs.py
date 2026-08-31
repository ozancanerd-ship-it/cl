"""Kosten-/Slippage-Modell für Paper-Positionen — **modular, konfigurierbar, Default 0.0**.

Die Paper-Simulation (``strategy.position``) rechnet in **R** (``r_unit = |entry - initial_sl|``).
Dieses Modul übersetzt Handelskosten in denselben R-Maßstab, damit ``realized_r`` **netto** wird
und ``gross_realized_r`` den Wert **vor** Kosten behält.

**Keine erfundenen realen Werte.** Alle Sätze sind per Default ``0.0``. Für einen realistischen
Lauf müssen echte Zahlen gesetzt werden — aus dem Exchange-Gebührenplan (``refdata.FeeSchedule``),
gemessener Slippage, realem Funding. Bis dahin ist der Lauf explizit **brutto**.

Bausteine (alle je **Transaktion / Seite**, in Basispunkten des Referenzpreises):

* ``fee``            — Maker/Taker-Gebühr
* ``half_spread``    — halbe Geld-Brief-Spanne (Marktorder zahlt sie)
* ``slippage``       — fixer Aufschlag  + optional ``mult · (ATR/Preis)``
* ``market_impact``  — zusätzlicher größen-/tiefenabhängiger Aufschlag
* ``funding``        — nur Perp: Betrag pro Tag, Vorzeichen aus der Richtung
"""

from __future__ import annotations

import dataclasses

from trading_agent.core.enums import Direction

_BPS = 1.0 / 10_000.0


@dataclasses.dataclass(frozen=True, slots=True)
class CostConfig:
    taker_fee_bps: float = 0.0
    maker_fee_bps: float = 0.0
    half_spread_bps: float = 0.0
    slippage_bps: float = 0.0
    slippage_atr_mult: float = 0.0  # + mult · (ATR / Preis), in bps-Äquivalent
    market_impact_bps: float = 0.0
    funding_bps_per_day: float = 0.0  # Perp-Funding-Betrag pro Tag (>0 = Longs zahlen)
    entry_is_maker: bool = True  # Limit am proximalen Zonenrand ⇒ i. d. R. Maker
    exit_is_maker: bool = False  # SL = Marktorder ⇒ Taker; konservativ auch für TP

    @property
    def is_zero(self) -> bool:
        return (
            self.taker_fee_bps == 0.0
            and self.maker_fee_bps == 0.0
            and self.half_spread_bps == 0.0
            and self.slippage_bps == 0.0
            and self.slippage_atr_mult == 0.0
            and self.market_impact_bps == 0.0
            and self.funding_bps_per_day == 0.0
        )


@dataclasses.dataclass(frozen=True, slots=True)
class LegCost:
    """Kosten **einer** Transaktion (Entry oder ein Exit-Leg), Anteil von ``r_unit``."""

    fee_r: float
    spread_r: float
    slippage_r: float
    impact_r: float

    @property
    def total_r(self) -> float:
        return self.fee_r + self.spread_r + self.slippage_r + self.impact_r

    @classmethod
    def zero(cls) -> LegCost:
        return cls(0.0, 0.0, 0.0, 0.0)


def leg_cost_r(
    cfg: CostConfig,
    *,
    price: float,
    r_unit: float,
    atr: float | None = None,
    is_maker: bool,
) -> LegCost:
    """Kosten einer Transaktion zum Preis ``price``, ausgedrückt als Anteil von ``r_unit``.

    Immer **positiv** (Kosten) — der Aufrufer zieht sie richtungsgerecht ab.
    """
    if r_unit <= 0.0 or cfg.is_zero:
        return LegCost.zero()
    fee_bps = cfg.maker_fee_bps if is_maker else cfg.taker_fee_bps
    atr_bps = 0.0
    if atr is not None and price > 0.0 and cfg.slippage_atr_mult != 0.0:
        atr_bps = cfg.slippage_atr_mult * (atr / price) / _BPS
    fee_px = price * fee_bps * _BPS
    spread_px = price * cfg.half_spread_bps * _BPS
    slip_px = price * (cfg.slippage_bps + atr_bps) * _BPS
    impact_px = price * cfg.market_impact_bps * _BPS
    return LegCost(
        fee_r=fee_px / r_unit,
        spread_r=spread_px / r_unit,
        slippage_r=slip_px / r_unit,
        impact_r=impact_px / r_unit,
    )


def funding_cost_r(
    cfg: CostConfig,
    *,
    price: float,
    r_unit: float,
    direction: Direction,
    bars_held: int,
    bar_seconds: int,
) -> float:
    """Perp-Funding über die Haltedauer, als Anteil von ``r_unit``. ``> 0`` = Kosten.

    Long zahlt bei positivem Funding, Short erhält es (bei ``funding_bps_per_day > 0``).
    """
    if r_unit <= 0.0 or cfg.funding_bps_per_day == 0.0 or price <= 0.0:
        return 0.0
    days = (bars_held * bar_seconds) / 86_400.0
    amount_px = price * cfg.funding_bps_per_day * _BPS * days
    signed = amount_px if direction is Direction.LONG else -amount_px
    return signed / r_unit


def from_fee_schedule(
    taker_bps: float, maker_bps: float, *, half_spread_bps: float = 0.0
) -> CostConfig:
    """Baut eine ``CostConfig`` aus einem echten Gebührenplan (``refdata.FeeSchedule``).
    Slippage / Impact / Funding bleiben 0.0, bis gemessene Werte vorliegen."""
    return CostConfig(
        taker_fee_bps=taker_bps,
        maker_fee_bps=maker_bps,
        half_spread_bps=half_spread_bps,
    )


def with_config(base: CostConfig, **overrides: float | bool) -> CostConfig:
    return dataclasses.replace(base, **overrides)  # type: ignore[arg-type]


__all__ = [
    "CostConfig",
    "LegCost",
    "from_fee_schedule",
    "funding_cost_r",
    "leg_cost_r",
    "with_config",
]
