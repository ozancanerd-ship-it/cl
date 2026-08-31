"""Phase 4 · Portfolio-Ledger (``portfolio.engine``).

Equity-Fortschreibung · Tages-/Wochen-Loss · Drawdown (peak-basiert) · offene 1R-Summe ·
Cluster-Risiko · Loss-Streak · Rollover · Erzeugung von AccountState + PortfolioContext.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from trading_agent.core.enums import Direction
from trading_agent.portfolio.engine import ClusterMap, PortfolioLedger

T0 = datetime(2025, 1, 6, 9, 0, tzinfo=UTC)  # Montag


def _ledger() -> PortfolioLedger:
    return PortfolioLedger(
        starting_equity=10_000.0,
        clusters=ClusterMap(
            by_instrument={"BTCUSDT": "crypto_majors", "ETHUSDT": "crypto_majors"},
            static_correlations={("BTCUSDT", "ETHUSDT"): 0.85},
        ),
    )


def test_open_close_equity_and_streak() -> None:
    lg = _ledger()
    lg.roll_time(T0)
    lg.on_open("BTCUSDT", Direction.LONG, risk_amount=100.0, entry_ts=T0)
    assert round(lg.total_open_risk_pct, 4) == 1.0  # 100 / 10000
    lg.on_close("BTCUSDT", realized_pnl=-100.0)  # −1R
    assert lg.equity == 9_900.0
    lg.on_open("ETHUSDT", Direction.LONG, risk_amount=100.0, entry_ts=T0)
    lg.on_close("ETHUSDT", realized_pnl=-100.0)
    acc = lg.to_account_state()
    assert acc.consecutive_losses == 2
    assert round(acc.daily_loss_pct, 2) == 2.0  # 200 / 10000
    lg.on_open("BTCUSDT", Direction.LONG, risk_amount=100.0, entry_ts=T0)
    lg.on_close("BTCUSDT", realized_pnl=+250.0)  # Gewinn → Streak reset
    assert lg.to_account_state().consecutive_losses == 0


def test_drawdown_peak_based() -> None:
    lg = _ledger()
    lg.roll_time(T0)
    lg.on_open("BTCUSDT", Direction.LONG, risk_amount=100.0, entry_ts=T0)
    lg.on_close("BTCUSDT", realized_pnl=+1_000.0)  # peak = 11000
    lg.on_open("ETHUSDT", Direction.LONG, risk_amount=100.0, entry_ts=T0)
    lg.on_close("ETHUSDT", realized_pnl=-1_500.0)  # equity 9500
    acc = lg.to_account_state()
    assert round(acc.drawdown_pct, 3) == round((11_000 - 9_500) / 11_000 * 100, 3)


def test_daily_weekly_rollover() -> None:
    lg = _ledger()
    lg.roll_time(T0)
    lg.on_open("BTCUSDT", Direction.LONG, risk_amount=100.0, entry_ts=T0)
    lg.on_close("BTCUSDT", realized_pnl=-300.0)
    assert round(lg.to_account_state().daily_loss_pct, 2) == 3.0
    lg.roll_time(T0 + timedelta(days=1))  # neuer Tag → daily reset, weekly bleibt
    acc = lg.to_account_state()
    assert acc.daily_loss_pct == 0.0
    assert round(acc.weekly_loss_pct, 2) == 3.0
    lg.roll_time(T0 + timedelta(days=8))  # neue Woche
    assert lg.to_account_state().weekly_loss_pct == 0.0


def test_cluster_and_context_output() -> None:
    lg = _ledger()
    lg.roll_time(T0)
    lg.on_open("BTCUSDT", Direction.LONG, risk_amount=70.0, entry_ts=T0)
    lg.set_armed("ETHUSDT", Direction.LONG)
    ctx = lg.to_portfolio_context(next_instrument="ETHUSDT")
    assert len(ctx.open_positions) == 1
    assert ctx.open_positions[0].cluster_id == "crypto_majors"
    assert round(ctx.cluster_open_risk_pct, 4) == 0.7  # BTC risk counts toward ETH's cluster
    assert ctx.armed_setups == {"ETHUSDT": Direction.LONG}
    assert ctx.correlation("BTCUSDT", "ETHUSDT") == 0.85


def test_trades_today_counter() -> None:
    lg = _ledger()
    lg.roll_time(T0)
    for _ in range(3):
        lg.on_open("BTCUSDT", Direction.LONG, risk_amount=10.0, entry_ts=T0)
        lg.on_close("BTCUSDT", realized_pnl=1.0, is_scratch=True)
    assert lg.to_account_state().trades_today == 3
