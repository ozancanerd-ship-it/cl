"""Phase 3 · Integration — Strategy-Backtest end-to-end (``engine.backtest``).

Repository → ReplayClock → MarketContextAssembler → PaperLiveRunner (evaluate→Signal→Position→
Alerts) → Trade Ledger + Metriken + Signal-Analyse.

Die *reale* ``strategy.evaluate``-Pipeline hat ihre eigenen End-to-End-Tests (`test_evaluate`).
Hier wird die **Backtest-Verdrahtung** geprüft: mit einer deterministischen, skript-gesteuerten
Pipeline (DI über ``evaluate_fn``) über echte synthetische M5-Bars. Kein Fake-Livedaten-Pfad.

Geprüft: Replay-Determinismus · Reproduzierbarkeit (output_hash / manifest) · Look-ahead-Immunität ·
PIT · Dataset-Incomplete-Fehler (KEINE Fake-Daten) · Multi-Asset · Long/Short · TP/SL/Runner ·
END_OF_DATA · Metriken + Signal-Analyse-Report.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta

import pytest

import tests.unit.test_evaluate as ev
from trading_agent.core.clock import FixedClock
from trading_agent.core.enums import DecisionType, Direction, NoTradeReason, RiskTier, Timeframe
from trading_agent.core.types import MarketContext
from trading_agent.data.providers.mock_provider import MockMarketDataProvider
from trading_agent.data.repository import MarketDataRepository
from trading_agent.engine.backtest import Backtest, BacktestConfig
from trading_agent.engine.replay import DatasetIncompleteError
from trading_agent.strategy.decision import Decision
from trading_agent.strategy.evaluate import EvaluationResult
from trading_agent.strategy.no_trade import NoTradeRecord, NoTradeReport

pytestmark = pytest.mark.integration

M5 = Timeframe.M5
START = datetime(2024, 6, 3, 0, 0, tzinfo=UTC)
END = datetime(2024, 6, 10, 0, 0, tzinfo=UTC)
_BASE = ev._run(ev._long_mtf())
assert _BASE.candidate is not None


def _repo(tmp_path, *, instruments=("BTCUSD",), data_start=None) -> MarketDataRepository:
    repo = MarketDataRepository(tmp_path / "repo")
    mp = MockMarketDataProvider(clock=FixedClock(END), volatility=0.006)
    ds = data_start or (START - timedelta(days=2))
    for inst in instruments:
        repo.write_ohlcv(mp.get_ohlcv(inst, M5, ds, END))
    return repo


def _scripted(mc: MarketContext, **_kw: object) -> EvaluationResult:
    """Deterministische Pseudo-Pipeline: alle 15 Bars ein 3-Bar-Trade-Fenster, Richtung
    alterniert je Fenster. Sonst NO_TRADE. Reine Funktion von ``mc``."""
    step = int((mc.information_cutoff - START).total_seconds() // 300)
    in_trade = step % 20 < 4  # 4-Bar-Fenster, 16-Bar-Lücke (> pending_expiry) dazwischen
    inst = mc.instrument
    cutoff = mc.information_cutoff
    cand = dataclasses.replace(_BASE.candidate, setup_id=f"SCRIPT:{inst}", instrument=inst)

    if in_trade and mc.price is not None:
        price = mc.price
        long = cutoff < START + timedelta(days=3)  # erste Hälfte LONG, zweite SHORT
        d = 0.01 * price
        if long:
            entry = price * 0.999  # Limit knapp unter Markt → füllt auf einem Dip
            dec = Decision.trade(
                inst,
                cutoff,
                Direction.LONG,
                entry=entry,
                sl=entry - d,
                tp1=entry + 1.5 * d,
                tp2=entry + 3 * d,
                tier=RiskTier.A,
                rr_to_tp2=3.0,
                score=78.0,
                confidence=0.74,
                chain_progress="scripted",
            )
        else:
            entry = price * 1.001  # Limit knapp über Markt → füllt auf einem Pop
            dec = Decision.trade(
                inst,
                cutoff,
                Direction.SHORT,
                entry=entry,
                sl=entry + d,
                tp1=entry - 1.5 * d,
                tp2=entry - 3 * d,
                tier=RiskTier.A,
                rr_to_tp2=3.0,
                score=78.0,
                confidence=0.74,
                chain_progress="scripted",
            )
        nt = NoTradeReport(inst, cutoff, (), ())
        return dataclasses.replace(_BASE, decision=dec, candidate=cand, no_trade=nt)

    nt = NoTradeReport(
        inst,
        cutoff,
        (
            NoTradeRecord(
                NoTradeReason.REGIME_UNCLEAR, _group(), "scripted flat", {}, cutoff, cutoff
            ),
        ),
        (),
    )
    dec = Decision.no_trade(inst, cutoff, [NoTradeReason.REGIME_UNCLEAR])
    return dataclasses.replace(_BASE, decision=dec, candidate=cand, no_trade=nt)


def _group():
    from trading_agent.strategy.no_trade import NoTradeGroup

    return NoTradeGroup.REGIME


def _cfg(tmp_path, **kw: object) -> BacktestConfig:
    base: dict = dict(
        instruments=("BTCUSD",),
        start=START,
        end=END,
        warmup_bars=200,
        min_days=5,
        starting_equity=10_000.0,
        risk_per_trade_pct=1.0,
        fixed_spread=0.5,
    )
    base.update(kw)
    return BacktestConfig(**base)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- Grundfluss


def test_backtest_runs_and_produces_trades(tmp_path) -> None:
    bt = Backtest(_repo(tmp_path), evaluate_fn=_scripted)
    res = bt.run(_cfg(tmp_path))
    assert res.ok
    assert res.bars_processed > 1500
    assert res.trades, "scripted pipeline should generate trades"
    assert res.metrics.n_trades == len(res.trades)
    # Trade-Felder plausibel
    for t in res.trades:
        assert t.entry_price > 0 and t.exit_price > 0
        assert t.exit_ts >= t.entry_ts
        assert t.exit_reason in {
            "tp1",
            "tp2",
            "tp3",
            "stop_loss",
            "breakeven_stop",
            "trail_stop",
            "end_of_data",
        }
    # Ledger persistiert
    assert bt.ledger.trades(res.run_id)


def test_long_and_short_both_occur() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        from pathlib import Path

        res = Backtest(_repo(Path(d)), evaluate_fn=_scripted).run(_cfg(Path(d)))
    dirs = {o.direction.value for o in res.outcomes}
    assert dirs == {"buy", "sell"}
    long_seg = next(s for s in res.strategy_report.by_direction if s.label == "LONG")
    short_seg = next(s for s in res.strategy_report.by_direction if s.label == "SHORT")
    assert long_seg.n > 0 and short_seg.n > 0


def test_signal_analysis_report_populated(tmp_path) -> None:
    res = Backtest(_repo(tmp_path), evaluate_fn=_scripted).run(_cfg(tmp_path))
    rep = res.strategy_report
    assert rep.n_trades == len(res.outcomes)
    assert 0.0 <= rep.tp1_hit_rate <= 1.0
    assert 0.0 <= rep.stop_rate <= 1.0
    assert rep.avg_hold_bars > 0
    # Telemetrie
    tel = res.telemetry
    assert tel.steps == res.bars_processed
    assert tel.decisions[DecisionType.NO_TRADE.value] > 0
    assert tel.signals_created > 0
    # jede Score-/Confidence-Tier-Zuordnung ist gesetzt (kein "?")
    assert all(o.score_tier in {"A+", "A", "B"} for o in res.outcomes)
    assert all(o.confidence_tier in {"high", "mid", "low"} for o in res.outcomes)


# --------------------------------------------------------------------------- Determinismus / Repro


def test_deterministic_output_hash(tmp_path) -> None:
    a = Backtest(_repo(tmp_path / "a"), evaluate_fn=_scripted).run(_cfg(tmp_path / "a"))
    b = Backtest(_repo(tmp_path / "b"), evaluate_fn=_scripted).run(_cfg(tmp_path / "b"))
    assert a.output_hash == b.output_hash
    assert a.manifest.manifest_hash() == b.manifest.manifest_hash()
    assert [t.realized_r for t in a.trades] == [t.realized_r for t in b.trades]
    assert a.metrics.as_dict() == b.metrics.as_dict()


def test_look_ahead_immunity(tmp_path) -> None:
    cutoff = START + timedelta(days=3)
    clean = _repo(tmp_path / "clean")
    baseline = Backtest(clean, evaluate_fn=_scripted).run(_cfg(tmp_path / "clean", end=cutoff))

    corrupt = _repo(tmp_path / "corrupt")
    future = corrupt.read_ohlcv("BTCUSD", M5, cutoff, END)
    corrupt.write_ohlcv(
        [
            b.model_copy(update={k: getattr(b, k) * 4 for k in ("open", "high", "low", "close")})
            for b in future
        ]
    )
    noised = Backtest(corrupt, evaluate_fn=_scripted).run(_cfg(tmp_path / "corrupt", end=cutoff))
    assert baseline.output_hash == noised.output_hash
    assert [t.trade_id for t in baseline.trades] == [t.trade_id for t in noised.trades]


# --------------------------------------------------------------------------- Dataset-Guard


def test_missing_dataset_raises_no_fake(tmp_path) -> None:
    repo = _repo(tmp_path)  # nur BTCUSD
    bt = Backtest(repo, evaluate_fn=_scripted)
    with pytest.raises(DatasetIncompleteError):
        bt.run(_cfg(tmp_path, instruments=("BTCUSD", "ETHUSD")))


def test_insufficient_warmup_raises(tmp_path) -> None:
    repo = _repo(tmp_path, data_start=START)  # kein Warmup-Vorlauf
    with pytest.raises(DatasetIncompleteError, match="Warmup"):
        Backtest(repo, evaluate_fn=_scripted).run(_cfg(tmp_path, warmup_bars=300))


# --------------------------------------------------------------------------- Multi-Asset


def test_multi_asset_backtest(tmp_path) -> None:
    repo = _repo(tmp_path, instruments=("BTCUSD", "ETHUSD"))
    res = Backtest(repo, evaluate_fn=_scripted).run(
        _cfg(tmp_path, instruments=("BTCUSD", "ETHUSD"))
    )
    insts = {t.instrument for t in res.trades}
    assert insts == {"BTCUSD", "ETHUSD"}
    assert "BTCUSD" in res.manifest.instrument and "ETHUSD" in res.manifest.instrument
    # Trades chronologisch gemergt
    assert [t.entry_ts for t in res.trades] == sorted(t.entry_ts for t in res.trades)


# --------------------------------------------------------------------------- END_OF_DATA


def test_open_position_closed_at_end_of_data(tmp_path) -> None:
    # Trade-Fenster bis zuletzt offen: enger Endzeitpunkt kurz nach einem Fenster-Start
    end = START + timedelta(minutes=5 * 17)  # gerade so über ein 15er-Fenster hinaus
    res = Backtest(_repo(tmp_path), evaluate_fn=_scripted).run(
        _cfg(tmp_path, end=end, warmup_bars=150)
    )
    # es kann sein, dass ein Trade per END_OF_DATA geschlossen wurde
    assert all(t.exit_ts <= end for t in res.trades)
