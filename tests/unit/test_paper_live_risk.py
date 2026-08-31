"""Phase 4 · Risk/Portfolio-Integration in ``PaperLiveRunner``.

Der Risk-Gate sitzt VOR dem Auto-Open und kann einen Entry **nur verhindern** — nie erzeugen.
Der PortfolioLedger wird aus den EngineTick-Events gefüttert (open/close/armed).
"""

from __future__ import annotations

from datetime import timedelta

import tests.unit.test_engine as te
from trading_agent.core.enums import DecisionType, Direction, RiskTier
from trading_agent.portfolio.engine import PortfolioLedger
from trading_agent.risk.limits import RiskLimits
from trading_agent.risk.risk_engine import RiskEngine
from trading_agent.safety.kill_switch import KillSwitch
from trading_agent.strategy.paper_live import PaperLiveRunner


def _runner(*, decision_overrides: dict | None = None, **kw) -> PaperLiveRunner:
    ov = decision_overrides or {}
    r = PaperLiveRunner(**kw)
    r.engine._evaluate = lambda mc, **_k: te._result_at(mc, **ov)  # scripted BUY
    return r


def test_risk_gate_blocks_entry_on_kill_switch(tmp_path) -> None:
    ks = KillSwitch(tmp_path / "ks.json")
    ks.trip("global", reason="test")
    r = _runner(
        risk_engine=RiskEngine(),
        portfolio_ledger=PortfolioLedger(starting_equity=10_000.0),
        kill_switch=ks,
    )
    step = r.feed(te._mc([te._bar(0, te._ENTRY + 1, te._ENTRY - 1)]))
    assert step.tick.decision is DecisionType.BUY
    assert step.tick.opened is None  # Risk Engine hat den Entry verhindert
    assert step.tick.risk_blocked is True
    assert r.engine.open_positions == ()


def test_risk_gate_allows_and_ledger_records(tmp_path) -> None:
    lg = PortfolioLedger(starting_equity=10_000.0)
    r = _runner(risk_engine=RiskEngine(), portfolio_ledger=lg)
    # Tick 1: pending eröffnet (Risk Engine approved)
    r.feed(te._mc([te._bar(0, te._ENTRY + 5, te._ENTRY + 2)]))
    assert not r.history[-1].tick.risk_blocked
    assert len(r.engine.open_positions) == 1
    # Tick 2: Bar füllt → Ledger sieht die offene Position
    r.feed(
        te._mc([te._bar(0, te._ENTRY + 5, te._ENTRY + 2), te._bar(1, te._ENTRY + 1, te._ENTRY - 1)])
    )
    assert lg.total_open_risk_pct > 0.0
    # Tick 3: Bar reißt den Stop → Ledger bucht den Verlust
    r.feed(
        te._mc(
            [
                te._bar(0, te._ENTRY + 5, te._ENTRY + 2),
                te._bar(1, te._ENTRY + 1, te._ENTRY - 1),
                te._bar(2, te._ENTRY, te._SL - 1),
            ]
        )
    )
    acc = lg.to_account_state()
    assert acc.equity < 10_000.0  # Verlust gebucht
    assert acc.consecutive_losses == 1
    assert lg.total_open_risk_pct == 0.0  # Position geschlossen


def test_risk_gate_blocks_on_daily_loss(tmp_path) -> None:
    lg = PortfolioLedger(starting_equity=10_000.0)
    lg.roll_time(te.START)
    # Tages-Verlust künstlich über das Limit bringen
    lg.on_open("BTCUSD", Direction.LONG, risk_amount=100.0, entry_ts=te.START)
    lg.on_close("BTCUSD", realized_pnl=-400.0)  # -4 % > max_daily_loss 3 %
    r = _runner(risk_engine=RiskEngine(RiskLimits(max_daily_loss_pct=3.0)), portfolio_ledger=lg)
    step = r.feed(te._mc([te._bar(0, te._ENTRY + 1, te._ENTRY - 1)]))
    assert step.tick.risk_blocked is True
    assert "daily_loss_limit" in step.tick.risk.reasons  # type: ignore[union-attr]


def test_risk_gate_blocks_on_max_drawdown(tmp_path) -> None:
    """Peak-to-Trough-Drawdown über dem Limit blockt — auch wenn der Tagesverlust
    (neuer Handelstag) schon zurückgesetzt ist."""
    d1 = te.START
    lg = PortfolioLedger(starting_equity=10_000.0)
    lg.roll_time(d1)
    lg.on_open("BTCUSD", Direction.LONG, risk_amount=100.0, entry_ts=d1)
    lg.on_close("BTCUSD", realized_pnl=-900.0)  # equity 9100, peak 10000 → DD 9 %
    lg.roll_time(d1 + timedelta(days=1))  # Tagesverlust resettet, DD bleibt
    r = _runner(
        risk_engine=RiskEngine(
            RiskLimits(
                max_drawdown_pct=5.0,
                max_daily_loss_pct=99.0,
                max_weekly_loss_pct=99.0,
                loss_streak_halt=99,
            )
        ),
        portfolio_ledger=lg,
    )
    step = r.feed(te._mc([te._bar(0, te._ENTRY + 1, te._ENTRY - 1)]))
    assert step.tick.risk_blocked is True
    assert "max_drawdown" in step.tick.risk.reasons  # type: ignore[union-attr]
    assert r.engine.open_positions == ()


def test_risk_gate_blocks_on_loss_streak(tmp_path) -> None:
    lg = PortfolioLedger(starting_equity=10_000.0)
    lg.roll_time(te.START)
    for _ in range(4):
        lg.on_open("BTCUSD", Direction.LONG, risk_amount=50.0, entry_ts=te.START)
        lg.on_close("BTCUSD", realized_pnl=-50.0)  # 4 Verluste in Folge, nur -2 % gesamt
    r = _runner(
        risk_engine=RiskEngine(RiskLimits(loss_streak_halt=4, max_daily_loss_pct=99.0)),
        portfolio_ledger=lg,
    )
    step = r.feed(te._mc([te._bar(0, te._ENTRY + 1, te._ENTRY - 1)]))
    assert step.tick.risk_blocked is True
    assert "loss_streak_halt" in step.tick.risk.reasons  # type: ignore[union-attr]


def test_risk_gate_blocks_on_max_open_positions(tmp_path) -> None:
    lg = PortfolioLedger(starting_equity=100_000.0)
    lg.roll_time(te.START)
    for inst in ("AAA", "BBB", "CCC"):  # max_open_positions default 3
        lg.on_open(inst, Direction.LONG, risk_amount=100.0, entry_ts=te.START)
    r = _runner(risk_engine=RiskEngine(), portfolio_ledger=lg)
    step = r.feed(te._mc([te._bar(0, te._ENTRY + 1, te._ENTRY - 1)]))
    assert step.tick.risk_blocked is True
    assert "max_open_positions" in step.tick.risk.reasons  # type: ignore[union-attr]


def test_score_and_confidence_cannot_override_hard_limit(tmp_path) -> None:
    """Projekt-Constraint: ein maximaler Score / A+-Tier hebt ein Risk-Limit NICHT auf."""
    lg = PortfolioLedger(starting_equity=10_000.0)
    lg.roll_time(te.START)
    lg.on_open("BTCUSD", Direction.LONG, risk_amount=100.0, entry_ts=te.START)
    lg.on_close("BTCUSD", realized_pnl=-400.0)  # -4 % > max_daily_loss 3 %
    r = _runner(
        decision_overrides={"score": 100.0, "tier": RiskTier.A_PLUS},
        risk_engine=RiskEngine(RiskLimits(max_daily_loss_pct=3.0)),
        portfolio_ledger=lg,
    )
    step = r.feed(te._mc([te._bar(0, te._ENTRY + 1, te._ENTRY - 1)]))
    assert step.tick.decision is DecisionType.BUY
    assert step.tick.result.decision.score == 100.0  # Score ist hoch …
    assert step.tick.risk_blocked is True  # … und wird trotzdem geblockt
    assert "daily_loss_limit" in step.tick.risk.reasons  # type: ignore[union-attr]
    assert r.engine.open_positions == ()


def test_no_risk_engine_unchanged() -> None:
    r = _runner()  # kein risk_engine / ledger
    step = r.feed(te._mc([te._bar(0, te._ENTRY + 5, te._ENTRY + 2)]))
    assert step.tick.opened is not None and step.tick.risk is None
    assert step.tick.risk_blocked is False
