"""Tests: Multi-Agent-Datenverträge — reine Architektur, kein Agent entscheidet/handelt."""

from __future__ import annotations

import dataclasses

import pytest

from trading_agent.agents.base import (
    Agent,
    AgentContext,
    AgentReport,
    AgentRole,
    Finding,
    FindingSeverity,
)
from trading_agent.agents.roles import ALL_ROLES, MarketAgent
from trading_agent.agents.runner import AgentRunner
from trading_agent.core.time import parse_timestamp

AS_OF = parse_timestamp("2025-01-02T00:00:00Z")


def test_finding_confidence_bounds() -> None:
    with pytest.raises(ValueError):
        Finding(claim="x", evidence="y", confidence=1.5)


def test_report_severity_helpers() -> None:
    rep = AgentReport(
        role=AgentRole.MARKET,
        as_of=AS_OF,
        findings=(
            Finding("a", "e", FindingSeverity.INFO),
            Finding("b", "e", FindingSeverity.WARNING),
        ),
    )
    assert rep.worst_severity is FindingSeverity.WARNING
    assert len(rep.of_severity(FindingSeverity.WARNING)) == 1


def test_all_role_stubs_are_not_implemented() -> None:
    for cls in ALL_ROLES:
        with pytest.raises(NotImplementedError):
            cls().observe(AgentContext(as_of=AS_OF))
    assert MarketAgent().role is AgentRole.MARKET


def test_runner_collects_reports_and_isolates_errors() -> None:
    @dataclasses.dataclass
    class _Good:
        role = AgentRole.MONITORING

        def observe(self, ctx: AgentContext) -> AgentReport:
            return AgentReport(role=self.role, as_of=ctx.as_of, data_quality=0.7)

    class _Stale:
        role = AgentRole.PORTFOLIO

        def observe(self, ctx: AgentContext) -> AgentReport:
            return AgentReport(role=self.role, as_of=parse_timestamp("2020-01-01T00:00:00Z"))

    agents: list[Agent] = [_Good(), _Stale(), MarketAgent()]
    bundle = AgentRunner(agents).run(as_of=AS_OF)

    assert bundle.by_role(AgentRole.MONITORING) is not None
    assert bundle.min_data_quality == 0.7
    roles_with_errors = {r for r, _ in bundle.errors}
    assert roles_with_errors == {AgentRole.PORTFOLIO, AgentRole.MARKET}  # stale + not_implemented


def test_runner_is_deterministic() -> None:
    b1 = AgentRunner([MarketAgent()]).run(as_of=AS_OF)
    b2 = AgentRunner([MarketAgent()]).run(as_of=AS_OF)
    assert b1 == b2
