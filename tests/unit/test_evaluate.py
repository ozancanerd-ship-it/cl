"""Phase 3 — ``strategy.evaluate`` Orchestrator (``SPEC-ADDENDUM-0.1.1`` §1.2).

BUY / SELL / WAIT / NO_TRADE end-to-end · No-Trade überstimmt Score · Veto überstimmt Score ·
Contradiction blockt · Confidence-Floor blockt · Explainability (context_ref / *_detail) ·
Long/Short-Symmetrie · deterministisches Replay · Look-ahead (alles am cutoff) · MarketContext-Pfad.
"""

from __future__ import annotations

import dataclasses

import tests.unit.test_confidence as tc
import tests.unit.test_gates as gt
import tests.unit.test_scoring as ts
import tests.unit.test_setup_fsm as fsm
import tests.unit.test_veto as tv
from trading_agent.core.enums import (
    DecisionType,
    NoTradeReason,
    RegimeVolatility,
    RiskTier,
    Timeframe,
    VetoId,
)
from trading_agent.strategy.decision import Decision
from trading_agent.strategy.evaluate import (
    EvaluateParams,
    EvaluationResult,
    decide,
    evaluate,
    evaluate_from_mtf,
)
from trading_agent.strategy.no_trade import SystemState

M15, H4, M5 = Timeframe.M15, Timeframe.H4, Timeframe.M5
_LIQ = (gt._buy_lvl(109.0, 0.6), gt._buy_lvl(112.0, 0.7))


def _clean(mtf):
    """News-Feed verfügbar + Spread gesetzt (sonst blocken NEWS_FEED_UNAVAILABLE / V4)."""
    return tv._with_spread(tv._with_news(mtf, feed_ok=True), 0.02)


def _long_mtf(*, settle: bool = True):
    mtf, _cand = gt._long_setup(m15_extra_liq=_LIQ)
    if settle:
        tc._settle_regime(mtf)
    return _clean(mtf)


def _run(mtf, **kw) -> EvaluationResult:
    return evaluate_from_mtf(mtf, **kw)


# =========================================================================== BUY / SELL


def test_buy_baseline() -> None:
    r = _run(_long_mtf())
    assert isinstance(r, EvaluationResult)
    dec = r.decision
    assert dec.decision is DecisionType.BUY
    assert dec.tier in (RiskTier.A_PLUS, RiskTier.A, RiskTier.B)
    assert dec.sl < dec.entry < dec.tp1 < dec.tp2
    assert dec.rr_to_tp2 is not None and dec.score is not None and dec.confidence is not None
    # alle Zwischen-Reports vorhanden
    assert r.candidate is not None and r.veto is not None and r.gates is not None
    assert r.confluence is not None and r.contradictions is not None
    assert r.confidence is not None and r.score is not None


def test_sell_baseline_symmetry() -> None:
    sm, _sc, _ = tc._short()
    tc._settle_regime(sm)
    sm = _clean(sm)
    r = _run(sm)
    assert r.decision.decision is DecisionType.SELL
    assert r.decision.sl > r.decision.entry > r.decision.tp1 > r.decision.tp2

    lr = _run(_long_mtf())
    assert lr.decision.decision is DecisionType.BUY
    # Score / Confidence in ähnlicher Größenordnung (Symmetrie der Bewertung)
    assert abs(lr.decision.score - r.decision.score) < 8.0


# =========================================================================== WAIT


# Warmup ohne Sweep-Bar, aber bis zum Modul-Cutoff frisch (20 M15-Bars → letzte schließt 05:00)
_WARMUP_FRESH = [(106.0, 106.6, 105.4, 106.0)] * 20


def test_wait_forming_state() -> None:
    warmup = fsm._rows_to_bars(_WARMUP_FRESH, tf=M15, start=fsm.DAY)
    mtf = _clean(fsm._mtf(m15_bars=warmup, m15_liquidity=(fsm._level(),)))
    r = _run(mtf)
    assert r.decision.decision is DecisionType.WAIT
    assert r.decision.setup_state.is_forming
    assert r.decision.reason_codes == () and r.decision.vetoes == ()


def test_wait_no_candidates_bias_set() -> None:
    warmup = fsm._rows_to_bars(_WARMUP_FRESH, tf=M15, start=fsm.DAY)
    mtf = _clean(fsm._mtf(m15_bars=warmup, m15_liquidity=()))
    r = _run(mtf)
    assert r.decision.decision is DecisionType.WAIT
    assert "keine qualifizierende Liquidität" in r.decision.chain_progress


# =========================================================================== NO_TRADE (Gates)


def test_no_trade_kill_switch_overrides_everything() -> None:
    r = _run(_long_mtf(), system=SystemState(kill_switch_global=True))
    assert r.decision.decision is DecisionType.NO_TRADE
    assert NoTradeReason.KILL_SWITCH_GLOBAL in r.decision.reason_codes
    assert r.candidate is None  # Pipeline vor der FSM gestoppt


def test_no_trade_news_feed_unavailable() -> None:
    mtf, _ = gt._long_setup(m15_extra_liq=_LIQ)  # ohne _clean → kein News-Feed
    mtf = tv._with_spread(mtf, 0.02)
    r = _run(mtf)
    assert r.decision.decision is DecisionType.NO_TRADE
    assert NoTradeReason.NEWS_FEED_UNAVAILABLE in r.decision.reason_codes


def test_no_trade_regime_gate() -> None:
    mtf = _long_mtf()
    gate = dataclasses.replace(mtf.htf_regime_gate, ok=False, reason=NoTradeReason.REGIME_UNCLEAR)
    mtf = dataclasses.replace(mtf, htf_regime_gate=gate)
    r = _run(mtf)
    assert r.decision.decision is DecisionType.NO_TRADE
    assert NoTradeReason.REGIME_UNCLEAR in r.decision.reason_codes


def test_no_trade_veto_overrides_score() -> None:
    mtf = _long_mtf()
    tc._patch_regime(mtf, H4, volatility=RegimeVolatility.EXTREME)  # → V3
    r = _run(mtf)
    assert r.decision.decision is DecisionType.NO_TRADE
    assert VetoId.V3 in r.decision.vetoes
    assert r.score is None  # Score nach Veto nicht mehr berechnet


def test_no_trade_location_block() -> None:
    # RR-Gate hart einschränken → BLOCK (V10) → NO_TRADE
    mtf = _long_mtf()
    p = EvaluateParams(gates=dataclasses.replace(gt.GateParams(), sl_max_distance_atr=0.3))
    r = _run(mtf, params=p)
    assert r.decision.decision is DecisionType.NO_TRADE
    assert VetoId.V10 in r.decision.vetoes or NoTradeReason.SL_TOO_WIDE in r.decision.reason_codes


def test_no_trade_score_below_b() -> None:
    mtf = _long_mtf()
    p = EvaluateParams(
        scoring=dataclasses.replace(
            ts.ScoreParams(), tier_score_min={"A+": 99.0, "A": 98.0, "B": 97.0}
        )
    )
    r = _run(mtf, params=p)
    assert r.decision.decision is DecisionType.NO_TRADE
    assert NoTradeReason.SCORE_BELOW_B in r.decision.reason_codes
    assert r.score is not None  # Score wird fürs Ledger trotzdem berechnet


def test_no_trade_contradiction_c2() -> None:
    from datetime import timedelta

    from trading_agent.core.enums import LiquidityState, MarketSide
    from trading_agent.strategy.setup_detection import detect_setups

    mtf = _long_mtf()
    anchor = detect_setups(mtf).primary.sweep.reclaim_bar
    c = mtf.per_tf[M15]
    both = (
        dataclasses.replace(
            gt._buy_lvl(125.0, 0.6),  # weit weg → beeinflusst RR nicht, aber SWEPT im Fenster
            state=LiquidityState.SWEPT,
            swept_at=anchor + timedelta(minutes=15),
        ),
        dataclasses.replace(
            gt._sell_lvl(90.0, 0.6),
            state=LiquidityState.SWEPT,
            swept_at=anchor - timedelta(minutes=15),
        ),
    )
    assert both[0].side is MarketSide.BUY_SIDE and both[1].side is MarketSide.SELL_SIDE
    mtf.per_tf[M15] = dataclasses.replace(c, liquidity=(*c.liquidity, *both))
    r = _run(mtf)
    assert r.decision.decision is DecisionType.NO_TRADE
    assert NoTradeReason.MESSY_LIQUIDITY in r.decision.reason_codes


def test_no_trade_confidence_floor() -> None:
    mtf = dataclasses.replace(_long_mtf(), data_confidence=0.3)
    r = _run(mtf)
    assert r.decision.decision is DecisionType.NO_TRADE
    assert NoTradeReason.DATA_CONFIDENCE_FLOOR in r.decision.reason_codes


# =========================================================================== API / Explainability


def test_decide_returns_bare_decision() -> None:
    from tests.unit.test_mtf import _DAILY_UP, _m5_series
    from trading_agent.core.types import MarketContext

    m5 = _m5_series(_DAILY_UP)
    mc = MarketContext(
        instrument="BTCUSD",
        base_timeframe=M5,
        information_cutoff=m5[-1].close_time,
        series={M5: tuple(m5)},
        spread=0.5,
    )
    assert isinstance(decide(mc), Decision)


def test_explainability_payload() -> None:
    r = _run(_long_mtf())
    dec = r.decision
    assert dec.context_ref["htf_directional"]
    assert "confluence_net" in dec.context_ref and "chain_progress" in dec.context_ref
    assert dec.score_detail is not None and "factors" in dec.score_detail
    assert dec.confidence_detail is not None
    assert dec.confidence_detail["setup_confidence"] == r.confidence.setup_confidence
    assert dec.setup_id == r.candidate.setup_id


def test_deterministic_replay() -> None:
    a = _run(_long_mtf())
    b = _run(_long_mtf())
    assert a.decision == b.decision


def test_lookahead_cutoff_consistent() -> None:
    mtf = _long_mtf()
    r = _run(mtf)
    assert r.decision.information_cutoff == mtf.information_cutoff
    assert r.no_trade.information_cutoff == mtf.information_cutoff


# =========================================================================== MarketContext-Pfad


def test_evaluate_from_marketcontext_builds_mtf() -> None:
    from tests.unit.test_mtf import _DAILY_UP, _m5_series
    from trading_agent.core.types import MarketContext

    m5 = _m5_series(_DAILY_UP)
    mc = MarketContext(
        instrument="BTCUSD",
        base_timeframe=M5,
        information_cutoff=m5[-1].close_time,
        series={M5: tuple(m5)},
        spread=0.5,
    )
    r = evaluate(mc)
    assert isinstance(r, EvaluationResult)
    assert r.mtf.instrument == "BTCUSD"
    assert isinstance(r.decision, Decision)  # synthetische Daten → i. d. R. NO_TRADE (Regime)
