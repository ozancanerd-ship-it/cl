"""Tests: provider-/asset-spezifische Kostenprofile — Provenance sauber getrennt, kein Fake."""

from __future__ import annotations

from trading_agent.core.enums import AssetClass
from trading_agent.refdata.seed import build_instrument_master
from trading_agent.strategy.cost_profiles import ZERO, estimate_profile, profile_for


def test_zero_profile_is_gross() -> None:
    assert ZERO.config.is_zero
    assert ZERO.provenance == "zero"
    assert not ZERO.is_measured


def test_estimate_profile_marks_provenance_and_leaves_funding_zero() -> None:
    p = estimate_profile(AssetClass.CRYPTO)
    assert p.provenance == "estimate_conservative"
    assert p.config.taker_fee_bps > 0 and p.config.half_spread_bps > 0
    assert p.config.funding_bps_per_day == 0.0  # braucht echte Historie
    assert not p.is_measured
    # Altcoin teurer als Crypto-Tier-1
    assert estimate_profile(AssetClass.ALTCOIN).config.slippage_bps > p.config.slippage_bps


def test_profile_for_uses_real_fee_schedule_only_by_default() -> None:
    btc = build_instrument_master().get("BTCUSDT")
    p = profile_for(btc)
    assert p.provenance == "exchange_schedule"
    assert p.config.taker_fee_bps == btc.fees.taker_bps
    assert p.config.maker_fee_bps == btc.fees.maker_bps
    assert p.config.half_spread_bps == 0.0 and p.config.slippage_bps == 0.0


def test_profile_for_with_estimates_combines_and_labels() -> None:
    sol = build_instrument_master().get("SOLUSDT")
    p = profile_for(sol, use_estimates=True)
    assert p.provenance == "exchange_schedule+estimate"
    assert p.config.taker_fee_bps == sol.fees.taker_bps  # echte Gebühr bleibt
    assert p.config.slippage_bps > 0  # geschätzte Slippage dazu
    assert "NICHT gemessen" in p.note


def test_measured_overrides_bump_provenance() -> None:
    eth = build_instrument_master().get("ETHUSDT")
    p = profile_for(eth, use_estimates=True, slippage_bps=0.7, funding_bps_per_day=1.2)
    assert p.provenance.endswith("+measured")
    assert p.config.slippage_bps == 0.7
    assert p.config.funding_bps_per_day == 1.2
