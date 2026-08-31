"""Sessions — Liquiditätsfenster-Auflösung, Session High/Low, Session-Range, Entry-Gate.

Baut auf ``refdata.calendar.resolve_session`` (DST-sichere Börsenlokalzeit → UTC) auf. Entsperrt:

* ``session_high`` / ``session_low`` Liquidity Levels (``primitives.md`` §4.1)
* ``session_range`` als Premium/Discount-Referenz (``primitives.md`` §13)
* Session-Entry-Gate (``SMC-SWEEP-REV-01`` §18): ``SESSION_NOT_ALLOWED`` / ``SESSION_OPEN_BUFFER``
  / ``WEEKEND`` / ``PRE_WEEKEND_BUFFER``

Look-ahead-frei: ``completed_sessions`` liefert nur Fenster, die **vor** dem letzten Bar-Close
endeten; High/Low kommen ausschließlich aus Bars innerhalb des Fensters.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta

from trading_agent.core.enums import (
    LiquidityType,
    MarketSide,
    NoTradeReason,
    SessionName,
    Timeframe,
)
from trading_agent.core.models import OHLCV, SessionWindow
from trading_agent.core.time import ensure_utc
from trading_agent.refdata.calendar import resolve_session
from trading_agent.refdata.models import SessionSpec
from trading_agent.strategy.primitives.models import LiquidityLevel

# Die "großen" Session-Opens (§18 avoid_first_min / news.session_open_buffer_min)
BIG_OPEN_SESSIONS: tuple[SessionName, ...] = (SessionName.LONDON, SessionName.NEW_YORK)


@dataclasses.dataclass(frozen=True, slots=True)
class SessionFilterParams:
    allowed: tuple[SessionName, ...] = (
        SessionName.LONDON,
        SessionName.NEW_YORK,
        SessionName.LONDON_NY_OVERLAP,
    )
    avoid_first_min: int = 15
    avoid_weekend: bool = True
    avoid_pre_weekend_min: int = 60


# --------------------------------------------------------------------------------- Auflösung


def resolve_sessions(specs: Sequence[SessionSpec], day: datetime | date) -> list[SessionWindow]:
    """Alle Session-Fenster für einen Kalendertag (UTC), inkl. abgeleitetem ``LONDON_NY_OVERLAP``."""
    d = day.date() if isinstance(day, datetime) else day
    out: list[SessionWindow] = []
    by_name: dict[SessionName, SessionWindow] = {}
    for spec in specs:
        if d.weekday() not in spec.weekdays:
            continue
        try:
            w = resolve_session(spec, d)
        except Exception:  # DST-Lücke / mehrdeutig -> Datenqualität behandelt das, nicht hier
            continue
        out.append(w)
        by_name[spec.name] = w

    lon, ny = by_name.get(SessionName.LONDON), by_name.get(SessionName.NEW_YORK)
    if lon is not None and ny is not None:
        start, end = max(lon.start, ny.start), min(lon.end, ny.end)
        if end > start:
            out.append(SessionWindow(name=SessionName.LONDON_NY_OVERLAP, start=start, end=end))
    return sorted(out, key=lambda w: w.start)


def active_session_names(specs: Sequence[SessionSpec], ts: datetime) -> set[SessionName]:
    """Session-Namen, deren Fenster den UTC-Zeitpunkt ``ts`` enthalten."""
    ts = ensure_utc(ts)
    names: set[SessionName] = set()
    for day in (ts - timedelta(days=1), ts):
        for w in resolve_sessions(specs, day):
            if w.contains(ts):
                names.add(w.name)
    return names


# --------------------------------------------------------------- abgeschlossene Sessions + High/Low


def _day_range(bars: Sequence[OHLCV]) -> list[datetime]:
    start = ensure_utc(bars[0].open_time) - timedelta(days=1)
    end = ensure_utc(bars[-1].open_time)
    days: list[datetime] = []
    cur = start.replace(hour=0, minute=0, second=0, microsecond=0)
    while cur <= end:
        days.append(cur)
        cur += timedelta(days=1)
    return days


def completed_sessions(
    bars: Sequence[OHLCV],
    specs: Sequence[SessionSpec],
    *,
    name: SessionName | None = None,
) -> list[SessionWindow]:
    """Session-Fenster, die **vor** ``bars[-1].close_time`` endeten, mit High/Low aus den Bars darin."""
    if not bars:
        return []
    now = ensure_utc(bars[-1].close_time)
    out: list[SessionWindow] = []
    for day in _day_range(bars):
        for w in resolve_sessions(specs, day):
            if name is not None and w.name is not name:
                continue
            if w.end > now:
                continue
            inside = [b for b in bars if w.start <= ensure_utc(b.open_time) < w.end]
            if not inside:
                continue
            out.append(
                w.model_copy(
                    update={
                        "high": max(b.high for b in inside),
                        "low": min(b.low for b in inside),
                    }
                )
            )
    return sorted(out, key=lambda w: w.end)


def last_completed_session(
    bars: Sequence[OHLCV], specs: Sequence[SessionSpec], name: SessionName
) -> SessionWindow | None:
    cs = completed_sessions(bars, specs, name=name)
    return cs[-1] if cs else None


def session_range(
    bars: Sequence[OHLCV],
    specs: Sequence[SessionSpec],
    *,
    name: SessionName = SessionName.NEW_YORK,
) -> tuple[float, float] | None:
    """[low, high] der letzten abgeschlossenen Session ``name`` — für die ``session_range`` PD-Referenz."""
    w = last_completed_session(bars, specs, name)
    if w is None or w.high is None or w.low is None or w.high <= w.low:
        return None
    return w.low, w.high


def session_levels(
    windows: Sequence[SessionWindow], *, timeframe: Timeframe = Timeframe.H4
) -> list[LiquidityLevel]:
    """``session_high`` / ``session_low`` Liquidity Levels aus abgeschlossenen Session-Fenstern."""
    out: list[LiquidityLevel] = []
    for w in windows:
        if w.high is not None:
            out.append(
                LiquidityLevel(
                    type=LiquidityType.SESSION_HIGH,
                    side=MarketSide.BUY_SIDE,
                    price=w.high,
                    timeframe=timeframe,
                    formed_at=w.end,
                )
            )
        if w.low is not None:
            out.append(
                LiquidityLevel(
                    type=LiquidityType.SESSION_LOW,
                    side=MarketSide.SELL_SIDE,
                    price=w.low,
                    timeframe=timeframe,
                    formed_at=w.end,
                )
            )
    return out


# --------------------------------------------------------------------------------- §18 Gate


def session_filter(
    now: datetime,
    specs: Sequence[SessionSpec],
    *,
    params: SessionFilterParams | None = None,
) -> NoTradeReason | None:
    """Session-Entry-Gate (``SMC-SWEEP-REV-01`` §18). ``None`` = ok."""
    p = params or SessionFilterParams()
    ts = ensure_utc(now)

    if p.avoid_weekend and ts.weekday() >= 5:  # Sa / So (UTC)
        return NoTradeReason.WEEKEND

    if p.avoid_pre_weekend_min > 0 and ts.weekday() == 4:  # Freitag
        sat = datetime.combine(ts.date() + timedelta(days=1), time.min, tzinfo=UTC)
        if sat - ts <= timedelta(minutes=p.avoid_pre_weekend_min):
            return NoTradeReason.PRE_WEEKEND_BUFFER

    active = active_session_names(specs, ts)
    if not (active & set(p.allowed)):
        return NoTradeReason.SESSION_NOT_ALLOWED

    if p.avoid_first_min > 0:
        for day in (ts - timedelta(days=1), ts):
            for w in resolve_sessions(specs, day):
                if w.name in BIG_OPEN_SESSIONS and w.start <= ts < w.start + timedelta(
                    minutes=p.avoid_first_min
                ):
                    return NoTradeReason.SESSION_OPEN_BUFFER

    return None


__all__ = [
    "BIG_OPEN_SESSIONS",
    "SessionFilterParams",
    "active_session_names",
    "completed_sessions",
    "last_completed_session",
    "resolve_sessions",
    "session_filter",
    "session_levels",
    "session_range",
]
