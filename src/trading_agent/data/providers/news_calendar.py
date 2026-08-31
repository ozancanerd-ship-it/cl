"""Economic-Calendar / News-Adapter — **Point-in-Time zwingend, keine Zukunftsdaten**.

Zwei Phasen je Termin:

1. **geplant** — der Termin ist im Kalender (``scheduled_time`` bekannt), ``actual = None``.
   ``available_time`` = wann der Kalender-Eintrag veröffentlicht wurde (meist Wochen vorher).
   → treibt den Pre-Positioning-Ban / Blackout **vor** dem Event.
2. **veröffentlicht** — nach ``scheduled_time`` kommt der Ist-Wert. Neuer/aktualisierter
   ``NewsEvent`` mit ``actual`` gesetzt, ``available_time >= scheduled_time``.

Ein Backtest darf zu einem ``cutoff`` **nur** Einträge mit ``available_time <= cutoff`` sehen —
das erzwingen ``MarketDataRepository.read_news`` und ``MarketContext.__post_init__``.

Kanonische High-Impact-Serien: FOMC-Zins, US-CPI, US-PCE, US-NFP, EZB-Zins (erweiterbar).

Live-Fetch braucht eine echte Kalender-Quelle mit Zeitstempel-Historie (Adapter deklariert die
nötigen ENV-Variablen, hält **keine** Werte). Ohne Quelle: ``CsvEconomicCalendar`` liest eine
lokale, manuell gepflegte CSV — **nichts wird erfunden**.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

from trading_agent.core.enums import DataKind, NewsImpact
from trading_agent.core.models import NewsEvent
from trading_agent.core.time import ensure_utc, parse_timestamp
from trading_agent.data.providers.adapter_base import AdapterInfo, CredentialSpec, LiveDataAdapter

# kanonischer Event-Typ → Standard-Impact (Kalender-Quellen weichen im Namen ab)
CANONICAL_EVENTS: dict[str, NewsImpact] = {
    "FOMC_RATE": NewsImpact.HIGH,
    "FOMC_MINUTES": NewsImpact.MEDIUM,
    "US_CPI": NewsImpact.HIGH,
    "US_CORE_CPI": NewsImpact.HIGH,
    "US_PCE": NewsImpact.HIGH,
    "US_NFP": NewsImpact.HIGH,
    "US_UNEMPLOYMENT": NewsImpact.MEDIUM,
    "US_GDP": NewsImpact.MEDIUM,
    "US_RETAIL_SALES": NewsImpact.MEDIUM,
    "ECB_RATE": NewsImpact.HIGH,
    "ECB_PRESS_CONF": NewsImpact.MEDIUM,
}

_IMPACT = {"low": NewsImpact.LOW, "medium": NewsImpact.MEDIUM, "high": NewsImpact.HIGH}


class EconomicCalendarAdapter(LiveDataAdapter):
    """Basis für Live-Kalender-Provider (z. B. ein offizieller Wirtschaftskalender-Feed).
    Konkrete Live-Implementierungen kommen mit Phase 9+; hier steht der Vertrag."""

    def __init__(self, *, name: str, env_vars: tuple[str, ...] = ()) -> None:
        super().__init__(
            AdapterInfo(
                name=name,
                asset_classes=("crypto", "forex", "equity", "gold"),
                data_kinds=(DataKind.OHLCV,),  # News/Macro-Kind falls im Enum vorhanden
                modes=("historical", "stream"),
                credentials=CredentialSpec(
                    provider=name,
                    env_vars=env_vars,
                    read_only=True,
                    note="Wirtschaftskalender — nur Lesezugriff, keine Orders",
                ),
                redistribution_allowed=False,
                note="Point-in-Time zwingend; jede Zeile trägt available_time",
            )
        )

    def get_calendar(
        self, start: datetime, end: datetime, *, as_of: datetime | None = None
    ) -> list[NewsEvent]:  # pragma: no cover - Live-Impl folgt
        raise NotImplementedError("Live-Kalender-Fetch ist Phase 9+ (braucht echte Quelle)")


class CsvEconomicCalendar(EconomicCalendarAdapter):
    """Liest einen lokal gepflegten Kalender (CSV). Kein Netz, kein Fake.

    Spalten: ``event_id,event_type,impact,scheduled_time,available_time[,actual,forecast,previous,affected_symbols]``
    Alle Zeitstempel mit expliziter Zeitzone. ``available_time`` **ist Pflicht** je Zeile.
    """

    def __init__(self, path: str | Path) -> None:
        super().__init__(name="csv_economic_calendar")
        self.path = Path(path)

    def _rows(self) -> Iterable[dict[str, str]]:
        if not self.path.exists():
            self._fail(f"Kalender-CSV fehlt: {self.path}")
            return
        with self.path.open(newline="") as fh:
            yield from csv.DictReader(fh)

    def get_calendar(
        self, start: datetime, end: datetime, *, as_of: datetime | None = None
    ) -> list[NewsEvent]:
        start, end = ensure_utc(start), ensure_utc(end)
        as_of_dt = ensure_utc(as_of) if as_of is not None else None
        out: list[NewsEvent] = []
        for row in self._rows():
            sched = parse_timestamp(row["scheduled_time"])
            if not (start <= sched < end):
                continue
            avail = parse_timestamp(row["available_time"])
            if as_of_dt is not None and avail > as_of_dt:
                continue  # Point-in-Time: noch nicht bekannt
            etype = row["event_type"].strip().upper()
            impact = _IMPACT.get(
                row.get("impact", "").strip().lower(),
                CANONICAL_EVENTS.get(etype, NewsImpact.MEDIUM),
            )
            syms = [
                s.strip().upper()
                for s in row.get("affected_symbols", "").replace(";", "|").split("|")
                if s.strip()
            ]
            out.append(
                NewsEvent(
                    event_id=row["event_id"].strip(),
                    event_type=etype,
                    impact=impact,
                    scheduled_time=sched,
                    available_time=avail,
                    affected_symbols=syms,
                    actual=_optf(row.get("actual")),
                    forecast=_optf(row.get("forecast")),
                    previous=_optf(row.get("previous")),
                )
            )
        if out:
            self._ok()
        return sorted(out, key=lambda e: (e.scheduled_time, e.available_time))


def _optf(raw: str | None) -> float | None:
    if raw is None or raw.strip() == "":
        return None
    try:
        return float(raw)
    except ValueError:
        return None


__all__ = ["CANONICAL_EVENTS", "CsvEconomicCalendar", "EconomicCalendarAdapter"]
