"""scripts/seed_economic_calendar — reproduzierbare FOMC/NFP-Termine (kein Feed)."""

from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime
from pathlib import Path

_PATH = Path(__file__).resolve().parents[2] / "scripts" / "seed_economic_calendar.py"
_spec = importlib.util.spec_from_file_location("seed_economic_calendar", _PATH)
assert _spec and _spec.loader
sec = importlib.util.module_from_spec(_spec)
sys.modules["seed_economic_calendar"] = sec
_spec.loader.exec_module(sec)


def test_first_friday_rule() -> None:
    # 2023: 6. Jan, 3. Feb, 3. März, 7. Apr …
    assert sec._first_friday(2023, 1).day == 6
    assert sec._first_friday(2023, 2).day == 3
    assert sec._first_friday(2023, 4).day == 7
    # immer ein Freitag
    for m in range(1, 13):
        assert sec._first_friday(2025, m).weekday() == 4


def test_nfp_dates_are_monthly_and_0830_et() -> None:
    ds = sec._nfp_dates(2024, 2024)
    assert len(ds) == 12
    assert all(d.weekday() == 4 and d.hour == 12 and d.minute == 30 for d in ds)


def test_fomc_dates_hardcoded_and_sane() -> None:
    for iso in sec._FOMC:
        d = datetime.fromisoformat(iso).replace(tzinfo=UTC)
        assert 2023 <= d.year <= 2026
    # bekannte Sitzung: 26. Juli 2023
    assert "2023-07-26" in sec._FOMC
    # available_time liegt vor scheduled_time
    assert sec._LEAD.days > 0
