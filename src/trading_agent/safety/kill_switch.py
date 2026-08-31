"""Hierarchischer Kill-Switch — blockiert **neue** Orders, fail-safe.

Ebenen (von hart nach fein): ``global`` › ``broker`` › ``asset`` › ``strategy`` › ``data``.
Ist eine Ebene ausgelöst, blockiert die Risk Engine jeden neuen Entry der betroffenen Reichweite.
Der Kill-Switch **schließt keine** offenen Positionen — er verhindert nur neue.

**Fail-safe:**

* Datei fehlt        → sauberer Start, nichts ausgelöst.
* Datei unlesbar/korrupt → ``global`` ausgelöst (im Zweifel sperren).
* Persistiert als JSON, damit der Zustand einen Neustart überlebt.

Kein Broker, keine Keys — reine lokale Sicherheits-Sperre.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

_LEVELS = ("global", "broker", "asset", "strategy", "data")


@dataclass(frozen=True, slots=True)
class KillSwitchState:
    global_: bool = False
    broker: bool = False
    asset: bool = False
    strategy: bool = False
    data: bool = False
    reason: str = ""
    updated_at: str = ""

    def tripped_levels(self) -> dict[str, bool]:
        return {
            "global": self.global_,
            "broker": self.broker,
            "asset": self.asset,
            "strategy": self.strategy,
            "data": self.data,
        }

    @property
    def any_tripped(self) -> bool:
        return any(self.tripped_levels().values())

    def to_json(self) -> dict[str, object]:
        return {
            "global": self.global_,
            "broker": self.broker,
            "asset": self.asset,
            "strategy": self.strategy,
            "data": self.data,
            "reason": self.reason,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_json(cls, data: dict[str, object]) -> KillSwitchState:
        return cls(
            global_=bool(data.get("global", False)),
            broker=bool(data.get("broker", False)),
            asset=bool(data.get("asset", False)),
            strategy=bool(data.get("strategy", False)),
            data=bool(data.get("data", False)),
            reason=str(data.get("reason", "")),
            updated_at=str(data.get("updated_at", "")),
        )


@dataclass(slots=True)
class KillSwitch:
    """Dateibasierter Kill-Switch. ``path`` ist die Persistenzdatei (JSON)."""

    path: Path
    _state: KillSwitchState = field(default_factory=KillSwitchState)

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        self._state = self._load()

    # ---- lesen ---------------------------------------------------------------
    @property
    def state(self) -> KillSwitchState:
        return self._state

    def _load(self) -> KillSwitchState:
        if not self.path.exists():
            return KillSwitchState()  # fail-safe: sauberer Start
        try:
            return KillSwitchState.from_json(json.loads(self.path.read_text()))
        except (OSError, ValueError, TypeError):
            # unlesbar/korrupt ⇒ im Zweifel global sperren
            return KillSwitchState(
                global_=True,
                reason="kill-switch state file unreadable/corrupt",
                updated_at=datetime.now(UTC).isoformat(),
            )

    # ---- schreiben ---------------------------------------------------------
    def trip(self, level: str, *, reason: str) -> KillSwitchState:
        if level not in _LEVELS:
            raise ValueError(f"unbekannte Kill-Switch-Ebene {level!r} (erlaubt: {_LEVELS})")
        cur = self._state.tripped_levels()
        cur[level] = True
        self._state = KillSwitchState(
            global_=cur["global"],
            broker=cur["broker"],
            asset=cur["asset"],
            strategy=cur["strategy"],
            data=cur["data"],
            reason=reason,
            updated_at=datetime.now(UTC).isoformat(),
        )
        self._persist()
        return self._state

    def reset(self, level: str | None = None, *, reason: str = "manual reset") -> KillSwitchState:
        """``level=None`` ⇒ alle Ebenen zurücksetzen (bewusster Eingriff)."""
        cur = self._state.tripped_levels()
        if level is None:
            cur = dict.fromkeys(_LEVELS, False)
        elif level in _LEVELS:
            cur[level] = False
        else:
            raise ValueError(f"unbekannte Ebene {level!r}")
        self._state = KillSwitchState(
            global_=cur["global"],
            broker=cur["broker"],
            asset=cur["asset"],
            strategy=cur["strategy"],
            data=cur["data"],
            reason=reason,
            updated_at=datetime.now(UTC).isoformat(),
        )
        self._persist()
        return self._state

    def _persist(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(self._state.to_json(), indent=2))
        tmp.replace(self.path)


__all__ = ["KillSwitch", "KillSwitchState"]
