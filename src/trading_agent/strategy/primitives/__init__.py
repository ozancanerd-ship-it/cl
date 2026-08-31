"""Primitive-Detektoren der Strategy Engine (``docs/strategy/primitives.md``, ``0.1.1``).

Jede Primitive ist objektiv definiert und arbeitet ausschließlich auf ``confirmed``-Bars.
Implementiert (Phase 3, schrittweise):

* ``atr`` — Wilder-ATR (§0.2)
* ``swings`` — Swing High/Low, HH/HL/LH/LL (§1)
* ``structure`` — Struktur-Zustand, BOS, CHoCH (§2, §3)
* ``liquidity`` — Liquidity Level (§4), Equal High/Low (§5), Liquidity Sweep (§6)
* ``imbalance`` — Displacement (§7), FVG (§8), IFVG (§9), Mitigation (§11)
* ``blocks`` — Order Block (§10), Breaker (§12)
* ``pd`` — Premium/Discount + Reference/Dealing Range (§13, §0.5)

``primitives.md`` §0–§13 damit vollständig (bis auf ``session_range`` — braucht Sessions-Modul).
"""

from __future__ import annotations

from trading_agent.strategy.primitives.atr import atr, atr_series, true_ranges
from trading_agent.strategy.primitives.blocks import (
    BreakerParams,
    ObParams,
    find_breakers,
    find_order_blocks,
    unmitigated,
)
from trading_agent.strategy.primitives.imbalance import (
    DisplacementParams,
    FvgParams,
    IfvgParams,
    ImbalanceResult,
    analyze_imbalance,
    find_displacements,
    find_fvgs,
    find_ifvgs,
    link_displacement,
    mitigation_fill,
    zone_state,
)
from trading_agent.strategy.primitives.liquidity import (
    SweepParams,
    apply_state,
    classify_level_state,
    equal_level_clusters,
    previous_period_levels,
    resolve_sweep,
    score_level,
    swing_levels,
)
from trading_agent.strategy.primitives.models import (
    FVG,
    IFVG,
    Breaker,
    Displacement,
    LiquidityLevel,
    LiquiditySweep,
    OrderBlock,
    PremiumDiscount,
    StructureBreak,
    StructureState,
    SwingPoint,
)
from trading_agent.strategy.primitives.pd import (
    PdParams,
    classify_zone,
    dealing_range,
    last_impulse_leg_range,
    pd_position,
    premium_discount,
    premium_discount_for,
    swept_leg_range,
)
from trading_agent.strategy.primitives.structure import (
    derive_structure_state,
    range_break,
    range_breaks,
    structure_breaks,
)
from trading_agent.strategy.primitives.swings import detect_swings, last_swing

__all__ = [
    "FVG",
    "IFVG",
    "Breaker",
    "BreakerParams",
    "Displacement",
    "DisplacementParams",
    "FvgParams",
    "IfvgParams",
    "ImbalanceResult",
    "LiquidityLevel",
    "LiquiditySweep",
    "ObParams",
    "OrderBlock",
    "PdParams",
    "PremiumDiscount",
    "StructureBreak",
    "StructureState",
    "SweepParams",
    "SwingPoint",
    "analyze_imbalance",
    "apply_state",
    "atr",
    "atr_series",
    "classify_level_state",
    "classify_zone",
    "dealing_range",
    "derive_structure_state",
    "detect_swings",
    "equal_level_clusters",
    "find_breakers",
    "find_displacements",
    "find_fvgs",
    "find_ifvgs",
    "find_order_blocks",
    "last_impulse_leg_range",
    "last_swing",
    "link_displacement",
    "mitigation_fill",
    "pd_position",
    "premium_discount",
    "premium_discount_for",
    "previous_period_levels",
    "range_break",
    "range_breaks",
    "resolve_sweep",
    "score_level",
    "structure_breaks",
    "swept_leg_range",
    "swing_levels",
    "true_ranges",
    "unmitigated",
    "zone_state",
]
