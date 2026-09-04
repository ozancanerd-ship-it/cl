#!/bin/bash
# Der eine taegliche Lauf: aufzeichnen -> Plan bauen -> aufs Handy schicken.
#
# Zwei Schritte, bewusst in dieser Reihenfolge und in EINEM Job:
#   1. tsmom_forward.py  schreibt die Journalzeile fuer heute (die saubere Datenspur)
#   2. daily_report.py   uebersetzt sie in Euro und schickt sie per Telegram
#
# Schritt 2 sendet nur, wenn sich etwas geaendert hat. Kein taegliches "alles gleich".
# Schlaegt Schritt 1 fehl, laeuft Schritt 2 trotzdem — dann eben auf der letzten Zeile,
# und der Report sagt selbst, von wann sie ist.

set -uo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"

PY="$REPO/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"
export PYTHONPATH="$REPO/src"

echo "=== $(date '+%Y-%m-%d %H:%M:%S %Z') ==="

echo "--- 1/2 Forward-Aufzeichnung ---"
"$PY" scripts/tsmom_forward.py || echo "! Aufzeichnung fehlgeschlagen — Report laeuft trotzdem"

echo "--- 2/2 Tagesplan ---"
"$PY" scripts/daily_report.py --send
