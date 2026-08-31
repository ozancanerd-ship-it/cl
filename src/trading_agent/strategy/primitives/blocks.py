"""Order Block (§10) + Breaker (§12) — ``primitives.md`` 0.1.1.

**Order Block** — bullisch = die letzte Bar mit ``close < open`` **unmittelbar vor** einem
bullischen Displacement (§7), das innerhalb ``ob.max_bars_to_break`` Bars einen ``StructureBreak``
(BOS/CHoCH, §2/§3) **in Displacement-Richtung** verursacht. Bearisch spiegelbildlich.

Zone je ``ob.zone``: ``full_range`` (Default) = ``[low, high]`` · ``body`` =
``[min(o,c), max(o,c)]`` · ``open_to_extreme`` = bull ``[low, open]`` / bear ``[open, high]``.

**Breaker** (§12) — ein Order Block, dessen schützende Struktur per **BOS** gebrochen wurde und der
daraufhin die **Polarität umkehrt**: ein *bearischer* OB an einem Hoch wird zum *bullischen*
Breaker, sobald ein bullischer BOS über ``OB.zone_high + breaker.buffer_atr × ATR`` schließt.
Gleiche Zone ``[OB.zone_low, OB.zone_high]``, invertierte Wirkung; ``max_age_bars`` ab
``flipped_at`` (= Close-Zeit der BOS-Bar).

Zustand über die **bestehende** Mitigation-Logik (``imbalance.mitigation_fill`` / ``zone_state``):
OB ab der Bar nach dem Strukturbruch, Breaker ab der Bar nach dem Flip-BOS. Zustandsmenge
``UNMITIGATED | PARTIAL | MITIGATED | STALE``.

Look-ahead-frei: Displacements/Breaks sind bereits look-ahead-frei; die Zone ist eine reine
Funktion der OB-Bar; der Zustand ist der Stand am letzten Bar. Long/Short-symmetrisch.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence

from trading_agent.core.enums import (
    OrderBlockZone,
    Polarity,
    StructureBreakKind,
    Timeframe,
    ZoneState,
)
from trading_agent.core.models import OHLCV
from trading_agent.strategy.primitives.atr import ATR_PERIOD_DEFAULT, atr_at_index, atr_series
from trading_agent.strategy.primitives.imbalance import (
    FVG_MAX_AGE_BARS_DEFAULT,
    MITIGATION_CONSUMED_THRESHOLD_DEFAULT,
    MITIGATION_TOUCH_THRESHOLD_DEFAULT,
    mitigation_fill,
    zone_state,
)
from trading_agent.strategy.primitives.models import (
    Breaker,
    Displacement,
    OrderBlock,
    StructureBreak,
)

OB_MAX_BARS_TO_BREAK_DEFAULT = 5
# §10 nennt keinen eigenen Alterswert -> an §8/§9 angelehnt (PROPOSED DEFAULT).
OB_MAX_AGE_BARS_DEFAULT = FVG_MAX_AGE_BARS_DEFAULT

BREAKER_BUFFER_ATR_DEFAULT = 0.0
BREAKER_MAX_AGE_BARS_DEFAULT = 50


@dataclasses.dataclass(frozen=True, slots=True)
class ObParams:
    zone: OrderBlockZone = OrderBlockZone.FULL_RANGE
    max_bars_to_break: int = OB_MAX_BARS_TO_BREAK_DEFAULT
    max_age_bars: int = OB_MAX_AGE_BARS_DEFAULT
    touch_threshold: float = MITIGATION_TOUCH_THRESHOLD_DEFAULT
    consumed_threshold: float = MITIGATION_CONSUMED_THRESHOLD_DEFAULT
    atr_period: int = ATR_PERIOD_DEFAULT


def _zone_bounds(bar: OHLCV, bullish_ob: bool, mode: OrderBlockZone) -> tuple[float, float]:
    if mode is OrderBlockZone.FULL_RANGE:
        return bar.low, bar.high
    if mode is OrderBlockZone.BODY:
        return min(bar.open, bar.close), max(bar.open, bar.close)
    # open_to_extreme
    return (bar.low, bar.open) if bullish_ob else (bar.open, bar.high)


def find_order_blocks(
    bars: Sequence[OHLCV],
    timeframe: Timeframe,
    displacements: Sequence[Displacement],
    structure_breaks: Sequence[StructureBreak],
    *,
    params: ObParams | None = None,
) -> list[OrderBlock]:
    """Ein Order Block je strukturbrechendem Displacement mit gültiger Gegenkerze davor."""
    p_ = params or ObParams()
    n = len(bars)
    bar_idx = {b.open_time: i for i, b in enumerate(bars)}
    out: list[OrderBlock] = []

    for d in displacements:
        ob_idx = d.start_index - 1  # Displacement-Kopplung: Impuls DIREKT nach der OB-Bar (§10.2)
        if ob_idx < 0:
            continue
        ob = bars[ob_idx]
        bullish_ob = d.direction is Polarity.BULLISH
        # Gegenkerze: bull-OB braucht Down-Close, bear-OB braucht Up-Close (Doji zählt nicht)
        if bullish_ob and not ob.close < ob.open:
            continue
        if not bullish_ob and not ob.close > ob.open:
            continue

        # Strukturkopplung: frühester Break in Displacement-Richtung im Fenster nach der OB-Bar
        brk: StructureBreak | None = None
        brk_i = -1
        for b in structure_breaks:
            bi = bar_idx.get(b.break_bar_timestamp, -1)
            if bi < 0 or b.direction is not d.direction:
                continue
            if ob_idx + 1 <= bi <= ob_idx + p_.max_bars_to_break and (brk is None or bi < brk_i):
                brk, brk_i = b, bi
        if brk is None:
            continue

        zl, zh = _zone_bounds(ob, bullish_ob, p_.zone)
        if zh <= zl:
            continue  # entartete Zone (z. B. Body einer Doji)

        after = bars[brk_i + 1 :]
        fill = mitigation_fill(zl, zh, d.direction, after)
        age = (n - 1) - brk_i
        state = zone_state(
            fill,
            age,
            False,
            max_age_bars=p_.max_age_bars,
            touch_threshold=p_.touch_threshold,
            consumed_threshold=p_.consumed_threshold,
        )
        out.append(
            OrderBlock(
                direction=d.direction,
                timeframe=timeframe,
                zone_low=zl,
                zone_high=zh,
                ob_bar=ob.open_time,
                bar_index=ob_idx,
                break_ref=brk,
                displacement_ref=d,
                state=state,
                fill_fraction=fill,
                age_bars=age,
            )
        )
    return out


def unmitigated(order_blocks: Sequence[OrderBlock]) -> list[OrderBlock]:
    """§10.3-Filter: nur unberührte OBs (die einzigen, die das Setup als Entry-Zone nutzt)."""
    return [ob for ob in order_blocks if ob.state is ZoneState.UNMITIGATED]


# ==========================================================================================
# §12 — Breaker
# ==========================================================================================


@dataclasses.dataclass(frozen=True, slots=True)
class BreakerParams:
    buffer_atr: float = BREAKER_BUFFER_ATR_DEFAULT
    max_age_bars: int = BREAKER_MAX_AGE_BARS_DEFAULT
    require_bos: bool = True  # §12: "per bullischem BOS (§2)"
    touch_threshold: float = MITIGATION_TOUCH_THRESHOLD_DEFAULT
    consumed_threshold: float = MITIGATION_CONSUMED_THRESHOLD_DEFAULT
    atr_period: int = ATR_PERIOD_DEFAULT


def find_breakers(
    bars: Sequence[OHLCV],
    timeframe: Timeframe,
    order_blocks: Sequence[OrderBlock],
    structure_breaks: Sequence[StructureBreak],
    *,
    params: BreakerParams | None = None,
) -> list[Breaker]:
    """Je Order Block der erste gegenläufige BOS, der die schützende Struktur bricht ⇒ Breaker."""
    p_ = params or BreakerParams()
    atr = atr_series(bars, p_.atr_period)
    bar_idx = {b.open_time: i for i, b in enumerate(bars)}
    n = len(bars)
    out: list[Breaker] = []

    for ob in order_blocks:
        flip_dir = ob.direction.opposite  # bearischer OB -> bullischer Breaker (und umgekehrt)
        best: StructureBreak | None = None
        best_i = -1
        for b in structure_breaks:
            bi = bar_idx.get(b.break_bar_timestamp, -1)
            if bi <= ob.bar_index or b.direction is not flip_dir:
                continue
            if p_.require_bos and b.kind is not StructureBreakKind.BOS:
                continue
            a = atr_at_index(atr, bi) or 0.0
            broke = (
                b.break_close > ob.zone_high + p_.buffer_atr * a
                if flip_dir is Polarity.BULLISH
                else b.break_close < ob.zone_low - p_.buffer_atr * a
            )
            if broke and (best is None or bi < best_i):
                best, best_i = b, bi
        if best is None:
            continue

        after = bars[best_i + 1 :]
        fill = mitigation_fill(ob.zone_low, ob.zone_high, flip_dir, after)
        age = (n - 1) - best_i
        state = zone_state(
            fill,
            age,
            False,
            max_age_bars=p_.max_age_bars,
            touch_threshold=p_.touch_threshold,
            consumed_threshold=p_.consumed_threshold,
        )
        out.append(
            Breaker(
                origin_ob=ob,
                direction=flip_dir,
                timeframe=timeframe,
                zone_low=ob.zone_low,
                zone_high=ob.zone_high,
                flipped_at=bars[best_i].close_time,  # Bestätigung = Close der BOS-Bar
                flip_bar_index=best_i,
                flip_break_ref=best,
                state=state,
                fill_fraction=fill,
                age_bars=age,
            )
        )
    return out


__all__ = [
    "BREAKER_BUFFER_ATR_DEFAULT",
    "OB_MAX_BARS_TO_BREAK_DEFAULT",
    "BreakerParams",
    "ObParams",
    "find_breakers",
    "find_order_blocks",
    "unmitigated",
]
