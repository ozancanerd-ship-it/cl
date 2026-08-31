"""Phase 3 · Schritt 10 — Paper-Live-Architektur (``strategy.paper_live``).

Vollständiger Datenfluss in einem Schritt: MarketContext → Decision → Signal → Alerts →
Paper-Position → Re-Evaluation. Keine Echtgeld-Orders, kein Broker. Historie erklärbar.
"""

from __future__ import annotations

import tests.unit.test_engine as te
from trading_agent.core.enums import DecisionType
from trading_agent.strategy.alerts import AlertType
from trading_agent.strategy.paper_live import PaperLiveRunner, PaperLiveStep


def _runner() -> PaperLiveRunner:
    r = PaperLiveRunner()
    # dieselbe DI wie in test_engine: vorbereitete Pipeline
    r.engine._evaluate = lambda mc, **_k: te._result_at(mc)
    return r


def test_feed_produces_step_with_signal_and_alert() -> None:
    r = _runner()
    step = r.feed(te._mc([te._bar(0, te._ENTRY + 5, te._ENTRY + 2)]))
    assert isinstance(step, PaperLiveStep)
    assert step.tick.decision is DecisionType.BUY
    assert step.tick.opened is not None  # Paper-Position (pending)
    assert any(a.alert.type is AlertType.BUY for a in step.delivered_alerts)
    assert r.history == (step,)


def test_full_flow_fill_and_tp1() -> None:
    r = _runner()
    r.feed(te._mc([te._bar(0, te._ENTRY + 5, te._ENTRY + 2)]))
    r.feed(
        te._mc([te._bar(0, te._ENTRY + 5, te._ENTRY + 2), te._bar(1, te._ENTRY + 1, te._ENTRY - 1)])
    )
    step3 = r.feed(
        te._mc(
            [
                te._bar(0, te._ENTRY + 5, te._ENTRY + 2),
                te._bar(1, te._ENTRY + 1, te._ENTRY - 1),
                te._bar(2, te._TP1 + 1, te._ENTRY + 1),
            ]
        )
    )
    assert step3.tick.position is not None and step3.tick.position.position.tp1_done
    assert len(r.history) == 3


def test_no_broker_surface() -> None:
    r = _runner()
    # der Runner hat keine Order-/Broker-Methoden
    assert not any(
        hasattr(r, name) for name in ("submit_order", "place_order", "connect_broker", "route")
    )
