"""Phase 3 — Fundament: Enums, MarketContext, Decision (strategy_version 0.1.1)."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from trading_agent.core.enums import (
    Bias,
    DecisionType,
    Direction,
    DisplayAlias,
    MarketSide,
    NoTradeReason,
    Polarity,
    RiskTier,
    SetupState,
    Timeframe,
    VetoId,
)
from trading_agent.core.models import OHLCV
from trading_agent.core.time import parse_timestamp
from trading_agent.core.types import MarketContext, OpenPositionInfo, PortfolioContext
from trading_agent.strategy.decision import Decision

CUTOFF = parse_timestamp("2024-06-02T00:00:00Z")


# ---------------------------------------------------------------------------- enums


def test_direction_helpers() -> None:
    assert Direction.LONG.opposite is Direction.SHORT
    assert Direction.LONG.side.value == "buy"
    assert Direction.SHORT.side.value == "sell"
    assert Direction.LONG.sign == 1 and Direction.SHORT.sign == -1


def test_polarity_and_bias() -> None:
    assert Polarity.of(Direction.LONG) is Polarity.BULLISH
    assert Polarity.BULLISH.opposite is Polarity.BEARISH
    assert Bias.LONG.as_direction() is Direction.LONG
    assert Bias.NONE.as_direction() is None


def test_marketside_against_direction() -> None:
    # Bias long -> das Setup sweept die SELL_SIDE (Liquidität gegen D)
    assert MarketSide.against(Direction.LONG) is MarketSide.SELL_SIDE
    assert MarketSide.against(Direction.SHORT) is MarketSide.BUY_SIDE


def test_setupstate_is_forming() -> None:
    forming = {
        SetupState.BIAS_SET,
        SetupState.LIQUIDITY_IDENTIFIED,
        SetupState.SWEPT,
        SetupState.RECLAIMED,
        SetupState.DISPLACED,
        SetupState.STRUCTURE_SHIFTED,
    }
    for s in SetupState:
        assert s.is_forming is (s in forming)


def test_display_alias_mapping() -> None:
    assert DisplayAlias.of(SetupState.SCANNING) is DisplayAlias.WATCH
    assert DisplayAlias.of(SetupState.STRUCTURE_SHIFTED) is DisplayAlias.DEVELOPING
    assert DisplayAlias.of(SetupState.ARMED) is DisplayAlias.ARMED
    assert DisplayAlias.of(SetupState.TRIGGERED) is DisplayAlias.CONFIRMED
    # jeder State ist abgedeckt
    for s in SetupState:
        assert isinstance(DisplayAlias.of(s), DisplayAlias)


def test_decisiontype_entry() -> None:
    assert DecisionType.entry(Direction.LONG) is DecisionType.BUY
    assert DecisionType.entry(Direction.SHORT) is DecisionType.SELL


def test_notradereason_is_append_only_stable_values() -> None:
    # Werte sind stabile Strings (kein auto()); Stichprobe.
    assert NoTradeReason.SWEEP_BECAME_BREAKOUT.value == "sweep_became_breakout"
    assert NoTradeReason.REGIME_UNCLEAR.value == "regime_unclear"
    assert len({r.value for r in NoTradeReason}) == len(list(NoTradeReason))


# ---------------------------------------------------------------------------- MarketContext


def _ctx(series: dict[Timeframe, list[OHLCV]], **kw: object) -> MarketContext:
    return MarketContext(
        instrument="BTCUSDT",
        base_timeframe=Timeframe.M5,
        information_cutoff=CUTOFF,
        series={tf: tuple(bars) for tf, bars in series.items()},
        **kw,  # type: ignore[arg-type]
    )


def test_marketcontext_valid(make_series: Callable[..., list[OHLCV]]) -> None:
    m5 = make_series(24, start="2024-06-01T22:00:00Z", timeframe=Timeframe.M5)
    ctx = _ctx({Timeframe.M5: m5})
    assert ctx.price == m5[-1].close
    assert ctx.last(Timeframe.M5) is m5[-1]
    assert ctx.bars(Timeframe.M15) == ()
    assert ctx.has(Timeframe.M5) and not ctx.has(Timeframe.H4)
    assert ctx.timeframes == (Timeframe.M5,)


def test_marketcontext_rejects_lookahead_bar(make_series: Callable[..., list[OHLCV]]) -> None:
    # eine Bar, die NACH dem cutoff schließt
    m5 = make_series(5, start="2024-06-02T00:00:00Z", timeframe=Timeframe.M5)
    with pytest.raises(ValueError, match="Look-ahead"):
        _ctx({Timeframe.M5: m5})


def test_marketcontext_requires_base_timeframe(make_series: Callable[..., list[OHLCV]]) -> None:
    m15 = make_series(5, start="2024-06-01T20:00:00Z", timeframe=Timeframe.M15)
    with pytest.raises(ValueError, match="base_timeframe"):
        _ctx({Timeframe.M15: m15})


def test_marketcontext_rejects_unsorted(make_series: Callable[..., list[OHLCV]]) -> None:
    m5 = make_series(5, start="2024-06-01T22:00:00Z", timeframe=Timeframe.M5)
    swapped = [m5[0], m5[2], m5[1], m5[3], m5[4]]
    with pytest.raises(ValueError, match="aufsteigend"):
        _ctx({Timeframe.M5: swapped})


# ---------------------------------------------------------------------------- Decision


def test_decision_no_trade_requires_reason() -> None:
    with pytest.raises(ValueError, match="mindestens einen reason_code"):
        Decision(
            decision=DecisionType.NO_TRADE,
            instrument="BTCUSDT",
            information_cutoff=CUTOFF,
            setup_state=SetupState.SCANNING,
        )
    d = Decision.no_trade("BTCUSDT", CUTOFF, [NoTradeReason.REGIME_UNCLEAR])
    assert d.decision is DecisionType.NO_TRADE
    assert d.reason_codes == (NoTradeReason.REGIME_UNCLEAR,)
    assert not d.is_actionable


def test_decision_no_trade_dedupes_reasons() -> None:
    d = Decision.no_trade(
        "BTCUSDT",
        CUTOFF,
        [NoTradeReason.NO_RECLAIM, NoTradeReason.NO_RECLAIM],
        vetoes=[VetoId.V5, VetoId.V5],
    )
    assert d.reason_codes == (NoTradeReason.NO_RECLAIM,)
    assert d.vetoes == (VetoId.V5,)


def test_decision_wait_only_from_forming_state() -> None:
    d = Decision.wait(
        "BTCUSDT",
        CUTOFF,
        SetupState.SWEPT,
        direction=Direction.LONG,
        chain_progress="SWEPT — warte auf Reclaim",
    )
    assert d.decision is DecisionType.WAIT
    assert d.display_alias is DisplayAlias.DEVELOPING
    with pytest.raises(ValueError, match="Forming-State"):
        Decision.wait("BTCUSDT", CUTOFF, SetupState.ARMED)


def test_decision_wait_has_no_reasons() -> None:
    with pytest.raises(ValueError, match="keine reason_codes"):
        Decision(
            decision=DecisionType.WAIT,
            instrument="BTCUSDT",
            information_cutoff=CUTOFF,
            setup_state=SetupState.SWEPT,
            reason_codes=(NoTradeReason.NO_RECLAIM,),
        )


def test_decision_entry_factory() -> None:
    d = Decision.trade(
        "BTCUSDT",
        CUTOFF,
        Direction.LONG,
        entry=100.0,
        sl=98.0,
        tp1=103.0,
        tp2=106.0,
        tier=RiskTier.A,
        rr_to_tp2=3.0,
    )
    assert d.decision is DecisionType.BUY
    assert d.is_actionable
    assert d.setup_state is SetupState.ARMED
    assert d.r_distance == 2.0
    assert d.display_alias is DisplayAlias.ARMED


def test_decision_entry_rejects_missing_tp2() -> None:
    with pytest.raises(ValueError, match="tp2"):
        Decision(
            decision=DecisionType.BUY,
            instrument="BTCUSDT",
            information_cutoff=CUTOFF,
            setup_state=SetupState.ARMED,
            direction=Direction.LONG,
            entry=100.0,
            sl=98.0,
            tp1=103.0,
            tier=RiskTier.A,
        )


def test_decision_entry_rejects_direction_mismatch() -> None:
    with pytest.raises(ValueError, match="passt nicht zu direction"):
        Decision(
            decision=DecisionType.BUY,
            instrument="BTCUSDT",
            information_cutoff=CUTOFF,
            setup_state=SetupState.ARMED,
            direction=Direction.SHORT,
            entry=100.0,
            sl=102.0,
            tp1=97.0,
            tp2=94.0,
            tier=RiskTier.B,
        )


def test_portfolio_context_helpers() -> None:
    pc = PortfolioContext(
        open_positions=(OpenPositionInfo("BTCUSDT", Direction.LONG, 0.5, "crypto_beta"),),
        static_correlations={("BTCUSDT", "ETHUSDT"): 0.80},
    )
    assert pc.open_direction("btcusdt") is Direction.LONG
    assert pc.open_direction("ETHUSDT") is None
    assert pc.correlation("ETHUSDT", "BTCUSDT") == 0.80
    assert pc.correlation("BTCUSDT", "BTCUSDT") == 1.0
