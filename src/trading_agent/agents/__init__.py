"""Multi-Agent-Datenverträge — **nur Architektur**, keine autonomen Trading-Gehirne.

Agenten liefern **Informationen** (``AgentReport``) an die zentrale Decision Engine
(``strategy.evaluate`` + ``risk.risk_engine``). Sie entscheiden **nichts** und lösen **keine**
Trades aus.

* Ein ``AgentReport`` ist Kontext, **kein** Score-Override.
* Die ``RiskEngine`` sieht **keine** Agenten-Confidence — sie prüft nur Konto/Portfolio/Limits.
* Ein Hard-Veto / No-Trade bleibt hart — ein Agent kann ``data_quality`` senken oder eine
  ``Contradiction`` beisteuern, aber nichts freigeben.

Dieses Paket enthält bewusst **keine** Agenten-Logik — nur die Protokolle/Modelle.
"""

from trading_agent.agents.base import (
    Agent,
    AgentContext,
    AgentReport,
    AgentRole,
    Finding,
    FindingSeverity,
)

__all__ = [
    "Agent",
    "AgentContext",
    "AgentReport",
    "AgentRole",
    "Finding",
    "FindingSeverity",
]
