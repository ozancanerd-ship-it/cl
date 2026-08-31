"""Premium / Discount (§13) + Reference/Dealing Range (§0.5) — ``primitives.md`` 0.1.1.

``pd_position = (price − range_low) / (range_high − range_low)`` gegen eine **Reference Range**:

| Reference | Range |
|-----------|-------|
| ``dealing_range`` | letzter bestätigter Swing Low … letzter bestätigter Swing High (§0.5) |
| ``swept_leg`` | Sweep-Extrem (§6) … Displacement-Extrem (§7) — für ``SMC-SWEEP-REV-01`` maßgeblich |
| ``last_impulse_leg`` | Preisspanne des letzten Displacements (Low..High seiner Bars) |
| ``session_range`` | **noch nicht** — braucht das Sessions-Modul |

Zone: ``pd_position ≤ pd.discount_max`` ⇒ **DISCOUNT** · ``≥ pd.premium_min`` ⇒ **PREMIUM** ·
sonst **EQUILIBRIUM** (kein bevorzugter Entry).

Look-ahead-frei: alle Eingaben (Swings, Sweep, Displacement, Bars) sind bereits look-ahead-frei;
``pd_position`` ist eine reine Funktion von ``price`` und der Range. Long/Short-symmetrisch
(Spiegelung ⇒ ``pd_position → 1 − pd_position``).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence

from trading_agent.core.enums import PDReference, PDZone, Polarity, Timeframe
from trading_agent.core.models import OHLCV
from trading_agent.strategy.primitives.models import (
    Displacement,
    LiquiditySweep,
    PremiumDiscount,
    SwingPoint,
)

PD_DISCOUNT_MAX_DEFAULT = 0.45
PD_PREMIUM_MIN_DEFAULT = 0.55


@dataclasses.dataclass(frozen=True, slots=True)
class PdParams:
    discount_max: float = PD_DISCOUNT_MAX_DEFAULT
    premium_min: float = PD_PREMIUM_MIN_DEFAULT
    reference: PDReference = PDReference.SWEPT_LEG  # config: pd.reference (Nutzer-Festlegung)


# ------------------------------------------------------------------------------- Kernrechnung


def pd_position(price: float, range_low: float, range_high: float) -> float:
    """``(price − range_low) / (range_high − range_low)``. Werte < 0 / > 1 = Preis außerhalb."""
    span = range_high - range_low
    if span <= 0:
        raise ValueError(f"entartete Range: low={range_low} high={range_high}")
    return (price - range_low) / span


def classify_zone(
    position: float,
    *,
    discount_max: float = PD_DISCOUNT_MAX_DEFAULT,
    premium_min: float = PD_PREMIUM_MIN_DEFAULT,
) -> PDZone:
    if position <= discount_max:
        return PDZone.DISCOUNT
    if position >= premium_min:
        return PDZone.PREMIUM
    return PDZone.EQUILIBRIUM


# ------------------------------------------------------------------------------- Reference Ranges


def dealing_range(swings: Sequence[SwingPoint]) -> tuple[float, float] | None:
    """§0.5: Intervall zwischen letztem bestätigten Swing Low und letztem bestätigten Swing High.

    ``swings`` enthält bereits nur bestätigte, alternierende Swings (``detect_swings``); die beiden
    jüngsten (ein SH, ein SL) gehören per Alternation zur selben Preisbewegung.
    """
    last_sh = next((s for s in reversed(swings) if s.is_high), None)
    last_sl = next((s for s in reversed(swings) if not s.is_high), None)
    if last_sh is None or last_sl is None:
        return None
    lo, hi = last_sl.price, last_sh.price
    return (lo, hi) if hi > lo else None


def _disp_extreme(displacement: Displacement, bars: Sequence[OHLCV]) -> float | None:
    s, e = displacement.start_index, displacement.end_index
    if not (0 <= s <= e < len(bars)):
        return None
    seg = bars[s : e + 1]
    if displacement.direction is Polarity.BULLISH:
        return max(b.high for b in seg)
    return min(b.low for b in seg)


def swept_leg_range(
    sweep: LiquiditySweep,
    displacement: Displacement,
    bars: Sequence[OHLCV],
) -> tuple[float, float] | None:
    """§13: Range vom Sweep-Extrem (§6) bis zum Displacement-Extrem (§7)."""
    ext = _disp_extreme(displacement, bars)
    if ext is None:
        return None
    lo, hi = sorted((sweep.penetration_extreme, ext))
    return (lo, hi) if hi > lo else None


def last_impulse_leg_range(
    displacement: Displacement, bars: Sequence[OHLCV]
) -> tuple[float, float] | None:
    """Preisspanne (Low..High) der Bars des letzten Displacements."""
    s, e = displacement.start_index, displacement.end_index
    if not (0 <= s <= e < len(bars)):
        return None
    seg = bars[s : e + 1]
    lo, hi = min(b.low for b in seg), max(b.high for b in seg)
    return (lo, hi) if hi > lo else None


# ------------------------------------------------------------------------------- Ausgabeobjekt


def premium_discount(
    price: float,
    range_low: float,
    range_high: float,
    *,
    reference: PDReference,
    reference_tf: Timeframe,
    params: PdParams | None = None,
) -> PremiumDiscount | None:
    """Klassifiziert ``price`` gegen ``[range_low, range_high]``. ``None`` bei entarteter Range."""
    if range_high <= range_low:
        return None
    p_ = params or PdParams()
    pos = pd_position(price, range_low, range_high)
    return PremiumDiscount(
        reference=reference,
        reference_tf=reference_tf,
        range_low=range_low,
        range_high=range_high,
        price=price,
        pd_position=pos,
        zone=classify_zone(pos, discount_max=p_.discount_max, premium_min=p_.premium_min),
    )


def premium_discount_for(
    price: float,
    reference_tf: Timeframe,
    *,
    params: PdParams | None = None,
    swings: Sequence[SwingPoint] = (),
    sweep: LiquiditySweep | None = None,
    displacement: Displacement | None = None,
    bars: Sequence[OHLCV] = (),
) -> PremiumDiscount | None:
    """Wählt die Range gemäß ``params.reference`` und klassifiziert ``price`` dagegen."""
    p_ = params or PdParams()
    rng: tuple[float, float] | None
    if p_.reference is PDReference.DEALING_RANGE:
        rng = dealing_range(swings)
    elif p_.reference is PDReference.SWEPT_LEG:
        rng = swept_leg_range(sweep, displacement, bars) if sweep and displacement else None
    elif p_.reference is PDReference.LAST_IMPULSE_LEG:
        rng = last_impulse_leg_range(displacement, bars) if displacement else None
    else:  # SESSION_RANGE — Sessions-Modul noch nicht implementiert
        rng = None
    if rng is None:
        return None
    return premium_discount(
        price, rng[0], rng[1], reference=p_.reference, reference_tf=reference_tf, params=p_
    )


__all__ = [
    "PD_DISCOUNT_MAX_DEFAULT",
    "PD_PREMIUM_MIN_DEFAULT",
    "PdParams",
    "classify_zone",
    "dealing_range",
    "last_impulse_leg_range",
    "pd_position",
    "premium_discount",
    "premium_discount_for",
    "swept_leg_range",
]
