"""State-Persistenz — **atomarer JSON-Snapshot** für den 24/7-Supervisor (M-01).

Zweck: nach einem geplanten Shutdown **oder** einem Absturz/Laptop-Sleep exakt dort
weitermachen, wo der Prozess aufgehört hat — ohne doppelte Events, ohne doppelte Paper-
Positionen, ohne Datenverlust (soweit per REST-Backfill erreichbar).

* **Atomar:** Schreiben in eine ``*.tmp``-Datei + ``os.replace`` — ein Crash mitten im Schreiben
  lässt den alten Snapshot intakt.
* **Versioniert:** ``schema_version`` in jedem Snapshot; ein unbekannter/älterer Snapshot wird
  **verworfen** (fail-safe Neustart), nicht halb geladen.
* **Cloud-fähig:** nur ein Pfad nötig (lokal ein Verzeichnis, in der Cloud ein Volume-Mount).
  Keine DB, kein Netz.

Kein Fake: der Snapshot hält **nur** echten Laufzeit-Zustand. Fehlt er, startet der Supervisor
sauber „von vorn" (Warmup + Prime), nichts wird erfunden.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import tempfile
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from trading_agent.core.time import ensure_utc

_log = logging.getLogger("trading_agent.state.store")

SNAPSHOT_SCHEMA_VERSION = 1


class SnapshotStore:
    """Ein Snapshot je ``name`` unter ``root`` (z. B. ``live_supervisor.json``)."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def _path(self, name: str) -> Path:
        return self.root / f"{name}.json"

    def save(self, name: str, payload: dict[str, Any]) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        body = {
            "schema_version": SNAPSHOT_SCHEMA_VERSION,
            "saved_at": ensure_utc(datetime.now().astimezone()).isoformat(),
            "payload": payload,
        }
        target = self._path(name)
        fd, tmp = tempfile.mkstemp(dir=self.root, prefix=f".{name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(body, fh, default=to_jsonable, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, target)
        finally:
            if os.path.exists(tmp):  # pragma: no cover - nur bei Schreibfehler
                os.unlink(tmp)
        return target

    def load(self, name: str) -> dict[str, Any] | None:
        path = self._path(name)
        if not path.exists():
            return None
        try:
            body = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            _log.warning("snapshot unreadable — starte sauber neu", extra={"err": str(exc)})
            return None
        if body.get("schema_version") != SNAPSHOT_SCHEMA_VERSION:
            _log.warning(
                "snapshot schema mismatch — verworfen",
                extra={"found": body.get("schema_version"), "want": SNAPSHOT_SCHEMA_VERSION},
            )
            return None
        result: dict[str, Any] = body.get("payload", {})
        result["_saved_at"] = body.get("saved_at")
        return result

    def clear(self, name: str) -> None:
        with __import__("contextlib").suppress(FileNotFoundError):
            self._path(name).unlink()


# --------------------------------------------------------------------------- JSON-Serde


def to_jsonable(obj: Any) -> Any:
    """``json.dump(default=…)``-Hook: Enums → ``.value``, datetime → ISO, dataclass → dict."""
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, datetime):
        return ensure_utc(obj).isoformat()
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: getattr(obj, f.name) for f in dataclasses.fields(obj)}
    if isinstance(obj, set | frozenset):
        return sorted(obj, key=str)
    raise TypeError(f"nicht serialisierbar: {type(obj).__name__}")


def dataclass_to_dict(obj: Any) -> dict[str, Any]:
    """Flache, JSON-taugliche dict-Darstellung einer (verschachtelten) Dataclass-Instanz."""
    out: dict[str, Any] = json.loads(json.dumps(obj, default=to_jsonable))
    return out


__all__ = [
    "SNAPSHOT_SCHEMA_VERSION",
    "SnapshotStore",
    "dataclass_to_dict",
    "to_jsonable",
]
