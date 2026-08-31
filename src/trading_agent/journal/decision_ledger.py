"""Decision-Log-Recorder — hängt sich an den ``EventBus`` und persistiert **jede** relevante
Entscheidung 24/7 nachvollziehbar (`Masterplan §31/§64`).

Kein Eingriff in die Pipeline: der Recorder abonniert nur die bereits publizierten Events
(``DecisionMade`` / ``SignalRevised`` / ``PaperPositionChanged`` / ``AlertRaised``) und schreibt
kompakte JSON-Zeilen in den vorhandenen ``journal.ledger.Ledger`` (SQLite, append-only).

Jede Zeile trägt: ``trace_id`` (je Instrument + UTC-Tag stabil), ``strategy_version``,
``config_hash`` (optional), ``step``, ``instrument``, ``ts`` und ein ``payload`` mit dem
verdichteten Entscheidungsinhalt (Decision-Typ, Score, Confidence, Reason-Codes, Entry/SL/TP,
Signal-Änderung, Positions-Event).
"""

from __future__ import annotations

import contextlib
from datetime import datetime
from typing import Any

from trading_agent.journal.ledger import Ledger
from trading_agent.runtime.bus import EventBus
from trading_agent.runtime.events import (
    AlertRaised,
    DecisionMade,
    PaperPositionChanged,
    SignalRevised,
)
from trading_agent.utils.logging import get_logger

_log = get_logger("decision_ledger")


def _decision_payload(result: object) -> dict[str, Any]:
    """Verdichtet ein ``EvaluationResult`` bzw. dessen ``Decision`` zu einem flachen Dict."""
    d = getattr(result, "decision", None)
    if d is None:
        return {}
    out: dict[str, Any] = {
        "decision": _enum(getattr(d, "decision", None)),
        "setup_state": _enum(getattr(d, "setup_state", None)),
        "direction": _enum(getattr(d, "direction", None)),
        "setup_id": getattr(d, "setup_id", None),
        "tier": _enum(getattr(d, "tier", None)),
        "score": getattr(d, "score", None),
        "confidence": getattr(d, "confidence", None),
        "entry": getattr(d, "entry", None),
        "sl": getattr(d, "sl", None),
        "tp1": getattr(d, "tp1", None),
        "tp2": getattr(d, "tp2", None),
        "tp3_ref": getattr(d, "tp3_ref", None),
        "rr_to_tp2": getattr(d, "rr_to_tp2", None),
        "blended_rr": getattr(d, "blended_rr", None),
        "reason_codes": [_enum(r) for r in getattr(d, "reason_codes", ()) or ()],
        "vetoes": [_enum(v) for v in getattr(d, "vetoes", ()) or ()],
        "chain_progress": getattr(d, "chain_progress", None),
    }
    return {k: v for k, v in out.items() if v is not None and v != []}


def _enum(v: object) -> Any:
    return getattr(v, "value", v)


class DecisionLedgerRecorder:
    """Abonniert den Bus und schreibt Decision-/Signal-/Position-Zeilen in den ``Ledger``."""

    def __init__(
        self,
        ledger: Ledger,
        *,
        strategy_version: str = "0.1.1",
        config_hash: str | None = None,
        code_sha: str | None = None,
    ) -> None:
        self.ledger = ledger
        self.strategy_version = strategy_version
        self.config_hash = config_hash
        self.code_sha = code_sha
        self.rows_written = 0

    def _trace_id(self, instrument: str, ts: datetime) -> str:
        return f"{instrument}-{ts.date().isoformat()}"

    def attach(self, bus: EventBus) -> None:
        bus.subscribe(DecisionMade, self._on_decision)
        bus.subscribe(SignalRevised, self._on_signal)
        bus.subscribe(PaperPositionChanged, self._on_position)
        bus.subscribe(AlertRaised, self._on_alert)

    async def _write(
        self, step: str, instrument: str, ts: datetime, payload: dict[str, Any]
    ) -> None:
        with contextlib.suppress(Exception):
            self.ledger.record_decision(
                step,
                trace_id=self._trace_id(instrument, ts),
                instrument=instrument,
                payload=payload,
                strategy_version=self.strategy_version,
                config_hash=self.config_hash,
                code_sha=self.code_sha,
                ts=ts,
            )
            self.rows_written += 1

    async def _on_decision(self, ev: DecisionMade) -> None:
        pl = _decision_payload(ev.result)
        pl.setdefault("decision", ev.decision_type)
        pl.setdefault("setup_state", ev.setup_state)
        pl.setdefault("score", ev.score)
        pl.setdefault("confidence", ev.confidence)
        await self._write("DECISION", ev.instrument, ev.ts, pl)

    async def _on_signal(self, ev: SignalRevised) -> None:
        await self._write(
            "SIGNAL",
            ev.instrument,
            ev.ts,
            {"signal_id": ev.signal_id, "state": ev.state, "change": ev.change},
        )

    async def _on_position(self, ev: PaperPositionChanged) -> None:
        await self._write(
            "POSITION",
            ev.instrument,
            ev.ts,
            {"change": ev.change, "realized_r": ev.realized_r},
        )

    async def _on_alert(self, ev: AlertRaised) -> None:
        await self._write(
            "ALERT",
            ev.instrument,
            ev.ts,
            {"alert_type": ev.alert_type, "message": ev.message, "delivered": ev.delivered},
        )


__all__ = ["DecisionLedgerRecorder"]
