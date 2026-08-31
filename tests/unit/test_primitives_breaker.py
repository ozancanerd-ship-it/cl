"""Phase 3 — Golden-Tests: Breaker (§12).

Breaker = Order Block, dessen schützende Struktur per **BOS** gebrochen wurde → Polaritätsumkehr,
gleiche Zone, invertierte Wirkung, ``max_age_bars`` ab ``flipped_at``, Zustand über die
bestehende Mitigation-Logik. Edge Cases, Long/Short-Symmetrie, Look-ahead-Schutz.
"""

from __future__ import annotations

from datetime import timedelta

from trading_agent.core.enums import (
    Polarity,
    StructureBreakKind,
    Timeframe,
    ZoneState,
)
from trading_agent.core.models import OHLCV
from trading_agent.core.time import bar_close_time, parse_timestamp
from trading_agent.strategy.primitives.blocks import (
    BreakerParams,
    find_breakers,
    find_order_blocks,
)
from trading_agent.strategy.primitives.imbalance import find_displacements, find_fvgs
from trading_agent.strategy.primitives.models import OrderBlock, StructureBreak
from trading_agent.strategy.primitives.structure import structure_breaks
from trading_agent.strategy.primitives.swings import detect_swings

TF = Timeframe.M5
Z_LOW, Z_HIGH = 97.3, 98.5  # Zone des bearischen Ursprungs-OB


def _bars(
    rows: list[tuple[float, float, float, float]], *, start: str = "2024-06-08T00:00:00Z"
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


_NEUTRAL = [(100.0, 100.5, 99.5, 100.0)] * 24


def _bear_ob(bars: list[OHLCV], idx: int = 16, zl: float = Z_LOW, zh: float = Z_HIGH) -> OrderBlock:
    return OrderBlock(
        direction=Polarity.BEARISH,
        timeframe=TF,
        zone_low=zl,
        zone_high=zh,
        ob_bar=bars[idx].open_time,
        bar_index=idx,
    )


def _bull_ob(bars: list[OHLCV], idx: int = 16, zl: float = Z_LOW, zh: float = Z_HIGH) -> OrderBlock:
    return OrderBlock(
        direction=Polarity.BULLISH,
        timeframe=TF,
        zone_low=zl,
        zone_high=zh,
        ob_bar=bars[idx].open_time,
        bar_index=idx,
    )


def _brk(
    bars: list[OHLCV],
    idx: int,
    direction: Polarity,
    close: float,
    kind: StructureBreakKind = StructureBreakKind.BOS,
) -> StructureBreak:
    return StructureBreak(
        kind=kind,
        direction=direction,
        timeframe=TF,
        broken_level_price=close,
        break_bar_timestamp=bars[idx].open_time,
        break_close=close,
    )


# --------------------------------------------------------------------- Unit: hand-made inputs


def test_bullish_breaker_golden() -> None:
    bars = _bars(_NEUTRAL)
    ob = _bear_ob(bars)
    bos = _brk(bars, 20, Polarity.BULLISH, close=99.5)  # schließt über zone_high
    brs = find_breakers(bars, TF, [ob], [bos])
    assert len(brs) == 1
    b = brs[0]
    assert b.direction is Polarity.BULLISH  # invertiert ggü. bearischem OB
    assert (b.zone_low, b.zone_high) == (Z_LOW, Z_HIGH)  # gleiche Zone
    assert b.origin_ob is ob and b.flip_break_ref is bos
    assert b.flip_bar_index == 20 and b.flipped_at == bars[20].close_time
    assert b.state is ZoneState.UNMITIGATED and b.fill_fraction == 0.0
    assert b.zone_id == "BRK-M5-bullish-16"


def test_breaker_requires_bos_not_choch() -> None:
    bars = _bars(_NEUTRAL)
    choch = _brk(bars, 20, Polarity.BULLISH, close=99.5, kind=StructureBreakKind.CHOCH)
    assert find_breakers(bars, TF, [_bear_ob(bars)], [choch]) == []  # default require_bos=True
    got = find_breakers(
        bars, TF, [_bear_ob(bars)], [choch], params=BreakerParams(require_bos=False)
    )
    assert len(got) == 1


def test_breaker_requires_close_beyond_zone() -> None:
    bars = _bars(_NEUTRAL)
    weak = _brk(bars, 20, Polarity.BULLISH, close=98.4)  # unter zone_high
    assert find_breakers(bars, TF, [_bear_ob(bars)], [weak]) == []


def test_breaker_buffer_atr() -> None:
    bars = _bars(_NEUTRAL)
    bos = _brk(bars, 20, Polarity.BULLISH, close=98.9)  # knapp über zone_high
    assert len(find_breakers(bars, TF, [_bear_ob(bars)], [bos])) == 1  # buffer 0
    assert (
        find_breakers(bars, TF, [_bear_ob(bars)], [bos], params=BreakerParams(buffer_atr=1.0)) == []
    )  # Puffer nicht überschritten


def test_breaker_break_must_come_after_ob() -> None:
    bars = _bars(_NEUTRAL)
    early = _brk(bars, 10, Polarity.BULLISH, close=99.5)  # vor der OB-Bar (idx 16)
    assert find_breakers(bars, TF, [_bear_ob(bars)], [early]) == []


def test_breaker_break_must_be_opposite_polarity() -> None:
    bars = _bars(_NEUTRAL)
    same_dir = _brk(bars, 20, Polarity.BEARISH, close=96.0)  # bearischer Bruch am bearischen OB
    assert find_breakers(bars, TF, [_bear_ob(bars)], [same_dir]) == []


def test_breaker_picks_first_qualifying_bos() -> None:
    bars = _bars(_NEUTRAL)
    b1 = _brk(bars, 19, Polarity.BULLISH, close=99.0)
    b2 = _brk(bars, 21, Polarity.BULLISH, close=99.8)
    brs = find_breakers(bars, TF, [_bear_ob(bars)], [b2, b1])
    assert len(brs) == 1 and brs[0].flip_bar_index == 19


def test_breaker_state_progression() -> None:
    # Flip bei idx 20; danach taucht der Preis in [97.3, 98.5] (H = 1.2)
    partial = _bars([*_NEUTRAL, (99.0, 99.0, 98.02, 99.0)])  # (98.5 - 98.02)/1.2 = 0.4
    ob, bos = _bear_ob(partial), _brk(partial, 20, Polarity.BULLISH, close=99.5)
    b = find_breakers(partial, TF, [ob], [bos])[0]
    assert b.state is ZoneState.PARTIAL and abs(b.fill_fraction - 0.4) < 1e-9

    mitig = _bars([*_NEUTRAL, (99.0, 99.0, 97.6, 99.0)])  # (98.5 - 97.6)/1.2 = 0.75
    b2 = find_breakers(
        mitig, TF, [_bear_ob(mitig)], [_brk(mitig, 20, Polarity.BULLISH, close=99.5)]
    )[0]
    assert b2.state is ZoneState.MITIGATED

    stale = _bars([*_NEUTRAL, *([(99.0, 99.5, 98.7, 99.0)] * 6)])  # nie in der Zone
    b3 = find_breakers(
        stale,
        TF,
        [_bear_ob(stale)],
        [_brk(stale, 20, Polarity.BULLISH, close=99.5)],
        params=BreakerParams(max_age_bars=3),
    )[0]
    assert b3.state is ZoneState.STALE


def test_breaker_long_short_symmetry() -> None:
    bars = _bars(_NEUTRAL)
    bull = find_breakers(
        bars, TF, [_bear_ob(bars)], [_brk(bars, 20, Polarity.BULLISH, close=99.5)]
    )[0]
    # gespiegelt: bullischer OB an einem Tief -> bearischer Breaker via bearischen BOS unter zone_low
    bear_ob = _bull_ob(bars, 16, zl=101.5, zh=102.7)
    bear = find_breakers(bars, TF, [bear_ob], [_brk(bars, 20, Polarity.BEARISH, close=100.5)])[0]
    assert bear.direction is Polarity.BEARISH
    assert bull.direction is Polarity.BULLISH
    assert bear.flip_bar_index == bull.flip_bar_index
    assert bear.state is bull.state is ZoneState.UNMITIGATED


def test_breaker_lookahead_immune() -> None:
    full = _bars([*_NEUTRAL, (99.0, 100.0, 98.6, 99.5), (99.5, 100.0, 98.8, 99.2)])
    ob = _bear_ob(full)
    bos = _brk(full, 20, Polarity.BULLISH, close=99.5)
    early = find_breakers(full[:21], TF, [ob], [_brk(full[:21], 20, Polarity.BULLISH, close=99.5)])[
        0
    ]
    late = find_breakers(full, TF, [ob], [bos])[0]
    assert (early.zone_low, early.zone_high, early.flipped_at, early.flip_bar_index) == (
        late.zone_low,
        late.zone_high,
        late.flipped_at,
        late.flip_bar_index,
    )
    assert early.state is late.state is ZoneState.UNMITIGATED  # spätere Bars füllen die Zone nicht
    # Flip-Bar fehlt noch -> kein Breaker
    assert find_breakers(full[:20], TF, [ob], []) == []


# --------------------------------------------------------------------- Golden: volle Pipeline


def _interp(pivots: list[float], per_leg: int) -> list[float]:
    prices = [pivots[0]]
    for tgt in pivots[1:]:
        s = prices[-1]
        prices += [round(s + (tgt - s) * k / per_leg, 3) for k in range(1, per_leg + 1)]
    return prices


def _breaker_pipeline_bars() -> tuple[list[OHLCV], int]:
    warmup = [(100.0, 100.5, 99.5, 100.0)] * 16
    # Aufwärtsstruktur: SH 96/100/104/108, SL 92/95/99  -> TREND_UP, letztes SL 99.
    up = _interp([90.0, 96.0, 92.0, 100.0, 95.0, 104.0, 99.0, 108.0], 5)
    rows: list[tuple[float, float, float, float]] = [*warmup, *[(p, p, p, p) for p in up]]
    ob_idx = len(rows)
    rows += [
        (106.5, 107.0, 106.3, 106.9),  # OB (Up-Close), knapp unter dem Hoch (108)
        (
            106.9,
            107.0,
            96.0,
            96.5,
        ),  # bearischer Impuls -> Close < letztes SL (99) => bearischer CHoCH
        (96.5, 97.5, 93.0, 94.0),  # -> bearische FVG [97.5, 106.3]
    ]
    # Erholung: SH 98/103/107, SL 92/95/100/104, dann Rally -> bullischer BOS > OB-Zone (107.0).
    rec = _interp([94.0, 92.0, 98.0, 95.0, 103.0, 100.0, 107.0, 104.0, 113.0], 5)
    rows += [(p, p, p, p) for p in rec]
    return _bars(rows), ob_idx


def test_breaker_full_pipeline() -> None:
    bars, ob_idx = _breaker_pipeline_bars()
    swings = detect_swings(bars, TF)
    breaks = structure_breaks(bars, swings, TF)
    fvgs = find_fvgs(bars, TF, tick_size=0.1)
    disps = find_displacements(bars, TF, fvgs)
    obs = find_order_blocks(bars, TF, disps, breaks)

    bear_obs = [o for o in obs if o.direction is Polarity.BEARISH]
    assert len(bear_obs) == 1 and bear_obs[0].bar_index == ob_idx

    assert any(b.kind is StructureBreakKind.BOS and b.direction is Polarity.BULLISH for b in breaks)

    brs = find_breakers(bars, TF, obs, breaks)
    assert len(brs) == 1
    b = brs[0]
    assert b.direction is Polarity.BULLISH
    assert b.origin_ob is bear_obs[0]
    assert (b.zone_low, b.zone_high) == (bear_obs[0].zone_low, bear_obs[0].zone_high)
    assert b.flip_break_ref is not None and b.flip_break_ref.kind is StructureBreakKind.BOS
    assert b.flip_break_ref.break_close > b.zone_high
    assert b.flip_bar_index > ob_idx
