"""Tests: FRED/ALFRED PIT-Makro-Adapter — Vintage-Historie, PIT-Filter, kein Fake ohne Key."""

from __future__ import annotations

import contextlib

import httpx
import pytest
import respx

from trading_agent.core.enums import ProviderHealth
from trading_agent.core.time import parse_timestamp
from trading_agent.data.providers.fred_alfred import AdapterUnavailable, FredAlfredProvider

_OBS = {
    "observations": [
        # Jan-Wert: Erstveröffentlichung, dann eine Revision
        {
            "realtime_start": "2024-02-13",
            "realtime_end": "2024-03-11",
            "date": "2024-01-01",
            "value": "308.417",
        },
        {
            "realtime_start": "2024-03-12",
            "realtime_end": "9999-12-31",
            "date": "2024-01-01",
            "value": "308.742",
        },
        # Feb-Wert: nur eine Vintage
        {
            "realtime_start": "2024-03-12",
            "realtime_end": "9999-12-31",
            "date": "2024-02-01",
            "value": "310.326",
        },
        # fehlender Wert wird ignoriert
        {
            "realtime_start": "2024-04-10",
            "realtime_end": "9999-12-31",
            "date": "2024-03-01",
            "value": ".",
        },
    ]
}
_DATES = {
    "release_dates": [
        {"release_id": 10, "date": "2024-01-11"},
        {"release_id": 10, "date": "2024-02-13"},
        {"release_id": 10, "date": "2025-01-15"},  # außerhalb des Fensters
    ]
}

START = parse_timestamp("2024-01-01T00:00:00Z")
END = parse_timestamp("2024-06-01T00:00:00Z")


@pytest.fixture
def _key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRED_API_KEY", "test-key-not-real")


@respx.mock
async def test_alfred_vintages_become_pit_macro_events(_key: None) -> None:
    respx.get("https://api.stlouisfed.org/fred/series/observations").mock(
        return_value=httpx.Response(200, json=_OBS)
    )
    p = FredAlfredProvider()
    events = await p.fetch_macro("US_CPI", START, END)
    await p.aclose()

    # nach available_time sortiert (PIT-Reihenfolge): Jan-Erstwert, dann Feb-Erstwert, dann Jan-Revision
    assert [(e.reference_period.date().isoformat(), e.revision) for e in events] == [
        ("2024-01-01", 0),
        ("2024-02-01", 0),
        ("2024-01-01", 1),
    ]
    jan_first, feb, jan_rev = events
    assert jan_first.value == 308.417
    assert jan_first.available_time == parse_timestamp("2024-02-13T12:30:00Z")
    assert jan_rev.value == 308.742
    assert jan_rev.available_time == parse_timestamp("2024-03-12T12:30:00Z")
    assert feb.value == 310.326
    assert all(e.source == "fred_alfred" for e in events)


@respx.mock
async def test_as_of_hides_later_vintages(_key: None) -> None:
    respx.get("https://api.stlouisfed.org/fred/series/observations").mock(
        return_value=httpx.Response(200, json=_OBS)
    )
    p = FredAlfredProvider()
    events = await p.fetch_macro(
        "US_CPI", START, END, as_of=parse_timestamp("2024-02-20T00:00:00Z")
    )
    await p.aclose()
    # nur der Jan-Erstwert war am 2024-02-20 bekannt
    assert len(events) == 1
    assert events[0].revision == 0 and events[0].value == 308.417


@respx.mock
async def test_release_calendar_conservative_announce_time(_key: None) -> None:
    respx.get("https://api.stlouisfed.org/fred/release/dates").mock(
        return_value=httpx.Response(200, json=_DATES)
    )
    p = FredAlfredProvider(announce_lead_days=365)
    events = await p.release_calendar("US_CPI", START, END)
    await p.aclose()
    assert [e.scheduled_time.date().isoformat() for e in events] == ["2024-01-11", "2024-02-13"]
    assert events[0].actual is None
    # available_time = scheduled - 365d (konservative Näherung)
    assert events[0].available_time == parse_timestamp("2023-01-11T12:30:00Z")


def test_no_key_is_not_available_no_fake() -> None:
    p = FredAlfredProvider()
    assert p.status().health is ProviderHealth.UNAVAILABLE
    with pytest.raises(AdapterUnavailable):
        p._key()


@respx.mock
async def test_net_error_raises_unavailable_not_fake(_key: None) -> None:
    respx.get("https://api.stlouisfed.org/fred/series/observations").mock(
        return_value=httpx.Response(500, json={"error": "boom"})
    )
    p = FredAlfredProvider()
    with pytest.raises(AdapterUnavailable):
        await p.fetch_macro("US_CPI", START, END)
    with contextlib.suppress(Exception):
        await p.aclose()
    assert p.status().health is not ProviderHealth.HEALTHY
