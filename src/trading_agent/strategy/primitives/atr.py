"""ATR — Average True Range, Wilder-geglättet (``primitives.md`` §0.2).

``TR = max(high-low, |high-prev_close|, |low-prev_close|)``.
``ATR[p]`` (Seed) = einfacher Mittelwert der ersten ``p`` TR-Werte; danach Wilder-Rekursion
``ATR_t = (ATR_{t-1}*(p-1) + TR_t) / p``.

Nur ``confirmed``-Bars — der Aufrufer übergibt bereits nur geschlossene Bars.
Braucht ``>= period + 1`` Bars (die erste TR benötigt einen ``prev_close``).
``primitives.atr.period`` ist THEORY-FIXED ``14``.
"""

from __future__ import annotations

from collections.abc import Sequence

from trading_agent.core.models import OHLCV

ATR_PERIOD_DEFAULT = 14


def true_ranges(bars: Sequence[OHLCV]) -> list[float]:
    """TR-Serie, Länge ``len(bars) - 1`` (ab der zweiten Bar)."""
    out: list[float] = []
    for i in range(1, len(bars)):
        h, low, pc = bars[i].high, bars[i].low, bars[i - 1].close
        out.append(max(h - low, abs(h - pc), abs(low - pc)))
    return out


def atr_series(bars: Sequence[OHLCV], period: int = ATR_PERIOD_DEFAULT) -> list[float | None]:
    """ATR-Wert je Bar (aligned auf ``bars``); ``None`` bis genug Historie vorliegt.

    ``result[i]`` ist der ATR **am Close von** ``bars[i]`` — nutzt nur Bars ``<= i``.
    """
    if period < 1:
        raise ValueError("period muss >= 1 sein")
    result: list[float | None] = [None] * len(bars)
    trs = true_ranges(bars)  # trs[k] gehört zu bars[k+1]
    if len(trs) < period:
        return result
    seed = sum(trs[:period]) / period
    result[period] = seed  # bars[period] ist die (period+1)-te Bar
    prev = seed
    for k in range(period, len(trs)):
        prev = (prev * (period - 1) + trs[k]) / period
        result[k + 1] = prev
    return result


def atr(bars: Sequence[OHLCV], period: int = ATR_PERIOD_DEFAULT) -> float | None:
    """ATR am Close der letzten Bar, oder ``None`` bei zu wenig Historie."""
    s = atr_series(bars, period)
    return s[-1] if s else None


def atr_at_index(series: list[float | None], idx: int) -> float | None:
    """ATR-Wert, der am Close von Bar ``idx`` bekannt war (letzter nicht-``None`` bis ``idx``)."""
    if not series:
        return None
    for j in range(min(idx, len(series) - 1), -1, -1):
        if series[j] is not None:
            return series[j]
    return None


__all__ = ["ATR_PERIOD_DEFAULT", "atr", "atr_at_index", "atr_series", "true_ranges"]
