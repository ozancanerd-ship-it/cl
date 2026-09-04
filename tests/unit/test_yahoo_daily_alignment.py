"""Yahoo stempelt Tagesbars auf die Handelseroeffnung, nicht auf Mitternacht.

Ohne Normalisierung lehnt das OHLCV-Modell jede US-Aktien-Tagesreihe ab
(`open_time 2018-09-04T13:30:00+00:00 ist nicht an D1 ausgerichtet`). Die Normalisierung
schneidet auf 00:00 UTC ab; alle beobachteten Stempel liegen im selben UTC-Kalendertag,
es verschiebt sich also kein Bar auf einen anderen Tag.
"""

from __future__ import annotations

from datetime import UTC, datetime

from trading_agent.core.enums import Timeframe
from trading_agent.data.providers.yahoo_finance import YahooFinanceProvider


def _chart(stamps: list[int]) -> dict:
    """Was ``_chart`` zurueckgibt: bereits das ausgepackte ``result[0]``."""
    n = len(stamps)
    return {
        "timestamp": stamps,
        "indicators": {
            "quote": [
                {
                    "open": [100.0] * n,
                    "high": [101.0] * n,
                    "low": [99.0] * n,
                    "close": [100.5] * n,
                    "volume": [1000] * n,
                }
            ]
        },
    }


def _stamp(y: int, m: int, d: int, h: int, mi: int = 0) -> int:
    return int(datetime(y, m, d, h, mi, tzinfo=UTC).timestamp())


def test_market_open_stamps_are_normalised_to_midnight(monkeypatch) -> None:
    # US-Aktien 13:30, VIX 07:00, DXY 04:00, ^TNX 12:20 — alle im selben UTC-Kalendertag
    stamps = [_stamp(2024, 5, 6, 13, 30), _stamp(2024, 5, 7, 7), _stamp(2024, 5, 8, 4)]
    provider = YahooFinanceProvider(symbol_map={"TEST-YFD": "TEST"})

    async def fake_chart(self, symbol, interval, *, range_="5d"):
        return _chart(stamps)

    monkeypatch.setattr(YahooFinanceProvider, "_chart", fake_chart)

    import asyncio

    bars = asyncio.run(
        provider.fetch_ohlcv(
            "TEST-YFD",
            Timeframe.D1,
            datetime(2024, 1, 1, tzinfo=UTC),
            datetime(2024, 12, 31, tzinfo=UTC),
        )
    )
    assert len(bars) == 3
    for b in bars:
        assert b.open_time.hour == 0
        assert b.open_time.minute == 0
    # kein Bar auf einen anderen Kalendertag verschoben
    assert [b.open_time.date().isoformat() for b in bars] == [
        "2024-05-06",
        "2024-05-07",
        "2024-05-08",
    ]


def test_ohlc_values_are_untouched_by_the_normalisation(monkeypatch) -> None:
    provider = YahooFinanceProvider(symbol_map={"TEST-YFD": "TEST"})

    async def fake_chart(self, symbol, interval, *, range_="5d"):
        return _chart([_stamp(2024, 5, 6, 13, 30)])

    monkeypatch.setattr(YahooFinanceProvider, "_chart", fake_chart)

    import asyncio

    bars = asyncio.run(
        provider.fetch_ohlcv(
            "TEST-YFD",
            Timeframe.D1,
            datetime(2024, 1, 1, tzinfo=UTC),
            datetime(2024, 12, 31, tzinfo=UTC),
        )
    )
    assert bars[0].open == 100.0
    assert bars[0].high == 101.0
    assert bars[0].low == 99.0
    assert bars[0].close == 100.5
