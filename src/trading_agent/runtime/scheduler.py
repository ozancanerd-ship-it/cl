"""Scan-Orchestrierung — ereignisgetrieben statt getaktet.

Der urspruenglich geplante Scheduler ist nie gebaut worden, weil er nicht gebraucht wird:
die ``LivePipeline`` arbeitet auf Bar-Close-Events, und der ``MarketScanner`` haengt am selben
Bus. Es gibt also keinen Takt, der Arbeit anstoesst — die geschlossene Kerze tut es.

Zwei Wege, wie im Projekt tatsaechlich gescannt wird:

* **laufend** — ``scripts/run_live_daemon.py``: WS-Stream -> Bar-Close -> MTF -> Strategie ->
  Decision -> Signal. Der ``MarketScanner`` bewertet dabei jedes Instrument neu.
* **stossweise** — ``scripts/build_scan_data.py``: der Gesamtmarkt (Krypto, Aktien, Gold) in
  einem Durchgang, aufgerufen vom CI-Zeitplan. Das ist der Weg, der ohne eigenen Server
  funktioniert.

Diese Datei bleibt als Wegweiser stehen.
"""

from __future__ import annotations

__all__: list[str] = []
