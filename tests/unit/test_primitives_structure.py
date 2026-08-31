"""Phase 3 — Primitive-Golden-Tests: ATR, Swings, Struktur, BOS/CHoCH (primitives.md §0-§3).

Determinismus, Look-ahead-Immunität und Long/Short-Symmetrie sind hier verankert.
"""

from __future__ import annotations

from datetime import timedelta

from trading_agent.core.enums import (
    Polarity,
    RegimeDirectional,
    StructureBreakKind,
    SwingLabel,
    SwingType,
    Timeframe,
)
from trading_agent.core.models import OHLCV
from trading_agent.core.time import bar_close_time, parse_timestamp
from trading_agent.strategy.primitives.atr import atr, atr_series
from trading_agent.strategy.primitives.structure import derive_structure_state, structure_breaks
from trading_agent.strategy.primitives.swings import detect_swings

START = parse_timestamp("2024-06-01T00:00:00Z")

# Dreieckswelle: 3 klar getrennte Swing-Hochs (105, 107, 110) und -Tiefs (101, 103, 105),
# durchgehende Aufwärtsstruktur. Index 28 wird je Test überschrieben.
BASE_PRICES = [
    100.0,
    101.2,
    102.4,
    103.6,
    104.8,
    105.0,  # 0-5   SH@5 = 105.0
    104.0,
    103.0,
    102.0,
    101.0,  # 6-9   SL@9 = 101.0
    102.2,
    103.4,
    104.6,
    105.8,
    107.0,  # 10-14 SH@14 = 107.0 (HH)
    105.5,
    104.0,
    103.0,  # 15-17 SL@17 = 103.0 (HL)
    104.5,
    106.0,
    108.0,
    110.0,  # 18-21 SH@21 = 110.0 (HH)
    109.0,
    108.0,  # 22-23
    106.5,
    105.0,  # 24-25 SL@25 = 105.0 (HL)
    106.0,
    107.5,  # 26-27
]


def _bars(prices: list[float], tf: Timeframe = Timeframe.M5) -> list[OHLCV]:
    out: list[OHLCV] = []
    t = START
    for p in prices:
        out.append(
            OHLCV(
                instrument="BTCUSDT",
                timeframe=tf,
                open_time=t,
                close_time=bar_close_time(t, tf),
                open=p,
                high=p,
                low=p,
                close=p,
                volume=1.0,
                source="test",
            )
        )
        t += timedelta(seconds=tf.seconds)
    return out


def _mirror(prices: list[float], pivot: float = 105.0) -> list[float]:
    return [2 * pivot - p for p in prices]


# --------------------------------------------------------------------------- ATR


def test_atr_constant_range_equals_range() -> None:
    # Bars mit konstantem True Range von 2.0: high/low fest, close alterniert nicht.
    bars: list[OHLCV] = []
    t = START
    for _ in range(40):
        base = 100.0
        bars.append(
            OHLCV(
                instrument="BTCUSDT",
                timeframe=Timeframe.M5,
                open_time=t,
                close_time=bar_close_time(t, Timeframe.M5),
                open=base,
                high=base + 1.0,
                low=base - 1.0,
                close=base,
                volume=1.0,
                source="test",
            )
        )
        t += timedelta(seconds=300)
    assert atr(bars, 14) == 2.0  # TR == 2.0 überall -> Wilder-ATR == 2.0


def test_atr_series_is_causal() -> None:
    bars = _bars(BASE_PRICES)
    full = atr_series(bars, 14)
    prefix = atr_series(bars[:20], 14)
    for i in range(20):
        assert full[i] == prefix[i]  # ATR[i] hängt nur von Bars <= i ab


# --------------------------------------------------------------------------- Swings


def test_detect_swings_golden() -> None:
    bars = _bars(BASE_PRICES)
    sw = detect_swings(bars, Timeframe.M5)
    got = [(s.type, s.bar_index, s.price, s.label) for s in sw]
    assert got == [
        (SwingType.SWING_HIGH, 5, 105.0, None),
        (SwingType.SWING_LOW, 9, 101.0, None),
        (SwingType.SWING_HIGH, 14, 107.0, SwingLabel.HH),
        (SwingType.SWING_LOW, 17, 103.0, SwingLabel.HL),
        (SwingType.SWING_HIGH, 21, 110.0, SwingLabel.HH),
        (SwingType.SWING_LOW, 25, 105.0, SwingLabel.HL),
    ]
    # confirmed_at = close_time der Bar i+R (R=2)
    assert sw[0].confirmed_at == bars[7].close_time
    assert sw[4].confirmed_at == bars[23].close_time


def test_swing_not_visible_until_confirmed() -> None:
    bars = _bars(BASE_PRICES)
    # SL@25 wird erst durch Bar 27 bestätigt (R=2). Ohne Bar 27 fehlt sie.
    sw = detect_swings(bars[:27], Timeframe.M5)
    assert all(not (s.type is SwingType.SWING_LOW and s.bar_index == 25) for s in sw)
    assert any(s.bar_index == 21 for s in sw)  # SH@21 (conf. Bar 23) ist da


def test_swings_lookahead_immune() -> None:
    bars = _bars(BASE_PRICES)
    early = detect_swings(bars[:24], Timeframe.M5)
    late = detect_swings(bars, Timeframe.M5)
    # die zum Bar-24-Zeitpunkt bestätigten Swings dürfen sich durch spätere Bars NICHT ändern
    assert [
        (s.type, s.bar_index, s.price, s.confirmed_at, s.label, round(s.leg_size_atr, 9))
        for s in early
    ] == [
        (s.type, s.bar_index, s.price, s.confirmed_at, s.label, round(s.leg_size_atr, 9))
        for s in late[: len(early)]
    ]


def test_swings_long_short_symmetry() -> None:
    up = detect_swings(_bars(BASE_PRICES), Timeframe.M5)
    down = detect_swings(_bars(_mirror(BASE_PRICES)), Timeframe.M5)
    assert len(up) == len(down)
    for a, b in zip(up, down, strict=True):
        assert a.bar_index == b.bar_index
        assert b.type is (
            SwingType.SWING_LOW if a.type is SwingType.SWING_HIGH else SwingType.SWING_HIGH
        )
        assert b.price == 210.0 - a.price
        if a.label in (SwingLabel.HH, SwingLabel.HL):
            assert b.label in (SwingLabel.LH, SwingLabel.LL)


# --------------------------------------------------------------------------- Structure state


def test_structure_state_uptrend() -> None:
    sw = detect_swings(_bars(BASE_PRICES), Timeframe.M5)
    st = derive_structure_state(sw, Timeframe.M5)
    assert st.directional is RegimeDirectional.TREND_UP
    assert st.is_uptrend and not st.is_downtrend


def test_structure_state_choppy_is_unclear() -> None:
    choppy = [100.0, 101.0, 100.0, 101.0, 100.0, 101.0, 100.0, 101.0, 100.0, 101.0] * 3
    sw = detect_swings(_bars(choppy), Timeframe.M5)
    st = derive_structure_state(sw, Timeframe.M5)
    assert st.directional is RegimeDirectional.UNCLEAR


# --------------------------------------------------------------------------- BOS / CHoCH


def test_bullish_bos_on_break_of_leg_start_high() -> None:
    prices = [*BASE_PRICES, 111.0]  # Index 28: schließt über SH@21 (110.0)
    bars = _bars(prices)
    sw = detect_swings(bars, Timeframe.M5)
    breaks = structure_breaks(bars, sw, Timeframe.M5)
    assert breaks
    last = breaks[-1]
    assert last.kind is StructureBreakKind.BOS
    assert last.direction is Polarity.BULLISH
    assert last.broken_level_price == 110.0
    assert last.break_bar_timestamp == bars[28].open_time


def test_bearish_choch_on_break_of_last_hl() -> None:
    prices = [*BASE_PRICES, 104.0]  # Index 28: schließt unter letztem HL (SL@25 = 105.0)
    bars = _bars(prices)
    sw = detect_swings(bars, Timeframe.M5)
    breaks = structure_breaks(bars, sw, Timeframe.M5)
    assert breaks
    last = breaks[-1]
    assert last.kind is StructureBreakKind.CHOCH
    assert last.direction is Polarity.BEARISH
    assert last.prior_state is RegimeDirectional.TREND_UP
    assert last.broken_level_price == 105.0


def test_structure_breaks_lookahead_immune() -> None:
    prices = [*BASE_PRICES, 111.0, 112.0, 113.0]
    bars = _bars(prices)
    sw_full = detect_swings(bars, Timeframe.M5)
    early = structure_breaks(bars[:29], detect_swings(bars[:29], Timeframe.M5), Timeframe.M5)
    late = structure_breaks(bars, sw_full, Timeframe.M5)
    assert [(b.kind, b.direction, b.broken_level_price, b.break_bar_timestamp) for b in early] == [
        (b.kind, b.direction, b.broken_level_price, b.break_bar_timestamp)
        for b in late[: len(early)]
    ]


def test_choch_long_short_symmetry() -> None:
    up_prices = [*BASE_PRICES, 104.0]  # bearish CHoCH
    down_prices = _mirror(up_prices)  # -> bullish CHoCH
    up = structure_breaks(
        _bars(up_prices), detect_swings(_bars(up_prices), Timeframe.M5), Timeframe.M5
    )
    down = structure_breaks(
        _bars(down_prices), detect_swings(_bars(down_prices), Timeframe.M5), Timeframe.M5
    )
    assert up[-1].kind is down[-1].kind is StructureBreakKind.CHOCH
    assert up[-1].direction is Polarity.BEARISH
    assert down[-1].direction is Polarity.BULLISH
