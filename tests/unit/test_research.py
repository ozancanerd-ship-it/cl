"""Tests: ledger, metrics, validation splits/walk-forward/stability, monte-carlo, run manifest."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from trading_agent.core.enums import Side, Timeframe
from trading_agent.core.time import parse_timestamp
from trading_agent.journal.ledger import Ledger, TradeRecord
from trading_agent.research.metrics import compute_metrics, equity_curve_r
from trading_agent.research.registry import RunManifest, RunRecord, RunRegistry, output_hash
from trading_agent.research.robustness import monte_carlo
from trading_agent.research.validation import (
    chronological_split,
    fraction_positive_windows,
    purge_embargo,
    symbol_stability,
    time_stability,
    walk_forward_folds,
)

T0 = parse_timestamp("2024-01-01T00:00:00Z")


def _trade(i: int, r: float, *, instrument: str = "BTCUSDT", days: int = 0) -> TradeRecord:
    entry = T0 + timedelta(days=days or i)
    return TradeRecord(
        trade_id=f"t{i}",
        instrument=instrument,
        direction=Side.BUY,
        signal_ts=entry,
        information_cutoff=entry,
        entry_ts=entry,
        entry_price=100.0,
        qty=1.0,
        exit_ts=entry + timedelta(hours=4),
        exit_price=100.0 + r,
        exit_reason="TP" if r > 0 else "SL",
        gross_r=r,
        realized_r=r,
        pnl_ccy=r,
        mfe_r=max(r, 0.5),
        mae_r=min(r, -0.3),
        win_loss="WIN" if r > 0.1 else "LOSS" if r < -0.1 else "SCRATCH",
    )


class TestLedger:
    def test_decision_and_trade_roundtrip(self, tmp_path: Path) -> None:
        lg = Ledger(tmp_path / "l.sqlite")
        lg.record_decision("SIGNAL", trace_id="tr1", instrument="BTCUSDT", payload={"x": 1})
        lg.record_decision("EXIT", trace_id="tr1", instrument="BTCUSDT", payload={"r": 1.5})
        assert lg.decision_count() == 2
        chain = lg.decisions_for("tr1")
        assert [d["step"] for d in chain] == ["SIGNAL", "EXIT"]

        rec = _trade(1, 1.5)
        rec = rec.model_copy(update={"run_id": "run1", "trace_id": "tr1"})
        lg.record_trade(rec)
        back = lg.trades("run1")
        assert len(back) == 1 and back[0].realized_r == 1.5


class TestMetrics:
    def test_basic(self) -> None:
        trades = [_trade(1, 2.0), _trade(2, -1.0), _trade(3, 1.0), _trade(4, -1.0)]
        m = compute_metrics(trades)
        assert m.n_trades == 4
        assert m.win_rate == 0.5
        assert m.profit_factor == 3.0 / 2.0
        assert abs(m.expectancy_r - 0.25) < 1e-9
        assert m.longest_loss_streak == 1

    def test_empty(self) -> None:
        m = compute_metrics([])
        assert m.n_trades == 0 and m.expectancy_r == 0.0

    def test_equity_curve(self) -> None:
        assert equity_curve_r([_trade(1, 1.0), _trade(2, -0.5)]) == [1.0, 0.5]

    def test_cost_drag(self) -> None:
        t = _trade(1, 1.0)
        t = t.model_copy(update={"gross_r": 1.2, "realized_r": 1.0})
        assert abs(compute_metrics([t]).cost_drag_r - 0.2) < 1e-9


class TestValidation:
    def test_chronological_split(self) -> None:
        trades = [_trade(i, 1.0) for i in range(1, 11)]
        s = chronological_split(trades, train=0.5, validation=0.3)
        assert len(s.train) == 5 and len(s.validation) == 3 and len(s.test) == 2

    def test_walk_forward_folds(self) -> None:
        folds = walk_forward_folds(
            T0, T0 + timedelta(days=365), train_days=180, test_days=60, step_days=60
        )
        assert folds and folds[0].train_start == T0
        assert folds[0].test_start == folds[0].train_end
        for f in folds:
            assert f.test_end <= T0 + timedelta(days=365)

    def test_purge_embargo_drops_overlapping(self) -> None:
        boundary = T0 + timedelta(days=10)
        trades = [_trade(i, 1.0, days=i) for i in range(1, 20)]
        kept = purge_embargo(
            trades, boundary=boundary, timeframe=Timeframe.H1, max_hold_bars=48, embargo_bars=48
        )
        # trades close to the boundary are removed
        assert all(
            not (
                t.entry_ts < boundary + timedelta(hours=48)
                and t.exit_ts > boundary - timedelta(hours=48)
            )
            for t in kept
        )
        assert len(kept) < len(trades)

    def test_time_stability(self) -> None:
        trades = [_trade(i, 1.0 if i % 2 else -1.0, days=i * 5) for i in range(1, 20)]
        stats = time_stability(trades, window_days=30, step_days=15)
        frac = fraction_positive_windows(stats, min_trades=1)
        assert 0.0 <= frac <= 1.0

    def test_symbol_stability(self) -> None:
        trades = [
            _trade(1, 3.0, instrument="BTCUSDT"),
            _trade(2, -0.5, instrument="ETHUSDT"),
            _trade(3, -0.2, instrument="ETHUSDT"),
        ]
        rep = symbol_stability(trades)
        assert rep.per_symbol_total_r["BTCUSDT"] == 3.0
        assert rep.total_r_without_best < 0  # edge carried entirely by BTC


class TestRobustness:
    def test_monte_carlo_shapes(self) -> None:
        trades = [_trade(i, 1.5 if i % 3 else -1.0) for i in range(1, 61)]
        rep = monte_carlo(trades, runs=200, seed=1, ruin_threshold_r=8.0)
        assert rep.runs == 200
        assert rep.final_equity_r_p05 <= rep.final_equity_r_p50 <= rep.final_equity_r_p95
        assert 0.0 <= rep.ruin_probability <= 1.0
        assert 0.0 <= rep.prob_positive <= 1.0

    def test_monte_carlo_deterministic(self) -> None:
        trades = [_trade(i, 1.0 if i % 2 else -1.0) for i in range(1, 41)]
        a = monte_carlo(trades, runs=100, seed=7)
        b = monte_carlo(trades, runs=100, seed=7)
        assert a == b

    def test_monte_carlo_empty(self) -> None:
        assert monte_carlo([], runs=10).runs == 0


class TestRunManifest:
    def test_manifest_hash_stable_ignoring_created_at(self) -> None:
        kw = dict(
            strategy_version="0.1.0",
            config_hash="abc",
            dataset_version="v1",
            dataset_fingerprint="fp",
            instrument="BTCUSDT",
            timeframe="M5",
            start=T0.isoformat(),
            end=(T0 + timedelta(days=1)).isoformat(),
            seed=0,
        )
        m1 = RunManifest(**kw)  # type: ignore[arg-type]
        m2 = RunManifest(**kw)  # type: ignore[arg-type]
        assert m1.manifest_hash() == m2.manifest_hash()

    def test_registry_save_and_count(self, tmp_path: Path) -> None:
        reg = RunRegistry(tmp_path / "runs")
        m = RunManifest(
            strategy_version="x",
            config_hash="c",
            dataset_version="d",
            dataset_fingerprint="f",
            instrument="BTCUSDT",
            timeframe="M5",
            start=T0.isoformat(),
            end=(T0 + timedelta(days=1)).isoformat(),
            seed=0,
        )
        reg.save(RunRecord(manifest=m, output_hash="oh", metrics={"n_trades": 3}))
        assert reg.count() == 1

    def test_output_hash_order_sensitive(self) -> None:
        a = output_hash([{"r": 1}, {"r": 2}])
        b = output_hash([{"r": 2}, {"r": 1}])
        assert a != b
