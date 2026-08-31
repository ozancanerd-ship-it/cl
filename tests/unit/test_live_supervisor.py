"""Tests: M-01 ``LiveSupervisor`` + ``LivePipeline``-Recovery/Resample/Derivatives.

Alles offline: Fake-REST (Mock-OHLCV + optional Funding/OI) + Fake-WS (feste Bar-Folge).
Kein Netz. Prüft: 24/7-Loop (bounded), WS-Restart, Recovery aus Snapshot ohne Doppel-Events,
sauberer Shutdown mit Snapshot, ``orders_sent == 0``, rollierendes M15-Resample, Derivatives
nur bei validen Daten, Health-Report.
"""

from __future__ import annotations

import asyncio
import itertools
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest

from trading_agent.core.clock import FixedClock
from trading_agent.core.enums import AssetClass, Timeframe
from trading_agent.core.models import OHLCV, Funding, OpenInterest, Quote
from trading_agent.core.time import align_down
from trading_agent.data.providers.mock_provider import MockMarketDataProvider
from trading_agent.runtime.events import DecisionMade, ShutdownRequested
from trading_agent.runtime.live_pipeline import LivePipeline, LivePipelineConfig
from trading_agent.runtime.supervisor import LiveSupervisor
from trading_agent.state.store import SnapshotStore

M5 = Timeframe.M5


class FakeRest:
    def __init__(self, *, with_derivs: bool = False, valid_derivs: bool = True) -> None:
        self._mp = MockMarketDataProvider(clock=FixedClock(datetime(2025, 1, 1, tzinfo=UTC)))
        self.calls: list[str] = []
        self._with_derivs = with_derivs
        self._valid_derivs = valid_derivs

    # Warmup/Backfill enden bewusst hier — die Fake-WS-Bars (_bars) knüpfen contiguous an.
    warmup_end = align_down(datetime.now(UTC) - timedelta(hours=2, minutes=35), Timeframe.M5)

    async def fetch_ohlcv(
        self, instrument: str, timeframe: Timeframe, start: datetime, end: datetime
    ) -> list[OHLCV]:
        self.calls.append(f"ohlcv:{timeframe.value}")
        capped = min(end, self.warmup_end)
        return list(self._mp.get_ohlcv(instrument.upper(), timeframe, start, capped))

    async def fetch_quote(self, instrument: str) -> Quote:
        self.calls.append("quote")
        return Quote(
            instrument=instrument.upper(),
            ts=datetime.now(UTC),
            bid=100.0,
            ask=100.05,
            source="fake",
        )

    async def fetch_funding(self, instrument: str, start: datetime, end: datetime) -> list[Funding]:
        self.calls.append("funding")
        if not self._with_derivs or not self._valid_derivs:
            return []
        return [
            Funding(
                instrument=instrument.upper(),
                ts=datetime.now(UTC) - timedelta(hours=4),
                rate=0.0001,
                interval_hours=8.0,
                source="fake",
            )
        ]

    async def fetch_open_interest(
        self, instrument: str, start: datetime, end: datetime
    ) -> list[OpenInterest]:
        self.calls.append("oi")
        if not self._with_derivs or not self._valid_derivs:
            return []
        base = datetime.now(UTC) - timedelta(hours=6)
        return [
            OpenInterest(instrument=instrument.upper(), ts=base, oi=1000.0, source="fake"),
            OpenInterest(
                instrument=instrument.upper(),
                ts=base + timedelta(hours=1),
                oi=1050.0,
                source="fake",
            ),
        ]

    async def aclose(self) -> None:
        return None


class FakeWS:
    """Yield-t eine feste Bar-Folge und endet dann (simuliert einen WS-Abriss)."""

    def __init__(self, bars: list[OHLCV]) -> None:
        self._bars = bars
        self._stopped = False
        self.reconnects = 0
        self.messages_seen = 0

    async def stream(self) -> AsyncIterator[OHLCV]:
        for b in self._bars:
            if self._stopped:
                return
            self.messages_seen += 1
            await asyncio.sleep(0)
            yield b

    async def stop(self) -> None:
        self._stopped = True


def _bars(n: int, *, instrument: str = "BTCUSDT", start_price: float = 100.0) -> list[OHLCV]:
    """Confirmed M5-Bars, die **contiguous** an das Warmup-Ende von ``FakeRest`` anknüpfen."""
    t0 = FakeRest.warmup_end
    out: list[OHLCV] = []
    p = start_price
    for i in range(n):
        ot = t0 + timedelta(minutes=5 * i)
        out.append(
            OHLCV(
                instrument=instrument,
                timeframe=M5,
                open_time=ot,
                close_time=ot + timedelta(minutes=5),
                open=p,
                high=p + 1,
                low=p - 1,
                close=p + 0.2,
                volume=10.0,
                source="fake_ws",
            )
        )
        p += 0.2
    return out


def _cfg(**kw: object) -> LivePipelineConfig:
    base: dict[str, object] = dict(
        exchange="bybit",
        instruments=("BTCUSDT",),
        asset_class=AssetClass.CRYPTO,
        m5_warmup=320,
        m5_window=300,
        m5_store_bars=1400,
        higher_warmup={Timeframe.M15: 200, Timeframe.H4: 120, Timeframe.D1: 120},
        rest_poll_seconds=0.3,
        stale_after_seconds=1_000_000,  # im Test steuern wir „WS-Stall" über die Fake-WS-Länge
    )
    base.update(kw)
    return LivePipelineConfig(**base)  # type: ignore[arg-type]


def _pipe(rest: FakeRest, ws_bars: list[OHLCV], **cfgkw: object) -> LivePipeline:
    fw = FakeWS(ws_bars)
    return LivePipeline(_cfg(**cfgkw), rest_provider=rest, ws_factory=lambda: fw)  # type: ignore[arg-type,return-value]


async def test_supervisor_runs_bounded_and_shuts_down_clean(tmp_path) -> None:
    rest = FakeRest()
    pipe = _pipe(rest, _bars(3))
    decisions: list[DecisionMade] = []
    shutdowns: list[ShutdownRequested] = []
    pipe.bus.subscribe(DecisionMade, lambda e: decisions.append(e))
    pipe.bus.subscribe(ShutdownRequested, lambda e: shutdowns.append(e))

    sup = LiveSupervisor(
        pipe,
        snapshot_store=SnapshotStore(tmp_path / "state"),
        heartbeat_interval_s=0.2,
        watchdog_interval_s=0.2,
        snapshot_interval_s=0.3,
        install_signal_handlers=False,
    )
    await sup.run(max_seconds=1.5)

    assert sup.started and sup.stopped
    assert sup.orders_sent == 0
    assert shutdowns  # ShutdownRequested published
    assert any(d.decision_type in {"no_trade", "wait", "buy", "sell"} for d in decisions)
    # der Fake-WS endet nach 3 Bars ⇒ der Supervisor startet ihn neu
    assert pipe.ws_restarts >= 1
    st = sup.status()
    assert st["orders_sent"] == 0 and st["snapshots_written"] >= 1
    # Snapshot liegt auf der Platte
    assert SnapshotStore(tmp_path / "state").load("live_supervisor") is not None


async def test_recovery_from_snapshot_no_duplicate_events(tmp_path) -> None:
    store = SnapshotStore(tmp_path / "state")
    # 1. Lauf
    rest = FakeRest()
    pipe1 = _pipe(rest, _bars(4))
    sup1 = LiveSupervisor(
        pipe1,
        snapshot_store=store,
        heartbeat_interval_s=0.2,
        watchdog_interval_s=0.3,
        snapshot_interval_s=0.3,
        install_signal_handlers=False,
    )
    await sup1.run(max_seconds=1.2)
    last_open_1 = pipe1._last_open["BTCUSDT"]
    decisions_1 = pipe1._counts["BTCUSDT"]["decisions"]
    assert last_open_1 is not None and decisions_1 >= 1

    # 2. Lauf — dieselben WS-Bars nochmal anbieten; keine darf erneut verarbeitet werden
    rest2 = FakeRest()
    pipe2 = _pipe(rest2, _bars(4))
    dec2: list[DecisionMade] = []
    pipe2.bus.subscribe(DecisionMade, lambda e: dec2.append(e))
    sup2 = LiveSupervisor(
        pipe2,
        snapshot_store=store,
        heartbeat_interval_s=0.2,
        watchdog_interval_s=0.3,
        snapshot_interval_s=0.3,
        install_signal_handlers=False,
    )
    await sup2.run(max_seconds=1.2)

    assert pipe2._recovered is True
    # die aus dem Snapshot bekannten open_times sind geseedet ⇒ keine Doppel-Verarbeitung
    assert last_open_1 in pipe2._fed_opens["BTCUSDT"]
    ws_decisions = [d for d in dec2 if d.instrument == "BTCUSDT"]
    # nur der prime-Durchlauf + evtl. echt neue Bars — die 4 alten WS-Bars nicht nochmal
    assert len(ws_decisions) <= 2


class _GapRest(FakeRest):
    """REST, dessen Warmup/Backfill bis kurz vor jetzt reicht — erzeugt eine echte Lücke
    zwischen dem Snapshot-Stand und der Gegenwart, die der Supervisor backfillen muss."""

    warmup_end = align_down(datetime.now(UTC) - timedelta(minutes=20), Timeframe.M5)


async def test_recovery_backfills_gap_without_duplicate_cutoff(tmp_path) -> None:
    store = SnapshotStore(tmp_path / "state")
    # 1. Lauf — endet mit last_open ~2h35m alt
    r1 = FakeRest()
    p1 = _pipe(r1, _bars(3))
    s1 = LiveSupervisor(p1, snapshot_store=store, install_signal_handlers=False)
    await s1.run(max_seconds=1.0)
    snap_last_open = p1._last_open["BTCUSDT"]
    assert snap_last_open is not None

    # 2. Lauf — _GapRest liefert Bars bis vor 20 min ⇒ Backfill muss die Lücke schließen
    r2 = _GapRest()
    p2 = _pipe(r2, [])  # keine WS-Bars in diesem Lauf — nur Recovery + Backfill + prime
    decs: list[DecisionMade] = []
    p2.bus.subscribe(DecisionMade, lambda e: decs.append(e))
    s2 = LiveSupervisor(p2, snapshot_store=store, install_signal_handlers=False)
    await s2.run(max_seconds=1.5)

    assert p2._recovered is True
    btc = [d for d in decs if d.instrument == "BTCUSDT"]
    cutoffs = [d.ts for d in btc]
    assert cutoffs, "der Backfill/prime muss mindestens einen Schritt erzeugt haben"
    assert len(cutoffs) == len(set(cutoffs))  # kein cutoff doppelt
    assert min(cutoffs) > snap_last_open  # der Snapshot-Bar-cutoff wurde nicht erneut gefeed-t
    assert p2.orders_sent == 0


async def test_rolling_m15_resample_avoids_rest(tmp_path) -> None:
    rest = FakeRest()
    pipe = _pipe(rest, _bars(6), resample_m15_from_m5=True)
    sup = LiveSupervisor(
        pipe,
        snapshot_store=SnapshotStore(tmp_path / "state"),
        watchdog_interval_s=1.0,
        snapshot_interval_s=1.0,
        install_signal_handlers=False,
    )
    await sup.run(max_seconds=1.2)

    # nach dem Warmup (force_rest) darf M15 NICHT mehr per REST geholt werden
    post_warmup_m15 = [c for c in rest.calls if c == "ohlcv:M15"]
    # genau ein M15-REST-Call pro Instrument im Warmup, danach nur noch Resample/Merge
    assert len(post_warmup_m15) <= len(pipe.cfg.instruments)
    # M15 bleibt tief (die per REST geholte Historie wird NICHT durch flaches ~12-Bar-Resample
    # ersetzt) und ist streng aufsteigend + lückenlos verschmolzen
    m15 = pipe._higher["BTCUSDT"].get(Timeframe.M15, ())
    assert len(m15) >= 120
    pairs = list(itertools.pairwise(m15))
    assert all(a.open_time < b.open_time for a, b in pairs)
    steps = {(b.open_time - a.open_time).total_seconds() for a, b in pairs}
    assert steps == {900.0}  # exakt 15-min-Schritte, keine Lücke an der Merge-Grenze


async def test_derivatives_only_when_valid(tmp_path) -> None:
    # valide Funding/OI ⇒ DerivativesContext gefüllt
    rest_ok = FakeRest(with_derivs=True, valid_derivs=True)
    pipe_ok = _pipe(rest_ok, _bars(3), derivatives=True, derivatives_refresh_every=1)
    sup_ok = LiveSupervisor(
        pipe_ok,
        snapshot_store=SnapshotStore(tmp_path / "a"),
        install_signal_handlers=False,
    )
    await sup_ok.run(max_seconds=1.2)
    d = pipe_ok._derivatives["BTCUSDT"]
    assert d.funding_rate == pytest.approx(0.0001)
    assert d.open_interest == pytest.approx(1050.0)
    assert d.open_interest_delta_pct == pytest.approx(5.0)

    # leere REST-Antwort ⇒ Kontext bleibt leer (kein Fake)
    rest_empty = FakeRest(with_derivs=True, valid_derivs=False)
    pipe_e = _pipe(rest_empty, _bars(3), derivatives=True, derivatives_refresh_every=1)
    sup_e = LiveSupervisor(
        pipe_e, snapshot_store=SnapshotStore(tmp_path / "b"), install_signal_handlers=False
    )
    await sup_e.run(max_seconds=1.2)
    assert pipe_e._derivatives["BTCUSDT"].funding_rate is None


async def test_request_stop_is_graceful(tmp_path) -> None:
    rest = FakeRest()
    pipe = _pipe(rest, _bars(2))
    sup = LiveSupervisor(
        pipe, snapshot_store=SnapshotStore(tmp_path / "state"), install_signal_handlers=False
    )

    async def _stopper() -> None:
        await asyncio.sleep(0.6)
        sup.request_stop("test")

    await asyncio.gather(sup.run(max_seconds=10), _stopper())
    assert sup.stopped and sup.stop_reason == "test"
    assert sup.orders_sent == 0
