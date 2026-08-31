"""Market Regime — 3 orthogonale Achsen + MTF-Konsens (``docs/strategy/regime.md`` 0.1.1).

* **Directional:** ``TREND_UP`` / ``TREND_DOWN`` (Struktur HH+HL bzw. LH+LL **und** Slope-Bedingung),
  ``RANGE`` (kein BOS, flache Envelope, Berührungen an beiden Grenzen), sonst ``UNCLEAR``.
* **Volatility:** Perzentil-Rang der ATR über ``vol.lookback`` Werte ⇒ ``LOW`` / ``NORMAL`` /
  ``HIGH`` / ``EXTREME`` (Absolut-Grenze ``atr/price`` je Assetklasse **oder** Perzentil ≥ 97).
* **Phase:** ATR-Dynamik (Ableitung, nicht Niveau) ⇒ ``EXPANSION`` / ``COMPRESSION`` (``coiled`` ab
  ``phase.coiled_bars``) / ``NEUTRAL``; ``expansion_direction`` aus BOS/CHoCH im Fenster.

``RegimeTracker`` wendet die **Hysterese** an (§6): ein Wechsel wird erst nach
``hysteresis.min_bars`` aufeinanderfolgenden ``confirmed``-Bars übernommen; Schmitt-Trigger für
``HIGH``-Vol-Austritt und Trend-Austritt; Directional-Cooldown nach jedem Wechsel.

``merge_htf`` / ``regime_gate`` bilden den MTF-Konsens D1+H4 (§7) und das ``NO_TRADE``-Gate (§8/§9).

Look-ahead-frei: alle Eingaben (Bars, Swings, Breaks, Displacements) sind bereits auf den
``information_cutoff`` gekürzt; jede Kennzahl nutzt nur Bars ``<= now``.
"""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Sequence
from datetime import datetime

from trading_agent.core.enums import (
    AssetClass,
    ExpansionDirection,
    NoTradeReason,
    Polarity,
    RegimeDirectional,
    RegimePhase,
    RegimeVolatility,
    StructureBreakKind,
    StructureOrigin,
    SwingLabel,
    Timeframe,
)
from trading_agent.core.models import OHLCV
from trading_agent.strategy.primitives.atr import atr_series
from trading_agent.strategy.primitives.models import Displacement, StructureBreak, SwingPoint
from trading_agent.strategy.primitives.structure import MIN_SWINGS_DEFAULT, derive_structure_state

# --------------------------------------------------------------------------------------------
# Parameter (config/regime.example.yaml, PROPOSED DEFAULT / TO-VALIDATE)
# --------------------------------------------------------------------------------------------

_EXTREME_ATR_RATIO: dict[AssetClass, float] = {
    AssetClass.CRYPTO: 0.08,
    AssetClass.ALTCOIN: 0.08,
    AssetClass.GOLD: 0.04,
    AssetClass.FOREX: 0.02,
    AssetClass.EQUITY: 0.06,
    AssetClass.ETF: 0.05,
}


@dataclasses.dataclass(frozen=True, slots=True)
class TrendParams:
    min_swings: int = MIN_SWINGS_DEFAULT
    slope_window: int = 50
    min_slope: float = 0.05
    slope_saturation: float = 0.20


@dataclasses.dataclass(frozen=True, slots=True)
class RangeParams:
    window: int = 40
    max_height_atr: float = 8.0
    max_slope: float = 0.03
    min_touches: int = 2
    touch_eps_atr: float = 0.15
    min_touch_separation_bars: int = 3


@dataclasses.dataclass(frozen=True, slots=True)
class VolParams:
    atr_period: int = 14
    lookback: int = 100
    low_pct: float = 20.0
    high_pct: float = 80.0
    extreme_pct: float = 97.0
    extreme_atr_ratio: dict[AssetClass, float] = dataclasses.field(
        default_factory=lambda: dict(_EXTREME_ATR_RATIO)
    )


@dataclasses.dataclass(frozen=True, slots=True)
class PhaseParams:
    window: int = 10
    expansion_atr_ratio: float = 1.30
    compression_atr_ratio: float = 0.80
    bandwidth_period: int = 20
    bandwidth_pct: float = 25.0
    min_compression_bars: int = 5
    narrow_bar_atr: float = 0.7
    coiled_bars: int = 10


@dataclasses.dataclass(frozen=True, slots=True)
class HysteresisParams:
    min_bars: int = 3
    vol_exit_pct: float = 70.0
    trend_exit_slope: float = 0.03


@dataclasses.dataclass(frozen=True, slots=True)
class RegimeParams:
    trend: TrendParams = dataclasses.field(default_factory=TrendParams)
    range_: RangeParams = dataclasses.field(default_factory=RangeParams)
    vol: VolParams = dataclasses.field(default_factory=VolParams)
    phase: PhaseParams = dataclasses.field(default_factory=PhaseParams)
    hysteresis: HysteresisParams = dataclasses.field(default_factory=HysteresisParams)
    cooldown_bars: int = 3


@dataclasses.dataclass(frozen=True, slots=True)
class RegimeState:
    timeframe: Timeframe
    directional: RegimeDirectional
    directional_score: float  # trend_strength bzw. range_maturity, 0..1
    volatility: RegimeVolatility
    volatility_pct: float  # 0..100
    phase: RegimePhase
    expansion_direction: ExpansionDirection
    computed_at: datetime
    bars_in_state: int = 0  # aufeinanderfolgende Bars mit dem übernommenen directional-Zustand
    coiled: bool = False
    cooldown_active: bool = False
    slope_norm: float = 0.0
    range_low: float | None = None
    range_high: float | None = None


# --------------------------------------------------------------------------------------------
# Kennzahlen
# --------------------------------------------------------------------------------------------


def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def slope_norm(bars: Sequence[OHLCV], window: int, atr_val: float) -> float | None:
    """Normierte Regressions-Steigung über ``log(close)`` der letzten ``window`` Bars.

    ``slope_norm = regression_slope_per_bar / ATR × price`` — Preisänderung pro Bar in ATR-Einheiten.
    """
    n = min(window, len(bars))
    if n < 2 or atr_val <= 0:
        return None
    seg = bars[-n:]
    ys = [math.log(b.close) for b in seg]
    mx = (n - 1) / 2.0
    my = sum(ys) / n
    sxx = sum((i - mx) ** 2 for i in range(n))
    sxy = sum((i - mx) * (ys[i] - my) for i in range(n))
    if sxx <= 0:
        return None
    slope_per_bar = sxy / sxx
    return slope_per_bar * seg[-1].close / atr_val


def _percentile_rank(values: Sequence[float], current: float) -> float:
    """Perzentil-Rang mit Ties-Mittelung (konstante Serie ⇒ 50, nicht 100)."""
    if not values:
        return 50.0
    below = sum(1 for v in values if v < current)
    equal = sum(1 for v in values if v == current)
    return 100.0 * (below + 0.5 * equal) / len(values)


# --------------------------------------------------------------------------------------------
# §2 Directional
# --------------------------------------------------------------------------------------------


def _structure_term(swings: Sequence[SwingPoint], up: bool) -> float:
    labelled = [
        s.label
        for s in swings
        if s.label in (SwingLabel.HH, SwingLabel.HL, SwingLabel.LH, SwingLabel.LL)
    ]
    if not labelled:
        return 0.0
    recent = labelled[-4:]
    want = (SwingLabel.HH, SwingLabel.HL) if up else (SwingLabel.LH, SwingLabel.LL)
    return sum(1 for lbl in recent if lbl in want) / len(recent)


def _pullback_term(bars: Sequence[OHLCV], swings: Sequence[SwingPoint], up: bool) -> float:
    highs = [s for s in swings if s.is_high]
    lows = [s for s in swings if not s.is_high]
    if not highs or not lows:
        return 0.5
    if up:
        sh, sl = highs[-1], lows[-1]
        leg = sh.price - sl.price
        if leg <= 0 or sh.bar_index >= len(bars):
            return 0.5
        deepest = min((b.low for b in bars[sh.bar_index + 1 :]), default=sh.price)
        retr = (sh.price - deepest) / leg
    else:
        sh, sl = highs[-1], lows[-1]
        leg = sh.price - sl.price
        if leg <= 0 or sl.bar_index >= len(bars):
            return 0.5
        highest = max((b.high for b in bars[sl.bar_index + 1 :]), default=sl.price)
        retr = (highest - sl.price) / leg
    return _clip(1.0 - retr, 0.0, 1.0)


def _range_bounds_and_touches(
    bars: Sequence[OHLCV], p: RangeParams, atr_val: float
) -> tuple[float, float, int, int] | None:
    n = min(p.window, len(bars))
    if n < p.min_touches * 2 or atr_val <= 0:
        return None
    seg = bars[-n:]
    rhigh = max(b.high for b in seg)
    rlow = min(b.low for b in seg)
    eps = p.touch_eps_atr * atr_val

    def _touches(prices: list[float], level: float) -> int:
        cnt, last = 0, -(10**9)
        for i, px in enumerate(prices):
            if abs(px - level) <= eps and i - last >= p.min_touch_separation_bars:
                cnt += 1
                last = i
        return cnt

    top = _touches([b.high for b in seg], rhigh)
    bot = _touches([b.low for b in seg], rlow)
    return rlow, rhigh, top, bot


def directional_regime(
    bars: Sequence[OHLCV],
    swings: Sequence[SwingPoint],
    structure_breaks: Sequence[StructureBreak],
    timeframe: Timeframe,
    *,
    params: RegimeParams,
    atr_val: float,
) -> tuple[RegimeDirectional, float, float, float | None, float | None]:
    """→ (directional, score, slope_norm, range_low, range_high)."""
    tp, rp = params.trend, params.range_
    sn = slope_norm(bars, tp.slope_window, atr_val) or 0.0
    struct = derive_structure_state(swings, timeframe, min_swings=tp.min_swings)

    up = struct.directional is RegimeDirectional.TREND_UP and sn >= tp.min_slope
    down = struct.directional is RegimeDirectional.TREND_DOWN and sn <= -tp.min_slope
    if up or down:
        st = _structure_term(swings, up)
        sl_term = _clip(abs(sn) / tp.slope_saturation, 0.0, 1.0) if tp.slope_saturation > 0 else 0.0
        pb = _pullback_term(bars, swings, up)
        score = _clip(0.5 * st + 0.3 * sl_term + 0.2 * pb, 0.0, 1.0)
        return (
            RegimeDirectional.TREND_UP if up else RegimeDirectional.TREND_DOWN,
            score,
            sn,
            None,
            None,
        )

    # RANGE (§2.2): kein BOS in den letzten range.window Bars
    window_start = len(bars) - min(rp.window, len(bars))
    no_bos = not any(
        b.kind is StructureBreakKind.BOS
        and b.origin is StructureOrigin.TREND  # nur ein *gerichteter* Bruch schließt RANGE aus
        and _bar_index(bars, b.break_bar_timestamp) >= window_start
        for b in structure_breaks
    )
    rb = _range_bounds_and_touches(bars, rp, atr_val)
    if no_bos and rb is not None:
        rlow, rhigh, top, bot = rb
        height_atr = (rhigh - rlow) / atr_val
        flat = abs(sn) <= rp.max_slope
        if (
            height_atr <= rp.max_height_atr
            and flat
            and top >= rp.min_touches
            and bot >= rp.min_touches
        ):
            maturity = _clip(min(top, bot) / 4.0, 0.0, 1.0) * _clip(
                1.0 - height_atr / rp.max_height_atr, 0.0, 1.0
            )
            return RegimeDirectional.RANGE, maturity, sn, rlow, rhigh

    return RegimeDirectional.UNCLEAR, 0.0, sn, None, None


def _bar_index(bars: Sequence[OHLCV], open_time: datetime) -> int:
    for i, b in enumerate(bars):
        if b.open_time == open_time:
            return i
    return -1


# --------------------------------------------------------------------------------------------
# §3 Volatility
# --------------------------------------------------------------------------------------------


def volatility_regime(
    bars: Sequence[OHLCV],
    asset_class: AssetClass,
    *,
    params: RegimeParams,
) -> tuple[RegimeVolatility, float, float]:
    """→ (volatility, volatility_pct, atr_val)."""
    vp = params.vol
    atr = [v for v in atr_series(bars, vp.atr_period) if v is not None]
    if not atr:
        return RegimeVolatility.NORMAL, 50.0, 0.0
    current = atr[-1]
    hist = atr[-vp.lookback :]
    pct = _percentile_rank(hist, current)
    atr_ratio = current / bars[-1].close if bars[-1].close > 0 else 0.0
    extreme_ratio = vp.extreme_atr_ratio.get(asset_class, 0.08)

    if atr_ratio >= extreme_ratio or pct >= vp.extreme_pct:
        state = RegimeVolatility.EXTREME
    elif pct >= vp.high_pct:
        state = RegimeVolatility.HIGH
    elif pct <= vp.low_pct:
        state = RegimeVolatility.LOW
    else:
        state = RegimeVolatility.NORMAL
    return state, pct, current


# --------------------------------------------------------------------------------------------
# §4 Phase
# --------------------------------------------------------------------------------------------


def phase_regime(
    bars: Sequence[OHLCV],
    displacements: Sequence[Displacement],
    structure_breaks: Sequence[StructureBreak],
    *,
    params: RegimeParams,
    atr_val: float,
) -> tuple[RegimePhase, ExpansionDirection, int]:
    """→ (phase, expansion_direction, compression_run_len)."""
    pp = params.phase
    k = pp.window
    atr = [v for v in atr_series(bars, params.vol.atr_period) if v is not None]
    if len(bars) < 2 * k or len(atr) <= k or atr_val <= 0:
        return RegimePhase.NEUTRAL, ExpansionDirection.NONE, 0

    atr_now, atr_past = atr[-1], atr[-1 - k]
    ratio = atr_now / atr_past if atr_past > 0 else 1.0
    rng_recent = max(b.high for b in bars[-k:]) - min(b.low for b in bars[-k:])
    rng_prior = max(b.high for b in bars[-2 * k : -k]) - min(b.low for b in bars[-2 * k : -k])

    win_start = len(bars) - k
    disp_in_win = any(d.end_index >= win_start for d in displacements)
    breaks_in_win = [
        b for b in structure_breaks if _bar_index(bars, b.break_bar_timestamp) >= win_start
    ]

    # Compression-Run: aufeinanderfolgende schmale Bars am aktuellen Rand
    run = 0
    for b in reversed(bars):
        if b.range <= pp.narrow_bar_atr * atr_val:
            run += 1
        else:
            break

    if ratio >= pp.expansion_atr_ratio and rng_recent > rng_prior and disp_in_win:
        direction = ExpansionDirection.NONE
        if breaks_in_win:
            last = breaks_in_win[-1]
            direction = (
                ExpansionDirection.UP
                if last.direction is Polarity.BULLISH
                else ExpansionDirection.DOWN
            )
        return RegimePhase.EXPANSION, direction, run

    bw = _bandwidth_percentile(bars, pp)
    if (
        ratio <= pp.compression_atr_ratio
        and bw <= pp.bandwidth_pct
        and run >= pp.min_compression_bars
    ):
        return RegimePhase.COMPRESSION, ExpansionDirection.NONE, run

    return RegimePhase.NEUTRAL, ExpansionDirection.NONE, run


def _bandwidth_percentile(bars: Sequence[OHLCV], p: PhaseParams) -> float:
    period, look = p.bandwidth_period, 100
    if len(bars) < period + 1:
        return 100.0
    series: list[float] = []
    for end in range(period, len(bars) + 1):
        seg = bars[end - period : end]
        mean_close = sum(b.close for b in seg) / period
        if mean_close <= 0:
            continue
        series.append((max(b.high for b in seg) - min(b.low for b in seg)) / mean_close)
    if not series:
        return 100.0
    return _percentile_rank(series[-look:], series[-1])


# --------------------------------------------------------------------------------------------
# Kombinierter Roh-Zustand
# --------------------------------------------------------------------------------------------


def raw_regime(
    bars: Sequence[OHLCV],
    swings: Sequence[SwingPoint],
    structure_breaks: Sequence[StructureBreak],
    displacements: Sequence[Displacement],
    *,
    timeframe: Timeframe,
    asset_class: AssetClass,
    now: datetime,
    params: RegimeParams | None = None,
) -> RegimeState:
    """Die 3 Achsen für den letzten Bar — **ohne** Hysterese (``bars_in_state = 0``)."""
    p = params or RegimeParams()
    vol, vol_pct, atr_val = volatility_regime(bars, asset_class, params=p)
    directional, dscore, sn, rlow, rhigh = directional_regime(
        bars, swings, structure_breaks, timeframe, params=p, atr_val=atr_val
    )
    phase, exp_dir, run = phase_regime(
        bars, displacements, structure_breaks, params=p, atr_val=atr_val
    )
    coiled = phase is RegimePhase.COMPRESSION and run >= p.phase.coiled_bars
    return RegimeState(
        timeframe=timeframe,
        directional=directional,
        directional_score=dscore,
        volatility=vol,
        volatility_pct=vol_pct,
        phase=phase,
        expansion_direction=exp_dir,
        computed_at=now,
        slope_norm=sn,
        coiled=coiled,
        range_low=rlow,
        range_high=rhigh,
    )


# --------------------------------------------------------------------------------------------
# §6 Hysterese
# --------------------------------------------------------------------------------------------


class RegimeTracker:
    """Wendet ``hysteresis.min_bars``-Bestätigung + Schmitt-Trigger + Directional-Cooldown an.

    ``update`` wird je ``confirmed``-Bar aufgerufen; gibt den **übernommenen** ``RegimeState`` zurück.
    """

    def __init__(
        self,
        timeframe: Timeframe,
        asset_class: AssetClass,
        *,
        params: RegimeParams | None = None,
    ) -> None:
        self.timeframe = timeframe
        self.asset_class = asset_class
        self.params = params or RegimeParams()
        self._dir: RegimeDirectional | None = None
        self._vol: RegimeVolatility | None = None
        self._phase: RegimePhase | None = None
        self._pending: dict[str, tuple[object, int]] = {}
        self._bars_in_dir = 0
        self._cooldown = 0

    def _confirm(self, axis: str, current: object, candidate: object) -> object:
        """Gibt ``candidate`` zurück, sobald er ``min_bars`` mal in Folge kam; sonst ``current``."""
        mb = self.params.hysteresis.min_bars
        if candidate == current or current is None:
            self._pending.pop(axis, None)
            return candidate if current is None else current
        prev = self._pending.get(axis)
        streak = prev[1] + 1 if prev and prev[0] == candidate else 1
        self._pending[axis] = (candidate, streak)
        if streak >= mb:
            self._pending.pop(axis, None)
            return candidate
        return current

    def update(
        self,
        bars: Sequence[OHLCV],
        swings: Sequence[SwingPoint],
        structure_breaks: Sequence[StructureBreak],
        displacements: Sequence[Displacement],
        *,
        now: datetime,
    ) -> RegimeState:
        raw = raw_regime(
            bars,
            swings,
            structure_breaks,
            displacements,
            timeframe=self.timeframe,
            asset_class=self.asset_class,
            now=now,
            params=self.params,
        )
        h = self.params.hysteresis

        # --- Directional mit Schmitt-Trend-Austritt ---
        cand_dir = raw.directional
        _trend = (RegimeDirectional.TREND_UP, RegimeDirectional.TREND_DOWN)
        prev_dir = self._dir
        leaving_trend = prev_dir in _trend and cand_dir not in _trend
        # Trend nur verlassen, wenn |slope_norm| klar unter der Austritts-Schwelle liegt
        if leaving_trend and prev_dir is not None and abs(raw.slope_norm) >= h.trend_exit_slope:
            cand_dir = prev_dir
        new_dir = self._confirm("dir", self._dir, cand_dir)
        assert isinstance(new_dir, RegimeDirectional)

        if new_dir != self._dir and self._dir is not None:
            self._cooldown = self.params.cooldown_bars
            self._bars_in_dir = 1
        else:
            self._bars_in_dir += 1
            if self._cooldown > 0:
                self._cooldown -= 1
        self._dir = new_dir

        # --- Volatility mit Schmitt-HIGH-Austritt ---
        cand_vol = raw.volatility
        leaving_high = self._vol is RegimeVolatility.HIGH and cand_vol in (
            RegimeVolatility.NORMAL,
            RegimeVolatility.LOW,
        )
        if leaving_high and raw.volatility_pct >= h.vol_exit_pct:
            cand_vol = RegimeVolatility.HIGH
        new_vol = self._confirm("vol", self._vol, cand_vol)
        assert isinstance(new_vol, RegimeVolatility)
        self._vol = new_vol

        # --- Phase (min_bars-Bestätigung, kein Schmitt) ---
        new_phase = self._confirm("phase", self._phase, raw.phase)
        assert isinstance(new_phase, RegimePhase)
        self._phase = new_phase

        return dataclasses.replace(
            raw,
            directional=new_dir,
            volatility=new_vol,
            phase=new_phase,
            bars_in_state=self._bars_in_dir,
            cooldown_active=self._cooldown > 0,
        )


# --------------------------------------------------------------------------------------------
# §7 MTF-Konsens  /  §8+§9 Gate
# --------------------------------------------------------------------------------------------

_DIR_NUM: dict[RegimeDirectional, int] = {
    RegimeDirectional.TREND_UP: 1,
    RegimeDirectional.RANGE: 0,
    RegimeDirectional.TREND_DOWN: -1,
}


def disagreement(d1: RegimeDirectional, h4: RegimeDirectional) -> float:
    """§7 Konfliktscore ∈ [0, 1]. ``UNCLEAR`` / ``CONFLICTING`` ⇒ 1.0."""
    if d1 not in _DIR_NUM or h4 not in _DIR_NUM:
        return 1.0
    return abs(_DIR_NUM[d1] - _DIR_NUM[h4]) / 2.0


def merge_htf(d1: RegimeDirectional, h4: RegimeDirectional) -> RegimeDirectional:
    """§7 Merge-Tabelle D1×H4 → HTF-directional."""
    if RegimeDirectional.UNCLEAR in (d1, h4):
        return RegimeDirectional.UNCLEAR
    up = {RegimeDirectional.TREND_UP, RegimeDirectional.RANGE}
    down = {RegimeDirectional.TREND_DOWN, RegimeDirectional.RANGE}
    if d1 is RegimeDirectional.TREND_UP and h4 is RegimeDirectional.TREND_DOWN:
        return RegimeDirectional.CONFLICTING
    if d1 is RegimeDirectional.TREND_DOWN and h4 is RegimeDirectional.TREND_UP:
        return RegimeDirectional.CONFLICTING
    if d1 in up and h4 in up and RegimeDirectional.TREND_UP in (d1, h4):
        return RegimeDirectional.TREND_UP
    if d1 in down and h4 in down and RegimeDirectional.TREND_DOWN in (d1, h4):
        return RegimeDirectional.TREND_DOWN
    return RegimeDirectional.RANGE


@dataclasses.dataclass(frozen=True, slots=True)
class RegimeGateParams:
    allow_unclear_htf: bool = False
    forbid_low_vol: bool = True  # SMC-SWEEP-REV-01: LOW verboten
    forbid_compression: bool = True  # verbotene phase: reine/coiled COMPRESSION
    conflict_max_disagreement: float = 0.0
    # Ob die **context**-TF (M15) mit ihrer Volatilität einen HARTEN EXTREME-Block auslöst.
    # Default True = bisheriges Verhalten. Kalibrierungs-Kandidat (regime-calibration 2026-08,
    # Variante V5): M15 ist Kontext, kein HTF — ein M15-Vol-Spike sollte ein sauberes D1/H4-Setup
    # evtl. nicht vetoen. Bis OOS-Beleg **nicht** umgestellt.
    context_vol_is_hard_block: bool = True


@dataclasses.dataclass(frozen=True, slots=True)
class RegimeGateResult:
    ok: bool
    reason: NoTradeReason | None
    merged_directional: RegimeDirectional
    disagreement: float


def regime_gate(
    d1: RegimeState,
    h4: RegimeState,
    context: RegimeState | None = None,
    *,
    params: RegimeGateParams | None = None,
) -> RegimeGateResult:
    """§8/§9: MTF-Konsens D1+H4 → ``NO_TRADE``-Grund oder Freigabe (+ gemergter HTF-directional)."""
    p = params or RegimeGateParams()
    dis = disagreement(d1.directional, h4.directional)
    considered = [d1, h4] + ([context] if context is not None else [])
    vol_considered = considered if p.context_vol_is_hard_block else [d1, h4]

    def _fail(reason: NoTradeReason) -> RegimeGateResult:
        return RegimeGateResult(False, reason, RegimeDirectional.UNCLEAR, dis)

    if any(s.volatility is RegimeVolatility.EXTREME for s in vol_considered):
        return _fail(NoTradeReason.REGIME_VOL_EXTREME)
    if not p.allow_unclear_htf and RegimeDirectional.UNCLEAR in (d1.directional, h4.directional):
        return _fail(NoTradeReason.REGIME_UNCLEAR)
    opposite = {
        d1.directional,
        h4.directional,
    } == {RegimeDirectional.TREND_UP, RegimeDirectional.TREND_DOWN}
    if opposite and dis > p.conflict_max_disagreement:
        return _fail(NoTradeReason.REGIME_CONFLICTING)

    merged = merge_htf(d1.directional, h4.directional)
    if merged is RegimeDirectional.CONFLICTING:
        return _fail(NoTradeReason.REGIME_CONFLICTING)
    if merged is RegimeDirectional.UNCLEAR:
        return _fail(NoTradeReason.REGIME_UNCLEAR)

    if d1.cooldown_active or h4.cooldown_active:
        return _fail(NoTradeReason.REGIME_COOLDOWN)
    if p.forbid_low_vol and any(s.volatility is RegimeVolatility.LOW for s in (d1, h4)):
        return _fail(NoTradeReason.REGIME_VOL_TOO_LOW)
    if p.forbid_compression and any(
        s.phase is RegimePhase.COMPRESSION and s.coiled for s in considered
    ):
        return _fail(NoTradeReason.REGIME_COMPRESSION)

    return RegimeGateResult(True, None, merged, dis)


__all__ = [
    "HysteresisParams",
    "PhaseParams",
    "RangeParams",
    "RegimeGateParams",
    "RegimeGateResult",
    "RegimeParams",
    "RegimeState",
    "RegimeTracker",
    "TrendParams",
    "VolParams",
    "directional_regime",
    "disagreement",
    "merge_htf",
    "phase_regime",
    "raw_regime",
    "regime_gate",
    "slope_norm",
    "volatility_regime",
]
