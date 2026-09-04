"""Kostenmodell je Symbol — Befund F4 aus docs/INDEPENDENT-METHOD-AUDIT-2026-09-03.md."""

from __future__ import annotations

import pytest

from trading_agent.research.costs import CostModel, load_cost_model

_DOC = {
    "classes": {
        "spot": {
            "taker_fee_pct": 0.10,
            "slippage_atr_frac": 0.04,
            "min_slippage_pct": 0.02,
            "tradeable": True,
        },
        "indicative": {
            "taker_fee_pct": 0.0,
            "slippage_atr_frac": 0.03,
            "min_slippage_pct": 0.008,
            "tradeable": False,
        },
    },
    "symbols": {
        "BTCUSDT": {"class": "spot"},
        "WILD": {"class": "spot", "slippage_atr_frac": 0.10},
        "EURUSD-YF": {"class": "indicative"},
    },
    "fallback": {"class": "spot"},
}


@pytest.fixture
def model() -> CostModel:
    return CostModel(_DOC)


def test_fee_and_slippage_are_charged_on_both_sides(model: CostModel) -> None:
    # entry 100, ATR 10 -> Gebuehr 0.10 je Seite, Slippage max(0.02, 0.4) = 0.4 je Seite
    c = model.for_symbol("BTCUSDT")
    assert c.cost_quote(entry=100.0, atr=10.0) == pytest.approx(2 * (0.10 + 0.40))


def test_cost_in_r_scales_with_stop_distance(model: CostModel) -> None:
    """Der Kernfehler von F4: ein enger Stop macht dieselben Kosten teurer."""
    tight = model.cost_r("BTCUSDT", entry=100.0, atr=10.0, r_unit=4.0)
    wide = model.cost_r("BTCUSDT", entry=100.0, atr=10.0, r_unit=40.0)
    assert tight == pytest.approx(wide * 10)
    assert tight > wide


def test_low_volatility_symbol_is_more_expensive_per_r() -> None:
    """Gold: niedrigste relative Volatilitaet -> engster Stop -> hoechster Kostenanteil."""
    m = CostModel(_DOC)
    # gleiches Preisniveau, unterschiedliche ATR; Stop jeweils 0.8 x ATR
    lowvol = m.cost_r("BTCUSDT", entry=1000.0, atr=7.0, r_unit=0.8 * 7.0)
    highvol = m.cost_r("BTCUSDT", entry=1000.0, atr=70.0, r_unit=0.8 * 70.0)
    assert lowvol > highvol


def test_min_slippage_floor_applies_when_atr_is_tiny(model: CostModel) -> None:
    c = model.for_symbol("BTCUSDT")
    # ATR-Anteil 0.04*0.1 = 0.004 liegt unter dem Boden 0.02 % von 100 = 0.02
    assert c.cost_quote(entry=100.0, atr=0.1) == pytest.approx(2 * (0.10 + 0.02))


def test_symbol_override_beats_class(model: CostModel) -> None:
    assert model.for_symbol("WILD").slippage_atr_frac == 0.10
    assert model.for_symbol("BTCUSDT").slippage_atr_frac == 0.04
    assert model.for_symbol("WILD").taker_fee_pct == 0.10  # aus der Klasse geerbt


def test_indicative_series_are_marked_not_tradeable(model: CostModel) -> None:
    assert model.is_tradeable("BTCUSDT") is True
    assert model.is_tradeable("EURUSD-YF") is False


def test_unknown_symbol_falls_back_and_is_not_cheaper(model: CostModel) -> None:
    unknown = model.cost_r("NEVERHEARDOF", entry=100.0, atr=10.0, r_unit=8.0)
    known = model.cost_r("BTCUSDT", entry=100.0, atr=10.0, r_unit=8.0)
    assert unknown >= known


def test_zero_or_negative_r_unit_is_free_not_infinite(model: CostModel) -> None:
    assert model.cost_r("BTCUSDT", entry=100.0, atr=10.0, r_unit=0.0) == 0.0
    assert model.cost_r("BTCUSDT", entry=100.0, atr=10.0, r_unit=-1.0) == 0.0


def test_shipped_config_loads_and_covers_the_panel() -> None:
    m = load_cost_model("config/costs.yaml")
    for sym in ("XAUUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT", "SEIUSDT", "EURUSD-YF"):
        assert m.for_symbol(sym).taker_fee_pct >= 0.0
    assert m.is_tradeable("XAUUSDT") is True
    assert m.is_tradeable("XAUUSD-YF") is False


def test_shipped_config_reproduces_the_audit_magnitudes() -> None:
    """XAUUSDT auf H4 muss deutlich ueber der alten Pauschale von 0.03 R liegen."""
    m = load_cost_model("config/costs.yaml")
    price, atr_h4 = 3300.0, 3300.0 * 0.00708
    cost = m.cost_r("XAUUSDT", entry=price, atr=atr_h4, r_unit=0.8 * atr_h4)
    assert 0.30 < cost < 0.60, cost
    assert cost > 10 * 0.03
