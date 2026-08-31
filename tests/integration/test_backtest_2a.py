"""Phase 2A integration: mock data -> repository -> event-driven backtest -> research.

Covers the Phase-2A exit-gate claims: deterministic reproducibility, look-ahead immunity,
cost impact, trade ledger trace, walk-forward + monte-carlo run on real trades.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from trading_agent.core.clock import FixedClock, SimClock
from trading_agent.core.enums import Timeframe
from trading_agent.core.time import parse_timestamp
from trading_agent.data.providers.mock_provider import MockMarketDataProvider
from trading_agent.data.repository import MarketDataRepository
from trading_agent.engine.reference_backtest import ReferenceBacktest, ReferenceBacktestConfig
from trading_agent.refdata.seed import build_instrument_master
from trading_agent.research.registry import RunRecord, RunRegistry
from trading_agent.research.robustness import monte_carlo
from trading_agent.research.validation import time_stability, walk_forward_folds

pytestmark = pytest.mark.integration

START = parse_timestamp("2024-06-01T00:00:00Z")
END = parse_timestamp("2024-06-15T00:00:00Z")
IM = build_instrument_master()


def _repo_with_btc(root: Path, *, volatility: float = 0.006) -> MarketDataRepository:
    repo = MarketDataRepository(root)
    mp = MockMarketDataProvider(clock=FixedClock(END), volatility=volatility)
    repo.write_ohlcv(mp.get_ohlcv("BTCUSDT", Timeframe.M15, START, END))
    return repo


def _cfg(**kw: object) -> ReferenceBacktestConfig:
    base: dict = dict(
        instrument="BTCUSDT",
        timeframe=Timeframe.M15,
        start=START,
        end=END,
        starting_equity=10_000.0,
        risk_per_trade_pct=1.0,
    )
    base.update(kw)
    return ReferenceBacktestConfig(**base)  # type: ignore[arg-type]


async def test_backtest_produces_trades_and_ledger_trace(tmp_path: Path) -> None:
    repo = _repo_with_btc(tmp_path / "repo")
    bt = ReferenceBacktest(repo, IM)
    result = await bt.run(_cfg())

    assert result.bars_processed > 1000
    assert result.trades, "reference strategy should trade on a 2-week window"
    m = result.metrics
    assert m.n_trades == len(result.trades)

    # every trade has a full decision trace SIGNAL -> ORDER -> EXIT under one trace_id
    for t in result.trades:
        steps = [d["step"] for d in bt.ledger.decisions_for(t.trace_id or "")]
        assert steps == ["SIGNAL", "ORDER", "EXIT"]

    # fees were charged; cost drag is visible
    assert any(t.fees_ccy > 0 for t in result.trades)
    assert m.cost_drag_r != 0.0


async def test_reproducible_output_hash(tmp_path: Path) -> None:
    r1 = await ReferenceBacktest(_repo_with_btc(tmp_path / "a"), IM).run(_cfg())
    r2 = await ReferenceBacktest(_repo_with_btc(tmp_path / "b"), IM).run(_cfg())
    assert r1.output_hash == r2.output_hash
    assert r1.manifest.manifest_hash() == r2.manifest.manifest_hash()
    assert [t.realized_r for t in r1.trades] == [t.realized_r for t in r2.trades]


async def test_look_ahead_immunity(tmp_path: Path) -> None:
    """Corrupting bars AFTER the decision cutoff must not change decisions up to the cutoff."""
    cutoff = START + timedelta(days=7)

    clean = _repo_with_btc(tmp_path / "clean")
    baseline = await ReferenceBacktest(clean, IM).run(_cfg(end=cutoff))

    corrupt = _repo_with_btc(tmp_path / "corrupt")
    future_bars = corrupt.read_ohlcv("BTCUSDT", Timeframe.M15, cutoff, END)
    mangled = [
        b.model_copy(
            update={"open": b.open * 3, "high": b.high * 3, "low": b.low * 3, "close": b.close * 3}
        )
        for b in future_bars
    ]
    corrupt.write_ohlcv(mangled)
    with_future_noise = await ReferenceBacktest(corrupt, IM).run(_cfg(end=cutoff))

    assert baseline.output_hash == with_future_noise.output_hash
    assert [t.trade_id for t in baseline.trades] == [t.trade_id for t in with_future_noise.trades]


async def test_cost_impact_measurable(tmp_path: Path) -> None:
    repo = _repo_with_btc(tmp_path / "repo")
    from trading_agent.execution.simulation import CostParams

    normal = await ReferenceBacktest(repo, IM).run(_cfg())
    zero = await ReferenceBacktest(repo, IM).run(
        _cfg(cost=CostParams(slippage_atr=0.0, slippage_spread_mult=0.0, min_slippage_bps=0.0))
    )
    # net expectancy with costs is <= without (fees still apply via broker fee schedule)
    assert normal.metrics.expectancy_r <= zero.metrics.expectancy_r + 1e-9


async def test_walk_forward_and_monte_carlo_on_real_trades(tmp_path: Path) -> None:
    result = await ReferenceBacktest(_repo_with_btc(tmp_path / "repo", volatility=0.008), IM).run(
        _cfg()
    )
    if len(result.trades) < 5:
        pytest.skip("not enough trades in this synthetic window")

    folds = walk_forward_folds(START, END, train_days=6, test_days=3, step_days=3)
    assert folds
    covered = sum(len(f.test_trades(result.trades)) for f in folds)
    assert covered >= 0  # folds slice the trade list without error

    stab = time_stability(result.trades, window_days=4, step_days=2)
    assert stab

    mc = monte_carlo(result.trades, runs=300, seed=3, ruin_threshold_r=10.0)
    assert mc.runs == 300
    assert mc.final_equity_r_p05 <= mc.final_equity_r_p95


async def test_run_registry_persists_manifest(tmp_path: Path) -> None:
    result = await ReferenceBacktest(_repo_with_btc(tmp_path / "repo"), IM).run(_cfg())
    reg = RunRegistry(tmp_path / "runs")
    reg.save(
        RunRecord(
            manifest=result.manifest,
            output_hash=result.output_hash,
            metrics=result.metrics.as_dict(),
        )
    )
    assert reg.count() == 1


async def test_backtest_uses_simclock_not_wallclock(tmp_path: Path) -> None:
    """The BacktestDriver drives a SimClock; the run never reads the wall clock for decisions."""
    from trading_agent.runtime.bus import EventBus
    from trading_agent.runtime.drivers.backtest_driver import BacktestDriver

    repo = _repo_with_btc(tmp_path / "repo")
    driver = BacktestDriver(EventBus(), repo)
    n = await driver.run("BTCUSDT", Timeframe.M15, START, START + timedelta(days=1))
    assert n > 0
    assert isinstance(driver.clock, SimClock)
    assert driver.clock.now() <= START + timedelta(days=1)
