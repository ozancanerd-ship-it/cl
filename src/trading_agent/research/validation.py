"""Validation harness: chronological splits, walk-forward, purge/embargo, stability axes.

Rules (from ``docs/strategy/anti-overfitting.md``):
* the **test** split is touched exactly once, at the very end;
* no parameter is fitted on out-of-sample data;
* an edge carried by a single regime / time window / symbol is **not** validated.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from trading_agent.core.enums import Timeframe
from trading_agent.core.time import ensure_utc
from trading_agent.journal.ledger import TradeRecord


@dataclass(frozen=True, slots=True)
class Split:
    train: list[TradeRecord]
    validation: list[TradeRecord]
    test: list[TradeRecord]


def chronological_split(
    trades: Sequence[TradeRecord],
    *,
    train: float = 0.5,
    validation: float = 0.25,
    key: Callable[[TradeRecord], datetime] = lambda t: t.entry_ts,
) -> Split:
    if not 0 < train < 1 or not 0 <= validation < 1 or train + validation >= 1:
        raise ValueError("invalid split fractions")
    ordered = sorted(trades, key=key)
    n = len(ordered)
    a = int(n * train)
    b = a + int(n * validation)
    return Split(ordered[:a], ordered[a:b], ordered[b:])


@dataclass(frozen=True, slots=True)
class WalkForwardFold:
    index: int
    train_start: datetime
    train_end: datetime
    test_start: datetime
    test_end: datetime

    def train_trades(self, trades: Sequence[TradeRecord]) -> list[TradeRecord]:
        return [t for t in trades if self.train_start <= t.entry_ts < self.train_end]

    def test_trades(self, trades: Sequence[TradeRecord]) -> list[TradeRecord]:
        return [t for t in trades if self.test_start <= t.entry_ts < self.test_end]


def walk_forward_folds(
    start: datetime,
    end: datetime,
    *,
    train_days: int = 180,
    test_days: int = 60,
    step_days: int = 60,
) -> list[WalkForwardFold]:
    start, end = ensure_utc(start), ensure_utc(end)
    folds: list[WalkForwardFold] = []
    i = 0
    cursor = start
    while True:
        train_end = cursor + timedelta(days=train_days)
        test_end = train_end + timedelta(days=test_days)
        if test_end > end:
            break
        folds.append(WalkForwardFold(i, cursor, train_end, train_end, test_end))
        cursor += timedelta(days=step_days)
        i += 1
    return folds


def purge_embargo(
    trades: Sequence[TradeRecord],
    *,
    boundary: datetime,
    timeframe: Timeframe,
    max_hold_bars: int = 96,
    embargo_bars: int = 96,
) -> list[TradeRecord]:
    """Drop trades whose life overlaps ``boundary`` (± the horizon), leaving a clean gap
    between a training block and the following test block."""
    boundary = ensure_utc(boundary)
    horizon = timedelta(seconds=timeframe.seconds * max_hold_bars)
    embargo = timedelta(seconds=timeframe.seconds * embargo_bars)
    lo = boundary - horizon
    hi = boundary + embargo
    return [t for t in trades if not (t.entry_ts < hi and t.exit_ts > lo)]


# --------------------------------------------------------------------------------------------
# stability axes
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WindowStat:
    start: datetime
    end: datetime
    n_trades: int
    total_r: float
    positive: bool


def time_stability(
    trades: Sequence[TradeRecord],
    *,
    window_days: int = 90,
    step_days: int = 30,
) -> list[WindowStat]:
    if not trades:
        return []
    ordered = sorted(trades, key=lambda t: t.entry_ts)
    first, last = ordered[0].entry_ts, ordered[-1].entry_ts
    out: list[WindowStat] = []
    cur = first
    while cur < last:
        w_end = cur + timedelta(days=window_days)
        window = [t for t in ordered if cur <= t.entry_ts < w_end]
        total = sum(t.realized_r for t in window)
        out.append(WindowStat(cur, w_end, len(window), round(total, 4), total > 0))
        cur += timedelta(days=step_days)
    return out


def fraction_positive_windows(stats: Sequence[WindowStat], *, min_trades: int = 1) -> float:
    considered = [s for s in stats if s.n_trades >= min_trades]
    if not considered:
        return 0.0
    return sum(1 for s in considered if s.positive) / len(considered)


@dataclass(frozen=True, slots=True)
class SymbolStabilityReport:
    per_symbol_total_r: dict[str, float]
    fraction_positive: float
    total_r_without_best: float


def symbol_stability(trades: Sequence[TradeRecord]) -> SymbolStabilityReport:
    by_sym: dict[str, float] = {}
    for t in trades:
        by_sym[t.instrument] = by_sym.get(t.instrument, 0.0) + t.realized_r
    if not by_sym:
        return SymbolStabilityReport({}, 0.0, 0.0)
    best = max(by_sym, key=lambda k: by_sym[k])
    total_wo_best = sum(v for k, v in by_sym.items() if k != best)
    frac_pos = sum(1 for v in by_sym.values() if v > 0) / len(by_sym)
    return SymbolStabilityReport(
        {k: round(v, 4) for k, v in by_sym.items()},
        round(frac_pos, 4),
        round(total_wo_best, 4),
    )


__all__ = [
    "Split",
    "SymbolStabilityReport",
    "WalkForwardFold",
    "WindowStat",
    "chronological_split",
    "fraction_positive_windows",
    "purge_embargo",
    "symbol_stability",
    "time_stability",
    "walk_forward_folds",
]
