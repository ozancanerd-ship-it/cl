"""Confluence-Bewertung — führt die einzelnen Analysebausteine zu **einer erklärbaren
Evidenz-Bilanz** zusammen (``scoring-rubric.md``, ``contradictions.md`` §2/§4/§5).

**Confluence löst kein BUY/SELL aus.** Sie ist die Schicht zwischen den Ketten-Gates und dem Score:
sie sammelt, welche bereits validierten Faktoren ein Setup **unterstützen** oder ihm
**widersprechen**, macht das für die App **erklärbar** und misst dabei **Information statt
Faktor-Anzahl**.

**Kein Double-Counting:**
* Korrelierte Faktoren teilen sich eine **Gruppe** (eine unabhängige Informationsdimension).
* Innerhalb einer Gruppe wird der **relevanz-gewichtete Durchschnitt** gebildet — ein redundanter,
  gleichgerichteter Faktor verschiebt das Gruppen-Ergebnis **nicht**.
* BOS/CHoCH werden **nie getrennt** geführt — es gibt genau **einen** ``structure_shift``
  (aus ``candidate.structure_break.kind``).
* ``net_confluence`` = gewichtetes Mittel der **verfügbaren** gescorten Gruppen.

**Kontext statt Score** (``scored=False`` — fließt in Confidence/Veto, **nicht** in
``net_confluence``): ``mtf_disagreement``, ``volatility_regime``, ``phase_compression``,
``session_context``, ``data_confidence``.

**Fehlende Daten** (``news`` / ``derivatives`` / ``cross_asset`` leer): Faktor
``data_quality = UNAVAILABLE``, Beitrag 0, Gruppe **aus dem Nenner ausgeschlossen** — keine
künstliche positive oder negative Bewertung. Eine später verfügbare Quelle ändert nur die
Extraktion, nicht die Aggregation.

**Point-in-time / look-ahead-frei:** alle Eingaben aus ``MtfContext`` (≤ ``information_cutoff``);
News nur aus ``market_context.news.events`` (bereits PIT-gefiltert). Rein funktional ⇒
deterministisch replaybar. **Long/Short-symmetrisch** (gespiegelte Preise ⇒ gespiegelte
Richtungs-Faktoren, identische Beträge).

Alle Zahlen ``PROPOSED DEFAULT``. Der Aggregations-Ansatz (Gruppen-Durchschnitt) ist bewusst
austauschbar — Log-Odds-Kombination ist als Upgrade nach dem ersten OOS-Backtest vorgesehen
(``docs/CONTINUOUS_IMPROVEMENT.md`` §6c).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from datetime import datetime
from enum import StrEnum

from trading_agent.analysis.mtf import MtfContext, TimeframeContext
from trading_agent.core.enums import (
    Direction,
    LiquidityType,
    Polarity,
    RegimeDirectional,
    RegimePhase,
    RegimeVolatility,
    SessionName,
    StructureBreakKind,
    Timeframe,
    VetoId,
    ZoneState,
)
from trading_agent.core.version import STRATEGY_VERSION
from trading_agent.strategy.gates import GateOutcome, GateReport
from trading_agent.strategy.price_action import ConfirmationScan
from trading_agent.strategy.primitives.models import FVG, OrderBlock
from trading_agent.strategy.setup_detection import SetupCandidate

# --------------------------------------------------------------------------------- Enums


class FactorDirection(StrEnum):
    SUPPORT = "support"
    CONTRADICT = "contradict"
    NEUTRAL = "neutral"


class ConfluenceDataQuality(StrEnum):
    OK = "ok"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


class ConfluenceGroup(StrEnum):
    HTF_BIAS = "htf_bias"
    LIQUIDITY_EVENT = "liquidity_event"
    MOMENTUM_STRUCTURE = "momentum_structure"
    ENTRY_ZONE = "entry_zone"
    LOCATION = "location"
    RISK_REWARD = "risk_reward"
    CONFIRMATION = "confirmation"
    EXTERNAL_CONTEXT = "external_context"
    # Kontext-Gruppen (scored=False)
    MTF_COHERENCE = "mtf_coherence"
    REGIME_FILTER = "regime_filter"
    SESSION = "session"
    DATA_QUALITY = "data_quality"


class ConfluenceRole(StrEnum):
    ENTRY_SUPPORT = "entry_support"
    CONTEXT = "context"
    EXIT_RELEVANT = "exit_relevant"
    VETO_CANDIDATE = "veto_candidate"


_SCORED_GROUPS: frozenset[ConfluenceGroup] = frozenset(
    {
        ConfluenceGroup.HTF_BIAS,
        ConfluenceGroup.LIQUIDITY_EVENT,
        ConfluenceGroup.MOMENTUM_STRUCTURE,
        ConfluenceGroup.ENTRY_ZONE,
        ConfluenceGroup.LOCATION,
        ConfluenceGroup.RISK_REWARD,
        ConfluenceGroup.CONFIRMATION,
        ConfluenceGroup.EXTERNAL_CONTEXT,
    }
)


# --------------------------------------------------------------------------------- Parameter


def _equal_group_weights() -> dict[ConfluenceGroup, float]:
    return {g: 1.0 for g in ConfluenceGroup}


@dataclasses.dataclass(frozen=True, slots=True)
class ConfluenceParams:
    group_weights: dict[ConfluenceGroup, float] = dataclasses.field(
        default_factory=_equal_group_weights
    )
    support_threshold: float = 0.15  # |raw| darunter ⇒ NEUTRAL
    htf_timeframes: tuple[Timeframe, ...] = (Timeframe.D1, Timeframe.H4)
    filter_timeframes: tuple[Timeframe, ...] = (Timeframe.D1, Timeframe.H4, Timeframe.M15)
    sweep_timeframe: Timeframe = Timeframe.M15
    entry_timeframe: Timeframe = Timeframe.M5
    data_confidence_floor: float = 0.50
    opposing_zone_buffer_atr: float = 0.5  # contradictions.md C9
    opposing_zone_overlap_veto: float = 0.50


# --------------------------------------------------------------------------------- Ausgabe


@dataclasses.dataclass(frozen=True, slots=True)
class ConfluenceFactor:
    factor: str
    factor_group: ConfluenceGroup
    role: ConfluenceRole
    direction: FactorDirection
    contribution: float  # signierter Roh-Beitrag [-1, 1] Richtung Setup-Direction
    relevance: float  # Relevanz-Gewicht innerhalb der Gruppe
    reason: str
    timestamp: datetime | None  # wann die zugrunde liegende Evidenz entstand
    information_cutoff: datetime
    data_quality: ConfluenceDataQuality
    scored: bool  # fließt in net_confluence?


@dataclasses.dataclass(frozen=True, slots=True)
class ConfluenceGroupResult:
    group: ConfluenceGroup
    weight: float
    net: float  # relevanz-gewichtetes Mittel der verfügbaren Mitglieder [-1, 1]
    scored: bool
    available: bool  # ≥ 1 Mitglied mit data_quality != UNAVAILABLE
    member_count: int
    note: str = ""


@dataclasses.dataclass(frozen=True, slots=True)
class ConfluenceReport:
    direction: Direction
    information_cutoff: datetime
    factors: tuple[ConfluenceFactor, ...]
    groups: tuple[ConfluenceGroupResult, ...]
    net_confluence: float  # [-1, 1] — de-dupliziertes gerichtetes Evidenz-Mittel
    support_score: float  # [0, 1] — für das Scoring (0.5 = neutral)
    agreement: float  # Anteil der gescorten, nicht-neutralen Faktoren, die zustimmen
    contradiction_flags: tuple[str, ...]  # harte Widersprüche für Veto/Contradiction-Matrix
    unavailable: tuple[str, ...]  # aktuell fehlende Datenquellen
    strategy_version: str = STRATEGY_VERSION

    @property
    def supporting(self) -> tuple[ConfluenceFactor, ...]:
        return tuple(f for f in self.factors if f.direction is FactorDirection.SUPPORT)

    @property
    def contradicting(self) -> tuple[ConfluenceFactor, ...]:
        return tuple(f for f in self.factors if f.direction is FactorDirection.CONTRADICT)

    @property
    def context_factors(self) -> tuple[ConfluenceFactor, ...]:
        return tuple(f for f in self.factors if not f.scored)

    def group(self, g: ConfluenceGroup) -> ConfluenceGroupResult | None:
        return next((gr for gr in self.groups if gr.group is g), None)


# --------------------------------------------------------------------------------- öffentlich


def assess_confluence(
    mtf: MtfContext,
    candidate: SetupCandidate,
    *,
    gates: GateReport | None = None,
    confirmation: ConfirmationScan | None = None,
    session_names: set[SessionName] | None = None,
    params: ConfluenceParams | None = None,
) -> ConfluenceReport:
    """Bewertet die Confluence **für die Richtung des Kandidaten**. ``gates`` / ``confirmation`` /
    ``session_names`` sind optional — fehlen sie, wird die jeweilige Gruppe als *unavailable*
    behandelt (nicht negativ)."""
    p = params or ConfluenceParams()
    cutoff = mtf.information_cutoff
    d = candidate.direction
    sign = float(d.sign)

    factors: list[ConfluenceFactor] = []
    factors += _htf_bias_factors(mtf, d, sign, cutoff, p)
    factors += _liquidity_event_factors(mtf, candidate, cutoff, p)
    factors += _momentum_structure_factors(mtf, candidate, cutoff, p)
    factors += _entry_zone_factors(mtf, candidate, d, cutoff, p)
    factors += _location_factors(candidate, d, gates, cutoff, p)
    factors += _risk_reward_factors(gates, cutoff, p)
    factors += _confirmation_factors(candidate, confirmation, cutoff, p)
    factors += _external_context_factors(mtf, cutoff, p)
    # Kontext (scored=False)
    factors += _mtf_coherence_factors(mtf, d, sign, cutoff, p)
    factors += _regime_filter_factors(mtf, d, cutoff, p)
    factors += _session_factors(session_names, cutoff, p)
    factors += _data_quality_factors(mtf, cutoff, p)

    factors_t = tuple(factors)
    groups = _aggregate_groups(factors_t, p)
    net = _net_confluence(groups)
    support = _clip(0.5 + 0.5 * net, 0.0, 1.0)
    agreement = _agreement(factors_t)
    flags = _contradiction_flags(mtf, candidate, d, sign, gates, factors_t, p)
    unavailable = tuple(
        sorted({f.factor for f in factors_t if f.data_quality is ConfluenceDataQuality.UNAVAILABLE})
    )

    return ConfluenceReport(
        direction=d,
        information_cutoff=cutoff,
        factors=factors_t,
        groups=groups,
        net_confluence=round(net, 6),
        support_score=round(support, 6),
        agreement=round(agreement, 6),
        contradiction_flags=flags,
        unavailable=unavailable,
    )


# --------------------------------------------------------------------------------- Aggregation


def _aggregate_groups(
    factors: Sequence[ConfluenceFactor], p: ConfluenceParams
) -> tuple[ConfluenceGroupResult, ...]:
    out: list[ConfluenceGroupResult] = []
    for g in ConfluenceGroup:
        members = [f for f in factors if f.factor_group is g]
        if not members:
            continue
        avail = [f for f in members if f.data_quality is not ConfluenceDataQuality.UNAVAILABLE]
        if avail:
            wsum = sum(f.relevance for f in avail) or 1.0
            net = sum(f.relevance * f.contribution for f in avail) / wsum
        else:
            net = 0.0
        out.append(
            ConfluenceGroupResult(
                group=g,
                weight=p.group_weights.get(g, 1.0),
                net=round(_clip(net, -1.0, 1.0), 6),
                scored=g in _SCORED_GROUPS,
                available=bool(avail),
                member_count=len(members),
            )
        )
    return tuple(out)


def _net_confluence(groups: Sequence[ConfluenceGroupResult]) -> float:
    active = [g for g in groups if g.scored and g.available]
    wsum = sum(g.weight for g in active)
    if wsum <= 0.0:
        return 0.0
    return _clip(sum(g.net * g.weight for g in active) / wsum, -1.0, 1.0)


def _agreement(factors: Sequence[ConfluenceFactor]) -> float:
    rel = [
        f
        for f in factors
        if f.scored
        and f.direction is not FactorDirection.NEUTRAL
        and f.data_quality is not ConfluenceDataQuality.UNAVAILABLE
    ]
    if not rel:
        return 0.0
    return sum(1 for f in rel if f.direction is FactorDirection.SUPPORT) / len(rel)


def _contradiction_flags(
    mtf: MtfContext,
    candidate: SetupCandidate,
    d: Direction,
    sign: float,
    gates: GateReport | None,
    factors: Sequence[ConfluenceFactor],
    p: ConfluenceParams,
) -> tuple[str, ...]:
    flags: list[str] = []
    if mtf.htf_directional is RegimeDirectional.CONFLICTING:
        flags.append("htf_conflict:V1")
    else:
        dn = {tf: _dir_num(_tf_regime_directional(mtf, tf)) for tf in (Timeframe.D1, Timeframe.H4)}
        vals = [v for v in dn.values() if v != 0]
        if len(vals) == 2 and vals[0] != vals[1]:
            flags.append("htf_conflict:V1")

    worst_vol = _worst_volatility(mtf, p.filter_timeframes)
    if worst_vol is RegimeVolatility.EXTREME:
        flags.append("regime_vol_extreme:V3")
    elif worst_vol is RegimeVolatility.LOW:
        flags.append("regime_vol_low:V3")
    if _any_coiled_compression(mtf, p.filter_timeframes):
        flags.append("regime_phase_compression:V3")

    if gates is not None:
        if gates.location.veto is VetoId.V2:  # nur die pd-Position, nicht NO_ENTRY_ZONE
            flags.append("location_block:V2")
        if gates.rr is not None and VetoId.V8 in gates.rr.vetoes:
            flags.append("rr_block:V8")
        if gates.rr is not None and VetoId.V10 in gates.rr.vetoes:
            flags.append("sl_undefinable:V10")

    news = mtf.market_context.news
    if news.feed_available and (news.risk_off or news.blocking_event_id is not None):
        flags.append("news_blocking:V4")

    if mtf.data_confidence < p.data_confidence_floor:
        flags.append("data_confidence_floor:V6")

    if any(
        f.factor == "opposing_htf_zone_proximity"
        and f.contribution <= -p.opposing_zone_overlap_veto
        for f in factors
    ):
        flags.append("opposing_htf_zone:C9")

    return tuple(dict.fromkeys(flags))


# --------------------------------------------------------------------------------- Faktor-Gruppen


def _htf_bias_factors(
    mtf: MtfContext, d: Direction, sign: float, cutoff: datetime, p: ConfluenceParams
) -> list[ConfluenceFactor]:
    out: list[ConfluenceFactor] = []
    merged = mtf.htf_directional
    m_raw = _dir_num(merged) * sign
    gate = mtf.htf_regime_gate
    conf = _clip(1.0 - gate.disagreement, 0.0, 1.0)
    if merged is RegimeDirectional.RANGE:
        m_raw, reason = 0.0, "HTF merged = RANGE (Range-Variante, keine gerichtete HTF-Stütze)"
    else:
        reason = f"HTF merged directional = {merged.value} ({'mit' if m_raw > 0 else 'gegen'} D)"
    out.append(
        _mk(
            "htf_merged_alignment",
            ConfluenceGroup.HTF_BIAS,
            ConfluenceRole.ENTRY_SUPPORT,
            m_raw * conf,
            1.0,
            reason,
            _tf_time(mtf, Timeframe.D1),
            cutoff,
            ConfluenceDataQuality.OK,
            True,
            p,
        )
    )
    for tf in p.htf_timeframes:
        sdir = _tf_structure_directional(mtf, tf)
        raw = _dir_num(sdir) * sign
        out.append(
            _mk(
                f"{tf.value.lower()}_structure_alignment",
                ConfluenceGroup.HTF_BIAS,
                ConfluenceRole.ENTRY_SUPPORT,
                raw,
                0.7,
                f"{tf.value} Struktur = {sdir.value}",
                _tf_time(mtf, tf),
                cutoff,
                ConfluenceDataQuality.OK,
                True,
                p,
            )
        )
    return out


def _liquidity_event_factors(
    mtf: MtfContext, cand: SetupCandidate, cutoff: datetime, p: ConfluenceParams
) -> list[ConfluenceFactor]:
    lvl = cand.liquidity
    type_bonus = {
        LiquidityType.EQUAL_HIGHS: 1.0,
        LiquidityType.EQUAL_LOWS: 1.0,
        LiquidityType.SESSION_HIGH: 0.8,
        LiquidityType.SESSION_LOW: 0.8,
        LiquidityType.PDH: 0.8,
        LiquidityType.PDL: 0.8,
        LiquidityType.PWH: 0.8,
        LiquidityType.PWL: 0.8,
        LiquidityType.RANGE_HIGH: 0.7,
        LiquidityType.RANGE_LOW: 0.7,
    }.get(lvl.type, 0.5)
    lvl_raw = _clip(0.6 * lvl.strength + 0.4 * type_bonus, 0.0, 1.0)
    out = [
        _mk(
            "swept_level_quality",
            ConfluenceGroup.LIQUIDITY_EVENT,
            ConfluenceRole.ENTRY_SUPPORT,
            lvl_raw,
            1.0,
            f"Pool {lvl.type.value} strength={lvl.strength:.2f}",
            lvl.formed_at,
            cutoff,
            ConfluenceDataQuality.OK,
            True,
            p,
        )
    ]
    sweep = cand.sweep
    if sweep is None:
        return out
    pen_term = _clip(1.0 - abs(sweep.penetration_depth_atr - 0.525) / 0.475, 0.0, 1.0)
    reclaim_speed = _clip(1.0 - sweep.bars_to_reclaim / 3.0, 0.0, 1.0)
    wick_term = _clip((sweep.wick_ratio - 1.0) / 2.0, 0.0, 1.0)
    out.append(
        _mk(
            "sweep_clarity",
            ConfluenceGroup.LIQUIDITY_EVENT,
            ConfluenceRole.ENTRY_SUPPORT,
            _clip((pen_term + reclaim_speed + wick_term) / 3.0, 0.0, 1.0),
            1.0,
            f"pen={sweep.penetration_depth_atr:.2f}ATR bars_to_reclaim={sweep.bars_to_reclaim} "
            f"wick_ratio={sweep.wick_ratio:.2f}",
            sweep.penetration_bar,
            cutoff,
            ConfluenceDataQuality.OK,
            True,
            p,
        )
    )
    atr_s = _tf_atr(mtf, p.sweep_timeframe)
    beyond = abs(sweep.reclaim_close - lvl.price) / atr_s if atr_s > 0 else 0.0
    close_term = _clip(beyond / 0.30, 0.0, 1.0)
    out.append(
        _mk(
            "reclaim_quality",
            ConfluenceGroup.LIQUIDITY_EVENT,
            ConfluenceRole.ENTRY_SUPPORT,
            _clip((reclaim_speed + close_term) / 2.0, 0.0, 1.0),
            0.8,
            f"Reclaim-Close {beyond:.2f}ATR jenseits, {sweep.bars_to_reclaim} Bars",
            sweep.reclaim_bar,
            cutoff,
            ConfluenceDataQuality.OK,
            True,
            p,
        )
    )
    return out


def _momentum_structure_factors(
    mtf: MtfContext, cand: SetupCandidate, cutoff: datetime, p: ConfluenceParams
) -> list[ConfluenceFactor]:
    out: list[ConfluenceFactor] = []
    disp = cand.displacement
    high_vol = _worst_volatility(mtf, (p.sweep_timeframe,)) in (
        RegimeVolatility.HIGH,
        RegimeVolatility.EXTREME,
    )
    if disp is not None:
        atr_term = _clip((disp.net_move_atr - 1.5) / 3.0, 0.0, 1.0)
        if high_vol:
            atr_term *= 0.85
        body_term = _clip((disp.body_ratio - 0.55) / 0.45, 0.0, 1.0)
        fvg_term = _clip(len(disp.fvgs) / 2.0, 0.0, 1.0)
        out.append(
            _mk(
                "displacement_strength",
                ConfluenceGroup.MOMENTUM_STRUCTURE,
                ConfluenceRole.ENTRY_SUPPORT,
                _clip((atr_term + body_term + fvg_term) / 3.0, 0.0, 1.0),
                1.0,
                f"net_move={disp.net_move_atr:.2f}ATR body_ratio={disp.body_ratio:.2f} "
                f"fvgs={len(disp.fvgs)}{' (High-Vol normiert)' if high_vol else ''}",
                disp.end_bar,
                cutoff,
                ConfluenceDataQuality.OK,
                True,
                p,
            )
        )
    brk = cand.structure_break
    if brk is not None:
        distance_term = _clip(1.0 - brk.break_distance_atr / 4.0, 0.0, 1.0)
        type_term = 1.0 if brk.kind is StructureBreakKind.CHOCH else 0.9
        caused = disp is not None and disp.caused_structure_break is not None
        caused_term = 1.0 if caused else 0.7
        out.append(
            _mk(
                "structure_shift",  # EIN Faktor — nie BOS + CHoCH getrennt
                ConfluenceGroup.MOMENTUM_STRUCTURE,
                ConfluenceRole.ENTRY_SUPPORT,
                _clip((distance_term + type_term + caused_term) / 3.0, 0.0, 1.0),
                1.0,
                f"{brk.kind.value} dist={brk.break_distance_atr:.2f}ATR "
                f"{'vom Displacement getragen' if caused else 'nicht direkt gekoppelt'}",
                brk.break_bar_timestamp,
                cutoff,
                ConfluenceDataQuality.OK,
                True,
                p,
            )
        )
    # Phase als schwacher, korrelierter Mit-Faktor (kein eigener Score-Punkt)
    phase, exp_dir = _tf_phase(mtf, p.sweep_timeframe)
    if phase is RegimePhase.EXPANSION:
        aligned = (exp_dir == "up" and cand.direction is Direction.LONG) or (
            exp_dir == "down" and cand.direction is Direction.SHORT
        )
        raw = 0.6 if aligned else -0.2
        reason = f"Phase EXPANSION ({exp_dir}) {'in' if aligned else 'gegen'} Richtung D"
    elif phase is RegimePhase.COMPRESSION:
        raw, reason = -0.5, "Phase COMPRESSION — Displacement-Fähigkeit fraglich"
    else:
        raw, reason = 0.0, "Phase NEUTRAL"
    out.append(
        _mk(
            "phase_alignment",
            ConfluenceGroup.MOMENTUM_STRUCTURE,
            ConfluenceRole.CONTEXT,
            raw,
            0.5,
            reason,
            _tf_time(mtf, p.sweep_timeframe),
            cutoff,
            ConfluenceDataQuality.OK,
            True,
            p,
        )
    )
    return out


def _entry_zone_factors(
    mtf: MtfContext, cand: SetupCandidate, d: Direction, cutoff: datetime, p: ConfluenceParams
) -> list[ConfluenceFactor]:
    zone = cand.entry_zone
    out: list[ConfluenceFactor] = []
    if zone is None:
        return out
    atr_e = _tf_atr(mtf, p.entry_timeframe)
    height = zone.zone_high - zone.zone_low
    size_term = _clip(height / (0.6 * atr_e), 0.0, 1.0) if atr_e > 0 else 0.5
    fresh_term = _clip(1.0 - zone.fill_fraction, 0.0, 1.0)
    stale_penalty = 0.0 if zone.state is ZoneState.UNMITIGATED else -0.4
    ob_factor = 0.9 if isinstance(zone, OrderBlock) else 1.0
    raw = _clip(ob_factor * (0.5 * size_term + 0.5 * fresh_term) + stale_penalty, -1.0, 1.0)
    out.append(
        _mk(
            "entry_zone_quality",
            ConfluenceGroup.ENTRY_ZONE,
            ConfluenceRole.ENTRY_SUPPORT,
            raw,
            1.0,
            f"{'OB' if isinstance(zone, OrderBlock) else 'FVG'} Höhe={height:.4f} "
            f"fill={zone.fill_fraction:.2f} state={zone.state.value}",
            _zone_time(zone),
            cutoff,
            ConfluenceDataQuality.OK,
            True,
            p,
        )
    )
    overlap = _opposing_htf_zone_overlap(mtf, cand, d, p)
    if overlap > 0.0:
        out.append(
            _mk(
                "opposing_htf_zone_proximity",
                ConfluenceGroup.ENTRY_ZONE,
                ConfluenceRole.EXIT_RELEVANT,
                -overlap,
                1.0,
                f"Entry-Zone überlappt eine gegen-D HTF-Zone zu {overlap * 100:.0f}%",
                cutoff,
                cutoff,
                ConfluenceDataQuality.OK,
                True,
                p,
            )
        )
    return out


def _location_factors(
    cand: SetupCandidate,
    d: Direction,
    gates: GateReport | None,
    cutoff: datetime,
    p: ConfluenceParams,
) -> list[ConfluenceFactor]:
    if gates is None:
        return [
            _mk(
                "discount_premium_depth",
                ConfluenceGroup.LOCATION,
                ConfluenceRole.ENTRY_SUPPORT,
                0.0,
                1.0,
                "Location-Gate nicht ausgewertet",
                None,
                cutoff,
                ConfluenceDataQuality.UNAVAILABLE,
                True,
                p,
            )
        ]
    loc = gates.location
    if loc.outcome is GateOutcome.BLOCK:
        raw, reason, dq = -1.0, f"Location BLOCK: {loc.note}", ConfluenceDataQuality.OK
    elif loc.outcome is GateOutcome.WAIT or loc.pd_position is None:
        raw, reason, dq = 0.0, f"Location WAIT: {loc.note}", ConfluenceDataQuality.PARTIAL
    else:
        pos = loc.pd_position
        depth = (0.5 - pos) if d is Direction.LONG else (pos - 0.5)
        raw = _clip(depth / 0.5, -1.0, 1.0)
        reason = f"pd_position(zone_mid)={pos:.3f} vs swept_leg (Tiefe im {'Discount' if d is Direction.LONG else 'Premium'})"
        dq = ConfluenceDataQuality.OK
    return [
        _mk(
            "discount_premium_depth",
            ConfluenceGroup.LOCATION,
            ConfluenceRole.ENTRY_SUPPORT,
            raw,
            1.0,
            reason,
            cutoff,
            cutoff,
            dq,
            True,
            p,
        )
    ]


def _risk_reward_factors(
    gates: GateReport | None, cutoff: datetime, p: ConfluenceParams
) -> list[ConfluenceFactor]:
    if gates is None or gates.rr is None:
        return [
            _mk(
                "risk_reward",
                ConfluenceGroup.RISK_REWARD,
                ConfluenceRole.ENTRY_SUPPORT,
                0.0,
                1.0,
                "RR-Gate nicht ausgewertet",
                None,
                cutoff,
                ConfluenceDataQuality.UNAVAILABLE,
                True,
                p,
            )
        ]
    rr = gates.rr
    if rr.outcome is GateOutcome.BLOCK:
        return [
            _mk(
                "risk_reward",
                ConfluenceGroup.RISK_REWARD,
                ConfluenceRole.VETO_CANDIDATE,
                -1.0,
                1.0,
                f"RR BLOCK: {rr.note}",
                cutoff,
                cutoff,
                ConfluenceDataQuality.OK,
                True,
                p,
            )
        ]
    g = rr.geometry
    if g is None:
        return [
            _mk(
                "risk_reward",
                ConfluenceGroup.RISK_REWARD,
                ConfluenceRole.ENTRY_SUPPORT,
                0.0,
                1.0,
                f"RR {rr.outcome.value} ohne Geometrie: {rr.note}",
                None,
                cutoff,
                ConfluenceDataQuality.PARTIAL,
                True,
                p,
            )
        ]
    rr_term = _clip((g.rr_to_tp2 - 2.0) / 2.0, 0.0, 1.0)
    blended_term = _clip((g.blended_rr - 1.3) / 1.3, 0.0, 1.0)
    room = (
        1.0 if g.target_room_r == float("inf") else _clip((g.target_room_r - 1.5) / 3.0, 0.0, 1.0)
    )
    return [
        _mk(
            "rr_to_tp2",
            ConfluenceGroup.RISK_REWARD,
            ConfluenceRole.ENTRY_SUPPORT,
            rr_term,
            1.0,
            f"RR_to_TP2={g.rr_to_tp2:.2f}",
            cutoff,
            cutoff,
            ConfluenceDataQuality.OK,
            True,
            p,
        ),
        _mk(
            "blended_rr",
            ConfluenceGroup.RISK_REWARD,
            ConfluenceRole.ENTRY_SUPPORT,
            blended_term,
            0.8,
            f"blended_RR={g.blended_rr:.2f}",
            cutoff,
            cutoff,
            ConfluenceDataQuality.OK,
            True,
            p,
        ),
        _mk(
            "target_room",
            ConfluenceGroup.RISK_REWARD,
            ConfluenceRole.EXIT_RELEVANT,
            room,
            0.8,
            f"target_room={'∞' if g.target_room_r == float('inf') else f'{g.target_room_r:.2f}'}R",
            cutoff,
            cutoff,
            ConfluenceDataQuality.OK,
            True,
            p,
        ),
    ]


def _confirmation_factors(
    cand: SetupCandidate,
    confirmation: ConfirmationScan | None,
    cutoff: datetime,
    p: ConfluenceParams,
) -> list[ConfluenceFactor]:
    if confirmation is None:
        return [
            _mk(
                "price_action_confirmation",
                ConfluenceGroup.CONFIRMATION,
                ConfluenceRole.ENTRY_SUPPORT,
                0.0,
                1.0,
                "Keine Confirmation übergeben (nur im confirmation_market-Modus relevant)",
                None,
                cutoff,
                ConfluenceDataQuality.UNAVAILABLE,
                True,
                p,
            )
        ]
    want = Polarity.of(cand.direction)
    if (
        confirmation.confirmed
        and confirmation.primary is not None
        and confirmation.primary.direction is want
    ):
        c = confirmation.primary
        raw = _clip(0.5 + 0.5 * c.strength, 0.0, 1.0)
        return [
            _mk(
                "price_action_confirmation",
                ConfluenceGroup.CONFIRMATION,
                ConfluenceRole.ENTRY_SUPPORT,
                raw,
                1.0,
                f"{c.pattern.value} strength={c.strength:.2f} @ {c.bar_timestamp.isoformat()}",
                c.bar_timestamp,
                cutoff,
                ConfluenceDataQuality.OK,
                True,
                p,
            )
        ]
    return [
        _mk(
            "price_action_confirmation",
            ConfluenceGroup.CONFIRMATION,
            ConfluenceRole.ENTRY_SUPPORT,
            0.0,
            1.0,
            f"noch keine Confirmation ({confirmation.note or 'kein Muster'})",
            confirmation.checked_through,
            cutoff,
            ConfluenceDataQuality.OK,  # geprüft, nichts gefunden — nicht negativ
            True,
            p,
        )
    ]


def _external_context_factors(
    mtf: MtfContext, cutoff: datetime, p: ConfluenceParams
) -> list[ConfluenceFactor]:
    mc = mtf.market_context
    out: list[ConfluenceFactor] = []

    news = mc.news
    if not news.feed_available:
        out.append(
            _mk(
                "news_context",
                ConfluenceGroup.EXTERNAL_CONTEXT,
                ConfluenceRole.VETO_CANDIDATE,
                0.0,
                1.0,
                "News-Feed nicht verfügbar",
                None,
                cutoff,
                ConfluenceDataQuality.UNAVAILABLE,
                True,
                p,
            )
        )
    else:
        blocking = news.risk_off or news.blocking_event_id is not None
        out.append(
            _mk(
                "news_context",
                ConfluenceGroup.EXTERNAL_CONTEXT,
                ConfluenceRole.VETO_CANDIDATE,
                -1.0 if blocking else 0.0,
                1.0,
                "Blockierendes Event / risk_off" if blocking else "News-Fenster frei",
                news.feed_as_of,
                cutoff,
                ConfluenceDataQuality.OK,
                True,
                p,
            )
        )

    deriv = mc.derivatives
    deriv_avail = any(
        v is not None
        for v in (deriv.funding_rate, deriv.open_interest, deriv.basis_pct, deriv.cvd_divergence)
    )
    out.append(
        _mk(
            "derivatives_context",
            ConfluenceGroup.EXTERNAL_CONTEXT,
            ConfluenceRole.CONTEXT,
            0.0,
            1.0,
            "Derivatives-Kontext verfügbar" if deriv_avail else "Keine Derivatives-Daten",
            deriv.funding_rate_as_of,
            cutoff,
            ConfluenceDataQuality.OK if deriv_avail else ConfluenceDataQuality.UNAVAILABLE,
            True,
            p,
        )
    )

    ca = mc.cross_asset
    ca_avail = any(v is not None for v in (ca.dxy_trend, ca.real_yield_10y, ca.vix)) or ca.risk_off
    out.append(
        _mk(
            "cross_asset_context",
            ConfluenceGroup.EXTERNAL_CONTEXT,
            ConfluenceRole.CONTEXT,
            -0.6 if ca.risk_off else 0.0,
            1.0,
            "Cross-Asset risk_off"
            if ca.risk_off
            else ("Cross-Asset-Kontext verfügbar" if ca_avail else "Keine Cross-Asset-Daten"),
            ca.as_of,
            cutoff,
            ConfluenceDataQuality.OK if ca_avail else ConfluenceDataQuality.UNAVAILABLE,
            True,
            p,
        )
    )
    return out


# --------------------------------------------------------------------------------- Kontext-Gruppen


def _mtf_coherence_factors(
    mtf: MtfContext, d: Direction, sign: float, cutoff: datetime, p: ConfluenceParams
) -> list[ConfluenceFactor]:
    dis = mtf.htf_regime_gate.disagreement
    m15_dir = _dir_num(_tf_regime_directional(mtf, Timeframe.M15)) * sign
    note = ""
    if m15_dir < 0:
        note = " (M15 gegen D — für SMC-SWEEP-REV-01 die Prämisse, kein Widerspruch)"
    return [
        _mk(
            "mtf_disagreement",
            ConfluenceGroup.MTF_COHERENCE,
            ConfluenceRole.CONTEXT,
            -_clip(dis, 0.0, 1.0),
            1.0,
            f"mtf_disagreement={dis:.2f}{note}",
            _tf_time(mtf, Timeframe.H4),
            cutoff,
            ConfluenceDataQuality.OK,
            False,
            p,
        )
    ]


def _regime_filter_factors(
    mtf: MtfContext, d: Direction, cutoff: datetime, p: ConfluenceParams
) -> list[ConfluenceFactor]:
    vol = _worst_volatility(mtf, p.filter_timeframes)
    if vol in (RegimeVolatility.EXTREME, RegimeVolatility.LOW):
        raw, role, reason = (
            -1.0,
            ConfluenceRole.VETO_CANDIDATE,
            f"Volatilität {vol.value} — untauglich",
        )
    else:
        raw, role, reason = (
            0.0,
            ConfluenceRole.CONTEXT,
            f"Volatilität {vol.value} — im tradebaren Band",
        )
    out = [
        _mk(
            "volatility_regime",
            ConfluenceGroup.REGIME_FILTER,
            role,
            raw,
            1.0,
            reason,
            _tf_time(mtf, Timeframe.H4),
            cutoff,
            ConfluenceDataQuality.OK,
            False,
            p,
        )
    ]
    if _any_coiled_compression(mtf, p.filter_timeframes):
        out.append(
            _mk(
                "phase_compression",
                ConfluenceGroup.REGIME_FILTER,
                ConfluenceRole.VETO_CANDIDATE,
                -1.0,
                1.0,
                "coiled COMPRESSION auf D1/H4/M15",
                _tf_time(mtf, Timeframe.H4),
                cutoff,
                ConfluenceDataQuality.OK,
                False,
                p,
            )
        )
    return out


def _session_factors(
    session_names: set[SessionName] | None, cutoff: datetime, p: ConfluenceParams
) -> list[ConfluenceFactor]:
    if session_names is None:
        return [
            _mk(
                "session_context",
                ConfluenceGroup.SESSION,
                ConfluenceRole.CONTEXT,
                0.0,
                1.0,
                "Session-Kontext nicht übergeben",
                None,
                cutoff,
                ConfluenceDataQuality.UNAVAILABLE,
                False,
                p,
            )
        ]
    scores = {
        SessionName.LONDON_NY_OVERLAP: 1.0,
        SessionName.NEW_YORK: 0.85,
        SessionName.LONDON: 0.8,
        SessionName.ASIA: 0.4,
    }
    best = max((scores.get(s, 0.3) for s in session_names), default=0.3)
    return [
        _mk(
            "session_context",
            ConfluenceGroup.SESSION,
            ConfluenceRole.CONTEXT,
            best,
            1.0,
            f"aktive Sessions: {sorted(s.value for s in session_names) or '—'}",
            cutoff,
            cutoff,
            ConfluenceDataQuality.OK,
            False,
            p,
        )
    ]


def _data_quality_factors(
    mtf: MtfContext, cutoff: datetime, p: ConfluenceParams
) -> list[ConfluenceFactor]:
    dc = mtf.data_confidence
    if dc < p.data_confidence_floor:
        raw, role, reason = -1.0, ConfluenceRole.VETO_CANDIDATE, f"data_confidence {dc:.2f} < Floor"
    else:
        raw, role, reason = (
            _clip((dc - 0.5) / 0.5, 0.0, 1.0),
            ConfluenceRole.CONTEXT,
            (f"data_confidence {dc:.2f}"),
        )
    return [
        _mk(
            "data_confidence",
            ConfluenceGroup.DATA_QUALITY,
            role,
            raw,
            1.0,
            reason,
            cutoff,
            cutoff,
            ConfluenceDataQuality.OK,
            False,
            p,
        )
    ]


# --------------------------------------------------------------------------------- intern


def _mk(
    factor: str,
    group: ConfluenceGroup,
    role: ConfluenceRole,
    raw: float,
    relevance: float,
    reason: str,
    timestamp: datetime | None,
    cutoff: datetime,
    dq: ConfluenceDataQuality,
    scored: bool,
    p: ConfluenceParams,
) -> ConfluenceFactor:
    raw = _clip(raw, -1.0, 1.0)
    if dq is ConfluenceDataQuality.UNAVAILABLE:
        direction = FactorDirection.NEUTRAL
    elif raw >= p.support_threshold:
        direction = FactorDirection.SUPPORT
    elif raw <= -p.support_threshold:
        direction = FactorDirection.CONTRADICT
    else:
        direction = FactorDirection.NEUTRAL
    return ConfluenceFactor(
        factor=factor,
        factor_group=group,
        role=role,
        direction=direction,
        contribution=round(raw, 6),
        relevance=relevance,
        reason=reason,
        timestamp=timestamp,
        information_cutoff=cutoff,
        data_quality=dq,
        scored=scored,
    )


def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _dir_num(directional: RegimeDirectional | None) -> int:
    if directional is RegimeDirectional.TREND_UP:
        return 1
    if directional is RegimeDirectional.TREND_DOWN:
        return -1
    return 0


def _tf_ctx(mtf: MtfContext, tf: Timeframe) -> TimeframeContext | None:
    return mtf.tf(tf)


def _tf_regime_directional(mtf: MtfContext, tf: Timeframe) -> RegimeDirectional | None:
    c = mtf.tf(tf)
    return c.regime.directional if c is not None else None


def _tf_structure_directional(mtf: MtfContext, tf: Timeframe) -> RegimeDirectional:
    c = mtf.tf(tf)
    return c.structure.directional if c is not None else RegimeDirectional.UNCLEAR


def _tf_atr(mtf: MtfContext, tf: Timeframe) -> float:
    c = mtf.tf(tf)
    return c.atr if c is not None else 0.0


def _tf_time(mtf: MtfContext, tf: Timeframe) -> datetime | None:
    c = mtf.tf(tf)
    if c is not None and c.bars:
        return c.bars[-1].close_time
    return mtf.information_cutoff


def _tf_phase(mtf: MtfContext, tf: Timeframe) -> tuple[RegimePhase, str]:
    c = mtf.tf(tf)
    if c is None:
        return RegimePhase.NEUTRAL, "none"
    return c.regime.phase, c.regime.expansion_direction.value


def _worst_volatility(mtf: MtfContext, timeframes: Sequence[Timeframe]) -> RegimeVolatility:
    order = {
        RegimeVolatility.LOW: 0,
        RegimeVolatility.NORMAL: 1,
        RegimeVolatility.HIGH: 2,
        RegimeVolatility.EXTREME: 3,
    }
    vols = [c.regime.volatility for tf in timeframes if (c := mtf.tf(tf)) is not None]
    if not vols:
        return RegimeVolatility.NORMAL
    # "worst" = EXTREME oder LOW dominiert; sonst NORMAL/HIGH nach Rang
    if RegimeVolatility.EXTREME in vols:
        return RegimeVolatility.EXTREME
    if RegimeVolatility.LOW in vols:
        return RegimeVolatility.LOW
    return max(vols, key=lambda v: order[v])


def _any_coiled_compression(mtf: MtfContext, timeframes: Sequence[Timeframe]) -> bool:
    for tf in timeframes:
        c = mtf.tf(tf)
        if c is not None and c.regime.phase is RegimePhase.COMPRESSION and c.regime.coiled:
            return True
    return False


def _zone_time(zone: FVG | OrderBlock) -> datetime:
    return zone.created_bar if isinstance(zone, FVG) else zone.ob_bar


def _opposing_htf_zone_overlap(
    mtf: MtfContext, cand: SetupCandidate, d: Direction, p: ConfluenceParams
) -> float:
    zone = cand.entry_zone
    if zone is None:
        return 0.0
    zlo, zhi = zone.zone_low, zone.zone_high
    span = zhi - zlo
    if span <= 0:
        return 0.0
    opp = Polarity.of(d).opposite
    best = 0.0
    for tf in p.htf_timeframes:
        c = mtf.tf(tf)
        if c is None:
            continue
        zones: list[FVG | OrderBlock] = [*c.fvgs, *c.order_blocks]
        for z in zones:
            if z.direction is not opp or z.state is not ZoneState.UNMITIGATED:
                continue
            lo = max(zlo, z.zone_low)
            hi = min(zhi, z.zone_high)
            if hi > lo:
                best = max(best, (hi - lo) / span)
    return _clip(best, 0.0, 1.0)


__all__ = [
    "ConfluenceDataQuality",
    "ConfluenceFactor",
    "ConfluenceGroup",
    "ConfluenceGroupResult",
    "ConfluenceParams",
    "ConfluenceReport",
    "ConfluenceRole",
    "FactorDirection",
    "assess_confluence",
]
