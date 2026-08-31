"""Lokales Daten-Repository.

* **Parquet** für historische Marktdaten (OHLCV, Funding) – spaltenorientiert, kompakt,
  gut für große Zeitreihen.
* **SQLite** für Metadaten (Dataset-Abdeckung, Ingestion-Historie) sowie für **News/Makro**
  (klein, muss flexibel Point-in-Time abgefragt werden, inkl. Revisionen).

**Point-in-Time**: jede Leseoperation akzeptiert ``as_of``. Dann werden ausschließlich Records
zurückgegeben, deren ``available_time <= as_of`` ist:

* OHLCV / Funding: ``available_time`` = ``close_time`` bzw. ``ts``.
* News / Makro: ``available_time`` = die echte Veröffentlichungszeit (Feld im Modell).

Damit sind Look-ahead-Bias und Future-Information-Leakage strukturell ausgeschlossen.

Keine Secrets, keine privaten Accountdaten in diesem Store.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from trading_agent.core.enums import NewsImpact, Timeframe
from trading_agent.core.models import OHLCV, Funding, MacroEvent, NewsEvent
from trading_agent.core.time import ensure_utc, to_epoch_ms
from trading_agent.core.version import REPOSITORY_LAYOUT_VERSION
from trading_agent.data.quality import deduplicate_ohlcv, sort_ohlcv

_OHLCV_SCHEMA = pa.schema(
    [
        ("instrument", pa.string()),
        ("timeframe", pa.string()),
        ("open_time", pa.timestamp("us", tz="UTC")),
        ("close_time", pa.timestamp("us", tz="UTC")),
        ("open", pa.float64()),
        ("high", pa.float64()),
        ("low", pa.float64()),
        ("close", pa.float64()),
        ("volume", pa.float64()),
        ("quote_volume", pa.float64()),
        ("trades", pa.int64()),
        ("source", pa.string()),
        ("ingested_at", pa.timestamp("us", tz="UTC")),
    ]
)

_FUNDING_SCHEMA = pa.schema(
    [
        ("instrument", pa.string()),
        ("ts", pa.timestamp("us", tz="UTC")),
        ("rate", pa.float64()),
        ("interval_hours", pa.float64()),
        ("source", pa.string()),
        ("ingested_at", pa.timestamp("us", tz="UTC")),
    ]
)


class RepositoryError(RuntimeError):
    pass


class MarketDataRepository:
    """Dateibasiertes Repository unter ``root``."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.ohlcv_dir = self.root / "ohlcv"
        self.funding_dir = self.root / "funding"
        self.meta_path = self.root / "meta.sqlite"
        self.ohlcv_dir.mkdir(parents=True, exist_ok=True)
        self.funding_dir.mkdir(parents=True, exist_ok=True)
        self._init_meta()

    # ---------------------------------------------------------------- meta / sqlite

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.meta_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.row_factory = sqlite3.Row
        return conn

    def _init_meta(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS repo_info (
                    key TEXT PRIMARY KEY, value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS datasets (
                    instrument TEXT NOT NULL,
                    timeframe  TEXT NOT NULL,
                    first_open_ms INTEGER,
                    last_open_ms  INTEGER,
                    row_count INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (instrument, timeframe)
                );
                CREATE TABLE IF NOT EXISTS ingestions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    instrument TEXT,
                    timeframe TEXT,
                    rows INTEGER NOT NULL,
                    source TEXT
                );
                CREATE TABLE IF NOT EXISTS news (
                    event_id TEXT NOT NULL,
                    revision_key TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    impact TEXT NOT NULL,
                    scheduled_ms INTEGER NOT NULL,
                    available_ms INTEGER NOT NULL,
                    affected_symbols TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    PRIMARY KEY (event_id, revision_key)
                );
                CREATE INDEX IF NOT EXISTS ix_news_time ON news(scheduled_ms, available_ms);
                CREATE TABLE IF NOT EXISTS macro (
                    series_id TEXT NOT NULL,
                    reference_ms INTEGER NOT NULL,
                    revision INTEGER NOT NULL,
                    available_ms INTEGER NOT NULL,
                    payload TEXT NOT NULL,
                    PRIMARY KEY (series_id, reference_ms, revision)
                );
                CREATE INDEX IF NOT EXISTS ix_macro_time ON macro(series_id, reference_ms, available_ms);
                """
            )
            conn.execute(
                "INSERT OR IGNORE INTO repo_info(key, value) VALUES (?, ?)",
                ("layout_version", str(REPOSITORY_LAYOUT_VERSION)),
            )

    def _record_ingestion(
        self,
        conn: sqlite3.Connection,
        *,
        kind: str,
        rows: int,
        instrument: str | None = None,
        timeframe: str | None = None,
        source: str | None = None,
    ) -> None:
        conn.execute(
            "INSERT INTO ingestions(ts, kind, instrument, timeframe, rows, source) VALUES (?,?,?,?,?,?)",
            (datetime.now(UTC).isoformat(), kind, instrument, timeframe, rows, source),
        )

    # ---------------------------------------------------------------- OHLCV (parquet)

    def _ohlcv_path(self, instrument: str, timeframe: Timeframe) -> Path:
        d = self.ohlcv_dir / f"instrument={instrument.upper()}" / f"timeframe={timeframe.value}"
        d.mkdir(parents=True, exist_ok=True)
        return d / "data.parquet"

    def write_ohlcv(self, bars: Iterable[OHLCV]) -> int:
        """Schreibt/merged Bars. Merge-Regel: Duplikate nach ``open_time`` -> neuestes
        ``ingested_at`` gewinnt; danach sortiert. Rückgabe: Gesamtzeilen nach Merge."""
        by_key: dict[tuple[str, Timeframe], list[OHLCV]] = {}
        for bar in bars:
            by_key.setdefault((bar.instrument.upper(), bar.timeframe), []).append(bar)

        total = 0
        with self._connect() as conn:
            for (instrument, timeframe), new_bars in by_key.items():
                path = self._ohlcv_path(instrument, timeframe)
                existing = self._read_ohlcv_file(path)
                merged = _merge_ohlcv(existing, new_bars)
                self._write_ohlcv_file(path, merged)
                total += len(merged)
                self._update_dataset(conn, instrument, timeframe.value, merged)
                self._record_ingestion(
                    conn,
                    kind="ohlcv",
                    rows=len(new_bars),
                    instrument=instrument,
                    timeframe=timeframe.value,
                    source=new_bars[0].source,
                )
        return total

    def read_ohlcv(
        self,
        instrument: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
        *,
        as_of: datetime | None = None,
    ) -> list[OHLCV]:
        """Bars mit ``start <= open_time < end``. ``as_of``: nur ``close_time <= as_of``."""
        start = ensure_utc(start)
        end = ensure_utc(end)
        as_of = ensure_utc(as_of) if as_of is not None else None
        if end <= start:
            raise RepositoryError(f"end {end} <= start {start}")

        bars = self._read_ohlcv_file(self._ohlcv_path(instrument, timeframe))
        out = [
            b
            for b in bars
            if start <= b.open_time < end and (as_of is None or b.close_time <= as_of)
        ]
        return sort_ohlcv(out)

    def ohlcv_coverage(
        self, instrument: str, timeframe: Timeframe
    ) -> tuple[datetime, datetime] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT first_open_ms, last_open_ms, row_count FROM datasets "
                "WHERE instrument=? AND timeframe=?",
                (instrument.upper(), timeframe.value),
            ).fetchone()
        if row is None or row["row_count"] == 0 or row["first_open_ms"] is None:
            return None
        from trading_agent.core.time import from_epoch_ms

        return from_epoch_ms(row["first_open_ms"]), from_epoch_ms(row["last_open_ms"])

    def dataset_fingerprint(
        self, instrument: str, timeframe: Timeframe, *, as_of: datetime | None = None
    ) -> str:
        """Deterministischer SHA-256 über die (ggf. Point-in-Time gefilterten) Bars.

        Grundlage für ``RunManifest`` / Reproduzierbarkeit ab Phase 2.
        """
        bars = self._read_ohlcv_file(self._ohlcv_path(instrument, timeframe))
        if as_of is not None:
            as_of = ensure_utc(as_of)
            bars = [b for b in bars if b.close_time <= as_of]
        h = hashlib.sha256()
        h.update(
            f"{instrument.upper()}|{timeframe.value}|layout{REPOSITORY_LAYOUT_VERSION}".encode()
        )
        for b in sort_ohlcv(bars):
            h.update(
                f"{to_epoch_ms(b.open_time)},{b.open!r},{b.high!r},{b.low!r},{b.close!r},{b.volume!r};".encode()
            )
        return h.hexdigest()

    def _read_ohlcv_file(self, path: Path) -> list[OHLCV]:
        if not path.exists():
            return []
        table = pq.read_table(path)
        out: list[OHLCV] = []
        for row in table.to_pylist():
            out.append(
                OHLCV(
                    instrument=row["instrument"],
                    timeframe=Timeframe(row["timeframe"]),
                    open_time=_as_dt(row["open_time"]),
                    close_time=_as_dt(row["close_time"]),
                    open=row["open"],
                    high=row["high"],
                    low=row["low"],
                    close=row["close"],
                    volume=row["volume"],
                    quote_volume=row["quote_volume"],
                    trades=row["trades"],
                    source=row["source"] or "unknown",
                    ingested_at=_as_dt(row["ingested_at"]) if row["ingested_at"] else None,
                )
            )
        return out

    def _write_ohlcv_file(self, path: Path, bars: list[OHLCV]) -> None:
        cols: dict[str, list[object]] = {name: [] for name in _OHLCV_SCHEMA.names}
        for b in bars:
            cols["instrument"].append(b.instrument)
            cols["timeframe"].append(b.timeframe.value)
            cols["open_time"].append(b.open_time)
            cols["close_time"].append(b.close_time)
            cols["open"].append(b.open)
            cols["high"].append(b.high)
            cols["low"].append(b.low)
            cols["close"].append(b.close)
            cols["volume"].append(b.volume)
            cols["quote_volume"].append(b.quote_volume)
            cols["trades"].append(b.trades)
            cols["source"].append(b.source)
            cols["ingested_at"].append(b.ingested_at)
        table = pa.table(cols, schema=_OHLCV_SCHEMA)
        tmp = path.with_suffix(".parquet.tmp")
        pq.write_table(table, tmp)
        tmp.replace(path)

    def _update_dataset(
        self, conn: sqlite3.Connection, instrument: str, timeframe: str, bars: list[OHLCV]
    ) -> None:
        if not bars:
            return
        first_ms = min(to_epoch_ms(b.open_time) for b in bars)
        last_ms = max(to_epoch_ms(b.open_time) for b in bars)
        conn.execute(
            """
            INSERT INTO datasets(instrument, timeframe, first_open_ms, last_open_ms, row_count, updated_at)
            VALUES (?,?,?,?,?,?)
            ON CONFLICT(instrument, timeframe) DO UPDATE SET
                first_open_ms=excluded.first_open_ms,
                last_open_ms=excluded.last_open_ms,
                row_count=excluded.row_count,
                updated_at=excluded.updated_at
            """,
            (instrument, timeframe, first_ms, last_ms, len(bars), datetime.now(UTC).isoformat()),
        )

    # ---------------------------------------------------------------- Funding (parquet)

    def _funding_path(self, instrument: str) -> Path:
        d = self.funding_dir / f"instrument={instrument.upper()}"
        d.mkdir(parents=True, exist_ok=True)
        return d / "data.parquet"

    def write_funding(self, rows: Iterable[Funding]) -> int:
        by_inst: dict[str, list[Funding]] = {}
        for r in rows:
            by_inst.setdefault(r.instrument.upper(), []).append(r)
        total = 0
        with self._connect() as conn:
            for instrument, new_rows in by_inst.items():
                path = self._funding_path(instrument)
                existing = self._read_funding_file(path)
                merged = {r.ts: r for r in (*existing, *new_rows)}
                ordered = [merged[k] for k in sorted(merged)]
                self._write_funding_file(path, ordered)
                total += len(ordered)
                self._record_ingestion(
                    conn, kind="funding", rows=len(new_rows), instrument=instrument
                )
        return total

    def read_funding(
        self,
        instrument: str,
        start: datetime,
        end: datetime,
        *,
        as_of: datetime | None = None,
    ) -> list[Funding]:
        start = ensure_utc(start)
        end = ensure_utc(end)
        as_of = ensure_utc(as_of) if as_of is not None else None
        rows = self._read_funding_file(self._funding_path(instrument))
        return [r for r in rows if start <= r.ts < end and (as_of is None or r.ts <= as_of)]

    def _read_funding_file(self, path: Path) -> list[Funding]:
        if not path.exists():
            return []
        out: list[Funding] = []
        for row in pq.read_table(path).to_pylist():
            out.append(
                Funding(
                    instrument=row["instrument"],
                    ts=_as_dt(row["ts"]),
                    rate=row["rate"],
                    interval_hours=row["interval_hours"],
                    source=row["source"] or "unknown",
                    ingested_at=_as_dt(row["ingested_at"]) if row["ingested_at"] else None,
                )
            )
        return out

    def _write_funding_file(self, path: Path, rows: list[Funding]) -> None:
        cols: dict[str, list[object]] = {name: [] for name in _FUNDING_SCHEMA.names}
        for r in rows:
            cols["instrument"].append(r.instrument)
            cols["ts"].append(r.ts)
            cols["rate"].append(r.rate)
            cols["interval_hours"].append(r.interval_hours)
            cols["source"].append(r.source)
            cols["ingested_at"].append(r.ingested_at)
        tmp = path.with_suffix(".parquet.tmp")
        pq.write_table(pa.table(cols, schema=_FUNDING_SCHEMA), tmp)
        tmp.replace(path)

    # ---------------------------------------------------------------- News / Macro (sqlite, PIT)

    def write_news(self, events: Iterable[NewsEvent]) -> int:
        n = 0
        with self._connect() as conn:
            for ev in events:
                revision_key = f"{to_epoch_ms(ev.available_time)}"
                conn.execute(
                    """
                    INSERT OR REPLACE INTO news
                    (event_id, revision_key, event_type, impact, scheduled_ms, available_ms,
                     affected_symbols, payload)
                    VALUES (?,?,?,?,?,?,?,?)
                    """,
                    (
                        ev.event_id,
                        revision_key,
                        ev.event_type,
                        ev.impact.value,
                        to_epoch_ms(ev.scheduled_time),
                        to_epoch_ms(ev.available_time),
                        json.dumps(ev.affected_symbols),
                        ev.model_dump_json(),
                    ),
                )
                n += 1
            self._record_ingestion(conn, kind="news", rows=n)
        return n

    def read_news(
        self,
        start: datetime,
        end: datetime,
        *,
        as_of: datetime | None = None,
        symbols: Iterable[str] | None = None,
        impact_at_least: NewsImpact | None = None,
    ) -> list[NewsEvent]:
        start_ms, end_ms = to_epoch_ms(ensure_utc(start)), to_epoch_ms(ensure_utc(end))
        as_of_ms = to_epoch_ms(ensure_utc(as_of)) if as_of is not None else None
        want = {s.upper() for s in symbols} if symbols is not None else None
        order = {NewsImpact.LOW: 0, NewsImpact.MEDIUM: 1, NewsImpact.HIGH: 2}
        min_rank = order[impact_at_least] if impact_at_least is not None else -1

        with self._connect() as conn:
            rows = conn.execute(
                "SELECT payload, available_ms FROM news WHERE scheduled_ms >= ? AND scheduled_ms < ?",
                (start_ms, end_ms),
            ).fetchall()

        # Point-in-Time: pro event_id die neueste Revision mit available_ms <= as_of.
        best: dict[str, tuple[int, NewsEvent]] = {}
        for row in rows:
            if as_of_ms is not None and row["available_ms"] > as_of_ms:
                continue
            ev = NewsEvent.model_validate_json(row["payload"])
            if want is not None and not (want & {s.upper() for s in ev.affected_symbols}):
                continue
            if order[ev.impact] < min_rank:
                continue
            cur = best.get(ev.event_id)
            if cur is None or row["available_ms"] > cur[0]:
                best[ev.event_id] = (row["available_ms"], ev)
        return sorted((ev for _, ev in best.values()), key=lambda e: e.scheduled_time)

    def write_macro(self, events: Iterable[MacroEvent]) -> int:
        n = 0
        with self._connect() as conn:
            for ev in events:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO macro
                    (series_id, reference_ms, revision, available_ms, payload)
                    VALUES (?,?,?,?,?)
                    """,
                    (
                        ev.series_id,
                        to_epoch_ms(ev.reference_period),
                        ev.revision,
                        to_epoch_ms(ev.available_time),
                        ev.model_dump_json(),
                    ),
                )
                n += 1
            self._record_ingestion(conn, kind="macro", rows=n)
        return n

    def read_macro(
        self,
        series_ids: Iterable[str],
        start: datetime,
        end: datetime,
        *,
        as_of: datetime | None = None,
    ) -> list[MacroEvent]:
        start_ms, end_ms = to_epoch_ms(ensure_utc(start)), to_epoch_ms(ensure_utc(end))
        as_of_ms = to_epoch_ms(ensure_utc(as_of)) if as_of is not None else None
        ids = tuple(series_ids)
        if not ids:
            return []
        placeholders = ",".join("?" * len(ids))
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT payload, reference_ms, available_ms FROM macro "
                f"WHERE series_id IN ({placeholders}) AND reference_ms >= ? AND reference_ms < ?",
                (*ids, start_ms, end_ms),
            ).fetchall()

        # pro (series_id, reference_period): neueste bekannte Revision <= as_of
        best: dict[tuple[str, int], tuple[int, MacroEvent]] = {}
        for row in rows:
            if as_of_ms is not None and row["available_ms"] > as_of_ms:
                continue
            ev = MacroEvent.model_validate_json(row["payload"])
            key = (ev.series_id, row["reference_ms"])
            cur = best.get(key)
            if cur is None or row["available_ms"] > cur[0]:
                best[key] = (row["available_ms"], ev)
        return sorted(
            (ev for _, ev in best.values()),
            key=lambda e: (e.series_id, e.reference_period),
        )

    # ---------------------------------------------------------------- Introspektion

    def ingestion_log(self, limit: int = 100) -> list[dict[str, object]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM ingestions ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]


def _as_dt(value: object) -> datetime:
    if isinstance(value, datetime):
        return ensure_utc(value)
    raise RepositoryError(f"unerwarteter Zeitwert aus Parquet: {value!r}")


def _merge_ohlcv(existing: list[OHLCV], new: list[OHLCV]) -> list[OHLCV]:
    by_time: dict[datetime, OHLCV] = {b.open_time: b for b in existing}
    for b in new:
        prev = by_time.get(b.open_time)
        if prev is None:
            by_time[b.open_time] = b
            continue
        # neuestes ingested_at gewinnt; fehlt es, gewinnt der neue Wert.
        p_ing = prev.ingested_at
        b_ing = b.ingested_at
        if b_ing is None or p_ing is None or b_ing >= p_ing:
            by_time[b.open_time] = b
    merged = [by_time[k] for k in sorted(by_time)]
    cleaned, _conflicts = deduplicate_ohlcv(merged)
    return sort_ohlcv(cleaned)


__all__ = ["MarketDataRepository", "RepositoryError"]
