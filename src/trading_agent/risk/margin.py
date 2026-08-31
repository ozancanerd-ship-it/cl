"""Margin & Liquidation — **isolated linear**, für die Risk Engine.

Reine Zahlenfunktionen. Kein Broker-State, kein ``Instrument`` nötig (die vollständige
Tier-Auswertung mit ``Instrument.margin_tiers`` liegt in ``execution.simulation.MarginModel`` /
``LiquidationModel`` — dieses Modul ist der schlanke, im Risk-Pfad nutzbare Kern).

Konvention (isolated margin, linear perp / CFD):

* ``initial_margin = notional / leverage``
* Liquidation, wenn der nicht realisierte Verlust die Initial-Margin **abzüglich** der
  Maintenance-Margin aufzehrt:
  ``price_move_to_liq = entry · (1/leverage − maintenance_margin_rate)``
* ``mmr = 0`` ⇒ konservative „bis 0 % Margin"-Näherung (identisch zur bisherigen
  ``entry/leverage``-Heuristik in ``position_sizing``).

Alle Formeln Long/Short-symmetrisch. Keine erfundenen Tier-Sätze — ``mmr`` muss der Aufrufer
aus echten Kontraktdaten liefern, sonst bleibt es bei der 0-Näherung.
"""

from __future__ import annotations

import dataclasses

from trading_agent.core.enums import Side


@dataclasses.dataclass(frozen=True, slots=True)
class LiquidationEstimate:
    liq_price: float
    distance_price: float  # |entry - liq_price|
    distance_pct: float  # distance_price / entry · 100
    distance_atr: float | None  # distance_price / atr, wenn atr gegeben
    maintenance_margin_rate: float

    @property
    def reachable(self) -> bool:
        return self.distance_price > 0.0


def initial_margin(notional: float, leverage: float) -> float:
    return abs(notional) / max(leverage, 1.0)


def liquidation_price(
    *, entry: float, side: Side, leverage: float, maintenance_margin_rate: float = 0.0
) -> float:
    """Isolated-Liquidationspreis (linear). ``side`` = Positionsrichtung."""
    if entry <= 0.0:
        raise ValueError("entry must be > 0")
    inv = 1.0 / max(leverage, 1.0)
    frac = max(0.0, inv - max(0.0, maintenance_margin_rate))
    if side is Side.BUY:
        return entry * (1.0 - frac)
    return entry * (1.0 + frac)


def estimate_liquidation(
    *,
    entry: float,
    side: Side,
    leverage: float,
    maintenance_margin_rate: float = 0.0,
    atr: float | None = None,
) -> LiquidationEstimate:
    liq = liquidation_price(
        entry=entry,
        side=side,
        leverage=leverage,
        maintenance_margin_rate=maintenance_margin_rate,
    )
    dist = abs(entry - liq)
    return LiquidationEstimate(
        liq_price=liq,
        distance_price=dist,
        distance_pct=dist / entry * 100.0 if entry else 0.0,
        distance_atr=(dist / atr if atr and atr > 0.0 else None),
        maintenance_margin_rate=maintenance_margin_rate,
    )


def max_leverage_for_liq_distance(
    *, entry: float, min_distance: float, maintenance_margin_rate: float = 0.0
) -> float:
    """Größter Hebel, bei dem der Liquidationsabstand ≥ ``min_distance`` (Preis) bleibt.

    Aus ``entry · (1/lev − mmr) >= min_distance`` ⇒ ``lev <= 1 / (min_distance/entry + mmr)``.
    Rückgabe < 1.0 ⇒ selbst 1× Hebel hält den Abstand nicht (Position untradebar).
    """
    if entry <= 0.0 or min_distance <= 0.0:
        raise ValueError("entry and min_distance must be > 0")
    denom = min_distance / entry + max(0.0, maintenance_margin_rate)
    return 1.0 / denom if denom > 0.0 else float("inf")


__all__ = [
    "LiquidationEstimate",
    "estimate_liquidation",
    "initial_margin",
    "liquidation_price",
    "max_leverage_for_liq_distance",
]
