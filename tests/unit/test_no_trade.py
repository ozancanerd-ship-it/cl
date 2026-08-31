"""Phase 3 — globale No-Trade-Checkliste (``strategy/no_trade.py``, ``no-trade.md``).

Kill-Switch · Cooldown · Session · Datenqualität · fehlende/stale Daten · harte Gates ·
Score kann No-Trade nicht überstimmen · mehrere Gründe · deterministisches Replay · Look-ahead · PIT.
"""

from __future__ import annotations

import dataclasses
from datetime import timedelta

import tests.unit.test_confidence as tc
import tests.unit.test_gates as gt
import tests.unit.test_veto as tv
from trading_agent.core.enums import Direction, NoTradeReason, RegimeDirectional, Timeframe
from trading_agent.core.time import parse_timestamp
from trading_agent.core.types import OpenPositionInfo, PortfolioContext
from trading_agent.refdata.seed import seed_sessions
from trading_agent.strategy.no_trade import (
    AccountRisk,
    InstrumentHistory,
    NoTradeGroup,
    NoTradeParams,
    NoTradeReport,
    SystemState,
    assess_no_trade,
    check_no_trade,
)

M5, M15, H4, D1 = Timeframe.M5, Timeframe.M15, Timeframe.H4, Timeframe.D1
_LIQ = (gt._buy_lvl(109.0, 0.6), gt._buy_lvl(112.0, 0.7))
MON = parse_timestamp("2024-06-03T12:00:00Z")  # Montag
SAT = parse_timestamp("2024-06-01T12:00:00Z")  # Samstag


def _ctx():
    mtf, cand = gt._long_setup(m15_extra_liq=_LIQ)
    mtf = tv._with_spread(tv._with_news(mtf, feed_ok=True), 0.02)
    return mtf, cand


def _run(mtf, cand, **kw) -> NoTradeReport:
    kw.setdefault("now", MON)
    return assess_no_trade(mtf, candidate=cand, **kw)


# =========================================================================== Baseline


def test_no_reasons_baseline() -> None:
    mtf, cand = _ctx()
    rep = _run(mtf, cand)
    assert isinstance(rep, NoTradeReport)
    assert rep.blocked is False
    assert rep.reasons == ()
    assert check_no_trade(mtf, candidate=cand, now=MON) == ()
    # Gruppen ohne Eingabe sind protokolliert, blockieren aber nicht
    assert {"risk.account_state", "strategy_state.portfolio", "execution.slippage"} <= set(
        rep.not_checked
    )


# =========================================================================== [1] SYSTEM


def test_kill_switch_global() -> None:
    mtf, cand = _ctx()
    rep = _run(mtf, cand, system=SystemState(kill_switch_global=True))
    assert NoTradeReason.KILL_SWITCH_GLOBAL in rep.reasons
    assert rep.by_group(NoTradeGroup.SYSTEM)[0].reason is NoTradeReason.KILL_SWITCH_GLOBAL


def test_all_kill_switches_and_states() -> None:
    mtf, cand = _ctx()
    s = SystemState(
        kill_switch_broker=True,
        kill_switch_asset=True,
        kill_switch_strategy=True,
        kill_switch_data=True,
        starting_up=True,
        reconciliation_pending=True,
        unhandled_error=True,
    )
    reasons = set(_run(mtf, cand, system=s).reasons)
    assert {
        NoTradeReason.KILL_SWITCH_BROKER,
        NoTradeReason.KILL_SWITCH_ASSET,
        NoTradeReason.KILL_SWITCH_STRATEGY,
        NoTradeReason.KILL_SWITCH_DATA,
        NoTradeReason.SYSTEM_STARTING_UP,
        NoTradeReason.RECONCILIATION_PENDING,
        NoTradeReason.UNHANDLED_ERROR_STATE,
    } <= reasons
    assert _run(mtf, cand, system=s).requires_alert is True  # reconciliation / unhandled_error


def test_setup_version_mismatch() -> None:
    mtf, cand = _ctx()
    rep = _run(mtf, cand, params=NoTradeParams(expected_strategy_version="9.9.9"))
    assert NoTradeReason.SETUP_VERSION_MISMATCH in rep.reasons


# =========================================================================== [2] DATA


def test_data_confidence_floor() -> None:
    mtf, cand = _ctx()
    mtf = dataclasses.replace(mtf, data_confidence=0.3)
    assert NoTradeReason.DATA_CONFIDENCE_FLOOR in _run(mtf, cand).reasons


def test_data_stale() -> None:
    mtf, cand = _ctx()
    tc._patch_dq(mtf, M5, freshness=0.2)
    rep = _run(mtf, cand)
    assert NoTradeReason.DATA_STALE in rep.reasons
    assert rep.by_group(NoTradeGroup.DATA)


def test_data_incomplete_completeness() -> None:
    mtf, cand = _ctx()
    tc._patch_dq(mtf, M15, completeness=0.5)
    assert NoTradeReason.DATA_INCOMPLETE in _run(mtf, cand).reasons


def test_missing_required_timeframe() -> None:
    mtf, cand = _ctx()
    del mtf.per_tf[D1]
    r = _run(mtf, cand)
    assert NoTradeReason.DATA_INCOMPLETE in r.reasons
    assert "D1" in r.by_group(NoTradeGroup.DATA)[0].evidence["missing"]  # type: ignore[operator]


# =========================================================================== [3] REGIME


def test_regime_gate_blocks() -> None:
    mtf, cand = _ctx()
    gate = dataclasses.replace(mtf.htf_regime_gate, ok=False, reason=NoTradeReason.REGIME_UNCLEAR)
    mtf = dataclasses.replace(mtf, htf_regime_gate=gate)
    rep = _run(mtf, cand)
    assert NoTradeReason.REGIME_UNCLEAR in rep.reasons
    assert rep.by_group(NoTradeGroup.REGIME)[0].reason is NoTradeReason.REGIME_UNCLEAR


# =========================================================================== [4] TIME


def test_weekend_without_specs() -> None:
    mtf, cand = _ctx()
    assert NoTradeReason.WEEKEND in _run(mtf, cand, now=SAT).reasons


def test_session_filter_via_specs() -> None:
    mtf, cand = _ctx()
    rep = _run(mtf, cand, now=SAT, session_specs=seed_sessions())
    assert rep.by_group(NoTradeGroup.TIME)
    assert NoTradeReason.WEEKEND in rep.reasons


def test_weekday_no_time_block() -> None:
    mtf, cand = _ctx()
    rep = _run(mtf, cand, now=MON)
    assert not rep.by_group(NoTradeGroup.TIME)
    assert "time.session_calendar" in rep.not_checked


# =========================================================================== [5] NEWS


def test_news_feed_unavailable() -> None:
    mtf, cand = gt._long_setup(m15_extra_liq=_LIQ)  # ohne _with_news
    mtf = tv._with_spread(mtf, 0.02)
    assert NoTradeReason.NEWS_FEED_UNAVAILABLE in _run(mtf, cand).reasons


def test_news_blocking_event() -> None:
    mtf, cand = _ctx()
    mtf = tv._with_news(mtf, feed_ok=True, blocking="FOMC-2024-06")
    assert NoTradeReason.NEWS_BLACKOUT_HIGH in _run(mtf, cand).reasons


def test_news_risk_off() -> None:
    mtf, cand = _ctx()
    mtf = tv._with_news(mtf, feed_ok=True, risk_off=True)
    assert NoTradeReason.NEWS_RISK_OFF_FLAG in _run(mtf, cand).reasons


def test_news_pre_positioning_ban() -> None:
    mtf, cand = _ctx()
    from trading_agent.core.types import NewsContext

    nc = NewsContext(feed_as_of=mtf.information_cutoff, minutes_to_next_high_impact=30.0)
    mtf = dataclasses.replace(mtf, market_context=dataclasses.replace(mtf.market_context, news=nc))
    assert NoTradeReason.NEWS_PRE_POSITIONING_BAN in _run(mtf, cand).reasons


# =========================================================================== [6] RISK


def test_portfolio_max_open_positions() -> None:
    mtf, cand = _ctx()
    pf = PortfolioContext(
        open_positions=tuple(
            OpenPositionInfo(instrument=f"X{i}", direction=Direction.LONG) for i in range(3)
        )
    )
    assert NoTradeReason.MAX_OPEN_POSITIONS in _run(mtf, cand, portfolio=pf).reasons


def test_portfolio_heat() -> None:
    mtf, cand = _ctx()
    pf = PortfolioContext(total_open_risk_pct=5.0)
    assert NoTradeReason.PORTFOLIO_HEAT in _run(mtf, cand, portfolio=pf).reasons


def test_account_daily_loss_limit() -> None:
    mtf, cand = _ctx()
    assert (
        NoTradeReason.DAILY_LOSS_LIMIT
        in _run(mtf, cand, account_risk=AccountRisk(daily_loss_pct=5.0)).reasons
    )


def test_loss_streak_review_requires_alert() -> None:
    mtf, cand = _ctx()
    rep = _run(mtf, cand, instrument_history=InstrumentHistory(consecutive_losses=4))
    assert NoTradeReason.LOSS_STREAK_REVIEW in rep.reasons
    assert rep.requires_alert is True


# =========================================================================== [7] STRATEGY-STATE


def test_duplicate_position() -> None:
    mtf, cand = _ctx()
    pf = PortfolioContext(
        open_positions=(OpenPositionInfo(instrument="BTCUSD", direction=Direction.LONG),)
    )
    assert NoTradeReason.DUPLICATE_POSITION in _run(mtf, cand, portfolio=pf).reasons


def test_opposite_position_open() -> None:
    mtf, cand = _ctx()
    pf = PortfolioContext(
        open_positions=(OpenPositionInfo(instrument="BTCUSD", direction=Direction.SHORT),)
    )
    assert NoTradeReason.OPPOSITE_POSITION_OPEN in _run(mtf, cand, portfolio=pf).reasons


def test_duplicate_armed_setup() -> None:
    mtf, cand = _ctx()
    pf = PortfolioContext(armed_setups={"BTCUSD": Direction.LONG})
    assert NoTradeReason.DUPLICATE_ARMED_SETUP in _run(mtf, cand, portfolio=pf).reasons


def test_cooldown_after_stop() -> None:
    mtf, cand = _ctx()
    h = InstrumentHistory(last_stop_out=MON - timedelta(minutes=30))  # 2 M15-Bars < 12
    assert NoTradeReason.COOLDOWN_AFTER_STOP in _run(mtf, cand, instrument_history=h).reasons


def test_cooldown_after_sweep_fail() -> None:
    mtf, cand = _ctx()
    h = InstrumentHistory(last_sweep_fail=MON - timedelta(minutes=30))
    assert NoTradeReason.COOLDOWN_AFTER_SWEEP_FAIL in _run(mtf, cand, instrument_history=h).reasons


def test_cooldown_expired_no_block() -> None:
    mtf, cand = _ctx()
    h = InstrumentHistory(last_stop_out=MON - timedelta(hours=6))  # > 12 M15-Bars
    assert NoTradeReason.COOLDOWN_AFTER_STOP not in _run(mtf, cand, instrument_history=h).reasons


# =========================================================================== [8] EXECUTION


def test_spread_too_wide() -> None:
    mtf, cand = _ctx()
    mtf = tv._with_spread(mtf, 5.0)
    rep = _run(mtf, cand)
    assert NoTradeReason.SPREAD_TOO_WIDE in rep.reasons
    assert rep.by_group(NoTradeGroup.EXECUTION)[0].reason is NoTradeReason.SPREAD_TOO_WIDE


def test_data_age_execution() -> None:
    mtf, cand = _ctx()
    m5c = mtf.per_tf[M5]
    mtf.per_tf[M5] = dataclasses.replace(m5c, bars=m5c.bars[:-12])
    assert NoTradeReason.DATA_AGE_EXECUTION in _run(mtf, cand).reasons


# =========================================================================== Kombination / Contract


def test_multiple_reasons_all_logged() -> None:
    mtf, cand = _ctx()
    gate = dataclasses.replace(
        mtf.htf_regime_gate, ok=False, reason=NoTradeReason.REGIME_VOL_EXTREME
    )
    mtf = dataclasses.replace(mtf, htf_regime_gate=gate)
    mtf = tv._with_news(mtf, feed_ok=True, risk_off=True)
    rep = _run(mtf, cand, system=SystemState(kill_switch_global=True))
    assert {
        NoTradeReason.KILL_SWITCH_GLOBAL,
        NoTradeReason.REGIME_VOL_EXTREME,
        NoTradeReason.NEWS_RISK_OFF_FLAG,
    } <= set(rep.reasons)


def test_hard_gate_has_no_score_input() -> None:
    # Es gibt bewusst keinen Score-Parameter, der ein No-Trade aufheben könnte.
    import inspect

    params = set(inspect.signature(assess_no_trade).parameters)
    assert "score" not in params and "tier" not in params
    mtf, cand = _ctx()
    rep = _run(mtf, cand, system=SystemState(kill_switch_global=True))
    assert rep.blocked is True


def test_long_short_symmetry_duplicate() -> None:
    lm, lc = _ctx()
    pf_l = PortfolioContext(
        open_positions=(OpenPositionInfo(instrument="BTCUSD", direction=Direction.LONG),)
    )
    sm, sc, _ = tc._short()
    sm = tv._with_spread(tv._with_news(sm), 0.02)
    pf_s = PortfolioContext(
        open_positions=(OpenPositionInfo(instrument="BTCUSD", direction=Direction.SHORT),)
    )
    assert NoTradeReason.DUPLICATE_POSITION in _run(lm, lc, portfolio=pf_l).reasons
    assert NoTradeReason.DUPLICATE_POSITION in _run(sm, sc, portfolio=pf_s).reasons


def test_deterministic_replay() -> None:
    a = _ctx()
    b = _ctx()
    assert assess_no_trade(a[0], candidate=a[1], now=MON) == assess_no_trade(
        b[0], candidate=b[1], now=MON
    )


def test_lookahead_all_records_carry_cutoff() -> None:
    mtf, cand = _ctx()
    rep = _run(mtf, cand, system=SystemState(kill_switch_global=True), now=MON)
    for r in rep.records:
        assert r.information_cutoff == mtf.information_cutoff
        assert r.timestamp <= MON


def test_regime_directional_unclear_via_gate() -> None:
    mtf, cand = _ctx()
    gate = dataclasses.replace(
        mtf.htf_regime_gate,
        ok=False,
        reason=NoTradeReason.REGIME_CONFLICTING,
        merged_directional=RegimeDirectional.CONFLICTING,
    )
    mtf = dataclasses.replace(mtf, htf_regime_gate=gate)
    assert NoTradeReason.REGIME_CONFLICTING in _run(mtf, cand).reasons
