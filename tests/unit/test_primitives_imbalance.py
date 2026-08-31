"""Phase 3 — Golden-Tests: Displacement (§7), FVG (§8), IFVG (§9), Mitigation (§11).

Look-ahead-Immunität (Zone/created_bar/flip ändern sich nicht durch spätere Bars),
Breakout/Imbalance-Abgrenzung, Long/Short-Symmetrie.
"""

from __future__ import annotations

from datetime import timedelta

from trading_agent.core.enums import Polarity, Timeframe, ZoneState
from trading_agent.core.models import OHLCV
from trading_agent.core.time import bar_close_time, parse_timestamp
from trading_agent.strategy.primitives.imbalance import (
    DisplacementParams,
    FvgParams,
    IfvgParams,
    analyze_imbalance,
    find_displacements,
    find_fvgs,
    find_ifvgs,
    mitigation_fill,
    zone_state,
)

START = parse_timestamp("2024-06-05T00:00:00Z")


def _bars(
    rows: list[tuple[float, float, float, float]],
    *,
    tf: Timeframe = Timeframe.M5,
    start: str = "2024-06-05T00:00:00Z",
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


def _mirror(
    rows: list[tuple[float, float, float, float]], pivot: float = 100.0
) -> list[tuple[float, float, float, float]]:
    return [(2 * pivot - o, 2 * pivot - low, 2 * pivot - h, 2 * pivot - c) for o, h, low, c in rows]


_WARMUP = [(100.0, 100.6, 99.4, 100.0)] * 16

# Bullische FVG (Zone [100.5, 105.0]) aus einem 2-Bar-Displacement.
_BULL = [
    *_WARMUP,
    (100.0, 100.5, 99.5, 100.0),  # 16  b1
    (100.0, 108.0, 99.8, 107.5),  # 17  Impuls
    (107.5, 109.0, 105.0, 108.0),  # 18  b3 -> FVG [100.5, 105.0]
]


# ------------------------------------------------------------------------- §8 FVG


def test_bullish_fvg_golden() -> None:
    fvgs = find_fvgs(_bars(_BULL), Timeframe.M5, tick_size=0.1)
    assert len(fvgs) == 1
    f = fvgs[0]
    assert f.direction is Polarity.BULLISH
    assert (f.zone_low, f.zone_high) == (100.5, 105.0)
    assert f.bar_index == 18
    assert f.state is ZoneState.UNMITIGATED and f.fill_fraction == 0.0


def test_bearish_fvg_symmetry() -> None:
    fvgs = find_fvgs(_bars(_mirror(_BULL)), Timeframe.M5, tick_size=0.1)
    assert len(fvgs) == 1
    f = fvgs[0]
    assert f.direction is Polarity.BEARISH
    assert (f.zone_low, f.zone_high) == (95.0, 99.5)  # gespiegelt zu (100.5, 105.0)


def test_fvg_size_filter_rejects_micro_gap() -> None:
    rows = [
        *_WARMUP,
        (100.0, 100.5, 99.5, 100.0),
        (100.0, 101.2, 99.9, 101.0),
        (101.0, 101.4, 100.55, 101.1),
    ]  # gap [100.5, 100.55] = 0.05
    assert find_fvgs(_bars(rows), Timeframe.M5, tick_size=0.1) == []


def test_no_fvg_when_bars_overlap() -> None:
    rows = [
        *_WARMUP,
        (100.0, 108.0, 99.8, 107.5),
        (107.5, 109.0, 99.0, 108.0),
        (108.0, 110.0, 100.0, 109.0),
    ]
    assert find_fvgs(_bars(rows), Timeframe.M5, tick_size=0.1) == []


# ------------------------------------------------------------------------- §11 Mitigation


def test_mitigation_fill_formula() -> None:
    after_half = _bars([(103, 103, 102.75, 102.9)])  # low 102.75 -> (105-102.75)/4.5 = 0.5
    assert mitigation_fill(100.5, 105.0, Polarity.BULLISH, after_half) == 0.5
    assert mitigation_fill(100.5, 105.0, Polarity.BULLISH, []) == 0.0
    after_full = _bars([(101, 101, 99.0, 100.0)])  # dips below zone_low -> clip to 1.0
    assert mitigation_fill(100.5, 105.0, Polarity.BULLISH, after_full) == 1.0


def test_zone_state_priority() -> None:
    kw = {"max_age_bars": 50, "touch_threshold": 0.0, "consumed_threshold": 0.5}
    assert zone_state(1.0, 0, True, **kw) is ZoneState.INVERTED
    assert zone_state(0.7, 0, False, **kw) is ZoneState.MITIGATED
    assert zone_state(0.2, 999, False, **kw) is ZoneState.STALE
    assert zone_state(0.2, 0, False, **kw) is ZoneState.PARTIAL
    assert zone_state(0.0, 0, False, **kw) is ZoneState.UNMITIGATED


def test_fvg_state_progression() -> None:
    partial = [*_BULL, (108, 108, 103.2, 104.0)]  # low 103.2 -> (105-103.2)/4.5 = 0.4
    f = find_fvgs(_bars(partial), Timeframe.M5, tick_size=0.1)[0]
    assert f.state is ZoneState.PARTIAL and abs(f.fill_fraction - 0.4) < 1e-9

    mitig = [*_BULL, (108, 108, 102.0, 103.0)]  # (105-102)/4.5 = 0.667
    assert find_fvgs(_bars(mitig), Timeframe.M5, tick_size=0.1)[0].state is ZoneState.MITIGATED

    stale = [*_BULL, *([(108, 108.4, 107.6, 108.0)] * 6)]  # 6 Bars ohne Fill, max_age=3
    fs = find_fvgs(_bars(stale), Timeframe.M5, tick_size=0.1, params=FvgParams(max_age_bars=3))[0]
    assert fs.state is ZoneState.STALE


# ------------------------------------------------------------------------- §7 Displacement


def test_displacement_golden() -> None:
    disps = find_displacements(
        _bars(_BULL), Timeframe.M5, find_fvgs(_bars(_BULL), Timeframe.M5, tick_size=0.1)
    )
    assert len(disps) == 1
    d = disps[0]
    assert d.direction is Polarity.BULLISH
    assert (d.start_index, d.end_index, d.bars) == (17, 17, 1)  # kleinstes qualifizierendes n
    assert d.net_move_atr >= 1.5
    assert d.body_ratio >= 0.60
    assert len(d.fvgs) == 1 and d.fvgs[0].bar_index == 18


def test_displacement_requires_an_fvg() -> None:
    # großer Impuls, aber die dritte Bar schließt die Lücke -> keine FVG -> kein Displacement
    rows = [*_WARMUP, (100.0, 108.0, 99.8, 107.5), (107.5, 109.0, 99.0, 108.0)]
    bars = _bars(rows)
    assert find_fvgs(bars, Timeframe.M5, tick_size=0.1) == []
    assert find_displacements(bars, Timeframe.M5, []) == []


def test_displacement_body_ratio_gate() -> None:
    rows = [*_WARMUP, (100.0, 112.0, 90.0, 102.0), (108.0, 112.0, 106.0, 110.0)]
    bars = _bars(rows)
    fvgs = find_fvgs(bars, Timeframe.M5, tick_size=0.1)
    assert fvgs and fvgs[0].direction is Polarity.BULLISH  # FVG existiert
    assert find_displacements(bars, Timeframe.M5, fvgs) == []  # aber Körperanteil zu klein


def test_displacement_counter_bar_gate() -> None:
    rows = [
        *_WARMUP,
        (100.0, 104.0, 100.0, 103.5),  # green
        (103.5, 104.0, 100.5, 101.5),  # red (Gegenkerze)
        (105.5, 109.0, 105.5, 108.0),
    ]  # green -> einzige FVG [104.0, 105.5]
    bars = _bars(rows)
    fvgs = find_fvgs(bars, Timeframe.M5, tick_size=0.1)
    assert find_displacements(bars, Timeframe.M5, fvgs) == []  # default max_counter_bars=0
    got = find_displacements(
        bars, Timeframe.M5, fvgs, params=DisplacementParams(max_counter_bars=1)
    )
    assert len(got) == 1 and got[0].direction is Polarity.BULLISH


def test_displacement_long_short_symmetry() -> None:
    up = find_displacements(
        _bars(_BULL), Timeframe.M5, find_fvgs(_bars(_BULL), Timeframe.M5, tick_size=0.1)
    )
    dn_bars = _bars(_mirror(_BULL))
    dn = find_displacements(dn_bars, Timeframe.M5, find_fvgs(dn_bars, Timeframe.M5, tick_size=0.1))
    assert len(up) == len(dn) == 1
    assert dn[0].direction is Polarity.BEARISH
    assert (dn[0].start_index, dn[0].end_index) == (up[0].start_index, up[0].end_index)
    assert abs(dn[0].net_move_atr - up[0].net_move_atr) < 1e-9


# ------------------------------------------------------------------------- §9 IFVG


_INVERT = [*_BULL, (108, 110, 107, 109.0), (109, 109.5, 98.0, 99.0)]  # idx20 schließt < zone_low


def test_ifvg_golden() -> None:
    bars = _bars(_INVERT)
    fvgs = find_fvgs(bars, Timeframe.M5, tick_size=0.1)
    assert fvgs[0].state is ZoneState.INVERTED
    ifvgs = find_ifvgs(fvgs, bars, Timeframe.M5)
    assert len(ifvgs) == 1
    iv = ifvgs[0]
    assert iv.direction is Polarity.BEARISH
    assert (iv.zone_low, iv.zone_high) == (100.5, 105.0)
    assert iv.flip_bar_index == 20
    assert iv.flipped_at == bars[20].close_time


def test_ifvg_not_created_when_price_holds() -> None:
    bars = _bars([*_BULL, (108, 110, 106, 109.0), (109, 111, 108, 110.0)])
    fvgs = find_fvgs(bars, Timeframe.M5, tick_size=0.1)
    assert fvgs[0].state is not ZoneState.INVERTED
    assert find_ifvgs(fvgs, bars, Timeframe.M5) == []


def test_ifvg_min_close_through_atr() -> None:
    bars = _bars([*_BULL, (108, 110, 107, 109.0), (109, 109.5, 100.2, 100.3)])  # knapp unter 100.5
    fvgs = find_fvgs(
        bars, Timeframe.M5, tick_size=0.1, params=FvgParams(invert_close_through_atr=1.0)
    )
    assert fvgs[0].state is not ZoneState.INVERTED
    assert find_ifvgs(fvgs, bars, Timeframe.M5, params=IfvgParams(min_close_through_atr=1.0)) == []


def test_ifvg_symmetry() -> None:
    bars = _bars(_mirror(_INVERT))
    fvgs = find_fvgs(bars, Timeframe.M5, tick_size=0.1)
    ifvgs = find_ifvgs(fvgs, bars, Timeframe.M5)
    assert len(ifvgs) == 1 and ifvgs[0].direction is Polarity.BULLISH


# ------------------------------------------------------------------------- Look-ahead


def test_fvg_zone_is_lookahead_immune() -> None:
    full = _bars(_INVERT)
    early = find_fvgs(full[:19], Timeframe.M5, tick_size=0.1)  # nur bis kurz nach der FVG
    late = find_fvgs(full, Timeframe.M5, tick_size=0.1)
    assert early and late
    assert (early[0].zone_low, early[0].zone_high, early[0].created_bar, early[0].bar_index) == (
        late[0].zone_low,
        late[0].zone_high,
        late[0].created_bar,
        late[0].bar_index,
    )


def test_displacement_is_lookahead_immune() -> None:
    full = _bars(_INVERT)
    e = 18
    early = find_displacements(
        full[: e + 1], Timeframe.M5, find_fvgs(full[: e + 1], Timeframe.M5, tick_size=0.1)
    )
    late = find_displacements(full, Timeframe.M5, find_fvgs(full, Timeframe.M5, tick_size=0.1))
    assert len(early) == len(late) == 1
    assert (early[0].start_index, early[0].end_index, early[0].direction) == (
        late[0].start_index,
        late[0].end_index,
        late[0].direction,
    )
    assert abs(early[0].net_move_atr - late[0].net_move_atr) < 1e-9


def test_ifvg_flip_is_lookahead_immune() -> None:
    full = _bars(_INVERT)
    fvgs_before = find_fvgs(full[:20], Timeframe.M5, tick_size=0.1)
    assert find_ifvgs(fvgs_before, full[:20], Timeframe.M5) == []  # Flip-Bar (20) fehlt noch
    fvgs_at = find_fvgs(full[:21], Timeframe.M5, tick_size=0.1)
    iv_at = find_ifvgs(fvgs_at, full[:21], Timeframe.M5)
    iv_full = find_ifvgs(find_fvgs(full, Timeframe.M5, tick_size=0.1), full, Timeframe.M5)
    assert iv_at[0].flip_bar_index == iv_full[0].flip_bar_index == 20
    assert iv_at[0].flipped_at == iv_full[0].flipped_at


# ------------------------------------------------------------------------- Orchestrierung


def test_analyze_imbalance_combines_all_three() -> None:
    res = analyze_imbalance(_bars(_INVERT), Timeframe.M5, tick_size=0.1)
    assert len(res.fvgs) == 1
    assert len(res.displacements) == 1
    assert len(res.ifvgs) == 1
    assert res.fvgs[0].from_displacement is True  # link_displacement hat gegriffen
