"""Phase 4 · Risk Engine (``risk.limits`` / ``risk.position_sizing`` / ``risk.risk_engine`` /
``safety.kill_switch``).

Invariante: die Risk Engine kann nur **ablehnen oder verkleinern** — nie aus WAIT/NO_TRADE einen
Trade machen, nie durch Score/Confidence überstimmt werden. Plus alle harten Limits + Sizing.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from trading_agent.core.enums import Direction, RiskTier
from trading_agent.core.types import OpenPositionInfo, PortfolioContext
from trading_agent.risk.limits import RiskLimits
from trading_agent.risk.position_sizing import SizingInputs, size_position
from trading_agent.risk.risk_engine import AccountState, RiskEngine, RiskOutcome
from trading_agent.safety.kill_switch import KillSwitch, KillSwitchState
from trading_agent.strategy.decision import Decision

T0 = datetime(2024, 6, 3, 5, 0, tzinfo=UTC)


def _buy(entry=100.0, sl=98.0, tier=RiskTier.A, score=99.0, conf=0.99) -> Decision:
    return Decision.trade(
        "BTCUSDT",
        T0,
        Direction.LONG,
        entry=entry,
        sl=sl,
        tp1=104.0,
        tp2=108.0,
        tier=tier,
        score=score,
        confidence=conf,
    )


def _acc(**kw) -> AccountState:
    base = dict(equity=10_000.0)
    base.update(kw)
    return AccountState(**base)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- Invariante


def test_pass_through_on_non_actionable() -> None:
    from trading_agent.core.enums import NoTradeReason, SetupState

    eng = RiskEngine()
    nt = Decision.no_trade("BTCUSDT", T0, [NoTradeReason.REGIME_UNCLEAR])
    wait = Decision.wait("BTCUSDT", T0, SetupState.SWEPT)
    for d in (nt, wait):
        v = eng.review(d, account=_acc())
        assert v.outcome is RiskOutcome.PASS_THROUGH
        assert v.size is None and not v.approved

    # kein Weg, aus NO_TRADE ein APPROVED zu machen — auch nicht mit „perfektem" Score
    assert eng.review(nt, account=_acc()).outcome is RiskOutcome.PASS_THROUGH


def test_high_score_does_not_override_limit() -> None:
    eng = RiskEngine()
    d = _buy(score=100.0, conf=1.0, tier=RiskTier.A_PLUS)
    v = eng.review(d, account=_acc(drawdown_pct=15.0))  # DD über Limit
    assert v.blocks and "max_drawdown" in v.reasons


# --------------------------------------------------------------------------- Kill-Switch


def test_kill_switch_blocks(tmp_path: Path) -> None:
    eng = RiskEngine()
    ks = KillSwitchState(global_=True, reason="test")
    v = eng.review(_buy(), account=_acc(), kill_switch=ks)
    assert v.blocks and "kill_switch:global" in v.reasons


def test_kill_switch_file_fail_safe(tmp_path: Path) -> None:
    p = tmp_path / "ks.json"
    assert not KillSwitch(p).state.any_tripped  # fehlt → sauber
    p.write_text("{ not json")
    assert KillSwitch(p).state.global_  # korrupt → global gesperrt
    ks = KillSwitch(tmp_path / "ks2.json")
    ks.trip("strategy", reason="dd")
    assert KillSwitch(tmp_path / "ks2.json").state.strategy  # persistiert
    ks.reset()
    assert not KillSwitch(tmp_path / "ks2.json").state.any_tripped


# --------------------------------------------------------------------------- Konto-Limits


def test_daily_and_weekly_loss_limits() -> None:
    eng = RiskEngine()
    assert eng.review(_buy(), account=_acc(daily_loss_pct=3.5)).blocks
    assert eng.review(_buy(), account=_acc(weekly_loss_pct=7.0)).blocks
    assert eng.review(_buy(), account=_acc(trades_today=6)).blocks
    assert eng.review(_buy(), account=_acc(consecutive_losses=4)).blocks
    assert eng.review(_buy(), account=_acc()).approved  # nichts ausgelöst


def test_not_checked_when_state_missing() -> None:
    v = RiskEngine().review(_buy(), account=AccountState(equity=10_000.0))
    assert v.approved
    assert "daily_loss" in v.not_checked and "drawdown" in v.not_checked


def test_no_equity_rejects() -> None:
    v = RiskEngine().review(_buy(), account=AccountState())
    assert v.blocks and "no_account_equity" in v.reasons


# --------------------------------------------------------------------------- Portfolio-Struktur


def test_max_open_positions() -> None:
    pf = PortfolioContext(
        open_positions=(
            OpenPositionInfo("ETHUSDT", Direction.LONG, 0.5),
            OpenPositionInfo("SOLUSDT", Direction.LONG, 0.5),
            OpenPositionInfo("XRPUSDT", Direction.LONG, 0.5),
        )
    )
    assert RiskEngine().review(_buy(), account=_acc(), portfolio=pf).blocks


def test_opposite_and_duplicate_position() -> None:
    eng = RiskEngine()
    short_open = PortfolioContext(
        open_positions=(OpenPositionInfo("BTCUSDT", Direction.SHORT, 0.5),)
    )
    assert (
        "opposite_position_open" in eng.review(_buy(), account=_acc(), portfolio=short_open).reasons
    )
    long_open = PortfolioContext(open_positions=(OpenPositionInfo("BTCUSDT", Direction.LONG, 0.5),))
    assert "duplicate_position" in eng.review(_buy(), account=_acc(), portfolio=long_open).reasons


def test_portfolio_heat_reduces_size() -> None:
    eng = RiskEngine(RiskLimits(max_total_open_risk_pct=1.0))
    pf = PortfolioContext(
        open_positions=(OpenPositionInfo("ETHUSDT", Direction.LONG, 0.7),),
        total_open_risk_pct=0.7,
    )
    v = eng.review(_buy(tier=RiskTier.A_PLUS), account=_acc(), portfolio=pf)  # A+ will 1.0 %
    assert v.approved
    assert v.size is not None and v.size.risk_pct <= 0.3 + 1e-6  # nur noch 0.3 % Headroom
    assert "portfolio_heat" in v.size.capped_by


# --------------------------------------------------------------------------- Sizing


def test_sizing_hard_max_cap() -> None:
    inp = SizingInputs(
        equity=10_000.0, entry=100.0, stop_loss=99.0, tier=RiskTier.A_PLUS, size_multiplier=5.0
    )
    s = size_position(inp, RiskLimits(hard_max_risk_pct=2.0))
    assert s.tradable and s.risk_pct <= 2.0 + 1e-6 and "hard_max_risk_pct" in s.capped_by


def test_sizing_risk_amount_matches_sl_distance() -> None:
    # 1 % von 10k = 100 Risiko; SL-Distanz 2 → qty = 50
    s = size_position(
        SizingInputs(equity=10_000.0, entry=100.0, stop_loss=98.0, tier=RiskTier.A_PLUS)
    )
    assert round(s.risk_amount, 2) == 100.0
    assert round(s.quantity, 6) == 50.0
    assert s.r_unit == 2.0


def test_sizing_leverage_bounded_by_broker_limit() -> None:
    s = size_position(
        SizingInputs(
            equity=10_000.0,
            entry=100.0,
            stop_loss=99.5,
            tier=RiskTier.A_PLUS,
            available_margin=200.0,
        ),
        RiskLimits(max_leverage=10.0),
    )
    assert s.tradable and s.leverage <= 10.0 + 1e-6


def test_sizing_below_min_not_tradable() -> None:
    s = size_position(
        SizingInputs(
            equity=100.0, entry=100.0, stop_loss=99.0, tier=RiskTier.B, min_notional=1_000.0
        )
    )
    assert not s.tradable and "below_min_notional" in s.capped_by


def test_no_trade_tier_yields_zero() -> None:
    s = size_position(
        SizingInputs(equity=10_000.0, entry=100.0, stop_loss=99.0, tier=RiskTier.NO_TRADE)
    )
    assert not s.tradable
