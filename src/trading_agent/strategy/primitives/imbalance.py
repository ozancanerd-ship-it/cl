"""Displacement (§7), FVG (§8), IFVG (§9) + Mitigation (§11) — ``primitives.md`` 0.1.1.

* **FVG** = 3-Kerzen-Imbalance (``low[3] > high[1]`` bullisch / ``high[3] < low[1]`` bearisch),
  Zone ``[high[1], low[3]]`` bzw. ``[high[3], low[1]]``, mit Mindestgröße.
* **Displacement** = 1..``max_bars`` impulsive Bars: Netto-Move ``≥ min_atr·ATR``, Körperdominanz
  ``≥ min_body_ratio``, gerichtet (``≤ max_counter_bars`` Gegenkerzen), erzeugt **≥ 1 FVG**
  gleicher Richtung.
* **IFVG** = eine FVG, die per ``confirmed close`` auf der **Gegenseite** durchbrochen wurde;
  gleiche Zone, invertierte Polarität, ``max_age_bars`` ab ``flipped_at``.
* **Mitigation** (§11): ``fill_fraction`` = Anteil der Zonenhöhe, den der Preis seit Entstehung
  durchlaufen hat ⇒ ``UNMITIGATED / PARTIAL / MITIGATED / STALE / INVERTED``.

Look-ahead-frei: alle Funktionen bekommen bereits auf ``information_cutoff`` gekürzte
``confirmed``-Bars; ATR je Bar nur aus Bars ``<= idx``; der Zustand einer Zone ist der Stand
**am letzten Bar**. Long/Short-symmetrisch (BULLISH ↔ BEARISH).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from datetime import datetime

from trading_agent.core.enums import Polarity, Timeframe, ZoneState
from trading_agent.core.models import OHLCV
from trading_agent.strategy.primitives.atr import ATR_PERIOD_DEFAULT, atr_at_index, atr_series
from trading_agent.strategy.primitives.models import FVG, IFVG, Displacement, StructureBreak

# ---- Defaults (primitives.example.yaml) ---------------------------------------------------

FVG_MIN_SIZE_ATR_DEFAULT = 0.20
FVG_MIN_SIZE_PCT_DEFAULT = 0.0005  # 0.05 %
FVG_MIN_SIZE_TICKS_DEFAULT = 4.0
FVG_MAX_AGE_BARS_DEFAULT = 50

DISP_MAX_BARS_DEFAULT = 3
DISP_MIN_ATR_DEFAULT = 1.5
DISP_MIN_BODY_RATIO_DEFAULT = 0.60
DISP_MAX_COUNTER_BARS_DEFAULT = 0

IFVG_MIN_CLOSE_THROUGH_ATR_DEFAULT = 0.0
IFVG_MAX_AGE_BARS_DEFAULT = 50

MITIGATION_TOUCH_THRESHOLD_DEFAULT = 0.0
MITIGATION_CONSUMED_THRESHOLD_DEFAULT = 0.5


@dataclasses.dataclass(frozen=True, slots=True)
class FvgParams:
    min_size_atr: float = FVG_MIN_SIZE_ATR_DEFAULT
    min_size_pct: float = FVG_MIN_SIZE_PCT_DEFAULT
    min_size_ticks: float = FVG_MIN_SIZE_TICKS_DEFAULT
    max_age_bars: int = FVG_MAX_AGE_BARS_DEFAULT
    touch_threshold: float = MITIGATION_TOUCH_THRESHOLD_DEFAULT
    consumed_threshold: float = MITIGATION_CONSUMED_THRESHOLD_DEFAULT
    invert_close_through_atr: float = IFVG_MIN_CLOSE_THROUGH_ATR_DEFAULT
    atr_period: int = ATR_PERIOD_DEFAULT


@dataclasses.dataclass(frozen=True, slots=True)
class DisplacementParams:
    max_bars: int = DISP_MAX_BARS_DEFAULT
    min_atr: float = DISP_MIN_ATR_DEFAULT
    min_body_ratio: float = DISP_MIN_BODY_RATIO_DEFAULT
    max_counter_bars: int = DISP_MAX_COUNTER_BARS_DEFAULT
    atr_period: int = ATR_PERIOD_DEFAULT


@dataclasses.dataclass(frozen=True, slots=True)
class IfvgParams:
    min_close_through_atr: float = IFVG_MIN_CLOSE_THROUGH_ATR_DEFAULT
    max_age_bars: int = IFVG_MAX_AGE_BARS_DEFAULT
    touch_threshold: float = MITIGATION_TOUCH_THRESHOLD_DEFAULT
    consumed_threshold: float = MITIGATION_CONSUMED_THRESHOLD_DEFAULT
    atr_period: int = ATR_PERIOD_DEFAULT


@dataclasses.dataclass(frozen=True, slots=True)
class ImbalanceResult:
    fvgs: tuple[FVG, ...]
    displacements: tuple[Displacement, ...]
    ifvgs: tuple[IFVG, ...]


# ==========================================================================================
# §11 — Mitigation
# ==========================================================================================


def mitigation_fill(
    zone_low: float, zone_high: float, polarity: Polarity, bars_after: Sequence[OHLCV]
) -> float:
    """Anteil der Zonenhöhe, den der Preis seit Entstehung in die Zone getrieben hat (0..1)."""
    h = zone_high - zone_low
    if h <= 0 or not bars_after:
        return 0.0
    if polarity is Polarity.BULLISH:  # Support, Preis kommt von oben
        pen = (zone_high - min(b.low for b in bars_after)) / h
    else:  # Resistance, Preis kommt von unten
        pen = (max(b.high for b in bars_after) - zone_low) / h
    return max(0.0, min(1.0, pen))


def zone_state(
    fill_fraction: float,
    age_bars: int,
    inverted: bool,
    *,
    max_age_bars: int,
    touch_threshold: float,
    consumed_threshold: float,
) -> ZoneState:
    """INVERTED > MITIGATED > STALE > PARTIAL > UNMITIGATED."""
    if inverted:
        return ZoneState.INVERTED
    if fill_fraction >= consumed_threshold:
        return ZoneState.MITIGATED
    if age_bars > max_age_bars:
        return ZoneState.STALE
    if fill_fraction > touch_threshold:
        return ZoneState.PARTIAL
    return ZoneState.UNMITIGATED


def _inversion_bar(
    zone_low: float,
    zone_high: float,
    direction: Polarity,
    bars: Sequence[OHLCV],
    start_idx: int,
    atr: list[float | None],
    min_close_through_atr: float,
) -> tuple[int, datetime] | None:
    """Erster ``confirmed close`` jenseits der Gegenseite der Zone (§9)."""
    for i in range(start_idx, len(bars)):
        a = atr_at_index(atr, i) or 0.0
        broke = (
            bars[i].close < zone_low - min_close_through_atr * a
            if direction is Polarity.BULLISH
            else bars[i].close > zone_high + min_close_through_atr * a
        )
        if broke:
            return i, bars[i].close_time
    return None


# ==========================================================================================
# §8 — FVG
# ==========================================================================================


def find_fvgs(
    bars: Sequence[OHLCV],
    timeframe: Timeframe,
    *,
    tick_size: float,
    params: FvgParams | None = None,
) -> list[FVG]:
    """Alle gültigen FVGs (3-Kerzen-Muster + Mindestgröße), jeweils mit Zustand am letzten Bar."""
    p_ = params or FvgParams()
    atr = atr_series(bars, p_.atr_period)
    n = len(bars)
    out: list[FVG] = []
    for k in range(2, n):
        b1, b3 = bars[k - 2], bars[k]
        if b3.low > b1.high:
            direction, zl, zh = Polarity.BULLISH, b1.high, b3.low
        elif b3.high < b1.low:
            direction, zl, zh = Polarity.BEARISH, b3.high, b1.low
        else:
            continue
        ak = atr_at_index(atr, k) or 0.0
        min_size = max(
            p_.min_size_atr * ak, p_.min_size_pct * b3.close, p_.min_size_ticks * tick_size
        )
        if zh - zl < min_size:
            continue

        after = bars[k + 1 :]
        fill = mitigation_fill(zl, zh, direction, after)
        inv = _inversion_bar(zl, zh, direction, bars, k + 1, atr, p_.invert_close_through_atr)
        age = (n - 1) - k
        state = zone_state(
            fill,
            age,
            inv is not None,
            max_age_bars=p_.max_age_bars,
            touch_threshold=p_.touch_threshold,
            consumed_threshold=p_.consumed_threshold,
        )
        out.append(
            FVG(
                direction=direction,
                timeframe=timeframe,
                zone_low=zl,
                zone_high=zh,
                created_bar=b3.close_time,
                bar_index=k,
                state=state,
                fill_fraction=fill,
                age_bars=age,
            )
        )
    return out


# ==========================================================================================
# §7 — Displacement
# ==========================================================================================


def find_displacements(
    bars: Sequence[OHLCV],
    timeframe: Timeframe,
    fvgs: Sequence[FVG],
    *,
    params: DisplacementParams | None = None,
    breaks: Sequence[StructureBreak] = (),
) -> list[Displacement]:
    """Nicht-überlappende Displacements (kleinstes qualifizierendes ``n`` je Endbar)."""
    p_ = params or DisplacementParams()
    atr = atr_series(bars, p_.atr_period)
    bar_idx = {b.open_time: i for i, b in enumerate(bars)}
    out: list[Displacement] = []
    last_end = -1
    for e in range(len(bars)):
        chosen = _best_window(bars, fvgs, atr, e, p_)
        if chosen is None:
            continue
        s, direction, net_atr, body_ratio, seq_fvgs = chosen
        if s <= last_end:
            continue  # überlappt ein bereits emittiertes Displacement
        caused = next(
            (
                b
                for b in breaks
                if b.break_bar_timestamp in bar_idx and s <= bar_idx[b.break_bar_timestamp] <= e
            ),
            None,
        )
        out.append(
            Displacement(
                direction=direction,
                timeframe=timeframe,
                start_bar=bars[s].open_time,
                end_bar=bars[e].open_time,
                bars=e - s + 1,
                net_move_atr=net_atr,
                body_ratio=body_ratio,
                start_index=s,
                end_index=e,
                fvgs=tuple(seq_fvgs),
                caused_structure_break=caused,
            )
        )
        last_end = e
    return out


def _best_window(
    bars: Sequence[OHLCV],
    fvgs: Sequence[FVG],
    atr: list[float | None],
    e: int,
    p_: DisplacementParams,
) -> tuple[int, Polarity, float, float, list[FVG]] | None:
    ae = atr_at_index(atr, e) or 0.0
    if ae <= 0:
        return None
    for n in range(1, p_.max_bars + 1):
        s = e - n + 1
        if s < 0:
            break
        seg = bars[s : e + 1]
        net = abs(bars[e].close - bars[s].open)
        if net < p_.min_atr * ae:
            continue
        range_sum = sum(b.range for b in seg)
        body_sum = sum(abs(b.close - b.open) for b in seg)
        if range_sum <= 0 or body_sum / range_sum < p_.min_body_ratio:
            continue
        direction = Polarity.BULLISH if bars[e].close >= bars[s].open else Polarity.BEARISH
        sign = 1.0 if direction is Polarity.BULLISH else -1.0
        counter = sum(1 for b in seg if (b.close - b.open) * sign < 0)
        if counter > p_.max_counter_bars:
            continue
        seq_fvgs = [f for f in fvgs if s + 1 <= f.bar_index <= e + 1 and f.direction is direction]
        if not seq_fvgs:
            continue
        return s, direction, net / ae, body_sum / range_sum, seq_fvgs
    return None


def link_displacement(fvgs: Sequence[FVG], displacements: Sequence[Displacement]) -> list[FVG]:
    """Setzt ``from_displacement=True`` für FVGs, die in einem Displacement gleicher Richtung liegen."""
    keys = {(f.bar_index, f.direction) for d in displacements for f in d.fvgs}
    return [
        dataclasses.replace(f, from_displacement=True) if (f.bar_index, f.direction) in keys else f
        for f in fvgs
    ]


# ==========================================================================================
# §9 — IFVG
# ==========================================================================================


def find_ifvgs(
    fvgs: Sequence[FVG],
    bars: Sequence[OHLCV],
    timeframe: Timeframe,
    *,
    params: IfvgParams | None = None,
) -> list[IFVG]:
    """Für jede FVG, die per Close auf der Gegenseite durchbrochen wurde: eine IFVG."""
    p_ = params or IfvgParams()
    atr = atr_series(bars, p_.atr_period)
    n = len(bars)
    out: list[IFVG] = []
    for f in fvgs:
        if f.bar_index < 0:
            continue
        flip = _inversion_bar(
            f.zone_low,
            f.zone_high,
            f.direction,
            bars,
            f.bar_index + 1,
            atr,
            p_.min_close_through_atr,
        )
        if flip is None:
            continue
        fi, fts = flip
        inv_dir = f.direction.opposite
        after = bars[fi + 1 :]
        fill = mitigation_fill(f.zone_low, f.zone_high, inv_dir, after)
        age = (n - 1) - fi
        state = zone_state(
            fill,
            age,
            False,
            max_age_bars=p_.max_age_bars,
            touch_threshold=p_.touch_threshold,
            consumed_threshold=p_.consumed_threshold,
        )
        out.append(
            IFVG(
                origin_fvg=f,
                direction=inv_dir,
                timeframe=timeframe,
                zone_low=f.zone_low,
                zone_high=f.zone_high,
                flipped_at=fts,
                flip_bar_index=fi,
                state=state,
                fill_fraction=fill,
                age_bars=age,
            )
        )
    return out


# ==========================================================================================
# Orchestrierung
# ==========================================================================================


def analyze_imbalance(
    bars: Sequence[OHLCV],
    timeframe: Timeframe,
    *,
    tick_size: float,
    fvg_params: FvgParams | None = None,
    disp_params: DisplacementParams | None = None,
    ifvg_params: IfvgParams | None = None,
    structure_breaks: Sequence[StructureBreak] = (),
) -> ImbalanceResult:
    fvgs = find_fvgs(bars, timeframe, tick_size=tick_size, params=fvg_params)
    disps = find_displacements(bars, timeframe, fvgs, params=disp_params, breaks=structure_breaks)
    fvgs = link_displacement(fvgs, disps)
    ifvgs = find_ifvgs(fvgs, bars, timeframe, params=ifvg_params)
    return ImbalanceResult(tuple(fvgs), tuple(disps), tuple(ifvgs))


__all__ = [
    "DISP_MIN_ATR_DEFAULT",
    "FVG_MIN_SIZE_ATR_DEFAULT",
    "DisplacementParams",
    "FvgParams",
    "IfvgParams",
    "ImbalanceResult",
    "analyze_imbalance",
    "find_displacements",
    "find_fvgs",
    "find_ifvgs",
    "link_displacement",
    "mitigation_fill",
    "zone_state",
]
