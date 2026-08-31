"""Phase 3 · Integration — Backtest ↔ Paper Parität (``engine.parity``).

Der vorgeladene Replay-Assembler (``bind(start, end)`` + Slice) muss für jeden ``cutoff`` die
**identische** Decision liefern wie ein pro Schritt frisch gebauter Kontext (``bind(cutoff,
cutoff)``). Weichen sie ab, hat der Replay Look-ahead.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from trading_agent.core.clock import FixedClock
from trading_agent.core.enums import Timeframe
from trading_agent.data.providers.mock_provider import MockMarketDataProvider
from trading_agent.data.repository import MarketDataRepository
from trading_agent.data.resample import resample_ohlcv
from trading_agent.engine.parity import compare_decisions, run_parity
from trading_agent.strategy.decision import Decision

pytestmark = pytest.mark.integration

M5 = Timeframe.M5
START = datetime(2025, 1, 20, 0, 0, tzinfo=UTC)
END = datetime(2025, 1, 22, 0, 0, tzinfo=UTC)
DATA_START = datetime(2024, 12, 1, 0, 0, tzinfo=UTC)  # ~50 Tage M5 → native D1/H4/M15


def _repo(tmp_path: Path) -> MarketDataRepository:
    repo = MarketDataRepository(tmp_path / "repo")
    mp = MockMarketDataProvider(clock=FixedClock(END), volatility=0.005)
    for inst in ("BTCUSDT", "ETHUSDT"):
        m5 = mp.get_ohlcv(inst, M5, DATA_START, END)
        repo.write_ohlcv(m5)
        for tf in (Timeframe.M15, Timeframe.H4, Timeframe.D1):
            repo.write_ohlcv(resample_ohlcv(m5, M5, tf, require_complete=True))
    return repo


def test_backtest_matches_streaming_context(tmp_path: Path) -> None:
    report = run_parity(
        _repo(tmp_path),
        instruments=("BTCUSDT", "ETHUSDT"),
        start=START,
        end=END,
        warmup_bars=200,
        max_cutoffs=40,
    )
    assert report.compared > 0
    assert report.ok, f"Parität verletzt: {report.diffs[:5]}"
    assert report.match_rate == 1.0
    assert set(report.instruments) == {"BTCUSDT", "ETHUSDT"}


def test_backtest_parity_check_integration(tmp_path: Path) -> None:
    """``BacktestConfig.parity_check=True`` hängt einen ParityReport an das Ergebnis."""
    from trading_agent.engine.backtest import Backtest, BacktestConfig

    repo = _repo(tmp_path)
    res = Backtest(repo).run(
        BacktestConfig(
            instruments=("BTCUSDT",),
            start=START,
            end=END,
            warmup_bars=200,
            min_days=1,
            require_native_higher=False,
            parity_check=True,
            parity_sample=25,
        )
    )
    assert res.parity is not None
    assert res.parity.ok, res.parity_summary()
    assert res.parity.match_rate == 1.0
    assert "identisch" in res.parity_summary()


def test_mtf_cache_does_not_change_decisions(tmp_path: Path) -> None:
    """Der über Ticks gehaltene ``mtf_cache`` ist reine Beschleunigung: Für jeden ``cutoff``
    muss ``evaluate`` mit und ohne Cache bit-identische Entscheidungen liefern."""
    from trading_agent.engine.parity import _FIELDS, _key
    from trading_agent.engine.replay import AssemblerConfig, MarketContextAssembler, ReplayClock
    from trading_agent.strategy.evaluate import evaluate

    repo = _repo(tmp_path)
    cfg = AssemblerConfig(
        instrument="BTCUSDT",
        base_timeframe=M5,
        warmup_bars=200,
        read_native_higher=True,
        news_feed_available=False,
        fixed_spread=None,
    )
    asm = MarketContextAssembler(repo, cfg)
    asm.bind(START, END)
    grid = repo.read_ohlcv("BTCUSDT", M5, START, END, as_of=END)
    cutoffs = list(ReplayClock.from_bars(grid))[::7]
    assert len(cutoffs) > 10

    shared: dict[tuple[object, ...], object] = {}
    for c in cutoffs:
        mc = asm.at(c)
        d_nocache = evaluate(mc).decision
        d_cache = evaluate(mc, mtf_cache=shared).decision
        for f in _FIELDS:
            assert _key(d_nocache, f) == _key(d_cache, f), f"{c} {f}"
    assert shared, "Cache wurde nie befüllt — Test prüft nichts"


def test_paper_live_runner_parity_trace(tmp_path: Path) -> None:
    """Zwei ``PaperLiveRunner`` mit derselben MC-Folge → identische Entscheidungs-Spur;
    eine veränderte Folge → ``parity_against`` meldet die Abweichung."""
    from trading_agent.engine.replay import AssemblerConfig, MarketContextAssembler, ReplayClock
    from trading_agent.strategy.paper_live import PaperLiveRunner

    repo = _repo(tmp_path)
    cfg = AssemblerConfig(
        instrument="BTCUSDT",
        base_timeframe=M5,
        warmup_bars=200,
        read_native_higher=True,
        news_feed_available=False,
        fixed_spread=None,
    )
    asm = MarketContextAssembler(repo, cfg)
    asm.bind(START, END)
    grid = repo.read_ohlcv("BTCUSDT", M5, START, END, as_of=END)
    cutoffs = list(ReplayClock.from_bars(grid))[::5]
    mcs = [asm.at(c) for c in cutoffs]

    r1, r2 = PaperLiveRunner(), PaperLiveRunner()
    for mc in mcs:
        r1.feed(mc)
    for mc in mcs:
        r2.feed(mc)

    rep = r1.parity_against(r2.decision_trace)
    assert rep.ok and rep.compared == len(mcs)
    assert rep.match_rate == 1.0

    r3 = PaperLiveRunner()
    for mc in mcs[:-3]:
        r3.feed(mc)
    partial = r1.parity_against(r3.decision_trace)
    assert partial.compared == len(mcs) - 3  # nur gemeinsame Schlüssel


def test_compare_decisions_flags_diff() -> None:
    from trading_agent.core.enums import Direction, NoTradeReason, RiskTier

    t = START
    buy = Decision.trade(
        "BTCUSDT", t, Direction.LONG, entry=100.0, sl=95.0, tp1=110.0, tp2=120.0, tier=RiskTier.A
    )
    nt = Decision.no_trade("BTCUSDT", t, [NoTradeReason.REGIME_UNCLEAR])
    same = compare_decisions([(t, "BTCUSDT", buy)], [(t, "BTCUSDT", buy)])
    assert same.ok and same.matches == 1

    diff = compare_decisions([(t, "BTCUSDT", buy)], [(t, "BTCUSDT", nt)])
    assert not diff.ok
    assert any(d.field == "decision" for d in diff.diffs)
