"""Backtest ↔ Paper Parität — beweist, dass der **vorgeladene** Replay-Assembler die
identischen Entscheidungen liefert wie ein **streaming** aufgebauter Kontext (die „Live"-Art).

Der Backtest lädt je Instrument die ganze Historie einmal und slict pro ``cutoff`` (schnell).
Live kommt jede Bar einzeln; der Kontext wird inkrementell gebaut. Wenn beide Wege für jeden
``cutoff`` dieselbe ``Decision`` (Typ, Entry/SL/TP, Tier, Score) erzeugen, ist der Replay
**look-ahead-frei** und repräsentativ.

``compare_decisions`` difft zwei Decision-Ströme; ``run_parity`` fährt beide Wege über dasselbe
Repository und gibt einen ``ParityReport``. Reine Analyse — keine Broker, keine Orders.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from datetime import datetime

from trading_agent.core.enums import Timeframe
from trading_agent.data.repository import MarketDataRepository
from trading_agent.engine.replay import (
    AssemblerConfig,
    MarketContextAssembler,
    ReplayClock,
)
from trading_agent.strategy.decision import Decision
from trading_agent.strategy.evaluate import EvaluateParams, evaluate


@dataclasses.dataclass(frozen=True, slots=True)
class DecisionDiff:
    at: datetime
    instrument: str
    field: str
    backtest: object
    live: object


@dataclasses.dataclass(frozen=True, slots=True)
class ParityReport:
    instruments: tuple[str, ...]
    compared: int  # Anzahl verglichener cutoffs
    matches: int
    diffs: tuple[DecisionDiff, ...]

    @property
    def ok(self) -> bool:
        return not self.diffs

    @property
    def match_rate(self) -> float:
        return self.matches / self.compared if self.compared else 1.0


_FIELDS = ("decision", "direction", "entry", "sl", "tp1", "tp2", "tier", "score")


def _key(d: Decision, field: str) -> object:
    v = getattr(d, field)
    if field in ("entry", "sl", "tp1", "tp2", "score") and v is not None:
        return round(float(v), 6)
    if hasattr(v, "value"):
        return v.value
    return v


def compare_decisions(
    backtest: Sequence[tuple[datetime, str, Decision]],
    live: Sequence[tuple[datetime, str, Decision]],
) -> ParityReport:
    """Beide Ströme müssen dieselbe Folge von (cutoff, instrument) haben."""
    bmap = {(t, i): d for t, i, d in backtest}
    lmap = {(t, i): d for t, i, d in live}
    keys = sorted(set(bmap) & set(lmap))
    diffs: list[DecisionDiff] = []
    matches = 0
    for k in keys:
        bd, ld = bmap[k], lmap[k]
        row_diffs = [
            DecisionDiff(k[0], k[1], f, _key(bd, f), _key(ld, f))
            for f in _FIELDS
            if _key(bd, f) != _key(ld, f)
        ]
        if row_diffs:
            diffs.extend(row_diffs)
        else:
            matches += 1
    instruments = tuple(sorted({i for _, i in keys}))
    return ParityReport(instruments, len(keys), matches, tuple(diffs))


def run_parity(
    repo: MarketDataRepository,
    *,
    instruments: Sequence[str],
    start: datetime,
    end: datetime,
    base_timeframe: Timeframe = Timeframe.M5,
    warmup_bars: int = 300,
    read_native_higher: bool = True,
    max_cutoffs: int | None = 500,
    evaluate_params: EvaluateParams | None = None,
) -> ParityReport:
    """Fährt den Replay zweimal:

    * **backtest**: ein Assembler, ``bind(start, end)`` einmal, dann pro cutoff slicen.
    * **live**:     pro cutoff ein **frischer** Assembler mit ``bind(cutoff, cutoff)`` — sieht
      per Konstruktion nichts nach dem cutoff.

    ``max_cutoffs`` begrenzt den Vergleich (der Live-Pfad baut je Schritt neu → teuer).
    """
    backtest_rows: list[tuple[datetime, str, Decision]] = []
    live_rows: list[tuple[datetime, str, Decision]] = []

    for inst in instruments:
        cfg = AssemblerConfig(
            instrument=inst,
            base_timeframe=base_timeframe,
            warmup_bars=warmup_bars,
            read_native_higher=read_native_higher,
            news_feed_available=False,
            fixed_spread=None,
        )
        grid = repo.read_ohlcv(inst, base_timeframe, start, end, as_of=end)
        if not grid:
            continue
        cutoffs = list(ReplayClock.from_bars(grid))
        if max_cutoffs is not None and len(cutoffs) > max_cutoffs:
            step = len(cutoffs) // max_cutoffs
            cutoffs = cutoffs[::step]

        ep = evaluate_params

        pre = MarketContextAssembler(repo, cfg)
        pre.bind(start, end)
        for c in cutoffs:
            backtest_rows.append((c, inst, evaluate(pre.at(c), params=ep).decision))

        for c in cutoffs:
            fresh = MarketContextAssembler(repo, cfg)
            fresh.bind(c, c)
            live_rows.append((c, inst, evaluate(fresh.at(c), params=ep).decision))

    return compare_decisions(backtest_rows, live_rows)


def render_parity(report: ParityReport, *, max_diffs: int = 20) -> str:
    """Menschenlesbarer Parity-Report — für Logs / CI / den Backtest-Output."""
    head = (
        f"Parity {report.matches}/{report.compared} cutoffs identisch "
        f"(match_rate {report.match_rate:.4f}) über {', '.join(report.instruments) or '—'} "
        f"→ {'OK — look-ahead-frei' if report.ok else f'{len(report.diffs)} ABWEICHUNGEN'}"
    )
    if report.ok:
        return head
    by_field: dict[str, int] = {}
    for d in report.diffs:
        by_field[d.field] = by_field.get(d.field, 0) + 1
    field_hist = "  Felder: " + ", ".join(f"{k}×{v}" for k, v in sorted(by_field.items()))
    rows = [
        f"  {d.at.isoformat()} {d.instrument:8s} {d.field:9s} "
        f"backtest={d.backtest!r}  live={d.live!r}"
        for d in report.diffs[:max_diffs]
    ]
    more = (
        [] if len(report.diffs) <= max_diffs else [f"  … +{len(report.diffs) - max_diffs} weitere"]
    )
    return "\n".join([head, field_hist, *rows, *more])


__all__ = [
    "DecisionDiff",
    "ParityReport",
    "compare_decisions",
    "render_parity",
    "run_parity",
]
