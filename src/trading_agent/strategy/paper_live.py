"""Paper-Live-Architektur (Schritt 10) — **keine Echtgeld-Orders, kein Broker-Routing.**

Verdrahtet den vollständigen Datenfluss zu einem einzigen Schritt:

```
LIVE MARKET DATA → MarketContext → evaluate() → Decision
      → DynamicSignal (Revision) → Alerts → PaperPosition → Re-Evaluation
```

Der ``PaperLiveRunner`` ist bewusst dünn: er hält **eine** ``ContinuousEvaluator``- und **eine**
``AlertEngine``-Instanz pro Instrument und reicht jeden ``MarketContext`` durch. Was er
**nicht** tut: Orders senden, Broker anbinden, Keys lesen. Der Übergang zu echten Live-Daten
später ändert nur die **Quelle** der ``MarketContext``-Objekte — die Engine bleibt identisch
(gleiche Pipeline für Backtest / Paper / Demo / Live).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Sequence
from datetime import datetime

from trading_agent.core.types import MarketContext, PortfolioContext
from trading_agent.engine.parity import ParityReport, compare_decisions
from trading_agent.portfolio.engine import PortfolioLedger
from trading_agent.refdata.models import SessionSpec
from trading_agent.risk.risk_engine import RiskEngine
from trading_agent.safety.kill_switch import KillSwitch
from trading_agent.strategy.alerts import AlertEngine, AlertEvent, AlertParams
from trading_agent.strategy.decision import Decision
from trading_agent.strategy.engine import ContinuousEvaluator, EngineParams, EngineTick
from trading_agent.strategy.evaluate import EvaluationResult
from trading_agent.strategy.m1_feed import M1Source
from trading_agent.strategy.no_trade import AccountRisk, InstrumentHistory, SystemState
from trading_agent.strategy.position import PaperPosition, PositionEvent


@dataclasses.dataclass(frozen=True, slots=True)
class PaperLiveStep:
    """Ergebnis eines einzelnen Fortschritts — vollständig erklärbar für die UI."""

    at: datetime
    tick: EngineTick
    alerts: tuple[AlertEvent, ...]

    @property
    def decision(self) -> object:
        return self.tick.result.decision

    @property
    def delivered_alerts(self) -> tuple[AlertEvent, ...]:
        return tuple(a for a in self.alerts if a.delivered)


class PaperLiveRunner:
    """Ein Instrument. Stateful. ``feed()`` pro neuem ``MarketContext``."""

    def __init__(
        self,
        *,
        engine_params: EngineParams | None = None,
        alert_params: AlertParams | None = None,
        m1_source: M1Source | None = None,
        evaluate_fn: Callable[..., EvaluationResult] | None = None,
        risk_engine: RiskEngine | None = None,
        portfolio_ledger: PortfolioLedger | None = None,
        kill_switch: KillSwitch | None = None,
    ) -> None:
        self.risk_engine = risk_engine
        self.ledger = portfolio_ledger
        self._kill_switch = kill_switch
        risk_gate = self._make_risk_gate() if risk_engine is not None else None
        self.engine = ContinuousEvaluator(
            params=engine_params,
            m1_source=m1_source,
            evaluate_fn=evaluate_fn,
            risk_gate=risk_gate,
        )
        self.alerts = AlertEngine(params=alert_params)
        self._history: list[PaperLiveStep] = []
        # Vollständige Entscheidungs-Spur (cutoff, instrument, Decision) — erlaubt einen
        # Parity-Vergleich zweier Läufe (z. B. Backtest-Replay ↔ neu-eingespielte Live-Folge).
        self._decision_trace: list[tuple[datetime, str, Decision]] = []

    def _make_risk_gate(self) -> Callable[[Decision], object]:
        def gate(decision: Decision) -> object:
            assert self.risk_engine is not None
            account = self.ledger.to_account_state() if self.ledger is not None else None
            portfolio = (
                self.ledger.to_portfolio_context(next_instrument=decision.instrument)
                if self.ledger is not None
                else None
            )
            ks = self._kill_switch.state if self._kill_switch is not None else None
            return self.risk_engine.review(
                decision, account=account, portfolio=portfolio, kill_switch=ks
            )

        return gate

    @property
    def history(self) -> tuple[PaperLiveStep, ...]:
        return tuple(self._history)

    @property
    def decision_trace(self) -> tuple[tuple[datetime, str, Decision], ...]:
        """(cutoff, instrument, Decision) je ``feed()`` — Eingabe für ``compare_decisions``."""
        return tuple(self._decision_trace)

    def parity_against(self, reference: Sequence[tuple[datetime, str, Decision]]) -> ParityReport:
        """Vergleicht die eigene Entscheidungs-Spur gegen eine Referenz-Spur (gleiche
        ``(cutoff, instrument)``-Schlüssel). ``report.ok`` ⇒ identische Entscheidungen bei
        identischen Eingaben."""
        return compare_decisions(list(self._decision_trace), list(reference))

    @property
    def open_positions(self) -> object:
        return self.engine.open_positions

    @property
    def active_alerts(self) -> object:
        return self.alerts.active

    @property
    def live_signals(self) -> object:
        return self.engine.signals.alive

    def feed(
        self,
        mc: MarketContext,
        *,
        portfolio_context: PortfolioContext | None = None,
        m1_bars: Sequence[object] = (),
        session_specs: Sequence[SessionSpec] = (),
        system: SystemState | None = None,
        instrument_history: InstrumentHistory | None = None,
        account_risk: AccountRisk | None = None,
    ) -> PaperLiveStep:
        if self.ledger is not None:
            self.ledger.roll_time(mc.information_cutoff)
        tick = self.engine.on_market_context(
            mc,
            portfolio_context=portfolio_context,
            m1_bars=m1_bars,
            session_specs=session_specs,
            system=system,
            instrument_history=instrument_history,
            account_risk=account_risk,
        )
        self._update_ledger(tick)
        self._decision_trace.append((mc.information_cutoff, mc.instrument, tick.result.decision))
        alert_events = self.alerts.on_engine_tick(tick)
        step = PaperLiveStep(at=mc.information_cutoff, tick=tick, alerts=alert_events)
        self._history.append(step)
        return step

    def _update_ledger(self, tick: EngineTick) -> None:
        if self.ledger is None:
            return
        # Eine Position zählt für das Ledger erst ab dem **Fill** (vorher ist sie nur ein
        # pending Limit). `tick.opened` (pending) direkt gefüllt → auch erfassen.
        pu = tick.position
        filled_now = pu is not None and pu.event is PositionEvent.FILLED
        if filled_now and pu is not None:
            self._record_open(pu.position)
        elif tick.opened is not None and tick.opened.entry_ts is not None:
            self._record_open(tick.opened)
        # ARMED-Setup vormerken (für Duplikat-/Gegenpositions-Check des nächsten Ticks)
        cand = tick.result.candidate
        if cand is not None and cand.is_armed and cand.direction is not None:
            self.ledger.set_armed(cand.instrument, cand.direction)
        if tick.closed is not None:
            eq = self.ledger.equity
            risk_amt = eq * (self._risk_pct_of(tick.closed))
            self.ledger.on_close(
                tick.closed.instrument,
                realized_pnl=tick.closed.realized_r * risk_amt,
                is_scratch=abs(tick.closed.realized_r) < 0.1,
            )

    def _record_open(self, pos: PaperPosition) -> None:
        assert self.ledger is not None and pos.entry_ts is not None
        eq = self.ledger.equity
        self.ledger.on_open(
            pos.instrument,
            pos.direction,
            risk_amount=eq * self._risk_pct_of(pos),
            entry_ts=pos.entry_ts,
        )

    @staticmethod
    def _risk_pct_of(pos: PaperPosition) -> float:
        # Paper-Sim rechnet in R; 1R Risiko = risk_per_trade (Default 1 %). Ohne echte
        # Sizing-Info nehmen wir 1 % — der PortfolioLedger ist relativ konsistent.
        return 0.01


__all__ = ["PaperLiveRunner", "PaperLiveStep"]
