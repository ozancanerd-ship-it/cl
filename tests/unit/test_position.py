"""Phase 3 · Schritt 6 — Exit / Position Management (``strategy.position``, Paper/Simulation).

Fill · Pending-Expiry · TP1/TP2/Runner · SL → Break-Even · Trail nach TP2 · Stop-Loss ·
worst-case-Fill (SL vor TP in einer Bar) · Long/Short-Symmetrie · Re-Analyse → EXIT_REQUIRED ·
manueller Exit/Close · MFE/MAE · deterministisches Replay · Signal-Lifecycle-Mapping.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from trading_agent.core.enums import Direction, RiskTier
from trading_agent.strategy.decision import Decision
from trading_agent.strategy.position import (
    ExitReason,
    PositionEvent,
    PositionManager,
    PositionParams,
    PositionState,
    PriceBar,
    signal_state_for,
)
from trading_agent.strategy.signal import SignalState

T0 = datetime(2024, 6, 3, 5, 0, tzinfo=UTC)


def _long(
    entry: float = 100.0, sl: float = 95.0, tp1: float = 110.0, tp2: float = 120.0
) -> Decision:
    return Decision.trade(
        "BTCUSD",
        T0,
        Direction.LONG,
        entry=entry,
        sl=sl,
        tp1=tp1,
        tp2=tp2,
        tier=RiskTier.A,
        rr_to_tp2=4.0,
        score=82.0,
        confidence=0.78,
    )


def _short(
    entry: float = 100.0, sl: float = 105.0, tp1: float = 90.0, tp2: float = 80.0
) -> Decision:
    return Decision.trade(
        "BTCUSD",
        T0,
        Direction.SHORT,
        entry=entry,
        sl=sl,
        tp1=tp1,
        tp2=tp2,
        tier=RiskTier.A,
        rr_to_tp2=4.0,
        score=82.0,
        confidence=0.78,
    )


def _bar(i: int, high: float, low: float, close: float | None = None) -> PriceBar:
    return PriceBar(
        T0 + timedelta(minutes=5 * i),
        high=high,
        low=low,
        close=close if close is not None else (high + low) / 2,
    )


# --------------------------------------------------------------------------- Eröffnung / Fill


def test_open_is_pending() -> None:
    pos = PositionManager().open(_long(), at=T0)
    assert pos.state is PositionState.PENDING
    assert pos.r_unit == 5.0
    assert signal_state_for(pos) is SignalState.ARMED


def test_open_rejects_non_actionable() -> None:
    import pytest

    from trading_agent.core.enums import SetupState

    wait = Decision.wait("BTCUSD", T0, SetupState.SWEPT)
    with pytest.raises(ValueError, match="BUY/SELL"):
        PositionManager().open(wait, at=T0)


def test_pending_fills_when_price_trades_through_entry() -> None:
    m = PositionManager()
    pos = m.open(_long(), at=T0)
    u = m.on_bar(pos, _bar(1, high=102, low=99))  # low <= entry 100
    assert u.event is PositionEvent.FILLED
    assert u.position.state is PositionState.OPEN
    assert u.signal_state is SignalState.TRIGGERED


def test_pending_expires_after_n_bars() -> None:
    m = PositionManager(params=PositionParams(pending_expiry_bars=3))
    pos = m.open(_long(), at=T0)
    for i in range(1, 3):
        pos = m.on_bar(pos, _bar(i, high=105, low=101)).position  # nie unter entry
        assert pos.state is PositionState.PENDING
    u = m.on_bar(pos, _bar(3, high=105, low=101))
    assert u.event is PositionEvent.EXPIRED
    assert u.position.state is PositionState.EXPIRED
    assert u.signal_state is SignalState.EXPIRED


# --------------------------------------------------------------------------- TP / Runner / Trail


def test_tp1_partial_and_be_stop() -> None:
    m = PositionManager()
    pos = m.open(_long(), at=T0, pending=False)
    u = m.on_bar(pos, _bar(1, high=111, low=101))  # TP1 @ 110
    assert u.event is PositionEvent.TP1_REACHED
    p = u.position
    assert p.state is PositionState.PARTIAL
    assert p.tp1_done and p.sl_at_be
    assert p.effective_sl == 100.0
    assert p.open_fraction == 0.5
    assert p.realized_r == 1.0  # 0.5 * 2R
    assert u.signal_state is SignalState.TP1_REACHED


def test_full_sequence_tp1_tp2_trail() -> None:
    m = PositionManager()
    pos = m.open(_long(), at=T0, pending=False)
    pos = m.on_bar(pos, _bar(1, high=111, low=101)).position  # TP1
    u2 = m.on_bar(pos, _bar(2, high=121, low=112))  # TP2 @ 120
    assert u2.event is PositionEvent.TP2_REACHED
    p = u2.position
    assert p.tp2_done and p.open_fraction == 0.2
    assert round(p.realized_r, 6) == 2.2  # 1.0 + 0.3*4R
    assert p.effective_sl == 110.0  # Runner-SL auf TP1

    u3 = m.on_bar(p, _bar(3, high=118, low=109))  # Runner-SL 110 getroffen
    assert u3.event is PositionEvent.SL_HIT
    assert u3.position.close_reason is ExitReason.TRAIL_STOP
    assert round(u3.position.realized_r, 6) == 2.6  # + 0.2 * 2R
    assert u3.signal_state is SignalState.CLOSED


# --------------------------------------------------------------------------- Stop-Loss


def test_direct_stop_loss() -> None:
    m = PositionManager()
    pos = m.open(_long(), at=T0, pending=False)
    u = m.on_bar(pos, _bar(1, high=101, low=94))  # SL @ 95
    assert u.event is PositionEvent.SL_HIT
    assert u.position.close_reason is ExitReason.STOP_LOSS
    assert u.position.realized_r == -1.0
    assert u.position.state is PositionState.CLOSED


def test_worst_case_fill_sl_before_tp() -> None:
    m = PositionManager()
    pos = m.open(_long(), at=T0, pending=False)
    u = m.on_bar(pos, _bar(1, high=125, low=94))  # sowohl SL als auch TP1/TP2 im Range
    assert u.event is PositionEvent.SL_HIT
    assert u.position.realized_r == -1.0
    assert not u.position.tp1_done


# --------------------------------------------------------------------------- Short-Symmetrie


def test_short_symmetry_tp1() -> None:
    m = PositionManager()
    pos = m.open(_short(), at=T0, pending=False)
    u = m.on_bar(pos, _bar(1, high=99, low=89))  # TP1 @ 90
    assert u.event is PositionEvent.TP1_REACHED
    assert u.position.realized_r == 1.0
    assert u.position.effective_sl == 100.0  # BE


def test_short_stop_loss() -> None:
    m = PositionManager()
    pos = m.open(_short(), at=T0, pending=False)
    u = m.on_bar(pos, _bar(1, high=106, low=99))  # SL @ 105
    assert u.event is PositionEvent.SL_HIT
    assert u.position.realized_r == -1.0


# --------------------------------------------------------------------------- Re-Analyse


def test_reevaluation_opposite_direction_requests_exit() -> None:
    m = PositionManager()
    pos = m.open(_long(), at=T0, pending=False)
    pos = m.on_bar(pos, _bar(1, high=105, low=100)).position
    flip = _short()
    u = m.on_reevaluation(pos, flip)
    assert u.event is PositionEvent.EXIT_REQUESTED
    assert u.position.state is PositionState.EXIT_REQUIRED
    assert u.signal_state is SignalState.EXIT_REQUIRED


def test_reevaluation_no_trade_requests_exit() -> None:
    from trading_agent.core.enums import NoTradeReason

    m = PositionManager()
    pos = m.open(_long(), at=T0, pending=False)
    nt = Decision.no_trade("BTCUSD", T0, [NoTradeReason.CANDIDATE_INVALIDATED])
    u = m.on_reevaluation(pos, nt)
    assert u.position.state is PositionState.EXIT_REQUIRED


def test_reevaluation_still_valid_is_no_change() -> None:
    m = PositionManager()
    pos = m.open(_long(), at=T0, pending=False)
    u = m.on_reevaluation(pos, _long())
    assert u.event is PositionEvent.NO_CHANGE
    assert u.position.state is PositionState.OPEN


def test_manual_request_then_close() -> None:
    m = PositionManager()
    pos = m.open(_long(), at=T0, pending=False)
    pos = m.on_bar(pos, _bar(1, high=108, low=101)).position
    req = m.request_exit(pos)
    assert req.position.state is PositionState.EXIT_REQUIRED
    done = m.close(
        req.position,
        price=107.0,
        at=T0 + timedelta(minutes=10),
        reason=ExitReason.MANUAL_EXIT_REQUEST,
    )
    assert done.event is PositionEvent.CLOSED
    assert done.position.state is PositionState.CLOSED
    assert round(done.position.realized_r, 6) == round(
        (107.0 - 100.0) / 5.0, 6
    )  # 1.4R auf voller Größe


# --------------------------------------------------------------------------- Tracking / Determinismus


def test_mfe_mae_tracking() -> None:
    m = PositionManager()
    pos = m.open(_long(), at=T0, pending=False)
    pos = m.on_bar(pos, _bar(1, high=108, low=97)).position  # +1.6R / -0.6R
    assert round(pos.mfe_r, 6) == 1.6
    assert round(pos.mae_r, 6) == -0.6
    pos = m.on_bar(pos, _bar(2, high=103, low=99)).position
    assert round(pos.mfe_r, 6) == 1.6  # bleibt Maximum
    assert round(pos.mae_r, 6) == -0.6


def test_deterministic_replay() -> None:
    bars = [_bar(1, 111, 101), _bar(2, 121, 112), _bar(3, 118, 109)]

    def run() -> list[tuple[str, float]]:
        m = PositionManager()
        pos = m.open(_long(), at=T0, pending=False)
        out: list[tuple[str, float]] = []
        for b in bars:
            u = m.on_bar(pos, b)
            pos = u.position
            out.append((u.event.value, round(pos.realized_r, 6)))
        return out

    assert run() == run()


def test_signal_state_mapping_covers_lifecycle() -> None:
    m = PositionManager()
    pos = m.open(_long(), at=T0)
    assert signal_state_for(pos) is SignalState.ARMED
    pos = m.on_bar(pos, _bar(1, high=101, low=99)).position  # fill
    assert signal_state_for(pos) is SignalState.TRIGGERED
    pos = m.on_bar(pos, _bar(2, high=104, low=100)).position  # managed (bars_held>0)
    assert signal_state_for(pos) is SignalState.MANAGED
    pos = m.on_bar(pos, _bar(3, high=111, low=103)).position  # TP1
    assert signal_state_for(pos) is SignalState.TP1_REACHED


# --------------------------------------------------------------------------- Integration Signal


def test_feeds_signal_tracker() -> None:
    import tests.unit.test_signal as sg
    from trading_agent.strategy.signal import SignalChangeKind, SignalTracker

    t = SignalTracker()
    t.ingest(sg._BASE)  # ARMED

    m = PositionManager()
    pos = m.open(sg._BASE.decision, at=T0, pending=False)
    st = signal_state_for(pos)  # TRIGGERED
    u = t.ingest(sg._BASE, position_state=st, position_changes=("filled",))
    assert u is not None and u.signal.state is SignalState.TRIGGERED
    assert u.revision.change_kind is SignalChangeKind.TRIGGERED
