#!/usr/bin/env bash
# Lint + Typecheck + Tests ohne `make` (GNU make ist Teil der Xcode CLT und hier nicht nötig).
set -euo pipefail
cd "$(dirname "$0")/.."

PY=.venv/bin
echo "=== 1/3 ruff (lint + format-check) ==="
"$PY/ruff" check .
"$PY/ruff" format --check .

echo "=== 2/3 mypy (strict) ==="
"$PY/mypy"

echo "=== 3/3 pytest ==="
"$PY/pytest" "$@"

echo "=== alle Prüfungen bestanden ==="
