"""Liquidity Level (§4), Equal High/Low (§5), Liquidity Sweep (§6) — ``primitives.md`` 0.1.1.

**Level ≠ Sweep.** Ein ``LiquidityLevel`` ist ein Preis mit vermuteten ruhenden Orders. Ein
``LiquiditySweep`` ist das *Ereignis* „Durchstich **mit** Reclaim". Ohne Reclaim ⇒ ``BROKEN``,
kein Sweep-Objekt.

Look-ahead-frei: alle Funktionen bekommen bereits auf den ``information_cutoff`` gekürzte,
aufsteigend sortierte ``confirmed``-Bars. ATR je Bar nutzt nur Bars ``<= idx``. Ein Level kann
frühestens nach ``formed_at`` (= ``confirmed_at`` des zugrundeliegenden Swings) gesweept werden.

Long/Short-symmetrisch: BUY_SIDE (über Hochs) und SELL_SIDE (unter Tiefs) sind gespiegelt.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from datetime import datetime
from typing import Literal

from trading_agent.core.enums import (
    LiquidityState,
    LiquidityType,
    MarketSide,
    Timeframe,
)
from trading_agent.core.models import OHLCV
from trading_agent.strategy.primitives.atr import ATR_PERIOD_DEFAULT, atr_at_index, atr_series
from trading_agent.strategy.primitives.models import LiquidityLevel, LiquiditySweep, SwingPoint

# ---- Defaults (primitives.example.yaml, PROPOSED DEFAULT / TO-VALIDATE) --------------------

TOUCH_EPS_ATR_DEFAULT = 0.10
TOUCH_SATURATION_DEFAULT = 4
AGE_SATURATION_DEFAULT = 50
STRENGTH_WEIGHTS_DEFAULT: dict[str, float] = {
    "touch": 0.30,
    "age": 0.15,
    "equal": 0.25,
    "session": 0.20,
    "htf": 0.10,
}

EQUAL_TOL_ATR_DEFAULT = 0.10
EQUAL_TOL_PCT_DEFAULT = 0.0005  # 0.05 %
EQUAL_TOL_TICKS_DEFAULT = 2
EQUAL_MIN_SEPARATION_BARS_DEFAULT = 3
EQUAL_MIN_INTERVENING_DEPTH_ATR_DEFAULT = 0.5

SWEEP_MIN_PENETRATION_ATR_DEFAULT = 0.05
SWEEP_MAX_PENETRATION_ATR_DEFAULT = 1.00
SWEEP_MAX_RECLAIM_BARS_DEFAULT = 3
SWEEP_MIN_RECLAIM_ATR_DEFAULT = 0.10
SWEEP_REQUIRE_WICK_DEFAULT = True
SWEEP_MIN_WICK_RATIO_DEFAULT = 1.5


@dataclasses.dataclass(frozen=True, slots=True)
class SweepParams:
    """§6-Parameter (``sweep.*`` in ``primitives.example.yaml`` / setup-Override)."""

    min_penetration_atr: float = SWEEP_MIN_PENETRATION_ATR_DEFAULT
    max_penetration_atr: float = SWEEP_MAX_PENETRATION_ATR_DEFAULT
    max_reclaim_bars: int = SWEEP_MAX_RECLAIM_BARS_DEFAULT
    min_reclaim_atr: float = SWEEP_MIN_RECLAIM_ATR_DEFAULT
    require_wick: bool = SWEEP_REQUIRE_WICK_DEFAULT
    min_wick_ratio: float = SWEEP_MIN_WICK_RATIO_DEFAULT
    atr_period: int = ATR_PERIOD_DEFAULT


_HTF_RANK_ORDER = (Timeframe.M15, Timeframe.M30, Timeframe.H1, Timeframe.H4, Timeframe.D1)

_SESSION_TYPES = {
    LiquidityType.PDH,
    LiquidityType.PDL,
    LiquidityType.PWH,
    LiquidityType.PWL,
    LiquidityType.SESSION_HIGH,
    LiquidityType.SESSION_LOW,
}


# ==========================================================================================
# §5 — Equal High / Equal Low
# ==========================================================================================


def equal_level_clusters(
    swings: Sequence[SwingPoint],
    timeframe: Timeframe,
    *,
    atr: float,
    tick_size: float,
    tol_atr: float = EQUAL_TOL_ATR_DEFAULT,
    tol_pct: float = EQUAL_TOL_PCT_DEFAULT,
    tol_ticks: float = EQUAL_TOL_TICKS_DEFAULT,
    min_separation_bars: int = EQUAL_MIN_SEPARATION_BARS_DEFAULT,
    min_intervening_depth_atr: float = EQUAL_MIN_INTERVENING_DEPTH_ATR_DEFAULT,
) -> list[LiquidityLevel]:
    """Equal-Highs/-Lows-Cluster (>= 2 Swings gleicher Preislage, mit tiefem Zwischen-Swing)."""
    highs = [s for s in swings if s.is_high]
    lows = [s for s in swings if not s.is_high]
    out: list[LiquidityLevel] = []
    out += _clusters_for_side(
        highs,
        lows,
        timeframe,
        MarketSide.BUY_SIDE,
        LiquidityType.EQUAL_HIGHS,
        atr=atr,
        tick_size=tick_size,
        tol_atr=tol_atr,
        tol_pct=tol_pct,
        tol_ticks=tol_ticks,
        min_separation_bars=min_separation_bars,
        min_intervening_depth_atr=min_intervening_depth_atr,
    )
    out += _clusters_for_side(
        lows,
        highs,
        timeframe,
        MarketSide.SELL_SIDE,
        LiquidityType.EQUAL_LOWS,
        atr=atr,
        tick_size=tick_size,
        tol_atr=tol_atr,
        tol_pct=tol_pct,
        tol_ticks=tol_ticks,
        min_separation_bars=min_separation_bars,
        min_intervening_depth_atr=min_intervening_depth_atr,
    )
    out.sort(key=lambda lvl: lvl.formed_at)
    return out


def _clusters_for_side(
    same: list[SwingPoint],
    opposite: list[SwingPoint],
    timeframe: Timeframe,
    side: MarketSide,
    liq_type: LiquidityType,
    *,
    atr: float,
    tick_size: float,
    tol_atr: float,
    tol_pct: float,
    tol_ticks: float,
    min_separation_bars: int,
    min_intervening_depth_atr: float,
) -> list[LiquidityLevel]:
    is_high = side is MarketSide.BUY_SIDE
    out: list[LiquidityLevel] = []
    i = 0
    while i < len(same):
        cluster = [same[i]]
        j = i + 1
        while j < len(same):
            cand = same[j]
            anchor = cluster[-1]
            ref_price = max(m.price for m in cluster) if is_high else min(m.price for m in cluster)
            tol = max(tol_atr * atr, tol_pct * cand.price, tol_ticks * tick_size)
            price_ok = abs(cand.price - ref_price) <= tol
            sep_ok = cand.bar_index - anchor.bar_index >= min_separation_bars
            interv_ok = any(
                anchor.bar_index < lo.bar_index < cand.bar_index
                and lo.leg_size_atr >= min_intervening_depth_atr
                for lo in opposite
            )
            if price_ok and sep_ok and interv_ok:
                cluster.append(cand)
                j += 1
            else:
                break
        if len(cluster) >= 2:
            prices = [m.price for m in cluster]
            ref = max(prices) if is_high else min(prices)
            out.append(
                LiquidityLevel(
                    type=liq_type,
                    side=side,
                    price=ref,
                    timeframe=timeframe,
                    formed_at=max(m.confirmed_at for m in cluster),
                    members=tuple(cluster),
                    spread_atr=(max(prices) - min(prices)) / atr if atr > 0 else 0.0,
                )
            )
            i = j
        else:
            i += 1
    return out


# ==========================================================================================
# §4.1 — Level-Quellen
# ==========================================================================================


def swing_levels(swings: Sequence[SwingPoint], timeframe: Timeframe) -> list[LiquidityLevel]:
    """Jeder bestätigte Swing wird zu einem ``swing_high`` / ``swing_low`` Level."""
    out: list[LiquidityLevel] = []
    for s in swings:
        out.append(
            LiquidityLevel(
                type=LiquidityType.SWING_HIGH if s.is_high else LiquidityType.SWING_LOW,
                side=MarketSide.BUY_SIDE if s.is_high else MarketSide.SELL_SIDE,
                price=s.price,
                timeframe=timeframe,
                formed_at=s.confirmed_at,
                members=(s,),
            )
        )
    return out


def previous_period_levels(
    period_bars: Sequence[OHLCV],
    *,
    kind: Literal["day", "week"],
) -> list[LiquidityLevel]:
    """PDH/PDL bzw. PWH/PWL aus der **letzten abgeschlossenen** D1- (bzw. W1-) Bar.

    ``period_bars`` enthält nur ``confirmed``-Bars ⇒ ``period_bars[-1]`` ist die letzte
    abgeschlossene Periode (§4.1).
    """
    if not period_bars:
        return []
    last = period_bars[-1]
    tf = Timeframe.D1 if kind == "day" else Timeframe.W1
    hi_t = LiquidityType.PDH if kind == "day" else LiquidityType.PWH
    lo_t = LiquidityType.PDL if kind == "day" else LiquidityType.PWL
    return [
        LiquidityLevel(
            type=hi_t,
            side=MarketSide.BUY_SIDE,
            price=last.high,
            timeframe=tf,
            formed_at=last.close_time,
        ),
        LiquidityLevel(
            type=lo_t,
            side=MarketSide.SELL_SIDE,
            price=last.low,
            timeframe=tf,
            formed_at=last.close_time,
        ),
    ]


# ==========================================================================================
# §4.2 — Stärke-Score
# ==========================================================================================


def _htf_rank(tf: Timeframe) -> int:
    if tf in _HTF_RANK_ORDER:
        return _HTF_RANK_ORDER.index(tf)
    return 0 if tf.seconds < Timeframe.M15.seconds else len(_HTF_RANK_ORDER) - 1


def _count_touches(level: LiquidityLevel, bars: Sequence[OHLCV], eps: float) -> int:
    n = 0
    for b in bars:
        if b.close_time <= level.formed_at:
            continue
        if level.side is MarketSide.BUY_SIDE:
            if abs(b.high - level.price) <= eps and b.close <= level.price:
                n += 1
        elif abs(b.low - level.price) <= eps and b.close >= level.price:
            n += 1
    return n


def score_level(
    level: LiquidityLevel,
    observation_bars: Sequence[OHLCV],
    *,
    atr: float,
    touch_eps_atr: float = TOUCH_EPS_ATR_DEFAULT,
    touch_saturation: int = TOUCH_SATURATION_DEFAULT,
    age_saturation: int = AGE_SATURATION_DEFAULT,
    weights: dict[str, float] | None = None,
) -> LiquidityLevel:
    """Setzt ``strength`` (0..1, §4.2) und ``touch_count``. ``observation_bars`` = Bars auf der
    Level-Timeframe (für ``age``) bzw. die Beobachtungsserie für Berührungen."""
    w = weights or STRENGTH_WEIGHTS_DEFAULT
    eps = touch_eps_atr * atr
    touch_count = _count_touches(level, observation_bars, eps)
    age_bars = sum(1 for b in observation_bars if b.close_time > level.formed_at)

    touch_term = min(touch_count / touch_saturation, 1.0) if touch_saturation > 0 else 0.0
    age_term = _clip(age_bars / age_saturation, 0.0, 1.0) if age_saturation > 0 else 0.0
    equal_term = 1.0 if level.is_equal_cluster else 0.0
    session_term = 1.0 if level.type in _SESSION_TYPES else 0.0
    htf_term = _htf_rank(level.timeframe) / 5.0

    strength = _clip(
        w["touch"] * touch_term
        + w["age"] * age_term
        + w["equal"] * equal_term
        + w["session"] * session_term
        + w["htf"] * htf_term,
        0.0,
        1.0,
    )
    return dataclasses.replace(level, strength=strength, touch_count=touch_count)


# ==========================================================================================
# §6 — Liquidity Sweep (Penetration + Reclaim + Wick)  /  §4.3 — Zustand
# ==========================================================================================


def resolve_sweep(
    level: LiquidityLevel,
    bars: Sequence[OHLCV],
    params: SweepParams | None = None,
) -> LiquiditySweep | None:
    """Erster gültiger Sweep von ``level`` auf ``bars`` (= sweep_tf), sonst ``None``.

    ``bars`` sind die sweep-Timeframe-Bars nach ``information_cutoff``-Kürzung.
    """
    p_ = params or SweepParams()
    atr = atr_series(bars, p_.atr_period)
    buy = level.side is MarketSide.BUY_SIDE
    px = level.price

    for pi, p in enumerate(bars):
        if p.close_time <= level.formed_at:
            continue
        ap = atr_at_index(atr, pi) or 0.0
        if ap <= 0:
            continue
        pen = (p.high - px) if buy else (px - p.low)
        if not (p_.min_penetration_atr * ap <= pen <= p_.max_penetration_atr * ap):
            continue

        # Reclaim innerhalb der Frist (p selbst erlaubt)
        for ri in range(pi, min(pi + p_.max_reclaim_bars, len(bars) - 1) + 1):
            r = bars[ri]
            ar = atr_at_index(atr, ri) or ap
            reclaimed = (
                r.close < px - p_.min_reclaim_atr * ar
                if buy
                else r.close > px + p_.min_reclaim_atr * ar
            )
            if not reclaimed:
                continue
            window = bars[pi : ri + 1]
            pen_extreme = max(b.high for b in window) if buy else min(b.low for b in window)
            wr = _wick_ratio(p, buy)
            if p_.require_wick and wr < p_.min_wick_ratio:
                break  # dieser Penetrationsversuch erfüllt die Docht-Bedingung nicht
            depth_atr = (pen_extreme - px) / ap if buy else (px - pen_extreme) / ap
            return LiquiditySweep(
                level=level,
                side=level.side,
                timeframe=level.timeframe,
                penetration_bar=p.open_time,
                penetration_extreme=pen_extreme,
                penetration_depth_atr=depth_atr,
                reclaim_bar=r.open_time,
                reclaim_close=r.close,
                bars_to_reclaim=ri - pi,
                wick_ratio=wr,
            )
    return None


def _wick_ratio(bar: OHLCV, buy_side: bool) -> float:
    body = abs(bar.close - bar.open)
    wick = bar.high - max(bar.open, bar.close) if buy_side else min(bar.open, bar.close) - bar.low
    denom = max(body, bar.range * 1e-6, 1e-12)
    return wick / denom


def classify_level_state(
    level: LiquidityLevel,
    bars: Sequence[OHLCV],
    params: SweepParams | None = None,
) -> tuple[LiquidityState, datetime | None, LiquiditySweep | None]:
    """``UNSWEPT`` / ``SWEPT`` (§6) / ``BROKEN`` (Close jenseits ohne Reclaim in der Frist)."""
    p_ = params or SweepParams()
    sweep = resolve_sweep(level, bars, p_)
    if sweep is not None:
        return LiquidityState.SWEPT, sweep.reclaim_bar, sweep

    buy = level.side is MarketSide.BUY_SIDE
    px = level.price
    for bi, b in enumerate(bars):
        if b.close_time <= level.formed_at:
            continue
        broke = b.close > px if buy else b.close < px
        if not broke:
            continue
        window = bars[bi + 1 : bi + 1 + p_.max_reclaim_bars]
        reclaimed = any((w.close <= px) if buy else (w.close >= px) for w in window)
        if not reclaimed:
            return LiquidityState.BROKEN, b.close_time, None
    return LiquidityState.UNSWEPT, None, None


def apply_state(
    level: LiquidityLevel,
    bars: Sequence[OHLCV],
    params: SweepParams | None = None,
) -> tuple[LiquidityLevel, LiquiditySweep | None]:
    state, swept_at, sweep = classify_level_state(level, bars, params)
    return dataclasses.replace(level, state=state, swept_at=swept_at), sweep


def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


__all__ = [
    "AGE_SATURATION_DEFAULT",
    "EQUAL_MIN_INTERVENING_DEPTH_ATR_DEFAULT",
    "EQUAL_MIN_SEPARATION_BARS_DEFAULT",
    "EQUAL_TOL_ATR_DEFAULT",
    "STRENGTH_WEIGHTS_DEFAULT",
    "SWEEP_MAX_PENETRATION_ATR_DEFAULT",
    "SWEEP_MAX_RECLAIM_BARS_DEFAULT",
    "SWEEP_MIN_PENETRATION_ATR_DEFAULT",
    "SWEEP_MIN_RECLAIM_ATR_DEFAULT",
    "SWEEP_MIN_WICK_RATIO_DEFAULT",
    "TOUCH_EPS_ATR_DEFAULT",
    "SweepParams",
    "apply_state",
    "classify_level_state",
    "equal_level_clusters",
    "previous_period_levels",
    "resolve_sweep",
    "score_level",
    "swing_levels",
]
