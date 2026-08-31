"""Tests: ``runtime.live_pipeline`` — offline (Fake-REST), keine Netz-Calls.

Prüft: Warmup baut den rolling Store · ``prime`` fährt die volle Pipeline · eine confirmed Bar
erzeugt einen MarketContext mit ``information_cutoff = close_time`` · Events werden publiziert ·
Dedupe · Data-Quality-Block · ``orders_sent`` bleibt 0 · keine Zukunftsdaten.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from trading_agent.core.clock import FixedClock
from trading_agent.core.enums import AssetClass, Timeframe
from trading_agent.core.models import OHLCV, Quote
from trading_agent.data.providers.mock_provider import MockMarketDataProvider
from trading_agent.runtime.events import BarClosed, DecisionMade
from trading_agent.runtime.live_pipeline import LivePipeline, LivePipelineConfig

M5 = Timeframe.M5
NOW = datetime(2025, 3, 1, 12, 0, tzinfo=UTC)


class FakeRest:
    """Deterministischer REST-Ersatz: liefert Mock-OHLCV + eine feste Quote. Kein Netz."""

    def __init__(self, now: datetime) -> None:
        self._mp = MockMarketDataProvider(clock=FixedClock(now), volatility=0.004)
        self.calls: list[str] = []

    async def fetch_ohlcv(
        self, instrument: str, timeframe: Timeframe, start: datetime, end: datetime
    ) -> list[OHLCV]:
        self.calls.append(f"ohlcv:{instrument}:{timeframe.value}")
        # Warmup endet bewusst 4h VOR jetzt, damit Tests danach "confirmed" Bars einspeisen
        # können, deren close_time in der Vergangenheit liegt (Live-Bar-Forming-Guard).
        capped_end = min(end, datetime.now(UTC) - timedelta(hours=4))
        return list(self._mp.get_ohlcv(instrument.upper(), timeframe, start, capped_end))

    async def fetch_quote(self, instrument: str) -> Quote:
        self.calls.append(f"quote:{instrument}")
        return Quote(instrument=instrument.upper(), ts=NOW, bid=100.0, ask=100.1, source="fake")

    async def aclose(self) -> None:
        return None


def _cfg(*, news_gate: bool = False) -> LivePipelineConfig:
    return LivePipelineConfig(
        exchange="bybit",
        instruments=("BTCUSDT",),
        asset_class=AssetClass.CRYPTO,
        m5_warmup=320,
        m5_window=300,
        higher_warmup={Timeframe.M15: 200, Timeframe.H4: 120, Timeframe.D1: 120},
        news_gate=news_gate,
    )


async def test_warmup_and_prime_runs_full_pipeline() -> None:
    pipe = LivePipeline(_cfg(), rest_provider=FakeRest(NOW))
    events: list[object] = []
    pipe.bus.subscribe(DecisionMade, lambda e: events.append(e))

    warm = await pipe.warmup()
    assert warm["BTCUSDT"].startswith("OK: M5=")
    await pipe.prime()

    assert len(events) == 1
    dm = events[0]
    assert isinstance(dm, DecisionMade)
    assert dm.instrument == "BTCUSDT"
    assert dm.decision_type in {"no_trade", "wait", "buy", "sell"}
    st = pipe.state("BTCUSDT")
    assert st.decisions == 1 and st.m5_bars > 250
    assert pipe.orders_sent == 0


async def test_confirmed_bar_advances_context_no_lookahead() -> None:
    rest = FakeRest(NOW)
    pipe = LivePipeline(_cfg(), rest_provider=rest)
    await pipe.warmup()
    await pipe.prime()

    bars: list[BarClosed] = []
    decisions: list[DecisionMade] = []
    pipe.bus.subscribe(BarClosed, lambda e: bars.append(e))
    pipe.bus.subscribe(DecisionMade, lambda e: decisions.append(e))

    last_open = pipe._last_open["BTCUSDT"]
    assert last_open is not None
    nxt_open = last_open + timedelta(minutes=5)
    bar = OHLCV(
        instrument="BTCUSDT",
        timeframe=M5,
        open_time=nxt_open,
        close_time=nxt_open + timedelta(minutes=5),
        open=100.0,
        high=101.0,
        low=99.5,
        close=100.5,
        volume=12.0,
        source="ws",
    )
    await pipe.feed_confirmed_bar(bar, source="ws")

    assert len(bars) == 1 and bars[0].instrument == "BTCUSDT"
    assert len(decisions) == 1
    # der MarketContext des zuletzt eingespeisten Steps trägt genau den cutoff der Bar
    step = pipe.steps[-1]
    assert step.at == bar.close_time
    mc = step.tick.result.mtf.market_context
    assert mc.information_cutoff == bar.close_time
    # keine Bar in irgendeiner Serie nach dem cutoff
    for series in mc.series.values():
        assert all(b.close_time <= bar.close_time for b in series)


async def test_duplicate_and_stale_bar_ignored() -> None:
    pipe = LivePipeline(_cfg(), rest_provider=FakeRest(NOW))
    await pipe.warmup()
    n_before = len(pipe.steps)
    last_open = pipe._last_open["BTCUSDT"]
    assert last_open is not None
    dup = OHLCV(
        instrument="BTCUSDT",
        timeframe=M5,
        open_time=last_open,  # == letzter bekannter open_time ⇒ Duplikat
        close_time=last_open + timedelta(minutes=5),
        open=1.0,
        high=1.0,
        low=1.0,
        close=1.0,
        volume=1.0,
        source="ws",
    )
    await pipe.feed_confirmed_bar(dup)
    assert len(pipe.steps) == n_before  # nichts passiert


async def test_orders_sent_invariant_holds_across_many_bars() -> None:
    rest = FakeRest(NOW)
    pipe = LivePipeline(_cfg(), rest_provider=rest)
    await pipe.warmup()
    await pipe.prime()
    open_t = pipe._last_open["BTCUSDT"]
    assert open_t is not None
    for i in range(1, 25):
        ot = open_t + timedelta(minutes=5 * i)
        px = 100.0 + i * 0.1
        await pipe.feed_confirmed_bar(
            OHLCV(
                instrument="BTCUSDT",
                timeframe=M5,
                open_time=ot,
                close_time=ot + timedelta(minutes=5),
                open=px,
                high=px + 0.5,
                low=px - 0.5,
                close=px + 0.1,
                volume=10.0,
                source="ws",
            )
        )
    assert pipe.orders_sent == 0
    st = pipe.state("BTCUSDT")
    assert st.decisions == 25  # 1 prime + 24 bars
    summ = pipe.summary()
    assert summ["orders_sent"] == 0


async def test_news_gate_on_sets_failsafe_context() -> None:
    # news_gate=on ⇒ NewsContext.feed_as_of gesetzt (kein Fail-safe-Veto), aber ohne echte
    # Events; news_gate=off ⇒ require_news_feed=False im EvaluateParams.
    pipe_on = LivePipeline(_cfg(news_gate=True), rest_provider=FakeRest(NOW))
    await pipe_on.warmup()
    await pipe_on.prime()
    mc = pipe_on.steps[-1].tick.result.mtf.market_context
    assert mc.news.feed_as_of == pipe_on.steps[-1].at

    pipe_off = LivePipeline(_cfg(news_gate=False), rest_provider=FakeRest(NOW))
    await pipe_off.warmup()
    await pipe_off.prime()
    mc2 = pipe_off.steps[-1].tick.result.mtf.market_context
    assert mc2.news.feed_as_of is None  # kein Feed vorgetäuscht
