"""Location-Gate (§8, Veto V2) + RR-Gate (§10 SL-Geometrie, §12–§14 TP, §16 RR) — ``SMC-SWEEP-REV-01``.

Diese beiden **Ketten-Gates** liegen zwischen der Setup-FSM (``ARMED``) und dem Entscheidungsblock
(Confirmation → Confluence → Veto → Confidence → Scoring → ``evaluate()``). Sie sind reine
**Geometrie über dem `MtfContext`** — keine neue Datenquelle, kein Score.

* **Location-Gate (§8):** Der **Mittelpunkt der Entry-Zone**, gemessen gegen das **gesweepte Leg**
  (Sweep-Extrem … Displacement-Extrem), muss im Discount (LONG) bzw. Premium (SHORT) liegen:
  ``pd_position(zone_mid) ≤ max_pd_position`` (LONG) / ``≥ 1 − max_pd_position`` (SHORT).
  Verletzung ⇒ ``BLOCK`` (``ENTRY_WRONG_SIDE_OF_EQUILIBRIUM``, Veto V2). Zusätzlich die harten
  Zonenfilter (Höhe, ``UNMITIGATED``, nicht ``STALE``).

* **RR-Gate (§10/§12–§16):**
  - **SL** = die **ungünstigere** (weiter entfernte) von {hinter dem Sweep-Extrem, hinter der
    distalen Zonenkante} + ``sl_buffer_atr × ATR(sweep_tf)``. Cap/Floor ⇒ ``SL_TOO_WIDE`` /
    ``SL_TOO_TIGHT`` (Veto V10). ``R = |entry − SL|``.
  - **TP1** = nächstgelegene opposing Liquidität in Richtung D, geklemmt in
    ``[entry ± tp1_min_r·R, entry ± tp1_r_multiple·R]``.
  - **TP2** = nächste **signifikante** opposing Liquidität (``strength ≥ tp2_significant_strength``)
    **oder** ein H4/M15-Swing-Level, geklemmt in ``[entry ± tp2_min_r·R, entry ± tp2_r_multiple·R]``.
  - **TP3** = Runner (kein fester Preis); für ``blended_RR`` konservativ ``tp3_assumed_r``.
  - Gates (§16): ``RR_to_TP2 ≥ rr_min_to_tp2`` · ``blended_RR ≥ rr_min_blended`` ·
    ``target_room ≥ rr_min_target_room_r`` (Distanz zur **ersten** opposing Liquidität). Verletzung
    ⇒ ``BLOCK`` (``RR_BELOW_MIN``, Veto V8).

**Konservativ:** Lässt sich die notwendige Geometrie nicht zuverlässig bestimmen (fehlende
Swept-Leg-Range, fehlendes ATR, fehlende Entry-Referenz) ⇒ ``WAIT`` (nicht ``ALLOW``).

**Look-ahead-frei / deterministisch:** alle Eingaben stammen aus dem ``MtfContext`` (≤
``information_cutoff``); die Gates sind reine Funktionen. **Long/Short-symmetrisch** (Spiegelung ⇒
``pd_position → 1 − pd_position``, alle Distanzen invariant).

**Alle Zahlen ``PROPOSED DEFAULT``** (``SMC-SWEEP-REV-01.md``) — Sensitivität noch zu validieren.
"""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Sequence
from datetime import datetime
from enum import StrEnum

from trading_agent.analysis.mtf import MtfContext
from trading_agent.core.enums import (
    Direction,
    EntryMode,
    MarketSide,
    NoTradeReason,
    PDReference,
    Polarity,
    Timeframe,
    VetoId,
    ZoneState,
)
from trading_agent.core.models import OHLCV
from trading_agent.core.version import STRATEGY_VERSION
from trading_agent.strategy.price_action import EntryConfirmation
from trading_agent.strategy.primitives.models import (
    FVG,
    Displacement,
    LiquidityLevel,
    LiquiditySweep,
    OrderBlock,
)
from trading_agent.strategy.primitives.pd import pd_position
from trading_agent.strategy.setup_detection import SetupCandidate

EntryZone = FVG | OrderBlock


class GateOutcome(StrEnum):
    ALLOW = "allow"
    BLOCK = "block"  # strukturell falsch (pd / RR / SL)
    WAIT = "wait"  # Geometrie (noch) nicht bestimmbar — konservativ, kein ALLOW


# --------------------------------------------------------------------------------- Parameter


@dataclasses.dataclass(frozen=True, slots=True)
class GateParams:
    sweep_timeframe: Timeframe = Timeframe.M15
    entry_timeframe: Timeframe = Timeframe.M5
    target_timeframes: tuple[Timeframe, ...] = (Timeframe.M15, Timeframe.H4, Timeframe.D1)
    structure_timeframes: tuple[Timeframe, ...] = (Timeframe.M15, Timeframe.H4)

    # §8 Location
    entry_mode: EntryMode = EntryMode.LIMIT_AT_PROXIMAL_EDGE
    max_pd_position: float = 0.50
    min_zone_height_atr: float = 0.15
    pd_reference: PDReference = PDReference.SWEPT_LEG

    # §10 Stop-Loss
    sl_buffer_atr: float = 0.50
    sl_max_distance_atr: float = 3.0
    sl_min_distance_atr: float = 0.40
    sl_min_spread_multiple: float = 5.0

    # §12–§14 Take-Profit
    tp1_r_multiple: float = 1.5
    tp1_min_r: float = 1.0
    tp1_size_pct: float = 50.0
    tp2_r_multiple: float = 3.0
    tp2_min_r: float = 2.0
    tp2_size_pct: float = 25.0
    tp2_significant_strength: float = 0.5
    tp3_size_pct: float = 25.0
    tp3_assumed_r: float = 2.5

    # §16 Mindest-RR
    rr_min_to_tp2: float = 2.0
    rr_min_blended: float = 1.3
    rr_min_target_room_r: float = 1.5


# ------------------------------------------------------------------------------- Ausgabe


@dataclasses.dataclass(frozen=True, slots=True)
class EntryGeometry:
    """Die abgeleitete SL-/TP-Geometrie (nur wenn ``R`` bestimmbar war)."""

    direction: Direction
    entry_mode: EntryMode
    entry: float
    sl: float
    tp1: float
    tp2: float
    tp3_ref: str  # Runner: kein fester Preis (Trailing M15, aktiv nach TP2)
    r_distance: float  # 1R = |entry − SL|
    rr_to_tp2: float
    blended_rr: float
    target_room_r: float  # Distanz Entry→erste opposing Liquidität, in R (inf ⇒ kein Hindernis)
    zone_pd_position: float
    swept_leg_low: float
    swept_leg_high: float
    zone_low: float
    zone_high: float
    tp1_from_structure: bool
    tp2_from_structure: bool
    strategy_version: str = STRATEGY_VERSION


@dataclasses.dataclass(frozen=True, slots=True)
class LocationCheck:
    outcome: GateOutcome
    pd_position: float | None = None
    swept_leg: tuple[float, float] | None = None
    zone_mid: float | None = None
    reason: NoTradeReason | None = None
    veto: VetoId | None = None
    note: str = ""


@dataclasses.dataclass(frozen=True, slots=True)
class RrCheck:
    outcome: GateOutcome
    geometry: EntryGeometry | None = None
    reasons: tuple[NoTradeReason, ...] = ()
    vetoes: tuple[VetoId, ...] = ()
    note: str = ""


@dataclasses.dataclass(frozen=True, slots=True)
class GateReport:
    location: LocationCheck
    rr: RrCheck | None = None  # None ⇒ bei blockierter Location nicht mehr ausgewertet
    strategy_version: str = STRATEGY_VERSION

    @property
    def outcome(self) -> GateOutcome:
        outs = [self.location.outcome] + ([self.rr.outcome] if self.rr is not None else [])
        if GateOutcome.BLOCK in outs:
            return GateOutcome.BLOCK
        if GateOutcome.WAIT in outs or self.rr is None:
            return GateOutcome.WAIT
        return GateOutcome.ALLOW

    @property
    def allowed(self) -> bool:
        return self.outcome is GateOutcome.ALLOW

    @property
    def geometry(self) -> EntryGeometry | None:
        return self.rr.geometry if self.rr is not None else None

    @property
    def reasons(self) -> tuple[NoTradeReason, ...]:
        out: list[NoTradeReason] = []
        if self.location.reason is not None:
            out.append(self.location.reason)
        if self.rr is not None:
            out.extend(self.rr.reasons)
        return tuple(dict.fromkeys(out))

    @property
    def vetoes(self) -> tuple[VetoId, ...]:
        out: list[VetoId] = []
        if self.location.veto is not None:
            out.append(self.location.veto)
        if self.rr is not None:
            out.extend(self.rr.vetoes)
        return tuple(dict.fromkeys(out))


# ------------------------------------------------------------------------------- öffentlich


def location_gate(
    mtf: MtfContext,
    candidate: SetupCandidate,
    *,
    params: GateParams | None = None,
) -> LocationCheck:
    """§8 — Zonen-Mitte gegen das gesweepte Leg im Discount (LONG) / Premium (SHORT)?"""
    p = params or GateParams()
    zone = candidate.entry_zone
    d = candidate.direction
    if zone is None or candidate.sweep is None or candidate.displacement is None:
        return LocationCheck(
            GateOutcome.BLOCK,
            reason=NoTradeReason.NO_ENTRY_ZONE,
            note="Entry-Zone/Sweep/Displacement fehlt",
        )

    zlow, zhigh = float(zone.zone_low), float(zone.zone_high)
    zmid = 0.5 * (zlow + zhigh)
    atr_e = _tf_atr(mtf, p.entry_timeframe)

    height = zhigh - zlow
    if height <= 0.0:
        return LocationCheck(
            GateOutcome.BLOCK, reason=NoTradeReason.NO_ENTRY_ZONE, note="Zone entartet"
        )
    if atr_e > 0.0 and height < p.min_zone_height_atr * atr_e:
        return LocationCheck(
            GateOutcome.BLOCK,
            reason=NoTradeReason.NO_ENTRY_ZONE,
            note="Zone zu dünn (< min_zone_height_atr)",
        )
    if zone.state is not ZoneState.UNMITIGATED:
        return LocationCheck(
            GateOutcome.BLOCK, reason=NoTradeReason.NO_ENTRY_ZONE, note=f"Zone {zone.state.value}"
        )

    leg = _swept_leg_range(
        candidate.sweep,
        candidate.displacement,
        _tf_bars(mtf, candidate.displacement.timeframe),
        cutoff=mtf.information_cutoff,
    )
    if leg is None:
        return LocationCheck(
            GateOutcome.WAIT, zone_mid=zmid, note="Swept-Leg-Range nicht bestimmbar"
        )

    pos = pd_position(zmid, leg[0], leg[1])
    ok = pos <= p.max_pd_position if d is Direction.LONG else pos >= (1.0 - p.max_pd_position)
    if not ok:
        return LocationCheck(
            GateOutcome.BLOCK,
            pd_position=pos,
            swept_leg=leg,
            zone_mid=zmid,
            reason=NoTradeReason.ENTRY_WRONG_SIDE_OF_EQUILIBRIUM,
            veto=VetoId.V2,
            note="Zonen-Mitte nicht im Discount/Premium des swept_leg",
        )
    return LocationCheck(GateOutcome.ALLOW, pd_position=pos, swept_leg=leg, zone_mid=zmid)


def rr_gate(
    mtf: MtfContext,
    candidate: SetupCandidate,
    *,
    confirmation: EntryConfirmation | None = None,
    spread: float | None = None,
    params: GateParams | None = None,
) -> RrCheck:
    """§10/§12–§16 — SL-/TP-Geometrie + Mindest-RR."""
    p = params or GateParams()
    zone = candidate.entry_zone
    d = candidate.direction
    if zone is None or candidate.sweep is None or candidate.displacement is None:
        return RrCheck(
            GateOutcome.BLOCK,
            reasons=(NoTradeReason.NO_ENTRY_ZONE,),
            note="Zone/Sweep/Displacement fehlt",
        )

    zlow, zhigh = float(zone.zone_low), float(zone.zone_high)
    sign = 1.0 if d is Direction.LONG else -1.0
    eff_spread = spread if spread is not None else mtf.market_context.spread

    entry = _entry_price(zlow, zhigh, d, p.entry_mode, confirmation)
    if entry is None:
        return RrCheck(
            GateOutcome.WAIT, note="Entry-Referenz fehlt (confirmation_market ohne Confirmation)"
        )

    atr_s = _tf_atr(mtf, p.sweep_timeframe)
    if atr_s <= 0.0:
        return RrCheck(GateOutcome.WAIT, note="ATR(sweep_tf) nicht verfügbar")

    leg = _swept_leg_range(
        candidate.sweep,
        candidate.displacement,
        _tf_bars(mtf, candidate.displacement.timeframe),
        cutoff=mtf.information_cutoff,
    )

    # --- SL (§10): die ungünstigere der beiden Kandidaten -------------------------
    buf = p.sl_buffer_atr * atr_s
    ext = candidate.sweep.penetration_extreme
    distal = zlow if d is Direction.LONG else zhigh
    sl = min(ext - buf, distal - buf) if d is Direction.LONG else max(ext + buf, distal + buf)
    r_dist = abs(entry - sl)
    if (
        r_dist <= 0.0
        or (d is Direction.LONG and sl >= entry)
        or (d is Direction.SHORT and sl <= entry)
    ):
        return RrCheck(
            GateOutcome.BLOCK,
            reasons=(NoTradeReason.SL_TOO_TIGHT,),
            vetoes=(VetoId.V10,),
            note="entartete SL-Geometrie",
        )
    if r_dist > p.sl_max_distance_atr * atr_s:
        return RrCheck(
            GateOutcome.BLOCK,
            reasons=(NoTradeReason.SL_TOO_WIDE,),
            vetoes=(VetoId.V10,),
            note=f"SL-Distanz {r_dist:.4f} > {p.sl_max_distance_atr}·ATR",
        )
    floor = p.sl_min_distance_atr * atr_s
    if eff_spread is not None:
        floor = max(floor, p.sl_min_spread_multiple * eff_spread)
    if r_dist < floor:
        return RrCheck(
            GateOutcome.BLOCK,
            reasons=(NoTradeReason.SL_TOO_TIGHT,),
            vetoes=(VetoId.V10,),
            note=f"SL-Distanz {r_dist:.4f} < Floor {floor:.4f}",
        )

    # --- opposing Liquidität + Struktur-Levels als Ziele -------------------------
    target_side = MarketSide.BUY_SIDE if d is Direction.LONG else MarketSide.SELL_SIDE
    opp = _opposing_levels(mtf, target_side, entry, sign, p.target_timeframes)
    struct = _structure_levels(mtf, d, entry, sign, p.structure_timeframes)

    first_opp = opp[0].price if opp else None
    target_room_r = abs(first_opp - entry) / r_dist if first_opp is not None else math.inf

    # --- TP1 (§12): nächste opposing Liquidität, geklemmt -----------------------
    tp1_floor = entry + sign * p.tp1_min_r * r_dist
    tp1_cap = entry + sign * p.tp1_r_multiple * r_dist
    tp1_raw = opp[0].price if opp else tp1_cap
    tp1 = _clamp_dir(tp1_raw, tp1_floor, tp1_cap, d)

    # --- TP2 (§13): nächste signifikante opposing Liquidität ODER Swing-Level ----
    sig_prices = [lv.price for lv in opp if lv.strength >= p.tp2_significant_strength]
    beyond_tp1 = [x for x in (*sig_prices, *struct) if sign * (x - tp1) > 0.0]
    tp2_from_structure = False
    if beyond_tp1:
        tp2_raw = min(beyond_tp1, key=lambda x: sign * (x - entry))
        tp2_from_structure = tp2_raw in struct and tp2_raw not in sig_prices
    else:
        tp2_raw = entry + sign * p.tp2_r_multiple * r_dist
    tp2_floor = entry + sign * p.tp2_min_r * r_dist
    tp2_cap = entry + sign * p.tp2_r_multiple * r_dist
    tp2 = _clamp_dir(tp2_raw, tp2_floor, tp2_cap, d)

    # --- RR-Prüfungen (§16) ---------------------------------------------------
    r1 = abs(tp1 - entry) / r_dist
    r2 = abs(tp2 - entry) / r_dist
    rr_to_tp2 = r2
    blended = (p.tp1_size_pct * r1 + p.tp2_size_pct * r2 + p.tp3_size_pct * p.tp3_assumed_r) / 100.0

    reasons: list[NoTradeReason] = []
    if rr_to_tp2 < p.rr_min_to_tp2:
        reasons.append(NoTradeReason.RR_BELOW_MIN)
    if blended < p.rr_min_blended:
        reasons.append(NoTradeReason.RR_BELOW_MIN)
    if target_room_r < p.rr_min_target_room_r:
        reasons.append(NoTradeReason.RR_BELOW_MIN)

    geom = EntryGeometry(
        direction=d,
        entry_mode=p.entry_mode,
        entry=round(entry, 8),
        sl=round(sl, 8),
        tp1=round(tp1, 8),
        tp2=round(tp2, 8),
        tp3_ref="runner: trailing M15, aktiv nach TP2",
        r_distance=round(r_dist, 8),
        rr_to_tp2=round(rr_to_tp2, 6),
        blended_rr=round(blended, 6),
        target_room_r=target_room_r if math.isinf(target_room_r) else round(target_room_r, 6),
        zone_pd_position=round(pd_position(0.5 * (zlow + zhigh), leg[0], leg[1]), 6)
        if leg is not None
        else -1.0,
        swept_leg_low=leg[0] if leg is not None else 0.0,
        swept_leg_high=leg[1] if leg is not None else 0.0,
        zone_low=zlow,
        zone_high=zhigh,
        tp1_from_structure=False,
        tp2_from_structure=tp2_from_structure,
    )
    note = "" if opp else "keine opposing Liquidität — TP aus R-Cap (Confidence sollte abwerten)"
    if reasons:
        return RrCheck(
            GateOutcome.BLOCK,
            geometry=geom,
            reasons=tuple(dict.fromkeys(reasons)),
            vetoes=(VetoId.V8,),
            note=note or "RR-Gate verletzt",
        )
    return RrCheck(GateOutcome.ALLOW, geometry=geom, note=note)


def evaluate_gates(
    mtf: MtfContext,
    candidate: SetupCandidate,
    *,
    confirmation: EntryConfirmation | None = None,
    spread: float | None = None,
    params: GateParams | None = None,
) -> GateReport:
    """Location-Gate → RR-Gate (kurzschließend). Nur sinnvoll für einen ``ARMED``-Kandidaten."""
    p = params or GateParams()
    loc = location_gate(mtf, candidate, params=p)
    if loc.outcome is not GateOutcome.ALLOW:
        return GateReport(location=loc, rr=None)
    rr = rr_gate(mtf, candidate, confirmation=confirmation, spread=spread, params=p)
    return GateReport(location=loc, rr=rr)


# ------------------------------------------------------------------------------- intern


def _tf_atr(mtf: MtfContext, tf: Timeframe) -> float:
    tfc = mtf.tf(tf)
    return tfc.atr if tfc is not None else 0.0


def _tf_bars(mtf: MtfContext, tf: Timeframe) -> tuple[OHLCV, ...]:
    tfc = mtf.tf(tf)
    return tfc.bars if tfc is not None else ()


def _entry_price(
    zone_low: float,
    zone_high: float,
    direction: Direction,
    mode: EntryMode,
    confirmation: EntryConfirmation | None,
) -> float | None:
    if mode is EntryMode.CONFIRMATION_MARKET:
        return confirmation.entry_ref_price if confirmation is not None else None
    if mode is EntryMode.LIMIT_AT_MID:
        return 0.5 * (zone_low + zone_high)
    # LIMIT_AT_PROXIMAL_EDGE — die dem retracenden Preis zugewandte Kante
    return zone_high if direction is Direction.LONG else zone_low


def _swept_leg_range(
    sweep: LiquiditySweep,
    displacement: Displacement,
    disp_bars: Sequence[OHLCV],
    *,
    cutoff: datetime,
) -> tuple[float, float] | None:
    """§13-Range: Sweep-Extrem … Displacement-Extrem. Zeitstempel-basiert (robust gegen Index-Drift)."""
    seg = [
        b
        for b in disp_bars
        if displacement.start_bar <= b.open_time <= displacement.end_bar and b.close_time <= cutoff
    ]
    if not seg:
        return None
    ext = (
        max(b.high for b in seg)
        if displacement.direction is Polarity.BULLISH
        else min(b.low for b in seg)
    )
    lo, hi = sorted((sweep.penetration_extreme, ext))
    return (lo, hi) if hi > lo else None


def _opposing_levels(
    mtf: MtfContext,
    target_side: MarketSide,
    entry: float,
    sign: float,
    timeframes: tuple[Timeframe, ...],
) -> list[LiquidityLevel]:
    seen: set[tuple[str, float]] = set()
    out: list[LiquidityLevel] = []
    for tf in timeframes:
        tfc = mtf.tf(tf)
        if tfc is None:
            continue
        for lv in tfc.liquidity:
            if lv.side is not target_side:
                continue
            if sign * (lv.price - entry) <= 0.0:  # muss in Richtung D jenseits des Entry liegen
                continue
            key = (lv.type.value, round(lv.price, 6))
            if key in seen:
                continue
            seen.add(key)
            out.append(lv)
    out.sort(key=lambda lv: sign * (lv.price - entry))  # nächstgelegene zuerst
    return out


def _structure_levels(
    mtf: MtfContext,
    direction: Direction,
    entry: float,
    sign: float,
    timeframes: tuple[Timeframe, ...],
) -> list[float]:
    out: list[float] = []
    for tf in timeframes:
        tfc = mtf.tf(tf)
        if tfc is None:
            continue
        sw = (
            tfc.structure.last_swing_high
            if direction is Direction.LONG
            else tfc.structure.last_swing_low
        )
        if sw is not None and sign * (sw.price - entry) > 0.0:
            out.append(sw.price)
    return out


def _clamp_dir(x: float, floor: float, cap: float, direction: Direction) -> float:
    lo, hi = (floor, cap) if direction is Direction.LONG else (cap, floor)
    return max(lo, min(hi, x))


__all__ = [
    "EntryGeometry",
    "GateOutcome",
    "GateParams",
    "GateReport",
    "LocationCheck",
    "RrCheck",
    "evaluate_gates",
    "location_gate",
    "rr_gate",
]
