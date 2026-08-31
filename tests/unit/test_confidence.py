"""Phase 3 — Confidence (``strategy/confidence.py``, ``confidence.md``).

Data- / Analysis- / Setup-Confidence sauber getrennt. Getestet: hohe/niedrige Datenqualität,
fehlende/stale Daten, hohe/niedrige Analysequalität, MTF-Konflikt, Structure-Clarity, ambivalenter
Sweep, FVG-Integrity, Confidence-Floors (data < 0.50 / setup < 0.60), floor_penalty,
Long/Short-Symmetrie, deterministisches Replay, Look-ahead, keine Double Counts.
"""

from __future__ import annotations

import dataclasses
from datetime import timedelta

import tests.unit.test_gates as gt
import tests.unit.test_setup_fsm as fsm
from trading_agent.core.enums import SwingLabel, SwingType, Timeframe
from trading_agent.strategy.confidence import (
    _ANALYSIS_TERMS,
    ConfidenceParams,
    ConfidenceReport,
    assess_confidence,
)
from trading_agent.strategy.primitives.models import SwingPoint

M5 = Timeframe.M5
M15 = Timeframe.M15
D1 = Timeframe.D1
H4 = Timeframe.H4
_LIQ = (gt._buy_lvl(109.0, 0.6), gt._buy_lvl(112.0, 0.7))


def _setup():
    m15b = fsm._rows_to_bars([*gt._WARMUP15, gt._SWEEP15, *gt._DISP15], tf=M15, start=fsm.DAY)
    mtf, cand = gt._long_setup(m15_bars=m15b, m15_extra_liq=_LIQ)
    return mtf, cand


def _patch_dq(mtf, tf, **kw):
    c = mtf.per_tf[tf]
    terms = dataclasses.replace(c.data_terms, **kw)
    mtf.per_tf[tf] = dataclasses.replace(c, data_terms=terms, data_confidence=terms.value)


def _patch_regime(mtf, tf, **kw):
    c = mtf.per_tf[tf]
    mtf.per_tf[tf] = dataclasses.replace(c, regime=dataclasses.replace(c.regime, **kw))


def _settle_regime(mtf):
    for tf in (D1, H4):
        _patch_regime(mtf, tf, bars_in_state=10, directional_score=0.85)


# =========================================================================== Data confidence


def test_data_high_quality_single_source_caps_at_source_term() -> None:
    mtf, cand = _setup()
    r = assess_confidence(mtf, cand)
    assert isinstance(r, ConfidenceReport)
    assert r.data.value == 0.8  # completeness/freshness/consistency = 1.0, source_term = 0.8
    assert r.data.limiting_factor == "source_term"
    assert r.blocks_data is False


def test_data_two_agreeing_sources() -> None:
    mtf, cand = _setup()
    r = assess_confidence(mtf, cand, source_count=2)
    assert r.data.value == 1.0
    assert r.data.terms["source_term"] == 1.0


def test_data_sources_disagree_zeroes_source_term() -> None:
    mtf, cand = _setup()
    r = assess_confidence(mtf, cand, source_count=2, source_disagreement_atr=0.5)
    assert r.data.terms["source_term"] == 0.0
    assert r.data.value == 0.0
    assert r.blocks_data is True


def test_data_bad_consistency() -> None:
    mtf, cand = _setup()
    _patch_dq(mtf, M5, consistency=0.0)
    r = assess_confidence(mtf, cand)
    assert r.data.value == 0.0
    assert r.data.limiting_factor == "consistency"
    assert r.blocks_data is True


def test_data_stale_freshness() -> None:
    mtf, cand = _setup()
    _patch_dq(mtf, H4, freshness=0.30)
    r = assess_confidence(mtf, cand)
    assert r.data.value == 0.30
    assert r.data.limiting_factor == "freshness"
    assert r.blocks_data is True  # < 0.50


def test_data_incomplete_completeness() -> None:
    mtf, cand = _setup()
    _patch_dq(mtf, M15, completeness=0.40)
    r = assess_confidence(mtf, cand)
    assert r.data.value == 0.40 and r.blocks_data is True
    assert r.data.evidence["worst_timeframe"] == "M15"


def test_data_min_not_mean() -> None:
    mtf, cand = _setup()
    _patch_dq(mtf, M5, freshness=0.2)  # eine schlechte Dimension …
    r = assess_confidence(mtf, cand)
    assert r.data.value == 0.2  # … bestimmt allein den Wert (min, nicht Mittelwert)


# =========================================================================== Analysis confidence


def test_analysis_high_quality() -> None:
    mtf, cand = _setup()
    _settle_regime(mtf)
    r = assess_confidence(mtf, cand)
    assert r.analysis.value > 0.85
    assert r.analysis.terms["htf_mtf_agreement"] == 1.0
    assert r.analysis.terms["fvg_integrity"] == 1.0
    assert set(r.analysis.terms) == set(_ANALYSIS_TERMS)


def test_analysis_low_quality_pulls_setup_down() -> None:
    mtf, cand = _setup()
    hi = assess_confidence(mtf, cand)
    weak_brk = dataclasses.replace(cand.structure_break, break_distance_atr=0.05)
    weak_fvg = dataclasses.replace(cand.entry_fvg, fill_fraction=0.45)
    lo_cand = dataclasses.replace(cand, structure_break=weak_brk, entry_fvg=weak_fvg)
    lo = assess_confidence(mtf, lo_cand)
    assert lo.analysis.value < hi.analysis.value
    assert lo.setup_confidence < hi.setup_confidence


def test_analysis_mtf_conflict_lowers_agreement() -> None:
    mtf, cand = _setup()
    m2 = dataclasses.replace(
        mtf, htf_regime_gate=dataclasses.replace(mtf.htf_regime_gate, disagreement=0.8)
    )
    r = assess_confidence(m2, cand)
    assert abs(r.analysis.terms["htf_mtf_agreement"] - 0.2) < 1e-6


def test_analysis_bad_structure_clarity_knapper_bruch() -> None:
    mtf, cand = _setup()
    brk = dataclasses.replace(cand.structure_break, break_distance_atr=0.1)
    r = assess_confidence(mtf, dataclasses.replace(cand, structure_break=brk))
    assert r.analysis.terms["structure_clarity"] < 0.6
    assert r.analysis.evidence["structure_note"] == "knapper Bruch"


def test_analysis_structure_ambiguity_equal_highs() -> None:
    mtf, cand = _setup()
    eq_swing = SwingPoint(
        type=SwingType.SWING_HIGH,
        timeframe=M5,
        bar_index=3,
        timestamp=fsm.DAY,
        price=105.3,
        confirmed_at=fsm.DAY,
        label=SwingLabel.EQUAL,
    )
    brk = dataclasses.replace(cand.structure_break, broken_swing=eq_swing)
    r = assess_confidence(mtf, dataclasses.replace(cand, structure_break=brk))
    assert r.analysis.terms["structure_clarity"] <= 0.30
    assert r.analysis.evidence["structure_ambiguous"] is True


def test_analysis_ambiguous_sweep_multiple_pools() -> None:
    mtf, cand = _setup()
    clean = assess_confidence(mtf, cand).analysis.terms["sweep_unambiguity"]
    # ein zweiter Pool, im selben Fenster gesweept
    m15c = mtf.per_tf[M15]
    second = dataclasses.replace(
        gt._buy_lvl(103.0, 0.5),
        swept_at=cand.sweep.reclaim_bar + timedelta(minutes=15),
    )
    mtf.per_tf[M15] = dataclasses.replace(m15c, liquidity=(*m15c.liquidity, second))
    messy = assess_confidence(mtf, cand).analysis.terms["sweep_unambiguity"]
    assert messy < clean
    assert assess_confidence(mtf, cand).analysis.evidence["sweep_pools_in_window"] == 2


def test_analysis_bad_fvg_integrity_stale() -> None:
    mtf, cand = _setup()
    from trading_agent.core.enums import ZoneState

    stale = dataclasses.replace(cand.entry_fvg, state=ZoneState.STALE)
    r = assess_confidence(mtf, dataclasses.replace(cand, entry_fvg=stale))
    assert r.analysis.terms["fvg_integrity"] == 0.0


def test_unconfirmed_swing_flagged_and_blocks() -> None:
    mtf, cand = _setup()
    fresh_swing = SwingPoint(
        type=SwingType.SWING_HIGH,
        timeframe=M5,
        bar_index=9,
        timestamp=mtf.information_cutoff - timedelta(minutes=10),
        price=105.3,
        confirmed_at=mtf.information_cutoff - timedelta(minutes=5),  # nur 1 M5-Bar her
    )
    brk = dataclasses.replace(cand.structure_break, broken_swing=fresh_swing)
    r = assess_confidence(mtf, dataclasses.replace(cand, structure_break=brk))
    assert r.unconfirmed_swing is True
    assert r.blocking is True
    assert r.analysis.terms["swing_confirmation"] <= 0.5


# =========================================================================== Setup confidence / Floors


def test_setup_confidence_formula() -> None:
    mtf, cand = _setup()
    _settle_regime(mtf)
    p = ConfidenceParams()
    r = assess_confidence(mtf, cand, params=p)
    if not r.floor_penalty_applied:
        expected = p.wd * r.data.value + p.wa * r.analysis.value
        assert abs(r.setup_confidence - round(expected, 6)) < 1e-6


def test_floor_penalty_when_component_weak() -> None:
    mtf, cand = _setup()
    _patch_dq(mtf, M5, freshness=0.55)  # data unter soft_floor 0.60, aber über hard_floor 0.50
    r = assess_confidence(mtf, cand)
    assert r.floor_penalty_applied is True
    assert r.blocks_data is False
    full = 0.40 * r.data.value + 0.60 * r.analysis.value
    assert abs(r.setup_confidence - round(full * 0.5, 6)) < 1e-6


def test_data_hard_floor_blocks() -> None:
    mtf, cand = _setup()
    _patch_dq(mtf, M5, consistency=0.0)
    r = assess_confidence(mtf, cand)
    assert r.blocks_data is True and r.blocking is True


def test_setup_below_min_blocks() -> None:
    mtf, cand = _setup()
    _patch_dq(mtf, M5, freshness=0.55, completeness=0.55)
    weak_brk = dataclasses.replace(cand.structure_break, break_distance_atr=0.02)
    weak_fvg = dataclasses.replace(cand.entry_fvg, fill_fraction=0.49)
    r = assess_confidence(
        mtf, dataclasses.replace(cand, structure_break=weak_brk, entry_fvg=weak_fvg)
    )
    assert r.setup_confidence < 0.60
    assert r.blocks_setup is True


def test_limiting_factor_global() -> None:
    mtf, cand = _setup()
    _patch_dq(mtf, M5, consistency=0.1)  # klar kleinster Term überhaupt
    r = assess_confidence(mtf, cand)
    assert r.limiting_factor == "data.consistency"


# =========================================================================== Symmetrie / Replay / PIT


def test_long_short_symmetry() -> None:
    lm, lc = _setup()
    _settle_regime(lm)
    sm, sc, _ = _short()
    _settle_regime(sm)
    lr = assess_confidence(lm, lc)
    sr = assess_confidence(sm, sc)
    assert abs(lr.setup_confidence - sr.setup_confidence) < 0.06
    assert abs(lr.analysis.value - sr.analysis.value) < 0.06
    assert lr.data.value == sr.data.value


def _short():
    zone = fsm._fvg(direction=fsm.Polarity.BEARISH, lo=200.0 - 105.5, hi=200.0 - 104.9)
    rows = gt._mirror([*gt._WARMUP15, gt._SWEEP15, *gt._DISP15])
    from trading_agent.strategy.setup_detection import detect_setups

    mtf = fsm._mtf(
        m15_bars=fsm._rows_to_bars(rows, tf=M15, start=fsm.DAY),
        m15_liquidity=(
            fsm._level(
                price=95.0, side=fsm.MarketSide.BUY_SIDE, ltype=fsm.LiquidityType.EQUAL_HIGHS
            ),
            gt._sell_lvl(200.0 - 109.0, 0.6),
            gt._sell_lvl(200.0 - 112.0, 0.7),
        ),
        m15_displacements=(fsm._disp(direction=fsm.Polarity.BEARISH),),
        m5_breaks=(fsm._brk(direction=fsm.Polarity.BEARISH),),
        m5_fvgs=(zone,),
        htf_directional=fsm.RegimeDirectional.TREND_DOWN,
        htf_bias=fsm.Bias.SHORT,
        m5_bars=fsm._m5_filler(base=94.5),
    )
    cand = detect_setups(mtf).primary
    assert cand is not None and cand.is_armed
    return mtf, cand, None


def test_deterministic_replay() -> None:
    m1, c1 = _setup()
    m2, c2 = _setup()
    assert assess_confidence(m1, c1) == assess_confidence(m2, c2)


def test_lookahead_all_timestamps_respect_cutoff() -> None:
    mtf, cand = _setup()
    r = assess_confidence(mtf, cand)
    for rec in (r.data, r.analysis):
        assert rec.information_cutoff == mtf.information_cutoff
        assert rec.timestamp is not None and rec.timestamp <= mtf.information_cutoff
    assert r.information_cutoff == mtf.information_cutoff


# =========================================================================== keine Double Counts


def test_analysis_terms_are_exactly_the_six_spec_terms() -> None:
    mtf, cand = _setup()
    r = assess_confidence(mtf, cand)
    assert tuple(r.analysis.terms) == _ANALYSIS_TERMS
    p = ConfidenceParams()
    assert abs(sum(p.analysis_weights.values()) - 1.0) < 1e-9


def test_analysis_value_is_weighted_mean_of_terms() -> None:
    mtf, cand = _setup()
    _settle_regime(mtf)
    r = assess_confidence(mtf, cand)
    p = ConfidenceParams()
    expected = sum(p.analysis_weights[t] * r.analysis.terms[t] for t in _ANALYSIS_TERMS)
    assert abs(r.analysis.value - round(expected, 6)) < 1e-6
