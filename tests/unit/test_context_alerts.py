"""``strategy.context_alerts`` — Kontext-Alert-Emitter (Portfolio-Risk / High-Impact-News /
Re-Entry). Nur bei echter Änderung; Auto-Dismiss beim Rückweg in den unkritischen Bereich;
teilt Dedup/Cooldown mit der :class:`AlertEngine`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from trading_agent.analysis.news import assess_news
from trading_agent.core.enums import AssetClass, Direction, NewsImpact
from trading_agent.core.models import NewsEvent
from trading_agent.portfolio_intel import AccountPortfolio, Holding, PortfolioIntelligenceEngine
from trading_agent.portfolio_intel.models import PositionVerdict
from trading_agent.portfolio_intel.reentry import ReEntryAssessment
from trading_agent.strategy.alerts import AlertEngine, AlertEventKind, AlertType
from trading_agent.strategy.context_alerts import ContextAlertEmitter, ContextAlertParams

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)


def _h(inst: str, ac: AssetClass, qty: float, entry: float, mark: float) -> Holding:
    return Holding(
        instrument=inst,
        asset_class=ac,
        account="binance",
        direction=Direction.LONG,
        quantity=qty,
        avg_entry_price=entry,
        mark_price=mark,
    )


def _concentrated_accounts() -> list[AccountPortfolio]:
    # eine einzige, große Position → Konzentration + geringe Streuung → YELLOW/RED
    return [
        AccountPortfolio(
            account="binance",
            as_of=NOW,
            cash=50.0,
            holdings=(
                Holding(
                    instrument="BTCUSDT",
                    asset_class=AssetClass.CRYPTO,
                    account="binance",
                    direction=Direction.LONG,
                    quantity=0.02,
                    avg_entry_price=60_000,
                    mark_price=55_000,
                ),
            ),
        )
    ]


def _report(accounts):
    return PortfolioIntelligenceEngine().assess(accounts, as_of=NOW)


# --------------------------------------------------------------------------- Portfolio


def test_portfolio_risk_alert_only_once_until_change() -> None:
    eng = AlertEngine()
    emitter = ContextAlertEmitter(eng)
    rep = _report(_concentrated_accounts())

    ev1 = emitter.on_portfolio_report(rep, NOW)
    raised = [e for e in ev1 if e.alert.type is AlertType.PORTFOLIO_RISK and e.delivered]
    assert raised, "erwartet PORTFOLIO_RISK bei konzentriertem, notleidendem Portfolio"

    # gleicher Zustand 1 min später → kein neuer Alert (Fingerprint unverändert)
    ev2 = emitter.on_portfolio_report(rep, NOW + timedelta(minutes=1))
    assert not [e for e in ev2 if e.delivered], "kein Spam bei unverändertem Zustand"


def test_portfolio_risk_alert_dismissed_when_health_recovers() -> None:
    eng = AlertEngine()
    emitter = ContextAlertEmitter(eng)

    bad = _report(_concentrated_accounts())
    emitter.on_portfolio_report(bad, NOW)
    assert any(a.type is AlertType.PORTFOLIO_RISK for a in eng.active)

    # gesundes, breit gestreutes Portfolio (GREEN)
    good = _report(
        [
            AccountPortfolio(
                account="binance",
                as_of=NOW,
                cash=3000.0,
                holdings=(
                    _h("BTCUSDT", AssetClass.CRYPTO, 0.03, 50_000, 52_000),
                    _h("AAPL", AssetClass.EQUITY, 10, 200, 210),
                    _h("MSFT", AssetClass.EQUITY, 8, 300, 305),
                    _h("XAUUSDT", AssetClass.GOLD, 1, 3800, 3850),
                ),
            )
        ]
    )
    ev = emitter.on_portfolio_report(good, NOW + timedelta(hours=1))
    assert any(e.kind is AlertEventKind.DISMISSED for e in ev)
    assert not any(a.type is AlertType.PORTFOLIO_RISK for a in eng.active)


# --------------------------------------------------------------------------- News


def _cpi(scheduled: datetime) -> NewsEvent:
    return NewsEvent(
        event_id="cpi-2026-09",
        event_type="US_CPI",
        impact=NewsImpact.HIGH,
        scheduled_time=scheduled,
        available_time=scheduled - timedelta(days=10),
        affected_symbols=[],
    )


def test_high_impact_news_alert_on_approach_and_dismiss_after() -> None:
    eng = AlertEngine()
    emitter = ContextAlertEmitter(eng, params=ContextAlertParams(news_lead_minutes=60.0))
    sched = NOW + timedelta(minutes=45)
    ev = _cpi(sched)

    a = assess_news([ev], cutoff=NOW, asset_class=AssetClass.CRYPTO, instrument="BTCUSDT")
    events = emitter.on_news(a, NOW)
    assert any(e.alert.type is AlertType.HIGH_IMPACT_NEWS and e.delivered for e in events)

    # weit vor dem Termin (kein Vorlauf) → Alert wird geschlossen
    a_far = assess_news(
        [_cpi(NOW + timedelta(hours=6))],
        cutoff=NOW + timedelta(minutes=1),
        asset_class=AssetClass.CRYPTO,
        instrument="BTCUSDT",
    )
    ev2 = emitter.on_news(a_far, NOW + timedelta(minutes=1))
    assert any(e.kind is AlertEventKind.DISMISSED for e in ev2)


def test_news_alert_not_repeated_within_same_bucket() -> None:
    eng = AlertEngine()
    emitter = ContextAlertEmitter(eng)
    a = assess_news(
        [_cpi(NOW + timedelta(minutes=50))],
        cutoff=NOW,
        asset_class=AssetClass.CRYPTO,
        instrument="BTCUSDT",
    )
    emitter.on_news(a, NOW)
    ev2 = emitter.on_news(a, NOW + timedelta(seconds=30))
    assert not [e for e in ev2 if e.delivered]


# --------------------------------------------------------------------------- Re-Entry


def _reentry(readiness: float) -> ReEntryAssessment:
    return ReEntryAssessment(
        instrument="ETHUSDT",
        direction=Direction.LONG,
        verdict=PositionVerdict.RE_ENTRY_WATCH,
        readiness=readiness,
        conditions=(("htf_trend_up", True),),
        trigger="reclaim 2500 + ARMED long",
        reasons=(),
    )


def test_reentry_alert_above_threshold_only() -> None:
    eng = AlertEngine()
    emitter = ContextAlertEmitter(eng, params=ContextAlertParams(reentry_min_readiness=0.6))

    assert not [e for e in emitter.on_reentry([_reentry(0.4)], NOW) if e.delivered]

    ev = emitter.on_reentry([_reentry(0.7)], NOW + timedelta(minutes=5))
    assert any(e.alert.type is AlertType.RE_ENTRY_SETUP and e.delivered for e in ev)

    # Watch verschwindet → Alert schließen
    ev2 = emitter.on_reentry([], NOW + timedelta(minutes=10))
    assert any(e.kind is AlertEventKind.DISMISSED for e in ev2)
