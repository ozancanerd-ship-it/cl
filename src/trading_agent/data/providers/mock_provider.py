"""Deterministischer synthetischer Marktdaten-Provider.

Erzeugt reproduzierbare OHLCV-/Quote-/Trade-/Funding-/OI-/Orderbook-Daten aus einem Seed, der
aus (Instrument, Timeframe, Startzeit) abgeleitet wird. Gleiche Eingaben ⇒ bit-identische
Ausgaben. Keine externen Aufrufe, keine Accounts, keine Keys.
"""

from __future__ import annotations

import random
from collections.abc import Iterator
from datetime import datetime, timedelta

from trading_agent.core.clock import Clock, SystemClock
from trading_agent.core.enums import DataKind, ProviderHealth, Side, Timeframe
from trading_agent.core.models import (
    OHLCV,
    Funding,
    OpenInterest,
    OrderbookSnapshot,
    Quote,
    Trade,
)
from trading_agent.core.time import align_up, bar_close_time, ensure_utc, iter_bar_opens
from trading_agent.data.health import HealthTracker
from trading_agent.data.interfaces import (
    FundingProvider,
    HistoricalOHLCVProvider,
    HistoricalQuoteProvider,
    HistoricalTradeProvider,
    LiveOHLCVProvider,
    OpenInterestProvider,
    OrderbookProvider,
    ProviderStatus,
)

_BASE_PRICE: dict[str, float] = {
    "BTCUSDT": 60_000.0,
    "ETHUSDT": 3_000.0,
    "SOLUSDT": 150.0,
    "XAUUSD": 2_400.0,
    "EURUSD": 1.08,
    "AAPL": 190.0,
    "SPY": 530.0,
}
_BASE_VOL: dict[str, float] = {
    "BTCUSDT": 120.0,
    "ETHUSDT": 900.0,
    "SOLUSDT": 5_000.0,
    "XAUUSD": 800.0,
    "EURUSD": 5_000_000.0,
    "AAPL": 40_000.0,
    "SPY": 60_000.0,
}


def _seed(instrument: str, timeframe: Timeframe, start: datetime) -> int:
    raw = f"{instrument.upper()}|{timeframe.value}|{int(start.timestamp())}"
    return int.from_bytes(raw.encode(), "little", signed=False) % (2**32)


class MockMarketDataProvider(
    HistoricalOHLCVProvider,
    LiveOHLCVProvider,
    HistoricalQuoteProvider,
    HistoricalTradeProvider,
    FundingProvider,
    OpenInterestProvider,
    OrderbookProvider,
):
    name = "synthetic"
    provides = frozenset(
        {
            DataKind.OHLCV,
            DataKind.QUOTE,
            DataKind.TRADE,
            DataKind.FUNDING,
            DataKind.OPEN_INTEREST,
            DataKind.ORDERBOOK,
        }
    )

    def __init__(
        self,
        *,
        clock: Clock | None = None,
        volatility: float = 0.004,
        force_health: ProviderHealth | None = None,
    ) -> None:
        self._clock = clock or SystemClock()
        self._vol = volatility
        self._health = HealthTracker(self.name, clock=self._clock)
        self._forced = force_health

    # ------------------------------------------------------------------ status

    def status(self) -> ProviderStatus:
        if self._forced is not None:
            now = self._clock.now()
            return ProviderStatus(
                provider=self.name,
                health=self._forced,
                checked_at=now,
                detail="erzwungen (Test)",
                last_success_at=now if self._forced is ProviderHealth.HEALTHY else None,
            )
        return self._health.status()

    # ------------------------------------------------------------------ ohlcv

    def _generate(
        self, instrument: str, timeframe: Timeframe, start: datetime, end: datetime
    ) -> list[OHLCV]:
        start = align_up(ensure_utc(start), timeframe)
        end = ensure_utc(end)
        opens = iter_bar_opens(start, end, timeframe)
        if not opens:
            return []
        rng = random.Random(_seed(instrument, timeframe, opens[0]))
        base = _BASE_PRICE.get(instrument.upper(), 100.0)
        base_vol = _BASE_VOL.get(instrument.upper(), 1_000.0)
        price = base
        bars: list[OHLCV] = []
        for ot in opens:
            drift = rng.uniform(-self._vol, self._vol)
            open_p = price
            close_p = max(open_p * (1.0 + drift), base * 1e-4)
            hi = max(open_p, close_p) * (1.0 + abs(rng.uniform(0, self._vol)))
            lo = min(open_p, close_p) * (1.0 - abs(rng.uniform(0, self._vol)))
            vol = base_vol * rng.uniform(0.4, 1.8)
            bars.append(
                OHLCV(
                    instrument=instrument.upper(),
                    timeframe=timeframe,
                    open_time=ot,
                    close_time=bar_close_time(ot, timeframe),
                    open=round(open_p, 8),
                    high=round(hi, 8),
                    low=round(lo, 8),
                    close=round(close_p, 8),
                    volume=round(vol, 4),
                    quote_volume=round(vol * (open_p + close_p) / 2, 4),
                    trades=rng.randint(50, 5000),
                    source=self.name,
                    ingested_at=self._clock.now(),
                )
            )
            price = close_p
        return bars

    def get_ohlcv(
        self, instrument: str, timeframe: Timeframe, start: datetime, end: datetime
    ) -> list[OHLCV]:
        try:
            bars = self._generate(instrument, timeframe, start, end)
            self._health.record_success(latency_ms=1.0)
            return bars
        except Exception as exc:
            self._health.record_failure(str(exc))
            raise

    def stream_ohlcv(self, instrument: str, timeframe: Timeframe) -> Iterator[OHLCV]:
        now = self._clock.now()
        start = now - timedelta(seconds=timeframe.seconds * 50)
        yield from self._generate(instrument, timeframe, start, now)

    # ------------------------------------------------------------------ quotes / trades

    def get_quotes(self, instrument: str, start: datetime, end: datetime) -> list[Quote]:
        bars = self._generate(instrument, Timeframe.M1, start, end)
        rng = random.Random(_seed(instrument, Timeframe.M1, ensure_utc(start)) ^ 0xABCD)
        out: list[Quote] = []
        for b in bars:
            spread_bps = rng.uniform(0.5, 3.0)
            half = b.close * spread_bps / 20_000.0
            out.append(
                Quote(
                    instrument=b.instrument,
                    ts=b.open_time,
                    bid=round(b.close - half, 8),
                    ask=round(b.close + half, 8),
                    bid_size=round(rng.uniform(0.1, 5.0), 4),
                    ask_size=round(rng.uniform(0.1, 5.0), 4),
                    source=self.name,
                    ingested_at=self._clock.now(),
                )
            )
        return out

    def get_trades(self, instrument: str, start: datetime, end: datetime) -> list[Trade]:
        bars = self._generate(instrument, Timeframe.M1, start, end)
        rng = random.Random(_seed(instrument, Timeframe.M1, ensure_utc(start)) ^ 0x1234)
        out: list[Trade] = []
        for b in bars:
            n = rng.randint(1, 3)
            for i in range(n):
                out.append(
                    Trade(
                        instrument=b.instrument,
                        ts=b.open_time + timedelta(seconds=i * 5),
                        price=round(rng.uniform(b.low, b.high), 8),
                        size=round(rng.uniform(0.001, 2.0), 6),
                        side=Side.BUY if rng.random() > 0.5 else Side.SELL,
                        trade_id=f"{b.instrument}-{int(b.open_time.timestamp())}-{i}",
                        source=self.name,
                        ingested_at=self._clock.now(),
                    )
                )
        return out

    # ------------------------------------------------------------------ orderbook

    def get_orderbook(self, instrument: str, at: datetime) -> OrderbookSnapshot | None:
        at = ensure_utc(at)
        bars = self._generate(
            instrument, Timeframe.M1, at - timedelta(minutes=1), at + timedelta(minutes=1)
        )
        if not bars:
            return None
        mid = bars[0].close
        rng = random.Random(_seed(instrument, Timeframe.M1, at) ^ 0x9999)
        tick = mid * 1e-4
        bids = [(round(mid - tick * (i + 1), 8), round(rng.uniform(0.1, 4.0), 4)) for i in range(5)]
        asks = [(round(mid + tick * (i + 1), 8), round(rng.uniform(0.1, 4.0), 4)) for i in range(5)]
        return OrderbookSnapshot(
            instrument=instrument.upper(),
            ts=at,
            bids=bids,
            asks=asks,
            source=self.name,
            ingested_at=self._clock.now(),
        )

    # ------------------------------------------------------------------ funding / OI

    def get_funding(self, instrument: str, start: datetime, end: datetime) -> list[Funding]:
        start = ensure_utc(start)
        end = ensure_utc(end)
        # Settlement alle 8h zu 00:00 / 08:00 / 16:00 UTC
        anchor = start.replace(hour=(start.hour // 8) * 8, minute=0, second=0, microsecond=0)
        if anchor < start:
            anchor += timedelta(hours=8)
        rng = random.Random(_seed(instrument, Timeframe.H4, start) ^ 0x5555)
        out: list[Funding] = []
        t = anchor
        while t < end:
            out.append(
                Funding(
                    instrument=instrument.upper(),
                    ts=t,
                    rate=round(rng.uniform(-0.0005, 0.0005), 8),
                    interval_hours=8.0,
                    next_funding_time=t + timedelta(hours=8),
                    source=self.name,
                    ingested_at=self._clock.now(),
                )
            )
            t += timedelta(hours=8)
        return out

    def get_open_interest(
        self, instrument: str, start: datetime, end: datetime
    ) -> list[OpenInterest]:
        bars = self._generate(instrument, Timeframe.H1, start, end)
        rng = random.Random(_seed(instrument, Timeframe.H1, ensure_utc(start)) ^ 0x7777)
        base = _BASE_VOL.get(instrument.upper(), 1_000.0) * 100
        return [
            OpenInterest(
                instrument=b.instrument,
                ts=b.open_time,
                oi=round(base * rng.uniform(0.8, 1.2), 4),
                oi_value=round(base * rng.uniform(0.8, 1.2) * b.close, 2),
                source=self.name,
                ingested_at=self._clock.now(),
            )
            for b in bars
        ]


__all__ = ["MockMarketDataProvider"]
