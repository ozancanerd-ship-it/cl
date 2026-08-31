"""Phase 3 · Replay-Harness (``engine.replay``).

ReplayClock-Determinismus · PIT-Aufbau des MarketContext (`as_of = cutoff`) · Look-ahead-Schutz ·
Dataset-Validierung (eindeutige Fehlmeldung, KEINE Fake-Daten) · Warmup-Fenster.
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime, timedelta

import pytest

from trading_agent.core.clock import FixedClock
from trading_agent.core.enums import Timeframe
from trading_agent.data.providers.mock_provider import MockMarketDataProvider
from trading_agent.data.repository import MarketDataRepository
from trading_agent.engine.replay import (
    AssemblerConfig,
    DatasetIncompleteError,
    DatasetRequirements,
    MarketContextAssembler,
    ReplayClock,
    ReplayHarness,
    validate_dataset,
)

M5 = Timeframe.M5
START = datetime(2024, 6, 1, 0, 0, tzinfo=UTC)
END = datetime(2024, 6, 8, 0, 0, tzinfo=UTC)


def _repo(
    tmp_path, *, instrument: str = "BTCUSD", data_start: datetime | None = None
) -> MarketDataRepository:
    repo = MarketDataRepository(tmp_path / "repo")
    mp = MockMarketDataProvider(clock=FixedClock(END), volatility=0.005)
    ds = data_start or (START - timedelta(days=3))
    repo.write_ohlcv(mp.get_ohlcv(instrument, M5, ds, END))
    return repo


# --------------------------------------------------------------------------- ReplayClock


def test_replayclock_from_range_deterministic() -> None:
    a = list(ReplayClock.from_range(START, START + timedelta(hours=2), M5))
    b = list(ReplayClock.from_range(START, START + timedelta(hours=2), M5))
    assert a == b
    assert a[0] == START + timedelta(minutes=5)  # erster cutoff = erste abgeschlossene Bar
    assert all(t2 > t1 for t1, t2 in itertools.pairwise(a))
    assert len(a) == 24


def test_replayclock_rejects_duplicates() -> None:
    with pytest.raises(ValueError, match="doppelt"):
        ReplayClock([START, START])


def test_replayclock_now_and_exhaustion() -> None:
    clock = ReplayClock.from_range(START, START + timedelta(minutes=15), M5)
    seen = []
    for t in clock:
        seen.append(t)
        assert clock.now() == t
    assert clock.exhausted
    assert seen == list(clock.cutoffs)


# --------------------------------------------------------------------------- Dataset-Validierung


def test_validate_dataset_ok(tmp_path) -> None:
    repo = _repo(tmp_path)
    rep = validate_dataset(
        repo,
        DatasetRequirements(instruments=("BTCUSD",), min_days=5, warmup_bars=100),
        start=START,
        end=END,
    )
    assert rep.ok and rep.missing == ()


def test_validate_dataset_missing_instrument(tmp_path) -> None:
    repo = _repo(tmp_path)
    rep = validate_dataset(
        repo,
        DatasetRequirements(instruments=("ETHUSD",), min_days=5),
        start=START,
        end=END,
    )
    assert not rep.ok
    assert any("keine Abdeckung" in g.reason for g in rep.missing)
    with pytest.raises(DatasetIncompleteError, match="KEINE Daten erfunden"):
        rep.raise_if_incomplete()


def test_validate_dataset_insufficient_warmup(tmp_path) -> None:
    # Daten beginnen erst bei START → 300 Warmup-Bars davor fehlen
    repo = _repo(tmp_path, data_start=START)
    rep = validate_dataset(
        repo,
        DatasetRequirements(instruments=("BTCUSD",), min_days=5, warmup_bars=300),
        start=START,
        end=END,
    )
    assert not rep.ok
    assert any("Warmup" in g.reason for g in rep.missing)


def test_validate_dataset_continuity_note_on_internal_gap(tmp_path) -> None:
    # zwei Bereiche mit einem 6h-Loch dazwischen → interne Lücke, aber kein harter Fehler
    repo = MarketDataRepository(tmp_path / "repo")
    mp = MockMarketDataProvider(clock=FixedClock(END), volatility=0.005)
    gap_start = START + timedelta(days=1)
    gap_end = gap_start + timedelta(hours=6)
    repo.write_ohlcv(mp.get_ohlcv("BTCUSD", M5, START - timedelta(days=1), gap_start))
    repo.write_ohlcv(mp.get_ohlcv("BTCUSD", M5, gap_end, END))
    rep = validate_dataset(
        repo,
        DatasetRequirements(instruments=("BTCUSD",), min_days=5, warmup_bars=100),
        start=START,
        end=END,
    )
    assert rep.ok  # Lücke ist ein Hinweis, kein Abbruch
    assert any("Lücke" in n and "BTCUSD/M5" in n for n in rep.notes)


def test_validate_dataset_continuity_can_be_disabled(tmp_path) -> None:
    repo = MarketDataRepository(tmp_path / "repo")
    mp = MockMarketDataProvider(clock=FixedClock(END), volatility=0.005)
    gap_start = START + timedelta(days=1)
    repo.write_ohlcv(mp.get_ohlcv("BTCUSD", M5, START - timedelta(days=1), gap_start))
    repo.write_ohlcv(mp.get_ohlcv("BTCUSD", M5, gap_start + timedelta(hours=6), END))
    rep = validate_dataset(
        repo,
        DatasetRequirements(
            instruments=("BTCUSD",), min_days=5, warmup_bars=100, check_continuity=False
        ),
        start=START,
        end=END,
    )
    assert rep.ok and not any("Lücke" in n for n in rep.notes)


def test_validate_dataset_min_days_note(tmp_path) -> None:
    repo = _repo(tmp_path)
    rep = validate_dataset(
        repo,
        DatasetRequirements(instruments=("BTCUSD",), min_days=180, warmup_bars=100),
        start=START,
        end=END,
    )
    assert any("< gefordert 180" in n for n in rep.notes)  # Hinweis, kein harter Fehler
    assert rep.ok  # 7-Tage-Fenster reicht technisch für den Replay


# --------------------------------------------------------------------------- Assembler PIT


def test_assembler_builds_pit_marketcontext(tmp_path) -> None:
    repo = _repo(tmp_path)
    asm = MarketContextAssembler(repo, AssemblerConfig(instrument="BTCUSD", warmup_bars=200))
    cutoff = START + timedelta(days=2)
    mc = asm.at(cutoff)
    assert mc.information_cutoff == cutoff
    assert mc.base_timeframe is M5
    m5 = mc.series[M5]
    assert m5 and all(b.close_time <= cutoff for b in m5)  # kein Look-ahead
    assert m5[-1].close_time <= cutoff < m5[-1].close_time + timedelta(minutes=10)
    # höhere TF nicht im Repo → nicht in series (werden später aus M5 abgeleitet)
    assert set(mc.series) == {M5}
    # kein News-Feed konfiguriert → fail-safe
    assert mc.news.feed_as_of is None


def test_assembler_native_higher_tf_windowed(tmp_path) -> None:
    from trading_agent.data.resample import resample_ohlcv

    repo = MarketDataRepository(tmp_path / "repo")
    mp = MockMarketDataProvider(clock=FixedClock(END), volatility=0.005)
    m5 = mp.get_ohlcv("BTCUSD", M5, START - timedelta(days=30), END)
    repo.write_ohlcv(m5)
    for tf in (Timeframe.M15, Timeframe.H4, Timeframe.D1):
        repo.write_ohlcv(resample_ohlcv(m5, M5, tf, require_complete=True))

    asm = MarketContextAssembler(
        repo,
        AssemblerConfig(
            instrument="BTCUSD",
            warmup_bars=200,
            higher_warmup_bars={Timeframe.M15: 50, Timeframe.H4: 20, Timeframe.D1: 10},
        ),
    )
    cutoff = START + timedelta(days=3)
    mc = asm.at(cutoff)
    assert Timeframe.M15 in mc.series and Timeframe.H4 in mc.series and Timeframe.D1 in mc.series
    assert len(mc.series[Timeframe.M15]) <= 50
    assert len(mc.series[Timeframe.D1]) <= 10
    for tf in (Timeframe.M15, Timeframe.H4, Timeframe.D1):
        assert all(b.close_time <= cutoff for b in mc.series[tf])  # kein Look-ahead


def test_validate_dataset_native_higher_depth(tmp_path) -> None:
    from trading_agent.data.resample import resample_ohlcv

    repo = MarketDataRepository(tmp_path / "repo")
    mp = MockMarketDataProvider(clock=FixedClock(END), volatility=0.005)
    m5 = mp.get_ohlcv("BTCUSD", M5, START - timedelta(days=5), END)  # nur 5 Tage → wenig D1
    repo.write_ohlcv(m5)
    for tf in (Timeframe.M15, Timeframe.H4, Timeframe.D1):
        repo.write_ohlcv(resample_ohlcv(m5, M5, tf, require_complete=True))

    rep = validate_dataset(
        repo,
        DatasetRequirements(
            instruments=("BTCUSD",),
            min_days=1,
            warmup_bars=100,
            require_native_higher=True,
            higher_min_bars={Timeframe.M15: 10, Timeframe.H4: 10, Timeframe.D1: 60},
        ),
        start=START,
        end=END,
    )
    assert not rep.ok
    assert any(g.timeframe is Timeframe.D1 and "Vorlauf" in g.reason for g in rep.missing)


def test_assembler_lookahead_immune_to_future_writes(tmp_path) -> None:
    repo = _repo(tmp_path)
    asm = MarketContextAssembler(repo, AssemblerConfig(instrument="BTCUSD", warmup_bars=200))
    cutoff = START + timedelta(days=2)
    before = [(b.open_time, b.close) for b in asm.at(cutoff).series[M5]]

    # zukünftige Bars grob verfälschen
    future = repo.read_ohlcv("BTCUSD", M5, cutoff, END)
    repo.write_ohlcv(
        [
            b.model_copy(
                update={
                    "open": b.open * 5,
                    "high": b.high * 5,
                    "low": b.low * 5,
                    "close": b.close * 5,
                }
            )
            for b in future
        ]
    )
    after = [(b.open_time, b.close) for b in asm.at(cutoff).series[M5]]
    assert before == after  # nichts nach dem cutoff beeinflusst den Kontext


def test_assembler_raises_when_no_bars(tmp_path) -> None:
    repo = _repo(tmp_path)
    asm = MarketContextAssembler(repo, AssemblerConfig(instrument="BTCUSD"))
    with pytest.raises(DatasetIncompleteError, match="kein Fake"):
        asm.at(START - timedelta(days=10))


# --------------------------------------------------------------------------- Harness


def test_harness_feeds_every_cutoff(tmp_path) -> None:
    repo = _repo(tmp_path)
    asm = MarketContextAssembler(repo, AssemblerConfig(instrument="BTCUSD", warmup_bars=100))
    clock = ReplayClock.from_range(START, START + timedelta(hours=3), M5)

    seen: list[datetime] = []

    class _Collect:
        def feed(self, mc: object) -> None:
            seen.append(mc.information_cutoff)  # type: ignore[attr-defined]

    res = ReplayHarness(clock, asm).run(_Collect())
    assert res.steps == len(clock) == len(seen)
    assert seen == sorted(seen)
    assert res.first_cutoff == seen[0] and res.last_cutoff == seen[-1]
