"""Phase 3 · erweiterte Backtest-Auswertung (``engine.backtest_metrics``).

Exit-Raten · Hold-Time · MFE-Ausnutzung (Exit-Qualität) · Segmente (Long/Short, Tier, Grund,
Asset) · Signal-Analyse-Buckets + Korrelation Score/Confidence vs. Ergebnis · Lauf-Telemetrie.
Keine Kennzahl wird geschönt.
"""

from __future__ import annotations

from collections import Counter

from trading_agent.core.enums import Side
from trading_agent.engine.backtest_metrics import (
    RunTelemetry,
    TradeOutcome,
    build_strategy_report,
    confidence_tier_of,
)


def _oc(
    tid: str,
    *,
    direction: Side = Side.BUY,
    instrument: str = "BTCUSD",
    realized_r: float = 1.0,
    mfe_r: float = 1.5,
    mae_r: float = -0.4,
    bars_held: int = 10,
    exit_reason: str = "tp1",
    tp_level: int = 1,
    score: float | None = 80.0,
    score_tier: str = "A",
    confidence: float | None = 0.75,
    confluence_net: float | None = 0.3,
) -> TradeOutcome:
    wl = "WIN" if realized_r > 0.1 else "LOSS" if realized_r < -0.1 else "SCRATCH"
    return TradeOutcome(
        trade_id=tid,
        instrument=instrument,
        timeframe="M5",
        direction=direction,
        setup_id="SMC-SWEEP-REV-01",
        entry_ts=f"2024-06-0{tid[-1]}T00:00:00+00:00",
        exit_ts=f"2024-06-0{tid[-1]}T02:00:00+00:00",
        realized_r=realized_r,
        gross_r=realized_r,
        mfe_r=mfe_r,
        mae_r=mae_r,
        bars_held=bars_held,
        exit_reason=exit_reason,
        tp_level=tp_level,
        win_loss=wl,
        score=score,
        score_tier=score_tier,
        confidence=confidence,
        confidence_tier=confidence_tier_of(confidence),
        confluence_net=confluence_net,
        confluence_support=0.6,
        setup_state_at_entry="armed",
    )


def _tele(**kw: object) -> RunTelemetry:
    base: dict = dict(
        steps=1000,
        decisions=Counter({"no_trade": 900, "wait": 60, "buy": 30, "sell": 10}),
        no_trade_reasons=Counter({"regime_unclear": 400, "score_below_b": 200}),
        veto_frequency=Counter({"V3": 12, "V8": 5}),
        signal_revisions=80,
        signals_created=25,
        signals_invalidated=6,
        signals_expired=4,
        exit_required_events=3,
        alerts_raised=44,
    )
    base.update(kw)
    return RunTelemetry(**base)  # type: ignore[arg-type]


def test_empty_is_all_zero() -> None:
    rep = build_strategy_report([], _tele(signals_created=0))
    assert rep.n_trades == 0
    assert rep.tp1_hit_rate == 0.0 and rep.exit_efficiency == 0.0
    assert rep.score_outcome_correlation is None


def test_exit_rates_and_tp_levels() -> None:
    ocs = [
        _oc("t1", exit_reason="tp1", tp_level=1, realized_r=1.0),
        _oc("t2", exit_reason="tp2", tp_level=2, realized_r=2.0),
        _oc("t3", exit_reason="stop_loss", tp_level=0, realized_r=-1.0),
        _oc("t4", exit_reason="breakeven_stop", tp_level=1, realized_r=0.0),
    ]
    rep = build_strategy_report(ocs, _tele())
    assert rep.n_trades == 4
    assert rep.tp1_hit_rate == 0.75  # t1, t2, t4 haben tp_level >= 1
    assert rep.tp2_hit_rate == 0.25
    assert rep.tp3_hit_rate == 0.0
    assert rep.stop_rate == 0.25
    assert rep.breakeven_rate == 0.25


def test_exit_quality_efficiency_and_giveback() -> None:
    ocs = [
        _oc("t1", realized_r=1.0, mfe_r=2.0),  # 1.0 von 2.0 mitgenommen
        _oc("t2", realized_r=1.0, mfe_r=2.0),
    ]
    rep = build_strategy_report(ocs, _tele())
    assert rep.exit_efficiency == 0.5  # Σ2 / Σ4
    assert rep.avg_give_back_r == 1.0  # im Mittel 1R liegen gelassen


def test_direction_segments_symmetry() -> None:
    ocs = [
        _oc("t1", direction=Side.BUY, realized_r=2.0),
        _oc("t2", direction=Side.SELL, realized_r=2.0),
        _oc("t3", direction=Side.SELL, realized_r=-1.0),
    ]
    rep = build_strategy_report(ocs, _tele())
    longs = next(s for s in rep.by_direction if s.label == "LONG")
    shorts = next(s for s in rep.by_direction if s.label == "SHORT")
    assert longs.n == 1 and shorts.n == 2
    assert longs.win_rate == 1.0 and shorts.win_rate == 0.5


def test_score_bucket_and_correlation_information_value() -> None:
    # Score korreliert perfekt positiv mit dem Ergebnis
    ocs = [
        _oc("t1", score=50.0, realized_r=-2.0, score_tier="B"),
        _oc("t2", score=60.0, realized_r=-1.0, score_tier="B"),
        _oc("t3", score=70.0, realized_r=0.5, score_tier="A"),
        _oc("t4", score=80.0, realized_r=1.5, score_tier="A"),
        _oc("t5", score=90.0, realized_r=3.0, score_tier="A+"),
    ]
    rep = build_strategy_report(ocs, _tele())
    assert rep.score_outcome_correlation is not None
    assert rep.score_outcome_correlation > 0.95  # klarer Informationswert
    lo = next(b for b in rep.score_vs_outcome if b.lo == 0)
    hi = next(b for b in rep.score_vs_outcome if b.lo == 85)
    assert lo.avg_realized_r < 0 < hi.avg_realized_r


def test_by_tier_segments() -> None:
    ocs = [
        _oc("t1", score_tier="A+", realized_r=2.0),
        _oc("t2", score_tier="A", realized_r=1.0),
        _oc("t3", score_tier="A", realized_r=-1.0),
        _oc("t4", score_tier="B", realized_r=-0.5),
    ]
    rep = build_strategy_report(ocs, _tele())
    tiers = {s.label: s for s in rep.by_score_tier}
    assert tiers["A+"].n == 1 and tiers["A"].n == 2 and tiers["B"].n == 1
    assert tiers["A+"].expectancy_r == 2.0


def test_telemetry_rates() -> None:
    rep = build_strategy_report(
        [_oc("t1")], _tele(signals_created=25, signals_invalidated=5, exit_required_events=2)
    )
    assert rep.invalidated_setup_rate == 5 / 25
    assert rep.exit_required_rate == 2 / 25
    assert rep.veto_rate_per_decision == 17 / 1000  # (12 V3 + 5 V8) / 1000 decisions
    assert rep.telemetry.no_trade_reasons["regime_unclear"] == 400


def test_confidence_tier_of() -> None:
    assert confidence_tier_of(None) == "?"
    assert confidence_tier_of(0.85) == "high"
    assert confidence_tier_of(0.65) == "mid"
    assert confidence_tier_of(0.4) == "low"


def test_hold_time_and_instrument_segments() -> None:
    ocs = [
        _oc("t1", instrument="BTCUSD", bars_held=10),
        _oc("t2", instrument="ETHUSD", bars_held=30),
        _oc("t3", instrument="ETHUSD", bars_held=20),
    ]
    rep = build_strategy_report(ocs, _tele())
    assert rep.avg_hold_bars == 20.0
    assert rep.median_hold_bars == 20.0
    by_inst = {s.label: s.n for s in rep.by_instrument}
    assert by_inst == {"BTCUSD": 1, "ETHUSD": 2}
