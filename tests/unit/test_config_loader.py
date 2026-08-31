"""Tests: Config-Loader – Schema-Version, Validierung, Hash."""

from __future__ import annotations

from pathlib import Path

import pytest

from trading_agent.config.loader import (
    ConfigError,
    config_hash,
    load_data_foundation_config,
    load_yaml,
)

REPO_CONFIG = Path(__file__).parents[2] / "config" / "config.example.yaml"


def _write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "cfg.yaml"
    p.write_text(text)
    return p


class TestLoadYaml:
    def test_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError):
            load_yaml(tmp_path / "nope.yaml")

    def test_missing_schema_version(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError, match="schema_version"):
            load_yaml(_write(tmp_path, "mode: development\n"))

    def test_unsupported_schema_version(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError, match="schema_version"):
            load_yaml(_write(tmp_path, "schema_version: 999\nmode: development\n"))

    def test_non_mapping(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError):
            load_yaml(_write(tmp_path, "- a\n- b\n"))


class TestConfigHash:
    def test_deterministic_and_order_independent(self) -> None:
        a = {"schema_version": 1, "mode": "development", "x": [1, 2]}
        b = {"x": [1, 2], "mode": "development", "schema_version": 1}
        assert config_hash(a) == config_hash(b)

    def test_changes_with_content(self) -> None:
        assert config_hash({"schema_version": 1, "mode": "development"}) != config_hash(
            {"schema_version": 1, "mode": "paper"}
        )


class TestDataFoundationConfig:
    def test_loads_repo_example(self) -> None:
        cfg = load_data_foundation_config(REPO_CONFIG)
        assert cfg.mode == "development"
        assert "BTCUSDT" in cfg.enabled_symbols
        assert "ETHUSDT" in cfg.enabled_symbols
        assert cfg.timeframes.htf  # nicht leer
        assert cfg.data.timezone == "UTC"

    def test_live_mode_rejected(self, tmp_path: Path) -> None:
        text = (
            "schema_version: 1\nmode: live\n"
            "instruments:\n  - symbol: BTCUSDT\n    asset_class: crypto\n    quote_currency: USDT\n"
            "timeframes:\n  htf: [D1, H4]\n"
        )
        with pytest.raises(ConfigError, match="live"):
            load_data_foundation_config(_write(tmp_path, text))

    def test_empty_instruments_rejected(self, tmp_path: Path) -> None:
        text = "schema_version: 1\nmode: development\ninstruments: []\ntimeframes:\n  htf: [D1]\n"
        with pytest.raises(ConfigError):
            load_data_foundation_config(_write(tmp_path, text))

    def test_empty_htf_rejected(self, tmp_path: Path) -> None:
        text = (
            "schema_version: 1\nmode: development\n"
            "instruments:\n  - symbol: BTCUSDT\n    asset_class: crypto\n    quote_currency: USDT\n"
            "timeframes:\n  htf: []\n"
        )
        with pytest.raises(ConfigError):
            load_data_foundation_config(_write(tmp_path, text))

    def test_non_utc_timezone_rejected(self, tmp_path: Path) -> None:
        text = (
            "schema_version: 1\nmode: development\n"
            "instruments:\n  - symbol: BTCUSDT\n    asset_class: crypto\n    quote_currency: USDT\n"
            "timeframes:\n  htf: [D1]\n"
            "data:\n  timezone: Europe/Berlin\n"
        )
        with pytest.raises(ConfigError):
            load_data_foundation_config(_write(tmp_path, text))

    def test_unknown_asset_class_rejected(self, tmp_path: Path) -> None:
        text = (
            "schema_version: 1\nmode: development\n"
            "instruments:\n  - symbol: XYZ\n    asset_class: banana\n    quote_currency: USD\n"
            "timeframes:\n  htf: [D1]\n"
        )
        with pytest.raises(ConfigError):
            load_data_foundation_config(_write(tmp_path, text))
