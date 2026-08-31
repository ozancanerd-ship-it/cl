"""Phase 3 · Kosten-/Slippage-Modell für Paper-Positionen (``strategy.costs`` + ``strategy.position``).

Default 0.0 (nichts erfunden) · bps→R-Umrechnung · Entry-/Exit-Kosten · Funding · Long/Short ·
``realized_r`` wird netto, ``gross_realized_r`` bleibt brutto.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from trading_agent.core.enums import Direction, RiskTier
from trading_agent.strategy.costs import CostConfig, from_fee_schedule, funding_cost_r, leg_cost_r
from trading_agent.strategy.decision import Decision
from trading_agent.strategy.position import PositionManager, PositionParams, PriceBar

T0 = datetime(2024, 6, 3, 5, 0, tzinfo=UTC)


def _long(entry=100.0, sl=95.0, tp1=110.0, tp2=120.0) -> Decision:
    return Decision.trade(
        "BTCUSD", T0, Direction.LONG, entry=entry, sl=sl, tp1=tp1, tp2=tp2, tier=RiskTier.A
    )


def _bar(i, hi, lo, close=None) -> PriceBar:
    return PriceBar(
        T0 + timedelta(minutes=5 * i), hi, lo, close if close is not None else (hi + lo) / 2
    )


# --------------------------------------------------------------------------- costs unit


def test_default_is_zero() -> None:
    c = CostConfig()
    assert c.is_zero
    assert leg_cost_r(c, price=100.0, r_unit=5.0, is_maker=True).total_r == 0.0
    assert (
        funding_cost_r(
            c, price=100.0, r_unit=5.0, direction=Direction.LONG, bars_held=10, bar_seconds=300
        )
        == 0.0
    )


def test_leg_cost_bps_to_r() -> None:
    # entry 100, r_unit 5 → 1 bps am Preis = 0.01 Preis = 0.002 R
    c = CostConfig(taker_fee_bps=5.0, half_spread_bps=2.0, slippage_bps=1.0)
    lc = leg_cost_r(c, price=100.0, r_unit=5.0, is_maker=False)
    assert round(lc.fee_r, 6) == round(100.0 * 5e-4 / 5.0, 6)  # 0.01
    assert round(lc.spread_r, 6) == round(100.0 * 2e-4 / 5.0, 6)
    assert round(lc.slippage_r, 6) == round(100.0 * 1e-4 / 5.0, 6)
    assert round(lc.total_r, 6) == round((5 + 2 + 1) * 1e-4 * 100.0 / 5.0, 6)  # 0.016


def test_maker_vs_taker() -> None:
    c = CostConfig(taker_fee_bps=5.0, maker_fee_bps=1.0)
    assert (
        leg_cost_r(c, price=100.0, r_unit=5.0, is_maker=True).fee_r
        < leg_cost_r(c, price=100.0, r_unit=5.0, is_maker=False).fee_r
    )


def test_funding_long_pays_short_receives() -> None:
    c = CostConfig(funding_bps_per_day=10.0)
    long = funding_cost_r(
        c, price=100.0, r_unit=5.0, direction=Direction.LONG, bars_held=288, bar_seconds=300
    )
    short = funding_cost_r(
        c, price=100.0, r_unit=5.0, direction=Direction.SHORT, bars_held=288, bar_seconds=300
    )
    assert long > 0 and short < 0 and abs(long + short) < 1e-9  # 288 M5 = 1 Tag
    assert round(long, 6) == round(100.0 * 10e-4 / 5.0, 6)


def test_from_fee_schedule() -> None:
    c = from_fee_schedule(taker_bps=5.5, maker_bps=2.0, half_spread_bps=1.0)
    assert c.taker_fee_bps == 5.5 and c.maker_fee_bps == 2.0 and c.half_spread_bps == 1.0
    assert c.slippage_bps == 0.0 and c.funding_bps_per_day == 0.0  # nichts erfunden


# --------------------------------------------------------------------------- position integration


def test_position_zero_cost_unchanged() -> None:
    m = PositionManager()  # kein cost
    pos = m.open(_long(), at=T0, pending=False)
    u = m.on_bar(pos, _bar(1, 111, 101))  # TP1
    assert u.position.realized_r == u.position.gross_realized_r == 1.0
    assert u.position.total_cost_r == 0.0


def test_position_with_costs_reduces_realized() -> None:
    cost = CostConfig(taker_fee_bps=5.0, maker_fee_bps=2.0, half_spread_bps=1.0)
    m = PositionManager(cost=cost)
    pos = m.open(_long(), at=T0, pending=False)  # Entry = Maker
    assert pos.entry_cost_r > 0.0
    pos = m.on_bar(pos, _bar(1, 111, 101)).position  # TP1 (Exit = Taker)
    u = m.on_bar(pos, _bar(2, 96, 94))  # BE-Stop auf Rest
    p = u.position
    assert p.gross_realized_r > p.realized_r  # Kosten drücken das Netto
    assert p.fees_r > 0.0 and p.entry_cost_r > 0.0 and p.exit_cost_r > 0.0
    assert round(p.realized_r, 8) == round(
        p.gross_realized_r - p.entry_cost_r - p.exit_cost_r - p.funding_r, 8
    )


def test_position_funding_accrues_over_hold() -> None:
    cost = CostConfig(funding_bps_per_day=30.0)
    m = PositionManager(cost=cost, params=PositionParams(bar_seconds=300))
    pos = m.open(_long(), at=T0, pending=False)
    # 20 Bars halten, dann Stop
    for i in range(1, 21):
        r = m.on_bar(pos, _bar(i, 101, 99.5))
        pos = r.position
        if pos.state.is_terminal:
            break
    u = m.on_bar(pos, _bar(21, 100, 94))  # SL
    assert u.position.funding_r > 0.0  # Long zahlt Funding über die Haltedauer
    assert u.position.realized_r < u.position.gross_realized_r


def test_short_costs_symmetry() -> None:
    cost = CostConfig(taker_fee_bps=5.0, half_spread_bps=1.0)
    m = PositionManager(cost=cost)
    short = Decision.trade(
        "BTCUSD", T0, Direction.SHORT, entry=100.0, sl=105.0, tp1=90.0, tp2=80.0, tier=RiskTier.A
    )
    pos = m.open(short, at=T0, pending=False)
    u = m.on_bar(pos, _bar(1, 91, 89))  # TP1
    assert u.position.gross_realized_r > u.position.realized_r
    assert u.position.entry_cost_r > 0.0
