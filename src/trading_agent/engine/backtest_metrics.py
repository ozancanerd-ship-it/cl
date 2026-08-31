"""Erweiterte Backtest-Auswertung — Strategy-spezifische Kennzahlen + **Signal-Analyse**.

`research.metrics.Metrics` liefert die brokerunabhängigen Basiskennzahlen (R-Erwartung, PF,
Drawdown, Streaks …). Dieses Modul ergänzt, was nur der volle Strategy-Pfad kennt:

* **Exit-Struktur** — TP1/TP2/TP3-Hit-Rate, Stop-Rate, Trail/BE, EXIT_REQUIRED-, Invalidated-Rate.
* **Segmente** — Long vs Short, je Score-Tier, je Confidence-Tier, je Exit-Grund.
* **Signal-Analyse** — Score / Confidence / Confluence / Setup-State vs. Ergebnis (haben unsere
  Scores überhaupt Informationswert?), Entry-Qualität vs. Exit-Qualität (MFE-Ausnutzung).
* **Lauf-Telemetrie** — Decision-Verteilung (BUY/SELL/WAIT/NO_TRADE), No-Trade-Gründe, Veto-
  Häufigkeit, Signal-Revisionen, Alerts.

Keine Kennzahl wird geschönt: Division durch 0 → 0.0 bzw. `None`, Buckets ohne Trades werden
ausgewiesen, `profit_factor` kann `inf` sein.
"""

from __future__ import annotations

import dataclasses
import itertools
import math
import statistics
from collections import Counter
from collections.abc import Callable, Sequence

from trading_agent.core.enums import DecisionType, Side


@dataclasses.dataclass(frozen=True, slots=True)
class TradeOutcome:
    """Ein abgeschlossener Paper-Trade + der Analyse-Schnappschuss bei Entry."""

    trade_id: str
    instrument: str
    timeframe: str
    direction: Side
    setup_id: str
    entry_ts: str
    exit_ts: str
    realized_r: float
    gross_r: float
    mfe_r: float
    mae_r: float
    bars_held: int
    exit_reason: str  # tp1|tp2|tp3|stop_loss|breakeven_stop|trail_stop|structure_invalidation|manual_exit_request|expiry|end_of_data
    tp_level: int  # 0..3 höchstes erreichtes TP
    win_loss: str  # WIN|LOSS|SCRATCH
    score: float | None
    score_tier: str  # A+|A|B|?
    confidence: float | None
    confidence_tier: str  # high|mid|low|?
    confluence_net: float | None
    confluence_support: float | None
    setup_state_at_entry: str


@dataclasses.dataclass(frozen=True, slots=True)
class SegmentStat:
    label: str
    n: int
    win_rate: float
    expectancy_r: float
    avg_r: float
    median_r: float
    total_r: float
    avg_mfe_r: float
    avg_mae_r: float
    profit_factor: float


@dataclasses.dataclass(frozen=True, slots=True)
class Bucket:
    label: str
    lo: float
    hi: float
    n: int
    avg_realized_r: float
    win_rate: float


@dataclasses.dataclass(frozen=True, slots=True)
class RunTelemetry:
    """Nicht-Trade-Ereignisse über den ganzen Lauf."""

    steps: int
    decisions: Counter[str]  # DecisionType.value -> Anzahl
    no_trade_reasons: Counter[str]
    veto_frequency: Counter[str]
    signal_revisions: int
    signals_created: int
    signals_invalidated: int
    signals_expired: int
    exit_required_events: int
    alerts_raised: int


@dataclasses.dataclass(frozen=True, slots=True)
class StrategyBacktestReport:
    n_trades: int

    # Exit-Struktur
    tp1_hit_rate: float
    tp2_hit_rate: float
    tp3_hit_rate: float
    stop_rate: float
    breakeven_rate: float
    trail_rate: float
    invalidated_exit_rate: float
    manual_exit_rate: float
    expiry_rate: float

    # Hold-Time
    avg_hold_bars: float
    median_hold_bars: float

    # Entry-/Exit-Qualität
    avg_mfe_r: float
    avg_mae_r: float
    exit_efficiency: float  # Σ realized_r / Σ mfe_r  (wieviel des Laufs wurde mitgenommen)
    avg_give_back_r: float  # mfe_r - realized_r im Mittel

    # Segmente
    by_direction: tuple[SegmentStat, ...]
    by_score_tier: tuple[SegmentStat, ...]
    by_confidence_tier: tuple[SegmentStat, ...]
    by_exit_reason: tuple[SegmentStat, ...]
    by_instrument: tuple[SegmentStat, ...]

    # Signal-Analyse (Informationswert)
    score_vs_outcome: tuple[Bucket, ...]
    confidence_vs_outcome: tuple[Bucket, ...]
    confluence_vs_outcome: tuple[Bucket, ...]
    setup_state_vs_outcome: tuple[SegmentStat, ...]
    score_outcome_correlation: float | None  # Pearson r(score, realized_r)
    confidence_outcome_correlation: float | None

    # Lauf-Telemetrie
    telemetry: RunTelemetry

    # abgeleitete Raten (bezogen auf Signal-Ereignisse, nicht nur Trades)
    exit_required_rate: float
    invalidated_setup_rate: float
    veto_rate_per_decision: float


# --------------------------------------------------------------------------------- Bauen


def _seg(label: str, rows: Sequence[TradeOutcome]) -> SegmentStat:
    if not rows:
        return SegmentStat(label, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    rs = [t.realized_r for t in rows]
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r < 0]
    gp, gl = sum(wins), -sum(losses)
    pf = gp / gl if gl > 0 else math.inf if gp > 0 else 0.0
    return SegmentStat(
        label=label,
        n=len(rows),
        win_rate=len(wins) / len(rows),
        expectancy_r=statistics.fmean(rs),
        avg_r=statistics.fmean(rs),
        median_r=statistics.median(rs),
        total_r=sum(rs),
        avg_mfe_r=statistics.fmean([t.mfe_r for t in rows]),
        avg_mae_r=statistics.fmean([t.mae_r for t in rows]),
        profit_factor=pf,
    )


def _rate(rows: Sequence[TradeOutcome], pred: Callable[[TradeOutcome], bool]) -> float:
    if not rows:
        return 0.0
    return sum(1 for t in rows if pred(t)) / len(rows)


def _buckets(
    rows: Sequence[TradeOutcome],
    value: Callable[[TradeOutcome], float | None],
    edges: Sequence[float],
    *,
    name: str,
) -> tuple[Bucket, ...]:
    out: list[Bucket] = []
    for lo, hi in itertools.pairwise(edges):
        sub = [t for t in rows if (v := value(t)) is not None and lo <= v < hi]
        rs = [t.realized_r for t in sub]
        out.append(
            Bucket(
                label=f"{name} [{lo:g},{hi:g})",
                lo=lo,
                hi=hi,
                n=len(sub),
                avg_realized_r=statistics.fmean(rs) if rs else 0.0,
                win_rate=(sum(1 for r in rs if r > 0) / len(rs)) if rs else 0.0,
            )
        )
    return tuple(out)


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    if len(xs) < 3:
        return None
    try:
        return statistics.correlation(xs, ys)
    except statistics.StatisticsError:
        return None


def build_strategy_report(
    outcomes: Sequence[TradeOutcome], telemetry: RunTelemetry
) -> StrategyBacktestReport:
    n = len(outcomes)

    def r(pred: Callable[[TradeOutcome], bool]) -> float:
        return _rate(outcomes, pred)

    holds = [t.bars_held for t in outcomes]
    sum_mfe = sum(t.mfe_r for t in outcomes)
    sum_real = sum(t.realized_r for t in outcomes)

    score_pairs = [(t.score, t.realized_r) for t in outcomes if t.score is not None]
    conf_pairs = [(t.confidence, t.realized_r) for t in outcomes if t.confidence is not None]

    decisions_total = sum(telemetry.decisions.values())
    signal_events = max(1, telemetry.signals_created)

    return StrategyBacktestReport(
        n_trades=n,
        tp1_hit_rate=r(lambda t: t.tp_level >= 1),
        tp2_hit_rate=r(lambda t: t.tp_level >= 2),
        tp3_hit_rate=r(lambda t: t.tp_level >= 3),
        stop_rate=r(lambda t: t.exit_reason == "stop_loss"),
        breakeven_rate=r(lambda t: t.exit_reason == "breakeven_stop"),
        trail_rate=r(lambda t: t.exit_reason == "trail_stop"),
        invalidated_exit_rate=r(lambda t: t.exit_reason == "structure_invalidation"),
        manual_exit_rate=r(lambda t: t.exit_reason == "manual_exit_request"),
        expiry_rate=r(lambda t: t.exit_reason in ("expiry", "end_of_data")),
        avg_hold_bars=statistics.fmean(holds) if holds else 0.0,
        median_hold_bars=statistics.median(holds) if holds else 0.0,
        avg_mfe_r=statistics.fmean([t.mfe_r for t in outcomes]) if n else 0.0,
        avg_mae_r=statistics.fmean([t.mae_r for t in outcomes]) if n else 0.0,
        exit_efficiency=(sum_real / sum_mfe) if sum_mfe > 0 else 0.0,
        avg_give_back_r=(
            statistics.fmean([t.mfe_r - t.realized_r for t in outcomes]) if n else 0.0
        ),
        by_direction=(
            _seg("LONG", [t for t in outcomes if t.direction is Side.BUY]),
            _seg("SHORT", [t for t in outcomes if t.direction is Side.SELL]),
        ),
        by_score_tier=tuple(
            _seg(tier, [t for t in outcomes if t.score_tier == tier])
            for tier in ("A+", "A", "B", "?")
        ),
        by_confidence_tier=tuple(
            _seg(tier, [t for t in outcomes if t.confidence_tier == tier])
            for tier in ("high", "mid", "low", "?")
        ),
        by_exit_reason=tuple(
            _seg(reason, [t for t in outcomes if t.exit_reason == reason])
            for reason in sorted({t.exit_reason for t in outcomes})
        ),
        by_instrument=tuple(
            _seg(inst, [t for t in outcomes if t.instrument == inst])
            for inst in sorted({t.instrument for t in outcomes})
        ),
        score_vs_outcome=_buckets(
            outcomes, lambda t: t.score, (0, 55, 65, 75, 85, 101), name="score"
        ),
        confidence_vs_outcome=_buckets(
            outcomes, lambda t: t.confidence, (0.0, 0.5, 0.6, 0.7, 0.8, 1.01), name="conf"
        ),
        confluence_vs_outcome=_buckets(
            outcomes, lambda t: t.confluence_net, (-1.01, -0.2, 0.0, 0.2, 0.5, 1.01), name="cfl"
        ),
        setup_state_vs_outcome=tuple(
            _seg(st, [t for t in outcomes if t.setup_state_at_entry == st])
            for st in sorted({t.setup_state_at_entry for t in outcomes})
        ),
        score_outcome_correlation=_pearson([p[0] for p in score_pairs], [p[1] for p in score_pairs])
        if len(score_pairs) >= 3
        else None,
        confidence_outcome_correlation=_pearson(
            [p[0] for p in conf_pairs], [p[1] for p in conf_pairs]
        )
        if len(conf_pairs) >= 3
        else None,
        telemetry=telemetry,
        exit_required_rate=telemetry.exit_required_events / signal_events,
        invalidated_setup_rate=telemetry.signals_invalidated / signal_events,
        veto_rate_per_decision=(
            sum(telemetry.veto_frequency.values()) / decisions_total if decisions_total else 0.0
        ),
    )


def confidence_tier_of(confidence: float | None) -> str:
    if confidence is None:
        return "?"
    if confidence >= 0.8:
        return "high"
    if confidence >= 0.6:
        return "mid"
    return "low"


def decision_key(dt: DecisionType) -> str:
    return dt.value


__all__ = [
    "Bucket",
    "RunTelemetry",
    "SegmentStat",
    "StrategyBacktestReport",
    "TradeOutcome",
    "build_strategy_report",
    "confidence_tier_of",
    "decision_key",
]
