"""SETUP-BREAKOUT-RETEST-01 — der 2. Setup-Typ. Konsolidierung → Ausbruch → haltender Retest.

Der Detektor liest nur ``mtf.h4.bars`` + ``mtf.d1.structure.directional`` → leichte Fakes.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from trading_agent.core.enums import (
    Direction,
    NoTradeReason,
    Polarity,
    RegimeDirectional,
    StructureBreakKind,
    StructureOrigin,
    Timeframe,
)
from trading_agent.core.models import OHLCV
from trading_agent.core.time import bar_close_time
from trading_agent.strategy.primitives.models import StructureBreak
from trading_agent.strategy.setups.breakout_retest import (
    BreakoutState,
    detect_breakout_retest,
)

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


class _E:
    def __init__(self, **kw: object) -> None:
        self.__dict__.update(kw)


def _bar(t: datetime, o: float, h: float, low: float, c: float) -> OHLCV:
    return OHLCV(
        instrument="XAUUSD",
        timeframe=Timeframe.H4,
        open_time=t,
        close_time=bar_close_time(t, Timeframe.H4),
        open=o,
        high=h,
        low=low,
        close=c,
        volume=1.0,
        source="test",
    )


def _d1_ctx(bars: list[OHLCV], d1_dir: RegimeDirectional, bos: Direction | None) -> _E:
    """Minimaler D1-Kontext: ~30 Tagesbars bis kurz vor dem letzten H4-Close + optional ein
    jüngster D1-BOS (für die S9-HTF-Konfluenz)."""
    end = bars[-1].open_time.replace(hour=0, minute=0, second=0, microsecond=0)
    d1_bars = [
        OHLCV(
            instrument="XAUUSD", timeframe=Timeframe.D1,
            open_time=end - timedelta(days=k), close_time=end - timedelta(days=k - 1),
            open=100.0, high=101.0, low=99.0, close=100.0, volume=1.0, source="test",
        )
        for k in range(30, 0, -1)
    ]
    breaks: tuple[StructureBreak, ...] = ()
    if bos is not None:
        breaks = (
            StructureBreak(
                kind=StructureBreakKind.BOS,
                direction=Polarity.BULLISH if bos is Direction.LONG else Polarity.BEARISH,
                timeframe=Timeframe.D1,
                broken_level_price=100.0,
                break_bar_timestamp=d1_bars[-8].open_time,
                break_close=100.0,
                origin=StructureOrigin.TREND,
            ),
        )
    return _E(structure=_E(directional=d1_dir), bars=tuple(d1_bars), structure_breaks=breaks)


def _mtf(
    bars: list[OHLCV],
    d1_dir: RegimeDirectional,
    *,
    bos: Direction | None = "auto",  # type: ignore[assignment]
) -> _E:
    # Standard: BOS bestätigt den Trend (realer ARMED-Breakout-Retest hat i. d. R. einen jüngsten
    # D1-BOS in Trendrichtung). Tests, die die Konfluenz gezielt prüfen, setzen bos= explizit.
    if bos == "auto":
        bos = (
            Direction.LONG if d1_dir is RegimeDirectional.TREND_UP
            else Direction.SHORT if d1_dir is RegimeDirectional.TREND_DOWN
            else None
        )
    return _E(
        instrument="XAUUSD",
        information_cutoff=bars[-1].close_time,
        h4=_E(bars=tuple(bars)),
        d1=_d1_ctx(bars, d1_dir, bos),  # type: ignore[arg-type]
    )


def _long_breakout_retest_series() -> list[OHLCV]:
    """40 Bars Konsolidierung um 100 (±1), dann Ausbruch auf 104, dann Retest auf 101.5, hält."""
    bars: list[OHLCV] = []
    t = _T0
    # 45 Bars enge Konsolidierung 99..101 (ATR ~0.8)
    for k in range(45):
        base = 100.0 + (0.6 if k % 2 else -0.6)
        bars.append(_bar(t, base, base + 0.4, base - 0.4, base + (0.2 if k % 3 else -0.2)))
        t += timedelta(hours=4)
    # Ausbruch: Close 104 (weit über 101)
    bars.append(_bar(t, 100.8, 104.5, 100.6, 104.2))
    t += timedelta(hours=4)
    # 2 Bars weiter hoch
    for c in (105.0, 105.5):
        bars.append(_bar(t, c - 0.3, c + 0.4, c - 0.6, c))
        t += timedelta(hours=4)
    # Retest der Bruchkante (~101): Low 101.0 berührt, Close 102.4 hält darüber, Close oben
    bars.append(_bar(t, 103.5, 103.6, 101.0, 102.6))
    return bars


def test_long_armed_on_holding_retest_in_uptrend() -> None:
    bars = _long_breakout_retest_series()
    rep = detect_breakout_retest(_mtf(bars, RegimeDirectional.TREND_UP))  # type: ignore[arg-type]
    assert rep.state is BreakoutState.ARMED
    assert rep.is_armed and rep.direction is Direction.LONG
    assert rep.entry is not None and rep.sl is not None and rep.sl < rep.entry
    assert rep.tp1 is not None and rep.tp2 is not None and rep.tp1 < rep.tp2
    assert rep.rr_to_tp2 is not None and rep.rr_to_tp2 >= 2.0
    assert rep.tp3_ref and "Runner" in rep.tp3_ref
    assert rep.broken_level is not None


def test_no_arm_without_d1_trend() -> None:
    bars = _long_breakout_retest_series()
    rep = detect_breakout_retest(_mtf(bars, RegimeDirectional.UNCLEAR))  # type: ignore[arg-type]
    assert rep.state is BreakoutState.SCANNING
    assert NoTradeReason.HTF_TREND_MISALIGNED in rep.reasons


def test_no_arm_without_htf_bos_confluence() -> None:
    """S9: D1-Trend up, aber kein bestätigender D1-BOS → SCANNING (kein ARMED)."""
    bars = _long_breakout_retest_series()
    rep = detect_breakout_retest(_mtf(bars, RegimeDirectional.TREND_UP, bos=None))  # type: ignore[arg-type]
    assert rep.state is BreakoutState.SCANNING
    assert not rep.is_armed


def test_no_arm_when_bos_opposes_breakout() -> None:
    """D1-Trend up + Long-Breakout, aber jüngster D1-BOS ist bearish → keine Konfluenz."""
    bars = _long_breakout_retest_series()
    rep = detect_breakout_retest(
        _mtf(bars, RegimeDirectional.TREND_UP, bos=Direction.SHORT)  # type: ignore[arg-type]
    )
    assert not rep.is_armed


def test_no_arm_when_breakout_direction_opposes_trend() -> None:
    bars = _long_breakout_retest_series()  # long breakout
    rep = detect_breakout_retest(_mtf(bars, RegimeDirectional.TREND_DOWN))  # type: ignore[arg-type]
    # D1 down + long breakout → Trend-Filter verwirft den Ausbruch
    assert not rep.is_armed


def test_await_retest_when_no_retest_yet() -> None:
    bars = _long_breakout_retest_series()[:-1]  # ohne die Retest-Bar
    bars.append(_bar(bars[-1].open_time + timedelta(hours=4), 105.5, 106.0, 105.2, 105.8))
    rep = detect_breakout_retest(_mtf(bars, RegimeDirectional.TREND_UP))  # type: ignore[arg-type]
    assert rep.state in (BreakoutState.AWAIT_RETEST, BreakoutState.SCANNING)
    assert not rep.is_armed


def test_scanning_when_flat_no_breakout() -> None:
    bars: list[OHLCV] = []
    t = _T0
    for k in range(70):
        base = 100.0 + (0.5 if k % 2 else -0.5)
        bars.append(_bar(t, base, base + 0.3, base - 0.3, base))
        t += timedelta(hours=4)
    rep = detect_breakout_retest(_mtf(bars, RegimeDirectional.TREND_UP))  # type: ignore[arg-type]
    assert rep.state in (BreakoutState.SCANNING, BreakoutState.CONSOLIDATION)
    assert not rep.is_armed


def test_no_lookahead_only_uses_bars_up_to_cutoff() -> None:
    bars = _long_breakout_retest_series()
    rep_full = detect_breakout_retest(_mtf(bars, RegimeDirectional.TREND_UP))  # type: ignore[arg-type]
    # ein Bar weniger → der Retest-Bar fehlt → darf NICHT ARMED sein
    rep_prev = detect_breakout_retest(_mtf(bars[:-1], RegimeDirectional.TREND_UP))  # type: ignore[arg-type]
    assert rep_full.state is BreakoutState.ARMED
    assert rep_prev.state is not BreakoutState.ARMED


# ------------------------------------------------------------- Integration in evaluate_from_mtf


def test_evaluate_from_mtf_routes_to_breakout_when_smc_not_actionable(monkeypatch) -> None:
    from trading_agent.core.enums import DecisionType, SetupState
    from trading_agent.strategy import evaluate as ev
    from trading_agent.strategy.decision import Decision

    bars = _long_breakout_retest_series()
    fake_mtf = _mtf(bars, RegimeDirectional.TREND_UP)

    no_trade = _E(blocked=False, records=(), reasons=())
    smc_result = ev.EvaluationResult(
        decision=Decision.no_trade(
            "XAUUSD",
            fake_mtf.information_cutoff,  # type: ignore[attr-defined]
            (NoTradeReason.REGIME_UNCLEAR,),
            setup_state=SetupState.SCANNING,
        ),
        mtf=fake_mtf,  # type: ignore[arg-type]
        scan=_E(no_trade_reason=None),  # type: ignore[arg-type]
        no_trade=no_trade,  # type: ignore[arg-type]
    )
    monkeypatch.setattr(ev, "_evaluate_smc", lambda *a, **k: smc_result)

    result = ev.evaluate_from_mtf(fake_mtf)  # type: ignore[arg-type]
    assert result.decision.decision in (DecisionType.BUY, DecisionType.SELL)
    assert result.decision.setup_id == "SETUP-BREAKOUT-RETEST-01"
    assert result.breakout is not None and result.breakout.is_armed
    assert result.decision.entry is not None and result.decision.tp2 is not None

    # Governance: SETUP-BREAKOUT-RETEST-01 ist IN_VALIDATION → SHADOW, nicht LIVE
    from trading_agent.governance import ValidationRegistry, apply_live_gate

    gated = apply_live_gate(result, registry=ValidationRegistry.default())
    assert not gated.is_actionable_live
    assert gated.live_gate.eligibility.value == "shadow"  # type: ignore[union-attr]


def test_evaluate_from_mtf_keeps_smc_buy_when_actionable(monkeypatch) -> None:
    from trading_agent.core.enums import DecisionType, Direction, RiskTier, SetupState
    from trading_agent.strategy import evaluate as ev
    from trading_agent.strategy.decision import Decision

    bars = _long_breakout_retest_series()
    fake_mtf = _mtf(bars, RegimeDirectional.TREND_UP)
    smc_buy = ev.EvaluationResult(
        decision=Decision.trade(
            "XAUUSD",
            fake_mtf.information_cutoff,  # type: ignore[attr-defined]
            Direction.LONG,
            entry=100.0,
            sl=99.0,
            tp1=101.5,
            tp2=103.0,
            tier=RiskTier.A,
        ),
        mtf=fake_mtf,  # type: ignore[arg-type]
        scan=_E(no_trade_reason=None),  # type: ignore[arg-type]
        no_trade=_E(blocked=False, records=()),  # type: ignore[arg-type]
    )
    monkeypatch.setattr(ev, "_evaluate_smc", lambda *a, **k: smc_buy)
    result = ev.evaluate_from_mtf(fake_mtf)  # type: ignore[arg-type]
    assert result.decision.setup_id != "SETUP-BREAKOUT-RETEST-01"
    assert result.decision.decision is DecisionType.BUY
    _ = SetupState
