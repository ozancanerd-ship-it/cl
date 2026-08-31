"""Einzelaktien-Analyse (Masterplan §45–§52) — **nur Einzelwerte, keine ETFs**.

Kombiniert die vorhandenen Primitive zu einem erklärbaren 0–100-Score + Verdikt:

* **Trend**      — D1-Struktur-Regime (HH/HL vs. LH/LL + Slope)
* **Struktur**   — jüngster BOS / Swing-Folge
* **Momentum**   — 63-Tage-ROC + RSI(14)
* **Rel. Stärke**— 63-Tage-Rendite minus Benchmark (S&P 500)
* **Volumen**    — 20T- vs. 60T-Durchschnitt (Akkumulation)
* **Volatilität**— ATR-Perzentil (moderat > extrem)
* **Fundamental**— ``analysis.fundamentals`` (nur wenn Daten da — sonst ausgeschlossen)
* **Earnings**   — ``analysis.earnings`` (Blackout / Drift — nur wenn Termin bekannt)

Fehlt ein Baustein, wird sein Gewicht **herausgenommen** (kein Default-Wert, kein Fake).
Rein lesend, keine Order. Look-ahead-frei: nur Bars mit ``close_time <= as_of``.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from trading_agent.analysis.earnings import EarningsContext
from trading_agent.analysis.fundamentals import FundamentalContext
from trading_agent.core.enums import RegimeDirectional, Timeframe
from trading_agent.core.models import OHLCV
from trading_agent.core.time import ensure_utc
from trading_agent.strategy.primitives.atr import atr_series
from trading_agent.strategy.primitives.structure import derive_structure_state
from trading_agent.strategy.primitives.swings import detect_swings

# Bekannte ETF-/Fonds-Ticker — die Engine handelt sie **nicht** (Masterplan: nur Einzelwerte).
_ETF_TICKERS = frozenset(
    {
        "SPY",
        "QQQ",
        "IWM",
        "DIA",
        "VOO",
        "VTI",
        "IVV",
        "ARKK",
        "XLK",
        "XLF",
        "XLE",
        "GLD",
        "SLV",
        "TLT",
        "HYG",
        "EEM",
        "EFA",
        "VEA",
        "VWO",
        "SPX",
        "NDX",
        "SMH",
        "SOXX",
    }
)


class StockVerdict(StrEnum):
    STRONG_BUY = "strong_buy"
    BUY = "buy"
    HOLD = "hold"
    AVOID = "avoid"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class StockFactor:
    name: str
    value: float  # 0..1
    weight: float
    detail: str


@dataclass(frozen=True, slots=True)
class StockAssessment:
    symbol: str
    as_of: datetime
    score: float  # 0..100
    verdict: StockVerdict
    factors: tuple[StockFactor, ...]
    excluded: tuple[str, ...] = field(default_factory=tuple)
    notes: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "as_of": self.as_of.isoformat(),
            "score": round(self.score, 1),
            "verdict": self.verdict.value,
            "factors": [
                {"name": f.name, "value": round(f.value, 3), "weight": f.weight, "detail": f.detail}
                for f in self.factors
            ],
            "excluded": list(self.excluded),
            "notes": list(self.notes),
        }


# --------------------------------------------------------------------------- Kennzahlen


def _clip01(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


def _rsi(closes: Sequence[float], period: int = 14) -> float | None:
    if len(closes) < period + 1:
        return None
    gains, losses = 0.0, 0.0
    for i in range(-period, 0):
        d = closes[i] - closes[i - 1]
        gains += max(d, 0.0)
        losses += max(-d, 0.0)
    if losses == 0:
        return 100.0
    rs = (gains / period) / (losses / period)
    return 100.0 - 100.0 / (1.0 + rs)


def _roc(closes: Sequence[float], lookback: int) -> float | None:
    if len(closes) <= lookback or closes[-lookback - 1] == 0:
        return None
    return (closes[-1] - closes[-lookback - 1]) / closes[-lookback - 1]


def _percentile_rank(series: Sequence[float], value: float) -> float:
    if not series:
        return 0.5
    return sum(1 for x in series if x <= value) / len(series)


# --------------------------------------------------------------------------- Faktoren


def _trend_factor(d1: list[OHLCV]) -> StockFactor:
    sw = detect_swings(d1, Timeframe.D1, left=2, right=2, min_leg_atr=0.5)
    st = derive_structure_state(sw, Timeframe.D1, min_swings=2).directional
    val = {
        RegimeDirectional.TREND_UP: 1.0,
        RegimeDirectional.RANGE: 0.5,
        RegimeDirectional.UNCLEAR: 0.45,
        RegimeDirectional.TREND_DOWN: 0.1,
    }[st]
    # Slope-Bonus: 50-Tage-Regression-Vorzeichen
    closes = [b.close for b in d1[-50:]]
    if len(closes) >= 20:
        slope = (closes[-1] - closes[0]) / (closes[0] or 1.0)
        val = _clip01(val + (0.1 if slope > 0.05 else -0.1 if slope < -0.05 else 0.0))
    return StockFactor("trend", val, 0.20, f"D1-Regime {st.value}")


def _structure_factor(d1: list[OHLCV]) -> StockFactor:
    highs = [b.high for b in d1[-40:]]
    lows = [b.low for b in d1[-40:]]
    if len(highs) < 20:
        return StockFactor("structure", 0.5, 0.12, "zu wenig Historie")
    hh = highs[-1] >= max(highs[:-1])
    hl = min(lows[-10:]) >= min(lows[-20:-10])
    val = 0.5 + (0.25 if hh else -0.15) + (0.25 if hl else -0.15)
    return StockFactor(
        "structure", _clip01(val), 0.12, f"{'HH' if hh else 'kein HH'}, {'HL' if hl else 'kein HL'}"
    )


def _momentum_factor(d1: list[OHLCV]) -> StockFactor:
    closes = [b.close for b in d1]
    roc63 = _roc(closes, 63)
    rsi = _rsi(closes, 14)
    if roc63 is None or rsi is None:
        return StockFactor("momentum", 0.5, 0.18, "zu wenig Historie")
    roc_hist = [
        (closes[i] - closes[i - 63]) / closes[i - 63]
        for i in range(63, len(closes))
        if closes[i - 63]
    ]
    roc_pct = _percentile_rank(roc_hist, roc63)
    # RSI: 45–70 ideal (Momentum ohne Überhitzung); <35 schwach; >80 überkauft
    rsi_term = _clip01(1.0 - abs(rsi - 58.0) / 42.0)
    val = 0.6 * roc_pct + 0.4 * rsi_term
    return StockFactor(
        "momentum", _clip01(val), 0.18, f"ROC63 {roc63:+.1%} (P{roc_pct:.0%}), RSI {rsi:.0f}"
    )


def _rel_strength_factor(d1: list[OHLCV], bench: list[OHLCV] | None) -> StockFactor | None:
    if not bench:
        return None
    closes = [b.close for b in d1]
    bcloses = [b.close for b in bench]
    r_s = _roc(closes, 63)
    r_b = _roc(bcloses, 63)
    if r_s is None or r_b is None:
        return None
    diff = r_s - r_b
    # RS-Linie (stock/bench) der letzten 20 Tage steigend?
    n = min(len(closes), len(bcloses), 21)
    rs_line = [closes[-k] / bcloses[-k] for k in range(n, 0, -1)]
    rising = rs_line[-1] > rs_line[0] if len(rs_line) >= 2 else False
    val = _clip01(0.5 + 2.0 * diff + (0.15 if rising else -0.15))
    return StockFactor(
        "relative_strength",
        val,
        0.18,
        f"vs Benchmark {diff:+.1%} / RS-Linie {'↑' if rising else '↓'}",
    )


def _volume_factor(d1: list[OHLCV]) -> StockFactor | None:
    vols = [b.volume for b in d1 if b.volume > 0]
    if len(vols) < 60:
        return None
    v20 = statistics.fmean(vols[-20:])
    v60 = statistics.fmean(vols[-60:])
    ratio = v20 / v60 if v60 > 0 else 1.0
    # steigendes Volumen in einem Aufwärtstrend = Akkumulation; 0.85–1.4 = normal
    val = _clip01(0.5 + (ratio - 1.0))
    return StockFactor("volume", val, 0.10, f"V20/V60 {ratio:.2f}")


def _volatility_factor(d1: list[OHLCV]) -> StockFactor:
    atr = [a for a in atr_series(d1, 14) if a is not None]
    if len(atr) < 60:
        return StockFactor("volatility", 0.5, 0.10, "zu wenig Historie")
    cur = atr[-1]
    pct = _percentile_rank(atr[-252:] if len(atr) >= 252 else atr, cur)
    # moderat (P20–P70) bevorzugt; extrem hoch/tief straft
    val = _clip01(1.0 - abs(pct - 0.42) / 0.58)
    return StockFactor("volatility", val, 0.10, f"ATR-Perzentil P{pct:.0%}")


def _fundamental_factor(fc: FundamentalContext | None) -> StockFactor | None:
    if fc is None or not fc.known or fc.composite is None:
        return None
    return StockFactor("fundamentals", _clip01(fc.composite), 0.08, f"{fc.verdict.value}")


def _earnings_factor(ec: EarningsContext | None) -> StockFactor | None:
    if ec is None or ec.days_until is None:
        return None
    if ec.blocks_new_entry:
        return StockFactor("earnings", 0.2, 0.04, f"Blackout ({ec.days_until:.0f}T bis Report)")
    drift = {1: 0.7, 0: 0.5, -1: 0.3}.get(ec.drift_bias, 0.5)
    return StockFactor("earnings", drift, 0.04, f"Drift-Bias {ec.drift_bias:+d}")


# --------------------------------------------------------------------------- Engine


class StockAnalysisEngine:
    """Erklärbare Einzelaktien-Bewertung. Keine Order, keine Empfehlung zum Echtgeld."""

    def analyze(
        self,
        symbol: str,
        d1_bars: Sequence[OHLCV],
        *,
        as_of: datetime,
        benchmark_d1: Sequence[OHLCV] | None = None,
        fundamentals: FundamentalContext | None = None,
        earnings: EarningsContext | None = None,
    ) -> StockAssessment:
        as_of = ensure_utc(as_of)
        notes: list[str] = []
        base = symbol.upper().split("-")[0]
        if base in _ETF_TICKERS:
            return StockAssessment(
                symbol=symbol,
                as_of=as_of,
                score=0.0,
                verdict=StockVerdict.UNKNOWN,
                factors=(),
                notes=(f"{base} ist ein ETF/Index — die Engine handelt nur Einzelwerte.",),
            )

        d1 = [b for b in d1_bars if ensure_utc(b.close_time) <= as_of]
        bench = (
            [b for b in benchmark_d1 if ensure_utc(b.close_time) <= as_of] if benchmark_d1 else None
        )
        if len(d1) < 80:
            return StockAssessment(
                symbol=symbol,
                as_of=as_of,
                score=0.0,
                verdict=StockVerdict.UNKNOWN,
                factors=(),
                notes=(f"zu wenig Historie ({len(d1)} D1-Bars, ≥80 nötig)",),
            )

        candidates: list[StockFactor | None] = [
            _trend_factor(d1),
            _structure_factor(d1),
            _momentum_factor(d1),
            _rel_strength_factor(d1, bench),
            _volume_factor(d1),
            _volatility_factor(d1),
            _fundamental_factor(fundamentals),
            _earnings_factor(earnings),
        ]
        factors = [f for f in candidates if f is not None]
        excluded = [
            n
            for n, f in zip(
                (
                    "trend",
                    "structure",
                    "momentum",
                    "relative_strength",
                    "volume",
                    "volatility",
                    "fundamentals",
                    "earnings",
                ),
                candidates,
                strict=True,
            )
            if f is None
        ]
        if bench is None:
            notes.append("keine Benchmark (SPX) übergeben → relative Stärke ausgeschlossen")
        if fundamentals is None or not fundamentals.known:
            notes.append("keine Fundamentaldaten → nur technische Bewertung")

        wsum = sum(f.weight for f in factors)
        score = 100.0 * sum(f.value * f.weight for f in factors) / wsum if wsum else 0.0
        verdict = (
            StockVerdict.STRONG_BUY
            if score >= 75
            else StockVerdict.BUY
            if score >= 62
            else StockVerdict.HOLD
            if score >= 45
            else StockVerdict.AVOID
        )
        return StockAssessment(
            symbol=symbol,
            as_of=as_of,
            score=round(score, 1),
            verdict=verdict,
            factors=tuple(factors),
            excluded=tuple(excluded),
            notes=tuple(notes),
        )


__all__ = ["StockAnalysisEngine", "StockAssessment", "StockFactor", "StockVerdict"]
