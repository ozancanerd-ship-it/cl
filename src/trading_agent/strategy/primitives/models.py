"""Ausgabeobjekte der Primitive-Detektoren (``docs/strategy/primitives.md``).

Alle Objekte sind unveränderlich, **timestamped** (``timestamp`` / ``formed_at`` / ``created_bar``
…) und **versioniert** (``strategy_version``). ``zone_id`` wird beim Einhängen in den
``MarketContext`` bzw. ins Decision Ledger ergänzt.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from trading_agent.core.enums import (
    Direction,
    LiquidityState,
    LiquidityType,
    MarketSide,
    PDReference,
    PDZone,
    Polarity,
    RegimeDirectional,
    StructureBreakKind,
    StructureOrigin,
    SwingLabel,
    SwingType,
    Timeframe,
    ZoneKind,
    ZoneState,
)
from trading_agent.core.version import STRATEGY_VERSION

# --------------------------------------------------------------------------------------------
# 1. Swing High / Low
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SwingPoint:
    type: SwingType
    timeframe: Timeframe
    bar_index: int
    timestamp: datetime  # open_time der Swing-Bar
    price: float  # high[i] bzw. low[i]
    confirmed_at: datetime  # close_time der Bar i+R
    leg_size_atr: float = 0.0  # Abstand zum vorherigen gegensätzlichen Swing, in ATR(tf)
    label: SwingLabel | None = None  # HH/HL/LH/LL/EQUAL — relativ zum Vorgänger gleichen Typs
    strategy_version: str = STRATEGY_VERSION

    @property
    def is_high(self) -> bool:
        return self.type is SwingType.SWING_HIGH


# --------------------------------------------------------------------------------------------
# 2. / 3. Structure Break (BOS / CHoCH)
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StructureBreak:
    kind: StructureBreakKind
    direction: Polarity
    timeframe: Timeframe
    broken_level_price: float
    break_bar_timestamp: datetime  # open_time der Bruch-Bar
    break_close: float
    origin: StructureOrigin = StructureOrigin.TREND
    broken_swing: SwingPoint | None = None  # None bei origin=RANGE
    prior_state: RegimeDirectional | None = None  # nur CHoCH
    break_distance_atr: float = 0.0
    strategy_version: str = STRATEGY_VERSION

    @property
    def break_id(self) -> str:
        """Deterministische ID (stabil über Neuberechnungen)."""
        return (
            f"SB-{self.kind.value}-{self.direction.value}-{self.origin.value}"
            f"-{self.timeframe.value}-{self.break_bar_timestamp.isoformat()}"
        )


# --------------------------------------------------------------------------------------------
# 4. / 5. Liquidity
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LiquidityLevel:
    type: LiquidityType
    side: MarketSide
    price: float
    timeframe: Timeframe
    formed_at: datetime
    strength: float = 0.0
    touch_count: int = 0
    state: LiquidityState = LiquidityState.UNSWEPT
    swept_at: datetime | None = None
    members: tuple[SwingPoint, ...] = ()  # bei equal_highs/lows: Cluster-Mitglieder
    spread_atr: float = 0.0  # bei Cluster: max-min der Member in ATR
    strategy_version: str = STRATEGY_VERSION

    @property
    def is_equal_cluster(self) -> bool:
        return self.type in (LiquidityType.EQUAL_HIGHS, LiquidityType.EQUAL_LOWS)


@dataclass(frozen=True, slots=True)
class LiquiditySweep:
    level: LiquidityLevel
    side: MarketSide
    timeframe: Timeframe
    penetration_bar: datetime
    penetration_extreme: float
    penetration_depth_atr: float
    reclaim_bar: datetime
    reclaim_close: float
    bars_to_reclaim: int
    wick_ratio: float = 0.0
    strategy_version: str = STRATEGY_VERSION


# --------------------------------------------------------------------------------------------
# 7. Displacement
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Displacement:
    direction: Polarity
    timeframe: Timeframe
    start_bar: datetime
    end_bar: datetime
    bars: int
    net_move_atr: float
    body_ratio: float
    start_index: int = -1
    end_index: int = -1
    fvgs: tuple[FVG, ...] = ()
    caused_structure_break: StructureBreak | None = None
    strategy_version: str = STRATEGY_VERSION


# --------------------------------------------------------------------------------------------
# 8. / 9. FVG / IFVG
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FVG:
    direction: Polarity
    timeframe: Timeframe
    zone_low: float
    zone_high: float
    created_bar: datetime  # Close-Zeit von Bar 3
    bar_index: int = -1  # Index von Bar 3 in der Quellserie
    state: ZoneState = ZoneState.UNMITIGATED
    fill_fraction: float = 0.0
    age_bars: int = 0
    from_displacement: bool = False  # liegt in einem Displacement gleicher Richtung (§7)
    kind: ZoneKind = ZoneKind.FVG
    strategy_version: str = STRATEGY_VERSION

    @property
    def zone_mid(self) -> float:
        return (self.zone_low + self.zone_high) / 2.0

    @property
    def height(self) -> float:
        return self.zone_high - self.zone_low


@dataclass(frozen=True, slots=True)
class IFVG:
    origin_fvg: FVG
    direction: Polarity  # invertiert ggü. origin
    timeframe: Timeframe
    zone_low: float
    zone_high: float
    flipped_at: datetime
    flip_bar_index: int = -1
    state: ZoneState = ZoneState.UNMITIGATED
    fill_fraction: float = 0.0
    age_bars: int = 0
    strategy_version: str = STRATEGY_VERSION

    @property
    def zone_mid(self) -> float:
        return (self.zone_low + self.zone_high) / 2.0


# --------------------------------------------------------------------------------------------
# 10. / 12. Order Block / Breaker
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OrderBlock:
    direction: Polarity
    timeframe: Timeframe
    zone_low: float
    zone_high: float
    ob_bar: datetime  # open_time der OB-Kerze
    bar_index: int = -1  # Index der OB-Kerze in der Quellserie (stabile ID)
    break_ref: StructureBreak | None = None
    displacement_ref: Displacement | None = None
    state: ZoneState = ZoneState.UNMITIGATED
    fill_fraction: float = 0.0
    age_bars: int = 0
    kind: ZoneKind = ZoneKind.ORDER_BLOCK
    strategy_version: str = STRATEGY_VERSION

    @property
    def zone_mid(self) -> float:
        return (self.zone_low + self.zone_high) / 2.0

    @property
    def height(self) -> float:
        return self.zone_high - self.zone_low

    @property
    def zone_id(self) -> str:
        """Deterministische ID (stabil über Neuberechnungen)."""
        return f"OB-{self.timeframe.value}-{self.direction.value}-{self.bar_index}"


@dataclass(frozen=True, slots=True)
class Breaker:
    origin_ob: OrderBlock
    direction: Polarity  # invertiert ggü. origin_ob
    timeframe: Timeframe
    zone_low: float
    zone_high: float
    flipped_at: datetime  # Close-Zeit der auslösenden BOS-Bar
    flip_bar_index: int = -1
    flip_break_ref: StructureBreak | None = None
    state: ZoneState = ZoneState.UNMITIGATED
    fill_fraction: float = 0.0
    age_bars: int = 0
    kind: ZoneKind = ZoneKind.BREAKER
    strategy_version: str = STRATEGY_VERSION

    @property
    def zone_mid(self) -> float:
        return (self.zone_low + self.zone_high) / 2.0

    @property
    def height(self) -> float:
        return self.zone_high - self.zone_low

    @property
    def zone_id(self) -> str:
        """Deterministische ID, an den Ursprungs-OB gebunden (stabil über Neuberechnungen)."""
        return f"BRK-{self.timeframe.value}-{self.direction.value}-{self.origin_ob.bar_index}"


# --------------------------------------------------------------------------------------------
# 13. Premium / Discount
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PremiumDiscount:
    reference: PDReference
    reference_tf: Timeframe
    range_low: float
    range_high: float
    price: float  # Preis, gegen den positioniert wurde
    pd_position: float  # (price - range_low) / (range_high - range_low); < 0 / > 1 = außerhalb
    zone: PDZone
    strategy_version: str = STRATEGY_VERSION

    @property
    def equilibrium(self) -> float:
        return self.range_low + 0.5 * (self.range_high - self.range_low)

    @property
    def favored_direction(self) -> Direction | None:
        """DISCOUNT bevorzugt Longs, PREMIUM Shorts, EQUILIBRIUM keinen bevorzugten Entry."""
        if self.zone is PDZone.DISCOUNT:
            return Direction.LONG
        if self.zone is PDZone.PREMIUM:
            return Direction.SHORT
        return None


# --------------------------------------------------------------------------------------------
# Struktur-Zustand (Ableitung aus der Swing-Folge)
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StructureState:
    timeframe: Timeframe
    directional: RegimeDirectional  # TREND_UP | TREND_DOWN | RANGE | UNCLEAR
    swings: tuple[SwingPoint, ...] = field(default_factory=tuple)
    last_swing_high: SwingPoint | None = None
    last_swing_low: SwingPoint | None = None

    @property
    def is_uptrend(self) -> bool:
        return self.directional is RegimeDirectional.TREND_UP

    @property
    def is_downtrend(self) -> bool:
        return self.directional is RegimeDirectional.TREND_DOWN


__all__ = [
    "FVG",
    "IFVG",
    "Breaker",
    "Displacement",
    "LiquidityLevel",
    "LiquiditySweep",
    "OrderBlock",
    "PremiumDiscount",
    "StructureBreak",
    "StructureState",
    "SwingPoint",
]
