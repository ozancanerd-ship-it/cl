"""``SETUP-BREAKOUT-RETEST-01`` — der zweite Setup-Typ (Swing).

**Muster:** enge H4-Konsolidierung → Ausbruch mit Displacement → **Retest** der Bruchkante,
der hält → Einstieg **in Richtung des D1-Struktur-Trends** (HTF-Trend-Filter).

Herkunft: `scripts/setup_research.py` Variante **S4**. Auf der Gold-Historie (XAUUSD-YF
2024-04…2026-08) über drei IS/OOS-Splits: OOS-Expectancy +0.40…+0.47 R, Walk-Forward 5–6 / 6–7
Fenster positiv, **Monte-Carlo `prob_positive` 0.59–0.78** (einziger Setup-Typ, der den
1.5×-Kosten-Stress besteht). Wenige, gute Trades (~7/Jahr auf Gold).
Status: **`IN_VALIDATION`** (`config/setup_validation.json`) — historische Edge *plausibel*,
noch nicht *bewiesen*: Yahoo-Daten sind indikativ, kleine OOS-Stichprobe, 2024–26 Gold-Hausse.
Live-Signale erscheinen als SHADOW, bis echte Dukascopy-Historie + ≥ 100 Forward-Trades
bestätigen (`scripts/edge_health_check.py`).

**Kein Look-ahead:** liest nur ``mtf.h4.bars`` / ``mtf.d1.structure`` (alle ≤ ``information_cutoff``).
Deterministisch. Long/Short-symmetrisch.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from trading_agent.analysis.mtf import MtfContext
from trading_agent.core.enums import Direction, NoTradeReason, RegimeDirectional
from trading_agent.core.models import OHLCV
from trading_agent.core.version import STRATEGY_VERSION
from trading_agent.strategy.primitives.atr import atr_series

SETUP_BREAKOUT_RETEST = "SETUP-BREAKOUT-RETEST-01"


class BreakoutState(StrEnum):
    SCANNING = "scanning"
    CONSOLIDATION = "consolidation"  # enge Range erkannt, kein Ausbruch
    AWAIT_RETEST = "await_retest"  # Ausbruch + Displacement, wartet auf Retest
    ARMED = "armed"  # Retest hält → Geometrie steht
    INVALIDATED = "invalidated"  # Retest-Fenster verstrichen / Bruchkante verloren


@dataclass(frozen=True, slots=True)
class BreakoutRetestParams:
    """Alle Werte aus der S4-Forschung (Gold-kalibriert). PROPOSED — Forward-Validierung offen."""

    consolidation_bars: int = 14
    consolidation_min_atr: float = 0.4  # Range-Höhe / ATR — nicht zu eng (Rauschen)
    consolidation_max_atr: float = 5.0  # … nicht zu weit (keine Range mehr)
    breakout_displacement_atr: float = 0.3  # Close jenseits der Kante, in ATR
    retest_window_bars: int = 12  # max Bars zwischen Ausbruch und Retest
    retest_touch_atr: float = 0.5  # Retest berührt die Kante innerhalb dieser Distanz
    stop_buffer_atr: float = 0.3  # SL jenseits des Retest-Extrems
    min_stop_atr: float = 0.4  # SL-Distanz mind. — sonst zu eng
    require_d1_trend: bool = True  # S4: nur mit D1-Struktur-Trend
    tp1_r: float = 1.5
    tp2_r: float = 3.0
    tp3_assumed_r: float = 2.5  # für blended RR (Runner, kein fester Preis)
    tp1_size_pct: float = 50.0
    tp2_size_pct: float = 25.0
    tp3_size_pct: float = 25.0
    min_rr_to_tp2: float = 2.0
    atr_period: int = 14
    armed_bars: int = 8  # ARMED läuft ab, wenn nach N Bars kein Trigger


@dataclass(frozen=True, slots=True)
class BreakoutRetestReport:
    instrument: str
    information_cutoff: datetime
    state: BreakoutState
    direction: Direction | None = None
    # Kontext
    consolidation_low: float | None = None
    consolidation_high: float | None = None
    broken_level: float | None = None
    breakout_bar: datetime | None = None
    retest_bar: datetime | None = None
    d1_trend: RegimeDirectional = RegimeDirectional.UNCLEAR
    # Geometrie (nur ARMED)
    entry: float | None = None
    sl: float | None = None
    tp1: float | None = None
    tp2: float | None = None
    tp3_ref: str | None = None
    rr_to_tp2: float | None = None
    blended_rr: float | None = None
    confidence: float | None = None  # 0..1 — D1-Trend-Stärke · Ausbruch-Wucht · Retest-Tiefe
    # Diagnose
    reasons: tuple[NoTradeReason, ...] = ()
    chain_progress: str = ""
    notes: tuple[str, ...] = field(default_factory=tuple)
    setup_id: str = SETUP_BREAKOUT_RETEST
    strategy_version: str = STRATEGY_VERSION

    @property
    def is_armed(self) -> bool:
        return (
            self.state is BreakoutState.ARMED
            and None not in (self.entry, self.sl, self.tp1, self.tp2)
            and self.direction is not None
        )


def _close_pos(b: OHLCV) -> float:
    rng = b.high - b.low
    return (b.close - b.low) / rng if rng > 0 else 0.5


def _d1_trend(mtf: MtfContext) -> RegimeDirectional:
    d1 = mtf.d1
    if d1 is None:
        return RegimeDirectional.UNCLEAR
    return d1.structure.directional


def _clip01(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


def _confidence(
    mtf: MtfContext, bob: OHLCV, level: float, retest: OHLCV, width: float, atr: float, up: bool
) -> float:
    """0..1 — D1-Trend-Stärke · Wucht des Ausbruch-Bars · Sauberkeit des Retests."""
    d1 = mtf.d1
    d1_score = float(getattr(getattr(d1, "regime", None), "directional_score", 0.0) or 0.0)
    trend_term = _clip01(0.45 + 0.55 * d1_score)
    # Ausbruch-Wucht: Distanz des Close jenseits der Kante, in ATR (0.3 → ~0.4, 1.5 → ~1.0)
    beyond = (bob.close - level) / atr if up else (level - bob.close) / atr
    thrust_term = _clip01(0.3 + 0.5 * beyond)
    # Retest-Sauberkeit: flacher Rücklauf (Close nah an der Kante, aber klar drüber/drunter)
    reclaim = (retest.close - level) / atr if up else (level - retest.close) / atr
    retest_term = _clip01(0.4 + 1.0 * _clip01(reclaim))
    # Range-Reife: nicht zu eng, nicht zu breit
    w = width / atr
    range_term = _clip01(1.0 - abs(w - 2.0) / 3.0)
    val = 0.4 * trend_term + 0.25 * thrust_term + 0.2 * retest_term + 0.15 * range_term
    return round(_clip01(val), 4)


def detect_breakout_retest(
    mtf: MtfContext, *, params: BreakoutRetestParams | None = None
) -> BreakoutRetestReport:
    """Wertet das Breakout-Retest-Muster am **letzten** bestätigten H4-Bar aus."""
    p = params or BreakoutRetestParams()
    inst, cutoff = mtf.instrument, mtf.information_cutoff
    h4c = mtf.h4
    bars: list[OHLCV] = list(h4c.bars) if h4c is not None else []
    trend = _d1_trend(mtf)

    base = BreakoutRetestReport(
        instrument=inst, information_cutoff=cutoff, state=BreakoutState.SCANNING, d1_trend=trend
    )
    need = p.consolidation_bars + p.retest_window_bars + p.atr_period + 5
    if len(bars) < need:
        return base

    i = len(bars) - 1
    # ATR am letzten Bar — bevorzugt der bereits im MtfContext berechnete Wert (kein O(n)-Rescan
    # pro Tick); Fallback auf eine lokale Berechnung (Research-/Test-Kontext ohne .h4.atr).
    a = float(getattr(h4c, "atr", 0.0) or 0.0)
    if a <= 0:
        series = atr_series(bars, p.atr_period)
        a = series[i] or 0.0
    if a <= 0:
        return base

    if p.require_d1_trend and trend not in (
        RegimeDirectional.TREND_UP,
        RegimeDirectional.TREND_DOWN,
    ):
        return BreakoutRetestReport(
            instrument=inst,
            information_cutoff=cutoff,
            state=BreakoutState.SCANNING,
            d1_trend=trend,
            reasons=(NoTradeReason.HTF_TREND_MISALIGNED,),
            chain_progress="warte auf klaren D1-Struktur-Trend",
        )

    look = p.consolidation_bars
    cur = bars[i]
    best: BreakoutRetestReport | None = None

    # Suche einen Ausbruch zwischen i-1 .. i-retest_window; der Retest muss AM Bar i halten.
    for bo in range(i - 1, i - p.retest_window_bars - 1, -1):
        if bo - look < 0:
            break
        cons = bars[bo - look : bo]
        hi = max(b.high for b in cons)
        lo = min(b.low for b in cons)
        width = hi - lo
        if not (p.consolidation_min_atr * a <= width <= p.consolidation_max_atr * a):
            continue
        bob = bars[bo]
        up = bob.close > hi + p.breakout_displacement_atr * a
        dn = bob.close < lo - p.breakout_displacement_atr * a
        if not (up or dn):
            continue
        d = Direction.LONG if up else Direction.SHORT
        # HTF-Trend-Filter (S4)
        if p.require_d1_trend:
            aligned = (trend is RegimeDirectional.TREND_UP and up) or (
                trend is RegimeDirectional.TREND_DOWN and dn
            )
            if not aligned:
                continue
        level = hi if up else lo

        # kein früherer Retest zwischen bo+1..i-1 (der erste Retest zählt)
        earlier = any(
            (bars[r].low <= level + p.retest_touch_atr * a)
            if up
            else (bars[r].high >= level - p.retest_touch_atr * a)
            for r in range(bo + 1, i)
        )
        touched_now = (
            (cur.low <= level + p.retest_touch_atr * a)
            if up
            else (cur.high >= level - p.retest_touch_atr * a)
        )
        held = (cur.close > level) if up else (cur.close < level)
        closes_dir = _close_pos(cur) > 0.5 if up else _close_pos(cur) < 0.5

        holds_now = touched_now and held and closes_dir
        if earlier and not holds_now:
            # Retest ist schon passiert (an einer Vorbar) → ARMED nur, wenn er JETZT hält
            if best is None:
                best = BreakoutRetestReport(
                    instrument=inst,
                    information_cutoff=cutoff,
                    state=BreakoutState.INVALIDATED,
                    direction=d,
                    consolidation_low=lo,
                    consolidation_high=hi,
                    broken_level=level,
                    breakout_bar=bob.open_time,
                    d1_trend=trend,
                    reasons=(NoTradeReason.NO_RETEST,),
                    chain_progress="Retest-Fenster ohne haltenden Retest",
                )
            continue

        if touched_now and held and closes_dir:
            stop = (
                (min(cur.low, level) - p.stop_buffer_atr * a)
                if up
                else (max(cur.high, level) + p.stop_buffer_atr * a)
            )
            entry = cur.close
            r_dist = abs(entry - stop)
            if r_dist < p.min_stop_atr * a:
                best = _reject(
                    base,
                    d,
                    lo,
                    hi,
                    level,
                    bob.open_time,
                    trend,
                    NoTradeReason.SL_TOO_WIDE,
                    "SL zu eng",
                )
                continue
            sign = 1.0 if up else -1.0
            tp1 = entry + sign * p.tp1_r * r_dist
            tp2 = entry + sign * p.tp2_r * r_dist
            rr_to_tp2 = abs(tp2 - entry) / r_dist
            blended = (
                p.tp1_size_pct / 100.0 * p.tp1_r
                + p.tp2_size_pct / 100.0 * p.tp2_r
                + p.tp3_size_pct / 100.0 * p.tp3_assumed_r
            )
            if rr_to_tp2 < p.min_rr_to_tp2:
                best = _reject(
                    base,
                    d,
                    lo,
                    hi,
                    level,
                    bob.open_time,
                    trend,
                    NoTradeReason.RR_BELOW_MIN,
                    f"RR {rr_to_tp2:.2f} < {p.min_rr_to_tp2}",
                )
                continue
            confidence = _confidence(mtf, bob, level, cur, width, a, up)
            return BreakoutRetestReport(
                instrument=inst,
                information_cutoff=cutoff,
                state=BreakoutState.ARMED,
                direction=d,
                consolidation_low=lo,
                consolidation_high=hi,
                broken_level=level,
                breakout_bar=bob.open_time,
                retest_bar=cur.open_time,
                d1_trend=trend,
                entry=round(entry, 8),
                sl=round(stop, 8),
                tp1=round(tp1, 8),
                tp2=round(tp2, 8),
                tp3_ref="Runner: Trailing H4-Swing, aktiv nach TP2",
                rr_to_tp2=round(rr_to_tp2, 4),
                blended_rr=round(blended, 4),
                confidence=confidence,
                chain_progress=(
                    f"Konsolidierung {width / a:.1f}·ATR → Ausbruch {d.value} → Retest hält"
                ),
            )

        # Ausbruch da, aber (noch) kein haltender Retest an Bar i
        if best is None or best.state is BreakoutState.SCANNING:
            best = BreakoutRetestReport(
                instrument=inst,
                information_cutoff=cutoff,
                state=BreakoutState.AWAIT_RETEST,
                direction=d,
                consolidation_low=lo,
                consolidation_high=hi,
                broken_level=level,
                breakout_bar=bob.open_time,
                d1_trend=trend,
                chain_progress=f"Ausbruch {d.value} @ {level:g} — warte auf Retest",
            )

    return best or base


def _reject(
    base: BreakoutRetestReport,
    d: Direction,
    lo: float,
    hi: float,
    level: float,
    bo: datetime,
    trend: RegimeDirectional,
    reason: NoTradeReason,
    note: str,
) -> BreakoutRetestReport:
    return BreakoutRetestReport(
        instrument=base.instrument,
        information_cutoff=base.information_cutoff,
        state=BreakoutState.AWAIT_RETEST,
        direction=d,
        consolidation_low=lo,
        consolidation_high=hi,
        broken_level=level,
        breakout_bar=bo,
        d1_trend=trend,
        reasons=(reason,),
        chain_progress=note,
    )


__all__ = [
    "SETUP_BREAKOUT_RETEST",
    "BreakoutRetestParams",
    "BreakoutRetestReport",
    "BreakoutState",
    "detect_breakout_retest",
]
