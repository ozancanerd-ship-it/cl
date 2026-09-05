"""Monitoring — Health-Checks, Heartbeats, Data-Source-Health.

Ebenfalls ein irrefuehrender Platzhalter: das Monitoring existiert, nur unter anderen Namen.
``SystemHealth`` haelt Provider-Health, Kill-Switch und Datensperren; ``MetricsRegistry``
zaehlt Heartbeats, Watchdog-Ticks, Feed-Fehler. Beide werden vom ``LiveSupervisor`` im
laufenden Betrieb gefuellt und stehen in dessen ``status()``.

Der Schwellen-Alarm darauf sitzt in :class:`~trading_agent.scanner.alerting.AlertBruecke`
(Live-Bus -> Telegram) und in ``scripts/scan_alert.py`` (Scan-Aenderung -> Telegram).
"""

from __future__ import annotations

from trading_agent.ops.health import SystemHealth
from trading_agent.ops.metrics import MetricsRegistry

__all__ = ["MetricsRegistry", "SystemHealth"]
