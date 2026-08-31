"""Dynamic Signal Engine — ein Signal ist ein **lebendes Setup**, kein statischer Schnappschuss.

Jede Neubewertung (``EvaluationResult``) wird gegen den letzten Stand **diffed**; jede Änderung
erzeugt eine **neue Revision** (append-only, alte Werte bleiben erhalten). Ein Signal wird **nie
überschrieben**.

```
BUY 91  →  BUY 86  →  BUY 77  →  WAIT  →  INVALIDATED
BUY 91  →  ENTRY_CHANGED  →  SL_CHANGED  →  TP1_REACHED  →  EXIT_REQUIRED
```

Lebenszyklus-States (``SignalState``):
``WATCH → DEVELOPING → ARMED → TRIGGERED → MANAGED → TP1_REACHED → TP2_REACHED → TP3_REACHED``
plus ``INVALIDATED``, ``EXIT_REQUIRED``, ``CLOSED``, ``EXPIRED``.

Die States bis ``ARMED`` ergeben sich aus ``Decision`` + FSM-``SetupState``; ab ``TRIGGERED`` aus
dem (optionalen) ``PaperPosition`` (``strategy.position``).

Point-in-time / deterministisch: die Engine ist ein dünner **zustandsbehafteter** Wrapper um die
reine ``evaluate``-Pipeline. ``signal_id`` = stabile ``SetupCandidate.setup_id``.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum

from trading_agent.core.enums import DecisionType, Direction, DisplayAlias, RiskTier, SetupState
from trading_agent.core.version import STRATEGY_VERSION
from trading_agent.strategy.decision import Decision
from trading_agent.strategy.evaluate import EvaluationResult

_Snapshot = Mapping[str, str | float | int | bool | None]


class SignalState(StrEnum):
    WATCH = "watch"
    DEVELOPING = "developing"
    ARMED = "armed"
    TRIGGERED = "triggered"
    MANAGED = "managed"
    TP1_REACHED = "tp1_reached"
    TP2_REACHED = "tp2_reached"
    TP3_REACHED = "tp3_reached"
    INVALIDATED = "invalidated"
    EXIT_REQUIRED = "exit_required"
    CLOSED = "closed"
    EXPIRED = "expired"

    @property
    def is_terminal(self) -> bool:
        return self in (SignalState.CLOSED, SignalState.EXPIRED, SignalState.INVALIDATED)

    @property
    def is_open_position(self) -> bool:
        return self in (
            SignalState.TRIGGERED,
            SignalState.MANAGED,
            SignalState.TP1_REACHED,
            SignalState.TP2_REACHED,
            SignalState.TP3_REACHED,
            SignalState.EXIT_REQUIRED,
        )


class SignalChangeKind(StrEnum):
    CREATED = "created"
    NO_CHANGE = "no_change"
    STATE_CHANGED = "state_changed"
    STRENGTHENED = "strengthened"
    WEAKENED = "weakened"
    ENTRY_CHANGED = "entry_changed"
    SL_CHANGED = "sl_changed"
    TP_CHANGED = "tp_changed"
    TRIGGERED = "triggered"
    TP_REACHED = "tp_reached"
    EXIT_REQUIRED = "exit_required"
    INVALIDATED = "invalidated"
    EXPIRED = "expired"
    CLOSED = "closed"


_FORMING_TO_STATE = {
    SetupState.SCANNING: SignalState.WATCH,
    SetupState.BIAS_SET: SignalState.WATCH,
    SetupState.LIQUIDITY_IDENTIFIED: SignalState.DEVELOPING,
    SetupState.SWEPT: SignalState.DEVELOPING,
    SetupState.RECLAIMED: SignalState.DEVELOPING,
    SetupState.DISPLACED: SignalState.DEVELOPING,
    SetupState.STRUCTURE_SHIFTED: SignalState.DEVELOPING,
    SetupState.ARMED: SignalState.ARMED,
}


# --------------------------------------------------------------------------------- Parameter


@dataclasses.dataclass(frozen=True, slots=True)
class SignalParams:
    score_change_eps: float = 3.0  # |Δ score| darüber ⇒ STRENGTHENED / WEAKENED
    price_change_eps: float = 1e-6  # |Δ entry/sl/tp| darüber ⇒ *_CHANGED
    stale_ticks: int = 40  # ohne Update ⇒ EXPIRED (sweep())


# --------------------------------------------------------------------------------- Ausgabe


@dataclasses.dataclass(frozen=True, slots=True)
class SignalRevision:
    revision: int
    at: datetime  # information_cutoff dieser Bewertung
    state: SignalState
    change_kind: SignalChangeKind
    changes: tuple[str, ...]
    decision: DecisionType
    tier: RiskTier | None
    score: float | None
    confidence: float | None
    entry: float | None
    sl: float | None
    tp1: float | None
    tp2: float | None
    reason_codes: tuple[str, ...]
    chain_progress: str

    @classmethod
    def _from_decision(
        cls,
        revision: int,
        state: SignalState,
        change_kind: SignalChangeKind,
        changes: tuple[str, ...],
        d: Decision,
    ) -> SignalRevision:
        return cls(
            revision=revision,
            at=d.information_cutoff,
            state=state,
            change_kind=change_kind,
            changes=changes,
            decision=d.decision,
            tier=d.tier,
            score=d.score,
            confidence=d.confidence,
            entry=d.entry,
            sl=d.sl,
            tp1=d.tp1,
            tp2=d.tp2,
            reason_codes=tuple(r.value for r in d.reason_codes),
            chain_progress=d.chain_progress,
        )

    @property
    def snapshot(self) -> _Snapshot:
        return {
            "revision": self.revision,
            "at": self.at.isoformat(),
            "state": self.state.value,
            "change_kind": self.change_kind.value,
            "decision": self.decision.value,
            "tier": self.tier.value if self.tier is not None else None,
            "score": self.score,
            "confidence": self.confidence,
            "entry": self.entry,
            "sl": self.sl,
            "tp1": self.tp1,
            "tp2": self.tp2,
        }


@dataclasses.dataclass(frozen=True, slots=True)
class DynamicSignal:
    signal_id: str
    instrument: str
    direction: Direction | None
    setup_id: str
    created_at: datetime
    updated_at: datetime
    state: SignalState
    revisions: tuple[SignalRevision, ...]
    latest_decision: Decision
    strategy_version: str = STRATEGY_VERSION

    @property
    def revision(self) -> int:
        return self.revisions[-1].revision

    @property
    def display_alias(self) -> DisplayAlias:
        alias_map = {
            SignalState.WATCH: DisplayAlias.WATCH,
            SignalState.DEVELOPING: DisplayAlias.DEVELOPING,
            SignalState.ARMED: DisplayAlias.ARMED,
            SignalState.TRIGGERED: DisplayAlias.CONFIRMED,
            SignalState.MANAGED: DisplayAlias.CONFIRMED,
            SignalState.TP1_REACHED: DisplayAlias.CONFIRMED,
            SignalState.TP2_REACHED: DisplayAlias.CONFIRMED,
            SignalState.TP3_REACHED: DisplayAlias.CONFIRMED,
            SignalState.EXIT_REQUIRED: DisplayAlias.CONFIRMED,
            SignalState.INVALIDATED: DisplayAlias.INVALIDATED,
            SignalState.CLOSED: DisplayAlias.EXPIRED,
            SignalState.EXPIRED: DisplayAlias.EXPIRED,
        }
        return alias_map[self.state]

    @property
    def is_alive(self) -> bool:
        return not self.state.is_terminal

    @property
    def history(self) -> tuple[_Snapshot, ...]:
        return tuple(r.snapshot for r in self.revisions)


@dataclasses.dataclass(frozen=True, slots=True)
class SignalUpdate:
    signal: DynamicSignal
    revision: SignalRevision
    is_new: bool
    changed: bool


# --------------------------------------------------------------------------------- Tracker


class SignalTracker:
    """Hält den lebenden Zustand aller Signale eines Universums. Zustandsbehaftet, aber
    deterministisch: gleiche Folge von ``EvaluationResult`` ⇒ gleiche Signal-Historie."""

    def __init__(self, *, params: SignalParams | None = None) -> None:
        self._p = params or SignalParams()
        self._signals: dict[str, DynamicSignal] = {}
        self._last_seen: dict[str, int] = {}
        self._tick = 0

    # ---- Zugriff ------------------------------------------------------------------
    @property
    def signals(self) -> tuple[DynamicSignal, ...]:
        return tuple(self._signals.values())

    @property
    def alive(self) -> tuple[DynamicSignal, ...]:
        return tuple(s for s in self._signals.values() if s.is_alive)

    def get(self, signal_id: str) -> DynamicSignal | None:
        return self._signals.get(signal_id)

    # ---- Fortschreibung --------------------------------------------------------
    def ingest(
        self,
        result: EvaluationResult,
        *,
        position_state: SignalState | None = None,
        position_changes: tuple[str, ...] = (),
    ) -> SignalUpdate | None:
        """Verarbeitet **eine** Neubewertung. Gibt das ``SignalUpdate`` zurück, oder ``None``,
        wenn kein Setup existiert (globaler No-Trade / reiner BIAS_SET ohne Kandidat).

        ``position_state`` / ``position_changes`` (aus ``strategy.position``) heben ein Signal
        ab ``TRIGGERED`` in den Positions-Lebenszyklus."""
        self._tick += 1
        cand = result.candidate
        d = result.decision

        if cand is None:
            return None

        sid = cand.setup_id
        self._last_seen[sid] = self._tick
        prev = self._signals.get(sid)

        target_state = _target_state(d, d.setup_state, position_state)

        if prev is None or prev.state.is_terminal:
            rev = SignalRevision._from_decision(
                1, target_state, SignalChangeKind.CREATED, ("created",), d
            )
            sig = DynamicSignal(
                signal_id=sid,
                instrument=cand.instrument,
                direction=cand.direction,
                setup_id=cand.setup_id,
                created_at=d.information_cutoff,
                updated_at=d.information_cutoff,
                state=target_state,
                revisions=(rev,),
                latest_decision=d,
            )
            self._signals[sid] = sig
            return SignalUpdate(sig, rev, is_new=True, changed=True)

        change_kind, changes = _diff(prev, d, target_state, position_changes, self._p)
        if change_kind is SignalChangeKind.NO_CHANGE:
            # Keine Änderung ⇒ keine neue Revision (ein Signal wird nur bei echten Änderungen
            # fortgeschrieben). latest_decision wird still aktualisiert (gleiche Substanz).
            sig = dataclasses.replace(prev, latest_decision=d)
            self._signals[sid] = sig
            return SignalUpdate(sig, prev.revisions[-1], is_new=False, changed=False)

        rev = SignalRevision._from_decision(
            prev.revision + 1, target_state, change_kind, changes, d
        )
        sig = dataclasses.replace(
            prev,
            updated_at=d.information_cutoff,
            state=target_state,
            revisions=(*prev.revisions, rev),
            latest_decision=d,
        )
        self._signals[sid] = sig
        return SignalUpdate(sig, rev, is_new=False, changed=True)

    def sweep(self, now: datetime) -> tuple[SignalUpdate, ...]:
        """Altert Signale aus, die seit ``stale_ticks`` nicht mehr als primär gesehen wurden."""
        out: list[SignalUpdate] = []
        for sid, sig in list(self._signals.items()):
            if not sig.is_alive or sig.state.is_open_position:
                continue
            if self._tick - self._last_seen.get(sid, 0) >= self._p.stale_ticks:
                rev = SignalRevision(
                    revision=sig.revision + 1,
                    at=now,
                    state=SignalState.EXPIRED,
                    change_kind=SignalChangeKind.EXPIRED,
                    changes=("stale — kein Update seit stale_ticks",),
                    decision=sig.latest_decision.decision,
                    tier=sig.latest_decision.tier,
                    score=sig.latest_decision.score,
                    confidence=sig.latest_decision.confidence,
                    entry=sig.latest_decision.entry,
                    sl=sig.latest_decision.sl,
                    tp1=sig.latest_decision.tp1,
                    tp2=sig.latest_decision.tp2,
                    reason_codes=(),
                    chain_progress=sig.latest_decision.chain_progress,
                )
                new = dataclasses.replace(
                    sig, updated_at=now, state=SignalState.EXPIRED, revisions=(*sig.revisions, rev)
                )
                self._signals[sid] = new
                out.append(SignalUpdate(new, rev, is_new=False, changed=True))
        return tuple(out)


# --------------------------------------------------------------------------------- intern


def _target_state(
    d: Decision, fsm_state: SetupState, position_state: SignalState | None
) -> SignalState:
    if position_state is not None:
        return position_state
    if d.decision in (DecisionType.BUY, DecisionType.SELL):
        return SignalState.ARMED
    if d.decision is DecisionType.WAIT:
        return _FORMING_TO_STATE.get(fsm_state, SignalState.WATCH)
    # NO_TRADE
    reasons = {r.value for r in d.reason_codes}
    if "candidate_expired" in reasons:
        return SignalState.EXPIRED
    if reasons or d.vetoes:
        return SignalState.INVALIDATED
    return SignalState.INVALIDATED


def _diff(
    prev: DynamicSignal,
    d: Decision,
    target_state: SignalState,
    position_changes: tuple[str, ...],
    p: SignalParams,
) -> tuple[SignalChangeKind, tuple[str, ...]]:
    last = prev.revisions[-1]
    changes: list[str] = list(position_changes)

    if target_state is not prev.state:
        changes.append(f"state {prev.state.value} → {target_state.value}")

    def moved(a: float | None, b: float | None) -> bool:
        return a is not None and b is not None and abs(a - b) > p.price_change_eps

    if moved(last.entry, d.entry):
        changes.append(f"entry {last.entry} → {d.entry}")
    if moved(last.sl, d.sl):
        changes.append(f"sl {last.sl} → {d.sl}")
    if moved(last.tp1, d.tp1) or moved(last.tp2, d.tp2):
        changes.append("tp geändert")

    score_delta = (d.score - last.score) if d.score is not None and last.score is not None else 0.0

    # Priorität der change_kind (grob → fein)
    if target_state in (SignalState.INVALIDATED,):
        kind = SignalChangeKind.INVALIDATED
    elif target_state is SignalState.EXPIRED:
        kind = SignalChangeKind.EXPIRED
    elif target_state is SignalState.CLOSED:
        kind = SignalChangeKind.CLOSED
    elif target_state is SignalState.EXIT_REQUIRED:
        kind = SignalChangeKind.EXIT_REQUIRED
    elif (
        target_state
        in (
            SignalState.TP1_REACHED,
            SignalState.TP2_REACHED,
            SignalState.TP3_REACHED,
        )
        and target_state is not prev.state
    ):
        kind = SignalChangeKind.TP_REACHED
    elif target_state is SignalState.TRIGGERED and prev.state is not SignalState.TRIGGERED:
        kind = SignalChangeKind.TRIGGERED
    elif any(c.startswith("entry ") for c in changes):
        kind = SignalChangeKind.ENTRY_CHANGED
    elif any(c.startswith("sl ") for c in changes):
        kind = SignalChangeKind.SL_CHANGED
    elif "tp geändert" in changes:
        kind = SignalChangeKind.TP_CHANGED
    elif target_state is not prev.state:
        kind = SignalChangeKind.STATE_CHANGED
    elif score_delta >= p.score_change_eps:
        kind = SignalChangeKind.STRENGTHENED
        changes.append(f"score {last.score:.1f} → {d.score:.1f}")
    elif score_delta <= -p.score_change_eps:
        kind = SignalChangeKind.WEAKENED
        changes.append(f"score {last.score:.1f} → {d.score:.1f}")
    else:
        kind = SignalChangeKind.NO_CHANGE

    return kind, tuple(changes)


__all__ = [
    "DynamicSignal",
    "SignalChangeKind",
    "SignalParams",
    "SignalRevision",
    "SignalState",
    "SignalTracker",
    "SignalUpdate",
]
