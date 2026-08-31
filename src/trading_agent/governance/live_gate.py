"""``evaluate_live_gate`` — die Freigabe-Entscheidung, getrennt von der Strategie-Entscheidung.

``strategy.evaluate`` sagt: *„der Live-Markt zeigt JETZT ein gültiges ARMED-Setup mit Tier X."*
Dieses Modul sagt: *„darf daraus ein **actionable** 🔥 BUY/SELL werden — oder nur ein SHADOW?"*

    LIVE     — Setup VALIDATED **und** Edge-Health nicht BROKEN  → actionable
    SHADOW   — Setup UNVALIDATED / IN_VALIDATION / Edge-Health unbekannt → nur tracken
    BLOCKED  — Setup EDGE_DEGRADED / RETIRED **oder** Edge-Health BROKEN → auch SHADOW unterdrücken

Die Governance verändert die ``Decision`` nicht — sie hängt einen ``LiveGateReport`` an das
``EvaluationResult``. Renderer (Signal-Report, Daemon, UI) entscheiden anhand von
``eligibility``, ob 🔥 BUY oder „⚠️ SHADOW — nicht validiert" gezeigt wird.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from trading_agent.governance.edge_health import EdgeHealth, EdgeHealthReport
from trading_agent.governance.validation import ValidationRegistry, ValidationStatus


class LiveEligibility(StrEnum):
    LIVE = "live"
    SHADOW = "shadow"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class LiveGateReport:
    eligibility: LiveEligibility
    validation_status: ValidationStatus
    edge_health: EdgeHealth | None
    forward_trades_seen: int
    forward_trades_required: int
    reasons: tuple[str, ...]

    @property
    def is_live(self) -> bool:
        return self.eligibility is LiveEligibility.LIVE

    @property
    def render_tag(self) -> str:
        return {
            LiveEligibility.LIVE: "LIVE",
            LiveEligibility.SHADOW: "SHADOW · nicht validiert",
            LiveEligibility.BLOCKED: "BLOCKED · Edge nicht intakt",
        }[self.eligibility]

    def as_dict(self) -> dict[str, object]:
        return {
            "eligibility": self.eligibility.value,
            "validation_status": self.validation_status.value,
            "edge_health": self.edge_health.value if self.edge_health else None,
            "forward_trades_seen": self.forward_trades_seen,
            "forward_trades_required": self.forward_trades_required,
            "reasons": list(self.reasons),
        }


def evaluate_live_gate(
    setup_id: str,
    strategy_version: str,
    *,
    registry: ValidationRegistry,
    edge_health: EdgeHealthReport | None = None,
    forward_trades_seen: int = 0,
) -> LiveGateReport:
    sv = registry.get(setup_id, strategy_version)
    eh = edge_health.health if edge_health is not None else None
    reasons: list[str] = []

    if sv.status in (ValidationStatus.RETIRED, ValidationStatus.EDGE_DEGRADED):
        reasons.append(f"Setup-Status {sv.status.value}")
        elig = LiveEligibility.BLOCKED
    elif eh is EdgeHealth.BROKEN:
        reasons.append("Edge-Health BROKEN auf Recent-Daten")
        if edge_health is not None:
            reasons.extend(edge_health.reasons[:2])
        elig = LiveEligibility.BLOCKED
    elif sv.status is ValidationStatus.VALIDATED:
        if eh is EdgeHealth.WEAKENING:
            reasons.append("VALIDATED, aber Edge-Health WEAKENING — enger beobachten")
        else:
            reasons.append("Setup VALIDATED, Edge-Health intakt/ok")
        elig = LiveEligibility.LIVE
    else:  # UNVALIDATED / IN_VALIDATION
        reasons.append(
            f"Setup {sv.status.value} — historische OOS-Edge "
            + (
                "belegt, sammelt Forward-Trades"
                if sv.status is ValidationStatus.IN_VALIDATION
                else "nicht belegt"
            )
        )
        if sv.status is ValidationStatus.IN_VALIDATION:
            reasons.append(f"{forward_trades_seen}/{sv.forward_trades_required} Forward-Trades")
        elig = LiveEligibility.SHADOW

    return LiveGateReport(
        eligibility=elig,
        validation_status=sv.status,
        edge_health=eh,
        forward_trades_seen=forward_trades_seen,
        forward_trades_required=sv.forward_trades_required,
        reasons=tuple(reasons),
    )


__all__ = ["LiveEligibility", "LiveGateReport", "evaluate_live_gate"]
