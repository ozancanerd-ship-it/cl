"""``runtime.context_alert_bridge`` — EventBus → Kontext-Alerts (News + Re-Entry).

Position CLOSED mit intakter These → ReEntryWatch; folgende Decision im HTF-Trend → RE_ENTRY_SETUP
auf den Bus. Position CLOSED mit gebrochener These → keine Watch. Kalender-Event im Vorlauf →
HIGH_IMPACT_NEWS.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from trading_agent.core.enums import AssetClass, Direction, NewsImpact
from trading_agent.core.models import NewsEvent
from trading_agent.runtime.bus import EventBus
from trading_agent.runtime.context_alert_bridge import ContextAlertBridge
from trading_agent.runtime.events import AlertRaised, DecisionMade, PaperPositionChanged
from trading_agent.strategy.decision import DecisionType
from trading_agent.strategy.position import ExitReason, PaperPosition, PositionLeg, PositionState

T0 = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)


class _E:
    def __init__(self, **kw: object) -> None:
        self.__dict__.update(kw)


def _closed_position(reason: ExitReason, *, entry: float = 2000.0) -> PaperPosition:
    return PaperPosition(
        position_id="p1",
        signal_id="s1",
        instrument="XAUUSDT",
        direction=Direction.LONG,
        opened_at=T0 - timedelta(days=2),
        information_cutoff=T0,
        entry=entry,
        initial_sl=entry - 20,
        tp1=entry + 30,
        tp2=entry + 60,
        tp3_ref="swing",
        state=PositionState.CLOSED,
        effective_sl=entry,
        open_fraction=0.0,
        realized_r=1.2,
        legs=(PositionLeg(fraction=1.0, price=entry + 24, r_multiple=1.2, reason=reason, at=T0),),
        bars_pending=0,
        bars_held=18,
        mfe_r=1.6,
        mae_r=-0.3,
        last_price=entry + 24,
        tp1_done=True,
        tp2_done=False,
        tp3_done=False,
        sl_at_be=True,
        entry_ts=T0 - timedelta(days=2),
        closed_at=T0,
        close_reason=reason,
    )


def _decision(*, htf: str, price: float, ts: datetime) -> DecisionMade:
    result = _E(
        decision=_E(
            decision=DecisionType.NO_TRADE,
            setup_state=_E(value="scanning"),
            direction=None,
        ),
        mtf=_E(m5=_E(last_close=price), htf_directional=_E(value=htf)),
    )
    return DecisionMade(
        ts=ts,
        instrument="XAUUSDT",
        decision_type="no_trade",
        setup_state="scanning",
        result=result,
    )


async def _collect(bus: EventBus) -> list[AlertRaised]:
    got: list[AlertRaised] = []
    bus.subscribe(AlertRaised, lambda e: got.append(e))
    return got


async def test_reentry_watch_registered_and_alerted_when_trend_returns() -> None:
    bus = EventBus(raise_on_handler_error=True)
    bridge = ContextAlertBridge(("XAUUSDT",), AssetClass.GOLD)
    bridge.attach(bus)
    got = await _collect(bus)

    await bus.publish(
        PaperPositionChanged(
            ts=T0,
            instrument="XAUUSDT",
            change="CLOSED",
            position=_closed_position(ExitReason.TRAIL_STOP),
        )
    )
    assert bridge.counts["watches_registered"] == 1
    assert bridge.active_watches == 1

    # HTF wieder trend_up, Preis über dem Reclaim-Level → RE_ENTRY_SETUP
    await bus.publish(_decision(htf="trend_up", price=2010.0, ts=T0 + timedelta(hours=4)))
    types = {e.alert_type for e in got}
    assert "re_entry_setup" in types
    assert bridge.counts["reentry_alerts"] >= 1


async def test_broken_thesis_registers_no_watch() -> None:
    bus = EventBus(raise_on_handler_error=True)
    bridge = ContextAlertBridge(("XAUUSDT",), AssetClass.GOLD)
    bridge.attach(bus)

    await bus.publish(
        PaperPositionChanged(
            ts=T0,
            instrument="XAUUSDT",
            change="CLOSED",
            position=_closed_position(ExitReason.STRUCTURE_INVALIDATION),
        )
    )
    assert bridge.active_watches == 0
    assert bridge.counts["watches_registered"] == 0


async def test_no_reentry_alert_while_trend_not_back() -> None:
    bus = EventBus(raise_on_handler_error=True)
    bridge = ContextAlertBridge(("XAUUSDT",), AssetClass.GOLD)
    bridge.attach(bus)
    got = await _collect(bus)

    await bus.publish(
        PaperPositionChanged(
            ts=T0,
            instrument="XAUUSDT",
            change="CLOSED",
            position=_closed_position(ExitReason.TRAIL_STOP),
        )
    )
    await bus.publish(_decision(htf="unclear", price=1980.0, ts=T0 + timedelta(hours=4)))
    assert not [e for e in got if e.alert_type == "re_entry_setup"]


async def test_high_impact_news_alert_from_calendar() -> None:
    bus = EventBus(raise_on_handler_error=True)
    cpi = NewsEvent(
        event_id="cpi-2026-09",
        event_type="US_CPI",
        impact=NewsImpact.HIGH,
        scheduled_time=T0 + timedelta(minutes=40),
        available_time=T0 - timedelta(days=10),
        affected_symbols=[],
    )
    bridge = ContextAlertBridge(("XAUUSDT",), AssetClass.GOLD, calendar_events=[cpi])
    bridge.attach(bus)
    got = await _collect(bus)

    await bus.publish(_decision(htf="unclear", price=2000.0, ts=T0))
    assert any(e.alert_type == "high_impact_news" for e in got)
    assert bridge.counts["news_alerts"] >= 1
