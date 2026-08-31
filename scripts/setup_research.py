#!/usr/bin/env python
"""Setup-Typ-Forschung — findet einen **zweiten** robusten Swing-Setup-Typ neben SMC-SWEEP-REV-01.

Frage (Masterplan): Erkennt die Strategie auf XAUUSDT tatsächlich valide Chancen? Der aktuelle
Sweep-Reversal deckt nur einen schmalen Kontext ab (Stufe-B: keine OOS-Edge). Dieses Skript
testet **mehrere strukturell verschiedene Setup-Typen** rein datenbasiert:

    S0  Liquidity Sweep + Reversal        (baseline-analog, vereinfacht)
    S1  Breakout + Retest
    S2  Trend Pullback / Continuation
    S3  HTF Structure Break + LTF Confirmation

Alle laufen auf **H4** (Setup + Entry + Management) mit **D1** als HTF-Regime — Swing-Fokus.
Kein Look-ahead: an H4-Bar i sieht ein Detektor nur Swings/Breaks mit ``confirmed_at``/
``break_bar_timestamp <= h4[i].close_time`` und D1-Zustand zum selben Cutoff; Entry = h4[i+1].open;
Exits ausschließlich vorwärts; Worst-Case-Fill (SL vor TP bei Bar-Overlap).

Swings/Structure werden **einmal je Symbol** über die volle Serie berechnet und per ``confirmed_at``
Point-in-Time gefiltert — identisch zur inkrementellen Rechnung (Swings werden nie revidiert),
aber ~100× schneller.

Je Setup: Expectancy · PF · Win-Rate · MaxDD · Sharpe · Sortino · MFE · MAE
        + IS/OOS-Split · Walk-Forward · Monte-Carlo · Symbol-Stabilität.
RR wird auf **IS** gewählt, OOS/WF/MC an genau diesem RR berichtet (kein Peeking).

    uv run python scripts/setup_research.py --repo data/repository_real \
        --symbols XAUUSDT BTCUSDT ETHUSDT SOLUSDT BNBUSDT XRPUSDT DOGEUSDT \
        --split 2025-06-01 --out data/repository_real/setup_research.json
"""

from __future__ import annotations

import argparse
import bisect
import json
import math
from bisect import bisect_right
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from trading_agent.core.enums import (
    Polarity,
    RegimeDirectional,
    Side,
    StructureBreakKind,
    SwingType,
    Timeframe,
)
from trading_agent.core.models import OHLCV
from trading_agent.core.time import parse_timestamp
from trading_agent.data.repository import MarketDataRepository
from trading_agent.journal.ledger import TradeRecord
from trading_agent.research.metrics import compute_metrics
from trading_agent.research.robustness import monte_carlo
from trading_agent.research.validation import symbol_stability, time_stability, walk_forward_folds
from trading_agent.strategy.primitives.atr import atr_series
from trading_agent.strategy.primitives.models import StructureBreak, SwingPoint
from trading_agent.strategy.primitives.structure import derive_structure_state, structure_breaks
from trading_agent.strategy.primitives.swings import detect_swings

_H4 = Timeframe.H4
_D1 = Timeframe.D1
_RRS = (1.5, 2.0, 2.5, 3.0)
_MAX_HOLD_H4 = 60  # ~10 Handelstage
_ATR_P = 14
_SWING_L = 2
_SWING_R = 2
_D1_BOS_LOOKBACK = 20


@dataclass(frozen=True, slots=True)
class Signal:
    at_index: int
    direction: int  # +1 long, -1 short
    stop: float
    reason: str


# --------------------------------------------------------------------------------- Symbol-Kontext


@dataclass(slots=True)
class Ctx:
    symbol: str
    h4: list[OHLCV]
    atr: list[float | None]
    _h4_sw: list[SwingPoint] = field(default_factory=list)  # confirmed_at aufsteigend
    _h4_sw_conf: list[datetime] = field(default_factory=list)
    _h4_brk: list[StructureBreak] = field(default_factory=list)
    _h4_brk_ts: list[datetime] = field(default_factory=list)
    _d1_state: list[RegimeDirectional] = field(default_factory=list)  # je H4-Bar
    _d1_bos: list[int] = field(default_factory=list)  # je H4-Bar: +1/-1/0

    def swings_at(self, i: int) -> list[SwingPoint]:
        cut = self.h4[i].close_time
        return self._h4_sw[: bisect_right(self._h4_sw_conf, cut)]

    def breaks_at(self, i: int) -> list[StructureBreak]:
        cut = self.h4[i].close_time
        return self._h4_brk[: bisect_right(self._h4_brk_ts, cut)]

    def d1_state(self, i: int) -> RegimeDirectional:
        return self._d1_state[i]

    def d1_bos(self, i: int) -> int:
        return self._d1_bos[i]


def build_ctx(symbol: str, h4: list[OHLCV], d1: list[OHLCV]) -> Ctx:
    atr = atr_series(h4, _ATR_P)
    h4_sw = detect_swings(h4, _H4, left=_SWING_L, right=_SWING_R, min_leg_atr=0.5)
    h4_sw.sort(key=lambda s: s.confirmed_at)
    h4_brk = structure_breaks(h4, h4_sw, _H4, min_swings=2)
    h4_brk.sort(key=lambda b: b.break_bar_timestamp)

    # D1: alle Swings/Breaks einmal, dann je H4-Bar den PIT-Zustand
    d1_sw = detect_swings(d1, _D1, left=_SWING_L, right=_SWING_R, min_leg_atr=0.5)
    d1_sw.sort(key=lambda s: s.confirmed_at)
    d1_brk = structure_breaks(d1, d1_sw, _D1, min_swings=2)
    d1_brk_bos = [b for b in d1_brk if b.kind is StructureBreakKind.BOS]
    d1_brk_bos.sort(key=lambda b: b.break_bar_timestamp)
    d1_open = [b.open_time for b in d1]
    d1_sw_conf = [s.confirmed_at for s in d1_sw]
    d1_bos_ts = [b.break_bar_timestamp for b in d1_brk_bos]

    d1_state: list[RegimeDirectional] = []
    d1_bos: list[int] = []
    for bar in h4:
        cut = bar.close_time
        vis_sw = d1_sw[: bisect_right(d1_sw_conf, cut)]
        d1_state.append(
            derive_structure_state(vis_sw, _D1, min_swings=2).directional
            if len(vis_sw) >= 4
            else RegimeDirectional.UNCLEAR
        )
        # jüngster D1-BOS mit break_bar <= cut, dessen Bar in den letzten N D1-Bars vor cut liegt
        k = bisect_right(d1_bos_ts, cut)
        if k == 0:
            d1_bos.append(0)
            continue
        last = d1_brk_bos[k - 1]
        d1_idx = bisect_right(d1_open, cut) - 1  # letzte D1-Bar bis cut
        last_idx = bisect.bisect_left(d1_open, last.break_bar_timestamp)
        d1_bos.append(
            (1 if last.direction is Polarity.BULLISH else -1)
            if 0 <= d1_idx - last_idx <= _D1_BOS_LOOKBACK
            else 0
        )

    return Ctx(
        symbol=symbol,
        h4=h4,
        atr=atr,
        _h4_sw=h4_sw,
        _h4_sw_conf=[s.confirmed_at for s in h4_sw],
        _h4_brk=h4_brk,
        _h4_brk_ts=[b.break_bar_timestamp for b in h4_brk],
        _d1_state=d1_state,
        _d1_bos=d1_bos,
    )


# --------------------------------------------------------------------------------- Bar-Helfer


def _body_ratio(b: OHLCV) -> float:
    rng = b.high - b.low
    return abs(b.close - b.open) / rng if rng > 0 else 0.0


def _close_pos(b: OHLCV) -> float:
    rng = b.high - b.low
    return (b.close - b.low) / rng if rng > 0 else 0.5


# --------------------------------------------------------------------------------- Detektoren


def detect_s0(ctx: Ctx, i: int) -> Signal | None:
    """Sweep eines jüngeren H4-Swing-Extrems + Reclaim."""
    a = ctx.atr[i]
    if not a:
        return None
    sw = ctx.swings_at(i)
    if len(sw) < 4:
        return None
    cur, prev = ctx.h4[i], ctx.h4[i - 1]
    lows = [s for s in sw if s.type is SwingType.SWING_LOW][-3:]
    highs = [s for s in sw if s.type is SwingType.SWING_HIGH][-3:]
    for lv in reversed(lows):
        pen = lv.price - min(cur.low, prev.low)
        ok = 0.05 * a <= pen <= 1.2 * a and cur.close > lv.price
        if ok and prev.close <= lv.price + 0.3 * a and _close_pos(cur) > 0.5:
            stop = min(cur.low, prev.low) - 0.25 * a
            if cur.close - stop > 0.4 * a:
                return Signal(i, +1, stop, f"sweep_low pen={pen / a:.2f}ATR")
    for lv in reversed(highs):
        pen = max(cur.high, prev.high) - lv.price
        ok = 0.05 * a <= pen <= 1.2 * a and cur.close < lv.price
        if ok and prev.close >= lv.price - 0.3 * a and _close_pos(cur) < 0.5:
            stop = max(cur.high, prev.high) + 0.25 * a
            if stop - cur.close > 0.4 * a:
                return Signal(i, -1, stop, f"sweep_high pen={pen / a:.2f}ATR")
    return None


def detect_s1(ctx: Ctx, i: int) -> Signal | None:
    """Konsolidierung → Ausbruch mit Displacement → Retest der Bruchkante hält (erster Retest, an Bar i)."""
    a = ctx.atr[i]
    if not a or i < 40:
        return None
    h4 = ctx.h4
    look, retest_window = 14, 12
    for bo in range(i - 1, i - retest_window - 1, -1):
        if bo - look < 0:
            break
        cons = h4[bo - look : bo]
        hi = max(b.high for b in cons)
        lo = min(b.low for b in cons)
        width = hi - lo
        if not (0.4 * a <= width <= 5.0 * a):
            continue
        bob = h4[bo]
        up = bob.close > hi + 0.3 * a
        dn = bob.close < lo - 0.3 * a
        if not (up or dn):
            continue
        level = hi if up else lo
        d = 1 if up else -1
        rb = h4[i]
        touched = (rb.low <= level + 0.5 * a) if up else (rb.high >= level - 0.5 * a)
        held = (rb.close > level) if up else (rb.close < level)
        cd = _close_pos(rb) > 0.5 if up else _close_pos(rb) < 0.5
        earlier = any(
            ((h4[r].low <= level + 0.5 * a) if up else (h4[r].high >= level - 0.5 * a))
            for r in range(bo + 1, i)
        )
        if touched and held and cd and not earlier:
            stop = (min(rb.low, level) - 0.3 * a) if up else (max(rb.high, level) + 0.3 * a)
            if abs(rb.close - stop) > 0.4 * a:
                return Signal(i, d, stop, f"brk_retest w={width / a:.1f}ATR")
    return None


def detect_s2(ctx: Ctx, i: int) -> Signal | None:
    """D1-Trend + H4-Pullback (33–85 % der Leg) + Rejection-Bar in Trendrichtung."""
    a = ctx.atr[i]
    if not a:
        return None
    st = ctx.d1_state(i)
    if st not in (RegimeDirectional.TREND_UP, RegimeDirectional.TREND_DOWN):
        return None
    d = 1 if st is RegimeDirectional.TREND_UP else -1
    sw = ctx.swings_at(i)
    highs = [s for s in sw if s.type is SwingType.SWING_HIGH]
    lows = [s for s in sw if s.type is SwingType.SWING_LOW]
    if len(highs) < 2 or len(lows) < 2:
        return None
    cur = ctx.h4[i]
    if d > 0:
        last_high, last_low = highs[-1].price, lows[-1].price
        leg = last_high - last_low
        if leg <= 0:
            return None
        pull_lo = min(b.low for b in ctx.h4[max(0, i - 6) : i + 1])
        depth = (last_high - pull_lo) / leg
        if 0.33 <= depth <= 0.85 and _close_pos(cur) > 0.6 and cur.close > cur.open:
            stop = pull_lo - 0.3 * a
            if cur.close - stop > 0.4 * a:
                return Signal(i, +1, stop, f"trend_up pull={depth:.2f}")
    else:
        last_low, last_high = lows[-1].price, highs[-1].price
        leg = last_high - last_low
        if leg <= 0:
            return None
        pull_hi = max(b.high for b in ctx.h4[max(0, i - 6) : i + 1])
        depth = (pull_hi - last_low) / leg
        if 0.33 <= depth <= 0.85 and _close_pos(cur) < 0.4 and cur.close < cur.open:
            stop = pull_hi + 0.3 * a
            if stop - cur.close > 0.4 * a:
                return Signal(i, -1, stop, f"trend_dn pull={depth:.2f}")
    return None


def detect_s3(ctx: Ctx, i: int) -> Signal | None:
    """Jüngster D1-BOS gibt Bias → LTF-Confirmation: H4-Close jenseits des jüngsten H4-Swing-
    Extrems in Bias-Richtung (nach einer Gegenbewegung)."""
    a = ctx.atr[i]
    if not a or i < 40:
        return None
    bias = ctx.d1_bos(i)
    if bias == 0:
        return None
    sw = ctx.swings_at(i)
    highs = [s for s in sw if s.type is SwingType.SWING_HIGH]
    lows = [s for s in sw if s.type is SwingType.SWING_LOW]
    if len(highs) < 1 or len(lows) < 1:
        return None
    cur, prev = ctx.h4[i], ctx.h4[i - 1]
    if bias > 0:
        ref = highs[-1].price
        broke_now = cur.close > ref and prev.close <= ref  # erster Close über dem Swing-High
        if broke_now and _close_pos(cur) > 0.5:
            pull_lo = min(b.low for b in ctx.h4[max(0, i - 8) : i + 1])
            stop = pull_lo - 0.3 * a
            if cur.close - stop > 0.5 * a:
                return Signal(i, +1, stop, f"d1_bos+ h4_break@{ref:.2f}")
    else:
        ref = lows[-1].price
        broke_now = cur.close < ref and prev.close >= ref
        if broke_now and _close_pos(cur) < 0.5:
            pull_hi = max(b.high for b in ctx.h4[max(0, i - 8) : i + 1])
            stop = pull_hi + 0.3 * a
            if stop - cur.close > 0.5 * a:
                return Signal(i, -1, stop, f"d1_bos- h4_break@{ref:.2f}")
    return None


def detect_s4(ctx: Ctx, i: int) -> Signal | None:
    """S1 (Breakout+Retest) **nur in Richtung des D1-Struktur-Trends** (Continuation-Filter)."""
    sig = detect_s1(ctx, i)
    if sig is None:
        return None
    st = ctx.d1_state(i)
    if st is RegimeDirectional.TREND_UP and sig.direction > 0:
        return sig
    if st is RegimeDirectional.TREND_DOWN and sig.direction < 0:
        return sig
    return None


def detect_s5(ctx: Ctx, i: int) -> Signal | None:
    """S3 (HTF-Break + LTF-Confirm) mit D1-**Struktur-Zustand** statt D1-BOS als Bias-Quelle."""
    a = ctx.atr[i]
    if not a or i < 40:
        return None
    st = ctx.d1_state(i)
    bias = (
        1 if st is RegimeDirectional.TREND_UP else -1 if st is RegimeDirectional.TREND_DOWN else 0
    )
    if bias == 0:
        return None
    sw = ctx.swings_at(i)
    highs = [s for s in sw if s.type is SwingType.SWING_HIGH]
    lows = [s for s in sw if s.type is SwingType.SWING_LOW]
    if not highs or not lows:
        return None
    cur, prev = ctx.h4[i], ctx.h4[i - 1]
    if bias > 0:
        ref = highs[-1].price
        if cur.close > ref and prev.close <= ref and _close_pos(cur) > 0.5:
            pull_lo = min(b.low for b in ctx.h4[max(0, i - 8) : i + 1])
            stop = pull_lo - 0.3 * a
            if cur.close - stop > 0.5 * a:
                return Signal(i, +1, stop, f"d1_trend+ h4_break@{ref:.2f}")
    else:
        ref = lows[-1].price
        if cur.close < ref and prev.close >= ref and _close_pos(cur) < 0.5:
            pull_hi = max(b.high for b in ctx.h4[max(0, i - 8) : i + 1])
            stop = pull_hi + 0.3 * a
            if stop - cur.close > 0.5 * a:
                return Signal(i, -1, stop, f"d1_trend- h4_break@{ref:.2f}")
    return None


def _brk_retest_core(
    ctx: Ctx,
    i: int,
    *,
    max_width_atr: float = 5.0,
    min_thrust_atr: float = 0.3,
    require_contraction: bool = False,
) -> tuple[Signal, float] | None:
    """Konfigurierbarer Breakout+Retest-Kern (Diagnose 2026-08-31: der S1/S4-Standard lässt
    zu breite 'Konsolidierungen' und zu schwache Ausbrüche zu → False Breakouts auf Gold).
    Rückgabe: (Signal, cons_width/ATR). Filter sind *struktureller* Natur, nicht an die
    6 Gold-Trades gefittet."""
    a = ctx.atr[i]
    if not a or i < 40:
        return None
    h4 = ctx.h4
    look, retest_window = 14, 12
    for bo in range(i - 1, i - retest_window - 1, -1):
        if bo - look < 0:
            break
        cons = h4[bo - look : bo]
        hi = max(b.high for b in cons)
        lo = min(b.low for b in cons)
        width = hi - lo
        if not (0.4 * a <= width <= max_width_atr * a):
            continue
        if require_contraction:
            half = look // 2
            r_early = max(b.high for b in cons[:half]) - min(b.low for b in cons[:half])
            r_late = max(b.high for b in cons[half:]) - min(b.low for b in cons[half:])
            if r_late >= 0.85 * r_early:  # zweite Hälfte nicht enger → kein echter Coil
                continue
        bob = h4[bo]
        up = bob.close > hi + min_thrust_atr * a
        dn = bob.close < lo - min_thrust_atr * a
        if not (up or dn):
            continue
        level = hi if up else lo
        d = 1 if up else -1
        rb = h4[i]
        touched = (rb.low <= level + 0.5 * a) if up else (rb.high >= level - 0.5 * a)
        held = (rb.close > level) if up else (rb.close < level)
        cd = _close_pos(rb) > 0.5 if up else _close_pos(rb) < 0.5
        earlier = any(
            ((h4[r].low <= level + 0.5 * a) if up else (h4[r].high >= level - 0.5 * a))
            for r in range(bo + 1, i)
        )
        if touched and held and cd and not earlier:
            stop = (min(rb.low, level) - 0.3 * a) if up else (max(rb.high, level) + 0.3 * a)
            if abs(rb.close - stop) > 0.4 * a:
                return Signal(i, d, stop, f"brk_retest w={width / a:.1f}ATR"), width / a
    return None


def _d1_trend_dir(ctx: Ctx, i: int) -> int:
    st = ctx.d1_state(i)
    return 1 if st is RegimeDirectional.TREND_UP else -1 if st is RegimeDirectional.TREND_DOWN else 0


def detect_s6(ctx: Ctx, i: int) -> Signal | None:
    """S4 + **enger Coil** (≤ 2.0·ATR) + **starker Ausbruch** (≥ 1.0·ATR Displacement).
    Ziel: nur echte Continuation-Breakouts, keine Range-Fades."""
    r = _brk_retest_core(ctx, i, max_width_atr=2.0, min_thrust_atr=1.0)
    if r is None:
        return None
    sig, _ = r
    return sig if _d1_trend_dir(ctx, i) == sig.direction else None


def detect_s7(ctx: Ctx, i: int) -> Signal | None:
    """S4 + **Range-Kontraktion** (2. Coil-Hälfte enger) + Ausbruch ≥ 0.8·ATR."""
    r = _brk_retest_core(ctx, i, max_width_atr=3.0, min_thrust_atr=0.8, require_contraction=True)
    if r is None:
        return None
    sig, _ = r
    return sig if _d1_trend_dir(ctx, i) == sig.direction else None


def detect_s8(ctx: Ctx, i: int) -> Signal | None:
    """S4 + **Session-Filter**: kein Einstieg, wenn die Retest-Bar 20–04 UTC schließt
    (illiquide; Diagnose: 4/6 Gold-Fehltrades in diesem Fenster)."""
    r = _brk_retest_core(ctx, i, max_width_atr=5.0, min_thrust_atr=0.3)
    if r is None:
        return None
    sig, _ = r
    if _d1_trend_dir(ctx, i) != sig.direction:
        return None
    return sig if ctx.h4[i].close_time.hour not in (20, 21, 22, 23, 0, 1, 2, 3) else None


def detect_s9(ctx: Ctx, i: int) -> Signal | None:
    """S4 + **HTF-Konfluenz**: D1-Struktur-Trend UND jüngster D1-BOS zeigen in dieselbe Richtung
    (Diagnose: der 2-Swing-D1-Trend allein flippt auf Gold in Pullbacks zu leicht auf trend_down)."""
    r = _brk_retest_core(ctx, i, max_width_atr=5.0, min_thrust_atr=0.3)
    if r is None:
        return None
    sig, _ = r
    td = _d1_trend_dir(ctx, i)
    return sig if td == sig.direction and ctx.d1_bos(i) == sig.direction else None


def detect_s10(ctx: Ctx, i: int) -> Signal | None:
    """S6 (enger Coil + starker Ausbruch) **und** S9 (HTF-Konfluenz) — die Qualitätsvariante."""
    r = _brk_retest_core(ctx, i, max_width_atr=2.0, min_thrust_atr=1.0)
    if r is None:
        return None
    sig, _ = r
    td = _d1_trend_dir(ctx, i)
    return sig if td == sig.direction and ctx.d1_bos(i) == sig.direction else None


def _s9_base(ctx: Ctx, i: int, *, max_width_atr: float = 5.0, min_thrust_atr: float = 0.3) -> Signal | None:
    r = _brk_retest_core(ctx, i, max_width_atr=max_width_atr, min_thrust_atr=min_thrust_atr)
    if r is None:
        return None
    sig, _ = r
    td = _d1_trend_dir(ctx, i)
    return sig if td == sig.direction and ctx.d1_bos(i) == sig.direction else None


def detect_s11(ctx: Ctx, i: int) -> Signal | None:
    """S9 (HTF-Konfluenz) + Session-Filter (kein Einstieg 20–04 UTC)."""
    sig = _s9_base(ctx, i)
    if sig is None:
        return None
    return sig if ctx.h4[i].close_time.hour not in (20, 21, 22, 23, 0, 1, 2, 3) else None


def detect_s12(ctx: Ctx, i: int) -> Signal | None:
    """S9 + moderater Ausbruch-Filter (Displacement ≥ 0.6·ATR — die schwächsten Breakouts raus)."""
    return _s9_base(ctx, i, min_thrust_atr=0.6)


def detect_s13(ctx: Ctx, i: int) -> Signal | None:
    """S9 + moderater Coil-Filter (Range ≤ 3.0·ATR — die breitesten 'Konsolidierungen' raus)."""
    return _s9_base(ctx, i, max_width_atr=3.0)


def _d1_efficiency_ratio(ctx: Ctx, i: int, window_h4_bars: int = 120) -> float:
    """Kaufman Efficiency Ratio auf den H4-Closes der letzten ~window Bars (Proxy für
    D1-Regime: ~1 = klarer Trend, ~0 = Chop). Rein preisbasiert, nicht überfittbar."""
    lo = max(0, i - window_h4_bars)
    seg = [b.close for b in ctx.h4[lo : i + 1]]
    if len(seg) < 10:
        return 0.0
    net = abs(seg[-1] - seg[0])
    path = sum(abs(seg[k] - seg[k - 1]) for k in range(1, len(seg)))
    return net / path if path > 0 else 0.0


def detect_s14(ctx: Ctx, i: int) -> Signal | None:
    """S9 + **Regime-Gate**: nur wenn der Markt tatsächlich trendet (Efficiency Ratio ≥ 0.30).
    Die Diagnose zeigt: Breakout-Continuation verliert systematisch in Ranges."""
    sig = _s9_base(ctx, i)
    if sig is None:
        return None
    return sig if _d1_efficiency_ratio(ctx, i) >= 0.30 else None


def detect_s15(ctx: Ctx, i: int) -> Signal | None:
    """S9 + strengeres Regime-Gate (Efficiency Ratio ≥ 0.40)."""
    sig = _s9_base(ctx, i)
    if sig is None:
        return None
    return sig if _d1_efficiency_ratio(ctx, i) >= 0.40 else None


DETECTORS: dict[str, Callable[[Ctx, int], Signal | None]] = {
    "S0_sweep_reversal": detect_s0,
    "S1_breakout_retest": detect_s1,
    "S2_trend_pullback": detect_s2,
    "S3_htf_break_confirm": detect_s3,
    "S4_breakout_retest_trendfilter": detect_s4,
    "S5_d1trend_h4break": detect_s5,
    "S6_coil_strong_thrust": detect_s6,
    "S7_range_contraction": detect_s7,
    "S8_session_filter": detect_s8,
    "S9_htf_confluence": detect_s9,
    "S10_quality_combo": detect_s10,
    "S11_htf_conf_session": detect_s11,
    "S12_htf_conf_thrust06": detect_s12,
    "S13_htf_conf_coil3": detect_s13,
    "S14_regime_gate_er30": detect_s14,
    "S15_regime_gate_er40": detect_s15,
}


# --------------------------------------------------------------------------------- Trade-Sim


_MANAGE = "fixed"  # "fixed" | "scaled"  (per --manage)


def _simulate(
    h4: list[OHLCV], sig: Signal, *, rr: float, cost_r: float, symbol: str, setup: str
) -> TradeRecord | None:
    ei = sig.at_index + 1
    if ei >= len(h4):
        return None
    entry = h4[ei].open
    r_unit = abs(entry - sig.stop)
    if r_unit <= 0:
        return None
    d = sig.direction
    tp = entry + d * rr * r_unit
    sl = sig.stop
    mfe = mae = 0.0
    exit_price = entry
    exit_reason = "max_hold"
    last = min(len(h4) - 1, ei + _MAX_HOLD_H4)
    exit_idx = last

    if _MANAGE == "scaled":
        # 50 % @ +1R, SL→BE danach, Rest bis +rr R (Runner). Worst-Case: SL vor TP je Bar.
        tp1 = entry + d * 1.0 * r_unit
        booked = 0.0
        part_left = 1.0
        be_moved = False
        for j in range(ei, last + 1):
            b = h4[j]
            mfe = max(mfe, ((b.high - entry) if d > 0 else (entry - b.low)) / r_unit)
            mae = max(mae, ((entry - b.low) if d > 0 else (b.high - entry)) / r_unit)
            hit_sl = (b.low <= sl) if d > 0 else (b.high >= sl)
            hit_tp1 = (b.high >= tp1) if d > 0 else (b.low <= tp1)
            hit_tp = (b.high >= tp) if d > 0 else (b.low <= tp)
            if hit_sl:
                booked += part_left * (d * (sl - entry) / r_unit)
                exit_reason, exit_idx = ("be" if be_moved else "stop"), j
                part_left = 0.0
                break
            if hit_tp:
                booked += part_left * rr
                exit_reason, exit_idx, part_left = "target", j, 0.0
                break
            if hit_tp1 and part_left == 1.0:
                booked += 0.5 * 1.0
                part_left = 0.5
                sl = entry
                be_moved = True
        if part_left > 0:
            booked += part_left * (d * (h4[last].close - entry) / r_unit)
        gross_r = booked
    else:
        for j in range(ei, last + 1):
            b = h4[j]
            mfe = max(mfe, ((b.high - entry) if d > 0 else (entry - b.low)) / r_unit)
            mae = max(mae, ((entry - b.low) if d > 0 else (b.high - entry)) / r_unit)
            hit_sl = (b.low <= sl) if d > 0 else (b.high >= sl)
            hit_tp = (b.high >= tp) if d > 0 else (b.low <= tp)
            if hit_sl:
                exit_price, exit_reason, exit_idx = sl, "stop", j
                break
            if hit_tp:
                exit_price, exit_reason, exit_idx = tp, "target", j
                break
        else:
            exit_price = h4[last].close
        gross_r = d * (exit_price - entry) / r_unit
    realized_r = gross_r - cost_r
    wl = "WIN" if realized_r > 0.02 else "LOSS" if realized_r < -0.02 else "SCRATCH"
    return TradeRecord(
        trade_id=f"{setup}:{symbol}:{h4[sig.at_index].open_time.isoformat()}",
        instrument=symbol,
        direction=Side.BUY if d > 0 else Side.SELL,
        setup_id=setup,
        strategy_version="research",
        signal_ts=h4[sig.at_index].close_time,
        information_cutoff=h4[sig.at_index].close_time,
        entry_ts=h4[ei].open_time,
        entry_price=entry,
        qty=1.0,
        initial_sl=sl,
        initial_tp=tp,
        exit_ts=h4[exit_idx].close_time,
        exit_price=exit_price,
        exit_reason=exit_reason,
        gross_r=round(gross_r, 4),
        realized_r=round(realized_r, 4),
        pnl_ccy=round(realized_r, 4),
        mfe_r=round(mfe, 4),
        mae_r=round(mae, 4),
        bars_held=exit_idx - ei,
        win_loss=wl,
    )


def _run_detector(
    name: str,
    det: Callable[[Ctx, int], Signal | None],
    ctxs: list[Ctx],
    *,
    rr: float,
    cost_r: float,
) -> list[TradeRecord]:
    trades: list[TradeRecord] = []
    for ctx in ctxs:
        busy_until = -1
        for i in range(30, len(ctx.h4) - 1):
            if i <= busy_until:
                continue
            sig = det(ctx, i)
            if sig is None:
                continue
            tr = _simulate(ctx.h4, sig, rr=rr, cost_r=cost_r, symbol=ctx.symbol, setup=name)
            if tr is None:
                continue
            trades.append(tr)
            busy_until = i + 1 + tr.bars_held
    return trades


# --------------------------------------------------------------------------------- Auswertung


def _m(trades: list[TradeRecord]) -> dict[str, object]:
    if not trades:
        return {"n_trades": 0}
    m = compute_metrics(trades)
    return {
        "n_trades": m.n_trades,
        "win_rate": round(m.win_rate, 4),
        "profit_factor": round(m.profit_factor, 3) if m.profit_factor != math.inf else "inf",
        "expectancy_r": round(m.expectancy_r, 4),
        "total_r": round(m.total_r, 2),
        "max_drawdown_r": round(m.max_drawdown_r, 2),
        "sharpe_r": m.sharpe_r,
        "sortino_r": m.sortino_r,
        "avg_mfe_r": round(m.avg_mfe_r, 3),
        "avg_mae_r": round(m.avg_mae_r, 3),
        "longest_loss_streak": m.longest_loss_streak,
    }


def _wf(trades: list[TradeRecord], start: datetime, end: datetime) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for f in walk_forward_folds(start, end, train_days=200, test_days=90, step_days=90):
        te = f.test_trades(trades)
        row: dict[str, object] = {
            "fold": f.index,
            "test": [f.test_start.date().isoformat(), f.test_end.date().isoformat()],
            "n": len(te),
        }
        if len(te) >= 5:
            mm = compute_metrics(te)
            row["expectancy_r"] = round(mm.expectancy_r, 4)
            row["profit_factor"] = (
                round(mm.profit_factor, 3) if mm.profit_factor != math.inf else "inf"
            )
            row["total_r"] = round(mm.total_r, 2)
        out.append(row)
    return out


def _mc(trades: list[TradeRecord]) -> dict[str, object]:
    if len(trades) < 20:
        return {"note": "zu wenige Trades"}
    r = monte_carlo(trades, runs=2000, seed=7)
    return {
        "final_r_p05": round(r.final_equity_r_p05, 2),
        "final_r_p50": round(r.final_equity_r_p50, 2),
        "final_r_p95": round(r.final_equity_r_p95, 2),
        "max_dd_r_p95": round(r.max_dd_r_p95, 2),
        "prob_positive": round(r.prob_positive, 4),
    }


def _dir_split(trades: list[TradeRecord]) -> dict[str, object]:
    longs = [t for t in trades if t.direction is Side.BUY]
    shorts = [t for t in trades if t.direction is Side.SELL]
    return {
        "long": {
            "n": len(longs),
            "total_r": round(sum(t.realized_r for t in longs), 2),
            "expectancy_r": round(sum(t.realized_r for t in longs) / len(longs), 4)
            if longs
            else 0.0,
        },
        "short": {
            "n": len(shorts),
            "total_r": round(sum(t.realized_r for t in shorts), 2),
            "expectancy_r": round(sum(t.realized_r for t in shorts) / len(shorts), 4)
            if shorts
            else 0.0,
        },
    }


def _focus(trades: list[TradeRecord], symbol: str, split: datetime) -> dict[str, object]:
    ft = [t for t in trades if t.instrument == symbol]
    if not ft:
        return {"n_trades": 0}
    ft.sort(key=lambda t: t.entry_ts)
    half = len(ft) // 2
    return {
        **_m(ft),
        "dir_split": _dir_split(ft),
        "first_half_total_r": round(sum(t.realized_r for t in ft[:half]), 2),
        "second_half_total_r": round(sum(t.realized_r for t in ft[half:]), 2),
        "span": [ft[0].entry_ts.date().isoformat(), ft[-1].entry_ts.date().isoformat()],
    }


def _evaluate(
    trades_by_rr: dict[float, list[TradeRecord]],
    *,
    split: datetime,
    start: datetime,
    end: datetime,
    focus_symbols: tuple[str, ...] = ("XAUUSDT",),
) -> dict[str, object]:
    is_pick: dict[float, float] = {}
    for rr, trs in trades_by_rr.items():
        is_t = [t for t in trs if t.entry_ts < split]
        if len(is_t) >= 15:
            is_pick[rr] = compute_metrics(is_t).expectancy_r
    best_rr = max(is_pick, key=lambda k: is_pick[k]) if is_pick else 2.0
    trades = trades_by_rr[best_rr]
    # Purge/Embargo: Trades, deren Leben die IS/OOS-Grenze überspannt, fallen aus beiden Blöcken
    # (max Haltedauer 60 H4-Bars = 10 Tage → 12-Tage-Puffer). Verhindert Leakage über die Grenze.
    from datetime import timedelta as _td

    embargo = _td(days=12)
    is_t = [t for t in trades if t.exit_ts < split - embargo]
    oos_t = [t for t in trades if t.entry_ts >= split + embargo]
    ss = symbol_stability(oos_t if oos_t else trades)
    ts = time_stability(trades, window_days=90, step_days=45)
    return {
        "chosen_rr_on_is": best_rr,
        "rr_sweep_is_expectancy": {str(k): round(v, 4) for k, v in is_pick.items()},
        "all": _m(trades),
        "IS": _m(is_t),
        "OOS": _m(oos_t),
        "dir_split_all": _dir_split(trades),
        "focus": {s: _focus(trades, s, split) for s in focus_symbols},
        "walk_forward": _wf(trades, start, end),
        "monte_carlo_full": _mc(trades),
        "symbol_stability": {
            "per_symbol_total_r": ss.per_symbol_total_r,
            "fraction_positive": ss.fraction_positive,
            "total_r_without_best": ss.total_r_without_best,
        },
        "time_windows_90d": [
            {"start": w.start.date().isoformat(), "n": w.n_trades, "total_r": w.total_r} for w in ts
        ],
    }


def _combine(a: list[TradeRecord], b: list[TradeRecord]) -> list[TradeRecord]:
    out: list[TradeRecord] = []
    by_sym: dict[str, list[TradeRecord]] = {}
    for t in sorted([*a, *b], key=lambda t: t.entry_ts):
        by_sym.setdefault(t.instrument, []).append(t)
    for ts in by_sym.values():
        busy: datetime | None = None
        for t in ts:
            if busy is not None and t.entry_ts < busy:
                continue
            out.append(t)
            busy = t.exit_ts
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", default="data/repository_real")
    ap.add_argument(
        "--symbols",
        nargs="+",
        default=["XAUUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT"],
    )
    ap.add_argument("--start", default="2023-01-01")
    ap.add_argument("--split", default="2025-06-01")
    ap.add_argument("--end", default="2026-08-29")
    ap.add_argument("--cost-r", type=float, default=0.03)
    ap.add_argument("--manage", choices=["fixed", "scaled"], default="fixed")
    ap.add_argument("--focus", nargs="+", default=["XAUUSDT", "XAUUSD-YF", "XAUUSD"])
    ap.add_argument("--out", default="data/repository_real/setup_research.json")
    args = ap.parse_args()

    global _MANAGE
    _MANAGE = args.manage
    start, split, end = (parse_timestamp(x) for x in (args.start, args.split, args.end))
    repo = MarketDataRepository(args.repo)

    ctxs: list[Ctx] = []
    coverage: dict[str, object] = {}
    for sym in args.symbols:
        h4 = repo.read_ohlcv(sym, _H4, start, end)
        d1 = repo.read_ohlcv(sym, _D1, start, end)
        if len(h4) < 200 or len(d1) < 40:
            coverage[sym] = {"skip": True, "h4": len(h4), "d1": len(d1)}
            continue
        ctxs.append(build_ctx(sym, h4, d1))
        coverage[sym] = {
            "h4_bars": len(h4),
            "d1_bars": len(d1),
            "span": [h4[0].open_time.date().isoformat(), h4[-1].open_time.date().isoformat()],
        }
        print(f"  ctx {sym}: {len(h4)} H4, {len(d1)} D1")

    results: dict[str, object] = {}
    store: dict[str, dict[float, list[TradeRecord]]] = {}
    for name, det in DETECTORS.items():
        by_rr = {rr: _run_detector(name, det, ctxs, rr=rr, cost_r=args.cost_r) for rr in _RRS}
        store[name] = by_rr
        results[name] = _evaluate(
            by_rr, split=split, start=start, end=end, focus_symbols=tuple(args.focus)
        )
        r = results[name]
        print(
            f"  {name}: {r['all'].get('n_trades', 0)} trades  RR={r['chosen_rr_on_is']}  "  # type: ignore[union-attr]
            f"OOS exp={r['OOS'].get('expectancy_r', 'n/a')}"  # type: ignore[union-attr]
        )

    def _oos_exp(nm: str) -> float:
        v = results[nm]["OOS"]  # type: ignore[index]
        return (
            float(v["expectancy_r"])
            if isinstance(v, dict) and v.get("n_trades", 0) >= 10 and "expectancy_r" in v
            else -9.0
        )

    others = [k for k in DETECTORS if k != "S0_sweep_reversal"]
    best_other = max(others, key=_oos_exp)
    rr0 = results["S0_sweep_reversal"]["chosen_rr_on_is"]  # type: ignore[index]
    rrb = results[best_other]["chosen_rr_on_is"]  # type: ignore[index]
    combined = _combine(store["S0_sweep_reversal"][rr0], store[best_other][rrb])  # type: ignore[index]
    results["COMBINED_S0_plus_best"] = {
        "components": ["S0_sweep_reversal", best_other],
        **_evaluate(
            {2.0: combined}, split=split, start=start, end=end, focus_symbols=tuple(args.focus)
        ),
    }

    report = {
        "params": {
            "start": args.start,
            "split": args.split,
            "end": args.end,
            "cost_r": args.cost_r,
            "timeframes": "H4 setup/entry, D1 regime",
            "max_hold_h4": _MAX_HOLD_H4,
            "rr_grid": list(_RRS),
        },
        "coverage": coverage,
        "best_other_setup": best_other,
        "results": results,
    }
    with open(args.out, "w") as fh:
        json.dump(report, fh, indent=2, default=str)
    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
