"""Trading-Calendar: Handelszeiten, Feiertage, DST-sichere Session-Auflösung.

Getrennt von der intraday *Session*-Logik: der Kalender sagt "ist der Markt offen", die
Session sagt "welches Liquiditätsfenster" (Asia/London/NY).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from trading_agent.core.models import SessionWindow
from trading_agent.core.time import ensure_utc, load_zone, resolve_local_time
from trading_agent.refdata.models import SessionSpec, TradingCalendarSpec


class TradingCalendar:
    """Auswertung eines ``TradingCalendarSpec``."""

    def __init__(self, spec: TradingCalendarSpec) -> None:
        self.spec = spec
        self._holidays = set(spec.holidays)
        self._half_days = {h.day: h.close for h in spec.half_days}

    @property
    def calendar_id(self) -> str:
        return self.spec.calendar_id

    def is_trading_day(self, day: date) -> bool:
        if self.spec.is_24_7:
            return True
        if day in self._holidays:
            return False
        if self.spec.weekend_gap:
            return day.weekday() < 5
        return day.weekday() in self.spec.weekmask

    def _in_daily_break(self, ts: datetime) -> bool:
        if self.spec.daily_break_start is None or self.spec.daily_break_end is None:
            return False
        local = ts.astimezone(load_zone(self.spec.timezone)).time()
        start, end = self.spec.daily_break_start, self.spec.daily_break_end
        if start <= end:
            return start <= local < end
        return local >= start or local < end  # Pause über Mitternacht

    def is_open(self, ts: datetime) -> bool:
        """Ist der Markt zum UTC-Zeitpunkt ``ts`` geöffnet?"""
        ts = ensure_utc(ts)
        if self.spec.is_24_7:
            return True

        if self._in_daily_break(ts):
            return False

        if self.spec.weekend_gap:
            # Forex: Sonntag ~22:00 UTC bis Freitag ~22:00 UTC durchgehend offen.
            wd = ts.weekday()
            if wd == 5:  # Samstag
                return False
            if wd == 6:  # Sonntag
                return ts.hour >= 22
            if wd == 4:  # Freitag
                return ts.hour < 22
            return True

        # Reguläre Börse: in Lokalzeit prüfen.
        local = ts.astimezone(load_zone(self.spec.timezone))
        day = local.date()
        if not self.is_trading_day(day):
            return False
        close = self._half_days.get(day, self.spec.regular_close)
        return self.spec.regular_open <= local.time() < close

    def next_open(self, ts: datetime, *, max_days: int = 10) -> datetime | None:
        """Nächster Öffnungszeitpunkt ``>= ts`` (oder ``None``, wenn nicht in ``max_days``)."""
        ts = ensure_utc(ts)
        if self.is_open(ts):
            return ts
        if self.spec.is_24_7:
            return ts
        probe = ts
        for _ in range(max_days * 24 * 4):  # 15-Minuten-Schritte
            probe += timedelta(minutes=15)
            if self.is_open(probe):
                return probe
        return None


def resolve_session(spec: SessionSpec, day: date) -> SessionWindow:
    """Löst eine ``SessionSpec`` für einen Kalendertag DST-sicher nach UTC auf.

    Wirft ``TimeError`` (aus ``resolve_local_time``), wenn Start/Ende in eine DST-Lücke fallen
    oder mehrdeutig sind – die aufrufende Datenqualitäts-Logik muss das als
    ``DST_AMBIGUOUS`` behandeln, nicht raten.
    """
    start_utc = resolve_local_time(day, spec.start, spec.tz)
    end_day = day + timedelta(days=1) if spec.crosses_midnight else day
    end_utc = resolve_local_time(end_day, spec.end, spec.tz)
    return SessionWindow(name=spec.name, start=start_utc, end=end_utc)


def active_sessions(specs: list[SessionSpec], ts: datetime) -> list[SessionWindow]:
    """Alle Session-Fenster, die den UTC-Zeitpunkt ``ts`` enthalten (für den passenden Tag)."""
    ts = ensure_utc(ts)
    out: list[SessionWindow] = []
    for spec in specs:
        for day in (ts.date() - timedelta(days=1), ts.date()):
            if day.weekday() not in spec.weekdays:
                continue
            try:
                window = resolve_session(spec, day)
            except Exception:
                continue
            if window.contains(ts):
                out.append(window)
    return out


ALWAYS_OPEN = TradingCalendar(TradingCalendarSpec(calendar_id="always_open", is_24_7=True))


def utc_today(now: datetime | None = None) -> date:
    return (now or datetime.now(UTC)).astimezone(UTC).date()


__all__ = [
    "ALWAYS_OPEN",
    "TradingCalendar",
    "active_sessions",
    "resolve_session",
    "utc_today",
]
