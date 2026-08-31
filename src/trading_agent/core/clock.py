"""Injizierbare Zeitquelle.

Kein Modul der Daten-, Analyse- oder Strategie-Schicht ruft ``datetime.now()`` direkt auf.
Stattdessen wird eine ``Clock`` übergeben. Das erlaubt:

* **Backtests / Point-in-Time**: ``SimClock`` liefert eine kontrollierte "Jetzt"-Zeit; alles,
  was danach liegt, ist per Definition unbekannt (Look-ahead-Schutz).
* **Paper / Live**: ``SystemClock`` liefert die echte UTC-Zeit.
* **Tests**: deterministische, verstellbare Zeit.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol, runtime_checkable

from trading_agent.core.time import ensure_utc


@runtime_checkable
class Clock(Protocol):
    """Minimaler Zeitquellen-Vertrag."""

    def now(self) -> datetime:
        """Aktuelle Zeit als tz-aware UTC-``datetime``."""
        ...


class SystemClock:
    """Echte Wanduhr (UTC). Für Paper/Live/Alltag."""

    def now(self) -> datetime:
        return datetime.now(UTC)


class SimClock:
    """Kontrollierte Zeit für Backtests und Tests.

    ``advance`` / ``set`` verstellen die Zeit. Die Zeit läuft NICHT von selbst weiter – der
    Backtest-Loop taktet sie explizit (Bar für Bar).
    """

    def __init__(self, start: datetime) -> None:
        self._now = ensure_utc(start)

    def now(self) -> datetime:
        return self._now

    def set(self, moment: datetime) -> None:
        moment = ensure_utc(moment)
        if moment < self._now:
            raise ValueError(
                f"SimClock darf nicht rückwärts laufen: {moment.isoformat()} < {self._now.isoformat()}"
            )
        self._now = moment

    def advance(self, delta: timedelta) -> datetime:
        if delta < timedelta(0):
            raise ValueError("SimClock.advance erwartet ein nicht-negatives Delta")
        self._now = self._now + delta
        return self._now


class FixedClock:
    """Unveränderliche Zeit – nur für kleine Unit-Tests."""

    def __init__(self, moment: datetime) -> None:
        self._now = ensure_utc(moment)

    def now(self) -> datetime:
        return self._now


__all__ = ["Clock", "FixedClock", "SimClock", "SystemClock"]
