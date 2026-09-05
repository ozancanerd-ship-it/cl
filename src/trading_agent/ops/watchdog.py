"""Watchdog — laeuft, aber nicht hier.

Der periodische Health-Check ist ``LiveSupervisor._watchdog_loop``: alle 20 Sekunden
Provider-Health aus stale Daten, WS-Neustarts und Feed-Fehlern ableiten, Datensperren je
Instrument setzen, ``watchdog_ticks_total`` hochzaehlen. Dazu ein Heartbeat alle 10 Sekunden
und ein Snapshot jede Minute.

Bewusst **kein** Kill-Switch im Paper-Betrieb: eine stumme Datenquelle degradiert die
Provider-Health, sie stoppt das System aber nicht. Der Kill-Switch ist fuer Echtgeld
reserviert und dort an die Verlustgrenzen gebunden (``config/risk.yaml``).

Diese Datei bleibt als Wegweiser stehen, damit niemand den Watchdog neu baut.
"""

from __future__ import annotations

from trading_agent.runtime.supervisor import LiveSupervisor

__all__ = ["LiveSupervisor"]
