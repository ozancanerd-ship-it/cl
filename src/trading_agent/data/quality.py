"""Data Quality Engine.

Prüft eine OHLCV-Serie auf: fehlende Kerzen, Duplikate, falsche Reihenfolge, ungültige
OHLC-/Volumen-Werte, stale data, Timestamp-/Timezone-/DST-Fehler, Datenlücken,
Symbol-/Timeframe-Mismatch. Ergebnis ist ein ``DataQualityStatus``.

``status.blocks_trading`` (== mind. ein ``CRITICAL``-Befund) ist die Schnittstelle zur
späteren Strategy Engine: dann muss sie für dieses Instrument/Timeframe ``NO_TRADE`` erzwingen.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta

from trading_agent.core.enums import (
    DataKind,
    DataQualityCode,
    DataQualitySeverity,
    Timeframe,
)
from trading_agent.core.models import (
    OHLCV,
    DataQualityIssue,
    DataQualityStatus,
    SessionWindow,
)
from trading_agent.core.time import TimeError, ensure_utc, is_aligned
from trading_agent.refdata.calendar import TradingCalendar, resolve_session
from trading_agent.refdata.models import SessionSpec

_CRIT = DataQualitySeverity.CRITICAL
_WARN = DataQualitySeverity.WARNING
_INFO = DataQualitySeverity.INFO


class QualityPolicy:
    """Schwellen der Qualitätsprüfung."""

    def __init__(
        self,
        *,
        stale_after_bars: float = 2.5,
        gap_warn_bars: int = 1,
        gap_critical_bars: int = 12,
        empty_is_critical: bool = True,
        future_slack: timedelta = timedelta(seconds=1),
    ) -> None:
        self.stale_after_bars = stale_after_bars
        self.gap_warn_bars = gap_warn_bars
        self.gap_critical_bars = gap_critical_bars
        self.empty_is_critical = empty_is_critical
        self.future_slack = future_slack


DEFAULT_POLICY = QualityPolicy()


# --------------------------------------------------------------------------------------------
# Hilfsfunktionen (auch von Provider/Repository genutzt)
# --------------------------------------------------------------------------------------------


def sort_ohlcv(bars: Iterable[OHLCV]) -> list[OHLCV]:
    """Aufsteigend nach ``open_time``. Stabil (behält Reihenfolge bei Gleichstand)."""
    return sorted(bars, key=lambda b: b.open_time)


def deduplicate_ohlcv(bars: Iterable[OHLCV]) -> tuple[list[OHLCV], list[OHLCV]]:
    """Entfernt Duplikate nach ``open_time`` (erstes Vorkommen gewinnt).

    Rückgabe: ``(bereinigt, entfernte_konflikte)``. ``entfernte_konflikte`` enthält Duplikate,
    deren OHLCV-Werte vom Erstvorkommen abweichen (potenziell ernst).
    """
    seen: dict[datetime, OHLCV] = {}
    conflicts: list[OHLCV] = []
    for bar in sort_ohlcv(bars):
        prev = seen.get(bar.open_time)
        if prev is None:
            seen[bar.open_time] = bar
        elif (prev.open, prev.high, prev.low, prev.close, prev.volume) != (
            bar.open,
            bar.high,
            bar.low,
            bar.close,
            bar.volume,
        ):
            conflicts.append(bar)
    return list(seen.values()), conflicts


# --------------------------------------------------------------------------------------------
# Kern: OHLCV-Serie prüfen
# --------------------------------------------------------------------------------------------


def check_ohlcv_series(
    bars: list[OHLCV],
    *,
    instrument: str,
    timeframe: Timeframe,
    now: datetime,
    as_of: datetime | None = None,
    calendar: TradingCalendar | None = None,
    policy: QualityPolicy | None = None,
) -> DataQualityStatus:
    """Vollständige Qualitätsprüfung einer OHLCV-Serie."""
    policy = policy or DEFAULT_POLICY
    now = ensure_utc(now)
    as_of = ensure_utc(as_of) if as_of is not None else None
    issues: list[DataQualityIssue] = []
    step = timedelta(seconds=timeframe.seconds)

    if not bars:
        issues.append(
            DataQualityIssue(
                code=DataQualityCode.EMPTY_SERIES,
                severity=_CRIT if policy.empty_is_critical else _WARN,
                message=f"keine Bars für {instrument}/{timeframe}",
            )
        )
        return _finish(instrument, timeframe, now, as_of, 0, issues)

    ordered = sort_ohlcv(bars)

    # 1) Reihenfolge (auf der Original-Reihenfolge, nicht der sortierten)
    original_times = [b.open_time for b in bars]
    if original_times != sorted(original_times):
        issues.append(
            DataQualityIssue(
                code=DataQualityCode.OUT_OF_ORDER,
                severity=_CRIT,
                message="Bars sind nicht aufsteigend nach open_time sortiert",
            )
        )

    # 2) je-Bar-Checks
    prev: OHLCV | None = None
    dup_conflict = 0
    dup_identical = 0
    for bar in ordered:
        if bar.instrument != instrument:
            issues.append(
                DataQualityIssue(
                    code=DataQualityCode.SYMBOL_MISMATCH,
                    severity=_CRIT,
                    message=f"Bar-Instrument {bar.instrument!r} != erwartet {instrument!r}",
                    at=bar.open_time,
                )
            )
        if bar.timeframe is not timeframe:
            issues.append(
                DataQualityIssue(
                    code=DataQualityCode.TIMEFRAME_MISMATCH,
                    severity=_CRIT,
                    message=f"Bar-Timeframe {bar.timeframe} != erwartet {timeframe}",
                    at=bar.open_time,
                )
            )
        if bar.open_time.utcoffset() != timedelta(0):
            issues.append(
                DataQualityIssue(
                    code=DataQualityCode.TIMESTAMP_NOT_UTC,
                    severity=_CRIT,
                    message=f"open_time nicht UTC: {bar.open_time.isoformat()}",
                    at=bar.open_time,
                )
            )
        if not is_aligned(bar.open_time, timeframe):
            issues.append(
                DataQualityIssue(
                    code=DataQualityCode.TIMESTAMP_MISALIGNED,
                    severity=_CRIT,
                    message=f"open_time nicht an {timeframe} ausgerichtet",
                    at=bar.open_time,
                )
            )
        # defensive OHLC/Volumen-Checks (Modell erzwingt das bereits – hier für rohe Serien)
        if not (bar.low <= min(bar.open, bar.close) and bar.high >= max(bar.open, bar.close)):
            issues.append(
                DataQualityIssue(
                    code=DataQualityCode.INVALID_OHLC,
                    severity=_CRIT,
                    message=f"OHLC inkonsistent bei {bar.open_time.isoformat()}",
                    at=bar.open_time,
                )
            )
        if bar.volume < 0:
            issues.append(
                DataQualityIssue(
                    code=DataQualityCode.INVALID_VOLUME,
                    severity=_CRIT,
                    message=f"negatives Volumen bei {bar.open_time.isoformat()}",
                    at=bar.open_time,
                )
            )
        horizon = as_of if as_of is not None else now
        if bar.close_time > horizon + policy.future_slack:
            issues.append(
                DataQualityIssue(
                    code=DataQualityCode.TIMESTAMP_IN_FUTURE,
                    severity=_CRIT,
                    message=(
                        f"Bar schließt in der Zukunft ({bar.close_time.isoformat()} > "
                        f"{horizon.isoformat()}) – Look-ahead/kaputte Daten"
                    ),
                    at=bar.close_time,
                )
            )

        if prev is not None:
            if bar.open_time == prev.open_time:
                same = (prev.open, prev.high, prev.low, prev.close, prev.volume) == (
                    bar.open,
                    bar.high,
                    bar.low,
                    bar.close,
                    bar.volume,
                )
                dup_identical += int(same)
                dup_conflict += int(not same)
            elif bar.open_time > prev.open_time:
                _detect_gap(prev, bar, timeframe, step, calendar, policy, issues)
        prev = bar

    if dup_conflict:
        issues.append(
            DataQualityIssue(
                code=DataQualityCode.DUPLICATE_BAR,
                severity=_CRIT,
                message=f"{dup_conflict} widersprüchliche Duplikate (gleiche open_time, andere Werte)",
                context={"count": dup_conflict},
            )
        )
    if dup_identical:
        issues.append(
            DataQualityIssue(
                code=DataQualityCode.DUPLICATE_BAR,
                severity=_WARN,
                message=f"{dup_identical} identische Duplikate",
                context={"count": dup_identical},
            )
        )

    # 3) Stale-Data
    last = ordered[-1]
    horizon = as_of if as_of is not None else now
    age = horizon - last.close_time
    if age > step * policy.stale_after_bars:
        market_open = calendar.is_open(horizon - step) if calendar is not None else True
        issues.append(
            DataQualityIssue(
                code=DataQualityCode.STALE_DATA,
                severity=_CRIT if market_open else _WARN,
                message=(
                    f"letzte Bar schloss vor {age} (> {policy.stale_after_bars} Bars); "
                    f"Markt {'offen' if market_open else 'geschlossen'}"
                ),
                at=last.close_time,
                context={"age_seconds": int(age.total_seconds())},
            )
        )

    return _finish(instrument, timeframe, now, as_of, len(ordered), issues)


def _detect_gap(
    prev: OHLCV,
    cur: OHLCV,
    timeframe: Timeframe,
    step: timedelta,
    calendar: TradingCalendar | None,
    policy: QualityPolicy,
    issues: list[DataQualityIssue],
) -> None:
    expected = prev.open_time + step
    if cur.open_time <= expected:
        return
    missing_slots: list[datetime] = []
    t = expected
    while t < cur.open_time:
        if calendar is None or calendar.is_open(t):
            missing_slots.append(t)
        t += step
    if not missing_slots:
        return  # Lücke fällt vollständig in Marktschluss -> keine echte Lücke
    n = len(missing_slots)
    severity = _WARN
    if n >= policy.gap_critical_bars:
        severity = _CRIT
    elif n < policy.gap_warn_bars:
        severity = _INFO
    issues.append(
        DataQualityIssue(
            code=DataQualityCode.GAP,
            severity=severity,
            message=(
                f"{n} fehlende Bar(s) zwischen {prev.open_time.isoformat()} und "
                f"{cur.open_time.isoformat()}"
            ),
            at=expected,
            context={"missing_bars": n},
        )
    )


def _finish(
    instrument: str,
    timeframe: Timeframe,
    now: datetime,
    as_of: datetime | None,
    n: int,
    issues: list[DataQualityIssue],
) -> DataQualityStatus:
    return DataQualityStatus(
        instrument=instrument,
        kind=DataKind.OHLCV,
        timeframe=timeframe,
        checked_at=now,
        as_of=as_of,
        bars_checked=n,
        issues=issues,
    )


# --------------------------------------------------------------------------------------------
# Session-/DST-Prüfung
# --------------------------------------------------------------------------------------------


def check_session_resolution(
    specs: Iterable[SessionSpec], day: date, *, now: datetime
) -> list[DataQualityIssue]:
    """Prüft, ob sich alle Sessions für ``day`` DST-sicher auflösen lassen."""
    issues: list[DataQualityIssue] = []
    for spec in specs:
        try:
            window: SessionWindow = resolve_session(spec, day)
        except TimeError as exc:
            issues.append(
                DataQualityIssue(
                    code=DataQualityCode.DST_AMBIGUOUS,
                    severity=_WARN,
                    message=f"Session {spec.name} @ {day}: {exc}",
                    at=datetime(day.year, day.month, day.day, tzinfo=UTC),
                )
            )
        else:
            _ = window
    return issues


__all__ = [
    "DEFAULT_POLICY",
    "QualityPolicy",
    "check_ohlcv_series",
    "check_session_resolution",
    "deduplicate_ohlcv",
    "sort_ohlcv",
]
