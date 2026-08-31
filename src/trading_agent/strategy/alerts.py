"""Alert Engine — verwandelt Signal-Revisionen und Pipeline-Zustände in **handlungsrelevante,
selbst-aktualisierende** Alerts (Schritt 7).

Kernprinzipien:

* **Kein Spam.** Pro ``dedup_key`` (Signal + Alert-Typ) lebt höchstens **ein** aktiver Alert.
  Ein gleichartiges Folge-Ereignis innerhalb des Cooldowns wird unterdrückt oder als *Update*
  in den bestehenden Alert gefaltet — es entsteht keine zweite Karte.
* **Selbst-aktualisierend.** Ändert sich das zugrunde liegende Signal (neue Revision, anderer
  State), wird der Alert automatisch aktualisiert; kippt das Setup (``INVALIDATED`` / ``EXPIRED``
  / ``CLOSED``), werden alle offenen Alerts dieses Signals **automatisch verworfen** und durch
  genau einen Abschluss-Alert ersetzt.
* **Deterministisch & point-in-time.** Eingang = ``SignalUpdate`` / ``EngineTick`` (beide tragen
  ``information_cutoff``); kein Wall-Clock.

Der ``Alert``-Record ist frozen; jede Änderung erzeugt eine neue Instanz mit erhöhter
``revision``. Historie über :attr:`AlertEngine.log`.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from datetime import datetime, timedelta
from enum import StrEnum

from trading_agent.core.enums import DecisionType, NoTradeReason, RiskTier
from trading_agent.strategy.engine import EngineTick
from trading_agent.strategy.no_trade import NoTradeGroup, NoTradeReport
from trading_agent.strategy.signal import SignalChangeKind, SignalState, SignalUpdate

_Evidence = Mapping[str, str | float | int | bool | None]


class AlertType(StrEnum):
    NEW_A_PLUS_SETUP = "new_a_plus_setup"
    BUY = "buy"
    SELL = "sell"
    SIGNAL_STRENGTHENED = "signal_strengthened"
    SIGNAL_WEAKENED = "signal_weakened"
    ENTRY_CHANGED = "entry_changed"
    SL_CHANGED = "sl_changed"
    TP_CHANGED = "tp_changed"
    TP_REACHED = "tp_reached"
    SETUP_INVALIDATED = "setup_invalidated"
    EXIT_REQUIRED = "exit_required"
    RISK_LIMIT = "risk_limit"
    DATA_STALE = "data_stale"
    DATA_QUALITY_FAILURE = "data_quality_failure"
    BROKER_DISCONNECTED = "broker_disconnected"


class AlertSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AlertState(StrEnum):
    ACTIVE = "active"
    UPDATED = "updated"  # noch aktiv, aber seit Erstausgabe verändert
    DISMISSED = "dismissed"  # automatisch verworfen (Signal gekippt / abgelöst)
    SUPERSEDED = "superseded"  # durch einen gegensätzlichen Alert abgelöst


class AlertEventKind(StrEnum):
    RAISED = "raised"
    UPDATED = "updated"
    DISMISSED = "dismissed"
    SUPPRESSED = "suppressed"  # wegen Cooldown/Dedup nicht ausgegeben


_SEVERITY: dict[AlertType, AlertSeverity] = {
    AlertType.NEW_A_PLUS_SETUP: AlertSeverity.INFO,
    AlertType.BUY: AlertSeverity.INFO,
    AlertType.SELL: AlertSeverity.INFO,
    AlertType.SIGNAL_STRENGTHENED: AlertSeverity.INFO,
    AlertType.SIGNAL_WEAKENED: AlertSeverity.INFO,
    AlertType.ENTRY_CHANGED: AlertSeverity.WARNING,
    AlertType.SL_CHANGED: AlertSeverity.WARNING,
    AlertType.TP_CHANGED: AlertSeverity.INFO,
    AlertType.TP_REACHED: AlertSeverity.INFO,
    AlertType.SETUP_INVALIDATED: AlertSeverity.WARNING,
    AlertType.EXIT_REQUIRED: AlertSeverity.CRITICAL,
    AlertType.RISK_LIMIT: AlertSeverity.CRITICAL,
    AlertType.DATA_STALE: AlertSeverity.WARNING,
    AlertType.DATA_QUALITY_FAILURE: AlertSeverity.WARNING,
    AlertType.BROKER_DISCONNECTED: AlertSeverity.CRITICAL,
}

_FROM_CHANGE: dict[SignalChangeKind, AlertType] = {
    SignalChangeKind.STRENGTHENED: AlertType.SIGNAL_STRENGTHENED,
    SignalChangeKind.WEAKENED: AlertType.SIGNAL_WEAKENED,
    SignalChangeKind.ENTRY_CHANGED: AlertType.ENTRY_CHANGED,
    SignalChangeKind.SL_CHANGED: AlertType.SL_CHANGED,
    SignalChangeKind.TP_CHANGED: AlertType.TP_CHANGED,
    SignalChangeKind.TP_REACHED: AlertType.TP_REACHED,
    SignalChangeKind.INVALIDATED: AlertType.SETUP_INVALIDATED,
    SignalChangeKind.EXPIRED: AlertType.SETUP_INVALIDATED,
    SignalChangeKind.EXIT_REQUIRED: AlertType.EXIT_REQUIRED,
}

# Ein neuer Alert dieses Typs löst einen aktiven Alert des anderen Typs ab (Gegensatzpaare).
_SUPERSEDES: dict[AlertType, tuple[AlertType, ...]] = {
    AlertType.SIGNAL_STRENGTHENED: (AlertType.SIGNAL_WEAKENED,),
    AlertType.SIGNAL_WEAKENED: (AlertType.SIGNAL_STRENGTHENED,),
}

# No-Trade-Gruppe → externer Alert-Typ (Pipeline-Zustand, nicht Signal-Lifecycle)
_GROUP_ALERT: dict[NoTradeGroup, AlertType] = {
    NoTradeGroup.RISK: AlertType.RISK_LIMIT,
    NoTradeGroup.SYSTEM: AlertType.BROKER_DISCONNECTED,
}

_BROKER_REASONS = frozenset(
    {
        NoTradeReason.KILL_SWITCH_BROKER,
        NoTradeReason.KILL_SWITCH_GLOBAL,
        NoTradeReason.RECONCILIATION_PENDING,
        NoTradeReason.UNHANDLED_ERROR_STATE,
        NoTradeReason.API_DEGRADED,
    }
)


@dataclasses.dataclass(frozen=True, slots=True)
class Alert:
    alert_id: str  # == dedup_key (ein aktiver Alert je Schlüssel)
    dedup_key: str
    signal_id: str | None
    type: AlertType
    severity: AlertSeverity
    state: AlertState
    title: str
    body: str
    created_at: datetime
    updated_at: datetime
    revision: int  # Alert-eigene Revision (nicht die Signal-Revision)
    signal_revision: int | None
    evidence: _Evidence

    @property
    def is_open(self) -> bool:
        return self.state in (AlertState.ACTIVE, AlertState.UPDATED)


@dataclasses.dataclass(frozen=True, slots=True)
class AlertEvent:
    alert: Alert
    kind: AlertEventKind

    @property
    def delivered(self) -> bool:
        return self.kind is not AlertEventKind.SUPPRESSED


@dataclasses.dataclass(frozen=True, slots=True)
class AlertParams:
    default_cooldown: timedelta = timedelta(minutes=15)
    cooldown_overrides: Mapping[AlertType, timedelta] = dataclasses.field(default_factory=dict)
    # Lifecycle-kritische Typen ignorieren den Cooldown (immer sofort ausgeben):
    always_deliver: frozenset[AlertType] = frozenset(
        {
            AlertType.EXIT_REQUIRED,
            AlertType.SETUP_INVALIDATED,
            AlertType.SL_CHANGED,
            AlertType.BUY,
            AlertType.SELL,
            AlertType.RISK_LIMIT,
            AlertType.BROKER_DISCONNECTED,
        }
    )

    def cooldown(self, t: AlertType) -> timedelta:
        return self.cooldown_overrides.get(t, self.default_cooldown)


class AlertEngine:
    """Zustandsbehaftet. Eine Instanz pro Instrument/Universum."""

    def __init__(self, *, params: AlertParams | None = None) -> None:
        self._p = params or AlertParams()
        self._active: dict[str, Alert] = {}  # dedup_key → Alert
        self._last_emit: dict[str, datetime] = {}  # dedup_key → letzter Ausgabezeitpunkt
        self._log: list[Alert] = []

    # ---- Zugriff -----------------------------------------------------------------
    @property
    def active(self) -> tuple[Alert, ...]:
        return tuple(a for a in self._active.values() if a.is_open)

    @property
    def log(self) -> tuple[Alert, ...]:
        return tuple(self._log)

    def active_for(self, signal_id: str) -> tuple[Alert, ...]:
        return tuple(a for a in self.active if a.signal_id == signal_id)

    # ---- Haupteinstiege ------------------------------------------------------
    def on_engine_tick(self, tick: EngineTick) -> tuple[AlertEvent, ...]:
        events: list[AlertEvent] = []
        events.extend(self._from_no_trade(tick.result.no_trade, tick.at))
        if tick.signal is not None:
            events.extend(self.on_signal_update(tick.signal, tick.at))
        return tuple(events)

    def on_signal_update(self, update: SignalUpdate, now: datetime) -> tuple[AlertEvent, ...]:
        sig = update.signal
        rev = update.revision

        # 1) Setup gekippt → alle offenen Alerts dieses Signals verwerfen
        if sig.state.is_terminal:
            dismissed = self._dismiss_all(sig.signal_id, now, keep=None)
            closing_type = (
                AlertType.SETUP_INVALIDATED
                if sig.state in (SignalState.INVALIDATED, SignalState.EXPIRED)
                else AlertType.EXIT_REQUIRED
                if sig.state is SignalState.EXIT_REQUIRED
                else AlertType.TP_REACHED
            )
            if sig.state is SignalState.CLOSED:
                return tuple(dismissed)  # geschlossene Position: still, kein neuer Alert
            raised = self._raise(
                closing_type,
                sig.signal_id,
                rev.revision,
                now,
                title=f"{sig.instrument}: Setup {sig.state.value}",
                body="; ".join(rev.changes) or sig.state.value,
                evidence={"state": sig.state.value, "chain": rev.chain_progress},
                force=True,
            )
            return (*dismissed, raised)

        events: list[AlertEvent] = []

        # 2) Erstausgabe: NEW_A_PLUS / BUY / SELL
        if update.is_new or rev.change_kind is SignalChangeKind.CREATED:
            events.extend(self._creation_alerts(sig, rev, now))

        # 3) Änderungs-Alert
        atype = _FROM_CHANGE.get(rev.change_kind)
        if atype is not None and rev.change_kind not in (
            SignalChangeKind.INVALIDATED,
            SignalChangeKind.EXPIRED,
        ):
            for superseded in _SUPERSEDES.get(atype, ()):  # Gegensatz ablösen
                self._supersede(f"{sig.signal_id}:{superseded.value}", now)
            events.append(
                self._raise(
                    atype,
                    sig.signal_id,
                    rev.revision,
                    now,
                    title=_title(sig.instrument, atype, rev),
                    body="; ".join(rev.changes) or atype.value,
                    evidence={
                        "state": sig.state.value,
                        "score": rev.score,
                        "entry": rev.entry,
                        "sl": rev.sl,
                    },
                )
            )
        return tuple(events)

    # ---- interne Bausteine -------------------------------------------------------
    def _creation_alerts(self, sig: object, rev: object, now: datetime) -> list[AlertEvent]:
        # sig / rev sind DynamicSignal / SignalRevision — lokal getypt gehalten
        from trading_agent.strategy.signal import DynamicSignal, SignalRevision

        assert isinstance(sig, DynamicSignal) and isinstance(rev, SignalRevision)
        out: list[AlertEvent] = []
        if rev.decision in (DecisionType.BUY, DecisionType.SELL):
            if rev.tier is RiskTier.A_PLUS:
                out.append(
                    self._raise(
                        AlertType.NEW_A_PLUS_SETUP,
                        sig.signal_id,
                        rev.revision,
                        now,
                        title=f"{sig.instrument}: neues A+ Setup",
                        body=f"{rev.decision.value.upper()} · Score {rev.score} · "
                        f"Confidence {rev.confidence}",
                        evidence={"tier": rev.tier.value, "score": rev.score},
                        force=True,
                    )
                )
            direction_type = AlertType.BUY if rev.decision is DecisionType.BUY else AlertType.SELL
            out.append(
                self._raise(
                    direction_type,
                    sig.signal_id,
                    rev.revision,
                    now,
                    title=f"{sig.instrument}: {rev.decision.value.upper()} "
                    f"{rev.tier.value if rev.tier else ''}".strip(),
                    body=f"Entry {rev.entry} · SL {rev.sl} · TP1 {rev.tp1} · TP2 {rev.tp2}",
                    evidence={
                        "entry": rev.entry,
                        "sl": rev.sl,
                        "tp1": rev.tp1,
                        "tp2": rev.tp2,
                        "score": rev.score,
                    },
                    force=True,
                )
            )
        return out

    def _from_no_trade(self, report: NoTradeReport, now: datetime) -> list[AlertEvent]:
        out: list[AlertEvent] = []
        seen: set[AlertType] = set()
        for rec in report.records:
            atype: AlertType | None = None
            if rec.reason is NoTradeReason.DATA_STALE:
                atype = AlertType.DATA_STALE
            elif (
                rec.reason is NoTradeReason.DATA_CONFIDENCE_FLOOR or rec.group is NoTradeGroup.DATA
            ):
                atype = AlertType.DATA_QUALITY_FAILURE
            elif rec.reason in _BROKER_REASONS:
                atype = AlertType.BROKER_DISCONNECTED
            elif rec.group is NoTradeGroup.RISK:
                atype = AlertType.RISK_LIMIT
            else:
                atype = _GROUP_ALERT.get(rec.group)
            if atype is None or atype in seen:
                continue
            seen.add(atype)
            out.append(
                self._raise(
                    atype,
                    None,
                    None,
                    now,
                    title=f"{report.instrument}: {atype.value.replace('_', ' ')}",
                    body=rec.detail,
                    evidence={"reason": rec.reason.value, "group": rec.group.value},
                )
            )
        return out

    # ---- Ausgabe / Dedup / Cooldown -------------------------------------------
    def _raise(
        self,
        atype: AlertType,
        signal_id: str | None,
        signal_rev: int | None,
        now: datetime,
        *,
        title: str,
        body: str,
        evidence: _Evidence,
        force: bool = False,
    ) -> AlertEvent:
        key = f"{signal_id or '-'}:{atype.value}"
        existing = self._active.get(key)
        force = force or atype in self._p.always_deliver

        # Dedup: aktiver Alert existiert → als Update falten
        if existing is not None and existing.is_open:
            if not force and self._in_cooldown(key, atype, now):
                return AlertEvent(existing, AlertEventKind.SUPPRESSED)
            updated = dataclasses.replace(
                existing,
                state=AlertState.UPDATED,
                body=body,
                updated_at=now,
                revision=existing.revision + 1,
                signal_revision=signal_rev if signal_rev is not None else existing.signal_revision,
                evidence=evidence,
            )
            self._active[key] = updated
            self._last_emit[key] = now
            self._log.append(updated)
            return AlertEvent(updated, AlertEventKind.UPDATED)

        # Cooldown seit letztem (bereits geschlossenem) Alert gleichen Schlüssels
        if not force and self._in_cooldown(key, atype, now):
            return AlertEvent(
                Alert(
                    key,
                    key,
                    signal_id,
                    atype,
                    _SEVERITY[atype],
                    AlertState.DISMISSED,
                    title,
                    body,
                    now,
                    now,
                    1,
                    signal_rev,
                    evidence,
                ),
                AlertEventKind.SUPPRESSED,
            )

        alert = Alert(
            alert_id=key,
            dedup_key=key,
            signal_id=signal_id,
            type=atype,
            severity=_SEVERITY[atype],
            state=AlertState.ACTIVE,
            title=title,
            body=body,
            created_at=now,
            updated_at=now,
            revision=1,
            signal_revision=signal_rev,
            evidence=evidence,
        )
        self._active[key] = alert
        self._last_emit[key] = now
        self._log.append(alert)
        return AlertEvent(alert, AlertEventKind.RAISED)

    def _in_cooldown(self, key: str, atype: AlertType, now: datetime) -> bool:
        last = self._last_emit.get(key)
        return last is not None and now - last < self._p.cooldown(atype)

    def _dismiss_all(self, signal_id: str, now: datetime, *, keep: str | None) -> list[AlertEvent]:
        out: list[AlertEvent] = []
        for key, alert in list(self._active.items()):
            if alert.signal_id != signal_id or not alert.is_open or key == keep:
                continue
            dismissed = dataclasses.replace(
                alert, state=AlertState.DISMISSED, updated_at=now, revision=alert.revision + 1
            )
            self._active[key] = dismissed
            self._log.append(dismissed)
            out.append(AlertEvent(dismissed, AlertEventKind.DISMISSED))
        return out

    def _supersede(self, key: str, now: datetime) -> None:
        alert = self._active.get(key)
        if alert is not None and alert.is_open:
            superseded = dataclasses.replace(
                alert, state=AlertState.SUPERSEDED, updated_at=now, revision=alert.revision + 1
            )
            self._active[key] = superseded
            self._log.append(superseded)


def _title(instrument: str, atype: AlertType, rev: object) -> str:
    from trading_agent.strategy.signal import SignalRevision

    assert isinstance(rev, SignalRevision)
    label = atype.value.replace("_", " ")
    return f"{instrument}: {label} (rev {rev.revision})"


__all__ = [
    "Alert",
    "AlertEngine",
    "AlertEvent",
    "AlertEventKind",
    "AlertParams",
    "AlertSeverity",
    "AlertState",
    "AlertType",
]
