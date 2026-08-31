"""Strukturiertes JSON-Logging mit Secret-Redaction.

* Ein Log-Eintrag = eine JSON-Zeile (maschinenlesbar, gut für spätere Aggregation).
* **Keine Secrets im Log.** Ein Redaction-Filter ersetzt Werte von Schlüsseln, die nach
  Zugangsdaten aussehen (``*key*``, ``*secret*``, ``*token*``, ``*password*``, ``authorization``),
  und maskiert API-Key-artige Zeichenketten im freien Text.
* Zeitstempel in UTC (ISO-8601).
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

_SENSITIVE_KEY = re.compile(
    r"(key|secret|token|password|passwd|pwd|authorization|api[_-]?key|access[_-]?token)",
    re.IGNORECASE,
)
# grobe Heuristik für versehentlich geloggte Schlüssel/Tokens im Klartext
_SECRET_LIKE = re.compile(r"\b[A-Za-z0-9_\-]{32,}\b")
_REDACTED = "***REDACTED***"

_RESERVED = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime", "taskName"}


def redact(value: Any) -> Any:
    """Rekursiv Secrets in Dicts/Listen/Strings maskieren."""
    if isinstance(value, dict):
        out: dict[Any, Any] = {}
        for k, v in value.items():
            if isinstance(k, str) and _SENSITIVE_KEY.search(k):
                out[k] = _REDACTED
            else:
                out[k] = redact(v)
        return out
    if isinstance(value, (list, tuple)):
        return type(value)(redact(v) for v in value)
    if isinstance(value, str):
        return _SECRET_LIKE.sub(_REDACTED, value)
    return value


class JsonFormatter(logging.Formatter):
    """Formatiert Records als eine JSON-Zeile."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)

        extra = {
            k: v for k, v in record.__dict__.items() if k not in _RESERVED and not k.startswith("_")
        }
        if extra:
            payload["ctx"] = redact(extra)

        return json.dumps(redact(payload), default=str, ensure_ascii=False)


_CONFIGURED = False


def configure_logging(level: str | int = "INFO", *, stream: Any | None = None) -> None:
    """Root-Logger auf JSON + Redaction stellen. Mehrfachaufruf ist harmlos (idempotent)."""
    global _CONFIGURED
    root = logging.getLogger()
    root.setLevel(level)
    if not _CONFIGURED:
        handler = logging.StreamHandler(stream)
        handler.setFormatter(JsonFormatter())
        root.handlers.clear()
        root.addHandler(handler)
        _CONFIGURED = True
    else:
        for h in root.handlers:
            h.setLevel(level)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


__all__ = ["JsonFormatter", "configure_logging", "get_logger", "redact"]
