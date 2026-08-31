"""Gold/FX-Datenquellen-Adapter — Vertrag, PIT, kein Fake.

* ``yahoo_finance``  — keyless OHLCV, indikativ; noch formende Bar wird verworfen.
* ``ctrader``        — Vertrag: ohne Credentials UNAVAILABLE + ``CTraderUnavailable``, nie Fake.
* ``dukascopy``      — echter ``.bi5``-Decoder (LZMA + 20-Byte-Records) + Tick→OHLCV + Spread.
"""

from __future__ import annotations

import lzma
import struct
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx

from trading_agent.core.enums import ProviderHealth, Timeframe
from trading_agent.data.providers.dukascopy import (
    DukascopyProvider,
    decode_bi5,
    point_factor,
    ticks_to_ohlcv,
)
from trading_agent.data.providers.yahoo_finance import YahooFinanceProvider

_REC = struct.Struct(">IIIff")


# --------------------------------------------------------------------------- yahoo


def _yahoo_payload(base_epoch: int, closes: list[float]) -> dict:
    n = len(closes)
    return {
        "chart": {
            "error": None,
            "result": [
                {
                    "meta": {
                        "regularMarketPrice": closes[-1],
                        "regularMarketTime": base_epoch + n * 300,
                    },
                    "timestamp": [base_epoch + i * 300 for i in range(n)],
                    "indicators": {
                        "quote": [
                            {
                                "open": [c - 0.001 for c in closes],
                                "high": [c + 0.002 for c in closes],
                                "low": [c - 0.002 for c in closes],
                                "close": list(closes),
                                "volume": [100.0] * n,
                            }
                        ]
                    },
                }
            ],
        }
    }


@respx.mock
async def test_yahoo_ohlcv_drops_forming_bar() -> None:
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    # 4 Bars: die letzte ist noch nicht geschlossen (open_time = now)
    base = int((now - timedelta(minutes=15)).timestamp())
    base -= base % 300
    payload = _yahoo_payload(base, [1.10, 1.101, 1.102, 1.103])
    respx.get(url__regex=r".*/v8/finance/chart/EURUSD=X").mock(
        return_value=httpx.Response(200, json=payload)
    )
    p = YahooFinanceProvider()
    bars = await p.fetch_ohlcv(
        "EURUSD", Timeframe.M5, now - timedelta(hours=2), now + timedelta(hours=1)
    )
    await p.aclose()
    assert bars, "mindestens die abgeschlossenen Bars"
    assert all(b.close_time <= datetime.now(UTC) for b in bars)
    assert all(b.source == "yahoo_indicative" and b.quote_volume is None for b in bars)


@respx.mock
async def test_yahoo_latest_indicative() -> None:
    base = int(datetime(2024, 6, 4, 10, tzinfo=UTC).timestamp())
    respx.get(url__regex=r".*/v8/finance/chart/GC=F").mock(
        return_value=httpx.Response(200, json=_yahoo_payload(base, [2340.0, 2341.5]))
    )
    p = YahooFinanceProvider()
    ip = await p.latest_indicative("XAUUSD")
    await p.aclose()
    assert ip.price == 2341.5 and ip.source == "yahoo_indicative"
    assert p.status().health is ProviderHealth.HEALTHY


# ctrader: siehe tests/unit/test_ctrader.py (eigener READ-ONLY-Client + Adapter, OAuth-Flow).


# --------------------------------------------------------------------------- dukascopy


def _bi5(hour: datetime, symbol: str, mids: list[float]) -> bytes:
    factor = point_factor(symbol)
    spread_pts = 20
    recs = []
    for i, mid in enumerate(mids):
        mid_pts = round(mid / factor)
        recs.append(_REC.pack(i * 60_000, mid_pts + spread_pts, mid_pts - spread_pts, 1.0, 1.2))
    return lzma.compress(b"".join(recs), format=lzma.FORMAT_ALONE)


def test_decode_bi5_roundtrip() -> None:
    hour = datetime(2024, 6, 4, 8, tzinfo=UTC)
    raw = _bi5(hour, "EURUSD", [1.08, 1.081, 1.0805])
    ticks = decode_bi5(raw, hour, "EURUSD")
    assert len(ticks) == 3
    assert ticks[0].ts == hour
    assert abs(ticks[0].mid - 1.08) < 1e-6
    assert ticks[0].ask > ticks[0].bid
    assert abs(ticks[0].spread - 0.0004) < 1e-6  # 40 Punkte * 1e-5


def test_decode_bi5_xauusd_scaling() -> None:
    hour = datetime(2024, 6, 4, 8, tzinfo=UTC)
    ticks = decode_bi5(_bi5(hour, "XAUUSD", [2345.6, 2346.0]), hour, "XAUUSD")
    assert abs(ticks[0].mid - 2345.6) < 0.01  # 1e-3-Skalierung


def test_decode_bi5_rejects_corrupt() -> None:
    from trading_agent.data.providers.dukascopy import DukascopyError

    bad = lzma.compress(b"\x00\x01\x02", format=lzma.FORMAT_ALONE)  # 3 Byte, kein Vielfaches von 20
    with pytest.raises(DukascopyError):
        decode_bi5(bad, datetime(2024, 1, 1, tzinfo=UTC), "EURUSD")


def test_ticks_to_ohlcv_aggregates_and_marks_pit() -> None:
    hour = datetime(2024, 6, 4, 8, tzinfo=UTC)
    mids = [1.08 + 0.0001 * i for i in range(15)]  # 15 min-getaktete Ticks
    ticks = decode_bi5(_bi5(hour, "EURUSD", mids), hour, "EURUSD")
    bars, spreads = ticks_to_ohlcv(ticks, "EURUSD", Timeframe.M5)
    assert [b.open_time.minute for b in bars] == [0, 5, 10]
    assert bars[0].close_time == bars[0].open_time + timedelta(minutes=5)
    assert bars[0].open == pytest.approx(mids[0])
    assert bars[-1].close == pytest.approx(mids[-1])
    assert all(s.mean_spread > 0 for s in spreads)
    assert bars[0].source == "dukascopy"


@respx.mock
def test_dukascopy_provider_download_skips_404(tmp_path) -> None:
    hour0 = datetime(2024, 6, 4, 8, tzinfo=UTC)
    raw = _bi5(hour0, "EURUSD", [1.08 + 0.0001 * i for i in range(12)])
    base = "https://datafeed.dukascopy.com/datafeed/EURUSD/2024/05/04"
    respx.get(f"{base}/08h_ticks.bi5").mock(return_value=httpx.Response(200, content=raw))
    respx.get(f"{base}/09h_ticks.bi5").mock(return_value=httpx.Response(404))

    p = DukascopyProvider(cache_dir=tmp_path)
    bars = p.get_ohlcv("EURUSD", Timeframe.M5, hour0, hour0 + timedelta(hours=2))
    p.close()
    assert bars and all(b.instrument == "EURUSD" for b in bars)
    assert len(p.missing_files) == 1 and "09h" in p.missing_files[0]
    assert p.last_spread_stats
    assert p.status().health is ProviderHealth.HEALTHY
