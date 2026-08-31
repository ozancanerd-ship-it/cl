.PHONY: help install test cov lint format typecheck check clean lock

PY := .venv/bin/python
PYTEST := .venv/bin/pytest
RUFF := .venv/bin/ruff
MYPY := .venv/bin/mypy

help:
	@echo "install    - venv anlegen + Projekt (editable) + dev-Abhängigkeiten (uv)"
	@echo "test       - Tests ausführen (pytest)"
	@echo "cov        - Tests mit Coverage-Report"
	@echo "lint       - ruff Lint"
	@echo "format     - ruff Autoformat + Autofix"
	@echo "typecheck  - mypy"
	@echo "check      - lint + typecheck + test"
	@echo "lock       - Dependency-Lockfile schreiben (uv.lock)"
	@echo "clean      - Caches entfernen"

install:
	uv venv --python 3.12
	uv pip install -e ".[dev]"

lock:
	uv lock

test:
	$(PYTEST)

cov:
	$(PYTEST) --cov=trading_agent --cov-report=term-missing

lint:
	$(RUFF) check .

format:
	$(RUFF) format .
	$(RUFF) check --fix .

typecheck:
	$(MYPY)

check: lint typecheck test

clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache htmlcov .coverage
	find . -type d -name __pycache__ -exec rm -rf {} +
