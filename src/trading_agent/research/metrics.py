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
    # Risiko-adjustierte Kennzahlen auf der **R-Sequenz je Trade** (nicht zeit-annualisiert —
    # ein Trade-Sequenz-Backtest hat keine feste Kalenderfrequenz):
    #   sharpe_r  = mean(R) / stdev(R)          — Rendite je Einheit Gesamtvolatilität
    #   sortino_r = mean(R) / downside_stdev(R) — Rendite je Einheit *Verlust*volatilität
    #   calmar_r  = total_R / max_drawdown_R    — Gesamtertrag je Einheit maximalem Rückschlag
    sharpe_r: float = 0.0
    sortino_r: float = 0.0
    calmar_r: float = 0.0

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


def _ratios(rs: list[float], max_dd: float) -> tuple[float, float, float]:
    """(sharpe_r, sortino_r, calmar_r) auf der R-Sequenz. ``0.0`` wenn nicht bestimmbar.

    Sortino nutzt die **Downside-Deviation** ``sqrt(mean(min(r,0)²))`` (Abweichung unter dem
    Ziel 0), nicht die Streuung der Verlust-Teilmenge — sonst wäre die Kennzahl 0, wenn alle
    Verluste gleich groß sind.
    """
    if len(rs) < 2:
        return 0.0, 0.0, 0.0
    mean = statistics.fmean(rs)
    sd = statistics.pstdev(rs)
    sharpe = mean / sd if sd > 0 else 0.0
    dd_dev = math.sqrt(statistics.fmean([min(r, 0.0) ** 2 for r in rs]))
    sortino = mean / dd_dev if dd_dev > 0 else 0.0
    calmar = sum(rs) / max_dd if max_dd > 0 else 0.0
    return round(sharpe, 4), round(sortino, 4), round(calmar, 4)


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

    max_dd = _drawdown_r(rs)
    sharpe_r, sortino_r, calmar_r = _ratios(rs, max_dd)

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
        max_drawdown_r=max_dd,
        longest_loss_streak=worst,
        avg_mfe_r=statistics.fmean([t.mfe_r for t in trades]),
        avg_mae_r=statistics.fmean([t.mae_r for t in trades]),
        total_r=sum(rs),
        total_pnl_ccy=sum(t.pnl_ccy for t in trades),
        sharpe_r=sharpe_r,
        sortino_r=sortino_r,
        calmar_r=calmar_r,
    )


def equity_curve_r(trades: list[TradeRecord]) -> list[float]:
    out: list[float] = []
    equity = 0.0
    for t in trades:
        equity += t.realized_r
        out.append(equity)
    return out


__all__ = ["Metrics", "compute_metrics", "equity_curve_r"]
