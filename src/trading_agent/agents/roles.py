"""Die sieben Agenten-Rollen — **Verträge, keine Logik**.

Jede Klasse fixiert Rolle + erwarteten ``AgentContext.payload`` und implementiert noch nichts
(``observe`` wirft ``NotImplementedError``). So steht die Schnittstelle fest, ohne dass jetzt
ein autonomes Trading-Gehirn entsteht.

Die zentrale Engine bleibt die einzige entscheidende Instanz (``strategy.evaluate`` +
``risk.risk_engine``). Agenten liefern nur ``AgentReport``.
"""

from __future__ import annotations

from trading_agent.agents.base import AgentContext, AgentReport, AgentRole


class _AgentBase:
    role: AgentRole
    #: Pflicht-Schlüssel in ``AgentContext.payload`` (Doku; nicht erzwungen)
    expects: tuple[str, ...] = ()

    def observe(self, ctx: AgentContext) -> AgentReport:  # pragma: no cover - Vertrag
        raise NotImplementedError(
            f"{type(self).__name__}.observe ist noch nicht implementiert "
            "(Multi-Agent = derzeit nur Architektur)."
        )


class MarketAgent(_AgentBase):
    """Struktur / Regime / Liquidität je Instrument. Quelle: ``analysis.mtf.build_mtf_context``."""

    role = AgentRole.MARKET
    expects = ("mtf_context",)


class NewsMacroAgent(_AgentBase):
    """Event-Fenster, Pre-Positioning-Ban, Überraschungs-Score, Risk-off.
    Quelle: ``data.providers.fred_alfred`` / ``news_calendar`` (PIT)."""

    role = AgentRole.NEWS_MACRO
    expects = ("news_context", "macro_events", "asset_class")


class PortfolioAgent(_AgentBase):
    """Offene Heat, Cluster-Auslastung, Korrelations-Warnungen.
    Quelle: ``portfolio.engine.PortfolioLedger``."""

    role = AgentRole.PORTFOLIO
    expects = ("ledger",)


class RiskAgent(_AgentBase):
    """Verbleibendes Tagesbudget, DD-Abstand, Kill-Switch-Status, Limit-Nähe.
    Quelle: ``risk.risk_engine`` / ``AccountState``. **Liefert nur Info** — die harte Prüfung
    macht weiterhin ``RiskEngine.review``."""

    role = AgentRole.RISK
    expects = ("account_state", "risk_limits", "kill_switch_state")


class ResearchAgent(_AgentBase):
    """„Dieses Setup in diesem Regime: OOS-Erwartung."
    Quelle: ``data/repository_real/*_calibration.json`` (read-only)."""

    role = AgentRole.RESEARCH
    expects = ("calibration_reports", "regime", "setup_id")


class ValidationAgent(_AgentBase):
    """Konsistenz-Checks, Leakage-/Data-Snooping-Verdacht, Kontra-Indikation.
    Heimat der Continuous-Improvement-Checks. Quelle: Decision + alle Sub-Reports + ``parity``."""

    role = AgentRole.VALIDATION
    expects = ("evaluation_result", "parity_report")


class MonitoringAgent(_AgentBase):
    """Feed-Health, Latenz, Drift, „Engine still healthy". Quelle: ``data.health`` + Telemetrie."""

    role = AgentRole.MONITORING
    expects = ("health_snapshot", "telemetry")


ALL_ROLES: tuple[type[_AgentBase], ...] = (
    MarketAgent,
    NewsMacroAgent,
    PortfolioAgent,
    RiskAgent,
    ResearchAgent,
    ValidationAgent,
    MonitoringAgent,
)


__all__ = [
    "ALL_ROLES",
    "MarketAgent",
    "MonitoringAgent",
    "NewsMacroAgent",
    "PortfolioAgent",
    "ResearchAgent",
    "RiskAgent",
    "ValidationAgent",
]
