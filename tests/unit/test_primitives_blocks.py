"""Phase 3 — Golden-Tests: Order Block (§10).

Gegenkerze vor strukturbrechendem Displacement, Zonenwahl, UNMITIGATED-Kopplung über die
bestehende Mitigation-Logik, Look-ahead-Schutz, Long/Short-Symmetrie, stabile IDs.
"""

from __future__ import annotations

from datetime import timedelta

from trading_agent.core.enums import (
    OrderBlockZone,
    Polarity,
    StructureBreakKind,
    Timeframe,
    ZoneState,
)
from trading_agent.core.models import OHLCV
from trading_agent.core.time import bar_close_time, parse_timestamp
from trading_agent.strategy.primitives.blocks import ObParams, find_order_blocks, unmitigated
from trading_agent.strategy.primitives.imbalance import find_displacements, find_fvgs
from trading_agent.strategy.primitives.models import Displacement, StructureBreak
from trading_agent.strategy.primitives.structure import structure_breaks
from trading_agent.strategy.primitives.swings import detect_swings

START = parse_timestamp("2024-06-07T00:00:00Z")
TF = Timeframe.M5


def _bars(
    rows: list[tuple[float, float, float, float]], *, start: str = "2024-06-07T00:00:00Z"
) -> list[OHLCV]:
    out: list[OHLCV] = []
    t = parse_timestamp(start)
    for o, h, low, c in rows:
        out.append(
            OHLCV(
                instrument="BTCUSDT",
                timeframe=TF,
                open_time=t,
                close_time=bar_close_time(t, TF),
                open=o,
                high=h,
                low=low,
                close=c,
                volume=1.0,
                source="test",
            )
        )
        t += timedelta(seconds=TF.seconds)
    return out


def _mirror(
    rows: list[tuple[float, float, float, float]], pivot: float = 100.0
) -> list[tuple[float, float, float, float]]:
    return [(2 * pivot - o, 2 * pivot - low, 2 * pivot - h, 2 * pivot - c) for o, h, low, c in rows]


_WARMUP = [(100.0, 100.5, 99.5, 100.0)] * 16
# Bull-OB @ idx16 (Down-Close), Displacement 17..18 (bullisch), FVG bei idx18.
_BULL = [
    *_WARMUP,
    (103.0, 103.2, 100.8, 101.0),  # 16  OB (close < open)
    (101.0, 108.0, 100.8, 107.5),  # 17  Impuls
    (107.5, 109.0, 105.0, 108.0),  # 18  -> FVG [103.2, 105.0]
    (108.0, 108.5, 106.0, 107.0),  # 19
    (107.0, 108.0, 106.0, 107.5),  # 20  (Break-Bar in den Unit-Tests)
    (107.5, 108.2, 106.5, 107.0),  # 21
]


def _fake_break(bars: list[OHLCV], idx: int, direction: Polarity) -> StructureBreak:
    return StructureBreak(
        kind=StructureBreakKind.CHOCH,
        direction=direction,
        timeframe=TF,
        broken_level_price=bars[idx].close,
        break_bar_timestamp=bars[idx].open_time,
        break_close=bars[idx].close,
    )


def _fake_disp(bars: list[OHLCV], s: int, e: int, direction: Polarity) -> Displacement:
    return Displacement(
        direction=direction,
        timeframe=TF,
        start_bar=bars[s].open_time,
        end_bar=bars[e].open_time,
        bars=e - s + 1,
        net_move_atr=2.0,
        body_ratio=0.8,
        start_index=s,
        end_index=e,
    )


# ------------------------------------------------------------------- Unit: hand-made inputs


def test_bullish_ob_golden() -> None:
    bars = _bars(_BULL)
    disp = _fake_disp(bars, 17, 18, Polarity.BULLISH)
    brk = _fake_break(bars, 20, Polarity.BULLISH)  # innerhalb [17, 21]
    obs = find_order_blocks(bars, TF, [disp], [brk])
    assert len(obs) == 1
    ob = obs[0]
    assert ob.direction is Polarity.BULLISH
    assert ob.bar_index == 16
    assert (ob.zone_low, ob.zone_high) == (100.8, 103.2)  # full_range der OB-Kerze
    assert ob.ob_bar == bars[16].open_time
    assert ob.break_ref is brk and ob.displacement_ref is disp
    assert ob.state is ZoneState.UNMITIGATED and ob.fill_fraction == 0.0
    assert ob.zone_id == "OB-M5-bullish-16"


def test_ob_requires_opposite_color_candle() -> None:
    rows = list(_BULL)
    rows[16] = (101.0, 103.2, 100.8, 103.0)  # jetzt Up-Close
    bars = _bars(rows)
    disp = _fake_disp(bars, 17, 18, Polarity.BULLISH)
    assert find_order_blocks(bars, TF, [disp], [_fake_break(bars, 20, Polarity.BULLISH)]) == []


def test_ob_requires_structure_break_in_window() -> None:
    bars = _bars([*_BULL, *([(107.0, 107.5, 106.0, 106.5)] * 6)])
    disp = _fake_disp(bars, 17, 18, Polarity.BULLISH)
    # Break zu spät (idx 24 > 16 + 5)
    assert find_order_blocks(bars, TF, [disp], [_fake_break(bars, 24, Polarity.BULLISH)]) == []
    # Break in falscher Richtung
    assert find_order_blocks(bars, TF, [disp], [_fake_break(bars, 20, Polarity.BEARISH)]) == []
    # gar kein Break
    assert find_order_blocks(bars, TF, [disp], []) == []


def test_ob_zone_modes() -> None:
    bars = _bars(_BULL)
    disp = _fake_disp(bars, 17, 18, Polarity.BULLISH)
    brk = _fake_break(bars, 20, Polarity.BULLISH)
    full = find_order_blocks(
        bars, TF, [disp], [brk], params=ObParams(zone=OrderBlockZone.FULL_RANGE)
    )[0]
    body = find_order_blocks(bars, TF, [disp], [brk], params=ObParams(zone=OrderBlockZone.BODY))[0]
    o2e = find_order_blocks(
        bars, TF, [disp], [brk], params=ObParams(zone=OrderBlockZone.OPEN_TO_EXTREME)
    )[0]
    assert (full.zone_low, full.zone_high) == (100.8, 103.2)  # [low, high]
    assert (body.zone_low, body.zone_high) == (101.0, 103.0)  # [close, open]
    assert (o2e.zone_low, o2e.zone_high) == (100.8, 103.0)  # [low, open]


def test_ob_state_progression() -> None:
    # nach dem Break (idx 20) taucht der Preis in die Zone [100.8, 103.2] (H = 2.4)
    partial_rows = [*_BULL, (107.0, 107.5, 106.0, 106.5), (106.5, 106.7, 102.24, 103.0)]
    bars = _bars(partial_rows)  # min_low 102.24 -> (103.2 - 102.24)/2.4 = 0.4
    disp = _fake_disp(bars, 17, 18, Polarity.BULLISH)
    ob = find_order_blocks(bars, TF, [disp], [_fake_break(bars, 20, Polarity.BULLISH)])[0]
    assert ob.state is ZoneState.PARTIAL and abs(ob.fill_fraction - 0.4) < 1e-9

    mitig_rows = [*_BULL, (107.0, 107.5, 106.0, 106.5), (106.5, 106.7, 101.6, 102.0)]
    bars2 = _bars(mitig_rows)  # (103.2 - 101.6)/2.4 = 0.667
    ob2 = find_order_blocks(bars2, TF, [disp], [_fake_break(bars2, 20, Polarity.BULLISH)])[0]
    assert ob2.state is ZoneState.MITIGATED

    stale_rows = [*_BULL, *([(107.0, 107.5, 106.0, 106.5)] * 6)]
    bars3 = _bars(stale_rows)
    ob3 = find_order_blocks(
        bars3,
        TF,
        [disp],
        [_fake_break(bars3, 20, Polarity.BULLISH)],
        params=ObParams(max_age_bars=3),
    )[0]
    assert ob3.state is ZoneState.STALE


def test_ob_body_zone_of_doji_is_rejected() -> None:
    rows = list(_BULL)
    rows[16] = (102.0, 103.0, 101.0, 102.0)  # Doji: open == close -> weder bull noch bear OB
    bars = _bars(rows)
    disp = _fake_disp(bars, 17, 18, Polarity.BULLISH)
    assert find_order_blocks(bars, TF, [disp], [_fake_break(bars, 20, Polarity.BULLISH)]) == []


def test_ob_long_short_symmetry() -> None:
    bull = _bars(_BULL)
    bull_ob = find_order_blocks(
        bull,
        TF,
        [_fake_disp(bull, 17, 18, Polarity.BULLISH)],
        [_fake_break(bull, 20, Polarity.BULLISH)],
    )[0]
    bear = _bars(_mirror(_BULL))
    bear_ob = find_order_blocks(
        bear,
        TF,
        [_fake_disp(bear, 17, 18, Polarity.BEARISH)],
        [_fake_break(bear, 20, Polarity.BEARISH)],
    )[0]
    assert bear_ob.direction is Polarity.BEARISH
    assert bear_ob.bar_index == bull_ob.bar_index
    assert bear_ob.zone_low == 200.0 - bull_ob.zone_high
    assert bear_ob.zone_high == 200.0 - bull_ob.zone_low
    assert bear_ob.state is bull_ob.state


def test_ob_lookahead_immune() -> None:
    full = _bars([*_BULL, (108, 109, 107, 108.5), (108.5, 110, 108, 109.5)])
    disp = _fake_disp(full, 17, 18, Polarity.BULLISH)
    brk = _fake_break(full, 20, Polarity.BULLISH)
    early = find_order_blocks(
        full[:21], TF, [disp], [_fake_break(full[:21], 20, Polarity.BULLISH)]
    )[0]
    late = find_order_blocks(full, TF, [disp], [brk])[0]
    assert (early.zone_low, early.zone_high, early.ob_bar, early.bar_index) == (
        late.zone_low,
        late.zone_high,
        late.ob_bar,
        late.bar_index,
    )
    assert early.state is late.state is ZoneState.UNMITIGATED  # spätere Bars füllen die Zone nicht


def test_unmitigated_filter() -> None:
    bars = _bars([*_BULL, (107.0, 107.5, 106.0, 106.5), (106.5, 106.7, 101.6, 102.0)])
    disp = _fake_disp(bars, 17, 18, Polarity.BULLISH)
    obs = find_order_blocks(bars, TF, [disp], [_fake_break(bars, 20, Polarity.BULLISH)])
    assert obs and obs[0].state is ZoneState.MITIGATED
    assert unmitigated(obs) == []


# ------------------------------------------------------------------- Golden: volle Pipeline


def _pipeline_bars() -> list[OHLCV]:
    base = [
        210.0 - p
        for p in [
            100.0,
            101.2,
            102.4,
            103.6,
            104.8,
            105.0,
            104.0,
            103.0,
            102.0,
            101.0,
            102.2,
            103.4,
            104.6,
            105.8,
            107.0,
            105.5,
            104.0,
            103.0,
            104.5,
            106.0,
            108.0,
            110.0,
            109.0,
            108.0,
            106.5,
            105.0,
            106.0,
            107.5,
        ]
    ]  # gespiegelt -> Abwärtsstruktur (LH/LL), letztes SH bei 105.0
    rows = [*_WARMUP]
    rows += [(p, p, p, p) for p in base]  # flache Bars -> saubere Fraktale
    rows += [
        (102.5, 102.7, 101.5, 101.8),  # OB-Kerze (Down-Close), idx 44
        (
            101.8,
            108.0,
            101.5,
            107.5,
        ),  # bullischer Impuls, idx 45  -> Close > letztes SH (105) => CHoCH
        (107.5, 108.5, 106.0, 107.0),  # idx 46  -> FVG
    ]
    return _bars(rows)


def test_order_block_full_pipeline() -> None:
    bars = _pipeline_bars()
    swings = detect_swings(bars, TF)
    breaks = structure_breaks(bars, swings, TF)
    fvgs = find_fvgs(bars, TF, tick_size=0.1)
    disps = find_displacements(bars, TF, fvgs)

    assert any(
        b.kind is StructureBreakKind.CHOCH and b.direction is Polarity.BULLISH for b in breaks
    )
    assert len(disps) == 1 and disps[0].direction is Polarity.BULLISH and disps[0].start_index == 45

    obs = find_order_blocks(bars, TF, disps, breaks)
    assert len(obs) == 1
    ob = obs[0]
    assert ob.direction is Polarity.BULLISH
    assert ob.bar_index == 44
    assert (ob.zone_low, ob.zone_high) == (101.5, 102.7)
    assert ob.break_ref is not None and ob.break_ref.kind is StructureBreakKind.CHOCH
    assert ob.displacement_ref is disps[0]
    assert ob.state is ZoneState.UNMITIGATED
