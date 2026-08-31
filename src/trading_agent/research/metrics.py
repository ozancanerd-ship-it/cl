"""Backtest performance metrics from a list of ``TradeRecord``.

All in R-multiples where possible (broker/account-currency agnostic). Reports **net** metrics
by default and the gross/cost drag alongside.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, fields

from trading_agent.journal.ledger import TradeRecord


@dataclass(frozen=True, slots=True)
class Metrics:
    n_trades: int
    win_rate: float
    win_rate_excl_scratch: float
    profit_factor: float
    expectancy_r: float
    gross_expectancy_r: float
    cost_drag_r: float
    avg_r: float
    median_r: float
    stdev_r: float
    max_drawdown_r: float
    longest_loss_streak: int
    avg_mfe_r: float
    avg_mae_r: float
    total_r: float
    total_pnl_ccy: float

    def as_dict(self) -> dict[str, float | int]:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @classmethod
    def empty(cls) -> Metrics:
        return cls(
            n_trades=0,
            win_rate=0.0,
            win_rate_excl_scratch=0.0,
            profit_factor=0.0,
            expectancy_r=0.0,
            gross_expectancy_r=0.0,
            cost_drag_r=0.0,
            avg_r=0.0,
            median_r=0.0,
            stdev_r=0.0,
            max_drawdown_r=0.0,
            longest_loss_streak=0,
            avg_mfe_r=0.0,
            avg_mae_r=0.0,
            total_r=0.0,
            total_pnl_ccy=0.0,
        )


def _drawdown_r(rs: list[float]) -> float:
    peak = 0.0
    equity = 0.0
    max_dd = 0.0
    for r in rs:
        equity += r
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    return max_dd


def compute_metrics(trades: list[TradeRecord]) -> Metrics:
    if not trades:
        return Metrics.empty()

    rs = [t.realized_r for t in trades]
    gross = [t.gross_r for t in trades]
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r < 0]
    non_scratch = [t for t in trades if t.win_loss != "SCRATCH"]

    gross_profit = sum(wins)
    gross_loss = -sum(losses)
    pf = gross_profit / gross_loss if gross_loss > 0 else math.inf if gross_profit > 0 else 0.0

    streak = worst = 0
    for r in rs:
        streak = streak + 1 if r < 0 else 0
        worst = max(worst, streak)

    return Metrics(
        n_trades=len(trades),
        win_rate=len(wins) / len(trades),
        win_rate_excl_scratch=(
            sum(1 for t in non_scratch if t.realized_r > 0) / len(non_scratch)
            if non_scratch
            else 0.0
        ),
        profit_factor=pf,
        expectancy_r=statistics.fmean(rs),
        gross_expectancy_r=statistics.fmean(gross),
        cost_drag_r=statistics.fmean(gross) - statistics.fmean(rs),
        avg_r=statistics.fmean(rs),
        median_r=statistics.median(rs),
        stdev_r=statistics.pstdev(rs) if len(rs) > 1 else 0.0,
        max_drawdown_r=_drawdown_r(rs),
        longest_loss_streak=worst,
        avg_mfe_r=statistics.fmean([t.mfe_r for t in trades]),
        avg_mae_r=statistics.fmean([t.mae_r for t in trades]),
        total_r=sum(rs),
        total_pnl_ccy=sum(t.pnl_ccy for t in trades),
    )


def equity_curve_r(trades: list[TradeRecord]) -> list[float]:
    out: list[float] = []
    equity = 0.0
    for t in trades:
        equity += t.realized_r
        out.append(equity)
    return out


__all__ = ["Metrics", "compute_metrics", "equity_curve_r"]
