"""Monte-Carlo robustness: a single equity curve says nothing about the distribution of outcomes.

Tests:
* **trade-order bootstrap** — resample R-multiples with replacement,
* **trade dropout** — randomly drop a fraction of trades,
* **cost stress** — inflate the loss side,
* **start jitter** — drop the first N trades.

Reports 5th-percentile final equity, max-drawdown distribution and **ruin probability**
(P(peak-to-trough drawdown in R >= threshold)).
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass

from trading_agent.journal.ledger import TradeRecord


def _max_dd_r(rs: Sequence[float]) -> float:
    peak = equity = 0.0
    dd = 0.0
    for r in rs:
        equity += r
        peak = max(peak, equity)
        dd = max(dd, peak - equity)
    return dd


@dataclass(frozen=True, slots=True)
class MonteCarloReport:
    runs: int
    final_equity_r_p05: float
    final_equity_r_p50: float
    final_equity_r_p95: float
    max_dd_r_p50: float
    max_dd_r_p95: float
    ruin_probability: float
    ruin_threshold_r: float
    prob_positive: float


def monte_carlo(
    trades: Sequence[TradeRecord],
    *,
    runs: int = 1000,
    seed: int = 0,
    ruin_threshold_r: float = 10.0,
    dropout_pct: float = 0.10,
    cost_stress: float = 1.5,
    start_jitter: int = 0,
) -> MonteCarloReport:
    base = [t.realized_r for t in trades]
    if start_jitter:
        base = base[start_jitter:]
    if not base:
        return MonteCarloReport(
            runs=0,
            final_equity_r_p05=0.0,
            final_equity_r_p50=0.0,
            final_equity_r_p95=0.0,
            max_dd_r_p50=0.0,
            max_dd_r_p95=0.0,
            ruin_probability=0.0,
            ruin_threshold_r=ruin_threshold_r,
            prob_positive=0.0,
        )

    rng = random.Random(seed)
    n = len(base)
    finals: list[float] = []
    dds: list[float] = []
    ruined = 0
    for _ in range(runs):
        sample = [rng.choice(base) for _ in range(n)]
        if dropout_pct > 0:
            keep = max(1, int(n * (1.0 - dropout_pct)))
            sample = rng.sample(sample, keep)
        if cost_stress != 1.0:
            sample = [r * cost_stress if r < 0 else r for r in sample]
        rng.shuffle(sample)
        finals.append(sum(sample))
        dd = _max_dd_r(sample)
        dds.append(dd)
        if dd >= ruin_threshold_r:
            ruined += 1

    finals.sort()
    dds.sort()

    def pct(data: list[float], p: float) -> float:
        idx = min(len(data) - 1, max(0, int(len(data) * p)))
        return round(data[idx], 4)

    return MonteCarloReport(
        runs=runs,
        final_equity_r_p05=pct(finals, 0.05),
        final_equity_r_p50=pct(finals, 0.50),
        final_equity_r_p95=pct(finals, 0.95),
        max_dd_r_p50=pct(dds, 0.50),
        max_dd_r_p95=pct(dds, 0.95),
        ruin_probability=round(ruined / runs, 4),
        ruin_threshold_r=ruin_threshold_r,
        prob_positive=round(sum(1 for f in finals if f > 0) / runs, 4),
    )


__all__ = ["MonteCarloReport", "monte_carlo"]
