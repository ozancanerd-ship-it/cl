"""Binance Vision — **offizieller Bulk-Datendienst** von Binance für historische Marktdaten.

* Basis-URL: ``https://data.binance.vision/data/spot/`` — öffentlich, **kein API-Key**, keine
  Rate-Limits, keine privaten Account-Daten.
* Struktur: ``{monthly|daily}/klines/{SYMBOL}/{interval}/{SYMBOL}-{interval}-{YYYY-MM[-DD]}.zip``
  plus je eine ``<datei>.zip.CHECKSUM`` (SHA-256) daneben.
* Jede ZIP enthält **eine** CSV ohne Header. Spalten (Spot-Klines):

  ``open_time, open, high, low, close, volume, close_time, quote_volume, trades,
    taker_buy_base, taker_buy_quote, ignore``

**Zeitzone / Timestamp-Konvention.** Alle Zeitstempel sind **UTC**, Epoch-Integer. Historisch
in **Millisekunden**; Dateien ab ~2025-01 in **Mikrosekunden** — wird hier über die
Größenordnung erkannt und normalisiert.

**OHLCV-Definition.** ``open_time`` = Intervallbeginn (inklusiv, an das Intervall ausgerichtet).
``open`` = erster Trade-Preis im Intervall, ``high``/``low`` = Extrema, ``close`` = letzter
Trade-Preis, ``volume`` = Basis-Asset-Menge. Binance liefert ``close_time`` als *letzte
Millisekunde* der Kerze (``open_time + interval − 1ms``); wir verwerfen das und setzen
``close_time = open_time + interval`` (Projekt-Konvention, ``core.time.bar_close_time``).

**Kein Fake.** Fehlt eine Monats-/Tagesdatei, wird sie **übersprungen und protokolliert** —
es werden keine Bars synthetisiert. Die anschließende ``data.quality`` / ``engine.replay``-
Validierung deckt die entstehende Lücke auf.
"""

from __future__ import annotations

import csv
import hashlib
import io
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from trading_agent.core.enums import DataKind, ProviderHealth, Timeframe
from trading_agent.core.models import OHLCV
from trading_agent.core.time import bar_close_time, ensure_utc, is_aligned
from trading_agent.data.interfaces import HistoricalOHLCVProvider, ProviderStatus
from trading_agent.data.quality import deduplicate_ohlcv, sort_ohlcv
from trading_agent.utils.logging import get_logger

_log = get_logger("binance_vision")

_BASE = "https://data.binance.vision/data/spot"
_INTERVAL: dict[Timeframe, str] = {
    Timeframe.M1: "1m",
    Timeframe.M5: "5m",
    Timeframe.M15: "15m",
    Timeframe.M30: "30m",
    Timeframe.H1: "1h",
    Timeframe.H4: "4h",
    Timeframe.D1: "1d",
    Timeframe.W1: "1w",
}
# ab dieser Größe ist der Epoch-Wert Mikro- statt Millisekunden (≈ Jahr 5138 in ms)
_US_THRESHOLD = 1e14


class BinanceVisionError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class _FileRef:
    url: str
    checksum_url: str
    name: str
    kind: str  # "monthly" | "daily"


@dataclass(slots=True)
class BinanceVisionProvider(HistoricalOHLCVProvider):
    """Lädt Binance-Vision-Bulk-Dateien, verifiziert die SHA-256-Checksummen und normalisiert
    zu ``OHLCV``. Heruntergeladene ZIPs werden lokal gecacht (Re-Runs ohne Netz)."""

    name: str = "binance_vision"
    provides: frozenset[DataKind] = field(default_factory=lambda: frozenset({DataKind.OHLCV}))
    cache_dir: Path = field(default_factory=lambda: Path("data/cache/binance_vision"))
    verify_checksum: bool = True
    timeout_s: float = 60.0
    _client: httpx.Client | None = None
    _last_error: str = ""
    _last_success: datetime | None = None
    _files_downloaded: int = 0
    _files_from_cache: int = 0
    _missing: tuple[str, ...] = ()

    # ---- öffentlich -------------------------------------------------------------
    def get_ohlcv(
        self, instrument: str, timeframe: Timeframe, start: datetime, end: datetime
    ) -> list[OHLCV]:
        symbol = instrument.upper()
        interval = _INTERVAL.get(timeframe)
        if interval is None:
            raise BinanceVisionError(f"Timeframe {timeframe} von Binance Vision nicht unterstützt")
        start, end = ensure_utc(start), ensure_utc(end)
        if end <= start:
            raise BinanceVisionError(f"end {end} <= start {start}")

        missing: list[str] = []
        bars: list[OHLCV] = []
        for ref in _file_refs(symbol, interval, start, end):
            try:
                raw = self._get_zip(ref)
            except FileNotFoundError:
                missing.append(ref.name)
                _log.warning("bulk file missing", extra={"file": ref.name, "kind": ref.kind})
                continue
            bars.extend(_zip_to_bars(raw, symbol, timeframe))

        self._missing = tuple(missing)
        window = [b for b in bars if start <= b.open_time < end]
        cleaned, conflicts = deduplicate_ohlcv(window)
        if conflicts:
            _log.warning(
                "duplicate rows with conflicting OHLC discarded",
                extra={"symbol": symbol, "count": len(conflicts)},
            )
        self._last_success = datetime.now(UTC)
        return sort_ohlcv(cleaned)

    def status(self) -> ProviderStatus:
        health = ProviderHealth.HEALTHY if not self._last_error else ProviderHealth.DEGRADED
        return ProviderStatus(
            provider=self.name,
            health=health,
            checked_at=datetime.now(UTC),
            detail=self._last_error
            or f"downloaded={self._files_downloaded} cached={self._files_from_cache} "
            f"missing={len(self._missing)}",
            last_success_at=self._last_success,
        )

    @property
    def missing_files(self) -> tuple[str, ...]:
        return self._missing

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    # ---- intern ---------------------------------------------------------------
    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=self.timeout_s, follow_redirects=True)
        return self._client

    def _get_zip(self, ref: _FileRef) -> bytes:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cached = self.cache_dir / ref.name
        chk_cached = self.cache_dir / f"{ref.name}.CHECKSUM"

        if cached.exists():
            data = cached.read_bytes()
            if self.verify_checksum and chk_cached.exists():
                _verify(data, chk_cached.read_text(), ref.name)
            self._files_from_cache += 1
            return data

        data = self._download(ref.url)
        if self.verify_checksum:
            try:
                chk = self._download(ref.checksum_url).decode()
            except FileNotFoundError:
                chk = ""
            if chk:
                _verify(data, chk, ref.name)
                chk_cached.write_text(chk)
        cached.write_bytes(data)
        self._files_downloaded += 1
        return data

    def _download(self, url: str) -> bytes:
        try:
            resp = self._http().get(url)
        except httpx.HTTPError as exc:  # Netzfehler
            self._last_error = f"{type(exc).__name__}: {exc}"
            raise BinanceVisionError(self._last_error) from exc
        if resp.status_code == 404:
            raise FileNotFoundError(url)
        if resp.status_code != 200:
            self._last_error = f"HTTP {resp.status_code} für {url}"
            raise BinanceVisionError(self._last_error)
        self._last_error = ""
        return resp.content


# --------------------------------------------------------------------------------- Hilfsfunktionen


def _file_refs(symbol: str, interval: str, start: datetime, end: datetime) -> list[_FileRef]:
    """Monats-Dateien für [start, end); der laufende Monat wird durch Tages-Dateien ergänzt."""
    refs: list[_FileRef] = []
    now = datetime.now(UTC)
    current_month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    month = start.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    last = (end - timedelta(microseconds=1)).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )
    while month <= last:
        if month < current_month_start:
            name = f"{symbol}-{interval}-{month:%Y-%m}.zip"
            url = f"{_BASE}/monthly/klines/{symbol}/{interval}/{name}"
            refs.append(_FileRef(url, f"{url}.CHECKSUM", name, "monthly"))
        month = (month + timedelta(days=32)).replace(day=1)

    # laufender Monat: Tages-Dateien
    if end > current_month_start:
        day = max(start, current_month_start).replace(hour=0, minute=0, second=0, microsecond=0)
        end_day = min(end, now).replace(hour=0, minute=0, second=0, microsecond=0)
        while day <= end_day:
            name = f"{symbol}-{interval}-{day:%Y-%m-%d}.zip"
            url = f"{_BASE}/daily/klines/{symbol}/{interval}/{name}"
            refs.append(_FileRef(url, f"{url}.CHECKSUM", name, "daily"))
            day += timedelta(days=1)
    return refs


def _verify(data: bytes, checksum_line: str, name: str) -> None:
    expected = checksum_line.strip().split()[0].lower()
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected:
        raise BinanceVisionError(
            f"Checksumme falsch für {name}: erwartet {expected}, berechnet {actual}"
        )


def _norm_epoch_ms(raw: str) -> int:
    val = float(raw)
    return int(val / 1000.0) if val >= _US_THRESHOLD else int(val)


def _zip_to_bars(raw: bytes, symbol: str, timeframe: Timeframe) -> list[OHLCV]:
    out: list[OHLCV] = []
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        names = zf.namelist()
        if len(names) != 1:
            raise BinanceVisionError(f"ZIP für {symbol} enthält {len(names)} Dateien, erwartet 1")
        with zf.open(names[0]) as fh:
            text = io.TextIOWrapper(fh, encoding="utf-8")
            reader = csv.reader(text)
            first = True
            for row in reader:
                if not row:
                    continue
                # manche neueren Dateien haben eine Header-Zeile
                if first and not row[0].lstrip("-").isdigit():
                    first = False
                    continue
                first = False
                out.append(_row_to_bar(row, symbol, timeframe))
    return out


def _row_to_bar(row: list[str], symbol: str, timeframe: Timeframe) -> OHLCV:
    open_ms = _norm_epoch_ms(row[0])
    open_time = datetime.fromtimestamp(open_ms / 1000.0, tz=UTC)
    if not is_aligned(open_time, timeframe):
        raise BinanceVisionError(
            f"{symbol}: open_time {open_time.isoformat()} nicht an {timeframe} ausgerichtet"
        )
    quote_volume = float(row[7]) if len(row) > 7 and row[7] != "" else None
    trades = int(float(row[8])) if len(row) > 8 and row[8] != "" else None
    return OHLCV(
        instrument=symbol,
        timeframe=timeframe,
        open_time=open_time,
        close_time=bar_close_time(open_time, timeframe),
        open=float(row[1]),
        high=float(row[2]),
        low=float(row[3]),
        close=float(row[4]),
        volume=float(row[5]),
        quote_volume=quote_volume,
        trades=trades,
        source="binance_vision",
    )


__all__ = ["BinanceVisionError", "BinanceVisionProvider"]
