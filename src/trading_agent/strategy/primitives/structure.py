"""Struktur-Zustand + BOS / CHoCH + Range-Bruch (``primitives.md`` §2, §2.3, §3).

* **Struktur-Zustand:** die letzten ``min_swings`` bestätigten Swing-Paare bilden HH+HL
  (``TREND_UP``) bzw. LH+LL (``TREND_DOWN``); sonst ``UNCLEAR``.
* **BOS** = ``confirmed close`` jenseits des Leg-Start-Swings **in** Trendrichtung
  (``+/- bos.buffer_atr × ATR``); ``origin = TREND``.
* **CHoCH** = erster ``confirmed close`` jenseits des letzten Gegen-Swings **gegen** die Struktur.
* **Range-Bruch (§2.3):** liegt **keine** gerichtete Struktur vor, gilt statt BOS die Range-Regel:
  ``confirmed close`` jenseits ``range_high``/``range_low`` (aus ``regime.md`` §Range, um
  ``bos.buffer_atr × ATR``) ⇒ BOS in Bruchrichtung mit ``origin = RANGE`` (``broken_swing = None``).

Look-ahead-frei: für Bar ``t`` zählen nur Swings mit ``confirmed_at <= bars[t].close_time``;
ATR je Bar nur aus Bars ``<= t``. Long/Short-symmetrisch.
"""

from __future__ import annotations

from collections.abc import Sequence

from trading_agent.core.enums import (
    Polarity,
    RegimeDirectional,
    StructureBreakKind,
    StructureOrigin,
    SwingType,
    Timeframe,
)
from trading_agent.core.models import OHLCV
from trading_agent.strategy.primitives.atr import ATR_PERIOD_DEFAULT, atr_at_index, atr_series
from trading_agent.strategy.primitives.models import StructureBreak, StructureState, SwingPoint

MIN_SWINGS_DEFAULT = 2
BOS_BUFFER_ATR_DEFAULT = 0.0
CHOCH_BUFFER_ATR_DEFAULT = 0.0


def derive_structure_state(
    swings: Sequence[SwingPoint],
    timeframe: Timeframe,
    *,
    min_swings: int = MIN_SWINGS_DEFAULT,
) -> StructureState:
    highs = [s for s in swings if s.type is SwingType.SWING_HIGH]
    lows = [s for s in swings if s.type is SwingType.SWING_LOW]
    last_high = highs[-1] if highs else None
    last_low = lows[-1] if lows else None

    directional = RegimeDirectional.UNCLEAR
    if len(highs) > min_swings and len(lows) > min_swings:
        rec_highs = highs[-min_swings:]
        rec_lows = lows[-min_swings:]
        up = all(
            rec_highs[i].price > rec_highs[i - 1].price for i in range(1, len(rec_highs))
        ) and all(rec_lows[i].price > rec_lows[i - 1].price for i in range(1, len(rec_lows)))
        # Vergleich auch gegen den jeweils davorliegenden Swing (min_swings echte Vergleiche)
        up = up and rec_highs[0].price > highs[-min_swings - 1].price
        up = up and rec_lows[0].price > lows[-min_swings - 1].price
        down = all(
            rec_highs[i].price < rec_highs[i - 1].price for i in range(1, len(rec_highs))
        ) and all(rec_lows[i].price < rec_lows[i - 1].price for i in range(1, len(rec_lows)))
        down = down and rec_highs[0].price < highs[-min_swings - 1].price
        down = down and rec_lows[0].price < lows[-min_swings - 1].price
        if up:
            directional = RegimeDirectional.TREND_UP
        elif down:
            directional = RegimeDirectional.TREND_DOWN

    return StructureState(
        timeframe=timeframe,
        directional=directional,
        swings=tuple(swings),
        last_swing_high=last_high,
        last_swing_low=last_low,
    )


def _leg_start_high(swings: Sequence[SwingPoint]) -> SwingPoint | None:
    """Jüngstes bestätigtes SH vor dem letzten HL (Start des aktuellen Aufwärts-Legs)."""
    last_low = next((s for s in reversed(swings) if s.type is SwingType.SWING_LOW), None)
    if last_low is None:
        return None
    return next(
        (
            s
            for s in reversed(swings)
            if s.type is SwingType.SWING_HIGH and s.bar_index < last_low.bar_index
        ),
        None,
    )


def _leg_start_low(swings: Sequence[SwingPoint]) -> SwingPoint | None:
    last_high = next((s for s in reversed(swings) if s.type is SwingType.SWING_HIGH), None)
    if last_high is None:
        return None
    return next(
        (
            s
            for s in reversed(swings)
            if s.type is SwingType.SWING_LOW and s.bar_index < last_high.bar_index
        ),
        None,
    )


def structure_breaks(
    bars: Sequence[OHLCV],
    swings: Sequence[SwingPoint],
    timeframe: Timeframe,
    *,
    min_swings: int = MIN_SWINGS_DEFAULT,
    bos_buffer_atr: float = BOS_BUFFER_ATR_DEFAULT,
    choch_buffer_atr: float = CHOCH_BUFFER_ATR_DEFAULT,
    atr_period: int = ATR_PERIOD_DEFAULT,
) -> list[StructureBreak]:
    """Alle **gerichteten** BOS/CHoCH-Ereignisse (``origin=TREND``), chronologisch, Dedupe je
    Level+Richtung. Der Range-Bruch (§2.3) ist ``range_breaks`` — ihn ruft der Regime-bewusste
    Konsument mit den Range-Grenzen auf.
    """
    atr = atr_series(bars, atr_period)
    events: list[StructureBreak] = []
    last_key: tuple[str, float] | None = None

    for t, bar in enumerate(bars):
        visible = [s for s in swings if s.confirmed_at <= bar.close_time]
        if len(visible) < 2:
            continue
        state = derive_structure_state(visible, timeframe, min_swings=min_swings)
        a = atr_at_index(atr, t) or 0.0

        brk: StructureBreak | None = None
        if state.directional is RegimeDirectional.TREND_UP:
            sh_star = _leg_start_high(visible)
            hl_last = next((s for s in reversed(visible) if s.type is SwingType.SWING_LOW), None)
            if sh_star is not None and bar.close > sh_star.price + bos_buffer_atr * a:
                brk = _mk(
                    StructureBreakKind.BOS,
                    Polarity.BULLISH,
                    timeframe,
                    sh_star,
                    bar,
                    a,
                    prior=RegimeDirectional.TREND_UP,
                )
            elif hl_last is not None and bar.close < hl_last.price - choch_buffer_atr * a:
                brk = _mk(
                    StructureBreakKind.CHOCH,
                    Polarity.BEARISH,
                    timeframe,
                    hl_last,
                    bar,
                    a,
                    prior=RegimeDirectional.TREND_UP,
                )
        elif state.directional is RegimeDirectional.TREND_DOWN:
            sl_star = _leg_start_low(visible)
            lh_last = next((s for s in reversed(visible) if s.type is SwingType.SWING_HIGH), None)
            if sl_star is not None and bar.close < sl_star.price - bos_buffer_atr * a:
                brk = _mk(
                    StructureBreakKind.BOS,
                    Polarity.BEARISH,
                    timeframe,
                    sl_star,
                    bar,
                    a,
                    prior=RegimeDirectional.TREND_DOWN,
                )
            elif lh_last is not None and bar.close > lh_last.price + choch_buffer_atr * a:
                brk = _mk(
                    StructureBreakKind.CHOCH,
                    Polarity.BULLISH,
                    timeframe,
                    lh_last,
                    bar,
                    a,
                    prior=RegimeDirectional.TREND_DOWN,
                )

        if brk is not None:
            key = (f"{brk.kind}:{brk.direction}", round(brk.broken_level_price, 8))
            if key != last_key:
                events.append(brk)
                last_key = key

    return events


def _mk(
    kind: StructureBreakKind,
    direction: Polarity,
    timeframe: Timeframe,
    swing: SwingPoint,
    bar: OHLCV,
    atr_val: float,
    *,
    prior: RegimeDirectional,
) -> StructureBreak:
    dist_atr = abs(bar.close - swing.price) / atr_val if atr_val > 0 else 0.0
    return StructureBreak(
        kind=kind,
        direction=direction,
        timeframe=timeframe,
        broken_level_price=swing.price,
        break_bar_timestamp=bar.open_time,
        break_close=bar.close,
        origin=StructureOrigin.TREND,
        broken_swing=swing,
        prior_state=prior if kind is StructureBreakKind.CHOCH else None,
        break_distance_atr=dist_atr,
    )


def _range_brk_for_bar(
    bar: OHLCV,
    timeframe: Timeframe,
    bounds: tuple[float, float],
    atr_val: float,
    buffer_atr: float,
) -> StructureBreak | None:
    rlow, rhigh = bounds
    if rhigh <= rlow:
        return None
    if bar.close > rhigh + buffer_atr * atr_val:
        level, direction = rhigh, Polarity.BULLISH
    elif bar.close < rlow - buffer_atr * atr_val:
        level, direction = rlow, Polarity.BEARISH
    else:
        return None
    dist_atr = abs(bar.close - level) / atr_val if atr_val > 0 else 0.0
    return StructureBreak(
        kind=StructureBreakKind.BOS,
        direction=direction,
        timeframe=timeframe,
        broken_level_price=level,
        break_bar_timestamp=bar.open_time,
        break_close=bar.close,
        origin=StructureOrigin.RANGE,
        broken_swing=None,
        break_distance_atr=dist_atr,
    )


def range_breaks(
    bars: Sequence[OHLCV],
    timeframe: Timeframe,
    range_low: float,
    range_high: float,
    *,
    buffer_atr: float = BOS_BUFFER_ATR_DEFAULT,
    atr_period: int = ATR_PERIOD_DEFAULT,
    from_index: int = 0,
) -> list[StructureBreak]:
    """§2.3: alle ``confirmed close`` jenseits einer Range-Grenze ⇒ BOS ``origin=RANGE``.

    Dedupe je (Richtung, Grenze) wie ``structure_breaks``. ``from_index`` = ab welchem Bar
    geprüft wird (typisch: nach dem Bar, an dem die Range erkannt wurde).
    """
    if range_high <= range_low or not bars:
        return []
    atr = atr_series(bars, atr_period)
    events: list[StructureBreak] = []
    last_key: tuple[str, float] | None = None
    for t in range(max(0, from_index), len(bars)):
        brk = _range_brk_for_bar(
            bars[t], timeframe, (range_low, range_high), atr_at_index(atr, t) or 0.0, buffer_atr
        )
        if brk is None:
            continue
        key = (f"{brk.kind}:{brk.direction}", round(brk.broken_level_price, 8))
        if key != last_key:
            events.append(brk)
            last_key = key
    return events


def range_break(
    bars: Sequence[OHLCV],
    timeframe: Timeframe,
    range_low: float,
    range_high: float,
    *,
    buffer_atr: float = BOS_BUFFER_ATR_DEFAULT,
    atr_period: int = ATR_PERIOD_DEFAULT,
    from_index: int = 0,
) -> StructureBreak | None:
    """Der jüngste Range-Bruch (§2.3), sonst ``None``."""
    events = range_breaks(
        bars,
        timeframe,
        range_low,
        range_high,
        buffer_atr=buffer_atr,
        atr_period=atr_period,
        from_index=from_index,
    )
    return events[-1] if events else None


__all__ = [
    "BOS_BUFFER_ATR_DEFAULT",
    "CHOCH_BUFFER_ATR_DEFAULT",
    "MIN_SWINGS_DEFAULT",
    "derive_structure_state",
    "range_break",
    "range_breaks",
    "structure_breaks",
]
