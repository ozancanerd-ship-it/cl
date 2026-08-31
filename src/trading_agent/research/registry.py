"""Experiment registry: every run gets a RunManifest and a deterministic output hash.

Guards against cherry-picking and makes reproducibility checkable:
same manifest inputs  ->  same output hash.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _git_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return os.environ.get("TRADING_AGENT_CODE_SHA", "nogit")


@dataclass(frozen=True, slots=True)
class RunManifest:
    strategy_version: str
    config_hash: str
    dataset_version: str
    dataset_fingerprint: str
    instrument: str
    timeframe: str
    start: str
    end: str
    seed: int
    params: dict[str, Any] = field(default_factory=dict)
    code_sha: str = field(default_factory=_git_sha)
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def manifest_hash(self) -> str:
        """Hash of the *inputs* only (excludes created_at)."""
        payload = {k: v for k, v in asdict(self).items() if k != "created_at"}
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(blob.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class RunRecord:
    manifest: RunManifest
    output_hash: str
    metrics: dict[str, float | int]


class RunRegistry:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, record: RunRecord) -> Path:
        run_id = record.manifest.manifest_hash()[:16]
        path = self.root / f"{run_id}.json"
        path.write_text(
            json.dumps(
                {
                    "manifest": asdict(record.manifest),
                    "manifest_hash": record.manifest.manifest_hash(),
                    "output_hash": record.output_hash,
                    "metrics": record.metrics,
                },
                indent=2,
                default=str,
            )
        )
        return path

    def count(self) -> int:
        return len(list(self.root.glob("*.json")))


def output_hash(rows: list[dict[str, Any]]) -> str:
    """Deterministic hash over an ordered list of result rows (e.g. trades)."""
    h = hashlib.sha256()
    for row in rows:
        h.update(json.dumps(row, sort_keys=True, separators=(",", ":"), default=str).encode())
    return h.hexdigest()


__all__ = ["RunManifest", "RunRecord", "RunRegistry", "output_hash"]
