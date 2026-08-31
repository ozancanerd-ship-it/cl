"""Phase 3 · Schritt 5 — Continuous Re-Evaluation (``strategy.engine``).

Bei jedem neuen ``MarketContext`` wird die komplette Pipeline neu gerechnet und gegen den letzten
Stand gedifft: Signal-Revisionen + Paper-Position-Events. Kein statisches Signal.

Die reine Pipeline ist über ``evaluate_fn`` injizierbar — hier speisen wir eine vorbereitete
Folge von ``EvaluationResult`` ein (die Pipeline selbst hat eigene End-to-End-Tests in
``test_evaluate``), und treiben die Preisbewegung über echte M5-Bars im ``MarketContext``.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta

import tests.unit.test_evaluate as ev
from trading_agent.core.enums import DecisionType, NoTradeReason, SetupState, Timeframe
from trading_agent.core.models import OHLCV
from trading_agent.core.time import bar_close_time
from trading_agent.core.types import MarketContext
from trading_agent.strategy.engine import ContinuousEvaluator, EngineParams, EngineTick
from trading_agent.strategy.position import PositionState
from trading_agent.strategy.signal import SignalChangeKind, SignalState

M5 = Timeframe.M5
START = datetime(2024, 6, 3, 0, 0, tzinfo=UTC)

_BASE = ev._run(ev._long_mtf())  # BUY auf SMC-SWEEP-REV-01
assert _BASE.decision.decision is DecisionType.BUY
_ENTRY = _BASE.decision.entry
_SL = _BASE.decision.sl
_TP1 = _BASE.decision.tp1


def _bar(i: int, hi: float, lo: float) -> OHLCV:
    ot = START + timedelta(minutes=5 * i)
    return OHLCV(
        instrument="BTCUSD",
        timeframe=M5,
        open_time=ot,
        close_time=bar_close_time(ot, M5),
        open=(hi + lo) / 2,
        high=hi,
        low=lo,
        close=(hi + lo) / 2,
        volume=1.0,
        source="t",
    )


def _mc(bars: list[OHLCV]) -> MarketContext:
    return MarketContext(
        instrument="BTCUSD",
        base_timeframe=M5,
        information_cutoff=bars[-1].close_time,
        series={M5: tuple(bars)},
        spread=0.5,
    )


def _result_at(mc: MarketContext, **decision_overrides: object):
    dec = dataclasses.replace(
        _BASE.decision, information_cutoff=mc.information_cutoff, **decision_overrides
    )
    return dataclasses.replace(_BASE, decision=dec)


def _engine_returning(*results):
    """ContinuousEvaluator, dessen Pipeline nacheinander ``results`` liefert."""
    seq = iter(results)

    def fake(mc: MarketContext, **_kw: object):
        try:
            return next(seq)
        except StopIteration:
            return _result_at(mc)

    return ContinuousEvaluator(evaluate_fn=fake)


# --------------------------------------------------------------------------- Basisfluss


def test_buy_opens_pending_paper_position() -> None:
    eng = _engine_returning()
    bars = [_bar(0, _ENTRY + 5, _ENTRY + 2)]  # Preis über Entry → kein Fill
    tick = eng.on_market_context(_mc(bars))
    assert isinstance(tick, EngineTick)
    assert tick.decision is DecisionType.BUY
    assert tick.opened is not None and tick.opened.state is PositionState.PENDING
    assert tick.signal is not None and tick.signal.signal.state is SignalState.ARMED
    assert eng.position_for(_BASE.candidate.setup_id) is not None


def test_fill_then_tp1_updates_signal_lifecycle() -> None:
    eng = _engine_returning()
    # Tick 1: pending
    eng.on_market_context(_mc([_bar(0, _ENTRY + 5, _ENTRY + 2)]))
    # Tick 2: Bar handelt durch Entry → Fill
    t2 = eng.on_market_context(
        _mc([_bar(0, _ENTRY + 5, _ENTRY + 2), _bar(1, _ENTRY + 1, _ENTRY - 1)])
    )
    assert t2.position is not None and t2.position.position.state is PositionState.OPEN
    assert t2.signal is not None and t2.signal.signal.state is SignalState.TRIGGERED

    # Tick 3: Bar erreicht TP1
    t3 = eng.on_market_context(
        _mc(
            [
                _bar(0, _ENTRY + 5, _ENTRY + 2),
                _bar(1, _ENTRY + 1, _ENTRY - 1),
                _bar(2, _TP1 + 1, _ENTRY + 1),
            ]
        )
    )
    assert t3.position is not None and t3.position.position.tp1_done
    assert t3.signal is not None and t3.signal.signal.state is SignalState.TP1_REACHED


def test_reevaluation_invalidation_sets_exit_required() -> None:
    mc1 = _mc([_bar(0, _ENTRY + 1, _ENTRY - 1)])  # Fill sofort
    mc2 = _mc([_bar(0, _ENTRY + 1, _ENTRY - 1), _bar(1, _ENTRY + 2, _ENTRY)])
    buy = _result_at(mc1)
    invalid = dataclasses.replace(
        _BASE,
        decision=dataclasses.replace(
            _BASE.decision,
            decision=DecisionType.NO_TRADE,
            setup_state=SetupState.SCANNING,
            reason_codes=(NoTradeReason.CANDIDATE_INVALIDATED,),
            tier=None,
            information_cutoff=mc2.information_cutoff,
        ),
    )
    seq = iter([buy, invalid])
    eng = ContinuousEvaluator(evaluate_fn=lambda mc, **_k: next(seq))

    eng.on_market_context(mc1)
    t2 = eng.on_market_context(mc2)
    assert t2.position is not None
    assert t2.position.position.state is PositionState.EXIT_REQUIRED
    assert t2.signal is not None and t2.signal.signal.state is SignalState.EXIT_REQUIRED


def test_wait_result_produces_developing_signal_no_position() -> None:
    mc = _mc([_bar(0, _ENTRY + 5, _ENTRY + 2)])
    wait = dataclasses.replace(
        _BASE,
        decision=dataclasses.replace(
            _BASE.decision,
            decision=DecisionType.WAIT,
            setup_state=SetupState.STRUCTURE_SHIFTED,
            reason_codes=(),
            vetoes=(),
            tier=None,
            information_cutoff=mc.information_cutoff,
        ),
    )
    eng = ContinuousEvaluator(evaluate_fn=lambda m, **_k: wait)
    tick = eng.on_market_context(mc)
    assert tick.opened is None
    assert tick.signal is not None and tick.signal.signal.state is SignalState.DEVELOPING
    assert eng.open_positions == ()


def test_no_candidate_yields_no_signal() -> None:
    mc = _mc([_bar(0, 100, 99)])
    nocand = dataclasses.replace(
        _BASE,
        candidate=None,
        decision=dataclasses.replace(
            _BASE.decision,
            decision=DecisionType.NO_TRADE,
            setup_state=SetupState.SCANNING,
            reason_codes=(NoTradeReason.KILL_SWITCH_GLOBAL,),
            tier=None,
            information_cutoff=mc.information_cutoff,
        ),
    )
    eng = ContinuousEvaluator(evaluate_fn=lambda m, **_k: nocand)
    tick = eng.on_market_context(mc)
    assert tick.signal is None
    assert tick.opened is None


def test_auto_paper_disabled() -> None:
    eng = ContinuousEvaluator(
        params=EngineParams(auto_paper=False), evaluate_fn=lambda mc, **_k: _result_at(mc)
    )
    tick = eng.on_market_context(_mc([_bar(0, _ENTRY + 1, _ENTRY - 1)]))
    assert tick.opened is None
    assert eng.open_positions == ()
    assert tick.signal is not None and tick.signal.signal.state is SignalState.ARMED


def test_position_closes_and_is_removed_from_open() -> None:
    eng = _engine_returning()
    b0 = _bar(0, _ENTRY + 1, _ENTRY - 1)
    eng.on_market_context(_mc([b0]))  # Position pending eröffnet
    # nächste Bar füllt das Limit (Bars vor der Eröffnung zählen nicht)
    eng.on_market_context(_mc([b0, _bar(1, _ENTRY + 1, _ENTRY - 1)]))
    # übernächste Bar reißt den Stop
    t3 = eng.on_market_context(_mc([b0, _bar(1, _ENTRY + 1, _ENTRY - 1), _bar(2, _ENTRY, _SL - 1)]))
    assert t3.closed is not None and t3.closed.state is PositionState.CLOSED
    assert eng.open_positions == ()
    assert t3.signal is not None and t3.signal.signal.state is SignalState.CLOSED


def test_deterministic_replay() -> None:
    steps = [
        _mc([_bar(0, _ENTRY + 5, _ENTRY + 2)]),
        _mc([_bar(0, _ENTRY + 5, _ENTRY + 2), _bar(1, _ENTRY + 1, _ENTRY - 1)]),
        _mc(
            [
                _bar(0, _ENTRY + 5, _ENTRY + 2),
                _bar(1, _ENTRY + 1, _ENTRY - 1),
                _bar(2, _TP1 + 1, _ENTRY + 1),
            ]
        ),
    ]

    def run() -> list[tuple[str, str]]:
        eng = ContinuousEvaluator(evaluate_fn=lambda mc, **_k: _result_at(mc))
        out: list[tuple[str, str]] = []
        for mc in steps:
            tk = eng.on_market_context(mc)
            sig = tk.signal.signal.state.value if tk.signal else "-"
            pos = tk.position.event.value if tk.position else "-"
            out.append((sig, pos))
        return out

    assert run() == run()


def test_signal_strengthens_on_score_rise() -> None:
    mc1 = _mc([_bar(0, _ENTRY + 5, _ENTRY + 2)])
    mc2 = _mc([_bar(0, _ENTRY + 5, _ENTRY + 2), _bar(1, _ENTRY + 4, _ENTRY + 2)])
    r1 = _result_at(mc1, score=_BASE.decision.score - 8.0)
    r2 = _result_at(mc2, score=_BASE.decision.score + 4.0)
    seq = iter([r1, r2])
    eng = ContinuousEvaluator(
        params=EngineParams(auto_paper=False), evaluate_fn=lambda mc, **_k: next(seq)
    )
    eng.on_market_context(mc1)
    t2 = eng.on_market_context(mc2)
    assert t2.signal is not None
    assert t2.signal.revision.change_kind is SignalChangeKind.STRENGTHENED


# ------------------------------------------------------------- 2. Setup-Typ (Breakout-Retest)


def _breakout_result(mc: MarketContext):
    """EvaluationResult wie vom Breakout-Pfad: kein `candidate`, Decision trägt die setup_id."""
    from trading_agent.strategy.setups.breakout_retest import SETUP_BREAKOUT_RETEST

    dec = dataclasses.replace(
        _BASE.decision,
        information_cutoff=mc.information_cutoff,
        setup_id=SETUP_BREAKOUT_RETEST,
    )
    return dataclasses.replace(_BASE, decision=dec, candidate=None)


def test_breakout_decision_opens_paper_position_for_forward_validation() -> None:
    from trading_agent.strategy.setups.breakout_retest import SETUP_BREAKOUT_RETEST

    eng = ContinuousEvaluator(evaluate_fn=lambda mc, **_k: _breakout_result(mc))
    # Bar über Entry → pending (kein Fill), dann Bar berührt Entry → Fill
    t1 = eng.on_market_context(_mc([_bar(0, _ENTRY + 5, _ENTRY + 2)]))
    assert t1.decision is DecisionType.BUY
    assert t1.opened is not None and t1.opened.position_id == SETUP_BREAKOUT_RETEST
    assert eng.position_for(SETUP_BREAKOUT_RETEST) is not None

    # Folge-Tick: Breakout re-detektiert NICHT (NO_TRADE) — die offene Position muss trotzdem
    # weiterlaufen (SETUP_BREAKOUT_RETEST-Fallback in engine._open).
    nt_result = dataclasses.replace(
        _BASE,
        candidate=None,
        decision=dataclasses.replace(
            _BASE.decision,
            decision=DecisionType.NO_TRADE,
            setup_state=SetupState.SCANNING,
            reason_codes=(NoTradeReason.NO_RETEST,),
            tier=None,
        ),
    )
    eng._evaluate = lambda mc, **_k: nt_result  # type: ignore[assignment, method-assign]
    t2 = eng.on_market_context(
        _mc([_bar(0, _ENTRY + 5, _ENTRY + 2), _bar(1, _ENTRY + 1, _ENTRY - 1)])
    )
    assert eng.position_for(SETUP_BREAKOUT_RETEST) is not None
    assert t2.position is not None  # on_bar hat die Position fortgeschrieben (Fill)
