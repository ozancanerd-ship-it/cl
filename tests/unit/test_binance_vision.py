"""Phase 3 · Binance-Vision-Bulk-Provider (``data.providers.binance_vision``).

Offline: synthetische ZIP-Bytes, kein Netz. Geprüft: URL-Generierung (monthly/daily),
Epoch-Normalisierung (ms/µs), CSV→OHLCV (``close_time = open_time + tf``, Binance-close_time
verworfen), SHA-256-Verifikation, Cache, Alignment-Ablehnung, Header-Zeile.
"""

from __future__ import annotations

import hashlib
import io
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from trading_agent.core.enums import Timeframe
from trading_agent.data.providers.binance_vision import (
    BinanceVisionError,
    BinanceVisionProvider,
    _file_refs,
    _norm_epoch_ms,
    _verify,
    _zip_to_bars,
)

M5 = Timeframe.M5


def _csv_rows(rows: list[list[object]]) -> str:
    return "\n".join(",".join(str(c) for c in r) for r in rows) + "\n"


def _zip_bytes(csv_text: str, name: str = "BTCUSDT-5m-2025-01.csv") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(name, csv_text)
    return buf.getvalue()


# eine echte Binance-Zeile: open_time(ms), o,h,l,c, vol, close_time(=open+300000-1), qv, trades, tb, tbq, ignore
_ROW_A = [
    1735689600000,
    "93000.1",
    "93100.5",
    "92950.0",
    "93050.2",
    "12.5",
    1735689899999,
    "1163000.0",
    420,
    "6.1",
    "0",
    "0",
]
_ROW_B = [
    1735689900000,
    "93050.2",
    "93200.0",
    "93000.0",
    "93180.0",
    "9.3",
    1735690199999,
    "865000.0",
    310,
    "4.0",
    "0",
    "0",
]


def test_file_refs_monthly_for_past_range() -> None:
    refs = _file_refs(
        "BTCUSDT", "5m", datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 3, 1, tzinfo=UTC)
    )
    names = [r.name for r in refs]
    assert names == ["BTCUSDT-5m-2024-01.zip", "BTCUSDT-5m-2024-02.zip"]
    assert all(r.kind == "monthly" for r in refs)
    assert refs[0].url == (
        "https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/5m/BTCUSDT-5m-2024-01.zip"
    )
    assert refs[0].checksum_url.endswith(".zip.CHECKSUM")


def test_file_refs_daily_for_current_month() -> None:
    now = datetime.now(UTC)
    refs = _file_refs(
        "ETHUSDT", "5m", now.replace(day=1, hour=0, minute=0, second=0, microsecond=0), now
    )
    assert refs and all(r.kind == "daily" for r in refs)
    assert "/daily/klines/ETHUSDT/5m/" in refs[0].url


def test_norm_epoch_ms_handles_ms_and_us() -> None:
    assert _norm_epoch_ms("1735689600000") == 1735689600000  # ms
    assert _norm_epoch_ms("1735689600000000") == 1735689600000  # µs → ms


def test_zip_to_bars_parses_and_recomputes_close_time() -> None:
    bars = _zip_to_bars(_zip_bytes(_csv_rows([_ROW_A, _ROW_B])), "BTCUSDT", M5)
    assert len(bars) == 2
    b = bars[0]
    assert b.open_time == datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
    assert b.close_time == datetime(2025, 1, 1, 0, 5, tzinfo=UTC)  # nicht Binance's ...899999
    assert (b.open, b.high, b.low, b.close, b.volume) == (93000.1, 93100.5, 92950.0, 93050.2, 12.5)
    assert b.quote_volume == 1163000.0 and b.trades == 420
    assert b.source == "binance_vision"
    assert bars[1].open_time == datetime(2025, 1, 1, 0, 5, tzinfo=UTC)


def test_zip_to_bars_skips_header_row() -> None:
    header = [
        "open_time",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "close_time",
        "quote_volume",
        "count",
        "x",
        "y",
        "z",
    ]
    bars = _zip_to_bars(_zip_bytes(_csv_rows([header, _ROW_A])), "BTCUSDT", M5)
    assert len(bars) == 1


def test_row_alignment_rejected() -> None:
    bad = list(_ROW_A)
    bad[0] = 1735689600000 + 60000  # +1 Minute → nicht an M5 ausgerichtet
    with pytest.raises(BinanceVisionError, match="ausgerichtet"):
        _zip_to_bars(_zip_bytes(_csv_rows([bad])), "BTCUSDT", M5)


def test_verify_checksum() -> None:
    data = b"payload"
    good = f"{hashlib.sha256(data).hexdigest()}  BTCUSDT-5m-2025-01.zip"
    _verify(data, good, "BTCUSDT-5m-2025-01.zip")  # kein Fehler
    with pytest.raises(BinanceVisionError, match="Checksumme falsch"):
        _verify(data, "deadbeef  BTCUSDT-5m-2025-01.zip", "BTCUSDT-5m-2025-01.zip")


def test_get_zip_caches_and_verifies(tmp_path: Path) -> None:
    raw = _zip_bytes(_csv_rows([_ROW_A]))
    chk = f"{hashlib.sha256(raw).hexdigest()}  BTCUSDT-5m-2025-01.zip"
    calls: list[str] = []

    class _P(BinanceVisionProvider):
        def _download(self, url: str) -> bytes:  # type: ignore[override]
            calls.append(url)
            return chk.encode() if url.endswith(".CHECKSUM") else raw

    p = _P(cache_dir=tmp_path / "cache")
    from trading_agent.data.providers.binance_vision import _FileRef

    ref = _FileRef(
        "http://x/BTCUSDT-5m-2025-01.zip",
        "http://x/BTCUSDT-5m-2025-01.zip.CHECKSUM",
        "BTCUSDT-5m-2025-01.zip",
        "monthly",
    )
    a = p._get_zip(ref)
    b = p._get_zip(ref)  # aus dem Cache
    assert a == b == raw
    assert len(calls) == 2  # nur der erste Aufruf lädt (zip + checksum)
    assert (tmp_path / "cache" / "BTCUSDT-5m-2025-01.zip").exists()


def test_get_ohlcv_reports_missing_no_fake(tmp_path: Path) -> None:
    class _P(BinanceVisionProvider):
        def _download(self, url: str) -> bytes:  # type: ignore[override]
            raise FileNotFoundError(url)

    p = _P(cache_dir=tmp_path / "c", verify_checksum=False)
    bars = p.get_ohlcv(
        "BTCUSDT", M5, datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 2, 1, tzinfo=UTC)
    )
    assert bars == []
    assert p.missing_files == ("BTCUSDT-5m-2024-01.zip",)  # gemeldet, nicht erfunden
