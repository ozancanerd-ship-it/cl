"""``AgentRunner`` — fährt mehrere Agenten über **denselben** ``as_of`` (deterministisch).

Sammelt die ``AgentReport``s ein und übergibt sie der zentralen Engine als Kontext-Bündel.
Der Runner **entscheidet nichts** und ruft **keine** Order-/Risk-Freigabe auf.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence
from datetime import datetime

from trading_agent.agents.base import Agent, AgentContext, AgentReport, AgentRole


@dataclasses.dataclass(frozen=True, slots=True)
class AgentBundle:
    """Alle Reports zu einem ``as_of``. Reiner Kontext für ``strategy.evaluate``-Aufrufer."""

    as_of: datetime
    reports: tuple[AgentReport, ...]
    errors: tuple[tuple[AgentRole, str], ...] = ()

    def by_role(self, role: AgentRole) -> AgentReport | None:
        return next((r for r in self.reports if r.role is role), None)

    @property
    def min_data_quality(self) -> float:
        return min((r.data_quality for r in self.reports), default=1.0)


class AgentRunner:
    def __init__(self, agents: Sequence[Agent]) -> None:
        self._agents = tuple(agents)

    def run(
        self, *, as_of: datetime, payloads: Mapping[AgentRole, Mapping[str, object]] | None = None
    ) -> AgentBundle:
        payloads = payloads or {}
        reports: list[AgentReport] = []
        errors: list[tuple[AgentRole, str]] = []
        for agent in self._agents:
            ctx = AgentContext(as_of=as_of, payload=payloads.get(agent.role, {}))
            try:
                rep = agent.observe(ctx)
            except NotImplementedError as exc:  # Rolle noch nicht implementiert — kein Fehler
                errors.append((agent.role, f"not_implemented: {exc}"))
                continue
            except Exception as exc:
                errors.append((agent.role, repr(exc)))
                continue
            if rep.as_of != as_of:
                errors.append((agent.role, f"as_of-Mismatch: {rep.as_of} != {as_of}"))
                continue
            reports.append(rep)
        return AgentBundle(as_of=as_of, reports=tuple(reports), errors=tuple(errors))


__all__ = ["AgentBundle", "AgentRunner"]
