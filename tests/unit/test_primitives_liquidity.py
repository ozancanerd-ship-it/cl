"""Phase 3 — Golden-Tests: Liquidity Level (§4), Equal High/Low (§5), Liquidity Sweep (§6).

Level != Sweep. Look-ahead-Immunität, Breakout-Abgrenzung und Long/Short-Symmetrie verankert.
"""

from __future__ import annotations

from datetime import timedelta

from trading_agent.core.enums import (
    LiquidityState,
    LiquidityType,
    MarketSide,
    Timeframe,
)
from trading_agent.core.models import OHLCV
from trading_agent.core.time import bar_close_time, parse_timestamp
from trading_agent.strategy.primitives.liquidity import (
    SweepParams,
    classify_level_state,
    equal_level_clusters,
    previous_period_levels,
    resolve_sweep,
    score_level,
    swing_levels,
)
from trading_agent.strategy.primitives.models import LiquidityLevel
from trading_agent.strategy.primitives.swings import detect_swings

START = parse_timestamp("2024-06-03T00:00:00Z")


def _flat_bars(
    prices: list[float], tf: Timeframe = Timeframe.M5, start: object = START
) -> list[OHLCV]:
    out: list[OHLCV] = []
    t = parse_timestamp(start) if isinstance(start, str) else START
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


def _ohlc_bars(
    rows: list[tuple[float, float, float, float]],
    *,
    tf: Timeframe = Timeframe.M15,
    start: str = "2024-06-03T00:00:00Z",
) -> list[OHLCV]:
    out: list[OHLCV] = []
    t = parse_timestamp(start)
    for o, h, low, c in rows:
        out.append(
            OHLCV(
                instrument="BTCUSDT",
                timeframe=tf,
                open_time=t,
                close_time=bar_close_time(t, tf),
                open=o,
                high=h,
                low=low,
                close=c,
                volume=1.0,
                source="test",
            )
        )
        t += timedelta(seconds=tf.seconds)
    return out


def _mirror_rows(
    rows: list[tuple[float, float, float, float]], pivot: float = 100.0
) -> list[tuple[float, float, float, float]]:
    # OHLC spiegeln: high<->low tauschen die Rolle
    return [(2 * pivot - o, 2 * pivot - low, 2 * pivot - h, 2 * pivot - c) for o, h, low, c in rows]


# ---- Doppel-Top-Preisfolge (M5): Warmup (Ties -> keine Swings) + zwei Swing-Highs bei 108,
#      dazwischen ein tiefer Swing-Low. Warmup füllt die ATR-Historie (period=14).
DOUBLE_TOP = [
    *([100.0, 99.0] * 5),  # 0-9   Warmup: strikte Ties -> kein Swing
    100.0,
    102.0,
    104.0,
    106.0,
    108.0,  # 10-14  SH@14 = 108
    106.0,
    104.0,
    102.0,
    100.0,  # 15-18  SL@18 = 100
    102.0,
    104.0,
    106.0,
    108.0,  # 19-22  SH@22 = 108
    106.0,
    104.0,  # 23-24
]
_SECOND_HIGH_IDX = 22


# ============================================================================ §5 Equal H/L


def test_equal_highs_cluster_golden() -> None:
    sw = detect_swings(_flat_bars(DOUBLE_TOP), Timeframe.M5)
    levels = equal_level_clusters(sw, Timeframe.M5, atr=2.0, tick_size=0.1)
    eq_highs = [lvl for lvl in levels if lvl.type is LiquidityType.EQUAL_HIGHS]
    assert len(eq_highs) == 1
    lvl = eq_highs[0]
    assert lvl.side is MarketSide.BUY_SIDE
    assert lvl.price == 108.0
    assert len(lvl.members) == 2
    assert lvl.is_equal_cluster
    assert lvl.state is LiquidityState.UNSWEPT


def test_equal_highs_rejected_when_prices_too_far() -> None:
    prices = list(DOUBLE_TOP)
    prices[_SECOND_HIGH_IDX] = 112.0  # zweites Hoch weit weg
    sw = detect_swings(_flat_bars(prices), Timeframe.M5)
    levels = equal_level_clusters(sw, Timeframe.M5, atr=2.0, tick_size=0.1)
    assert not [lvl for lvl in levels if lvl.type is LiquidityType.EQUAL_HIGHS]


def test_equal_highs_rejected_when_intervening_swing_too_shallow() -> None:
    sw = detect_swings(_flat_bars(DOUBLE_TOP), Timeframe.M5)
    levels = equal_level_clusters(
        sw, Timeframe.M5, atr=2.0, tick_size=0.1, min_intervening_depth_atr=100.0
    )
    assert not [lvl for lvl in levels if lvl.type is LiquidityType.EQUAL_HIGHS]


def test_equal_lows_symmetry() -> None:
    up = detect_swings(_flat_bars(DOUBLE_TOP), Timeframe.M5)
    down = detect_swings(_flat_bars([200.0 - p for p in DOUBLE_TOP]), Timeframe.M5)
    up_lvls = equal_level_clusters(up, Timeframe.M5, atr=2.0, tick_size=0.1)
    down_lvls = equal_level_clusters(down, Timeframe.M5, atr=2.0, tick_size=0.1)
    assert [lvl.type for lvl in up_lvls] == [LiquidityType.EQUAL_HIGHS]
    assert [lvl.type for lvl in down_lvls] == [LiquidityType.EQUAL_LOWS]
    assert down_lvls[0].side is MarketSide.SELL_SIDE
    assert down_lvls[0].price == 200.0 - up_lvls[0].price


# ============================================================================ §4.1 Quellen


def test_swing_levels() -> None:
    sw = detect_swings(_flat_bars(DOUBLE_TOP), Timeframe.H1)
    levels = swing_levels(sw, Timeframe.H1)
    assert len(levels) == len(sw)
    high_lvl = next(lvl for lvl in levels if lvl.type is LiquidityType.SWING_HIGH)
    assert high_lvl.side is MarketSide.BUY_SIDE
    assert high_lvl.formed_at == sw[0].confirmed_at


def test_previous_day_levels() -> None:
    d1 = _ohlc_bars(
        [(100, 110, 95, 105), (105, 115, 100, 112), (112, 120, 108, 118)],
        tf=Timeframe.D1,
        start="2024-06-01T00:00:00Z",
    )
    levels = previous_period_levels(d1, kind="day")
    pdh = next(lvl for lvl in levels if lvl.type is LiquidityType.PDH)
    pdl = next(lvl for lvl in levels if lvl.type is LiquidityType.PDL)
    assert pdh.price == 120.0 and pdh.side is MarketSide.BUY_SIDE
    assert pdl.price == 108.0 and pdl.side is MarketSide.SELL_SIDE
    assert pdh.timeframe is Timeframe.D1
    assert previous_period_levels([], kind="day") == []


# ============================================================================ §4.2 Stärke


def test_strength_counts_touches_without_close_break() -> None:
    lvl = LiquidityLevel(
        type=LiquidityType.SWING_HIGH,
        side=MarketSide.BUY_SIDE,
        price=108.0,
        timeframe=Timeframe.M5,
        formed_at=START,
    )
    obs = _ohlc_bars(
        [(107.0, 108.1, 106.5, 107.0)] * 5 + [(107.0, 109.5, 106.5, 109.4)],
        tf=Timeframe.M5,
        start="2024-06-03T01:00:00Z",
    )
    scored = score_level(lvl, obs, atr=2.0)  # eps = 0.2
    assert scored.touch_count == 5  # die letzte Bar bricht per close -> keine Berührung
    assert scored.strength > 0.30 * min(5 / 4, 1.0) - 1e-9


def test_strength_higher_for_equal_and_session_levels() -> None:
    plain = LiquidityLevel(
        type=LiquidityType.SWING_LOW,
        side=MarketSide.SELL_SIDE,
        price=90.0,
        timeframe=Timeframe.M15,
        formed_at=START,
    )
    pdl = LiquidityLevel(
        type=LiquidityType.PDL,
        side=MarketSide.SELL_SIDE,
        price=90.0,
        timeframe=Timeframe.D1,
        formed_at=START,
    )
    obs = _ohlc_bars([(95, 96, 94, 95)] * 3, tf=Timeframe.M15, start="2024-06-03T02:00:00Z")
    assert score_level(pdl, obs, atr=2.0).strength > score_level(plain, obs, atr=2.0).strength


# ============================================================================ §6 Sweep


def _buy_side_level() -> LiquidityLevel:
    return LiquidityLevel(
        type=LiquidityType.EQUAL_HIGHS,
        side=MarketSide.BUY_SIDE,
        price=108.0,
        timeframe=Timeframe.M15,
        formed_at=parse_timestamp("2024-06-02T23:00:00Z"),
    )


_WARMUP = [(106.0, 106.6, 105.4, 106.0)] * 16


def test_sweep_penetration_and_reclaim_golden() -> None:
    rows = [*_WARMUP, (107.0, 108.5, 106.8, 107.3)]  # idx16: durchsticht 108, schließt zurück
    bars = _ohlc_bars(rows)
    sweep = resolve_sweep(_buy_side_level(), bars)
    assert sweep is not None
    assert sweep.side is MarketSide.BUY_SIDE
    assert sweep.bars_to_reclaim == 0
    assert sweep.penetration_extreme == 108.5
    assert 0.30 < sweep.penetration_depth_atr < 0.45  # 0.5 / ATR(~1.29)
    assert sweep.wick_ratio >= 1.5

    state, swept_at, s2 = classify_level_state(_buy_side_level(), bars)
    assert state is LiquidityState.SWEPT and s2 is not None and swept_at == s2.reclaim_bar


def test_breakout_is_broken_not_swept() -> None:
    rows = [
        *_WARMUP,
        (107.0, 110.0, 106.9, 109.5),
        (109.5, 110.0, 109.0, 109.6),
        (109.6, 110.0, 109.2, 109.7),
        (109.7, 110.0, 109.3, 109.8),
    ]
    bars = _ohlc_bars(rows)
    assert resolve_sweep(_buy_side_level(), bars) is None  # pen > max_penetration_atr
    state, _, sweep = classify_level_state(_buy_side_level(), bars)
    assert state is LiquidityState.BROKEN and sweep is None


def test_shallow_reclaim_is_not_a_sweep() -> None:
    # Docht sticht durch, Close kommt zwar unter das Level, aber NICHT tief genug (min_reclaim_atr).
    rows = [*_WARMUP, (107.0, 108.5, 106.9, 107.9)]
    bars = _ohlc_bars(rows)
    assert resolve_sweep(_buy_side_level(), bars) is None
    assert classify_level_state(_buy_side_level(), bars)[0] is LiquidityState.UNSWEPT


def test_wick_requirement_gates_the_sweep() -> None:
    rows = [*_WARMUP, (107.9, 108.7, 106.9, 107.0)]  # großer Body, kleiner Docht
    bars = _ohlc_bars(rows)
    assert resolve_sweep(_buy_side_level(), bars) is None
    got = resolve_sweep(_buy_side_level(), bars, SweepParams(require_wick=False))
    assert got is not None


def test_sweep_before_level_formed_is_ignored() -> None:
    late = LiquidityLevel(
        type=LiquidityType.EQUAL_HIGHS,
        side=MarketSide.BUY_SIDE,
        price=108.0,
        timeframe=Timeframe.M15,
        formed_at=parse_timestamp("2024-06-04T00:00:00Z"),
    )
    rows = [*_WARMUP, (107.0, 108.5, 106.8, 107.3)]
    assert resolve_sweep(late, _ohlc_bars(rows)) is None


def test_sweep_lookahead_immune() -> None:
    rows = [
        *_WARMUP,
        (107.0, 108.5, 106.8, 107.3),
        (107.3, 107.8, 106.0, 106.5),
        (106.5, 107.0, 105.0, 105.5),
    ]
    bars = _ohlc_bars(rows)
    early = resolve_sweep(_buy_side_level(), bars[:17])  # nur bis zur Penetrations-/Reclaim-Bar
    late = resolve_sweep(_buy_side_level(), bars)
    assert early is not None and late is not None
    assert (early.penetration_bar, early.reclaim_bar, early.bars_to_reclaim) == (
        late.penetration_bar,
        late.reclaim_bar,
        late.bars_to_reclaim,
    )
    assert resolve_sweep(_buy_side_level(), bars[:16]) is None  # Penetrationsbar fehlt noch


def test_sweep_long_short_symmetry() -> None:
    buy_rows = [*_WARMUP, (107.0, 108.5, 106.8, 107.3)]
    sell_rows = _mirror_rows(buy_rows)
    sell_level = LiquidityLevel(
        type=LiquidityType.EQUAL_LOWS,
        side=MarketSide.SELL_SIDE,
        price=92.0,
        timeframe=Timeframe.M15,
        formed_at=parse_timestamp("2024-06-02T23:00:00Z"),
    )
    buy = resolve_sweep(_buy_side_level(), _ohlc_bars(buy_rows))
    sell = resolve_sweep(sell_level, _ohlc_bars(sell_rows))
    assert buy is not None and sell is not None
    assert sell.side is MarketSide.SELL_SIDE
    assert sell.penetration_extreme == 200.0 - buy.penetration_extreme
    assert round(sell.penetration_depth_atr, 6) == round(buy.penetration_depth_atr, 6)
    assert sell.bars_to_reclaim == buy.bars_to_reclaim
