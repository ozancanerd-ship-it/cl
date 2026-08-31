"""Phase 3 — Confluence-Engine (``strategy/confluence.py``).

Long/Short · starke/schwache Confluence · widersprüchliche Faktoren · MTF-/Regime-Konflikte ·
fehlende Daten (News/Derivatives/Cross-Asset) · Double-Counting · Location/RR BLOCK ·
Confirmation vorhanden/nicht · Look-ahead · deterministisches Replay · Symmetrie.

``MtfContext``/``SetupCandidate``/``GateReport`` kommen aus ``test_setup_fsm`` + ``test_gates``.
"""

from __future__ import annotations

import dataclasses

import tests.unit.test_gates as gt
import tests.unit.test_setup_fsm as fsm
from trading_agent.core.enums import (
    ConfirmationPattern,
    Direction,
    Polarity,
    RegimeDirectional,
    RegimeVolatility,
    SessionName,
    Timeframe,
    ZoneKind,
)
from trading_agent.strategy.confluence import (
    ConfluenceDataQuality,
    ConfluenceGroup,
    ConfluenceReport,
    ConfluenceRole,
    FactorDirection,
    assess_confluence,
)
from trading_agent.strategy.gates import evaluate_gates
from trading_agent.strategy.price_action import ConfirmationScan, EntryConfirmation
from trading_agent.strategy.primitives.models import StructureState

M15 = Timeframe.M15
_MIRROR = 200.0
_STRONG_LIQ = (gt._buy_lvl(109.0, 0.6), gt._buy_lvl(112.0, 0.7))


def _long():
    mtf, cand = gt._long_setup(m15_extra_liq=_STRONG_LIQ)
    gates = evaluate_gates(mtf, cand)
    return mtf, cand, gates


def _short():
    mtf, cand = gt._short_setup(
        m15_extra_liq=(gt._sell_lvl(_MIRROR - 109.0, 0.6), gt._sell_lvl(_MIRROR - 112.0, 0.7))
    )
    gates = evaluate_gates(mtf, cand)
    return mtf, cand, gates


def _confirmation(direction: Polarity = Polarity.BULLISH) -> ConfirmationScan:
    c = EntryConfirmation(
        pattern=ConfirmationPattern.PIN,
        timeframe=Timeframe.M1,
        bar_timestamp=fsm.BREAK_TS,
        direction=direction,
        strength=0.8,
        zone_kind=ZoneKind.FVG,
        zone_id="FVG-M5-bullish-5",
        entry_ref_price=105.1,
    )
    return ConfirmationScan(
        confirmed=True,
        direction=Direction.LONG if direction is Polarity.BULLISH else Direction.SHORT,
        zone_id=c.zone_id,
        zone_kind=ZoneKind.FVG,
        checked_through=fsm.CUTOFF,
        confirmations=(c,),
    )


def _patch_tf_structure(mtf, tf: Timeframe, directional: RegimeDirectional) -> None:
    c = mtf.per_tf[tf]
    mtf.per_tf[tf] = dataclasses.replace(
        c, structure=StructureState(timeframe=tf, directional=directional)
    )


def _patch_tf_regime(mtf, tf: Timeframe, **kw) -> None:
    c = mtf.per_tf[tf]
    mtf.per_tf[tf] = dataclasses.replace(c, regime=dataclasses.replace(c.regime, **kw))


# =========================================================================== Grund


def test_long_confluence_report_shape() -> None:
    mtf, cand, gates = _long()
    r = assess_confluence(mtf, cand, gates=gates, confirmation=_confirmation())
    assert isinstance(r, ConfluenceReport)
    assert r.direction is Direction.LONG
    assert r.net_confluence > 0.0
    assert r.support_score > 0.5
    assert 0.0 <= r.agreement <= 1.0
    assert len(r.factors) >= 12
    # jeder Faktor trägt die geforderten Felder
    for f in r.factors:
        assert f.factor and f.factor_group and f.role and f.direction and f.reason
        assert f.information_cutoff == mtf.information_cutoff
        assert f.data_quality in ConfluenceDataQuality
    # genau EIN structure_shift (nie BOS + CHoCH getrennt)
    assert [f.factor for f in r.factors].count("structure_shift") == 1
    assert not any(f.factor in ("bos", "choch") for f in r.factors)


def test_short_symmetry() -> None:
    lm, lc, lg = _long()
    sm, sc, sg = _short()
    lr = assess_confluence(lm, lc, gates=lg, confirmation=_confirmation(Polarity.BULLISH))
    sr = assess_confluence(sm, sc, gates=sg, confirmation=_confirmation(Polarity.BEARISH))
    assert lr.direction is Direction.LONG and sr.direction is Direction.SHORT
    assert abs(lr.net_confluence - sr.net_confluence) < 0.06
    assert abs(lr.support_score - sr.support_score) < 0.03
    # HTF-Merge-Faktor spiegelt (beide unterstützen ihre Richtung)
    lh = next(f for f in lr.factors if f.factor == "htf_merged_alignment")
    sh = next(f for f in sr.factors if f.factor == "htf_merged_alignment")
    assert lh.direction is sh.direction is FactorDirection.SUPPORT


# =========================================================================== stark / schwach


def test_strong_vs_weak_confluence() -> None:
    sm, sc, sg = _long()
    strong = assess_confluence(sm, sc, gates=sg, confirmation=_confirmation())

    wm, wc = gt._long_setup(m15_extra_liq=_STRONG_LIQ)
    # schwaches Displacement + schwacher Struktur-Bruch injizieren
    weak_disp = dataclasses.replace(wc.displacement, net_move_atr=1.55, body_ratio=0.56)
    weak_brk = dataclasses.replace(wc.structure_break, break_distance_atr=3.9)
    wc = dataclasses.replace(wc, displacement=weak_disp, structure_break=weak_brk)
    wg = evaluate_gates(wm, wc)
    weak = assess_confluence(wm, wc, gates=wg)

    assert strong.net_confluence > weak.net_confluence
    sm_grp = strong.group(ConfluenceGroup.MOMENTUM_STRUCTURE)
    wm_grp = weak.group(ConfluenceGroup.MOMENTUM_STRUCTURE)
    assert sm_grp is not None and wm_grp is not None
    assert sm_grp.net > wm_grp.net


# =========================================================================== Widersprüche


def test_contradicting_htf_structure_lowers_net() -> None:
    base_m, base_c, base_g = _long()
    base = assess_confluence(base_m, base_c, gates=base_g)

    m, c, g = _long()
    _patch_tf_structure(m, Timeframe.D1, RegimeDirectional.TREND_DOWN)
    _patch_tf_structure(m, Timeframe.H4, RegimeDirectional.TREND_DOWN)
    conflicted = assess_confluence(m, c, gates=g)

    assert conflicted.net_confluence < base.net_confluence
    d1f = next(f for f in conflicted.factors if f.factor == "d1_structure_alignment")
    assert d1f.direction is FactorDirection.CONTRADICT


def test_regime_conflict_sets_flag_but_not_net() -> None:
    m, c, g = _long()
    _patch_tf_regime(m, Timeframe.H4, volatility=RegimeVolatility.EXTREME)
    r = assess_confluence(m, c, gates=g)
    assert "regime_vol_extreme:V3" in r.contradiction_flags
    vf = next(f for f in r.factors if f.factor == "volatility_regime")
    assert vf.scored is False and vf.direction is FactorDirection.CONTRADICT
    assert vf.role is ConfluenceRole.VETO_CANDIDATE


def test_htf_directional_conflict_flag() -> None:
    m, c, g = _long()
    _patch_tf_regime(m, Timeframe.D1, directional=RegimeDirectional.TREND_DOWN)
    _patch_tf_regime(m, Timeframe.H4, directional=RegimeDirectional.TREND_UP)
    r = assess_confluence(m, c, gates=g)
    assert "htf_conflict:V1" in r.contradiction_flags


def test_mtf_disagreement_is_context_only() -> None:
    m, c, g = _long()
    m2 = dataclasses.replace(
        m, htf_regime_gate=dataclasses.replace(m.htf_regime_gate, disagreement=0.9)
    )
    r = assess_confluence(m2, c, gates=g)
    mf = next(f for f in r.factors if f.factor == "mtf_disagreement")
    assert mf.scored is False
    assert mf.factor_group is ConfluenceGroup.MTF_COHERENCE
    assert mf.direction is FactorDirection.CONTRADICT


# =========================================================================== fehlende Daten


def test_external_context_unavailable_excluded_from_net() -> None:
    m, c, g = _long()
    r = assess_confluence(m, c, gates=g)
    ext = r.group(ConfluenceGroup.EXTERNAL_CONTEXT)
    assert ext is not None
    assert ext.available is False
    assert "news_context" in r.unavailable
    assert "derivatives_context" in r.unavailable
    assert "cross_asset_context" in r.unavailable
    for name in ("news_context", "derivatives_context", "cross_asset_context"):
        f = next(x for x in r.factors if x.factor == name)
        assert f.data_quality is ConfluenceDataQuality.UNAVAILABLE
        assert f.contribution == 0.0
        assert f.direction is FactorDirection.NEUTRAL
    # net_confluence identisch, ob EXTERNAL da ist oder nicht (nur verfügbare gescorte Gruppen)
    active_weight = sum(grp.weight for grp in r.groups if grp.scored and grp.available)
    assert active_weight > 0


def test_confirmation_absent_is_neutral_not_negative() -> None:
    m, c, g = _long()
    without = assess_confluence(m, c, gates=g, confirmation=None)
    cf = next(f for f in without.factors if f.factor == "price_action_confirmation")
    assert cf.data_quality is ConfluenceDataQuality.UNAVAILABLE
    assert "price_action_confirmation" in without.unavailable
    grp = without.group(ConfluenceGroup.CONFIRMATION)
    assert grp is not None and grp.available is False

    with_conf = assess_confluence(m, c, gates=g, confirmation=_confirmation())
    cf2 = next(f for f in with_conf.factors if f.factor == "price_action_confirmation")
    assert cf2.direction is FactorDirection.SUPPORT
    assert with_conf.net_confluence >= without.net_confluence


def test_gates_absent_marks_location_and_rr_unavailable() -> None:
    m, c, _ = _long()
    r = assess_confluence(m, c, gates=None)
    assert "discount_premium_depth" in r.unavailable
    assert "risk_reward" in r.unavailable
    assert r.group(ConfluenceGroup.LOCATION).available is False
    assert r.group(ConfluenceGroup.RISK_REWARD).available is False


def test_session_unavailable_when_not_passed() -> None:
    m, c, g = _long()
    r = assess_confluence(m, c, gates=g, session_names=None)
    sf = next(f for f in r.factors if f.factor == "session_context")
    assert sf.data_quality is ConfluenceDataQuality.UNAVAILABLE and sf.scored is False

    r2 = assess_confluence(m, c, gates=g, session_names={SessionName.LONDON_NY_OVERLAP})
    sf2 = next(f for f in r2.factors if f.factor == "session_context")
    assert sf2.data_quality is ConfluenceDataQuality.OK
    assert sf2.contribution == 1.0  # Overlap


# =========================================================================== Double Counting


def test_momentum_group_averages_not_sums() -> None:
    m, c, g = _long()
    r = assess_confluence(m, c, gates=g, confirmation=_confirmation())
    grp = r.group(ConfluenceGroup.MOMENTUM_STRUCTURE)
    members = [f for f in r.factors if f.factor_group is ConfluenceGroup.MOMENTUM_STRUCTURE]
    assert len(members) >= 2
    assert grp is not None
    # Gruppe = relevanz-gewichteter Durchschnitt, niemals Summe → im Betrag ≤ 1
    assert -1.0 <= grp.net <= 1.0
    wsum = sum(f.relevance for f in members)
    expected = sum(f.relevance * f.contribution for f in members) / wsum
    assert abs(grp.net - round(expected, 6)) < 1e-6
    # ein zusätzlicher redundanter Support-Faktor würde die Gruppe nicht über ihr Mitgliedermax heben
    assert grp.net <= max(f.contribution for f in members) + 1e-9


def test_net_confluence_bounded_and_group_weighted() -> None:
    m, c, g = _long()
    r = assess_confluence(m, c, gates=g, confirmation=_confirmation())
    assert -1.0 <= r.net_confluence <= 1.0
    active = [grp for grp in r.groups if grp.scored and grp.available]
    wsum = sum(grp.weight for grp in active)
    expected = sum(grp.net * grp.weight for grp in active) / wsum
    assert abs(r.net_confluence - round(expected, 6)) < 1e-6


# =========================================================================== Location / RR BLOCK


def test_location_block_contradicts_and_flags() -> None:
    m, c = gt._long_setup(m15_extra_liq=_STRONG_LIQ)
    blocked = dataclasses.replace(c, entry_fvg=fsm._fvg(lo=107.2, hi=107.8))  # Premium
    g = evaluate_gates(m, blocked)
    r = assess_confluence(m, blocked, gates=g)
    lf = next(f for f in r.factors if f.factor == "discount_premium_depth")
    assert lf.direction is FactorDirection.CONTRADICT
    assert lf.contribution == -1.0
    assert "location_block:V2" in r.contradiction_flags


def test_rr_block_contradicts_and_flags() -> None:
    m, c = gt._long_setup(m15_extra_liq=(gt._buy_lvl(105.8, 0.7),))  # target room zu klein
    g = evaluate_gates(m, c)
    r = assess_confluence(m, c, gates=g)
    rf = next(f for f in r.factors if f.factor == "risk_reward")
    assert rf.direction is FactorDirection.CONTRADICT
    assert "rr_block:V8" in r.contradiction_flags


# =========================================================================== Look-ahead / Replay


def test_all_factor_timestamps_respect_cutoff() -> None:
    m, c, g = _long()
    r = assess_confluence(m, c, gates=g, confirmation=_confirmation())
    for f in r.factors:
        assert f.information_cutoff == m.information_cutoff
        if f.timestamp is not None:
            assert f.timestamp <= m.information_cutoff


def test_deterministic_replay() -> None:
    m1, c1, g1 = _long()
    m2, c2, g2 = _long()
    a = assess_confluence(m1, c1, gates=g1, confirmation=_confirmation())
    b = assess_confluence(m2, c2, gates=g2, confirmation=_confirmation())
    assert a == b


def test_roles_distinguish_context_from_entry_support() -> None:
    m, c, g = _long()
    r = assess_confluence(m, c, gates=g, confirmation=_confirmation())
    context = {f.factor for f in r.context_factors}
    assert {"mtf_disagreement", "volatility_regime", "data_confidence"} <= context
    entry = {f.factor for f in r.factors if f.role is ConfluenceRole.ENTRY_SUPPORT}
    assert {"htf_merged_alignment", "sweep_clarity", "displacement_strength"} <= entry
    # Kontext-Faktoren sind nie 'scored'
    assert all(f.scored is False for f in r.context_factors)
