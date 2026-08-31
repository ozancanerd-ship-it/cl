"""Phase 3 — Location-Gate (§8) + RR-Gate (§10/§12–§16), ``strategy/gates.py``.

Long/Short · Premium/Discount · Swept-Leg · verschiedene Zonen · ungültige Zone · fehlende Daten ·
SL-Geometrie · TP-Geometrie · RR_to_TP2 · blended_RR · min_target_room · Grenzfälle · Look-ahead ·
deterministisches Replay · Symmetrie.

Die ``MtfContext``-/``SetupCandidate``-Bausteine kommen aus ``test_setup_fsm`` (die FSM + Primitives
haben eigene Golden-Tests); geprüft wird hier die **Geometrie-/Gate-Logik** isoliert.
"""

from __future__ import annotations

import dataclasses
from datetime import timedelta

import tests.unit.test_setup_fsm as fsm
from trading_agent.core.enums import (
    ConfirmationPattern,
    EntryMode,
    LiquidityType,
    MarketSide,
    NoTradeReason,
    Polarity,
    SwingType,
    Timeframe,
    VetoId,
    ZoneKind,
    ZoneState,
)
from trading_agent.core.time import parse_timestamp
from trading_agent.strategy.gates import (
    GateOutcome,
    GateParams,
    GateReport,
    evaluate_gates,
    location_gate,
    rr_gate,
)
from trading_agent.strategy.price_action import EntryConfirmation
from trading_agent.strategy.primitives.models import (
    FVG,
    LiquidityLevel,
    StructureState,
    SwingPoint,
)
from trading_agent.strategy.setup_detection import detect_setups

M15 = Timeframe.M15
M5 = Timeframe.M5
_MIRROR = 200.0

# M15: 16 Warmup + Sweep (SELL_SIDE 105.0, Docht 104.5, Reclaim 105.6) + 2 Displacement-Bars hoch.
_WARMUP15 = [(106.0, 106.6, 105.4, 106.0)] * 16
_SWEEP15 = (106.0, 106.1, 104.5, 105.6)
_DISP15 = [(105.6, 107.0, 105.5, 106.9), (106.9, 108.5, 106.8, 108.3)]
SWEPT_LEG = (104.5, 108.5)  # Sweep-Extrem … Displacement-Extrem


def _m15(extra: list[tuple[float, float, float, float]] | None = None) -> list:
    return fsm._rows_to_bars(
        [*_WARMUP15, _SWEEP15, *_DISP15, *(extra or [])], tf=M15, start=fsm.DAY
    )


def _mirror(
    rows: list[tuple[float, float, float, float]],
) -> list[tuple[float, float, float, float]]:
    return [(_MIRROR - o, _MIRROR - low, _MIRROR - h, _MIRROR - c) for o, h, low, c in rows]


def _buy_lvl(price: float, strength: float = 0.6) -> LiquidityLevel:
    return LiquidityLevel(
        type=LiquidityType.SWING_HIGH,
        side=MarketSide.BUY_SIDE,
        price=price,
        timeframe=Timeframe.H4,
        formed_at=fsm.DAY,
        strength=strength,
    )


def _sell_lvl(price: float, strength: float = 0.6) -> LiquidityLevel:
    return LiquidityLevel(
        type=LiquidityType.SWING_LOW,
        side=MarketSide.SELL_SIDE,
        price=price,
        timeframe=Timeframe.H4,
        formed_at=fsm.DAY,
        strength=strength,
    )


def _long_setup(
    *,
    fvg: FVG | None = None,
    m15_extra_liq: tuple[LiquidityLevel, ...] = (),
    m15_bars: list | None = None,
    cutoff=fsm.CUTOFF,
):
    """→ (mtf, armed SetupCandidate) für eine Long-Kette mit sauberem Swept-Leg."""
    zone = fvg if fvg is not None else fsm._fvg(lo=104.9, hi=105.5)
    mtf = fsm._mtf(
        m15_bars=m15_bars if m15_bars is not None else _m15(),
        m15_liquidity=(fsm._level(), *m15_extra_liq),
        m15_displacements=(fsm._disp(),),
        m5_breaks=(fsm._brk(),),
        m5_fvgs=(zone,),
        cutoff=cutoff,
    )
    cand = detect_setups(mtf).primary
    assert cand is not None and cand.is_armed, cand
    return mtf, cand


def _short_setup(*, m15_extra_liq: tuple[LiquidityLevel, ...] = ()):
    zone = fsm._fvg(direction=Polarity.BEARISH, lo=_MIRROR - 105.5, hi=_MIRROR - 104.9)
    m15b = fsm._rows_to_bars(_mirror([*_WARMUP15, _SWEEP15, *_DISP15]), tf=M15, start=fsm.DAY)
    mtf = fsm._mtf(
        m15_bars=m15b,
        m15_liquidity=(
            fsm._level(price=95.0, side=MarketSide.BUY_SIDE, ltype=LiquidityType.EQUAL_HIGHS),
            *m15_extra_liq,
        ),
        m15_displacements=(fsm._disp(direction=Polarity.BEARISH),),
        m5_breaks=(fsm._brk(direction=Polarity.BEARISH),),
        m5_fvgs=(zone,),
        htf_directional=fsm.RegimeDirectional.TREND_DOWN,
        htf_bias=fsm.Bias.SHORT,
        m5_bars=fsm._m5_filler(base=94.5),
    )
    cand = detect_setups(mtf).primary
    assert cand is not None and cand.is_armed
    return mtf, cand


# =========================================================================== Location-Gate


def test_location_allow_long_discount() -> None:
    mtf, cand = _long_setup()  # zone_mid 105.2 tief im Discount des swept_leg [104.5, 108.5]
    loc = location_gate(mtf, cand)
    assert loc.outcome is GateOutcome.ALLOW
    assert loc.swept_leg == SWEPT_LEG
    assert loc.pd_position is not None and loc.pd_position < 0.5
    assert loc.veto is None and loc.reason is None


def test_location_block_long_premium() -> None:
    mtf, cand = _long_setup(fvg=fsm._fvg(lo=107.2, hi=107.8))  # zone_mid 107.5 im Premium
    loc = location_gate(mtf, cand)
    assert loc.outcome is GateOutcome.BLOCK
    assert loc.reason is NoTradeReason.ENTRY_WRONG_SIDE_OF_EQUILIBRIUM
    assert loc.veto is VetoId.V2
    assert loc.pd_position is not None and loc.pd_position > 0.5


def test_location_short_symmetry() -> None:
    lmtf, lcand = _long_setup()
    smtf, scand = _short_setup()
    lloc = location_gate(lmtf, lcand)
    sloc = location_gate(smtf, scand)
    assert lloc.outcome is sloc.outcome is GateOutcome.ALLOW
    assert lloc.pd_position is not None and sloc.pd_position is not None
    assert round(lloc.pd_position + sloc.pd_position, 6) == 1.0  # gespiegelt
    assert sloc.swept_leg is not None
    assert round(sloc.swept_leg[0] + SWEPT_LEG[1], 6) == _MIRROR


def test_location_block_thin_zone() -> None:
    mtf, cand = _long_setup()
    thin = dataclasses.replace(cand, entry_fvg=fsm._fvg(lo=105.19, hi=105.21))  # Höhe 0.02
    loc = location_gate(mtf, thin)
    assert loc.outcome is GateOutcome.BLOCK
    assert loc.reason is NoTradeReason.NO_ENTRY_ZONE
    assert "dünn" in loc.note


def test_location_block_mitigated_zone() -> None:
    mtf, cand = _long_setup()
    mitigated = dataclasses.replace(cand, entry_fvg=fsm._fvg(state=ZoneState.MITIGATED))
    loc = location_gate(mtf, mitigated)
    assert loc.outcome is GateOutcome.BLOCK
    assert loc.reason is NoTradeReason.NO_ENTRY_ZONE


def test_location_wait_when_swept_leg_undeterminable() -> None:
    mtf, cand = _long_setup()
    # Displacement-Fenster ohne Bars → keine Swept-Leg-Range
    far = fsm._disp(
        start=parse_timestamp("2024-06-03T20:00:00Z"),
        end=parse_timestamp("2024-06-03T21:00:00Z"),
    )
    cand2 = dataclasses.replace(cand, displacement=far)
    loc = location_gate(mtf, cand2)
    assert loc.outcome is GateOutcome.WAIT
    assert "Swept-Leg" in loc.note


# =========================================================================== RR-Gate: SL


def test_rr_sl_is_the_farther_candidate() -> None:
    mtf, cand = _long_setup(m15_extra_liq=(_buy_lvl(109.0), _buy_lvl(111.0, 0.7)))
    rr = rr_gate(mtf, cand)
    g = rr.geometry
    assert g is not None
    # entry = proximal edge (zone_high 105.5); SL hinter dem tieferen der beiden Kandidaten
    assert g.entry == 105.5
    assert g.sl < 104.5  # hinter dem Sweep-Extrem 104.5 (nicht nur hinter zone_low 104.9)
    assert g.sl < g.entry
    assert g.r_distance == round(abs(g.entry - g.sl), 8)


def test_rr_block_sl_too_wide() -> None:
    mtf, cand = _long_setup(m15_extra_liq=(_buy_lvl(109.0), _buy_lvl(111.0, 0.7)))
    rr = rr_gate(mtf, cand, params=GateParams(sl_max_distance_atr=0.3))
    assert rr.outcome is GateOutcome.BLOCK
    assert NoTradeReason.SL_TOO_WIDE in rr.reasons
    assert VetoId.V10 in rr.vetoes


def test_rr_block_sl_too_tight_via_spread() -> None:
    mtf, cand = _long_setup(m15_extra_liq=(_buy_lvl(109.0),))
    rr = rr_gate(mtf, cand, spread=5.0, params=GateParams(sl_min_spread_multiple=5.0))
    assert rr.outcome is GateOutcome.BLOCK
    assert NoTradeReason.SL_TOO_TIGHT in rr.reasons


def test_rr_block_sl_too_tight_via_atr_floor() -> None:
    mtf, cand = _long_setup(m15_extra_liq=(_buy_lvl(109.0),))
    rr = rr_gate(mtf, cand, params=GateParams(sl_min_distance_atr=99.0))
    assert rr.outcome is GateOutcome.BLOCK
    assert NoTradeReason.SL_TOO_TIGHT in rr.reasons


# =========================================================================== RR-Gate: TP + RR


def test_rr_allow_full_geometry() -> None:
    mtf, cand = _long_setup(m15_extra_liq=(_buy_lvl(109.0, 0.6), _buy_lvl(112.0, 0.7)))
    rr = rr_gate(mtf, cand)
    g = rr.geometry
    assert rr.outcome is GateOutcome.ALLOW
    assert g is not None
    assert g.sl < g.entry < g.tp1 < g.tp2
    assert g.rr_to_tp2 >= 2.0
    assert g.blended_rr >= 1.3
    assert g.target_room_r >= 1.5
    assert g.tp3_ref.startswith("runner")


def test_rr_block_target_room_too_small() -> None:
    # einzige opposing Liquidität klebt direkt über dem Entry
    mtf, cand = _long_setup(m15_extra_liq=(_buy_lvl(105.8, 0.7),))
    rr = rr_gate(mtf, cand)
    assert rr.outcome is GateOutcome.BLOCK
    assert NoTradeReason.RR_BELOW_MIN in rr.reasons
    assert VetoId.V8 in rr.vetoes
    assert rr.geometry is not None and rr.geometry.target_room_r < 1.5


def test_rr_tp2_from_structure_level() -> None:
    # schwache opposing Liquidität (nicht „signifikant"), aber ein H4-Swing-High über TP1
    mtf, cand = _long_setup(m15_extra_liq=(_buy_lvl(109.0, 0.3),))
    sp = SwingPoint(
        type=SwingType.SWING_HIGH,
        timeframe=Timeframe.H4,
        bar_index=3,
        timestamp=fsm.DAY,
        price=110.0,
        confirmed_at=fsm.DAY,
    )
    h4 = mtf.per_tf[Timeframe.H4]
    mtf.per_tf[Timeframe.H4] = dataclasses.replace(
        h4,
        structure=StructureState(
            timeframe=Timeframe.H4,
            directional=h4.structure.directional,
            last_swing_high=sp,
        ),
    )
    g = rr_gate(mtf, cand, params=GateParams(target_timeframes=(Timeframe.M15,))).geometry
    assert g is not None
    assert g.tp2_from_structure
    lo = g.entry + 2.0 * g.r_distance
    hi = g.entry + 3.0 * g.r_distance
    assert g.tp2 == round(max(lo, min(hi, 110.0)), 8)


def test_rr_wait_no_atr() -> None:
    mtf, cand = _long_setup()
    m15 = mtf.per_tf[M15]
    mtf.per_tf[M15] = dataclasses.replace(m15, atr=0.0)
    rr = rr_gate(mtf, cand)
    assert rr.outcome is GateOutcome.WAIT
    assert "ATR" in rr.note


def test_rr_note_when_no_opposing_liquidity() -> None:
    mtf, cand = _long_setup()  # keine BUY_SIDE-Levels injiziert
    rr = rr_gate(mtf, cand)
    assert rr.geometry is not None
    assert "keine opposing Liquidität" in rr.note
    assert rr.geometry.target_room_r == float("inf")


# =========================================================================== Entry-Modi


def test_entry_mode_limit_at_mid() -> None:
    mtf, cand = _long_setup(m15_extra_liq=(_buy_lvl(109.0),))
    rr = rr_gate(mtf, cand, params=GateParams(entry_mode=EntryMode.LIMIT_AT_MID))
    assert rr.geometry is not None
    assert rr.geometry.entry == 105.2  # zone_mid von [104.9, 105.5]


def test_entry_mode_confirmation_market_needs_confirmation() -> None:
    mtf, cand = _long_setup(m15_extra_liq=(_buy_lvl(109.0),))
    p = GateParams(entry_mode=EntryMode.CONFIRMATION_MARKET)
    assert rr_gate(mtf, cand, params=p).outcome is GateOutcome.WAIT

    conf = EntryConfirmation(
        pattern=ConfirmationPattern.PIN,
        timeframe=Timeframe.M1,
        bar_timestamp=fsm.BREAK_TS,
        direction=Polarity.BULLISH,
        strength=0.7,
        zone_kind=ZoneKind.FVG,
        zone_id="FVG-M5-bullish-5",
        entry_ref_price=105.1,
    )
    rr = rr_gate(mtf, cand, confirmation=conf, params=p)
    assert rr.geometry is not None
    assert rr.geometry.entry == 105.1


# =========================================================================== evaluate_gates


def test_evaluate_gates_short_circuits_on_location_block() -> None:
    mtf, cand = _long_setup(fvg=fsm._fvg(lo=107.2, hi=107.8))
    rep = evaluate_gates(mtf, cand)
    assert isinstance(rep, GateReport)
    assert rep.rr is None
    assert rep.outcome is GateOutcome.BLOCK
    assert not rep.allowed
    assert NoTradeReason.ENTRY_WRONG_SIDE_OF_EQUILIBRIUM in rep.reasons
    assert VetoId.V2 in rep.vetoes


def test_evaluate_gates_allow_end_to_end() -> None:
    mtf, cand = _long_setup(m15_extra_liq=(_buy_lvl(109.0, 0.6), _buy_lvl(112.0, 0.7)))
    rep = evaluate_gates(mtf, cand)
    assert rep.allowed
    assert rep.geometry is not None
    assert rep.reasons == () and rep.vetoes == ()


def test_evaluate_gates_not_armed_candidate() -> None:
    mtf = fsm._mtf(m15_liquidity=(fsm._level(),))  # bleibt LIQUIDITY_IDENTIFIED
    cand = detect_setups(mtf).primary
    assert cand is not None and not cand.is_armed
    rep = evaluate_gates(mtf, cand)
    assert rep.outcome is GateOutcome.BLOCK
    assert NoTradeReason.NO_ENTRY_ZONE in rep.reasons


# =========================================================================== Look-ahead / Replay


def test_lookahead_swept_leg_excludes_bars_after_cutoff() -> None:
    # spätere M15-Bar mit riesigem High — darf NICHT ins swept_leg lecken
    spike = [(108.3, 130.0, 108.0, 129.0)]  # open 04:45, close 05:00
    cutoff = fsm.DISP_END + timedelta(minutes=15)  # 04:45 = close der letzten Displacement-Bar
    mtf, cand = _long_setup(m15_bars=_m15(extra=spike), cutoff=cutoff)
    loc = location_gate(mtf, cand)
    assert loc.swept_leg == SWEPT_LEG  # Displacement-Extrem 108.5, der Spike (130) fehlt


def test_deterministic_replay() -> None:
    mtf1, cand1 = _long_setup(m15_extra_liq=(_buy_lvl(109.0), _buy_lvl(112.0, 0.7)))
    mtf2, cand2 = _long_setup(m15_extra_liq=(_buy_lvl(109.0), _buy_lvl(112.0, 0.7)))
    assert evaluate_gates(mtf1, cand1) == evaluate_gates(mtf2, cand2)


def test_rr_long_short_symmetry() -> None:
    lmtf, lcand = _long_setup(m15_extra_liq=(_buy_lvl(109.0, 0.6), _buy_lvl(112.0, 0.7)))
    smtf, scand = _short_setup(
        m15_extra_liq=(_sell_lvl(_MIRROR - 109.0, 0.6), _sell_lvl(_MIRROR - 112.0, 0.7))
    )
    lg = rr_gate(lmtf, lcand).geometry
    sg = rr_gate(smtf, scand).geometry
    assert lg is not None and sg is not None
    assert round(lg.entry + sg.entry, 4) == _MIRROR
    assert round(lg.sl + sg.sl, 4) == _MIRROR
    assert round(lg.tp1 + sg.tp1, 4) == _MIRROR
    assert round(lg.tp2 + sg.tp2, 4) == _MIRROR
    assert round(lg.r_distance, 4) == round(sg.r_distance, 4)
    assert round(lg.rr_to_tp2, 4) == round(sg.rr_to_tp2, 4)
    assert round(lg.blended_rr, 4) == round(sg.blended_rr, 4)
