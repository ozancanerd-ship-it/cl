"""Phase 3 · Schritt 7 — Alert Engine (``strategy.alerts``).

18 Event-Typen (inkl. Portfolio-Risk / High-Impact-News / Re-Entry-Setup / Partial-TP) · Anti-Spam (Dedup je Signal+Typ) · Cooldown · Auto-Update / Auto-Dismiss wenn
sich das zugrunde liegende Signal ändert · Gegensatz-Ablösung (strengthen ↔ weaken) ·
Pipeline-Alerts (DATA_STALE / DATA_QUALITY_FAILURE / RISK_LIMIT / BROKER_DISCONNECTED) ·
deterministisch.
"""

from __future__ import annotations

from datetime import timedelta

import tests.unit.test_signal as sg
from trading_agent.core.enums import NoTradeReason, RiskTier
from trading_agent.strategy.alerts import (
    AlertEngine,
    AlertEventKind,
    AlertParams,
    AlertSeverity,
    AlertState,
    AlertType,
)
from trading_agent.strategy.no_trade import NoTradeGroup, NoTradeRecord, NoTradeReport
from trading_agent.strategy.signal import SignalTracker

NOW = sg._BASE.decision.information_cutoff


def _tracker_update(*results, now=NOW):
    """Letztes SignalUpdate aus einer Folge von EvaluationResults."""
    t = SignalTracker()
    upd = None
    for r in results:
        upd = t.ingest(r)
    assert upd is not None
    return upd


def _aplus():
    return sg._res(tier=RiskTier.A_PLUS, score=88.0)


# --------------------------------------------------------------------------- Erstausgabe


def test_buy_alert_on_new_signal() -> None:
    eng = AlertEngine()
    ev = eng.on_signal_update(_tracker_update(sg._BASE), NOW)
    types = {e.alert.type for e in ev if e.delivered}
    assert AlertType.BUY in types


def test_a_plus_setup_alert() -> None:
    eng = AlertEngine()
    ev = eng.on_signal_update(_tracker_update(_aplus()), NOW)
    types = {e.alert.type for e in ev if e.delivered}
    assert AlertType.NEW_A_PLUS_SETUP in types and AlertType.BUY in types
    aplus = next(e.alert for e in ev if e.alert.type is AlertType.NEW_A_PLUS_SETUP)
    assert aplus.severity is AlertSeverity.INFO
    assert aplus.state is AlertState.ACTIVE


# --------------------------------------------------------------------------- Änderungen


def test_weakened_then_strengthened_supersedes() -> None:
    eng = AlertEngine()
    t = SignalTracker()
    t.ingest(sg._BASE)
    w = eng.on_signal_update(t.ingest(sg._score(-10.0)), NOW)
    assert any(
        e.alert.type is AlertType.SIGNAL_WEAKENED and e.kind is AlertEventKind.RAISED for e in w
    )

    s = eng.on_signal_update(t.ingest(sg._score(+6.0)), NOW + timedelta(minutes=30))
    assert any(e.alert.type is AlertType.SIGNAL_STRENGTHENED for e in s)
    # der weakened-Alert ist nicht mehr offen
    open_types = {a.type for a in eng.active}
    assert AlertType.SIGNAL_WEAKENED not in open_types
    assert AlertType.SIGNAL_STRENGTHENED in open_types


def test_dedup_second_weakening_in_cooldown_is_suppressed() -> None:
    eng = AlertEngine()
    t = SignalTracker()
    t.ingest(sg._BASE)
    eng.on_signal_update(t.ingest(sg._score(-10.0)), NOW)
    ev2 = eng.on_signal_update(t.ingest(sg._score(-20.0)), NOW + timedelta(minutes=2))
    weak = [e for e in ev2 if e.alert.type is AlertType.SIGNAL_WEAKENED]
    assert weak and weak[0].kind is AlertEventKind.SUPPRESSED
    assert len([a for a in eng.active if a.type is AlertType.SIGNAL_WEAKENED]) == 1


def test_entry_and_sl_change_alerts() -> None:
    eng = AlertEngine()
    t = SignalTracker()
    t.ingest(sg._BASE)
    ent = sg._BASE.decision.entry + 5
    e1 = eng.on_signal_update(t.ingest(sg._res(entry=ent)), NOW)
    assert any(e.alert.type is AlertType.ENTRY_CHANGED for e in e1)
    e2 = eng.on_signal_update(
        t.ingest(sg._res(entry=ent, sl=sg._BASE.decision.sl - 3)), NOW + timedelta(minutes=1)
    )
    sl = [e for e in e2 if e.alert.type is AlertType.SL_CHANGED]
    assert sl and sl[0].delivered  # SL_CHANGED umgeht Cooldown (always_deliver)


# --------------------------------------------------------------------------- Auto-Dismiss


def test_invalidation_dismisses_open_alerts_and_raises_one() -> None:
    eng = AlertEngine()
    t = SignalTracker()
    eng.on_signal_update(t.ingest(sg._BASE), NOW)  # BUY-Alert offen
    assert eng.active_for(sg._BASE.candidate.setup_id)

    ev = eng.on_signal_update(t.ingest(sg._invalidated()), NOW + timedelta(minutes=5))
    dismissed = [e for e in ev if e.kind is AlertEventKind.DISMISSED]
    raised = [e for e in ev if e.kind is AlertEventKind.RAISED]
    assert dismissed and len(raised) == 1
    assert raised[0].alert.type is AlertType.SETUP_INVALIDATED
    assert raised[0].alert.severity is AlertSeverity.WARNING
    open_now = {a.type for a in eng.active}
    assert open_now == {AlertType.SETUP_INVALIDATED}


def test_exit_required_is_critical_and_clears_others() -> None:
    eng = AlertEngine()
    t = SignalTracker()
    eng.on_signal_update(t.ingest(sg._BASE), NOW)
    ev = eng.on_signal_update(
        t.ingest(sg._BASE, position_state=sg.SignalState.EXIT_REQUIRED), NOW + timedelta(minutes=3)
    )
    raised = [e for e in ev if e.kind is AlertEventKind.RAISED]
    assert raised and raised[0].alert.type is AlertType.EXIT_REQUIRED
    assert raised[0].alert.severity is AlertSeverity.CRITICAL


# --------------------------------------------------------------------------- Pipeline-Alerts


def _report(*records: NoTradeRecord) -> NoTradeReport:
    return NoTradeReport("BTCUSD", NOW, records, ())


def _rec(reason: NoTradeReason, group: NoTradeGroup) -> NoTradeRecord:
    return NoTradeRecord(reason, group, f"{reason.value} detail", {}, NOW, NOW)


def test_data_stale_alert() -> None:
    eng = AlertEngine()
    ev = eng._from_no_trade(_report(_rec(NoTradeReason.DATA_STALE, NoTradeGroup.DATA)), NOW)
    assert ev and ev[0].alert.type is AlertType.DATA_STALE


def test_data_quality_failure_alert() -> None:
    eng = AlertEngine()
    ev = eng._from_no_trade(
        _report(_rec(NoTradeReason.DATA_CONFIDENCE_FLOOR, NoTradeGroup.DATA)), NOW
    )
    assert ev and ev[0].alert.type is AlertType.DATA_QUALITY_FAILURE


def test_risk_limit_alert() -> None:
    eng = AlertEngine()
    ev = eng._from_no_trade(_report(_rec(NoTradeReason.MAX_DRAWDOWN, NoTradeGroup.RISK)), NOW)
    assert ev and ev[0].alert.type is AlertType.RISK_LIMIT
    assert ev[0].alert.severity is AlertSeverity.CRITICAL


def test_broker_disconnected_alert() -> None:
    eng = AlertEngine()
    ev = eng._from_no_trade(
        _report(_rec(NoTradeReason.KILL_SWITCH_GLOBAL, NoTradeGroup.SYSTEM)), NOW
    )
    assert ev and ev[0].alert.type is AlertType.BROKER_DISCONNECTED


def test_no_trade_dedup_same_type_once_per_call() -> None:
    eng = AlertEngine()
    ev = eng._from_no_trade(
        _report(
            _rec(NoTradeReason.DAILY_LOSS_LIMIT, NoTradeGroup.RISK),
            _rec(NoTradeReason.MAX_DRAWDOWN, NoTradeGroup.RISK),
        ),
        NOW,
    )
    assert len([e for e in ev if e.alert.type is AlertType.RISK_LIMIT]) == 1


# --------------------------------------------------------------------------- Integration / Determinismus


def test_on_engine_tick_end_to_end() -> None:
    import tests.unit.test_engine as te

    eng_alerts = AlertEngine()
    ce = te._engine_returning()
    tick = ce.on_market_context(te._mc([te._bar(0, te._ENTRY + 5, te._ENTRY + 2)]))
    ev = eng_alerts.on_engine_tick(tick)
    assert any(e.alert.type is AlertType.BUY for e in ev)


def test_deterministic() -> None:
    def run() -> list[tuple[str, str]]:
        eng = AlertEngine()
        t = SignalTracker()
        t.ingest(sg._BASE)
        out: list[tuple[str, str]] = []
        for r, dt in [
            (sg._score(-10.0), 0),
            (sg._score(+6.0), 30),
            (sg._invalidated(), 40),
        ]:
            for e in eng.on_signal_update(t.ingest(r), NOW + timedelta(minutes=dt)):
                out.append((e.alert.type.value, e.kind.value))
        return out

    assert run() == run()


def test_cooldown_override_param() -> None:
    eng = AlertEngine(
        params=AlertParams(cooldown_overrides={AlertType.SIGNAL_WEAKENED: timedelta(0)})
    )
    t = SignalTracker()
    t.ingest(sg._BASE)
    eng.on_signal_update(t.ingest(sg._score(-10.0)), NOW)
    ev = eng.on_signal_update(t.ingest(sg._score(-20.0)), NOW + timedelta(seconds=1))
    weak = [e for e in ev if e.alert.type is AlertType.SIGNAL_WEAKENED]
    assert weak and weak[0].kind is AlertEventKind.UPDATED  # kein Cooldown → als Update gefaltet


def test_context_alert_portfolio_and_news_dedup() -> None:
    eng = AlertEngine()
    e1 = eng.raise_context_alert(
        AlertType.PORTFOLIO_RISK,
        key="portfolio:concentration",
        title="Klumpenrisiko",
        body="FET 20 %",
        now=NOW,
    )
    assert e1.kind is AlertEventKind.RAISED
    assert e1.alert.severity is AlertSeverity.CRITICAL
    assert e1.alert.dedup_key == "portfolio:concentration:portfolio_risk"
    # gleicher Schlüssel innerhalb Cooldown → als Update gefaltet, nicht neu
    e2 = eng.raise_context_alert(
        AlertType.PORTFOLIO_RISK,
        key="portfolio:concentration",
        title="Klumpenrisiko",
        body="FET 22 %",
        now=NOW + timedelta(minutes=1),
    )
    assert e2.kind is AlertEventKind.UPDATED

    news = eng.raise_context_alert(
        AlertType.HIGH_IMPACT_NEWS,
        key="news:FOMC_RATE:2026-01-28",
        title="FOMC in 2 h",
        body="Blackout",
        now=NOW,
    )
    assert news.kind is AlertEventKind.RAISED
    assert news.alert.type is AlertType.HIGH_IMPACT_NEWS
