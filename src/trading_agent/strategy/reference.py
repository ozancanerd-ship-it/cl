"""ReferenceMAStrategy — a trivial SMA-crossover strategy.

**This is NOT the real trading strategy.** It exists only to exercise the Phase 2 machinery
(event bus, sizing, fees/spread/slippage/funding, partial fills, liquidation, MFE/MAE, walk-
forward, Monte-Carlo). The real strategy is ``docs/strategy/`` (``strategy_version 0.1.0``) and
arrives in Phase 3 via the same ``evaluate()`` contract.
"""

from __future__ import annotations

from dataclasses import dataclass

from trading_agent.core.enums import Side
from trading_agent.core.models import OHLCV


@dataclass(frozen=True, slots=True)
class ReferenceSignal:
    side: Side
    sl_price: float
    tp_price: float
    reason: str = "ma_cross"


@dataclass(frozen=True, slots=True)
class Flat:
    reason: str = "ma_cross_exit"


def _sma(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def _atr(bars: list[OHLCV], period: int) -> float | None:
    if len(bars) < period + 1:
        return None
    trs: list[float] = []
    for i in range(-period, 0):
        h, low, pc = bars[i].high, bars[i].low, bars[i - 1].close
        trs.append(max(h - low, abs(h - pc), abs(low - pc)))
    return sum(trs) / period


class ReferenceMAStrategy:
    def __init__(
        self,
        *,
        fast: int = 10,
        slow: int = 30,
        atr_period: int = 14,
        sl_atr: float = 1.5,
        tp_atr: float = 3.0,
        allow_short: bool = True,
    ) -> None:
        self.fast = fast
        self.slow = slow
        self.atr_period = atr_period
        self.sl_atr = sl_atr
        self.tp_atr = tp_atr
        self.allow_short = allow_short

    def evaluate(self, bars: list[OHLCV], has_position: bool) -> ReferenceSignal | Flat | None:
        """``bars`` are confirmed bars up to and including the one that just closed."""
        if len(bars) < self.slow + 2:
            return None
        closes = [b.close for b in bars]
        atr = _atr(bars, self.atr_period)
        if atr is None or atr <= 0:
            return None

        fast_now, fast_prev = _sma(closes, self.fast), _sma(closes[:-1], self.fast)
        slow_now, slow_prev = _sma(closes, self.slow), _sma(closes[:-1], self.slow)
        if None in (fast_now, fast_prev, slow_now, slow_prev):
            return None
        assert fast_now is not None and fast_prev is not None
        assert slow_now is not None and slow_prev is not None

        crossed_up = fast_prev <= slow_prev and fast_now > slow_now
        crossed_down = fast_prev >= slow_prev and fast_now < slow_now
        price = closes[-1]

        if has_position:
            if crossed_up or crossed_down:
                return Flat()
            return None

        if crossed_up:
            return ReferenceSignal(
                side=Side.BUY,
                sl_price=price - self.sl_atr * atr,
                tp_price=price + self.tp_atr * atr,
            )
        if crossed_down and self.allow_short:
            return ReferenceSignal(
                side=Side.SELL,
                sl_price=price + self.sl_atr * atr,
                tp_price=price - self.tp_atr * atr,
            )
        return None


__all__ = ["Flat", "ReferenceMAStrategy", "ReferenceSignal"]
