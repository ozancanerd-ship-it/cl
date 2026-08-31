"""Dukascopy Bulk Tick Feed — **echte historische Tick-Daten** für FX + XAUUSD, keyless.

``https://datafeed.dukascopy.com/datafeed/{SYMBOL}/{YYYY}/{MM0}/{DD}/{HH}h_ticks.bi5``

* **kein API-Key**, öffentlich. ``MM0`` ist **0-indexiert** (Januar = ``00``).
* Eine Datei je **Stunde**. Fehlt eine Stunde (Wochenende / Feiertag / Ausfall) → HTTP 404,
  wird **übersprungen und protokolliert** — nichts wird synthetisiert.
* Format ``.bi5``: **LZMA-komprimiert**; entpackt eine Folge von **20-Byte**-Records,
  big-endian ``>IIIff``:

  ``(ms_seit_stundenbeginn, ask_points, bid_points, ask_volume, bid_volume)``

  ``ask_points`` / ``bid_points`` sind Ganzzahlen — Preis = ``points * point_factor`` (5-stellige
  FX: ``1e-5``; JPY-Paare: ``1e-3``; XAUUSD: ``1e-3``).

**Zeitzone.** Alle Zeitstempel sind **UTC**.

**PIT / Look-ahead.** Ticks tragen millisekundengenaue Zeitstempel; aggregierte Bars bekommen
``close_time = open_time + timeframe`` (Projekt-Konvention) und sind erst ab ``close_time``
bekannt. Der Aggregator schließt die noch laufende Bar am Fensterende **nicht** mit ein.

**Bid/Ask.** Aggregierte ``OHLCV`` nutzen den **Mid** ``(bid+ask)/2``; der mittlere Spread je
Bar wird über ``last_spread_stats`` bereitgestellt (nicht Teil des ``OHLCV``-Modells).

Aus Umgebungen mit „Bot"-Reputation liefert Dukascopy **HTTP 429** — der Adapter meldet das
als ``DEGRADED`` / wirft ``DukascopyError``; aus einer sauberen Cloud-IP funktioniert der
Abruf normal.
"""

from __future__ import annotations

import lzma
import struct
import time as _time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from trading_agent.core.enums import DataKind, ProviderHealth, Timeframe
from trading_agent.core.models import OHLCV
from trading_agent.core.time import bar_close_time, ensure_utc, is_aligned
from trading_agent.data.interfaces import HistoricalOHLCVProvider, ProviderStatus
from trading_agent.data.quality import sort_ohlcv
from trading_agent.utils.logging import get_logger

_log = get_logger("dukascopy")

_BASE = "https://datafeed.dukascopy.com/datafeed"
_REC = struct.Struct(">IIIff")  # 20 Byte je Tick

# Preis-Skalierung: raw points → Preis
_POINT_FACTOR: dict[str, float] = {
    "XAUUSD": 1e-3,
    "XAGUSD": 1e-3,
    "USDJPY": 1e-3,
    "EURJPY": 1e-3,
    "GBPJPY": 1e-3,
}
_DEFAULT_POINT_FACTOR = 1e-5  # 5-stellige Majors (EURUSD, GBPUSD, ...)

_INTRADAY: dict[Timeframe, int] = {
    Timeframe.M1: 60,
    Timeframe.M5: 300,
    Timeframe.M15: 900,
    Timeframe.M30: 1800,
    Timeframe.H1: 3600,
    Timeframe.H4: 14400,
}


class DukascopyError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Tick:
    ts: datetime
    bid: float
    ask: float
    bid_volume: float
    ask_volume: float

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0

    @property
    def spread(self) -> float:
        return self.ask - self.bid


@dataclass(frozen=True, slots=True)
class BarSpread:
    open_time: datetime
    mean_spread: float
    max_spread: float
    ticks: int


def point_factor(symbol: str) -> float:
    return _POINT_FACTOR.get(symbol.upper(), _DEFAULT_POINT_FACTOR)


def decode_bi5(raw: bytes, hour_start: datetime, symbol: str) -> list[Tick]:
    """Entpackt eine ``.bi5``-Stunde zu Ticks. ``raw`` = **komprimierte** Bytes."""
    if not raw:
        return []
    try:
        data = _lzma_decompress(raw)
    except lzma.LZMAError as exc:
        raise DukascopyError(f"bi5 LZMA-Fehler: {exc}") from exc
    if len(data) % _REC.size != 0:
        raise DukascopyError(f"bi5 Länge {len(data)} nicht durch {_REC.size} teilbar (korrupt?)")
    factor = point_factor(symbol)
    hour_start = ensure_utc(hour_start)
    out: list[Tick] = []
    for ms, ask_pts, bid_pts, ask_vol, bid_vol in _REC.iter_unpack(data):
        bid = bid_pts * factor
        ask = ask_pts * factor
        if bid <= 0 or ask <= 0:
            continue
        out.append(
            Tick(
                ts=hour_start + timedelta(milliseconds=ms),
                bid=bid,
                ask=ask,
                bid_volume=float(bid_vol),
                ask_volume=float(ask_vol),
            )
        )
    out.sort(key=lambda t: t.ts)
    return out


def _lzma_decompress(raw: bytes) -> bytes:
    for fmt in (lzma.FORMAT_AUTO, lzma.FORMAT_ALONE):
        try:
            return lzma.LZMADecompressor(format=fmt).decompress(raw)
        except lzma.LZMAError:
            continue
    raise lzma.LZMAError("kein passendes LZMA-Format")


def ticks_to_ohlcv(
    ticks: list[Tick], instrument: str, timeframe: Timeframe
) -> tuple[list[OHLCV], list[BarSpread]]:
    """Aggregiert Ticks (Mid-Preis) zu ``OHLCV`` + Spread-Statistik je Bar."""
    step = _INTRADAY.get(timeframe)
    if step is None:
        raise DukascopyError(f"dukascopy: Timeframe {timeframe} nicht unterstützt")
    buckets: dict[int, list[Tick]] = {}
    for t in ticks:
        epoch = int(t.ts.timestamp())
        buckets.setdefault(epoch - (epoch % step), []).append(t)

    bars: list[OHLCV] = []
    spreads: list[BarSpread] = []
    for start_epoch in sorted(buckets):
        group = buckets[start_epoch]
        open_time = datetime.fromtimestamp(start_epoch, tz=UTC)
        if not is_aligned(open_time, timeframe):
            continue
        mids = [g.mid for g in group]
        vols = sum(g.bid_volume + g.ask_volume for g in group)
        sprd = [g.spread for g in group]
        bars.append(
            OHLCV(
                instrument=instrument.upper(),
                timeframe=timeframe,
                open_time=open_time,
                close_time=bar_close_time(open_time, timeframe),
                open=mids[0],
                high=max(mids),
                low=min(mids),
                close=mids[-1],
                volume=max(0.0, vols),
                quote_volume=None,
                trades=len(group),
                source="dukascopy",
            )
        )
        spreads.append(
            BarSpread(
                open_time=open_time,
                mean_spread=sum(sprd) / len(sprd),
                max_spread=max(sprd),
                ticks=len(group),
            )
        )
    return sort_ohlcv(bars), spreads


@dataclass(slots=True)
class DukascopyProvider(HistoricalOHLCVProvider):
    """Lädt stündliche ``.bi5``-Dateien, dekodiert Ticks, aggregiert zu ``OHLCV``.
    Heruntergeladene Dateien werden lokal gecacht (Re-Runs ohne Netz)."""

    name: str = "dukascopy"
    provides: frozenset[DataKind] = field(default_factory=lambda: frozenset({DataKind.OHLCV}))
    cache_dir: Path = field(default_factory=lambda: Path("data/cache/dukascopy"))
    timeout_s: float = 60.0
    max_retries: int = 3
    retry_backoff_s: float = 1.0
    request_delay_s: float = 0.0  # Pause vor jedem NICHT gecachten Request (Rate-Limit-Schonung)
    _client: httpx.Client | None = None
    _last_error: str = ""
    _last_success: datetime | None = None
    _missing: tuple[str, ...] = ()
    _last_spreads: tuple[BarSpread, ...] = ()

    # ---- öffentlich -----------------------------------------------------------
    def get_ohlcv(
        self, instrument: str, timeframe: Timeframe, start: datetime, end: datetime
    ) -> list[OHLCV]:
        start, end = ensure_utc(start), ensure_utc(end)
        if end <= start:
            raise DukascopyError(f"end {end} <= start {start}")
        if timeframe not in _INTRADAY:
            raise DukascopyError(f"dukascopy: Timeframe {timeframe} nicht unterstützt")

        ticks: list[Tick] = []
        missing: list[str] = []
        hour = start.replace(minute=0, second=0, microsecond=0)
        now = datetime.now(UTC)
        while hour < end:
            if hour < now:
                try:
                    raw = self._get_hour(instrument, hour)
                except FileNotFoundError:
                    missing.append(_hour_path(instrument, hour))
                    _log.info("dukascopy: Stunde fehlt (404)", extra={"hour": hour.isoformat()})
                except DukascopyError:
                    if "429" in self._last_error:
                        raise  # IP-Block — sofort abbrechen, nicht weiterlaufen
                    missing.append(_hour_path(instrument, hour))
                    _log.warning(
                        "dukascopy: Stunde nach Retries nicht ladbar — übersprungen",
                        extra={"hour": hour.isoformat(), "err": self._last_error},
                    )
                else:
                    ticks.extend(decode_bi5(raw, hour, instrument.upper()))
            hour += timedelta(hours=1)

        self._missing = tuple(missing)
        bars, spreads = ticks_to_ohlcv(ticks, instrument, timeframe)
        window = [b for b in bars if start <= b.open_time < end and b.close_time <= now]
        keep_opens = {b.open_time for b in window}
        self._last_spreads = tuple(s for s in spreads if s.open_time in keep_opens)
        self._last_success = datetime.now(UTC)
        return window

    @property
    def last_spread_stats(self) -> tuple[BarSpread, ...]:
        return self._last_spreads

    @property
    def missing_files(self) -> tuple[str, ...]:
        return self._missing

    def status(self) -> ProviderStatus:
        health = ProviderHealth.HEALTHY if not self._last_error else ProviderHealth.DEGRADED
        return ProviderStatus(
            provider=self.name,
            health=health,
            checked_at=datetime.now(UTC),
            detail=self._last_error or f"missing={len(self._missing)}",
            last_success_at=self._last_success,
        )

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    # ---- intern -------------------------------------------------------------
    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                timeout=self.timeout_s,
                follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0 (compatible; trading-agent/0.1; research)"},
            )
        return self._client

    def _get_hour(self, instrument: str, hour: datetime) -> bytes:
        rel = _hour_path(instrument, hour)
        cached = self.cache_dir / rel
        if cached.exists():
            return cached.read_bytes()
        url = f"{_BASE}/{rel}"
        if self.request_delay_s > 0:
            _time.sleep(self.request_delay_s)
        last: str = ""
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self._http().get(url)
            except httpx.HTTPError as exc:
                last = f"{type(exc).__name__}: {exc}"
            else:
                if resp.status_code == 404:
                    raise FileNotFoundError(url)  # keine Daten für diese Stunde — normal
                if resp.status_code == 429:
                    self._last_error = "HTTP 429 — Dukascopy hat die IP als Bot eingestuft"
                    raise DukascopyError(self._last_error)
                if resp.status_code == 200:
                    self._last_error = ""
                    cached.parent.mkdir(parents=True, exist_ok=True)
                    cached.write_bytes(resp.content)
                    return resp.content
                last = f"HTTP {resp.status_code} für {url}"
            if attempt < self.max_retries:
                _time.sleep(self.retry_backoff_s * attempt)
        self._last_error = last
        raise DukascopyError(last)


def _hour_path(instrument: str, hour: datetime) -> str:
    sym = instrument.upper()
    return f"{sym}/{hour.year:04d}/{hour.month - 1:02d}/{hour.day:02d}/{hour.hour:02d}h_ticks.bi5"


__all__ = [
    "BarSpread",
    "DukascopyError",
    "DukascopyProvider",
    "Tick",
    "decode_bi5",
    "point_factor",
    "ticks_to_ohlcv",
]
