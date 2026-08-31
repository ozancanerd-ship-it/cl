"""Phase 3 — Range-Bruch (``primitives.md`` §2.3).

``confirmed close`` jenseits einer Range-Grenze ⇒ BOS ``origin=RANGE``. Edge Cases,
Look-ahead-Immunität, Long/Short-Symmetrie, Integration mit dem Regime-Modul.
"""

from __future__ import annotations

from datetime import timedelta

from trading_agent.analysis.regime import RegimeParams, directional_regime
from trading_agent.core.enums import (
    Polarity,
    RegimeDirectional,
    StructureBreakKind,
    StructureOrigin,
    Timeframe,
)
from trading_agent.core.models import OHLCV
from trading_agent.core.time import bar_close_time, parse_timestamp
from trading_agent.strategy.primitives.structure import (
    range_break,
    range_breaks,
    structure_breaks,
)
from trading_agent.strategy.primitives.swings import detect_swings

TF = Timeframe.H1
S = parse_timestamp("2024-06-01T00:00:00Z")


def _bars(prices: list[float], *, w: float = 0.3) -> list[OHLCV]:
    out: list[OHLCV] = []
    t = S
    for i, p in enumerate(prices):
        o = (prices[i - 1] + p) / 2 if i > 0 else p
        out.append(
            OHLCV(
                instrument="X",
                timeframe=TF,
                open_time=t,
                close_time=bar_close_time(t, TF),
                open=o,
                high=max(o, p) + w,
                low=min(o, p) - w,
                close=p,
                volume=1.0,
                source="t",
            )
        )
        t += timedelta(seconds=TF.seconds)
    return out


def _interp(pivots: list[float], per_leg: int) -> list[float]:
    out = [pivots[0]]
    for tgt in pivots[1:]:
        s = out[-1]
        out += [round(s + (tgt - s) * k / per_leg, 4) for k in range(1, per_leg + 1)]
    return out


_RANGE_PRICES = _interp([100.0, 110.0] * 4 + [100.0], 5)  # Oszillation 100..110, kein Trend
_LOW, _HIGH = 100.0, 110.0


# --------------------------------------------------------------------------- range_breaks


def test_range_break_bullish() -> None:
    bars = _bars([*_RANGE_PRICES, 112.0])  # letzter Close jenseits range_high
    evs = range_breaks(bars, TF, _LOW, _HIGH)
    assert len(evs) == 1
    b = evs[0]
    assert b.kind is StructureBreakKind.BOS
    assert b.direction is Polarity.BULLISH
    assert b.origin is StructureOrigin.RANGE
    assert b.broken_level_price == _HIGH
    assert b.broken_swing is None
    assert b.break_close == 112.0
    assert b.break_bar_timestamp == bars[-1].open_time
    assert b.break_id.startswith("SB-bos-bullish-range-H1-")


def test_range_break_bearish_symmetry() -> None:
    up = _bars([*_RANGE_PRICES, 112.0])
    down = _bars([*[200.0 - p for p in _RANGE_PRICES], 88.0])
    up_b = range_breaks(up, TF, _LOW, _HIGH)[0]
    down_b = range_breaks(down, TF, 200.0 - _HIGH, 200.0 - _LOW)[0]
    assert down_b.direction is Polarity.BEARISH
    assert down_b.origin is StructureOrigin.RANGE
    assert down_b.broken_level_price == 200.0 - _HIGH  # = 90 (gespiegelte untere Grenze)
    assert down_b.break_close == 88.0
    assert (down_b.kind, down_b.broken_swing) == (up_b.kind, up_b.broken_swing)


def test_no_range_break_inside_range() -> None:
    assert range_breaks(_bars(_RANGE_PRICES), TF, _LOW, _HIGH) == []
    assert range_break(_bars(_RANGE_PRICES), TF, _LOW, _HIGH) is None


def test_range_break_buffer_atr() -> None:
    bars = _bars([*_RANGE_PRICES, 110.6])  # knapp über der Grenze
    assert len(range_breaks(bars, TF, _LOW, _HIGH, buffer_atr=0.0)) == 1
    # mit Puffer (ATR ~ 4 in dieser Oszillation) reicht 0.6 nicht mehr
    assert range_breaks(bars, TF, _LOW, _HIGH, buffer_atr=1.0) == []


def test_range_break_dedupe_and_from_index() -> None:
    bars = _bars([*_RANGE_PRICES, 112.0, 113.0, 114.0])  # 3 Bars jenseits der Grenze
    evs = range_breaks(bars, TF, _LOW, _HIGH)
    assert len(evs) == 1 and evs[0].break_close == 112.0  # nur der erste Bruch
    # from_index nach dem ersten Ausbruch -> kein Bruch mehr
    assert range_breaks(bars, TF, _LOW, _HIGH, from_index=len(bars) - 2) != []
    assert range_breaks(bars, TF, _LOW, _HIGH, from_index=len(bars)) == []


def test_range_break_degenerate_bounds() -> None:
    assert range_breaks(_bars([*_RANGE_PRICES, 120.0]), TF, 110.0, 100.0) == []  # high <= low


def test_range_break_lookahead_immune() -> None:
    full = _bars([*_RANGE_PRICES, 112.0, 108.0, 105.0])
    k = len(_RANGE_PRICES) + 1  # bis inkl. Bruch-Bar
    early = range_breaks(full[:k], TF, _LOW, _HIGH)
    late = range_breaks(full, TF, _LOW, _HIGH)
    assert early and late
    assert (early[0].break_bar_timestamp, early[0].break_close, early[0].broken_level_price) == (
        late[0].break_bar_timestamp,
        late[0].break_close,
        late[0].broken_level_price,
    )
    assert range_breaks(full[: k - 1], TF, _LOW, _HIGH) == []  # Bruch-Bar fehlt noch


# --------------------------------------------------------------- Abgrenzung structure_breaks


def test_structure_breaks_emits_only_trend_origin() -> None:
    # structure_breaks bleibt rein gerichtet; Range-Bruch ist Sache von range_breaks.
    bars = _bars([*_RANGE_PRICES, 112.0, 113.0])
    sw = detect_swings(bars, TF)
    assert all(b.origin is StructureOrigin.TREND for b in structure_breaks(bars, sw, TF))


# --------------------------------------------------------------- Integration Regime-Modul


def test_range_break_uses_regime_range_bounds() -> None:
    bars = _bars([*_RANGE_PRICES, 112.5])
    inside = bars[: len(_RANGE_PRICES)]  # Regime auf der reinen Oszillation
    sw = detect_swings(inside, TF)
    br = structure_breaks(inside, sw, TF)
    d, _score, _sn, rlo, rhi = directional_regime(
        inside, sw, br, TF, params=RegimeParams(), atr_val=4.0
    )
    assert d is RegimeDirectional.RANGE and rlo is not None and rhi is not None
    # die vom Regime gelieferten Grenzen speisen die Range-Bruch-Regel
    ev = range_break(bars, TF, rlo, rhi, from_index=len(_RANGE_PRICES))
    assert ev is not None
    assert ev.origin is StructureOrigin.RANGE and ev.direction is Polarity.BULLISH
