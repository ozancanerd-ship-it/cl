"""Konfiguration laden, validieren, versionieren.

* YAML -> Dict -> typisiertes Pydantic-Modell (Fail-fast bei ungültiger Config).
* Jede Config-Datei trägt ``schema_version``; unbekannte/fehlende Version -> Fehler.
* ``config_hash`` liefert einen deterministischen Fingerabdruck (für Reproduzierbarkeit /
  ``RunManifest`` ab Phase 2).

Phase 1 typisiert nur den Data-Foundation-relevanten Teil von ``config.example.yaml``.
Strategie-/Risk-/Scoring-Config-Modelle kommen in späteren Phasen dazu.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from trading_agent.core.enums import AssetClass, Timeframe

SUPPORTED_SCHEMA_VERSIONS: frozenset[int] = frozenset({1})


class ConfigError(ValueError):
    pass


def load_yaml(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        raise ConfigError(f"Config-Datei nicht gefunden: {p}")
    with p.open() as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ConfigError(f"{p}: erwartet ein YAML-Mapping, erhielt {type(data).__name__}")
    version = data.get("schema_version")
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        raise ConfigError(
            f"{p}: schema_version {version!r} nicht unterstützt "
            f"(bekannt: {sorted(SUPPORTED_SCHEMA_VERSIONS)})"
        )
    return data


def config_hash(mapping: dict[str, Any]) -> str:
    """Deterministischer SHA-256 über die kanonisierte Config."""
    blob = json.dumps(mapping, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


# --------------------------------------------------------------------------------------------
# Typisierte Teilmodelle (Data Foundation)
# --------------------------------------------------------------------------------------------


class InstrumentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str
    asset_class: AssetClass
    quote_currency: str
    settle_currency: str | None = None
    enabled: bool = False


class TimeframesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    htf: list[Timeframe]
    context: list[Timeframe] = Field(default_factory=list)
    entry: list[Timeframe] = Field(default_factory=list)
    execution_detail: list[Timeframe] = Field(default_factory=list)

    @field_validator("htf")
    @classmethod
    def _htf_not_empty(cls, v: list[Timeframe]) -> list[Timeframe]:
        if not v:
            raise ValueError("timeframes.htf darf nicht leer sein")
        return v


class DataConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = "mock"
    csv_dir: str = "data/raw"
    repository_dir: str = "data/repository"
    timezone: str = "UTC"
    reject_on_gaps: bool = True
    max_bar_age_factor: float = 1.5
    gap_lookback_bars: int = 50

    @field_validator("timezone")
    @classmethod
    def _utc_only(cls, v: str) -> str:
        if v != "UTC":
            raise ValueError("interne Zeitzone muss 'UTC' sein")
        return v


class LoggingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level: str = "INFO"
    format: str = "json"
    redact_secrets: bool = True


class DataFoundationConfig(BaseModel):
    """Der in Phase 1 benötigte, validierte Ausschnitt der Hauptkonfiguration.

    ``extra="ignore"``: spätere Abschnitte (``strategy``, ``sessions``, ``scanner`` …) werden
    hier nicht geprüft – dafür gibt es ab der jeweiligen Phase eigene Modelle.
    """

    model_config = ConfigDict(extra="ignore")

    schema_version: int
    mode: str
    strategy_version: str | None = None
    instruments: list[InstrumentConfig]
    timeframes: TimeframesConfig
    data: DataConfig = Field(default_factory=DataConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

    @field_validator("mode")
    @classmethod
    def _mode_allowed(cls, v: str) -> str:
        allowed = {"development", "research", "backtest", "paper", "paper_live", "demo"}
        if v not in allowed:
            raise ValueError(
                f"mode {v!r} not allowed in this phase (allowed: {sorted(allowed)}); "
                "'live' is excluded"
            )
        return v

    @field_validator("instruments")
    @classmethod
    def _instruments_not_empty(cls, v: list[InstrumentConfig]) -> list[InstrumentConfig]:
        if not v:
            raise ValueError("mindestens ein Instrument erforderlich")
        return v

    @property
    def enabled_symbols(self) -> list[str]:
        return [i.symbol for i in self.instruments if i.enabled]


def load_data_foundation_config(path: str | Path) -> DataFoundationConfig:
    raw = load_yaml(path)
    try:
        return DataFoundationConfig.model_validate(raw)
    except Exception as exc:
        raise ConfigError(f"{path}: ungültige Konfiguration: {exc}") from exc


__all__ = [
    "SUPPORTED_SCHEMA_VERSIONS",
    "ConfigError",
    "DataConfig",
    "DataFoundationConfig",
    "InstrumentConfig",
    "LoggingConfig",
    "TimeframesConfig",
    "config_hash",
    "load_data_foundation_config",
    "load_yaml",
]
