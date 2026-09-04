"""Daten-Frische-Pruefung — Gegenmassnahme zu Befund F12."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from trading_agent.data.freshness import (
    format_report,
    scan_repository,
    stale_series,
    warn_if_stale,
)

NOW = datetime(2026, 9, 4, tzinfo=UTC)


def _write(repo, instrument: str, timeframe: str, last: datetime, bars: int = 10) -> None:
    d = repo / "ohlcv" / f"instrument={instrument}" / f"timeframe={timeframe}"
    d.mkdir(parents=True, exist_ok=True)
    times = [last - timedelta(hours=4 * i) for i in range(bars)][::-1]
    table = pa.table(
        {
            "open_time": pa.array(times, type=pa.timestamp("us", tz="UTC")),
            "close": pa.array([1.0] * bars, type=pa.float64()),
        }
    )
    pq.write_table(table, d / "data.parquet")


@pytest.fixture
def repo(tmp_path):
    # der reale Fall aus F12: Krypto 430 Tage alt, Yahoo aktuell
    _write(tmp_path, "BTCUSDT", "H4", datetime(2025, 6, 30, tzinfo=UTC))
    _write(tmp_path, "ETHUSDT", "H4", NOW - timedelta(days=34))
    _write(tmp_path, "EURUSD-YF", "H4", NOW - timedelta(days=6))
    _write(tmp_path, "SPX-YF", "H4", NOW - timedelta(days=40))
    return tmp_path


def test_detects_the_f12_case(repo) -> None:
    ages = {a.instrument: a for a in scan_repository(repo, now=NOW)}
    assert ages["BTCUSDT"].stale is True
    assert ages["BTCUSDT"].age_days > 400


def test_binance_vision_publication_lag_is_not_flagged(repo) -> None:
    """34 Tage sind bei Monatsdateien normal — sonst waere die Warnung nutzlos."""
    ages = {a.instrument: a for a in scan_repository(repo, now=NOW)}
    assert ages["ETHUSDT"].stale is False


def test_weekend_gap_on_yahoo_is_not_flagged(repo) -> None:
    ages = {a.instrument: a for a in scan_repository(repo, now=NOW)}
    assert ages["EURUSD-YF"].stale is False


def test_yahoo_tolerance_still_catches_a_real_gap(repo) -> None:
    ages = {a.instrument: a for a in scan_repository(repo, now=NOW)}
    assert ages["SPX-YF"].stale is True


def test_missing_timeframe_counts_as_stale(tmp_path) -> None:
    _write(tmp_path, "BTCUSDT", "D1", NOW)
    ages = scan_repository(tmp_path, timeframe="H4", now=NOW)
    assert [a.instrument for a in ages] == ["BTCUSDT"]
    assert ages[0].stale is True
    assert ages[0].bars == 0


def test_corrupt_parquet_is_reported_not_skipped(tmp_path) -> None:
    d = tmp_path / "ohlcv" / "instrument=BROKEN" / "timeframe=H4"
    d.mkdir(parents=True)
    (d / "data.parquet").write_bytes(b"nicht wirklich parquet")
    ages = scan_repository(tmp_path, now=NOW)
    assert len(ages) == 1
    assert ages[0].stale is True


def test_warn_if_stale_filters_to_the_used_symbols(repo) -> None:
    only_fresh = warn_if_stale(repo, ["ETHUSDT", "EURUSD-YF"], now=NOW)
    assert only_fresh == []
    with_bad = warn_if_stale(repo, ["ETHUSDT", "BTCUSDT"], now=NOW)
    assert [a.instrument for a in with_bad] == ["BTCUSDT"]


def test_stale_series_and_report_render(repo) -> None:
    ages = scan_repository(repo, now=NOW)
    assert len(stale_series(ages)) == 2
    text = format_report(ages)
    assert "BTCUSDT" in text and "VERALTET" in text
    assert "2 von 4" in text


def test_empty_repository_is_not_an_error(tmp_path) -> None:
    assert scan_repository(tmp_path, now=NOW) == []


def test_live_profile_is_stricter_than_research() -> None:
    """Der Fall, der am 2026-09-04 durchrutschte: Krypto 34 Tage alt, Schwelle 45.

    Fuer einen Backtest ueber sieben Jahre ist der letzte Monat egal. Fuer eine taegliche
    Allokationsregel nicht: aus 34 Tagen Bewegung wurde eine Tageskerze von +29 %, die
    realisierte Volatilitaet sprang von ~50 % auf 79.6 %.
    """
    from trading_agent.data.freshness import _tolerance_for

    assert _tolerance_for("BTCUSDT", "research") == 45
    assert _tolerance_for("BTCUSDT", "live") == 3
    assert _tolerance_for("NVDA-YF", "live") < _tolerance_for("NVDA-YF", "research")
    # unbekanntes Profil faellt auf research zurueck, statt zu krachen
    assert _tolerance_for("BTCUSDT", "quatsch") == 45


def test_live_profile_flags_a_month_old_series() -> None:
    from datetime import UTC, datetime, timedelta

    from trading_agent.data.freshness import SeriesAge, _tolerance_for

    last = datetime.now(UTC) - timedelta(days=34)
    fuer_forschung = SeriesAge(
        "BTCUSDT", "D1", 1000, last, last, 34, _tolerance_for("BTCUSDT", "research")
    )
    fuer_live = SeriesAge("BTCUSDT", "D1", 1000, last, last, 34, _tolerance_for("BTCUSDT", "live"))
    assert not fuer_forschung.stale
    assert fuer_live.stale
