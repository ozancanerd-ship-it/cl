"""CSV-Marktdaten-Provider.

Liest historische OHLCV-Daten, News und Makro-Zeitreihen aus lokalen CSV-Dateien. Alle
Zeitstempel müssen eine explizite Zeitzone tragen (``...Z`` oder Offset) – naive Timestamps
werden abgelehnt (das ist ein Timezone-Fehler, kein Rateanlass).

Verzeichnis-Konvention unter ``root``:

    ohlcv/<INSTRUMENT>_<TIMEFRAME>.csv   Spalten: open_time,open,high,low,close,volume[,quote_volume,trades]
    news.csv                            Spalten: event_id,event_type,impact,scheduled_time,available_time,affected_symbols[,actual,forecast,previous]
    macro.csv                           Spalten: series_id,reference_period,value,available_time[,revision,unit]
"""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from trading_agent.core.clock import Clock, SystemClock
from trading_agent.core.enums import DataKind, NewsImpact, Timeframe
from trading_agent.core.models import OHLCV, MacroEvent, NewsEvent
from trading_agent.core.time import (
    TimeError,
    bar_close_time,
    ensure_utc,
    parse_timestamp,
)
from trading_agent.data.health import HealthTracker
from trading_agent.data.interfaces import (
    HistoricalOHLCVProvider,
    MacroProvider,
    NewsProvider,
    ProviderStatus,
)
from trading_agent.data.quality import sort_ohlcv


class CsvProviderError(RuntimeError):
    pass


def _split_symbols(raw: str) -> list[str]:
    return [s.strip().upper() for s in raw.replace(";", "|").split("|") if s.strip()]


def _opt_float(raw: str | None) -> float | None:
    if raw is None or raw.strip() == "":
        return None
    return float(raw)


class CsvMarketDataProvider(HistoricalOHLCVProvider, NewsProvider, MacroProvider):
    name = "csv"
    provides = frozenset({DataKind.OHLCV, DataKind.NEWS, DataKind.MACRO})

    def __init__(self, root: str | Path, *, clock: Clock | None = None) -> None:
        self.root = Path(root)
        self._clock = clock or SystemClock()
        self._health = HealthTracker(self.name, clock=self._clock)

    def status(self) -> ProviderStatus:
        return self._health.status()

    # ------------------------------------------------------------------ ohlcv

    def _ohlcv_file(self, instrument: str, timeframe: Timeframe) -> Path:
        return self.root / "ohlcv" / f"{instrument.upper()}_{timeframe.value}.csv"

    def get_ohlcv(
        self, instrument: str, timeframe: Timeframe, start: datetime, end: datetime
    ) -> list[OHLCV]:
        path = self._ohlcv_file(instrument, timeframe)
        if not path.exists():
            self._health.record_failure(f"Datei fehlt: {path}")
            raise CsvProviderError(f"keine CSV-Datei: {path}")
        start = ensure_utc(start)
        end = ensure_utc(end)
        out: list[OHLCV] = []
        try:
            with path.open(newline="") as fh:
                reader = csv.DictReader(fh)
                required = {"open_time", "open", "high", "low", "close", "volume"}
                if reader.fieldnames is None or not required.issubset(reader.fieldnames):
                    raise CsvProviderError(
                        f"{path}: erwartete Spalten fehlen (mindestens {sorted(required)})"
                    )
                for lineno, row in enumerate(reader, start=2):
                    try:
                        ot = parse_timestamp(row["open_time"])
                    except TimeError as exc:
                        raise CsvProviderError(
                            f"{path}:{lineno}: ungültiger/naiver Timestamp {row['open_time']!r}: {exc}"
                        ) from exc
                    if not (start <= ot < end):
                        continue
                    bar = OHLCV(
                        instrument=instrument.upper(),
                        timeframe=timeframe,
                        open_time=ot,
                        close_time=bar_close_time(ot, timeframe),
                        open=float(row["open"]),
                        high=float(row["high"]),
                        low=float(row["low"]),
                        close=float(row["close"]),
                        volume=float(row["volume"]),
                        quote_volume=_opt_float(row.get("quote_volume")),
                        trades=int(row["trades"]) if row.get("trades") else None,
                        source=self.name,
                        ingested_at=self._clock.now(),
                    )
                    out.append(bar)
        except CsvProviderError:
            self._health.record_failure("CSV-Parsefehler")
            raise
        self._health.record_success()
        return sort_ohlcv(out)

    # ------------------------------------------------------------------ news

    def get_news(
        self,
        start: datetime,
        end: datetime,
        *,
        as_of: datetime | None = None,
        symbols: list[str] | None = None,
    ) -> list[NewsEvent]:
        path = self.root / "news.csv"
        start = ensure_utc(start)
        end = ensure_utc(end)
        as_of = ensure_utc(as_of) if as_of is not None else None
        want = {s.upper() for s in symbols} if symbols is not None else None
        if not path.exists():
            self._health.record_success()
            return []
        # pro event_id die neueste Revision, die zum Zeitpunkt as_of bereits bekannt war
        best: dict[str, NewsEvent] = {}
        with path.open(newline="") as fh:
            for lineno, row in enumerate(csv.DictReader(fh), start=2):
                try:
                    scheduled = parse_timestamp(row["scheduled_time"])
                    available = parse_timestamp(row["available_time"])
                except (TimeError, KeyError) as exc:
                    raise CsvProviderError(f"news.csv:{lineno}: {exc}") from exc
                if not (start <= scheduled < end):
                    continue
                if as_of is not None and available > as_of:
                    continue  # Point-in-Time: noch nicht bekannt
                ev = NewsEvent(
                    event_id=row["event_id"],
                    event_type=row["event_type"],
                    impact=NewsImpact(row["impact"].lower()),
                    scheduled_time=scheduled,
                    available_time=available,
                    affected_symbols=_split_symbols(row.get("affected_symbols", "")),
                    actual=_opt_float(row.get("actual")),
                    forecast=_opt_float(row.get("forecast")),
                    previous=_opt_float(row.get("previous")),
                    source=self.name,
                    ingested_at=self._clock.now(),
                )
                if want is not None and not (want & {s.upper() for s in ev.affected_symbols}):
                    continue
                cur = best.get(ev.event_id)
                if cur is None or ev.available_time > cur.available_time:
                    best[ev.event_id] = ev
        self._health.record_success()
        return sorted(best.values(), key=lambda e: e.scheduled_time)

    # ------------------------------------------------------------------ macro

    def get_macro(
        self,
        series_ids: list[str],
        start: datetime,
        end: datetime,
        *,
        as_of: datetime | None = None,
    ) -> list[MacroEvent]:
        path = self.root / "macro.csv"
        start = ensure_utc(start)
        end = ensure_utc(end)
        as_of = ensure_utc(as_of) if as_of is not None else None
        wanted = {s.upper() for s in series_ids}
        rows: list[MacroEvent] = []
        if not path.exists():
            self._health.record_success()
            return rows
        with path.open(newline="") as fh:
            for lineno, row in enumerate(csv.DictReader(fh), start=2):
                if row["series_id"].upper() not in wanted:
                    continue
                try:
                    ref = parse_timestamp(row["reference_period"])
                    available = parse_timestamp(row["available_time"])
                except (TimeError, KeyError) as exc:
                    raise CsvProviderError(f"macro.csv:{lineno}: {exc}") from exc
                if not (start <= ref < end):
                    continue
                if as_of is not None and available > as_of:
                    continue
                rows.append(
                    MacroEvent(
                        series_id=row["series_id"].upper(),
                        reference_period=ref,
                        value=float(row["value"]),
                        available_time=available,
                        revision=int(row["revision"]) if row.get("revision") else 0,
                        unit=row.get("unit") or None,
                        source=self.name,
                        ingested_at=self._clock.now(),
                    )
                )
        # pro (series_id, reference_period) die neueste bekannte Revision
        best: dict[tuple[str, datetime], MacroEvent] = {}
        for ev in rows:
            key = (ev.series_id, ev.reference_period)
            cur = best.get(key)
            if cur is None or ev.available_time > cur.available_time:
                best[key] = ev
        self._health.record_success()
        return sorted(best.values(), key=lambda e: (e.series_id, e.reference_period))


__all__ = ["CsvMarketDataProvider", "CsvProviderError"]
