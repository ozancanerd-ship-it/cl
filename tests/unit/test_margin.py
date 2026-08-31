"""Tests: ``risk.margin`` — isolated-linear Liquidation, Long/Short-Symmetrie, mmr-Näherung."""

from __future__ import annotations

import math

import pytest

from trading_agent.core.enums import Side
from trading_agent.risk.margin import (
    estimate_liquidation,
    initial_margin,
    liquidation_price,
    max_leverage_for_liq_distance,
)


def test_initial_margin() -> None:
    assert initial_margin(10_000.0, 10.0) == 1000.0
    assert initial_margin(10_000.0, 0.5) == 10_000.0  # Hebel < 1 wird auf 1 geklemmt


def test_liq_price_zero_mmr_matches_entry_over_leverage() -> None:
    # mmr=0 ⇒ Preisbewegung bis Liquidation = entry/leverage
    long = liquidation_price(entry=100.0, side=Side.BUY, leverage=5.0)
    assert math.isclose(100.0 - long, 20.0)
    short = liquidation_price(entry=100.0, side=Side.SELL, leverage=5.0)
    assert math.isclose(short - 100.0, 20.0)


def test_mmr_makes_liquidation_closer() -> None:
    no_mmr = liquidation_price(entry=100.0, side=Side.BUY, leverage=10.0)
    with_mmr = liquidation_price(
        entry=100.0, side=Side.BUY, leverage=10.0, maintenance_margin_rate=0.005
    )
    assert with_mmr > no_mmr  # Long: Liquidation liegt höher (näher am Entry)
    assert math.isclose(100.0 - with_mmr, 100.0 * (0.1 - 0.005))


def test_estimate_liquidation_fields() -> None:
    est = estimate_liquidation(entry=2000.0, side=Side.BUY, leverage=4.0, atr=50.0)
    assert math.isclose(est.distance_price, 500.0)
    assert math.isclose(est.distance_pct, 25.0)
    assert math.isclose(est.distance_atr, 10.0)
    assert est.reachable


def test_max_leverage_for_liq_distance_inverts_correctly() -> None:
    # min_distance 10% des Preises, mmr=0 ⇒ max Hebel 10x
    lev = max_leverage_for_liq_distance(entry=100.0, min_distance=10.0)
    assert math.isclose(lev, 10.0)
    # mit mmr wird der erlaubte Hebel kleiner
    lev2 = max_leverage_for_liq_distance(
        entry=100.0, min_distance=10.0, maintenance_margin_rate=0.05
    )
    assert lev2 < lev
    # Konsistenz: bei diesem Hebel ist der Abstand genau min_distance
    est = estimate_liquidation(entry=100.0, side=Side.SELL, leverage=lev)
    assert math.isclose(est.distance_price, 10.0, rel_tol=1e-9)


def test_invalid_inputs() -> None:
    with pytest.raises(ValueError):
        liquidation_price(entry=0.0, side=Side.BUY, leverage=5.0)
    with pytest.raises(ValueError):
        max_leverage_for_liq_distance(entry=100.0, min_distance=0.0)
