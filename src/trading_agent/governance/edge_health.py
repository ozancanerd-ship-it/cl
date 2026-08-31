"""Edge-Health — trägt eine historisch validierte Edge auf **Recent-/Forward-Daten** noch?

`assess_edge_health(baseline, recent_trades)` vergleicht die jüngste realisierte Performance
mit der Baseline aus der historischen Validierung (Expectancy, Profit-Factor, Win-Rate,
Max-Drawdown) und liefert ein Verdikt:

* ``INTACT``            — recent Expectancy ≥ Floor·Baseline, PF ≥ 1, DD im Rahmen.
* ``WEAKENING``         — noch positiv, aber deutlich unter Baseline; genauer beobachten.
* ``BROKEN``            — recent Expectancy ≤ 0 (mit genug Trades), PF < Floor, oder DD-Explosion.
* ``INSUFFICIENT_DATA`` — zu wenige Recent-Trades für eine Aussage.

Reine Statistik über eine ``TradeRecord``-Liste — kein Look-ahead (die Trades sind bereits
geschlossen), keine Netzwerk-/Live-Abhängigkeit.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from trading_agent.journal.ledger import TradeRecord


class EdgeHealth(StrEnum):
    INTACT = "intact"
    WEAKENING = "weakening"
    BROKEN = "broken"
    INSUFFICIENT_DATA = "insufficient_data"


@dataclass(frozen=True, slots=True)
class BaselineMetrics:
    """Kennzahlen aus der historischen Validierung, gegen die Recent-Daten geprüft werden."""

    expectancy_r: float
    profit_factor: float
    win_rate: float
    max_drawdown_r: float
    n_trades: int

    def as_dict(self) -> dict[str, float | int]:
        return {
            "expectancy_r": self.expectancy_r,
            "profit_factor": self.profit_factor,
            "win_rate": self.win_rate,
            "max_drawdown_r": self.max_drawdown_r,
            "n_trades": self.n_trades,
        }


@dataclass(frozen=True, slots=True)
class EdgeHealthReport:
    health: EdgeHealth
    recent_n: int
    recent_expectancy_r: float
    baseline_expectancy_r: float
    expectancy_ratio: float  # recent / baseline (0 wenn baseline <= 0)
    recent_profit_factor: float
    recent_win_rate: float
    recent_max_drawdown_r: float
    within_baseline_ci: bool  # recent Expectancy im 2·SE-Band um die Baseline?
    reasons: tuple[str, ...]

    @property
    def blocks_live(self) -> bool:
        return self.health is EdgeHealth.BROKEN

    def as_dict(self) -> dict[str, object]:
        return {
            "health": self.health.value,
            "recent_n": self.recent_n,
            "recent_expectancy_r": round(self.recent_expectancy_r, 4),
            "baseline_expectancy_r": round(self.baseline_expectancy_r, 4),
            "expectancy_ratio": round(self.expectancy_ratio, 3),
            "recent_profit_factor": round(self.recent_profit_factor, 3),
            "recent_win_rate": round(self.recent_win_rate, 4),
            "recent_max_drawdown_r": round(self.recent_max_drawdown_r, 3),
            "within_baseline_ci": self.within_baseline_ci,
            "reasons": list(self.reasons),
        }


def _drawdown_r(rs: Sequence[float]) -> float:
    eq = peak = mdd = 0.0
    for r in rs:
        eq += r
        peak = max(peak, eq)
        mdd = max(mdd, peak - eq)
    return mdd


def assess_edge_health(
    baseline: BaselineMetrics,
    recent_trades: Sequence[TradeRecord],
    *,
    min_recent: int = 20,
    expectancy_floor_ratio: float = 0.4,
    pf_floor: float = 0.95,
    dd_ceiling_ratio: float = 1.75,
) -> EdgeHealthReport:
    n = len(recent_trades)
    rs = [t.realized_r for t in recent_trades]

    if n < min_recent:
        return EdgeHealthReport(
            health=EdgeHealth.INSUFFICIENT_DATA,
            recent_n=n,
            recent_expectancy_r=statistics.fmean(rs) if rs else 0.0,
            baseline_expectancy_r=baseline.expectancy_r,
            expectancy_ratio=0.0,
            recent_profit_factor=0.0,
            recent_win_rate=0.0,
            recent_max_drawdown_r=_drawdown_r(rs),
            within_baseline_ci=True,
            reasons=(f"nur {n} Recent-Trades (< {min_recent}) — keine Aussage",),
        )

    exp = statistics.fmean(rs)
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r < 0]
    gp, gl = sum(wins), -sum(losses)
    pf = gp / gl if gl > 0 else math.inf if gp > 0 else 0.0
    wr = len(wins) / n
    dd = _drawdown_r(rs)
    sd = statistics.pstdev(rs) if n > 1 else 0.0
    se = sd / math.sqrt(n) if n > 0 else 0.0
    within_ci = abs(exp - baseline.expectancy_r) <= 2.0 * se if se > 0 else exp >= 0.0
    ratio = exp / baseline.expectancy_r if baseline.expectancy_r > 0 else 0.0

    reasons: list[str] = []
    health = EdgeHealth.INTACT

    if exp <= 0.0:
        health = EdgeHealth.BROKEN
        reasons.append(f"Recent-Expectancy {exp:+.3f}R ≤ 0 über {n} Trades")
    if pf != math.inf and pf < pf_floor:
        health = EdgeHealth.BROKEN
        reasons.append(f"Recent-PF {pf:.2f} < {pf_floor}")
    if baseline.max_drawdown_r > 0 and dd > baseline.max_drawdown_r * dd_ceiling_ratio:
        health = EdgeHealth.BROKEN
        reasons.append(
            f"Recent-Drawdown {dd:.1f}R > {dd_ceiling_ratio}× Baseline ({baseline.max_drawdown_r:.1f}R)"
        )

    if health is not EdgeHealth.BROKEN:
        weak = False
        if baseline.expectancy_r > 0 and ratio < expectancy_floor_ratio:
            weak = True
            reasons.append(
                f"Recent-Expectancy nur {ratio:.0%} der Baseline ({exp:+.3f} vs {baseline.expectancy_r:+.3f}R)"
            )
        if pf != math.inf and pf < 1.05:
            weak = True
            reasons.append(f"Recent-PF {pf:.2f} nahe 1.0")
        if baseline.win_rate > 0 and wr < baseline.win_rate * 0.7:
            weak = True
            reasons.append(
                f"Recent-Win-Rate {wr:.0%} deutlich unter Baseline ({baseline.win_rate:.0%})"
            )
        if weak:
            health = EdgeHealth.WEAKENING
        else:
            reasons.append(
                f"Recent-Expectancy {exp:+.3f}R (Baseline {baseline.expectancy_r:+.3f}), PF {pf:.2f} — im Rahmen"
            )

    return EdgeHealthReport(
        health=health,
        recent_n=n,
        recent_expectancy_r=exp,
        baseline_expectancy_r=baseline.expectancy_r,
        expectancy_ratio=ratio,
        recent_profit_factor=pf if pf != math.inf else 999.0,
        recent_win_rate=wr,
        recent_max_drawdown_r=dd,
        within_baseline_ci=within_ci,
        reasons=tuple(reasons),
    )


__all__ = [
    "BaselineMetrics",
    "EdgeHealth",
    "EdgeHealthReport",
    "assess_edge_health",
]
