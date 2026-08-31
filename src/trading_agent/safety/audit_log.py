"""Hash-verkettetes, append-only Audit-Log (Masterplan §51/§56).

Jede sicherheitsrelevante Handlung — Signal ausgegeben, Alert versendet, Kill-Switch
betätigt, Config geändert, (später) Order gesendet — wird als eine JSONL-Zeile
angehängt. Jede Zeile trägt den SHA-256 der **vorherigen** Zeile (`prev_hash`) und
ihren eigenen Hash (`entry_hash`). Manipulation an einer alten Zeile bricht die Kette
ab dort → `verify()` findet den Bruch.

Kein Löschen, kein Überschreiben. Reine Anhänge-Semantik (`open(path, "a")`).
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_GENESIS = "0" * 64


def _canonical(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _hash(prev_hash: str, body: str) -> str:
    return hashlib.sha256(f"{prev_hash}\n{body}".encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class AuditEntry:
    seq: int
    ts: str
    actor: str
    action: str
    detail: dict[str, Any]
    prev_hash: str
    entry_hash: str

    def as_line(self) -> str:
        return _canonical(
            {
                "seq": self.seq,
                "ts": self.ts,
                "actor": self.actor,
                "action": self.action,
                "detail": self.detail,
                "prev_hash": self.prev_hash,
                "entry_hash": self.entry_hash,
            }
        )


@dataclass(frozen=True, slots=True)
class VerifyResult:
    ok: bool
    entries: int
    broken_at: int | None = None
    reason: str = ""


class AuditLog:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._seq, self._tip = self._scan_tip()

    # ------------------------------------------------------------------ write

    def record(self, actor: str, action: str, detail: dict[str, Any] | None = None) -> AuditEntry:
        seq = self._seq + 1
        ts = datetime.now(UTC).isoformat()
        detail = detail or {}
        body = _canonical(
            {"seq": seq, "ts": ts, "actor": actor, "action": action, "detail": detail}
        )
        entry_hash = _hash(self._tip, body)
        entry = AuditEntry(
            seq=seq,
            ts=ts,
            actor=actor,
            action=action,
            detail=detail,
            prev_hash=self._tip,
            entry_hash=entry_hash,
        )
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(entry.as_line() + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        self._seq = seq
        self._tip = entry_hash
        return entry

    # ------------------------------------------------------------------ read

    def __iter__(self) -> Iterator[AuditEntry]:
        if not self.path.exists():
            return
        with open(self.path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                d = json.loads(line)
                yield AuditEntry(
                    seq=d["seq"],
                    ts=d["ts"],
                    actor=d["actor"],
                    action=d["action"],
                    detail=d["detail"],
                    prev_hash=d["prev_hash"],
                    entry_hash=d["entry_hash"],
                )

    @property
    def tip_hash(self) -> str:
        return self._tip

    @property
    def count(self) -> int:
        return self._seq

    def verify(self) -> VerifyResult:
        prev = _GENESIS
        n = 0
        expected_seq = 1
        for entry in self:
            n += 1
            if entry.seq != expected_seq:
                return VerifyResult(False, n, entry.seq, f"seq-Sprung: erwartet {expected_seq}")
            if entry.prev_hash != prev:
                return VerifyResult(False, n, entry.seq, "prev_hash passt nicht zur Kette")
            body = _canonical(
                {
                    "seq": entry.seq,
                    "ts": entry.ts,
                    "actor": entry.actor,
                    "action": entry.action,
                    "detail": entry.detail,
                }
            )
            if _hash(prev, body) != entry.entry_hash:
                return VerifyResult(
                    False, n, entry.seq, "entry_hash stimmt nicht (Zeile verändert)"
                )
            prev = entry.entry_hash
            expected_seq += 1
        return VerifyResult(True, n)

    # ------------------------------------------------------------------ intern

    def _scan_tip(self) -> tuple[int, str]:
        seq, tip = 0, _GENESIS
        for entry in self:
            seq, tip = entry.seq, entry.entry_hash
        return seq, tip


__all__ = ["AuditEntry", "AuditLog", "VerifyResult"]
