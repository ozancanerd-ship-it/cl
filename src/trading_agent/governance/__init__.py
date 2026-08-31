"""Governance — trennt die **Strategie-Entscheidung** von der **Freigabe-Entscheidung**.

Architektur-Prinzip (Masterplan, 2026-08-31):

* **Historische Daten** = Validierung. Nie direkte Preisprognose.
* **Live-Daten** = Entscheidung. „Wie sieht der Markt JETZT aus?"
* **Recent-Daten** = Adaptation / Regime-Check. Funktioniert die historisch gefundene Edge noch?
* **Paper-/Forward-Daten** = laufende Validierung.

Ein Setup darf ein **actionable** 🔥 BUY/SELL nur erzeugen, wenn:

1. der Live-Markt ein gültiges Setup zeigt   (→ ``strategy.evaluate`` / ``Decision``)
2. der Market-Context passt                  (→ ``strategy.evaluate``)
3. **die Strategie dafür validiert ist**      (→ ``ValidationRegistry``)
4. das R:R sinnvoll ist                        (→ Gates)
5. das Risiko akzeptabel ist                   (→ Risk-Engine)
6. keine wichtigen Gegenargumente bestehen     (→ Contradictions/Veto)
   **und** die Edge aktuell noch trägt         (→ ``assess_edge_health`` auf Recent/Forward-Daten)

Punkt 3 + Edge-Health sind hier. Ist ein Setup nicht validiert, produziert die Pipeline
weiterhin die volle Analyse + ein **SHADOW-Signal** (sichtbar, forward-getrackt) — aber kein
actionable Live-Signal.
"""

from trading_agent.governance.apply import apply_live_gate
from trading_agent.governance.edge_health import (
    BaselineMetrics,
    EdgeHealth,
    EdgeHealthReport,
    assess_edge_health,
)
from trading_agent.governance.live_gate import (
    LiveEligibility,
    LiveGateReport,
    evaluate_live_gate,
)
from trading_agent.governance.validation import (
    SetupValidation,
    ValidationRegistry,
    ValidationStatus,
)

__all__ = [
    "BaselineMetrics",
    "EdgeHealth",
    "EdgeHealthReport",
    "LiveEligibility",
    "LiveGateReport",
    "SetupValidation",
    "ValidationRegistry",
    "ValidationStatus",
    "apply_live_gate",
    "assess_edge_health",
    "evaluate_live_gate",
]
