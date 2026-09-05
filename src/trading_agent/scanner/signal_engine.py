"""Signal-Engine — der Ort, an dem aus einer Entscheidung ein konkretes Signal wird.

Diese Datei war ein Platzhalter aus dem alten Plan und behauptete „noch nicht implementiert".
Das stimmte nicht: die Signal-Erzeugung liegt seit M-01 in ``strategy/signal_report.py`` und
wird vom Live-Daemon und vom Signal-Journal benutzt. Der Platzhalter hat den Projektstand
falsch dargestellt — deshalb zeigt die Datei jetzt auf den echten Code, statt ihn zu leugnen.

Wo was liegt:

* :func:`build_signal_report` — Entry/SL/TP1-3, R:R, Score, Confidence, Begruendung,
  Invalidierung. Das feste Format aus Masterplan Punkt 6.
* :func:`apply_live_gate` — die Freigabe-Autoritaet: ohne validiertes Setup bleibt das
  Signal SHADOW. Kein Signal wird "live", weil es gut aussieht.
* :class:`~trading_agent.scanner.opportunity.OpportunityScore` — der 0-100-Score.
* :class:`~trading_agent.scanner.chart_score.ChartChance` — der Chart-Zustand fuer den
  Gesamtmarkt-Scan, auch ohne fertiges Setup.
"""

from __future__ import annotations

from trading_agent.governance import apply_live_gate
from trading_agent.strategy.signal_report import build_signal_report

__all__ = ["apply_live_gate", "build_signal_report"]
