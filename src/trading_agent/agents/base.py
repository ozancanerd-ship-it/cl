"""Agent-Basis: ``Finding`` · ``AgentReport`` · ``AgentContext`` · ``Agent``-Protokoll.

Alle Modelle sind frozen und **Point-in-Time**: ``AgentReport.as_of`` ist der
``information_cutoff``, zu dem der Report gilt. Ein Agent ist eine **reine** Beobachtung —
``observe(ctx) -> AgentReport``, kein Seiteneffekt, kein Netz-Zugriff im Aufruf (Daten kommen
über den ``AgentContext``).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable


class AgentRole(StrEnum):
    MARKET = "market"
    NEWS_MACRO = "news_macro"
    PORTFOLIO = "portfolio"
    RISK = "risk"
    RESEARCH = "research"
    VALIDATION = "validation"
    MONITORING = "monitoring"


class FindingSeverity(StrEnum):
    INFO = "info"
    NOTABLE = "notable"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclasses.dataclass(frozen=True, slots=True)
class Finding:
    """Eine einzelne Beobachtung eines Agenten.

    ``confidence`` in ``[0, 1]`` ist die Selbsteinschätzung des Agenten — sie geht **nicht** in
    Score/Confidence der Pipeline und **nicht** in die RiskEngine ein. Sie dient der zentralen
    Engine als Priorisierungs-/Anzeige-Hilfe.
    """

    claim: str  # was der Agent beobachtet ("D1-Struktur = TREND_UP", "FOMC in 40 min")
    evidence: str  # worauf es sich stützt (kurz, nachvollziehbar)
    severity: FindingSeverity = FindingSeverity.INFO
    confidence: float = 0.0
    instrument: str | None = None
    tags: tuple[str, ...] = ()
    data: Mapping[str, object] = dataclasses.field(default_factory=dict)

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence muss in [0,1] liegen, ist {self.confidence}")


@dataclasses.dataclass(frozen=True, slots=True)
class AgentReport:
    """Was ein Agent zu einem ``as_of``-Zeitpunkt liefert. Reiner Kontext für die Engine."""

    role: AgentRole
    as_of: datetime  # = information_cutoff; nur Fakten mit available_time <= as_of
    findings: tuple[Finding, ...] = ()
    data_quality: float = 1.0  # 0..1 — wie belastbar ist die Grundlage des Reports
    horizon: str = "intraday"  # "intraday" | "swing" | "macro"
    note: str = ""

    def __post_init__(self) -> None:
        if not 0.0 <= self.data_quality <= 1.0:
            raise ValueError("data_quality muss in [0,1] liegen")

    @property
    def worst_severity(self) -> FindingSeverity:
        order = list(FindingSeverity)
        return max(
            (f.severity for f in self.findings),
            key=order.index,
            default=FindingSeverity.INFO,
        )

    def of_severity(self, at_least: FindingSeverity) -> tuple[Finding, ...]:
        order = list(FindingSeverity)
        cut = order.index(at_least)
        return tuple(f for f in self.findings if order.index(f.severity) >= cut)


@dataclasses.dataclass(frozen=True, slots=True)
class AgentContext:
    """Alles, was ein Agent zum Beobachten braucht — **schon PIT-gefiltert** vom Aufrufer.

    Bewusst lose typisiert (``object``): die konkreten Typen (``MtfContext``,
    ``PortfolioLedger``, ``AccountState``, Kalibrier-Reports …) hängen von der Rolle ab und
    sollen dieses Basismodul nicht an die halbe Codebase koppeln.
    """

    as_of: datetime
    instrument: str | None = None
    payload: Mapping[str, object] = dataclasses.field(default_factory=dict)


@runtime_checkable
class Agent(Protocol):
    """Ein Informations-Agent. Reine Funktion: gleicher Kontext ⇒ gleicher Report."""

    role: AgentRole

    def observe(self, ctx: AgentContext) -> AgentReport: ...


__all__ = [
    "Agent",
    "AgentContext",
    "AgentReport",
    "AgentRole",
    "Finding",
    "FindingSeverity",
]
