"""Continuous Re-Evaluation — der Motor, der die statische ``evaluate``-Pipeline in einen
**lebenden** Prozess verwandelt (Schritt 5, Rückgrat für Paper-Live Schritt 10).

```
LIVE MARKET DATA → MarketContext → evaluate() → EvaluationResult
                                        │
                                        ├─► SignalTracker.ingest()  → Signal-Revision
                                        └─► PositionManager          → Paper-Position
```

Bei **jedem** neuen ``MarketContext`` wird die **komplette** Analyse neu gerechnet (Bias, Setup,
Confirmation, Confluence, Veto, Confidence, Score, Entry/SL/TP, RR) — es gibt keine statischen
Signale. Das Ergebnis wird gegen den letzten Stand gedifft; Änderungen erzeugen Signal-Revisionen
und/oder Positions-Events.

Die Engine hält **keinen** Broker-Bezug: sie konsumiert ``MarketContext`` und produziert
``EngineTick``. Woher die Bars kommen (Backtest-Replay, Paper-Feed, später Live-Feed) ist der
Engine egal — identische Pipeline für Backtest / Paper / Demo / Live.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Sequence
from datetime import datetime

from trading_agent.core.enums import DecisionType, Timeframe
from trading_agent.core.types import MarketContext, PortfolioContext
from trading_agent.refdata.models import SessionSpec
from trading_agent.strategy.costs import CostConfig
from trading_agent.strategy.decision import Decision
from trading_agent.strategy.evaluate import EvaluateParams, EvaluationResult, evaluate
from trading_agent.strategy.m1_feed import M1FeedParams, M1Source, NullM1Source, confirmation_window
from trading_agent.strategy.no_trade import AccountRisk, InstrumentHistory, SystemState
from trading_agent.strategy.position import (
    ExitReason,
    PaperPosition,
    PositionManager,
    PositionParams,
    PositionState,
    PositionUpdate,
    PriceBar,
    signal_state_for,
)
from trading_agent.strategy.setup_detection import SetupCandidate
from trading_agent.strategy.setups.breakout_retest import SETUP_BREAKOUT_RETEST
from trading_agent.strategy.signal import SignalState, SignalTracker, SignalUpdate


@dataclasses.dataclass(frozen=True, slots=True)
class EngineParams:
    evaluate: EvaluateParams = dataclasses.field(default_factory=EvaluateParams)
    position: PositionParams = dataclasses.field(default_factory=PositionParams)
    cost: CostConfig = dataclasses.field(default_factory=CostConfig)  # Default: alle Sätze 0.0
    auto_paper: bool = True  # bei aktionierbarer Decision automatisch Paper-Position eröffnen
    fill_timeframe: Timeframe = Timeframe.M5  # Serie, aus der on_bar-Preisbewegung kommt
    sweep_stale: bool = True  # abgestandene Signale je Tick altern lassen
    m1_feed: M1FeedParams = dataclasses.field(default_factory=M1FeedParams)


@dataclasses.dataclass(frozen=True, slots=True)
class EngineTick:
    at: datetime
    result: EvaluationResult
    signal: SignalUpdate | None
    position: PositionUpdate | None
    opened: PaperPosition | None
    closed: PaperPosition | None
    swept: tuple[SignalUpdate, ...]
    risk: object | None = None  # RiskVerdict, wenn ein risk_gate gesetzt ist (sonst None)
    risk_blocked: bool = False  # True ⇒ die Risk Engine hat einen sonst offenen Entry verhindert

    @property
    def decision(self) -> DecisionType:
        return self.result.decision.decision


# Risk-Gate: bekommt die (aktionierbare) Decision, gibt ein Objekt mit `.blocks: bool` zurück
# (typischerweise ``risk.risk_engine.RiskVerdict``). Kann nur ablehnen — nie hochstufen.
RiskGate = Callable[[Decision], object]


class ContinuousEvaluator:
    """Zustandsbehafteter Treiber. Eine Instanz pro Instrument."""

    def __init__(
        self,
        *,
        params: EngineParams | None = None,
        evaluate_fn: Callable[..., EvaluationResult] | None = None,
        m1_source: M1Source | None = None,
        risk_gate: RiskGate | None = None,
    ) -> None:
        self._p = params or EngineParams()
        # DI-Hook: der reine Pipeline-Aufruf. Default = ``strategy.evaluate.evaluate``.
        # Ein Backtest-Replay oder ein Test kann hier eine vorbereitete Pipeline einhängen.
        self._evaluate: Callable[..., EvaluationResult] = evaluate_fn or evaluate
        # Native M1-Zufuhr für die Confirmation. Default = keine (kein Fake).
        self._m1: M1Source = m1_source or NullM1Source()
        # Optionaler Risk-Gate VOR dem Auto-Open. Kann einen Entry nur verhindern.
        self._risk_gate = risk_gate
        self._last_candidate: SetupCandidate | None = None
        self.signals = SignalTracker()
        self._positions = PositionManager(params=self._p.position, cost=self._p.cost)
        self._open: dict[str, PaperPosition] = {}
        self._seen_fill_bar: dict[str, datetime] = {}
        # Über Ticks gehaltener MTF-Analyse-Cache (memoisiert höhere Timeframes,
        # solange ihre Bar-Fenster + Freshness-Bucket unverändert sind). Reine
        # Performance — der Key kodiert die volle Eingabe-Identität.
        self._mtf_cache: dict[tuple[object, ...], object] = {}

    # ---- Zugriff -----------------------------------------------------------------
    @property
    def open_positions(self) -> tuple[PaperPosition, ...]:
        return tuple(self._open.values())

    def position_for(self, setup_id: str) -> PaperPosition | None:
        return self._open.get(setup_id)

    # ---- Recovery (M-01) --------------------------------------------------------
    def seen_fill_bars(self) -> dict[str, str]:
        """ISO-Zeitstempel je Position — Teil des Supervisor-Snapshots. ``signals``/``_mtf_cache``
        werden **nicht** exportiert — sie leiten sich nach dem Neustart aus dem Markt neu ab
        (neue, kürzere Revisions-Historie; konsistent)."""
        return {k: v.isoformat() for k, v in self._seen_fill_bar.items()}

    def restore_positions(
        self,
        positions: dict[str, PaperPosition],
        seen_fill_bar: dict[str, datetime] | None = None,
    ) -> int:
        """Offene Paper-Positionen nach einem Neustart wieder einhängen — damit der Engine sie
        **fortschreibt** statt sie **erneut zu öffnen**. Nur nicht-terminale Positionen."""
        n = 0
        for sid, pos in positions.items():
            if pos.state.is_terminal:
                continue
            self._open[sid] = pos
            n += 1
        for k, v in (seen_fill_bar or {}).items():
            self._seen_fill_bar[k] = v
        return n

    # ---- Haupteinstieg ---------------------------------------------------------
    def on_market_context(
        self,
        mc: MarketContext,
        *,
        portfolio_context: PortfolioContext | None = None,
        m1_bars: Sequence[object] = (),
        session_specs: Sequence[SessionSpec] = (),
        system: SystemState | None = None,
        instrument_history: InstrumentHistory | None = None,
        account_risk: AccountRisk | None = None,
    ) -> EngineTick:
        # Native M1-Confirmation-Fenster: aus dem zuletzt bekannten ARMED-Kandidaten (1 Tick
        # Nachlauf — Chicken/Egg: der aktuelle Kandidat entsteht erst in diesem evaluate()).
        # Explizit übergebene ``m1_bars`` haben Vorrang.
        if not m1_bars and self._last_candidate is not None and self._last_candidate.is_armed:
            m1_bars = confirmation_window(
                self._m1,
                self._last_candidate,
                information_cutoff=mc.information_cutoff,
                params=self._p.m1_feed,
            )

        # ``mtf_cache`` nur an den echten Pipeline-Aufruf reichen — ein eingehängter
        # Test-/Replay-Stub akzeptiert das Keyword evtl. nicht.
        extra: dict[str, object] = (
            {"mtf_cache": self._mtf_cache} if self._evaluate is evaluate else {}
        )
        result = self._evaluate(
            mc,
            portfolio_context=portfolio_context,
            m1_bars=m1_bars,
            session_specs=session_specs,
            system=system,
            instrument_history=instrument_history,
            account_risk=account_risk,
            params=self._p.evaluate,
            **extra,
        )
        now = mc.information_cutoff
        cand = result.candidate
        self._last_candidate = cand
        # SMC-Kandidat ODER 2. Setup-Typ (Breakout-Retest): dessen setup_id trägt die Decision,
        # es gibt keinen `candidate`. So bekommt auch der 2. Setup-Typ eine Paper-Position →
        # Forward-Validierung (governance.assess_edge_health) läuft.
        setup_id = cand.setup_id if cand is not None else None
        if setup_id is None and result.decision.decision in (DecisionType.BUY, DecisionType.SELL):
            setup_id = result.decision.setup_id
        # 2. Setup-Typ (Breakout-Retest) hat keinen persistenten `candidate` → eine bereits
        # offene Breakout-Position muss weiterlaufen, auch wenn der aktuelle Tick NO_TRADE ist.
        if setup_id is None and SETUP_BREAKOUT_RETEST in self._open:
            setup_id = SETUP_BREAKOUT_RETEST

        pos_update: PositionUpdate | None = None
        opened: PaperPosition | None = None
        closed: PaperPosition | None = None
        position_state: SignalState | None = None
        position_changes: tuple[str, ...] = ()
        risk_verdict: object | None = None
        risk_blocked = False

        # 1) bestehende Paper-Position fortschreiben
        pos = self._open.get(setup_id) if setup_id is not None else None
        if pos is not None:
            pos, pos_update, closed = self._advance_position(pos, mc, result)
            position_state = signal_state_for(pos)
            position_changes = pos_update.changes if pos_update is not None else ()

        # 2) neue Paper-Position eröffnen (nur wenn keine offen ist)
        elif (
            self._p.auto_paper
            and setup_id is not None
            and result.decision.decision in (DecisionType.BUY, DecisionType.SELL)
        ):
            # Risk-Gate VOR dem Öffnen — kann den Entry nur verhindern, nie erzeugen.
            if self._risk_gate is not None:
                risk_verdict = self._risk_gate(result.decision)
                risk_blocked = bool(getattr(risk_verdict, "blocks", False))
            if risk_blocked:
                position_changes = ("risk engine blocked entry",)
            else:
                opened = self._positions.open(result.decision, at=now, pending=True)
                # position_id/signal_id an die Kandidaten-``setup_id`` binden (die ``Decision``
                # trägt ggf. noch den Default-Setup-Namen). So sind ``_open`` und
                # ``_seen_fill_bar`` konsistent verschlüsselt.
                if opened.position_id != setup_id:
                    opened = dataclasses.replace(opened, position_id=setup_id, signal_id=setup_id)
                self._open[setup_id] = opened
                # Die Fill-Simulation darf die Position nur gegen Bars laufen lassen, die NACH
                # der Eröffnung schließen — nie gegen die Warmup-Historie im MarketContext.
                self._seen_fill_bar[setup_id] = now
                position_state = signal_state_for(opened)
                position_changes = ("paper position opened (pending)",)

        # 3) Signal-Revision
        sig_update = self.signals.ingest(
            result, position_state=position_state, position_changes=position_changes
        )

        swept = self.signals.sweep(now) if self._p.sweep_stale else ()
        return EngineTick(
            at=now,
            result=result,
            signal=sig_update,
            position=pos_update,
            opened=opened,
            closed=closed,
            swept=swept,
            risk=risk_verdict,
            risk_blocked=risk_blocked,
        )

    # ---- intern -----------------------------------------------------------------
    def _advance_position(
        self, pos: PaperPosition, mc: MarketContext, result: EvaluationResult
    ) -> tuple[PaperPosition, PositionUpdate | None, PaperPosition | None]:
        update: PositionUpdate | None = None

        # a) neue abgeschlossene Preis-Bars seit dem letzten Tick durchlaufen
        bars = mc.series.get(self._p.fill_timeframe, ())
        last_seen = self._seen_fill_bar.get(pos.position_id)
        for ohlcv in bars:
            if last_seen is not None and ohlcv.close_time <= last_seen:
                continue
            if pos.state.is_terminal:
                break
            update = self._positions.on_bar(
                pos, PriceBar(ohlcv.close_time, ohlcv.high, ohlcv.low, ohlcv.close)
            )
            pos = update.position
        if bars:
            self._seen_fill_bar[pos.position_id] = bars[-1].close_time

        # b) Re-Analyse: kippt das Setup, EXIT_REQUIRED setzen
        if pos.state.is_live and pos.state is not PositionState.EXIT_REQUIRED:
            re = self._positions.on_reevaluation(pos, result.decision)
            if re.changed:
                pos = re.position
                update = re

        self._open[pos.position_id] = pos
        closed: PaperPosition | None = None
        if pos.state.is_terminal:
            closed = pos
            self._open.pop(pos.position_id, None)
            self._seen_fill_bar.pop(pos.position_id, None)
        return pos, update, closed

    # ---- Abschluss (Ende des Replay-Fensters) -----------------------------------
    def force_close(
        self, *, price: float, at: datetime, reason: ExitReason
    ) -> tuple[PaperPosition, ...]:
        """Schließt alle noch offenen Paper-Positionen (z. B. am Ende der Historie).
        Pending-Positionen (nie gefüllt) werden verworfen, nicht als Trade gewertet."""
        out: list[PaperPosition] = []
        for pid, pos in list(self._open.items()):
            if pos.state is PositionState.PENDING:
                self._open.pop(pid, None)
                self._seen_fill_bar.pop(pid, None)
                continue
            done = self._positions.close(pos, price=price, at=at, reason=reason).position
            out.append(done)
            self._open.pop(pid, None)
            self._seen_fill_bar.pop(pid, None)
        return tuple(out)


__all__ = ["ContinuousEvaluator", "EngineParams", "EngineTick"]
