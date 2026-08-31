"""Phase 3 — Scoring-Engine (``strategy/scoring.py``, ``scoring-rubric.md`` §1–§4, §21).

Score misst „wie gut ist die Konstellation" — getrennt von Confidence / Confluence / Veto / Risk.
Getestet: Score-Formel · MVP-Gleichgewicht · A+/A/B/unter-B · Score- & Confidence-Grenzwerte ·
hoher Score + hartes Veto · niedriger Score + gute Confidence · fehlende Daten · widersprüchliche
Faktoren · Double-Counting-Ausweis · GATE/WEIGHTED/VETO-Exklusivität · Long/Short-Symmetrie ·
Look-ahead · deterministisches Replay.
"""

from __future__ import annotations

import dataclasses

import tests.unit.test_confidence as tc
import tests.unit.test_gates as gt
from trading_agent.core.enums import RiskTier
from trading_agent.strategy.confidence import assess_confidence
from trading_agent.strategy.confluence import FactorDirection, assess_confluence
from trading_agent.strategy.gates import evaluate_gates
from trading_agent.strategy.scoring import (
    _WEIGHTED_FACTORS,
    ScoreParams,
    ScoreReport,
    score_setup,
)

_STRONG_LIQ = (gt._buy_lvl(109.0, 0.6), gt._buy_lvl(112.0, 0.7))


def _ctx(*, settle: bool = True):
    mtf, cand = gt._long_setup(m15_extra_liq=_STRONG_LIQ)
    if settle:
        tc._settle_regime(mtf)
    gates = evaluate_gates(mtf, cand)
    conf = assess_confluence(mtf, cand, gates=gates)
    conf_rep = assess_confidence(mtf, cand)
    return mtf, cand, conf, conf_rep, gates


def _score(**over) -> ScoreReport:
    mtf, cand, conf, conf_rep, gates = over.pop("ctx", None) or _ctx()
    return score_setup(
        mtf,
        cand,
        confluence=over.pop("confluence", conf),
        confidence=over.pop("confidence", conf_rep),
        gates=over.pop("gates", gates),
        vetoed=over.pop("vetoed", False),
        params=over.pop("params", None),
    )


# =========================================================================== Formel / MVP


def test_score_formula_and_weight_normalisation() -> None:
    r = _score()
    assert isinstance(r, ScoreReport)
    avail = [f for f in r.factors if f.available]
    assert abs(r.raw - round(sum(f.contribution for f in avail), 6)) < 1e-6
    assert abs(r.weight_sum - sum(f.weight for f in avail)) < 1e-6
    expected = 100.0 * r.raw / r.weight_sum
    assert abs(r.score_0_100 - round(expected, 4)) < 1e-3
    assert 0.0 <= r.final_score <= 100.0
    assert r.penalties_total == 0.0
    assert abs(r.final_score - round(r.score_0_100, 4)) < 1e-3  # MVP: keine Penalties


def test_mvp_equal_weights_and_no_penalties() -> None:
    p = ScoreParams()
    assert set(p.weights) == set(_WEIGHTED_FACTORS)
    assert all(w == 10.0 for w in p.weights.values())
    assert p.penalties == {}
    r = _score()
    assert all(f.weight == 10.0 for f in r.factors)


def test_exactly_twelve_weighted_factors_no_gate_or_veto_names() -> None:
    r = _score()
    names = [f.name for f in r.factors]
    assert len(names) == 12 == len(set(names))
    assert set(names) == set(_WEIGHTED_FACTORS)
    # R-06: keine reinen GATE-/VETO-Faktornamen im Score
    forbidden = {"entry_location_ok", "rr_ok", "sl_definable", "regime_allowed", "chain_complete"}
    assert not (set(names) & forbidden)


# =========================================================================== Tier-Leiter


def test_tier_ladder_a_plus_a_b_below() -> None:
    ctx = _ctx()
    _, _, _, conf_rep, _ = ctx
    s = _score(ctx=ctx).final_score
    hi, mid, lo = s - 5, s + 5, s + 15  # unter s = erreichbar, über s = nicht

    def tier(sp: dict[str, float], cp: dict[str, float], conf: float) -> RiskTier:
        cr = dataclasses.replace(conf_rep, setup_confidence=conf, blocks_data=False)
        return _score(
            ctx=_ctx(),
            params=ScoreParams(tier_score_min=sp, tier_confidence_min=cp),
            confidence=cr,
        ).tier

    cp = {"A+": 0.80, "A": 0.70, "B": 0.60}
    assert tier({"A+": hi, "A": lo, "B": lo}, cp, 0.90) is RiskTier.A_PLUS
    # Score verfehlt A+ (Schwelle mid unerreichbar), erreicht A:
    assert tier({"A+": mid, "A": hi, "B": lo}, cp, 0.90) is RiskTier.A
    # Score erreicht nur B; Confidence verfehlt A:
    assert tier({"A+": mid, "A": mid, "B": hi}, cp, 0.62) is RiskTier.B
    # Alle Score-Schwellen unerreichbar:
    assert tier({"A+": mid, "A": mid, "B": mid}, cp, 0.95) is RiskTier.NO_TRADE


def test_score_boundary_is_inclusive() -> None:
    ctx = _ctx()
    _, _, _, cr, _ = ctx
    s = _score(ctx=ctx).final_score
    cr_ok = dataclasses.replace(cr, setup_confidence=0.95, blocks_data=False)
    p_at = ScoreParams(
        tier_score_min={"A+": s, "A": 0, "B": 0},
        tier_confidence_min={"A+": 0.0, "A": 0.0, "B": 0.0},
    )
    p_above = ScoreParams(
        tier_score_min={"A+": s + 0.01, "A": 0, "B": 0},
        tier_confidence_min={"A+": 0.0, "A": 0.0, "B": 0.0},
    )
    assert _score(ctx=_ctx(), params=p_at, confidence=cr_ok).tier is RiskTier.A_PLUS
    assert _score(ctx=_ctx(), params=p_above, confidence=cr_ok).tier is not RiskTier.A_PLUS


def test_confidence_boundary_is_inclusive() -> None:
    ctx = _ctx()
    _, _, _, cr, _ = ctx
    p = ScoreParams(
        tier_score_min={"A+": 0.0, "A": 0.0, "B": 0.0},
        tier_confidence_min={"A+": 0.80, "A": 0.70, "B": 0.60},
    )
    at = dataclasses.replace(cr, setup_confidence=0.80, blocks_data=False, blocks_setup=False)
    below = dataclasses.replace(cr, setup_confidence=0.7999, blocks_data=False, blocks_setup=False)
    assert _score(ctx=_ctx(), params=p, confidence=at).tier is RiskTier.A_PLUS
    assert _score(ctx=_ctx(), params=p, confidence=below).tier is RiskTier.A


# =========================================================================== Veto / Datenqualität


def test_hard_veto_forces_no_trade_despite_high_score() -> None:
    ctx = _ctx()
    _, _, _, cr, _ = ctx
    strong = dataclasses.replace(cr, setup_confidence=0.95, blocks_data=False)
    p = ScoreParams(
        tier_score_min={"A+": 0, "A": 0, "B": 0}, tier_confidence_min={"A+": 0, "A": 0, "B": 0}
    )
    r = _score(ctx=_ctx(), params=p, confidence=strong, vetoed=True)
    assert r.tier is RiskTier.NO_TRADE
    assert "Veto" in r.tier_reason
    assert r.final_score > 0.0  # der Score wird trotzdem berechnet (Ledger)


def test_bad_data_confidence_forces_no_trade() -> None:
    mtf, cand = tc._setup()
    tc._patch_dq(mtf, tc.M5, consistency=0.0)  # data_confidence → 0.0
    gates = evaluate_gates(mtf, cand)
    conf = assess_confluence(mtf, cand, gates=gates)
    cr = assess_confidence(mtf, cand)
    assert cr.blocks_data is True
    p = ScoreParams(
        tier_score_min={"A+": 0, "A": 0, "B": 0}, tier_confidence_min={"A+": 0, "A": 0, "B": 0}
    )
    r = score_setup(mtf, cand, confluence=conf, confidence=cr, gates=gates, params=p)
    assert r.tier is RiskTier.NO_TRADE
    assert "0.50" in r.tier_reason or "V6" in r.tier_reason


def test_low_score_good_confidence_still_no_trade() -> None:
    ctx = _ctx()
    _, _, _, cr, _ = ctx
    good = dataclasses.replace(cr, setup_confidence=0.95, blocks_data=False)
    # Score-Schwellen unerreichbar → auch mit exzellenter Confidence NO_TRADE
    p = ScoreParams(tier_score_min={"A+": 99, "A": 98, "B": 97})
    assert _score(ctx=_ctx(), params=p, confidence=good).tier is RiskTier.NO_TRADE


# =========================================================================== fehlende Daten


def test_missing_gates_excludes_gate_factors_from_denominator() -> None:
    ctx = _ctx()
    with_gates = _score(ctx=ctx)
    without = _score(ctx=_ctx(), gates=None)
    rr = next(f for f in without.factors if f.name == "risk_reward")
    loc = next(f for f in without.factors if f.name == "entry_location_depth")
    assert rr.available is False and loc.available is False
    assert without.weight_sum == with_gates.weight_sum - 20.0  # 2 × Gewicht 10
    assert without.final_score > 0.0  # Score bleibt berechenbar


def test_missing_session_factor_is_excluded_not_zeroed() -> None:
    r = _score()  # ohne session_names
    sc = next(f for f in r.factors if f.name == "session_context")
    assert sc.available is False
    assert sc.name not in {f.name for f in r.factors if f.available}


# =========================================================================== Widersprüche


def test_contradicting_confluence_factor_drives_value_to_zero() -> None:
    mtf, cand, conf, cr, gates = _ctx()
    patched = list(conf.factors)
    for i, f in enumerate(patched):
        if f.factor == "structure_shift":
            patched[i] = dataclasses.replace(
                f, direction=FactorDirection.CONTRADICT, contribution=-0.8
            )
    conf2 = dataclasses.replace(conf, factors=tuple(patched))
    r = score_setup(mtf, cand, confluence=conf2, confidence=cr, gates=gates)
    ss = next(f for f in r.factors if f.name == "structure_shift_quality")
    assert ss.value == 0.0 and ss.available is True  # zählt (gegen den Score), nicht ausgeschlossen
    assert r.final_score < _score(ctx=(mtf, cand, conf, cr, gates)).final_score


# =========================================================================== Double-Counting-Ausweis


def test_correlated_factor_groups_exposed() -> None:
    r = _score()
    g = r.correlated_factor_groups
    assert set(g["liquidity_event"]) == {"liquidity_quality", "reclaim_quality", "sweep_clarity"}
    assert set(g["momentum_structure"]) == {"displacement_strength", "structure_shift_quality"}
    assert set(g["htf_bias"]) == {"htf_bias_strength", "regime_alignment"}
    assert all(len(members) > 1 for members in g.values())


# =========================================================================== Symmetrie / Replay / PIT


def test_long_short_symmetry() -> None:
    long_r = _score()
    sm, sc, _ = tc._short()
    tc._settle_regime(sm)
    sg = evaluate_gates(sm, sc)
    scf = assess_confluence(sm, sc, gates=sg)
    scr = assess_confidence(sm, sc)
    short_r = score_setup(sm, sc, confluence=scf, confidence=scr, gates=sg)
    assert abs(long_r.final_score - short_r.final_score) < 6.0


def test_deterministic_replay() -> None:
    a = _score(ctx=_ctx())
    b = _score(ctx=_ctx())
    assert a == b


def test_lookahead_cutoff_propagated() -> None:
    mtf, cand, conf, cr, gates = _ctx()
    r = score_setup(mtf, cand, confluence=conf, confidence=cr, gates=gates)
    assert r.information_cutoff == mtf.information_cutoff
    assert r.setup_id == cand.setup_id
