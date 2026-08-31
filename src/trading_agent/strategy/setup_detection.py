"""SMC-SWEEP-REV-01 — kausale Setup-Kette / State Machine (``setups/SMC-SWEEP-REV-01.md`` §0, §24).

Konsumiert den ``MtfContext`` (``analysis.mtf``) und stellt **ausschließlich** fest, *wie weit* ein
konkretes Setup in seiner kausalen Entwicklung ist:

```
SCANNING → BIAS_SET → LIQUIDITY_IDENTIFIED → SWEPT → RECLAIMED
        → DISPLACED → STRUCTURE_SHIFTED → ARMED
```

Das ist **nicht** die finale ``BUY``/``SELL``-Entscheidung. Confirmation, Confluence, Veto, Score,
Confidence, Risk und das Dynamic Signal kommen später (``strategy.evaluate``). Der Übergang
``STRUCTURE_SHIFTED → ARMED`` bedeutet hier: *kausale Kette vollständig + Entry-Zone (FVG primär /
OB Fallback) identifiziert*. Location-Gate (V2), RR, Score, Confidence und die harten Vetos sind
nachgelagert.

**Kausalität (hart):** jedes Kettenglied setzt seinen Vorgänger **und die korrekte zeitliche
Reihenfolge** voraus. Ein einzelnes Ereignis erzeugt nie einen vollständigen State. Wird eine
notwendige Bedingung ungültig, bricht die Kette ab (→ ``SCANNING`` + ``abort_reason``) bzw. der
``ARMED``-Kandidat wird invalidiert (Klasse A, ``invalidation.md`` §2).

**Look-ahead:** es werden nur Primitive aus dem ``MtfContext`` gelesen (alle bis
``information_cutoff`` beschnitten). Keine neue Datenquelle, keine Zukunftsinformation.

Post-``ARMED``-Lebenszyklus (``TRIGGERED``/``MANAGED``/``CLOSED``/``REVIEW``) braucht
Order-/Positions-Zustand und ist nachgelagert — die Enum-Werte existieren, dieses Modul endet bei
``ARMED``.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from datetime import datetime, timedelta

from trading_agent.analysis.mtf import MtfContext
from trading_agent.core.enums import (
    Direction,
    DisplayAlias,
    LiquidityState,
    LiquidityType,
    MarketSide,
    NoTradeReason,
    Polarity,
    RegimeDirectional,
    SetupState,
    Timeframe,
    ZoneState,
)
from trading_agent.core.models import OHLCV
from trading_agent.core.version import STRATEGY_VERSION
from trading_agent.strategy.primitives.atr import atr_at_index, atr_series
from trading_agent.strategy.primitives.liquidity import (
    SweepParams,
    classify_level_state,
    resolve_sweep,
)
from trading_agent.strategy.primitives.models import (
    FVG,
    Displacement,
    LiquidityLevel,
    LiquiditySweep,
    OrderBlock,
    StructureBreak,
)

SETUP_TYPE = "SMC-SWEEP-REV-01"

_STATE_RANK: dict[SetupState, int] = {
    SetupState.SCANNING: 0,
    SetupState.BIAS_SET: 1,
    SetupState.LIQUIDITY_IDENTIFIED: 2,
    SetupState.SWEPT: 3,
    SetupState.RECLAIMED: 4,
    SetupState.DISPLACED: 5,
    SetupState.STRUCTURE_SHIFTED: 6,
    SetupState.ARMED: 7,
    SetupState.TRIGGERED: 8,
    SetupState.MANAGED: 9,
    SetupState.CLOSED: 10,
    SetupState.REVIEW: 11,
}
_RANK_STATE = {v: k for k, v in _STATE_RANK.items()}

_DEFAULT_LIQUIDITY_TYPES: frozenset[LiquidityType] = frozenset(
    {
        LiquidityType.EQUAL_LOWS,
        LiquidityType.EQUAL_HIGHS,
        LiquidityType.SESSION_LOW,
        LiquidityType.SESSION_HIGH,
        LiquidityType.PDL,
        LiquidityType.PDH,
        LiquidityType.PWL,
        LiquidityType.PWH,
        LiquidityType.SWING_LOW,
        LiquidityType.SWING_HIGH,
        LiquidityType.RANGE_LOW,
        LiquidityType.RANGE_HIGH,
    }
)
_SWING_TYPES = (LiquidityType.SWING_HIGH, LiquidityType.SWING_LOW)


# --------------------------------------------------------------------------------- Parameter


@dataclasses.dataclass(frozen=True, slots=True)
class SetupParams:
    """Alle Werte sind ``PROPOSED DEFAULT`` aus ``SMC-SWEEP-REV-01.md`` (empirisch zu validieren)."""

    sweep_timeframe: Timeframe = Timeframe.M15
    structure_timeframe: Timeframe = Timeframe.M5
    displacement_timeframe: Timeframe = Timeframe.M15
    entry_timeframe: Timeframe = Timeframe.M5
    htf_timeframes: tuple[Timeframe, Timeframe] = (Timeframe.D1, Timeframe.H4)

    # §3 relevante Liquidität
    liquidity_min_strength: float = 0.40
    liquidity_max_distance_atr: float = 5.0
    liquidity_freshness_bars: int = 50
    allowed_liquidity_types: frozenset[LiquidityType] = _DEFAULT_LIQUIDITY_TYPES

    # §4 Sweep
    sweep_min_penetration_atr: float = 0.05
    sweep_max_penetration_atr: float = 1.00
    sweep_max_reclaim_bars: int = 3
    sweep_require_wick: bool = True
    sweep_min_wick_ratio: float = 1.5
    # §5 Reclaim
    reclaim_min_close_beyond_atr: float = 0.10

    # §6 Displacement
    displacement_max_bars_after_reclaim: int = 3
    displacement_min_atr: float = 1.5
    displacement_min_body_ratio: float = 0.55

    # §7 Struktur-Shift
    structure_max_bars_after_displacement: int = 3
    structure_max_break_distance_atr: float = 4.0

    # §8 Entry-Zone
    entry_allow_ob_fallback: bool = True
    entry_min_zone_height_atr: float = 0.15

    # §15 Expiry
    armed_bars: int = 12

    atr_period: int = 14


def _sweep_params(p: SetupParams) -> SweepParams:
    return SweepParams(
        min_penetration_atr=p.sweep_min_penetration_atr,
        max_penetration_atr=p.sweep_max_penetration_atr,
        max_reclaim_bars=p.sweep_max_reclaim_bars,
        min_reclaim_atr=p.reclaim_min_close_beyond_atr,
        require_wick=p.sweep_require_wick,
        min_wick_ratio=p.sweep_min_wick_ratio,
        atr_period=p.atr_period,
    )


# ------------------------------------------------------------------------------- Ausgabe


@dataclasses.dataclass(frozen=True, slots=True)
class SetupCandidate:
    """Eine konkrete Setup-Instanz (eine Richtung × ein Liquiditäts-Pool) + ihr Ketten-Stand.

    ``setup_id`` ist **stabil** über Re-Evaluationen (an Instrument/Richtung/Pool gebunden).
    ``revision`` zählt hoch, sobald sich State oder ein Ketten-Anker ändert (nur mit ``previous``).
    """

    setup_id: str
    instrument: str
    direction: Direction
    state: SetupState
    revision: int
    created_at: datetime
    updated_at: datetime
    information_cutoff: datetime
    chain_progress: str
    liquidity: LiquidityLevel
    sweep: LiquiditySweep | None = None
    displacement: Displacement | None = None
    structure_break: StructureBreak | None = None
    entry_fvg: FVG | None = None
    entry_ob: OrderBlock | None = None
    abort_reason: NoTradeReason | None = None  # Kette abgebrochen → state == SCANNING
    invalidation: NoTradeReason | None = None  # Klasse-A-Invalidierung eines ARMED-Kandidaten
    setup_type: str = SETUP_TYPE
    strategy_version: str = STRATEGY_VERSION
    notes: tuple[str, ...] = ()

    @property
    def is_alive(self) -> bool:
        return self.abort_reason is None and self.invalidation is None

    @property
    def is_armed(self) -> bool:
        return self.state is SetupState.ARMED and self.is_alive

    @property
    def display_alias(self) -> DisplayAlias:
        if NoTradeReason.CANDIDATE_EXPIRED in (self.invalidation, self.abort_reason):
            return DisplayAlias.EXPIRED
        if self.invalidation is not None:
            return DisplayAlias.INVALIDATED
        return DisplayAlias.of(self.state)

    @property
    def entry_zone(self) -> FVG | OrderBlock | None:
        return self.entry_fvg if self.entry_fvg is not None else self.entry_ob

    @property
    def reclaim_bar(self) -> datetime | None:
        return self.sweep.reclaim_bar if self.sweep is not None else None

    def anchor_key(self) -> tuple[object, ...]:
        """Stabile Ketten-Anker für den Revisions-Vergleich."""
        zone = self.entry_zone
        return (
            self.direction.value,
            round(self.liquidity.price, 6),
            None if self.sweep is None else self.sweep.penetration_bar,
            None if self.displacement is None else self.displacement.start_bar,
            None if self.structure_break is None else self.structure_break.break_bar_timestamp,
            None if zone is None else (type(zone).__name__, round(zone.zone_mid, 6)),
        )


@dataclasses.dataclass(frozen=True, slots=True)
class SetupScan:
    """Ergebnis eines FSM-Laufs über einen ``MtfContext``."""

    instrument: str
    information_cutoff: datetime
    state: SetupState  # am weitesten fortgeschrittener Kandidat (Floor = BIAS_SET, wenn Bias steht)
    candidates: tuple[SetupCandidate, ...]
    regime_ok: bool
    no_trade_reason: NoTradeReason | None = None  # Regime-/Bias-Gate hat den ganzen Scan blockiert
    strategy_version: str = STRATEGY_VERSION

    @property
    def primary(self) -> SetupCandidate | None:
        return self.candidates[0] if self.candidates else None

    @property
    def alive(self) -> tuple[SetupCandidate, ...]:
        return tuple(c for c in self.candidates if c.is_alive)

    @property
    def armed(self) -> tuple[SetupCandidate, ...]:
        return tuple(c for c in self.candidates if c.is_armed)


# ------------------------------------------------------------------------------- öffentlich


def detect_setups(
    mtf: MtfContext,
    *,
    params: SetupParams | None = None,
    previous: Sequence[SetupCandidate] | None = None,
) -> SetupScan:
    """Schreibt die SMC-SWEEP-REV-01-Kette für **alle** aktuell erkennbaren Kandidaten fort.

    ``previous`` (die Kandidaten des letzten Laufs) trägt ``revision``/``created_at`` weiter — das
    Modul selbst ist zustandslos und deterministisch.
    """
    p = params or SetupParams()
    directions, gate_reason = _candidate_directions(mtf)
    if not directions:
        return SetupScan(
            instrument=mtf.instrument,
            information_cutoff=mtf.information_cutoff,
            state=SetupState.SCANNING,
            candidates=(),
            regime_ok=mtf.regime_ok,
            no_trade_reason=gate_reason,
        )

    cands: list[SetupCandidate] = []
    for d in directions:
        for level in _qualifying_pools(mtf, d, p):
            cands.append(_build_candidate(mtf, d, level, p, previous))

    cands.sort(key=lambda c: (_STATE_RANK[c.state], c.liquidity.strength), reverse=True)

    ranks = [_STATE_RANK[c.state] for c in cands] + [_STATE_RANK[SetupState.BIAS_SET]]
    overall = _RANK_STATE[max(ranks)]

    return SetupScan(
        instrument=mtf.instrument,
        information_cutoff=mtf.information_cutoff,
        state=overall,
        candidates=tuple(cands),
        regime_ok=mtf.regime_ok,
        no_trade_reason=None,
    )


# ------------------------------------------------------------------------------- Bias / Pools


def _candidate_directions(
    mtf: MtfContext,
) -> tuple[list[Direction], NoTradeReason | None]:
    if not mtf.regime_ok:
        return [], mtf.htf_regime_gate.reason
    d = mtf.htf_directional
    if d is RegimeDirectional.TREND_UP:
        return [Direction.LONG], None
    if d is RegimeDirectional.TREND_DOWN:
        return [Direction.SHORT], None
    if d is RegimeDirectional.RANGE:
        # Range-Variante: Richtung ergibt sich aus der gesweepten Grenze (§1/§2)
        return [Direction.LONG, Direction.SHORT], None
    return [], NoTradeReason.REGIME_UNCLEAR


def _qualifying_pools(mtf: MtfContext, d: Direction, p: SetupParams) -> list[LiquidityLevel]:
    """§3: mindestens ein ``LiquidityLevel`` entgegen ``D``, richtiger Typ, stark genug, nah genug."""
    against = MarketSide.against(d)
    m5c = mtf.m5
    price = m5c.last_close if m5c is not None else None
    sweep_tfc = mtf.tf(p.sweep_timeframe)
    ref_atr = (sweep_tfc.atr if sweep_tfc is not None else 0.0) or (
        m5c.atr if m5c is not None else 0.0
    )

    seen: set[tuple[LiquidityType, float]] = set()
    out: list[LiquidityLevel] = []
    for tf in (p.sweep_timeframe, *p.htf_timeframes):
        tfc = mtf.tf(tf)
        if tfc is None:
            continue
        for lvl in tfc.liquidity:
            if lvl.side is not against:
                continue
            if lvl.type not in p.allowed_liquidity_types:
                continue
            if lvl.type in _SWING_TYPES and tf not in p.htf_timeframes:
                continue  # Swing-Pools nur von HTF (Spec: H1/H4) — hier D1/H4
            if lvl.strength < p.liquidity_min_strength:
                continue
            if (
                price is not None
                and ref_atr > 0.0
                and abs(price - lvl.price) > p.liquidity_max_distance_atr * ref_atr
            ):
                continue
            key = (lvl.type, round(lvl.price, 6))
            if key in seen:
                continue
            seen.add(key)
            out.append(lvl)
    out.sort(key=lambda lv: lv.strength, reverse=True)
    return out


# ------------------------------------------------------------------------------- Ketten-Aufbau


def _bars_between(a: datetime, b: datetime, tf: Timeframe) -> float:
    return (b - a).total_seconds() / tf.seconds


def _build_candidate(
    mtf: MtfContext,
    d: Direction,
    level: LiquidityLevel,
    p: SetupParams,
    previous: Sequence[SetupCandidate] | None,
) -> SetupCandidate:
    cutoff = mtf.information_cutoff
    sid = _setup_id(mtf.instrument, d, level)
    pol = Polarity.of(d)
    m15c = mtf.tf(p.sweep_timeframe)
    m5c = mtf.tf(p.structure_timeframe)
    m15_bars = list(m15c.bars) if m15c is not None else []
    notes: list[str] = []

    sweep: LiquiditySweep | None = None
    disp: Displacement | None = None
    brk: StructureBreak | None = None
    fvg: FVG | None = None
    ob: OrderBlock | None = None

    def finish(
        st: SetupState,
        *,
        abort_reason: NoTradeReason | None = None,
        invalidation: NoTradeReason | None = None,
    ) -> SetupCandidate:
        progress = _chain_progress(level, st, sweep, disp, brk, fvg, ob, abort_reason, invalidation)
        anchors = _anchor_key(d, level, sweep, disp, brk, fvg, ob)
        revision, created = _revision(sid, st, anchors, previous, cutoff)
        return SetupCandidate(
            setup_id=sid,
            instrument=mtf.instrument,
            direction=d,
            state=st,
            revision=revision,
            created_at=created,
            updated_at=cutoff,
            information_cutoff=cutoff,
            chain_progress=progress,
            liquidity=level,
            sweep=sweep,
            displacement=disp,
            structure_break=brk,
            entry_fvg=fvg,
            entry_ob=ob,
            abort_reason=abort_reason,
            invalidation=invalidation,
            notes=tuple(notes),
        )

    if not m15_bars or m5c is None:
        return finish(SetupState.LIQUIDITY_IDENTIFIED)

    # (4)/(5) Sweep + Reclaim -----------------------------------------------------------
    sp = _sweep_params(p)
    kind, payload = _analyze_sweep(level, m15_bars, sp, p.atr_period)
    if kind == "breakout":
        return finish(SetupState.SCANNING, abort_reason=NoTradeReason.SWEEP_BECAME_BREAKOUT)
    if kind == "no_reclaim":
        return finish(SetupState.SCANNING, abort_reason=NoTradeReason.NO_RECLAIM)
    if kind == "none":
        return finish(SetupState.LIQUIDITY_IDENTIFIED)
    if kind == "swept":
        notes.append("Reclaim ausstehend (Penetration im Frist-Fenster)")
        return finish(SetupState.SWEPT)

    assert isinstance(payload, LiquiditySweep)
    sweep = payload
    bars_since_reclaim = _bars_between(sweep.reclaim_bar, cutoff, p.sweep_timeframe)
    if bars_since_reclaim > p.liquidity_freshness_bars:
        return finish(SetupState.SCANNING, abort_reason=NoTradeReason.CANDIDATE_EXPIRED)

    # (6) Displacement in Richtung D ---------------------------------------------------
    disp = _find_displacement(mtf, pol, sweep, p)
    if disp is None:
        if bars_since_reclaim > p.displacement_max_bars_after_reclaim:
            return finish(SetupState.SCANNING, abort_reason=NoTradeReason.NO_DISPLACEMENT)
        return finish(SetupState.RECLAIMED)

    # (7) CHoCH/BOS in Richtung D auf dem Struktur-TF ---------------------------------
    brk = _couple_structure_shift(mtf, pol, disp, p)
    if brk is None:
        bars_since_disp = _bars_between(disp.end_bar, cutoff, p.structure_timeframe)
        if bars_since_disp > p.structure_max_bars_after_displacement:
            return finish(SetupState.SCANNING, abort_reason=NoTradeReason.NO_STRUCTURE_SHIFT)
        return finish(SetupState.DISPLACED)

    # (8) Entry-Zone: jüngste unberührte FVG in Richtung D, sonst OB-Fallback ---------
    fvg, ob, zone_reason = _entry_zone(mtf, pol, disp, p)
    if zone_reason is not None:
        return finish(SetupState.SCANNING, abort_reason=zone_reason)

    # STRUCTURE_SHIFTED → ARMED: kausale Kette komplett + Entry-Zone vorhanden.
    # Location (V2) / RR / Score / Confidence / Veto sind nachgelagert.

    # Klasse-A-Invalidierung (pre-entry, ohne Fill-Tracking) --------------------------
    inval = _class_a_invalidation(mtf, d, level, sweep, disp, brk, p)
    if inval is not None:
        return finish(SetupState.SCANNING, invalidation=inval)

    bars_since_shift = _bars_between(brk.break_bar_timestamp, cutoff, p.entry_timeframe)
    if bars_since_shift > p.armed_bars:
        return finish(SetupState.SCANNING, invalidation=NoTradeReason.CANDIDATE_EXPIRED)

    return finish(SetupState.ARMED)


# ------------------------------------------------------------------------------- Kettenglieder


def _analyze_sweep(
    level: LiquidityLevel,
    m15_bars: Sequence[OHLCV],
    sp: SweepParams,
    atr_period: int,
) -> tuple[str, object | None]:
    """→ ``("reclaimed", LiquiditySweep)`` | ``("swept", ...)`` | ``("no_reclaim", ...)``
    | ``("breakout", None)`` | ``("none", None)``."""
    sweep = resolve_sweep(level, m15_bars, sp)
    if sweep is not None:
        return "reclaimed", sweep

    st, _ts, _s = classify_level_state(level, m15_bars, sp)
    if st is LiquidityState.BROKEN:
        return "breakout", None

    aser = atr_series(m15_bars, atr_period)
    buy = level.side is MarketSide.BUY_SIDE
    n = len(m15_bars)
    last_pen: int | None = None
    for pi in range(n):
        b = m15_bars[pi]
        if b.close_time <= level.formed_at:
            continue
        ap = atr_at_index(aser, pi) or 0.0
        if ap <= 0.0:
            continue
        pen = (b.high - level.price) if buy else (level.price - b.low)
        if sp.min_penetration_atr * ap <= pen <= sp.max_penetration_atr * ap:
            last_pen = pi
    if last_pen is None:
        return "none", None
    if last_pen >= n - 1 - sp.max_reclaim_bars:
        return "swept", m15_bars[last_pen].open_time  # Reclaim noch in der Frist möglich
    return "no_reclaim", m15_bars[last_pen].open_time


def _find_displacement(
    mtf: MtfContext, pol: Polarity, sweep: LiquiditySweep, p: SetupParams
) -> Displacement | None:
    tfc = mtf.tf(p.displacement_timeframe)
    if tfc is None:
        return None
    cutoff = mtf.information_cutoff
    limit = p.displacement_max_bars_after_reclaim
    best: Displacement | None = None
    for dsp in tfc.displacements:
        if dsp.direction is not pol:
            continue
        if dsp.end_bar > cutoff:  # Look-ahead-Schutz
            continue
        if dsp.start_bar < sweep.reclaim_bar:  # Kausalität: erst nach dem Reclaim
            continue
        if _bars_between(sweep.reclaim_bar, dsp.start_bar, p.displacement_timeframe) > limit:
            continue
        if dsp.net_move_atr < p.displacement_min_atr:
            continue
        if dsp.body_ratio < p.displacement_min_body_ratio:
            continue
        if not dsp.fvgs:
            continue
        if best is None or dsp.start_bar < best.start_bar:
            best = dsp
    return best


def _couple_structure_shift(
    mtf: MtfContext, pol: Polarity, disp: Displacement, p: SetupParams
) -> StructureBreak | None:
    tfc = mtf.tf(p.structure_timeframe)
    if tfc is None:
        return None
    cutoff = mtf.information_cutoff
    lo = disp.start_bar
    hi = disp.end_bar + timedelta(
        seconds=p.structure_timeframe.seconds * p.structure_max_bars_after_displacement
    )
    atr_m5 = tfc.atr

    # bevorzugt: der vom Displacement direkt getragene Bruch (gleiche TF)
    dcb = disp.caused_structure_break
    if (
        dcb is not None
        and dcb.direction is pol
        and dcb.break_bar_timestamp <= cutoff
        and lo <= dcb.break_bar_timestamp <= hi
    ):
        return dcb

    cand: StructureBreak | None = None
    for b in tfc.structure_breaks:
        if b.direction is not pol:
            continue
        if b.break_bar_timestamp > cutoff:  # Look-ahead-Schutz
            continue
        if not (lo <= b.break_bar_timestamp <= hi):
            continue
        if atr_m5 > 0.0 and b.break_distance_atr > p.structure_max_break_distance_atr:
            continue
        if cand is None or b.break_bar_timestamp < cand.break_bar_timestamp:
            cand = b
    return cand


def _entry_zone(
    mtf: MtfContext, pol: Polarity, disp: Displacement, p: SetupParams
) -> tuple[FVG | None, OrderBlock | None, NoTradeReason | None]:
    tfc = mtf.tf(p.entry_timeframe)
    if tfc is None:
        return None, None, NoTradeReason.NO_ENTRY_ZONE
    cutoff = mtf.information_cutoff
    atr_e = tfc.atr
    min_h = p.entry_min_zone_height_atr * atr_e if atr_e > 0.0 else 0.0

    fvgs = [
        f
        for f in tfc.fvgs
        if f.direction is pol
        and f.state is ZoneState.UNMITIGATED
        and f.height >= min_h
        and disp.start_bar <= f.created_bar <= cutoff
    ]
    if fvgs:
        fvgs.sort(key=lambda f: f.created_bar, reverse=True)  # jüngste unberührte FVG
        return fvgs[0], None, None

    if p.entry_allow_ob_fallback:
        obs = [
            o
            for o in tfc.order_blocks
            if o.direction is pol
            and o.state is ZoneState.UNMITIGATED
            and o.height >= min_h
            and o.ob_bar <= disp.end_bar
            and o.ob_bar <= cutoff
        ]
        if obs:
            obs.sort(key=lambda o: o.ob_bar, reverse=True)
            return None, obs[0], None

    return None, None, NoTradeReason.NO_ENTRY_ZONE


def _class_a_invalidation(
    mtf: MtfContext,
    d: Direction,
    level: LiquidityLevel,
    sweep: LiquiditySweep,
    disp: Displacement,
    brk: StructureBreak,
    p: SetupParams,
) -> NoTradeReason | None:
    """``invalidation.md`` §2 — Kandidaten-Invalidierung, solange ``ARMED`` und kein Fill."""
    pol = Polarity.of(d)
    opp = pol.opposite
    cutoff = mtf.information_cutoff
    m15c = mtf.tf(p.sweep_timeframe)
    m5c = mtf.tf(p.structure_timeframe)
    dspc = mtf.tf(p.displacement_timeframe)
    buy_side = sweep.side is MarketSide.BUY_SIDE
    ext = sweep.penetration_extreme

    # RE_SWEEP: confirmed close erneut jenseits des Sweep-Extrems (nach dem Reclaim)
    if m15c is not None:
        for b in m15c.bars:
            if b.open_time <= sweep.reclaim_bar or b.close_time > cutoff:
                continue
            if (buy_side and b.close > ext) or (not buy_side and b.close < ext):
                return NoTradeReason.CANDIDATE_INVALIDATED

    # COUNTER_DISPLACEMENT: Displacement gegen D nach unserem Displacement
    if dspc is not None:
        for x in dspc.displacements:
            if x.direction is opp and disp.end_bar < x.start_bar and x.end_bar <= cutoff:
                return NoTradeReason.CANDIDATE_INVALIDATED

    # COUNTER_CHOCH: Struktur-Bruch gegen D nach unserem Bruch
    if m5c is not None:
        for sb in m5c.structure_breaks:
            if (
                sb.direction is opp
                and sb.break_bar_timestamp > brk.break_bar_timestamp
                and sb.break_bar_timestamp <= cutoff
            ):
                return NoTradeReason.CANDIDATE_INVALIDATED

    # BIAS_LOST / REGIME_LOST
    if not mtf.regime_ok:
        return NoTradeReason.CANDIDATE_INVALIDATED
    bias_dir = mtf.htf_bias.as_direction()
    if (
        mtf.htf_directional is not RegimeDirectional.RANGE
        and bias_dir is not None
        and bias_dir is not d
    ):
        return NoTradeReason.CANDIDATE_INVALIDATED

    return None


# ------------------------------------------------------------------------------- IDs / Revision


def _setup_id(instrument: str, d: Direction, level: LiquidityLevel) -> str:
    return f"{SETUP_TYPE}:{instrument}:{d.value}:{level.type.value}@{round(level.price, 6)}"


def _anchor_key(
    d: Direction,
    level: LiquidityLevel,
    sweep: LiquiditySweep | None,
    disp: Displacement | None,
    brk: StructureBreak | None,
    fvg: FVG | None,
    ob: OrderBlock | None,
) -> tuple[object, ...]:
    zone: FVG | OrderBlock | None = fvg if fvg is not None else ob
    return (
        d.value,
        round(level.price, 6),
        None if sweep is None else sweep.penetration_bar,
        None if disp is None else disp.start_bar,
        None if brk is None else brk.break_bar_timestamp,
        None if zone is None else (type(zone).__name__, round(zone.zone_mid, 6)),
    )


def _revision(
    setup_id: str,
    state: SetupState,
    anchors: tuple[object, ...],
    previous: Sequence[SetupCandidate] | None,
    cutoff: datetime,
) -> tuple[int, datetime]:
    if previous:
        for pc in previous:
            if pc.setup_id != setup_id:
                continue
            changed = (pc.state is not state) or (pc.anchor_key() != anchors)
            return pc.revision + (1 if changed else 0), pc.created_at
    return 1, cutoff


# ------------------------------------------------------------------------------- Darstellung


def _chain_progress(
    level: LiquidityLevel,
    state: SetupState,
    sweep: LiquiditySweep | None,
    disp: Displacement | None,
    brk: StructureBreak | None,
    fvg: FVG | None,
    ob: OrderBlock | None,
    abort: NoTradeReason | None,
    inval: NoTradeReason | None,
) -> str:
    parts = ["BIAS_SET", f"LIQUIDITY({level.type.value}@{level.price:.2f})"]
    if _STATE_RANK[state] >= _STATE_RANK[SetupState.SWEPT] or sweep is not None:
        parts.append("SWEPT")
    if sweep is not None:
        parts.append(f"RECLAIMED(+{sweep.bars_to_reclaim}b)")
    if disp is not None:
        parts.append(f"DISPLACED({disp.net_move_atr:.1f}ATR)")
    if brk is not None:
        parts.append(f"STRUCTURE_SHIFTED({brk.kind.value})")
    if state is SetupState.ARMED:
        zk = "FVG" if fvg is not None else ("OB" if ob is not None else "?")
        parts.append(f"ARMED(zone={zk})")
    if abort is not None:
        parts.append(f"✗ abort:{abort.value}")
    if inval is not None:
        parts.append(f"✗ invalidated:{inval.value}")
    return " → ".join(parts)


__all__ = [
    "SETUP_TYPE",
    "SetupCandidate",
    "SetupParams",
    "SetupScan",
    "detect_setups",
]
