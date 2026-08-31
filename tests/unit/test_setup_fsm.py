"""Phase 3 — Setup-FSM (``strategy/setup_detection.py``, ``SMC-SWEEP-REV-01`` §0/§24).

Getestet: vollständige Long-/Short-Kette, falsche Reihenfolge, fehlendes Kettenglied je Stufe,
Invalidierung, Expiry, Duplicate Events, mehrere Setups gleichzeitig, Long/Short-Symmetrie,
Look-ahead-Schutz, deterministisches Replay, MTF-Kontext, setup_id/Revision.

Die ``MtfContext``-Bausteine werden direkt konstruiert (die Primitive-Detektoren + ``build_mtf_context``
haben eigene Golden-Tests). So wird die **FSM-Kausalitäts-/Reihenfolgen-Logik** isoliert geprüft:
Der Sweep wird von echten M15-Bars über ``resolve_sweep`` aufgelöst; Displacement / Struktur-Bruch /
FVG werden als typisierte Primitive eingehängt.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from trading_agent.analysis.mtf import MtfContext, TimeframeContext
from trading_agent.analysis.regime import RegimeGateResult, RegimeState
from trading_agent.core.enums import (
    Bias,
    Direction,
    DisplayAlias,
    LiquidityState,
    LiquidityType,
    MarketSide,
    NoTradeReason,
    Polarity,
    RegimeDirectional,
    RegimePhase,
    RegimeVolatility,
    SetupState,
    StructureBreakKind,
    Timeframe,
    ZoneState,
)
from trading_agent.core.enums import ExpansionDirection as ExpDir
from trading_agent.core.models import OHLCV
from trading_agent.core.time import bar_close_time, parse_timestamp
from trading_agent.core.types import MarketContext
from trading_agent.data.quality import check_ohlcv_series
from trading_agent.strategy.primitives.models import (
    FVG,
    Displacement,
    LiquidityLevel,
    OrderBlock,
    StructureBreak,
    StructureState,
)
from trading_agent.strategy.setup_detection import (
    _STATE_RANK,
    SetupParams,
    SetupScan,
    detect_setups,
)

DAY = parse_timestamp("2024-06-03T00:00:00Z")
M15 = Timeframe.M15
M5 = Timeframe.M5
_MIRROR = 200.0

# M15-Grid: 16 Warmup-Bars → letzter Warmup schließt 04:00; Sweep-Bar hat open_time 04:00.
RECLAIM_BAR = DAY + timedelta(minutes=15 * 16)  # 04:00
DISP_START = RECLAIM_BAR  # Displacement beginnt auf der Reclaim-Bar
DISP_END = RECLAIM_BAR + timedelta(minutes=30)  # 04:30
BREAK_TS = RECLAIM_BAR + timedelta(minutes=35)  # 04:35 (M5-Bar kurz nach dem Displacement)
FVG_TS = RECLAIM_BAR + timedelta(minutes=40)  # 04:40
CUTOFF = parse_timestamp("2024-06-03T05:00:00Z")


# --------------------------------------------------------------------------- Bar-/Primitive-Bau


def _bar(tf: Timeframe, t: datetime, o: float, h: float, low: float, c: float) -> OHLCV:
    return OHLCV(
        instrument="BTCUSD",
        timeframe=tf,
        open_time=t,
        close_time=bar_close_time(t, tf),
        open=o,
        high=h,
        low=low,
        close=c,
        volume=1.0,
        source="t",
    )


def _rows_to_bars(
    rows: list[tuple[float, float, float, float]], *, tf: Timeframe, start: datetime
) -> list[OHLCV]:
    out: list[OHLCV] = []
    t = start
    for o, h, low, c in rows:
        out.append(_bar(tf, t, o, h, low, c))
        t += timedelta(seconds=tf.seconds)
    return out


_WARMUP_LONG = [(106.0, 106.6, 105.4, 106.0)] * 16
# Sweep-Bar (SELL_SIDE-Level bei 105.0): Docht auf 104.5 (Penetration), Close 105.6 (Reclaim).
_SWEEP_BAR_LONG = (106.0, 106.1, 104.5, 105.6)
SWEEP_EXTREME_LONG = 104.5


def _m15_long(extra: list[tuple[float, float, float, float]] | None = None) -> list[OHLCV]:
    rows = [*_WARMUP_LONG, _SWEEP_BAR_LONG, *(extra or [])]
    return _rows_to_bars(rows, tf=M15, start=DAY)


def _mirror_rows(
    rows: list[tuple[float, float, float, float]],
) -> list[tuple[float, float, float, float]]:
    return [(_MIRROR - o, _MIRROR - low, _MIRROR - h, _MIRROR - c) for o, h, low, c in rows]


def _m15_short(extra: list[tuple[float, float, float, float]] | None = None) -> list[OHLCV]:
    rows = _mirror_rows([*_WARMUP_LONG, _SWEEP_BAR_LONG]) + _mirror_rows(extra or [])
    return _rows_to_bars(rows, tf=M15, start=DAY)


def _m5_filler(
    *, start: datetime = DAY, end: datetime = CUTOFF, base: float = 105.5
) -> list[OHLCV]:
    out: list[OHLCV] = []
    t = start
    i = 0
    while bar_close_time(t, M5) <= end:
        p = base + (0.3 if i % 2 == 0 else -0.3)
        out.append(_bar(M5, t, base, max(base, p) + 0.2, min(base, p) - 0.2, p))
        t += timedelta(seconds=M5.seconds)
        i += 1
    return out


def _level(
    *,
    price: float = 105.0,
    side: MarketSide = MarketSide.SELL_SIDE,
    ltype: LiquidityType = LiquidityType.EQUAL_LOWS,
    strength: float = 0.80,
) -> LiquidityLevel:
    return LiquidityLevel(
        type=ltype,
        side=side,
        price=price,
        timeframe=M15,
        formed_at=parse_timestamp("2024-06-02T23:00:00Z"),
        strength=strength,
        state=LiquidityState.UNSWEPT,
    )


def _fvg(
    *,
    direction: Polarity = Polarity.BULLISH,
    lo: float = 104.9,
    hi: float = 105.5,
    created: datetime = FVG_TS,
    state: ZoneState = ZoneState.UNMITIGATED,
) -> FVG:
    return FVG(
        direction=direction,
        timeframe=M5,
        zone_low=lo,
        zone_high=hi,
        created_bar=created,
        bar_index=5,
        state=state,
    )


def _disp(
    *,
    direction: Polarity = Polarity.BULLISH,
    start: datetime = DISP_START,
    end: datetime = DISP_END,
    net_atr: float = 2.5,
    body_ratio: float = 0.7,
    fvgs: tuple[FVG, ...] | None = None,
    caused: StructureBreak | None = None,
) -> Displacement:
    return Displacement(
        direction=direction,
        timeframe=M15,
        start_bar=start,
        end_bar=end,
        bars=3,
        net_move_atr=net_atr,
        body_ratio=body_ratio,
        start_index=17,
        end_index=19,
        fvgs=fvgs if fvgs is not None else (_fvg(direction=direction),),
        caused_structure_break=caused,
    )


def _brk(
    *,
    direction: Polarity = Polarity.BULLISH,
    kind: StructureBreakKind = StructureBreakKind.CHOCH,
    ts: datetime = BREAK_TS,
    dist_atr: float = 1.0,
) -> StructureBreak:
    return StructureBreak(
        kind=kind,
        direction=direction,
        timeframe=M5,
        broken_level_price=105.3,
        break_bar_timestamp=ts,
        break_close=105.9 if direction is Polarity.BULLISH else 94.1,
        break_distance_atr=dist_atr,
    )


def _ob(
    *,
    direction: Polarity = Polarity.BULLISH,
    lo: float = 104.6,
    hi: float = 105.0,
    ob_bar: datetime = DISP_START,
    state: ZoneState = ZoneState.UNMITIGATED,
) -> OrderBlock:
    return OrderBlock(
        direction=direction,
        timeframe=M5,
        zone_low=lo,
        zone_high=hi,
        ob_bar=ob_bar,
        bar_index=16,
        state=state,
    )


# --------------------------------------------------------------------------- MtfContext-Bau


def _regime(tf: Timeframe, directional: RegimeDirectional) -> RegimeState:
    return RegimeState(
        timeframe=tf,
        directional=directional,
        directional_score=0.7,
        volatility=RegimeVolatility.NORMAL,
        volatility_pct=50.0,
        phase=RegimePhase.EXPANSION,
        expansion_direction=ExpDir.UP,
        computed_at=CUTOFF,
    )


def _tfctx(
    tf: Timeframe,
    bars: list[OHLCV],
    *,
    directional: RegimeDirectional = RegimeDirectional.TREND_UP,
    liquidity: tuple[LiquidityLevel, ...] = (),
    displacements: tuple[Displacement, ...] = (),
    fvgs: tuple[FVG, ...] = (),
    order_blocks: tuple[OrderBlock, ...] = (),
    breaks: tuple[StructureBreak, ...] = (),
    atr: float = 1.2,
) -> TimeframeContext:
    q = check_ohlcv_series(bars, instrument="BTCUSD", timeframe=tf, now=CUTOFF)
    return TimeframeContext(
        timeframe=tf,
        bars=tuple(bars),
        swings=(),
        structure=StructureState(timeframe=tf, directional=directional),
        structure_breaks=breaks,
        regime=_regime(tf, directional),
        bias=Bias.NONE,
        premium_discount=None,
        liquidity=liquidity,
        fvgs=fvgs,
        displacements=displacements,
        order_blocks=order_blocks,
        quality=q,
        data_confidence=1.0,
        atr=atr,
    )


def _mtf(
    *,
    m15_bars: list[OHLCV] | None = None,
    m5_bars: list[OHLCV] | None = None,
    m15_liquidity: tuple[LiquidityLevel, ...] = (),
    m15_displacements: tuple[Displacement, ...] = (),
    m5_breaks: tuple[StructureBreak, ...] = (),
    m5_fvgs: tuple[FVG, ...] = (),
    m5_order_blocks: tuple[OrderBlock, ...] = (),
    htf_directional: RegimeDirectional = RegimeDirectional.TREND_UP,
    htf_bias: Bias = Bias.LONG,
    regime_ok: bool = True,
    cutoff: datetime = CUTOFF,
) -> MtfContext:
    raw_m15 = m15_bars if m15_bars is not None else _m15_long()
    raw_m5 = m5_bars if m5_bars is not None else _m5_filler(end=cutoff)
    h4b = _rows_to_bars([(105, 106, 104, 105.5)], tf=Timeframe.H4, start=DAY)
    d1b = _rows_to_bars(
        [(100, 106, 99, 105)], tf=Timeframe.D1, start=parse_timestamp("2024-06-02T00:00:00Z")
    )
    # MtfContext-Garantie: nur abgeschlossene Bars <= information_cutoff
    m15b = [b for b in raw_m15 if b.close_time <= cutoff]
    m5b = [b for b in raw_m5 if b.close_time <= cutoff]
    h4b = [b for b in h4b if b.close_time <= cutoff]
    d1b = [b for b in d1b if b.close_time <= cutoff]

    per_tf = {
        M5: _tfctx(
            M5,
            m5b,
            directional=htf_directional,
            breaks=m5_breaks,
            fvgs=m5_fvgs,
            order_blocks=m5_order_blocks,
            atr=1.0,
        ),
        M15: _tfctx(
            M15,
            m15b,
            directional=htf_directional,
            liquidity=m15_liquidity,
            displacements=m15_displacements,
        ),
        Timeframe.H4: _tfctx(Timeframe.H4, h4b, directional=htf_directional),
        Timeframe.D1: _tfctx(Timeframe.D1, d1b, directional=htf_directional),
    }
    market = MarketContext(
        instrument="BTCUSD",
        base_timeframe=M5,
        information_cutoff=cutoff,
        series={
            M5: tuple(m5b),
            M15: tuple(m15b),
            Timeframe.H4: tuple(h4b),
            Timeframe.D1: tuple(d1b),
        },
    )
    gate = RegimeGateResult(
        ok=regime_ok,
        reason=None if regime_ok else NoTradeReason.REGIME_UNCLEAR,
        merged_directional=htf_directional,
        disagreement=0.0,
    )
    return MtfContext(
        instrument="BTCUSD",
        information_cutoff=cutoff,
        base_timeframe=M5,
        per_tf=per_tf,
        htf_regime_gate=gate,
        htf_directional=htf_directional,
        htf_bias=htf_bias,
        data_confidence=1.0,
        analysis_confidence=0.8,
        issues=(),
        market_context=market,
    )


def _full_long_mtf(**kw: object) -> MtfContext:
    brk = _brk()
    return _mtf(
        m15_liquidity=(_level(),),
        m15_displacements=(_disp(caused=None),),
        m5_breaks=(brk,),
        m5_fvgs=(_fvg(),),
        **kw,  # type: ignore[arg-type]
    )


# =========================================================================== vollständige Kette


def test_full_long_chain_reaches_armed() -> None:
    scan = detect_setups(_full_long_mtf())
    assert isinstance(scan, SetupScan)
    c = scan.primary
    assert c is not None
    assert c.direction is Direction.LONG
    assert c.state is SetupState.ARMED
    assert c.is_armed and c.is_alive
    assert c.sweep is not None and c.sweep.penetration_extreme == SWEEP_EXTREME_LONG
    assert c.displacement is not None and c.structure_break is not None
    assert c.entry_fvg is not None and c.entry_zone is c.entry_fvg
    assert c.abort_reason is None and c.invalidation is None
    assert c.display_alias is DisplayAlias.ARMED
    for token in ("SWEPT", "RECLAIMED", "DISPLACED", "STRUCTURE_SHIFTED", "ARMED"):
        assert token in c.chain_progress
    assert scan.state is SetupState.ARMED


def test_full_short_chain_reaches_armed() -> None:
    brk = _brk(direction=Polarity.BEARISH)
    scan = detect_setups(
        _mtf(
            m15_bars=_m15_short(),
            m15_liquidity=(
                _level(price=95.0, side=MarketSide.BUY_SIDE, ltype=LiquidityType.EQUAL_HIGHS),
            ),
            m15_displacements=(_disp(direction=Polarity.BEARISH),),
            m5_breaks=(brk,),
            m5_fvgs=(_fvg(direction=Polarity.BEARISH, lo=94.5, hi=95.1),),
            htf_directional=RegimeDirectional.TREND_DOWN,
            htf_bias=Bias.SHORT,
            m5_bars=_m5_filler(base=94.5),
        )
    )
    c = scan.primary
    assert c is not None
    assert c.direction is Direction.SHORT
    assert c.state is SetupState.ARMED
    assert c.sweep is not None and c.sweep.side is MarketSide.BUY_SIDE
    assert c.entry_fvg is not None and c.entry_fvg.direction is Polarity.BEARISH


# =========================================================================== Kausalität / Reihenfolge


def test_wrong_order_displacement_before_reclaim_does_not_arm() -> None:
    early = _disp(start=DAY + timedelta(minutes=45), end=DAY + timedelta(minutes=90))
    scan = detect_setups(
        _mtf(
            m15_liquidity=(_level(),),
            m15_displacements=(early,),
            m5_breaks=(_brk(),),
            m5_fvgs=(_fvg(),),
        )
    )
    c = scan.primary
    assert c is not None
    assert c.state in (SetupState.RECLAIMED, SetupState.SCANNING)
    assert c.state not in (SetupState.DISPLACED, SetupState.STRUCTURE_SHIFTED, SetupState.ARMED)
    assert c.displacement is None


def test_single_event_does_not_create_full_state() -> None:
    # Nur ein Struktur-Bruch + FVG, aber KEIN Sweep und KEIN Displacement.
    scan = detect_setups(
        _mtf(
            m15_bars=_rows_to_bars(_WARMUP_LONG, tf=M15, start=DAY),  # kein Sweep-Bar
            m15_liquidity=(_level(),),
            m5_breaks=(_brk(),),
            m5_fvgs=(_fvg(),),
        )
    )
    c = scan.primary
    assert c is not None
    assert c.state is SetupState.LIQUIDITY_IDENTIFIED
    assert c.sweep is None and c.displacement is None and c.structure_break is None


# =========================================================================== fehlende Kettenglieder


def test_missing_liquidity_no_candidate() -> None:
    scan = detect_setups(_mtf(m15_liquidity=()))
    assert scan.candidates == ()
    assert scan.state is SetupState.BIAS_SET  # Bias steht, aber kein Pool


def test_missing_sweep_stays_liquidity_identified() -> None:
    scan = detect_setups(
        _mtf(m15_bars=_rows_to_bars(_WARMUP_LONG, tf=M15, start=DAY), m15_liquidity=(_level(),))
    )
    assert scan.primary is not None
    assert scan.primary.state is SetupState.LIQUIDITY_IDENTIFIED


def test_missing_reclaim_aborts_no_reclaim() -> None:
    # Penetration, aber Close bleibt tief; Folgebars berühren das Level nicht mehr → Frist verstreicht.
    tail = [(105.0, 105.15, 105.0, 105.08)] * 3 + [(105.08, 105.6, 105.0, 105.5)] * 3
    m15b = _rows_to_bars([*_WARMUP_LONG, (106.0, 106.1, 104.5, 104.8), *tail], tf=M15, start=DAY)
    late = parse_timestamp("2024-06-03T07:00:00Z")
    scan = detect_setups(_mtf(m15_bars=m15b, m15_liquidity=(_level(),), cutoff=late))
    c = scan.primary
    assert c is not None
    assert c.state is SetupState.SCANNING
    assert c.abort_reason is NoTradeReason.NO_RECLAIM


def test_sweep_became_breakout_aborts() -> None:
    # Tiefer Durchstich, Close hält jenseits (unter dem Level) → Breakout, kein Sweep.
    tail = [(104.0, 104.2, 103.0, 103.5)] * 4
    m15b = _rows_to_bars([*_WARMUP_LONG, (106.0, 106.0, 103.0, 103.4), *tail], tf=M15, start=DAY)
    late = parse_timestamp("2024-06-03T07:00:00Z")
    scan = detect_setups(_mtf(m15_bars=m15b, m15_liquidity=(_level(),), cutoff=late))
    c = scan.primary
    assert c is not None
    assert c.state is SetupState.SCANNING
    assert c.abort_reason is NoTradeReason.SWEEP_BECAME_BREAKOUT


def test_missing_displacement_waits_then_aborts() -> None:
    near = detect_setups(
        _mtf(m15_liquidity=(_level(),), cutoff=RECLAIM_BAR + timedelta(minutes=30))
    ).primary
    assert near is not None and near.state is SetupState.RECLAIMED

    late = detect_setups(
        _mtf(m15_liquidity=(_level(),), cutoff=RECLAIM_BAR + timedelta(minutes=90))
    ).primary
    assert late is not None
    assert late.state is SetupState.SCANNING
    assert late.abort_reason is NoTradeReason.NO_DISPLACEMENT


def test_missing_structure_shift_waits_then_aborts() -> None:
    near = detect_setups(
        _mtf(
            m15_liquidity=(_level(),),
            m15_displacements=(_disp(),),
            cutoff=DISP_END + timedelta(minutes=10),
        )
    ).primary
    assert near is not None and near.state is SetupState.DISPLACED

    late = detect_setups(
        _mtf(
            m15_liquidity=(_level(),),
            m15_displacements=(_disp(),),
            cutoff=DISP_END + timedelta(minutes=60),
        )
    ).primary
    assert late is not None
    assert late.state is SetupState.SCANNING
    assert late.abort_reason is NoTradeReason.NO_STRUCTURE_SHIFT


def test_no_entry_zone_aborts() -> None:
    scan = detect_setups(
        _mtf(
            m15_liquidity=(_level(),),
            m15_displacements=(_disp(),),
            m5_breaks=(_brk(),),
            m5_fvgs=(),  # keine FVG
            m5_order_blocks=(),  # kein OB
        )
    )
    c = scan.primary
    assert c is not None
    assert c.state is SetupState.SCANNING
    assert c.abort_reason is NoTradeReason.NO_ENTRY_ZONE


def test_order_block_fallback_when_no_fvg() -> None:
    scan = detect_setups(
        _mtf(
            m15_liquidity=(_level(),),
            m15_displacements=(_disp(),),
            m5_breaks=(_brk(),),
            m5_fvgs=(),
            m5_order_blocks=(_ob(),),
        )
    )
    c = scan.primary
    assert c is not None
    assert c.state is SetupState.ARMED
    assert c.entry_fvg is None and c.entry_ob is not None
    assert c.entry_zone is c.entry_ob


# =========================================================================== Regime-Gate


def test_regime_gate_blocks_whole_scan() -> None:
    scan = detect_setups(_full_long_mtf(regime_ok=False))
    assert scan.candidates == ()
    assert scan.state is SetupState.SCANNING
    assert scan.no_trade_reason is NoTradeReason.REGIME_UNCLEAR


def test_unclear_htf_direction_no_candidates() -> None:
    scan = detect_setups(
        _mtf(
            htf_directional=RegimeDirectional.UNCLEAR, htf_bias=Bias.NONE, m15_liquidity=(_level(),)
        )
    )
    assert scan.candidates == ()
    assert scan.no_trade_reason is NoTradeReason.REGIME_UNCLEAR


# =========================================================================== Invalidierung / Expiry


def test_armed_candidate_invalidated_by_re_sweep() -> None:
    # nach dem Reclaim schließt eine M15-Bar erneut unter das Sweep-Extrem
    scan = detect_setups(
        _mtf(
            m15_bars=_m15_long(extra=[(105.6, 105.7, 103.8, 104.0)]),
            m15_liquidity=(_level(),),
            m15_displacements=(_disp(),),
            m5_breaks=(_brk(),),
            m5_fvgs=(_fvg(),),
        )
    )
    c = scan.primary
    assert c is not None
    assert c.state is SetupState.SCANNING
    assert c.invalidation is NoTradeReason.CANDIDATE_INVALIDATED
    assert not c.is_alive
    assert c.display_alias is DisplayAlias.INVALIDATED


def test_armed_candidate_invalidated_by_counter_choch() -> None:
    counter = _brk(direction=Polarity.BEARISH, ts=BREAK_TS + timedelta(minutes=10))
    scan = detect_setups(
        _mtf(
            m15_liquidity=(_level(),),
            m15_displacements=(_disp(),),
            m5_breaks=(_brk(), counter),
            m5_fvgs=(_fvg(),),
        )
    )
    c = scan.primary
    assert c is not None
    assert c.invalidation is NoTradeReason.CANDIDATE_INVALIDATED


def test_armed_candidate_expires() -> None:
    scan = detect_setups(_full_long_mtf(), params=SetupParams(armed_bars=2))
    c = scan.primary
    assert c is not None
    assert c.state is SetupState.SCANNING
    assert c.invalidation is NoTradeReason.CANDIDATE_EXPIRED
    assert c.display_alias is DisplayAlias.EXPIRED


# =========================================================================== Duplicate Events


def test_duplicate_events_are_idempotent() -> None:
    single = detect_setups(_full_long_mtf()).primary
    dup = detect_setups(
        _mtf(
            m15_liquidity=(_level(),),
            m15_displacements=(_disp(), _disp()),
            m5_breaks=(_brk(), _brk()),
            m5_fvgs=(_fvg(), _fvg()),
        )
    ).primary
    assert single is not None and dup is not None
    assert dup.state is single.state is SetupState.ARMED
    assert dup.setup_id == single.setup_id
    assert dup.structure_break == single.structure_break
    assert dup.displacement == single.displacement


# =========================================================================== mehrere Setups


def test_multiple_setups_tracked_independently() -> None:
    # zwei sell-side Pools: 105.0 (wird gesweept → ARMED) und 103.5 (unberührt → LIQ_IDENTIFIED)
    scan = detect_setups(
        _mtf(
            m15_liquidity=(
                _level(price=105.0),
                _level(price=103.5, ltype=LiquidityType.PDL),
            ),
            m15_displacements=(_disp(),),
            m5_breaks=(_brk(),),
            m5_fvgs=(_fvg(),),
        )
    )
    assert len(scan.candidates) == 2
    ids = {c.setup_id for c in scan.candidates}
    assert len(ids) == 2
    by_state = {c.liquidity.price: c.state for c in scan.candidates}
    assert by_state[105.0] is SetupState.ARMED
    assert by_state[103.5] is SetupState.LIQUIDITY_IDENTIFIED
    assert scan.primary is not None and scan.primary.liquidity.price == 105.0  # weitester zuerst
    assert scan.state is SetupState.ARMED


# =========================================================================== Long/Short-Symmetrie


def test_long_short_symmetry() -> None:
    lng = detect_setups(_full_long_mtf()).primary
    sht = detect_setups(
        _mtf(
            m15_bars=_m15_short(),
            m15_liquidity=(
                _level(price=95.0, side=MarketSide.BUY_SIDE, ltype=LiquidityType.EQUAL_HIGHS),
            ),
            m15_displacements=(_disp(direction=Polarity.BEARISH),),
            m5_breaks=(_brk(direction=Polarity.BEARISH),),
            m5_fvgs=(_fvg(direction=Polarity.BEARISH, lo=94.5, hi=95.1),),
            htf_directional=RegimeDirectional.TREND_DOWN,
            htf_bias=Bias.SHORT,
            m5_bars=_m5_filler(base=94.5),
        )
    ).primary
    assert lng is not None and sht is not None
    assert lng.state is sht.state is SetupState.ARMED
    assert (lng.direction, sht.direction) == (Direction.LONG, Direction.SHORT)
    assert lng.sweep is not None and sht.sweep is not None
    assert round(lng.sweep.penetration_extreme + sht.sweep.penetration_extreme, 6) == _MIRROR
    assert lng.sweep.bars_to_reclaim == sht.sweep.bars_to_reclaim


# =========================================================================== Look-ahead / Replay


def test_lookahead_break_after_cutoff_is_ignored() -> None:
    future_break = _brk(ts=CUTOFF + timedelta(minutes=30))
    scan = detect_setups(
        _mtf(
            m15_liquidity=(_level(),),
            m15_displacements=(_disp(),),
            m5_breaks=(future_break,),
            m5_fvgs=(_fvg(),),
        )
    )
    c = scan.primary
    assert c is not None
    assert c.structure_break is None
    assert c.state is SetupState.SCANNING
    assert c.abort_reason is NoTradeReason.NO_STRUCTURE_SHIFT


def test_progressive_cutoff_advances_state_monotonically() -> None:
    order = [
        (RECLAIM_BAR - timedelta(minutes=15), SetupState.LIQUIDITY_IDENTIFIED),
        (RECLAIM_BAR + timedelta(minutes=15), SetupState.RECLAIMED),
        (DISP_END + timedelta(minutes=2), SetupState.DISPLACED),
        (BREAK_TS + timedelta(minutes=15), SetupState.ARMED),
    ]
    seen: list[int] = []
    for cutoff, expected in order:
        c = detect_setups(
            _mtf(
                m15_liquidity=(_level(),),
                m15_displacements=(_disp(),),
                m5_breaks=(_brk(),),
                m5_fvgs=(_fvg(),),
                m5_bars=_m5_filler(end=max(cutoff, DAY + timedelta(minutes=30))),
                cutoff=cutoff,
            )
        ).primary
        assert c is not None
        assert c.state is expected
        seen.append(_STATE_RANK[c.state])
    assert seen == sorted(seen)


def test_deterministic_replay() -> None:
    a = detect_setups(_full_long_mtf())
    b = detect_setups(_full_long_mtf())
    assert a == b


# =========================================================================== setup_id / Revision


def test_setup_id_stable_and_revision_tracks_changes() -> None:
    # Lauf 1: nur Liquidität identifiziert
    s1 = detect_setups(
        _mtf(m15_bars=_rows_to_bars(_WARMUP_LONG, tf=M15, start=DAY), m15_liquidity=(_level(),))
    ).primary
    assert s1 is not None
    assert s1.revision == 1
    sid = s1.setup_id
    assert sid.startswith("SMC-SWEEP-REV-01:BTCUSD:long:")

    # Lauf 2: dieselbe Instanz, jetzt vollständige Kette → State geändert → Revision +1
    s2 = detect_setups(_full_long_mtf(), previous=(s1,)).primary
    assert s2 is not None
    assert s2.setup_id == sid
    assert s2.revision == 2
    assert s2.created_at == s1.created_at  # created_at bleibt erhalten

    # Lauf 3: unverändert gegenüber Lauf 2 → Revision bleibt
    s3 = detect_setups(_full_long_mtf(), previous=(s2,)).primary
    assert s3 is not None
    assert s3.revision == 2


def test_revision_without_previous_is_one() -> None:
    for c in detect_setups(_full_long_mtf()).candidates:
        assert c.revision == 1
        assert c.created_at == CUTOFF


# =========================================================================== MTF-Kontext


def test_range_regime_allows_both_directions() -> None:
    scan = detect_setups(
        _mtf(
            htf_directional=RegimeDirectional.RANGE,
            htf_bias=Bias.NONE,
            m15_liquidity=(
                _level(price=105.0, side=MarketSide.SELL_SIDE, ltype=LiquidityType.RANGE_LOW),
                _level(price=109.0, side=MarketSide.BUY_SIDE, ltype=LiquidityType.RANGE_HIGH),
            ),
        )
    )
    dirs = {c.direction for c in scan.candidates}
    assert dirs == {Direction.LONG, Direction.SHORT}


def test_candidate_carries_mtf_cutoff_and_version() -> None:
    c = detect_setups(_full_long_mtf()).primary
    assert c is not None
    assert c.information_cutoff == CUTOFF
    assert c.setup_type == "SMC-SWEEP-REV-01"
    assert c.strategy_version == "0.1.1"
    assert c.updated_at == CUTOFF
