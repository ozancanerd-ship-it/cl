"""Swing High / Low — Fraktal-Methode + HH/HL/LH/LL-Labeling (``primitives.md`` §1).

* Bar ``i`` ist **Swing High**, wenn ``high[i] > high[i±k]`` für alle ``k ∈ 1..L`` (links) bzw.
  ``1..R`` (rechts) — strikt ``>``. Swing Low analog mit ``<``.
* Ein Swing ist **confirmed**, sobald ``R`` weitere Bars geschlossen sind — Bars ohne diese
  Bestätigung sind für Entscheidungen unsichtbar (Look-ahead-Schutz).
* ``min_leg_atr`` filtert Mikro-Swings: aufeinanderfolgende **gegensätzliche** Swings mit
  Abstand ``< min_leg_atr × ATR(tf)`` werden verworfen; zwei gleichartige Swings in Folge werden
  zum extremeren zusammengefasst ⇒ streng alternierende Folge.
* Labeling gegen den vorherigen Swing **gleichen Typs**, Toleranz ``equal_eps_atr × ATR``.
"""

from __future__ import annotations

from collections.abc import Sequence

from trading_agent.core.enums import SwingLabel, SwingType, Timeframe
from trading_agent.core.models import OHLCV
from trading_agent.strategy.primitives.atr import ATR_PERIOD_DEFAULT, atr_at_index, atr_series
from trading_agent.strategy.primitives.models import SwingPoint

SWING_LEFT_DEFAULT = 2
SWING_RIGHT_DEFAULT = 2
MIN_LEG_ATR_DEFAULT = 0.5
EQUAL_EPS_ATR_DEFAULT = 0.05


def _raw_fractals(bars: Sequence[OHLCV], left: int, right: int) -> list[tuple[int, SwingType]]:
    out: list[tuple[int, SwingType]] = []
    n = len(bars)
    for i in range(left, n - right):
        hi, lo = bars[i].high, bars[i].low
        if all(hi > bars[i - k].high for k in range(1, left + 1)) and all(
            hi > bars[i + k].high for k in range(1, right + 1)
        ):
            out.append((i, SwingType.SWING_HIGH))
        if all(lo < bars[i - k].low for k in range(1, left + 1)) and all(
            lo < bars[i + k].low for k in range(1, right + 1)
        ):
            out.append((i, SwingType.SWING_LOW))
    out.sort(key=lambda t: (t[0], 0 if t[1] is SwingType.SWING_HIGH else 1))
    return out


def _price(bar: OHLCV, kind: SwingType) -> float:
    return bar.high if kind is SwingType.SWING_HIGH else bar.low


def detect_swings(
    bars: Sequence[OHLCV],
    timeframe: Timeframe,
    *,
    left: int = SWING_LEFT_DEFAULT,
    right: int = SWING_RIGHT_DEFAULT,
    min_leg_atr: float = MIN_LEG_ATR_DEFAULT,
    equal_eps_atr: float = EQUAL_EPS_ATR_DEFAULT,
    atr_period: int = ATR_PERIOD_DEFAULT,
) -> list[SwingPoint]:
    """Bestätigte, streng alternierende Swings mit HH/HL/LH/LL-Label."""
    if left < 1 or right < 1:
        raise ValueError("left und right müssen >= 1 sein")
    atr = atr_series(bars, atr_period)
    raw = _raw_fractals(bars, left, right)

    # 1) Alternation + Mikro-Filter
    kept: list[tuple[int, SwingType]] = []
    for idx, kind in raw:
        px = _price(bars[idx], kind)
        if not kept:
            kept.append((idx, kind))
            continue
        last_idx, last_kind = kept[-1]
        last_px = _price(bars[last_idx], last_kind)
        if kind is last_kind:
            # gleichartig in Folge -> extremeren behalten
            more_extreme = px > last_px if kind is SwingType.SWING_HIGH else px < last_px
            if more_extreme:
                kept[-1] = (idx, kind)
            continue
        a = atr_at_index(atr, idx)
        if a is not None and abs(px - last_px) < min_leg_atr * a:
            continue  # Leg zu klein -> Mikro-Swing verwerfen
        kept.append((idx, kind))

    # 2) SwingPoint-Objekte + leg_size_atr + Label
    result: list[SwingPoint] = []
    prev_by_type: dict[SwingType, SwingPoint] = {}
    for pos, (idx, kind) in enumerate(kept):
        conf_bar = idx + right
        if conf_bar >= len(bars):
            break  # noch nicht bestätigt
        bar = bars[idx]
        px = _price(bar, kind)
        a = atr_at_index(atr, idx) or 0.0
        leg_atr = 0.0
        if pos > 0 and a > 0:
            prev_idx, prev_kind = kept[pos - 1]
            leg_atr = abs(px - _price(bars[prev_idx], prev_kind)) / a

        label: SwingLabel | None = None
        prev_same = prev_by_type.get(kind)
        if prev_same is not None:
            eps = equal_eps_atr * a
            if abs(px - prev_same.price) <= eps:
                label = SwingLabel.EQUAL
            elif kind is SwingType.SWING_HIGH:
                label = SwingLabel.HH if px > prev_same.price else SwingLabel.LH
            else:
                label = SwingLabel.HL if px > prev_same.price else SwingLabel.LL

        sp = SwingPoint(
            type=kind,
            timeframe=timeframe,
            bar_index=idx,
            timestamp=bar.open_time,
            price=px,
            confirmed_at=bars[conf_bar].close_time,
            leg_size_atr=leg_atr,
            label=label,
        )
        result.append(sp)
        prev_by_type[kind] = sp
    return result


def last_swing(swings: Sequence[SwingPoint], kind: SwingType) -> SwingPoint | None:
    for sp in reversed(swings):
        if sp.type is kind:
            return sp
    return None


__all__ = [
    "EQUAL_EPS_ATR_DEFAULT",
    "MIN_LEG_ATR_DEFAULT",
    "SWING_LEFT_DEFAULT",
    "SWING_RIGHT_DEFAULT",
    "detect_swings",
    "last_swing",
]
