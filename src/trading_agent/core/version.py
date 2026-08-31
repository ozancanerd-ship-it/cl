"""Versionskonstanten für nachvollziehbare Datensätze und Records.

Jedes persistierte Datenmodell trägt ``schema_version``. Ändert sich die Struktur eines
Modells inkompatibel, wird ``SCHEMA_VERSION`` erhöht und eine Migration dokumentiert.

``STRATEGY_VERSION`` kommt aus der eingefrorenen Spezifikation (aktuell
``docs/strategy/DECISIONS-0.1.1.md``, Vorgänger ``…-0.1.0.md``) und wird ab Phase 3 im Code
geführt.
"""

from __future__ import annotations

# Schema-Version aller Kern-Datenmodelle (core.models, refdata.models).
# Historie:
#   1  – 2026-08-28  Phase 1: erste Data-Foundation-Modelle.
SCHEMA_VERSION: int = 1

# Version des Datensatz-Layouts im Repository (Verzeichnis-/Partitionsstruktur unter data/).
REPOSITORY_LAYOUT_VERSION: int = 1

# Eingefrorene Strategie-Spezifikations-Version (docs/strategy/DECISIONS-*.md).
STRATEGY_VERSION: str = "0.1.1"

__all__ = ["REPOSITORY_LAYOUT_VERSION", "SCHEMA_VERSION", "STRATEGY_VERSION"]
