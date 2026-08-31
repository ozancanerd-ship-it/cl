"""``trace_id``-Propagation über ``contextvars`` — ein Scan/Signal/Trade-Vorgang zieht sich als
eine ID durch Scan → Setup → Signal → Approval → Order → Fill → Management → Exit.

Kein globaler Zustand: ``contextvars`` ist task-lokal (asyncio-sicher). Ohne aktiven Trace
liefert ``current_trace_id()`` ``None`` — der Aufrufer entscheidet, ob das ein Fehler ist.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator
from contextvars import ContextVar

_TRACE_ID: ContextVar[str | None] = ContextVar("trace_id", default=None)


def new_trace_id(prefix: str = "t") -> str:
    """Neue, kurze, eindeutige Trace-ID (``<prefix>-<12 hex>``)."""
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def current_trace_id() -> str | None:
    return _TRACE_ID.get()


def set_trace_id(trace_id: str | None) -> None:
    """Setzt die Trace-ID für den aktuellen Kontext (ohne Reset-Token — für Einstiegspunkte)."""
    _TRACE_ID.set(trace_id)


@contextlib.contextmanager
def trace(trace_id: str | None = None, *, prefix: str = "t") -> Iterator[str]:
    """Kontextmanager: setzt eine Trace-ID für den Block und stellt danach die vorige wieder her.

    with trace() as tid:
        ...  # current_trace_id() == tid
    """
    tid = trace_id or new_trace_id(prefix)
    token = _TRACE_ID.set(tid)
    try:
        yield tid
    finally:
        _TRACE_ID.reset(token)


__all__ = ["current_trace_id", "new_trace_id", "set_trace_id", "trace"]
