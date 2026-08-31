"""Phase 3 · Schritt 4 — Dynamic Signal Engine (``strategy.signal``).

Ein Signal lebt: jede Änderung erzeugt eine neue Revision, alte Werte bleiben erhalten,
nichts wird überschrieben. Beispiele aus der Spezifikation:

``BUY 91 → BUY 86 → BUY 77 → WAIT → INVALIDATED``
``BUY 91 → ENTRY_CHANGED → SL_CHANGED → TP1_REACHED → EXIT_REQUIRED``
"""

from __future__ import annotations

import dataclasses
from datetime import timedelta

import tests.unit.test_evaluate as ev
from trading_agent.core.enums import DecisionType, DisplayAlias, NoTradeReason, SetupState
from trading_agent.strategy.signal import (
    DynamicSignal,
    SignalChangeKind,
    SignalParams,
    SignalState,
    SignalTracker,
    SignalUpdate,
)

# --------------------------------------------------------------------------- Fixtures / Helper

_BASE = ev._run(ev._long_mtf())  # EvaluationResult mit BUY-Decision auf SMC-SWEEP-REV-01
assert _BASE.decision.decision is DecisionType.BUY
assert _BASE.candidate is not None


def _res(**decision_overrides: object):
    """Neuer ``EvaluationResult`` mit derselben Kette, aber modifizierter Decision."""
    dec = dataclasses.replace(_BASE.decision, **decision_overrides)  # type: ignore[arg-type]
    return dataclasses.replace(_BASE, decision=dec)


def _score(delta: float):
    return _res(score=_BASE.decision.score + delta)


def _wait():
    return _res(
        decision=DecisionType.WAIT,
        setup_state=SetupState.STRUCTURE_SHIFTED,
        reason_codes=(),
        vetoes=(),
        tier=None,
    )


def _invalidated():
    return _res(
        decision=DecisionType.NO_TRADE,
        setup_state=SetupState.SCANNING,
        reason_codes=(NoTradeReason.CANDIDATE_INVALIDATED,),
        tier=None,
    )


def _expired():
    return _res(
        decision=DecisionType.NO_TRADE,
        setup_state=SetupState.SCANNING,
        reason_codes=(NoTradeReason.CANDIDATE_EXPIRED,),
        tier=None,
    )


# --------------------------------------------------------------------------- Erzeugung


def test_new_signal_created() -> None:
    t = SignalTracker()
    upd = t.ingest(_BASE)
    assert isinstance(upd, SignalUpdate)
    assert upd.is_new and upd.changed
    assert upd.signal.state is SignalState.ARMED
    assert upd.revision.revision == 1
    assert upd.revision.change_kind is SignalChangeKind.CREATED
    assert upd.signal.signal_id == _BASE.candidate.setup_id
    assert upd.signal.display_alias is DisplayAlias.ARMED
    assert t.get(upd.signal.signal_id) is upd.signal


def test_no_candidate_returns_none() -> None:
    t = SignalTracker()
    empty = dataclasses.replace(_BASE, candidate=None)
    assert t.ingest(empty) is None
    assert t.signals == ()


# --------------------------------------------------------------------------- Revisionen / Diff


def test_no_change_does_not_add_revision() -> None:
    t = SignalTracker()
    t.ingest(_BASE)
    upd = t.ingest(_BASE)
    assert upd is not None and not upd.changed
    assert upd.signal.revision == 1
    assert len(upd.signal.revisions) == 1
    assert upd.revision.change_kind is SignalChangeKind.CREATED


def test_weakened_then_strengthened() -> None:
    t = SignalTracker()
    t.ingest(_BASE)
    w = t.ingest(_score(-10.0))
    assert w is not None and w.revision.change_kind is SignalChangeKind.WEAKENED
    assert w.signal.revision == 2
    s = t.ingest(_score(+2.0))
    assert s is not None and s.revision.change_kind is SignalChangeKind.STRENGTHENED
    assert s.signal.revision == 3
    # Historie vollständig erhalten (nichts überschrieben)
    scores = [r.score for r in s.signal.revisions]
    assert scores == [
        _BASE.decision.score,
        _BASE.decision.score - 10.0,
        _BASE.decision.score + 2.0,
    ]


def test_small_score_move_is_no_change() -> None:
    t = SignalTracker(params=SignalParams(score_change_eps=3.0))
    t.ingest(_BASE)
    upd = t.ingest(_score(-1.0))
    assert upd is not None and not upd.changed
    assert upd.signal.revision == 1


def test_entry_changed() -> None:
    t = SignalTracker()
    t.ingest(_BASE)
    upd = t.ingest(_res(entry=_BASE.decision.entry + 5.0))
    assert upd is not None
    assert upd.revision.change_kind is SignalChangeKind.ENTRY_CHANGED
    assert any("entry" in c for c in upd.revision.changes)


def test_sl_changed() -> None:
    t = SignalTracker()
    t.ingest(_BASE)
    upd = t.ingest(_res(sl=_BASE.decision.sl - 3.0))
    assert upd is not None and upd.revision.change_kind is SignalChangeKind.SL_CHANGED


def test_tp_changed() -> None:
    t = SignalTracker()
    t.ingest(_BASE)
    upd = t.ingest(_res(tp1=_BASE.decision.tp1 + 4.0))
    assert upd is not None and upd.revision.change_kind is SignalChangeKind.TP_CHANGED


# --------------------------------------------------------------------------- Lifecycle


def test_buy_to_wait_to_invalidated() -> None:
    t = SignalTracker()
    sid = t.ingest(_BASE).signal.signal_id

    w = t.ingest(_wait())
    assert w is not None and w.signal.signal_id == sid  # gleiches Signal, nicht neu
    assert w.signal.state is SignalState.DEVELOPING
    assert w.revision.change_kind is SignalChangeKind.STATE_CHANGED

    inv = t.ingest(_invalidated())
    assert inv is not None and inv.signal.state is SignalState.INVALIDATED
    assert inv.revision.change_kind is SignalChangeKind.INVALIDATED
    assert not inv.signal.is_alive
    assert inv.signal.display_alias is DisplayAlias.INVALIDATED
    # komplette Kette nachvollziehbar
    kinds = [r.change_kind for r in inv.signal.revisions]
    assert kinds == [
        SignalChangeKind.CREATED,
        SignalChangeKind.STATE_CHANGED,
        SignalChangeKind.INVALIDATED,
    ]


def test_terminal_signal_rearms_as_fresh_signal() -> None:
    t = SignalTracker()
    t.ingest(_BASE)
    t.ingest(_invalidated())
    again = t.ingest(_BASE)
    assert again is not None and again.is_new
    assert again.signal.revision == 1
    assert again.signal.state is SignalState.ARMED


def test_expiry_reason_maps_to_expired() -> None:
    t = SignalTracker()
    t.ingest(_BASE)
    upd = t.ingest(_expired())
    assert upd is not None and upd.signal.state is SignalState.EXPIRED
    assert upd.revision.change_kind is SignalChangeKind.EXPIRED
    assert not upd.signal.is_alive


# --------------------------------------------------------------------------- Positions-Lifecycle


def test_position_states_drive_trigger_to_exit() -> None:
    t = SignalTracker()
    t.ingest(_BASE)

    trg = t.ingest(
        _BASE, position_state=SignalState.TRIGGERED, position_changes=("filled @ entry",)
    )
    assert trg is not None and trg.signal.state is SignalState.TRIGGERED
    assert trg.revision.change_kind is SignalChangeKind.TRIGGERED

    m = t.ingest(_BASE, position_state=SignalState.MANAGED)
    assert m is not None and m.signal.state is SignalState.MANAGED

    tp1 = t.ingest(_BASE, position_state=SignalState.TP1_REACHED)
    assert tp1 is not None and tp1.revision.change_kind is SignalChangeKind.TP_REACHED

    ex = t.ingest(
        _BASE, position_state=SignalState.EXIT_REQUIRED, position_changes=("SL breach on M5",)
    )
    assert ex is not None and ex.signal.state is SignalState.EXIT_REQUIRED
    assert ex.revision.change_kind is SignalChangeKind.EXIT_REQUIRED
    assert "SL breach on M5" in ex.revision.changes

    cl = t.ingest(_BASE, position_state=SignalState.CLOSED)
    assert cl is not None and cl.signal.state is SignalState.CLOSED
    assert not cl.signal.is_alive


# --------------------------------------------------------------------------- sweep / Alterung


def test_sweep_expires_stale_watch_signals() -> None:
    t = SignalTracker(params=SignalParams(stale_ticks=3))
    other = dataclasses.replace(
        _BASE,
        candidate=dataclasses.replace(_BASE.candidate, setup_id="OTHER:x"),
    )
    t.ingest(_BASE)
    for _ in range(4):
        t.ingest(other)  # nur "other" wird weiter gesehen
    out = t.sweep(now=_BASE.decision.information_cutoff + timedelta(hours=1))
    ids = {u.signal.signal_id for u in out}
    assert _BASE.candidate.setup_id in ids
    assert "OTHER:x" not in ids
    expired = t.get(_BASE.candidate.setup_id)
    assert expired is not None and expired.state is SignalState.EXPIRED


def test_sweep_never_expires_open_position() -> None:
    t = SignalTracker(params=SignalParams(stale_ticks=1))
    t.ingest(_BASE)
    t.ingest(_BASE, position_state=SignalState.TRIGGERED)
    out = t.sweep(now=_BASE.decision.information_cutoff + timedelta(hours=2))
    assert out == ()
    assert t.get(_BASE.candidate.setup_id).state is SignalState.TRIGGERED


# --------------------------------------------------------------------------- Determinismus / UI


def test_deterministic_history() -> None:
    seq = [_BASE, _score(-10.0), _wait(), _invalidated()]

    def run() -> list[str]:
        tr = SignalTracker()
        hist: list[str] = []
        for r in seq:
            u = tr.ingest(r)
            assert u is not None
            hist.append(f"{u.signal.state}:{u.revision.change_kind}:{u.revision.revision}")
        return hist

    assert run() == run()


def test_history_snapshot_payload_for_ui() -> None:
    t = SignalTracker()
    t.ingest(_BASE)
    sig = t.ingest(_score(-10.0)).signal
    hist = sig.history
    assert len(hist) == 2
    assert hist[0]["change_kind"] == "created"
    assert hist[1]["change_kind"] == "weakened"
    assert hist[1]["score"] == _BASE.decision.score - 10.0
    assert all("at" in row and "state" in row for row in hist)
    assert isinstance(sig, DynamicSignal)


def test_direction_and_ids_stable_across_revisions() -> None:
    t = SignalTracker()
    a = t.ingest(_BASE).signal
    b = t.ingest(_score(-10.0)).signal
    assert a.signal_id == b.signal_id
    assert a.direction == b.direction
    assert a.created_at == b.created_at
    assert b.updated_at == _BASE.decision.information_cutoff


def test_bare_decision_state_mapping_symmetry() -> None:
    # SELL-Signal genauso behandelt wie BUY
    sm, _sc, _ = _sell_result()
    t = SignalTracker()
    upd = t.ingest(sm)
    assert upd is not None and upd.signal.state is SignalState.ARMED
    assert upd.signal.direction is not None


def _sell_result():
    import tests.unit.test_confidence as tc

    sm, sc, _ = tc._short()
    tc._settle_regime(sm)
    sm = ev._clean(sm)
    r = ev._run(sm)
    assert r.decision.decision is DecisionType.SELL
    return r, sc, None
