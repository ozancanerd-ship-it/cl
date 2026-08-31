"""``ValidationRegistry`` — welcher Setup-Typ ist für **Live-Signale** freigegeben?

Die Registry ist die einzige Autorität für Regel 3 des Masterplans („die Strategie dafür
validiert ist"). Sie wird aus ``config/setup_validation.json`` geladen; ohne Datei gilt der
konservative Default: **alles UNVALIDATED** (kein Setup darf ein actionable Live-Signal geben,
bis es nachweislich eine historische OOS-Edge gezeigt hat).

Zustände:

* ``UNVALIDATED``    — keine belegte OOS-Edge. Pipeline läuft, Signale = SHADOW.
* ``IN_VALIDATION``  — historische Edge belegt, sammelt jetzt Forward-/Paper-Trades (≥ N).
* ``VALIDATED``      — historische Edge belegt **und** ≥ N Forward-Trades bestätigen sie.
* ``EDGE_DEGRADED``  — war VALIDATED, aber ``assess_edge_health`` meldet BROKEN auf Recent-Daten.
* ``RETIRED``        — dauerhaft abgeschaltet.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path

from trading_agent.core.version import STRATEGY_VERSION
from trading_agent.governance.edge_health import BaselineMetrics

_DEFAULT_FORWARD_REQUIRED = 100  # Masterplan §44


class ValidationStatus(StrEnum):
    UNVALIDATED = "unvalidated"
    IN_VALIDATION = "in_validation"
    VALIDATED = "validated"
    EDGE_DEGRADED = "edge_degraded"
    RETIRED = "retired"


@dataclass(frozen=True, slots=True)
class SetupValidation:
    setup_id: str
    strategy_version: str
    status: ValidationStatus
    baseline: BaselineMetrics | None = None
    validated_window: tuple[str, str] | None = None
    forward_trades_required: int = _DEFAULT_FORWARD_REQUIRED
    notes: str = ""

    @property
    def live_allowed(self) -> bool:
        return self.status is ValidationStatus.VALIDATED

    def as_dict(self) -> dict[str, object]:
        return {
            "setup_id": self.setup_id,
            "strategy_version": self.strategy_version,
            "status": self.status.value,
            "baseline": self.baseline.as_dict() if self.baseline else None,
            "validated_window": list(self.validated_window) if self.validated_window else None,
            "forward_trades_required": self.forward_trades_required,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, d: dict[str, object]) -> SetupValidation:
        b = d.get("baseline")
        baseline: BaselineMetrics | None = None
        if isinstance(b, dict):
            baseline = BaselineMetrics(
                expectancy_r=float(b.get("expectancy_r", 0.0)),
                profit_factor=float(b.get("profit_factor", 0.0)),
                win_rate=float(b.get("win_rate", 0.0)),
                max_drawdown_r=float(b.get("max_drawdown_r", 0.0)),
                n_trades=int(b.get("n_trades", 0)),
            )
        w = d.get("validated_window")
        window = (str(w[0]), str(w[1])) if isinstance(w, list) and len(w) == 2 else None
        fwd_raw = d.get("forward_trades_required", _DEFAULT_FORWARD_REQUIRED)
        return cls(
            setup_id=str(d["setup_id"]),
            strategy_version=str(d.get("strategy_version", STRATEGY_VERSION)),
            status=ValidationStatus(str(d["status"])),
            baseline=baseline,
            validated_window=window,
            forward_trades_required=int(fwd_raw)
            if isinstance(fwd_raw, (int, float, str))
            else _DEFAULT_FORWARD_REQUIRED,
            notes=str(d.get("notes", "")),
        )


# Konservativer eingebauter Default (überschreibbar via config/setup_validation.json).
# Stand: `docs/STRATEGY-EDGE-INVESTIGATION-2026-08.md`.
_BUILTIN: dict[tuple[str, str], SetupValidation] = {
    ("SMC-SWEEP-REV-01", STRATEGY_VERSION): SetupValidation(
        setup_id="SMC-SWEEP-REV-01",
        strategy_version=STRATEGY_VERSION,
        status=ValidationStatus.UNVALIDATED,
        notes=(
            "Baseline OOS-kalibriert + gesperrt, aber ohne nachgewiesene OOS-Edge "
            "(Stufe B / Strategy-Edge-Investigation 2026-08). Live-Signale = SHADOW."
        ),
    ),
    # 2. Setup-Typ: historische OOS-Edge auf Gold *plausibel* (S4-Forschung), aber auf
    # indikativen Yahoo-Daten + kleiner OOS-Stichprobe → sammelt Forward-Trades.
    ("SETUP-BREAKOUT-RETEST-01", STRATEGY_VERSION): SetupValidation(
        setup_id="SETUP-BREAKOUT-RETEST-01",
        strategy_version=STRATEGY_VERSION,
        status=ValidationStatus.IN_VALIDATION,
        baseline=BaselineMetrics(
            expectancy_r=0.25, profit_factor=1.7, win_rate=0.53, max_drawdown_r=9.0, n_trades=47
        ),
        validated_window=("2023-11-14", "2026-08-28"),
        notes=(
            "S4 (Breakout+Retest, D1-Trend-Filter). EVIDENZ GEMISCHT: (+) Yahoo Gold-Futures + "
            "FX-Proxy + Crypto: OOS +0.40..+0.58R, WF 8-11/9-12, MC pp 0.55-0.65, alle 13 "
            "Parameter-Störungen OOS-positiv. (-) echte Dukascopy-Spot-XAUUSD (n=7, Lücke): "
            "-2.17R gesamt. Yahoo GC=F = Futures, nicht Spot. IN_VALIDATION → SHADOW. VALIDATED "
            "nur nach vollständiger Dukascopy-Historie mit + OOS UND ≥100 Forward-Trades."
        ),
    ),
}


class ValidationRegistry:
    def __init__(self, entries: dict[tuple[str, str], SetupValidation] | None = None) -> None:
        self._entries: dict[tuple[str, str], SetupValidation] = dict(_BUILTIN)
        if entries:
            self._entries.update(entries)

    # -------------------------------------------------------------- Fabriken

    @classmethod
    def default(cls) -> ValidationRegistry:
        return cls()

    @classmethod
    def from_file(cls, path: str | Path) -> ValidationRegistry:
        p = Path(path)
        if not p.exists():
            return cls()
        raw = json.loads(p.read_text(encoding="utf-8"))
        rows = raw["setups"] if isinstance(raw, dict) and "setups" in raw else raw
        entries: dict[tuple[str, str], SetupValidation] = {}
        for row in rows:
            sv = SetupValidation.from_dict(row)
            entries[(sv.setup_id, sv.strategy_version)] = sv
        return cls(entries)

    # -------------------------------------------------------------- Abfrage

    def get(self, setup_id: str, strategy_version: str = STRATEGY_VERSION) -> SetupValidation:
        hit = self._entries.get((setup_id, strategy_version))
        if hit is not None:
            return hit
        return SetupValidation(
            setup_id=setup_id,
            strategy_version=strategy_version,
            status=ValidationStatus.UNVALIDATED,
            notes="nicht in der Registry — konservativer Default UNVALIDATED",
        )

    def status_of(
        self, setup_id: str, strategy_version: str = STRATEGY_VERSION
    ) -> ValidationStatus:
        return self.get(setup_id, strategy_version).status

    def live_allowed(self, setup_id: str, strategy_version: str = STRATEGY_VERSION) -> bool:
        return self.get(setup_id, strategy_version).live_allowed

    def all(self) -> tuple[SetupValidation, ...]:
        return tuple(self._entries.values())

    # -------------------------------------------------------------- immutable Update

    def with_entry(self, sv: SetupValidation) -> ValidationRegistry:
        entries = dict(self._entries)
        entries[(sv.setup_id, sv.strategy_version)] = sv
        reg = ValidationRegistry()
        reg._entries = entries
        return reg

    def degrade(
        self, setup_id: str, strategy_version: str = STRATEGY_VERSION, *, reason: str = ""
    ) -> ValidationRegistry:
        cur = self.get(setup_id, strategy_version)
        note = f"{cur.notes} | EDGE_DEGRADED: {reason}".strip(" |")
        return self.with_entry(replace(cur, status=ValidationStatus.EDGE_DEGRADED, notes=note))


__all__ = ["SetupValidation", "ValidationRegistry", "ValidationStatus"]
