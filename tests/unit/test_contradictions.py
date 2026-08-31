"""Phase 3 — Widerspruchs-Matrix (``strategy/contradictions.py``, ``contradictions.md`` §4/§5).

Jedes C1–C12 · Long/Short · mehrere gleichzeitig · Severity · Evidence · Veto-Echo blockt nicht ·
Penalties werden gemeldet nicht angewandt · Score nicht still verändert · Look-ahead · Replay.
"""

from __future__ import annotations

import dataclasses
from datetime import timedelta

import tests.unit.test_gates as gt
import tests.unit.test_scoring as ts
from trading_agent.core.enums import (
    Direction,
    LiquidityState,
    MarketSide,
    NoTradeReason,
    Polarity,
    RegimeVolatility,
    SwingLabel,
    SwingType,
    Timeframe,
    VetoId,
    ZoneState,
)
from trading_agent.strategy.confluence import assess_confluence
from trading_agent.strategy.contradictions import (
    ContradictionKind,
    ContradictionParams,
    ContradictionReport,
    ContradictionSeverity,
    assess_contradictions,
)
from trading_agent.strategy.gates import evaluate_gates
from trading_agent.strategy.primitives.models import FVG, SwingPoint
from trading_agent.strategy.setup_detection import SetupScan, detect_setups
from trading_agent.strategy.veto import assess_vetoes

M15, H4 = Timeframe.M15, Timeframe.H4


def _ctx():
    mtf, cand, conf, _cr, gates = ts._ctx()
    return mtf, cand, conf, gates


def _lvl(price: float, side: MarketSide, state: LiquidityState, swept_at):
    return dataclasses.replace(
        gt._buy_lvl(price, 0.6) if side is MarketSide.BUY_SIDE else gt._sell_lvl(price, 0.6),
        state=state,
        swept_at=swept_at,
    )


def _add_m15_liq(mtf, *levels):
    c = mtf.per_tf[M15]
    mtf.per_tf[M15] = dataclasses.replace(c, liquidity=(*c.liquidity, *levels))


def _reassess(mtf, cand, gates):
    conf = assess_confluence(mtf, cand, gates=gates)
    return assess_contradictions(mtf, cand, confluence=conf, gates=gates)


# =========================================================================== Baseline


def test_no_contradictions_baseline() -> None:
    mtf, cand, conf, gates = _ctx()
    r = assess_contradictions(mtf, cand, confluence=conf, gates=gates)
    assert isinstance(r, ContradictionReport)
    assert r.blocked is False
    assert not [x for x in r.records if x.severity is ContradictionSeverity.BLOCK]


# =========================================================================== C1 / C2


def test_c1_opposing_liquidity_breakout() -> None:
    mtf, cand, _conf, gates = _ctx()
    _add_m15_liq(
        mtf,
        _lvl(
            110.0,
            MarketSide.BUY_SIDE,
            LiquidityState.BROKEN,
            mtf.information_cutoff - timedelta(minutes=30),
        ),
    )
    r = _reassess(mtf, cand, gates)
    rec = next(x for x in r.records if x.contradiction_id == "C1")
    assert rec.severity is ContradictionSeverity.BLOCK
    assert rec.no_trade_reason is NoTradeReason.OPPOSING_LIQUIDITY_BREAKOUT
    assert r.blocked is True


def test_c1_stale_breakout_does_not_block() -> None:
    mtf, cand, _conf, gates = _ctx()
    _add_m15_liq(
        mtf,
        _lvl(
            110.0,
            MarketSide.BUY_SIDE,
            LiquidityState.BROKEN,
            mtf.information_cutoff - timedelta(days=2),
        ),
    )
    assert "C1" not in {x.contradiction_id for x in _reassess(mtf, cand, gates).records}


def test_c2_both_sides_swept() -> None:
    mtf, cand, _conf, gates = _ctx()
    anchor = cand.sweep.reclaim_bar
    _add_m15_liq(
        mtf,
        _lvl(106.0, MarketSide.BUY_SIDE, LiquidityState.SWEPT, anchor + timedelta(minutes=15)),
        _lvl(103.0, MarketSide.SELL_SIDE, LiquidityState.SWEPT, anchor - timedelta(minutes=15)),
    )
    r = _reassess(mtf, cand, gates)
    rec = next(x for x in r.records if x.contradiction_id == "C2")
    assert rec.no_trade_reason is NoTradeReason.MESSY_LIQUIDITY
    assert r.blocked is True


# =========================================================================== C3–C8 / C10 (Veto-Echos)


def test_veto_echoes_recorded_but_not_blocking() -> None:
    mtf, cand, conf, gates = _ctx()
    ts.tc._patch_regime(mtf, H4, volatility=RegimeVolatility.EXTREME)  # → V3
    vr = assess_vetoes(mtf, cand, gates=gates)
    assert VetoId.V3 in vr.veto_ids
    r = assess_contradictions(mtf, cand, confluence=conf, gates=gates, veto=vr)
    echo = next(x for x in r.records if x.contradiction_id == "C8")
    assert echo.kind is ContradictionKind.VETO_ECHO
    assert echo.severity is ContradictionSeverity.INFO
    assert echo.covered_by_veto is VetoId.V3
    assert VetoId.V3 in r.veto_echoes
    assert r.blocked is False  # das harte NO_TRADE kommt vom Veto-Schritt, nicht hier


def test_no_veto_report_no_echoes() -> None:
    mtf, cand, conf, gates = _ctx()
    r = assess_contradictions(mtf, cand, confluence=conf, gates=gates, veto=None)
    assert not [x for x in r.records if x.kind is ContradictionKind.VETO_ECHO]


# =========================================================================== C9


def _opposing_h4_fvg(zone: FVG, *, overlap_frac: float) -> FVG:
    lo, hi = zone.zone_low, zone.zone_high
    h = hi - lo
    return FVG(
        direction=Polarity.BEARISH,  # gegen D (LONG)
        timeframe=H4,
        zone_low=lo,
        zone_high=lo + overlap_frac * h,
        created_bar=zone.created_bar,
        bar_index=3,
        state=ZoneState.UNMITIGATED,
    )


def test_c9_hard_when_overlap_ge_50() -> None:
    mtf, cand, _conf, gates = _ctx()
    h4 = mtf.per_tf[H4]
    mtf.per_tf[H4] = dataclasses.replace(
        h4, fvgs=(*h4.fvgs, _opposing_h4_fvg(cand.entry_fvg, overlap_frac=0.8))
    )
    r = _reassess(mtf, cand, gates)
    rec = next(x for x in r.records if x.contradiction_id == "C9")
    assert rec.severity is ContradictionSeverity.BLOCK
    assert rec.no_trade_reason is NoTradeReason.ENTRY_INTO_OPPOSING_HTF_ZONE


def test_c9_penalty_when_overlap_below_50() -> None:
    mtf, cand, _conf, gates = _ctx()
    h4 = mtf.per_tf[H4]
    mtf.per_tf[H4] = dataclasses.replace(
        h4, fvgs=(*h4.fvgs, _opposing_h4_fvg(cand.entry_fvg, overlap_frac=0.3))
    )
    r = _reassess(mtf, cand, gates)
    assert r.blocked is False
    assert r.negative_penalties.get("proximity_opposing_htf_zone") == 10.0


# =========================================================================== C11 / C12


def test_c11_overstretched_break() -> None:
    mtf, cand, conf, gates = _ctx()
    aborted = dataclasses.replace(cand, abort_reason=NoTradeReason.NO_STRUCTURE_SHIFT)
    r = assess_contradictions(mtf, aborted, confluence=conf, gates=gates)
    rec = next(x for x in r.records if x.contradiction_id == "C11")
    assert rec.no_trade_reason is NoTradeReason.NO_STRUCTURE_SHIFT


def test_c12_counter_setup_conflict() -> None:
    mtf, cand, conf, gates = _ctx()
    opp = dataclasses.replace(
        cand,
        setup_id="SMC-SWEEP-REV-01:BTCUSD:short:equal_highs@110.0",
        direction=Direction.SHORT,
    )
    scan = SetupScan(
        instrument="BTCUSD",
        information_cutoff=mtf.information_cutoff,
        state=cand.state,
        candidates=(cand, opp),
        regime_ok=True,
    )
    r = assess_contradictions(mtf, cand, confluence=conf, gates=gates, scan=scan)
    rec = next(x for x in r.records if x.contradiction_id == "C12")
    assert rec.no_trade_reason is NoTradeReason.COUNTER_SETUP_CONFLICT
    assert r.blocked is True


def test_c12_no_scan_no_conflict() -> None:
    mtf, cand, conf, gates = _ctx()
    r = assess_contradictions(mtf, cand, confluence=conf, gates=gates, scan=None)
    assert "C12" not in {x.contradiction_id for x in r.records}


# =========================================================================== §5 Negativfaktoren


def test_negative_messy_sweep() -> None:
    mtf, cand, _conf, gates = _ctx()
    anchor = cand.sweep.reclaim_bar
    _add_m15_liq(
        mtf,
        _lvl(104.0, MarketSide.SELL_SIDE, LiquidityState.SWEPT, anchor + timedelta(minutes=15)),
    )
    r = _reassess(mtf, cand, gates)
    assert r.negative_penalties.get("messy_sweep") == 8.0
    assert r.blocked is False


def test_negative_stale_structure() -> None:
    mtf, cand, conf, gates = _ctx()
    old_swing = SwingPoint(
        type=SwingType.SWING_HIGH,
        timeframe=Timeframe.M5,
        bar_index=1,
        timestamp=mtf.information_cutoff - timedelta(hours=20),
        price=105.3,
        confirmed_at=mtf.information_cutoff - timedelta(hours=20),  # > 50 M5-Bars
        label=SwingLabel.LH,
    )
    brk = dataclasses.replace(cand.structure_break, broken_swing=old_swing)
    r = assess_contradictions(
        mtf, dataclasses.replace(cand, structure_break=brk), confluence=conf, gates=gates
    )
    assert r.negative_penalties.get("stale_structure") == 5.0


def test_negative_weak_displacement() -> None:
    mtf, cand, conf, gates = _ctx()
    weak = dataclasses.replace(cand.displacement, net_move_atr=1.7)  # < 1.2 × 1.5
    r = assess_contradictions(
        mtf, dataclasses.replace(cand, displacement=weak), confluence=conf, gates=gates
    )
    assert r.negative_penalties.get("weak_displacement") == 6.0


def test_negative_mtf_partial_disagreement() -> None:
    mtf, cand, _conf, gates = _ctx()
    mtf = dataclasses.replace(
        mtf, htf_regime_gate=dataclasses.replace(mtf.htf_regime_gate, disagreement=0.5)
    )
    conf2 = assess_confluence(mtf, cand, gates=gates)
    r = assess_contradictions(mtf, cand, confluence=conf2, gates=gates)
    assert r.negative_penalties.get("mtf_partial_disagreement") == 7.0


def test_negative_wide_sl() -> None:
    mtf, cand, conf, gates = _ctx()
    r = assess_contradictions(
        mtf,
        cand,
        confluence=conf,
        gates=gates,
        params=ContradictionParams(wide_sl_atr_factor=0.3),
    )
    assert r.negative_penalties.get("wide_sl") == 5.0


def test_negative_late_session() -> None:
    mtf, cand, conf, gates = _ctx()
    r = assess_contradictions(mtf, cand, confluence=conf, gates=gates, minutes_to_session_end=10.0)
    assert r.negative_penalties.get("late_session") == 4.0


def test_penalties_reported_not_a_score() -> None:
    mtf, cand, conf, gates = _ctx()
    r = assess_contradictions(
        mtf,
        cand,
        confluence=conf,
        gates=gates,
        params=ContradictionParams(wide_sl_atr_factor=0.3),
        minutes_to_session_end=5.0,
    )
    assert r.penalties_total == 9.0  # wide_sl 5 + late_session 4
    # der Report enthält keine Score-Größe
    assert not hasattr(r, "score") and not hasattr(r, "final_score")


# =========================================================================== Kombination / Symmetrie / PIT


def test_multiple_contradictions() -> None:
    mtf, cand, _conf, gates = _ctx()
    anchor = cand.sweep.reclaim_bar
    _add_m15_liq(
        mtf,
        _lvl(
            110.0,
            MarketSide.BUY_SIDE,
            LiquidityState.BROKEN,
            mtf.information_cutoff - timedelta(minutes=20),
        ),
        _lvl(106.0, MarketSide.BUY_SIDE, LiquidityState.SWEPT, anchor + timedelta(minutes=15)),
        _lvl(103.0, MarketSide.SELL_SIDE, LiquidityState.SWEPT, anchor - timedelta(minutes=15)),
    )
    ids = {x.contradiction_id for x in _reassess(mtf, cand, gates).records}
    assert {"C1", "C2"} <= ids


def test_long_short_symmetry_c1() -> None:
    lm, lc, _lconf, lg = _ctx()
    _add_m15_liq(
        lm,
        _lvl(
            110.0,
            MarketSide.BUY_SIDE,
            LiquidityState.BROKEN,
            lm.information_cutoff - timedelta(minutes=20),
        ),
    )
    long_blocked = _reassess(lm, lc, lg).blocked

    sm, sc, _sconf, _scr, sg = ts._ctx()
    # symmetrischer Kern: gespiegelte Seite (SELL_SIDE below) für einen SHORT-Kandidaten
    short_cand = dataclasses.replace(sc, direction=Direction.SHORT)
    _add_m15_liq(
        sm,
        _lvl(
            95.0,
            MarketSide.SELL_SIDE,
            LiquidityState.BROKEN,
            sm.information_cutoff - timedelta(minutes=20),
        ),
    )
    sconf = assess_confluence(sm, short_cand, gates=sg)
    short_blocked = assess_contradictions(sm, short_cand, confluence=sconf, gates=sg).blocked
    assert long_blocked is short_blocked is True


def test_deterministic_replay() -> None:
    a = _ctx()
    b = _ctx()
    ra = assess_contradictions(a[0], a[1], confluence=a[2], gates=a[3])
    rb = assess_contradictions(b[0], b[1], confluence=b[2], gates=b[3])
    assert ra == rb


def test_lookahead_records_carry_cutoff() -> None:
    mtf, cand, _conf, gates = _ctx()
    _add_m15_liq(
        mtf,
        _lvl(
            110.0,
            MarketSide.BUY_SIDE,
            LiquidityState.BROKEN,
            mtf.information_cutoff - timedelta(minutes=20),
        ),
    )
    r = _reassess(mtf, cand, gates)
    for rec in r.records:
        assert rec.information_cutoff == mtf.information_cutoff
        assert rec.timestamp <= mtf.information_cutoff


def test_full_pipeline_ordering_smoke() -> None:
    # No-Trade → Veto → Contradiction: sanity, dass die Objekte zusammenpassen
    mtf, cand = gt._long_setup(m15_extra_liq=(gt._buy_lvl(109.0, 0.6),))
    gates = evaluate_gates(mtf, cand)
    conf = assess_confluence(mtf, cand, gates=gates)
    vr = assess_vetoes(mtf, cand, gates=gates)
    _ = detect_setups(mtf)
    r = assess_contradictions(mtf, cand, confluence=conf, gates=gates, veto=vr)
    assert isinstance(r, ContradictionReport)
