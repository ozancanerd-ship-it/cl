"""Phase 3 — Veto-Engine (``strategy/veto.py``, V1–V10 aus ``contradictions.md`` §4/§23).

Jedes V1–V10 einzeln · mehrere gleichzeitig · keine Vetos · Veto trotz hoher Confluence ·
fehlende/stale Daten · News/Portfolio unavailable · Long/Short-Symmetrie · Look-ahead ·
deterministisches Replay · Grenzwerte · Priorität/Severity · vollständige Evidence · Location/RR.
"""

from __future__ import annotations

import dataclasses
from datetime import timedelta

import tests.unit.test_gates as gt
import tests.unit.test_setup_fsm as fsm
from trading_agent.core.enums import (
    Direction,
    RegimeDirectional,
    RegimeVolatility,
    Timeframe,
    VetoId,
)
from trading_agent.core.types import NewsContext, OpenPositionInfo, PortfolioContext
from trading_agent.strategy.confluence import assess_confluence
from trading_agent.strategy.gates import GateParams, evaluate_gates
from trading_agent.strategy.veto import (
    VetoParams,
    VetoRecord,
    VetoReport,
    VetoSeverity,
    VetoSource,
    assess_vetoes,
    collect_vetoes,
)

M15 = Timeframe.M15
M5 = Timeframe.M5
_LIQ = (gt._buy_lvl(109.0, 0.6), gt._buy_lvl(112.0, 0.7))


def _with_news(mtf, *, feed_ok: bool = True, risk_off: bool = False, blocking: str | None = None):
    nc = NewsContext(
        feed_as_of=mtf.information_cutoff if feed_ok else None,
        risk_off=risk_off,
        blocking_event_id=blocking,
    )
    return dataclasses.replace(mtf, market_context=dataclasses.replace(mtf.market_context, news=nc))


def _with_spread(mtf, spread: float | None):
    return dataclasses.replace(
        mtf, market_context=dataclasses.replace(mtf.market_context, spread=spread)
    )


def _base(*, extra_m15: list | None = None):
    """Sauberes ARMED-Long-Setup ohne jeden Veto."""
    m15b = fsm._rows_to_bars(
        [*gt._WARMUP15, gt._SWEEP15, *gt._DISP15, *(extra_m15 or [])], tf=M15, start=fsm.DAY
    )
    mtf, cand = gt._long_setup(m15_bars=m15b, m15_extra_liq=_LIQ)
    mtf = _with_spread(_with_news(mtf, feed_ok=True), 0.02)
    gates = evaluate_gates(mtf, cand)
    return mtf, cand, gates


def _patch_regime(mtf, tf: Timeframe, **kw):
    c = mtf.per_tf[tf]
    mtf.per_tf[tf] = dataclasses.replace(c, regime=dataclasses.replace(c.regime, **kw))


def _append_m15(mtf, o: float, h: float, low: float, c: float):
    """Hängt eine M15-Bar an die M15-TimeframeContext-Serie an (≤ information_cutoff)."""
    m15c = mtf.per_tf[M15]
    t = m15c.bars[-1].open_time + timedelta(minutes=15)
    bar = fsm._bar(M15, t, o, h, low, c)
    assert bar.close_time <= mtf.information_cutoff
    mtf.per_tf[M15] = dataclasses.replace(m15c, bars=(*m15c.bars, bar))


# =========================================================================== keine Vetos


def test_no_vetoes_baseline() -> None:
    mtf, cand, gates = _base()
    rep = assess_vetoes(mtf, cand, gates=gates)
    assert isinstance(rep, VetoReport)
    assert rep.blocking is False
    assert rep.veto_ids == ()
    assert collect_vetoes(mtf, cand, gates=gates) == ()
    assert rep.worst_severity is None


# =========================================================================== V1–V10 einzeln


def test_v1_htf_directional_conflict() -> None:
    mtf, cand, gates = _base()
    _patch_regime(mtf, Timeframe.D1, directional=RegimeDirectional.TREND_DOWN)
    _patch_regime(mtf, Timeframe.H4, directional=RegimeDirectional.TREND_UP)
    rep = assess_vetoes(mtf, cand, gates=gates)
    assert VetoId.V1 in rep.veto_ids
    r = rep.by_id(VetoId.V1)
    assert r is not None and r.source is VetoSource.REGIME
    assert r.evidence["d1_directional"] == "trend_down"


def test_v2_location_blocked() -> None:
    m15b = fsm._rows_to_bars([*gt._WARMUP15, gt._SWEEP15, *gt._DISP15], tf=M15, start=fsm.DAY)
    mtf, cand = gt._long_setup(m15_bars=m15b, m15_extra_liq=_LIQ)
    blocked = dataclasses.replace(cand, entry_fvg=fsm._fvg(lo=107.2, hi=107.8))  # Premium
    mtf = _with_spread(_with_news(mtf), 0.02)
    gates = evaluate_gates(mtf, blocked)
    rep = assess_vetoes(mtf, blocked, gates=gates)
    assert VetoId.V2 in rep.veto_ids
    assert rep.by_id(VetoId.V2).source is VetoSource.LOCATION_GATE
    # RR wurde nicht ausgewertet → als not_available protokolliert, kein V8/V10
    assert VetoId.V8 not in rep.veto_ids and VetoId.V10 not in rep.veto_ids
    assert "v8_rr_gate" in rep.not_available


def test_v3_volatility_extreme() -> None:
    mtf, cand, gates = _base()
    _patch_regime(mtf, Timeframe.H4, volatility=RegimeVolatility.EXTREME)
    rep = assess_vetoes(mtf, cand, gates=gates)
    assert VetoId.V3 in rep.veto_ids
    assert rep.by_id(VetoId.V3).evidence["worst_volatility"] == "extreme"


def test_v3_volatility_low() -> None:
    mtf, cand, gates = _base()
    _patch_regime(mtf, Timeframe.M15, volatility=RegimeVolatility.LOW)
    assert VetoId.V3 in assess_vetoes(mtf, cand, gates=gates).veto_ids


def test_v3_coiled_compression() -> None:
    mtf, cand, gates = _base()
    _patch_regime(mtf, Timeframe.H4, phase=fsm.RegimePhase.COMPRESSION, coiled=True)
    r = assess_vetoes(mtf, cand, gates=gates).by_id(VetoId.V3)
    assert r is not None and r.evidence["coiled_compression"] is True


def test_v3_htf_unclear() -> None:
    mtf, cand, gates = _base()
    _patch_regime(mtf, Timeframe.D1, directional=RegimeDirectional.UNCLEAR)
    assert VetoId.V3 in assess_vetoes(mtf, cand, gates=gates).veto_ids


def test_v4_news_feed_unavailable_failsafe() -> None:
    mtf, cand, gates = _base()
    mtf = _with_news(mtf, feed_ok=False)
    rep = assess_vetoes(mtf, cand, gates=gates)
    r = rep.by_id(VetoId.V4)
    assert r is not None and r.severity is VetoSeverity.CRITICAL
    assert r.evidence["feed_available"] is False
    # abschaltbar (asset-aware Konfiguration)
    off = assess_vetoes(mtf, cand, gates=gates, params=VetoParams(require_news_feed=False))
    assert VetoId.V4 not in off.veto_ids
    assert "v4_news_feed" in off.not_available


def test_v4_news_blocking_event() -> None:
    mtf, cand, gates = _base()
    mtf = _with_news(mtf, feed_ok=True, blocking="FOMC-2024-06")
    r = assess_vetoes(mtf, cand, gates=gates).by_id(VetoId.V4)
    assert r is not None and r.evidence["blocking_event_id"] == "FOMC-2024-06"


def test_v4_risk_off() -> None:
    mtf, cand, gates = _base()
    mtf = _with_news(mtf, feed_ok=True, risk_off=True)
    assert VetoId.V4 in assess_vetoes(mtf, cand, gates=gates).veto_ids


def test_v5_re_sweep() -> None:
    # ARMED-Kandidat, dann eine M15-Bar, die erneut unter das Sweep-Extrem (104.5) schließt
    mtf, cand, gates = _base()
    _append_m15(mtf, 105.6, 105.7, 103.8, 104.0)
    rep = assess_vetoes(mtf, cand, gates=gates)
    r = rep.by_id(VetoId.V5)
    assert r is not None
    assert r.source is VetoSource.SWEEP
    assert r.evidence["re_sweep_close"] == 104.0
    assert r.timestamp <= mtf.information_cutoff


def test_v5_invalidated_candidate() -> None:
    from trading_agent.core.enums import NoTradeReason

    mtf, cand, gates = _base()
    inval = dataclasses.replace(cand, invalidation=NoTradeReason.CANDIDATE_INVALIDATED)
    assert VetoId.V5 in assess_vetoes(mtf, inval, gates=gates).veto_ids


def test_v6_data_confidence_floor() -> None:
    mtf, cand, gates = _base()
    mtf = dataclasses.replace(mtf, data_confidence=0.30)
    r = assess_vetoes(mtf, cand, gates=gates).by_id(VetoId.V6)
    assert r is not None and r.severity is VetoSeverity.CRITICAL
    assert r.evidence["data_confidence"] == 0.3


def test_v7_spread_too_wide() -> None:
    mtf, cand, gates = _base()
    mtf = _with_spread(mtf, 5.0)
    r = assess_vetoes(mtf, cand, gates=gates).by_id(VetoId.V7)
    assert r is not None and r.source is VetoSource.EXECUTION
    assert "Spread" in r.reason


def test_v7_data_age_stale() -> None:
    mtf, cand, gates = _base()
    m5c = mtf.per_tf[M5]
    mtf.per_tf[M5] = dataclasses.replace(m5c, bars=m5c.bars[:-12])  # letzte M5-Bar 60 min alt
    r = assess_vetoes(mtf, cand, gates=gates).by_id(VetoId.V7)
    assert r is not None and "Datenalter" in r.reason


def test_v8_rr_blocked() -> None:
    m15b = fsm._rows_to_bars([*gt._WARMUP15, gt._SWEEP15, *gt._DISP15], tf=M15, start=fsm.DAY)
    mtf, cand = gt._long_setup(m15_bars=m15b, m15_extra_liq=(gt._buy_lvl(105.8, 0.7),))
    mtf = _with_spread(_with_news(mtf), 0.02)
    gates = evaluate_gates(mtf, cand)
    rep = assess_vetoes(mtf, cand, gates=gates)
    assert VetoId.V8 in rep.veto_ids
    assert rep.by_id(VetoId.V8).source is VetoSource.RR_GATE


def test_v10_no_valid_sl() -> None:
    mtf, cand, _ = _base()
    gates = evaluate_gates(mtf, cand, params=GateParams(sl_max_distance_atr=0.3))
    rep = assess_vetoes(mtf, cand, gates=gates)
    assert VetoId.V10 in rep.veto_ids
    assert "sl_too_wide" in str(rep.by_id(VetoId.V10).evidence["reasons"])


def test_v9_portfolio_correlated_exposure() -> None:
    mtf, cand, gates = _base()
    pf = PortfolioContext(
        open_positions=(OpenPositionInfo(instrument="ETHUSD", direction=Direction.LONG),),
        cluster_open_risk_pct=1.5,
        cluster_cap_pct=1.0,
        static_correlations={("BTCUSD", "ETHUSD"): 0.9},
        correlation_threshold=0.70,
    )
    r = assess_vetoes(mtf, cand, gates=gates, portfolio_context=pf).by_id(VetoId.V9)
    assert r is not None and r.severity is VetoSeverity.PORTFOLIO
    assert "ETHUSD=0.9" in str(r.evidence["correlated_same_direction"])


def test_v9_portfolio_unavailable_passthrough() -> None:
    mtf, cand, gates = _base()
    rep = assess_vetoes(mtf, cand, gates=gates, portfolio_context=None)
    assert VetoId.V9 not in rep.veto_ids
    assert "v9_portfolio_context" in rep.not_available


# =========================================================================== Kombination


def test_multiple_vetoes_sorted_by_priority() -> None:
    mtf, cand, gates = _base()
    mtf = _with_news(mtf, feed_ok=False)  # V4
    _patch_regime(mtf, Timeframe.H4, volatility=RegimeVolatility.EXTREME)  # V3
    mtf = dataclasses.replace(mtf, data_confidence=0.2)  # V6
    rep = assess_vetoes(mtf, cand, gates=gates)
    assert set(rep.veto_ids) >= {VetoId.V3, VetoId.V4, VetoId.V6}
    # V6 (prio 0) vor V4 (prio 2) vor V3 (prio 4)
    order = [r.veto_id for r in rep.records]
    assert order.index(VetoId.V6) < order.index(VetoId.V4) < order.index(VetoId.V3)
    assert rep.worst_severity is VetoSeverity.CRITICAL


def test_veto_despite_high_confluence() -> None:
    mtf, cand, gates = _base()
    conf = assess_confluence(mtf, cand, gates=gates)
    assert conf.support_score > 0.5  # gute Confluence …
    _patch_regime(mtf, Timeframe.H4, volatility=RegimeVolatility.EXTREME)
    ids = collect_vetoes(mtf, cand, confluence=conf, gates=gates)
    assert VetoId.V3 in ids  # … wird trotzdem hart geblockt


def test_correlated_vetoes_linked() -> None:
    mtf, cand, gates = _base()
    mtf = dataclasses.replace(mtf, data_confidence=0.2)  # V6
    m5c = mtf.per_tf[M5]
    mtf.per_tf[M5] = dataclasses.replace(m5c, bars=m5c.bars[:-12])  # V7 (data age)
    rep = assess_vetoes(mtf, cand, gates=gates)
    v6, v7 = rep.by_id(VetoId.V6), rep.by_id(VetoId.V7)
    assert v6 is not None and v7 is not None
    assert VetoId.V7 in v6.correlated_with and VetoId.V6 in v7.correlated_with


# =========================================================================== fehlende Daten


def test_missing_external_data_is_not_a_veto() -> None:
    mtf, cand, gates = _base()  # derivatives / cross_asset leer
    rep = assess_vetoes(mtf, cand, gates=gates)
    assert not rep.blocking
    assert "v7_slippage_estimate" in rep.not_available
    assert "v7_orderbook_depth" in rep.not_available


def test_spread_unavailable_is_not_a_veto() -> None:
    mtf, cand, gates = _base()
    mtf = _with_spread(mtf, None)
    rep = assess_vetoes(mtf, cand, gates=gates)
    assert VetoId.V7 not in rep.veto_ids
    assert "v7_spread" in rep.not_available


def test_gates_absent_marks_gate_vetoes_unavailable() -> None:
    mtf, cand, _ = _base()
    rep = assess_vetoes(mtf, cand, gates=None)
    assert {"v2_location_gate", "v8_rr_gate", "v10_sl_geometry"} <= set(rep.not_available)


# =========================================================================== Grenzwerte


def test_v6_boundary() -> None:
    mtf, cand, gates = _base()
    at_floor = dataclasses.replace(mtf, data_confidence=0.50)
    below = dataclasses.replace(mtf, data_confidence=0.4999)
    assert VetoId.V6 not in assess_vetoes(at_floor, cand, gates=gates).veto_ids
    assert VetoId.V6 in assess_vetoes(below, cand, gates=gates).veto_ids


def test_v7_spread_boundary() -> None:
    mtf, cand, gates = _base()
    atr_e = mtf.tf(M5).atr
    limit = 0.10 * atr_e
    pp = VetoParams(max_spread_pct=1.0)  # nur die absolute Schwelle prüfen
    assert (
        VetoId.V7
        not in assess_vetoes(_with_spread(mtf, limit * 0.99), cand, gates=gates, params=pp).veto_ids
    )
    assert (
        VetoId.V7
        in assess_vetoes(_with_spread(mtf, limit * 1.5), cand, gates=gates, params=pp).veto_ids
    )


# =========================================================================== Symmetrie

_MIRROR = 200.0


def _short(*, extra_m15: list | None = None):
    zone = fsm._fvg(direction=fsm.Polarity.BEARISH, lo=_MIRROR - 105.5, hi=_MIRROR - 104.9)
    rows = gt._mirror([*gt._WARMUP15, gt._SWEEP15, *gt._DISP15]) + gt._mirror(extra_m15 or [])
    mtf = fsm._mtf(
        m15_bars=fsm._rows_to_bars(rows, tf=M15, start=fsm.DAY),
        m15_liquidity=(
            fsm._level(
                price=95.0, side=fsm.MarketSide.BUY_SIDE, ltype=fsm.LiquidityType.EQUAL_HIGHS
            ),
            gt._sell_lvl(_MIRROR - 109.0, 0.6),
            gt._sell_lvl(_MIRROR - 112.0, 0.7),
        ),
        m15_displacements=(fsm._disp(direction=fsm.Polarity.BEARISH),),
        m5_breaks=(fsm._brk(direction=fsm.Polarity.BEARISH),),
        m5_fvgs=(zone,),
        htf_directional=RegimeDirectional.TREND_DOWN,
        htf_bias=fsm.Bias.SHORT,
        m5_bars=fsm._m5_filler(base=94.5),
    )
    mtf = _with_spread(_with_news(mtf), 0.02)
    from trading_agent.strategy.setup_detection import detect_setups

    cand = detect_setups(mtf).primary
    assert cand is not None and cand.is_armed
    return mtf, cand, evaluate_gates(mtf, cand)


def test_long_short_symmetry_v3() -> None:
    lm, lc, lg = _base()
    sm, sc, sg = _short()
    _patch_regime(lm, Timeframe.H4, volatility=RegimeVolatility.EXTREME)
    _patch_regime(sm, Timeframe.H4, volatility=RegimeVolatility.EXTREME)
    assert VetoId.V3 in collect_vetoes(lm, lc, gates=lg)
    assert VetoId.V3 in collect_vetoes(sm, sc, gates=sg)


def test_long_short_symmetry_v5() -> None:
    lm, lc, lg = _base()
    _append_m15(lm, 105.6, 105.7, 103.8, 104.0)  # close < Sweep-Extrem 104.5
    sm, sc, sg = _short()
    _append_m15_short(sm, 105.6, 105.7, 103.8, 104.0)  # gespiegelt: close > Extrem
    assert VetoId.V5 in collect_vetoes(lm, lc, gates=lg)
    assert VetoId.V5 in collect_vetoes(sm, sc, gates=sg)


def _append_m15_short(mtf, o: float, h: float, low: float, c: float):
    om, hm, lm_, cm = gt._mirror([(o, h, low, c)])[0]
    _append_m15(mtf, om, hm, lm_, cm)


# =========================================================================== Look-ahead / Replay


def test_lookahead_re_sweep_after_cutoff_ignored() -> None:
    # Re-Sweep-Bar liegt NACH dem cutoff → wird durch die MtfContext-Kürzung entfernt
    mtf, cand = gt._long_setup(
        m15_bars=fsm._rows_to_bars(
            [*gt._WARMUP15, gt._SWEEP15, *gt._DISP15], tf=M15, start=fsm.DAY
        ),
        m15_extra_liq=_LIQ,
    )
    mtf = _with_spread(_with_news(mtf), 0.02)
    gates = evaluate_gates(mtf, cand)
    rep = assess_vetoes(mtf, cand, gates=gates)
    assert VetoId.V5 not in rep.veto_ids
    for r in rep.records:
        assert r.information_cutoff == mtf.information_cutoff
        assert r.timestamp <= mtf.information_cutoff


def test_deterministic_replay() -> None:
    m1, c1, g1 = _base()
    m2, c2, g2 = _base()
    assert assess_vetoes(m1, c1, gates=g1) == assess_vetoes(m2, c2, gates=g2)


# =========================================================================== Evidence / Contract


def test_every_record_has_complete_evidence() -> None:
    mtf, cand, gates = _base()
    mtf = _with_news(mtf, feed_ok=False)
    _patch_regime(mtf, Timeframe.H4, volatility=RegimeVolatility.EXTREME)
    rep = assess_vetoes(mtf, cand, gates=gates)
    assert rep.records
    for r in rep.records:
        assert isinstance(r, VetoRecord)
        assert r.veto_id and r.reason and r.severity in VetoSeverity
        assert r.source in VetoSource
        assert r.blocking is True
        assert isinstance(r.evidence, dict) and len(r.evidence) >= 1
        assert r.information_cutoff == mtf.information_cutoff
        assert r.timestamp <= mtf.information_cutoff
        assert r.priority == r.priority  # stabil
