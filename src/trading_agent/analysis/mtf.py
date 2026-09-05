"""Multi-Timeframe-Assembly — M5-Basis → M15 / H4 / D1 (``docs/strategy/`` 0.1.1, C11).

Führt die vorhandene Resample-Infrastruktur (``data/resample.py``) mit den Analyse-Bausteinen
(``strategy.primitives``, ``analysis.regime``, ``analysis.sessions``) zu einem **look-ahead-freien**
MTF-Kontext zusammen, den die Setup-FSM direkt konsumiert.

Regeln:
* nur **abgeschlossene** Bars (``close_time <= information_cutoff``); höhere TFs per
  ``resample_ohlcv(require_complete=True, horizon=cutoff)``.
* korrektes Timestamp-Alignment (via ``align_down`` im Resampler).
* Datenqualität je TF (``data/quality``): Lücken / stale / Duplikate ⇒ Befund + niedrigere
  ``data_confidence``; ``blocks_trading`` wird weitergereicht.
* fehlende Historie ⇒ ``data_confidence`` sinkt (nicht: Absturz).

Diese Datei trifft **keine** Entry-Entscheidung. Sie liefert je TF: Struktur, Regime, Bias,
Liquidität, Premium/Discount + einen aggregierten HTF-Regime-Gate.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence
from datetime import datetime

from trading_agent.analysis.regime import (
    RegimeGateParams,
    RegimeGateResult,
    RegimeParams,
    RegimeState,
    merge_htf,
    raw_regime,
    regime_gate,
)
from trading_agent.analysis.sessions import completed_sessions, session_levels
from trading_agent.core.enums import (
    AssetClass,
    Bias,
    DataQualitySeverity,
    LiquidityType,
    MarketSide,
    PDReference,
    RegimeDirectional,
    Timeframe,
)
from trading_agent.core.models import OHLCV, DataQualityStatus, SessionWindow
from trading_agent.core.time import ensure_utc
from trading_agent.core.types import (
    CrossAssetContext,
    DerivativesContext,
    MarketContext,
    NewsContext,
)
from trading_agent.data.quality import check_ohlcv_series, sort_ohlcv
from trading_agent.data.resample import resample_ohlcv
from trading_agent.refdata.models import SessionSpec
from trading_agent.strategy.primitives.atr import ATR_PERIOD_DEFAULT, atr
from trading_agent.strategy.primitives.blocks import find_order_blocks
from trading_agent.strategy.primitives.imbalance import find_displacements, find_fvgs
from trading_agent.strategy.primitives.liquidity import (
    apply_state,
    equal_level_clusters,
    previous_period_levels,
    score_level,
    swing_levels,
)
from trading_agent.strategy.primitives.models import (
    FVG,
    Displacement,
    LiquidityLevel,
    OrderBlock,
    StructureBreak,
    StructureState,
    SwingPoint,
)
from trading_agent.strategy.primitives.pd import dealing_range, premium_discount
from trading_agent.strategy.primitives.structure import (
    derive_structure_state,
    structure_breaks,
)
from trading_agent.strategy.primitives.swings import detect_swings

# H1 war hier nie enthalten — die Analyse sprang von 4 Stunden direkt auf 15 Minuten.
# Fuer Swing-Entscheidungen ist H1 aber die Bruecke zwischen Tagesbild und Ausfuehrung:
# dort zeigt sich, ob ein Bruch im H4-Bild von der naechstkleineren Ebene getragen wird.
MTF_TF_ORDER: tuple[Timeframe, ...] = (
    Timeframe.M5,
    Timeframe.M15,
    Timeframe.H1,
    Timeframe.H4,
    Timeframe.D1,
)
_HTF: tuple[Timeframe, ...] = (Timeframe.D1, Timeframe.H4)
_HIGHER: tuple[Timeframe, ...] = (Timeframe.M15, Timeframe.H1, Timeframe.H4, Timeframe.D1)


class MtfError(ValueError):
    pass


def _default_min_bars() -> dict[Timeframe, int]:
    return {
        Timeframe.M5: 200,
        Timeframe.M15: 200,
        Timeframe.H1: 200,
        Timeframe.H4: 120,
        Timeframe.D1: 120,
    }


@dataclasses.dataclass(frozen=True, slots=True)
class MtfParams:
    tick_size: float = 0.1
    atr_period: int = ATR_PERIOD_DEFAULT
    stale_factor: float = 1.5  # letzter Bar älter als stale_factor·Δ ⇒ stale
    min_bars: dict[Timeframe, int] = dataclasses.field(default_factory=_default_min_bars)
    regime: RegimeParams = dataclasses.field(default_factory=RegimeParams)
    regime_gate: RegimeGateParams = dataclasses.field(default_factory=RegimeGateParams)
    session_specs: tuple[SessionSpec, ...] = ()


@dataclasses.dataclass(frozen=True, slots=True)
class DataQualityTerms:
    """Die drei Roh-Terme hinter ``TimeframeContext.data_confidence`` (``confidence.md`` §2)."""

    completeness: float
    freshness: float
    consistency: float

    @property
    def value(self) -> float:
        return min(self.completeness, self.freshness, self.consistency)


@dataclasses.dataclass(frozen=True, slots=True)
class TimeframeContext:
    """Analyse-Ergebnis für einen Timeframe — Stand = letzter ``confirmed`` Bar, look-ahead-frei."""

    timeframe: Timeframe
    bars: tuple[OHLCV, ...]
    swings: tuple[SwingPoint, ...]
    structure: StructureState
    structure_breaks: tuple[StructureBreak, ...]
    regime: RegimeState
    bias: Bias  # aus dem TF-Regime abgeleitet (Merge geschieht auf MtfContext-Ebene)
    premium_discount: object | None  # PremiumDiscount | None
    liquidity: tuple[LiquidityLevel, ...]
    fvgs: tuple[FVG, ...]
    displacements: tuple[Displacement, ...]
    order_blocks: tuple[OrderBlock, ...]
    quality: DataQualityStatus
    data_confidence: float
    atr: float
    data_terms: DataQualityTerms = dataclasses.field(
        default_factory=lambda: DataQualityTerms(1.0, 1.0, 1.0)
    )

    @property
    def blocks_trading(self) -> bool:
        return self.quality.blocks_trading

    @property
    def last_close(self) -> float | None:
        return self.bars[-1].close if self.bars else None


@dataclasses.dataclass(frozen=True, slots=True)
class MtfContext:
    """Der vollständige MTF-Kontext für die Setup-FSM (keine Entry-Entscheidung)."""

    instrument: str
    information_cutoff: datetime
    base_timeframe: Timeframe
    per_tf: dict[Timeframe, TimeframeContext]
    htf_regime_gate: RegimeGateResult
    htf_directional: RegimeDirectional
    htf_bias: Bias
    data_confidence: float  # min über alle TFs
    analysis_confidence: float  # vorläufig — volle Berechnung folgt mit strategy/confidence.py
    issues: tuple[str, ...]
    market_context: MarketContext  # rohe Serien + Zusatzdaten-Slots

    def tf(self, timeframe: Timeframe) -> TimeframeContext | None:
        return self.per_tf.get(timeframe)

    @property
    def d1(self) -> TimeframeContext | None:
        return self.per_tf.get(Timeframe.D1)

    @property
    def h4(self) -> TimeframeContext | None:
        return self.per_tf.get(Timeframe.H4)

    @property
    def m15(self) -> TimeframeContext | None:
        return self.per_tf.get(Timeframe.M15)

    @property
    def m5(self) -> TimeframeContext | None:
        return self.per_tf.get(Timeframe.M5)

    @property
    def regime_ok(self) -> bool:
        return self.htf_regime_gate.ok


# ------------------------------------------------------------------------------- Builder


def build_mtf_context(
    m5_bars: Sequence[OHLCV],
    *,
    instrument: str,
    asset_class: AssetClass,
    now: datetime | None = None,
    native_higher: Mapping[Timeframe, Sequence[OHLCV]] | None = None,
    spread: float | None = None,
    account_equity: float | None = None,
    derivatives: DerivativesContext | None = None,
    cross_asset: CrossAssetContext | None = None,
    news: NewsContext | None = None,
    params: MtfParams | None = None,
    analysis_cache: dict[tuple[object, ...], TimeframeContext] | None = None,
) -> MtfContext:
    """Baut den MTF-Kontext aus M5-Bars. ``native_higher`` erlaubt nativ geladene M15/H4/D1-Serien
    (sonst werden sie aus M5 abgeleitet). Zusatzdaten-Slots bleiben leer, wenn nicht übergeben.

    ``analysis_cache`` (optional, vom Aufrufer gehalten): memoisiert die **höheren** TF-Analysen
    (M15/H4/D1). Zwischen zwei Replay-Ticks (5 min) ändert sich die D1-Analyse nur alle 288 Ticks
    — der Cache spart diese Wiederholung. M5 wird **nie** gecacht. Der Schlüssel identifiziert das
    exakte Bar-Fenster + den `cutoff`-Bucket (Frische) — **kein** Korrektheitsverlust.
    """
    p = params or MtfParams()
    m5 = [b for b in sort_ohlcv(list(m5_bars)) if b.timeframe is Timeframe.M5]
    if not m5:
        raise MtfError("keine M5-Bars")
    cutoff = ensure_utc(now) if now is not None else m5[-1].close_time
    m5 = [b for b in m5 if b.close_time <= cutoff]
    if not m5:
        raise MtfError("keine M5-Bars <= information_cutoff")

    series: dict[Timeframe, list[OHLCV]] = {Timeframe.M5: m5}
    for tf in _HIGHER:
        native = None if native_higher is None else native_higher.get(tf)
        if native:
            series[tf] = [
                b for b in sort_ohlcv(list(native)) if b.timeframe is tf and b.close_time <= cutoff
            ]
        else:
            series[tf] = resample_ohlcv(m5, Timeframe.M5, tf, require_complete=True, horizon=cutoff)

    d1_bars = series[Timeframe.D1]
    session_windows = completed_sessions(m5, list(p.session_specs)) if p.session_specs else []

    d1_key = (d1_bars[0].open_time, d1_bars[-1].open_time, len(d1_bars)) if d1_bars else ()
    per_tf: dict[Timeframe, TimeframeContext] = {}
    issues: list[str] = []
    for tf in MTF_TF_ORDER:
        bars_tf = series[tf]
        ctx: TimeframeContext | None = None
        key: tuple[object, ...] | None = None
        # Nur cachen, wenn die letzte Bar dieser TF frisch ist (Alter ≤ 1 Periode).
        # Dann ist der ``freshness``-Term im Bucket beweisbar konstant 1.0; bei einer
        # Datenlücke (Alter > 1 Periode) zerfällt freshness *innerhalb* des Buckets →
        # dort nicht cachen, um kein veraltetes data_confidence zu servieren.
        fresh_tail = (
            (cutoff - bars_tf[-1].close_time).total_seconds() <= tf.seconds if bars_tf else False
        )
        if analysis_cache is not None and tf is not Timeframe.M5 and bars_tf and fresh_tail:
            key = (
                instrument,
                tf.value,
                asset_class.value,
                bars_tf[0].open_time,
                bars_tf[-1].open_time,
                len(bars_tf),
                int(cutoff.timestamp()) // tf.seconds,  # Frische-Bucket
                () if tf is Timeframe.D1 else d1_key,  # previous_period_levels-Abhängigkeit
                len(session_windows),
            )
            ctx = analysis_cache.get(key)
        if ctx is None:
            ctx = _analyze_tf(
                bars_tf,
                tf,
                asset_class,
                cutoff,
                instrument=instrument,
                d1_bars=d1_bars,
                session_windows=session_windows,
                params=p,
            )
            if key is not None and analysis_cache is not None:
                if len(analysis_cache) > 4096:  # simple Obergrenze
                    analysis_cache.clear()
                analysis_cache[key] = ctx
        per_tf[tf] = ctx
        issues.extend(_tf_issues(ctx, p))

    d1c, h4c = per_tf[Timeframe.D1], per_tf[Timeframe.H4]
    m15c = per_tf.get(Timeframe.M15)
    gate = regime_gate(
        d1c.regime,
        h4c.regime,
        m15c.regime if m15c is not None else None,
        params=p.regime_gate,
    )
    merged = merge_htf(d1c.regime.directional, h4c.regime.directional)
    htf_bias = _bias_from_directional(merged)

    data_conf = min(c.data_confidence for c in per_tf.values())
    analysis_conf = _provisional_analysis_confidence(per_tf)

    market = MarketContext(
        instrument=instrument,
        base_timeframe=Timeframe.M5,
        information_cutoff=cutoff,
        series={tf: tuple(bs) for tf, bs in series.items()},
        spread=spread,
        account_equity=account_equity,
        derivatives=derivatives or DerivativesContext(),
        cross_asset=cross_asset or CrossAssetContext(),
        news=news or NewsContext(),
    )
    return MtfContext(
        instrument=instrument,
        information_cutoff=cutoff,
        base_timeframe=Timeframe.M5,
        per_tf=per_tf,
        htf_regime_gate=gate,
        htf_directional=merged,
        htf_bias=htf_bias,
        data_confidence=data_conf,
        analysis_confidence=analysis_conf,
        issues=tuple(issues),
        market_context=market,
    )


# ------------------------------------------------------------------------------- pro TF


def _analyze_tf(
    bars: Sequence[OHLCV],
    tf: Timeframe,
    asset_class: AssetClass,
    cutoff: datetime,
    *,
    instrument: str,
    d1_bars: Sequence[OHLCV],
    session_windows: Sequence[SessionWindow],
    params: MtfParams,
) -> TimeframeContext:
    blist = list(bars)
    quality = check_ohlcv_series(blist, instrument=instrument, timeframe=tf, now=cutoff)
    a = atr(blist, params.atr_period) or 0.0
    swings = detect_swings(blist, tf)
    breaks = structure_breaks(blist, swings, tf)
    structure = derive_structure_state(swings, tf, min_swings=params.regime.trend.min_swings)
    fvgs = find_fvgs(blist, tf, tick_size=params.tick_size)
    disps = find_displacements(blist, tf, fvgs, breaks=breaks)
    obs = find_order_blocks(blist, tf, disps, breaks)
    regime = raw_regime(
        blist,
        swings,
        breaks,
        disps,
        timeframe=tf,
        asset_class=asset_class,
        now=cutoff,
        params=params.regime,
    )

    levels: list[LiquidityLevel] = list(swing_levels(swings, tf))
    levels += equal_level_clusters(swings, tf, atr=a or 1.0, tick_size=params.tick_size)
    if d1_bars:
        levels += previous_period_levels(list(d1_bars), kind="day")
    if tf in _HTF and session_windows:
        levels += session_levels(list(session_windows), timeframe=tf)
    if regime.range_low is not None and regime.range_high is not None:
        levels += _range_levels(regime.range_low, regime.range_high, tf, regime.computed_at)

    scored: list[LiquidityLevel] = []
    for lvl in levels:
        s = score_level(lvl, blist, atr=a) if a > 0 else lvl
        s, _sweep = apply_state(s, blist)
        scored.append(s)

    dr = dealing_range(swings)
    # Warmup zu kurz für diese TF (z. B. noch keine vollständige D1-Kerze) ⇒ kein Preis,
    # kein Premium/Discount. `quality` meldet EMPTY_SERIES, `_data_quality_terms` → 0 ⇒ die
    # Pipeline blockt sauber über data_confidence, statt hier zu crashen.
    price = blist[-1].close if blist else 0.0
    pd = (
        premium_discount(price, dr[0], dr[1], reference=PDReference.DEALING_RANGE, reference_tf=tf)
        if dr is not None and blist
        else None
    )
    terms = _data_quality_terms(blist, tf, cutoff, quality, params)

    return TimeframeContext(
        timeframe=tf,
        bars=tuple(blist),
        swings=tuple(swings),
        structure=structure,
        structure_breaks=tuple(breaks),
        regime=regime,
        bias=_bias_from_directional(regime.directional),
        premium_discount=pd,
        liquidity=tuple(scored),
        fvgs=tuple(fvgs),
        displacements=tuple(disps),
        order_blocks=tuple(obs),
        quality=quality,
        data_confidence=terms.value,
        atr=a,
        data_terms=terms,
    )


def _range_levels(
    rlow: float, rhigh: float, tf: Timeframe, formed_at: datetime
) -> list[LiquidityLevel]:
    return [
        LiquidityLevel(
            type=LiquidityType.RANGE_HIGH,
            side=MarketSide.BUY_SIDE,
            price=rhigh,
            timeframe=tf,
            formed_at=formed_at,
        ),
        LiquidityLevel(
            type=LiquidityType.RANGE_LOW,
            side=MarketSide.SELL_SIDE,
            price=rlow,
            timeframe=tf,
            formed_at=formed_at,
        ),
    ]


def _bias_from_directional(d: RegimeDirectional) -> Bias:
    if d is RegimeDirectional.TREND_UP:
        return Bias.LONG
    if d is RegimeDirectional.TREND_DOWN:
        return Bias.SHORT
    return Bias.NONE


def _data_quality_terms(
    bars: Sequence[OHLCV],
    tf: Timeframe,
    cutoff: datetime,
    quality: DataQualityStatus,
    params: MtfParams,
) -> DataQualityTerms:
    needed = params.min_bars.get(tf, 100)
    completeness = min(1.0, len(bars) / needed) if needed > 0 else 1.0

    if not bars:
        freshness = 0.0
    else:
        age_periods = (cutoff - bars[-1].close_time).total_seconds() / tf.seconds
        freshness = max(0.0, min(1.0, 1.0 - max(0.0, age_periods - 1.0) / params.stale_factor))

    if quality.blocks_trading:
        consistency = 0.0
    elif quality.worst_severity is DataQualitySeverity.WARNING:
        consistency = 0.6
    else:
        consistency = 1.0

    return DataQualityTerms(completeness=completeness, freshness=freshness, consistency=consistency)


def _tf_issues(ctx: TimeframeContext, params: MtfParams) -> list[str]:
    out: list[str] = []
    needed = params.min_bars.get(ctx.timeframe, 100)
    if len(ctx.bars) < needed:
        out.append(f"{ctx.timeframe.value}: {len(ctx.bars)}/{needed} Bars (Warmup unvollständig)")
    for iss in ctx.quality.issues:
        out.append(f"{ctx.timeframe.value}: {iss.code.value} ({iss.severity.value})")
    if ctx.data_confidence < 0.5:
        out.append(f"{ctx.timeframe.value}: data_confidence {ctx.data_confidence:.2f}")
    return out


def _provisional_analysis_confidence(per_tf: Mapping[Timeframe, TimeframeContext]) -> float:
    """Grober Platzhalter — die volle ``confidence.md``-Berechnung folgt mit ``strategy/confidence``."""

    def term(c: TimeframeContext) -> float:
        d = c.regime.directional
        if d in (RegimeDirectional.TREND_UP, RegimeDirectional.TREND_DOWN):
            return 0.9
        if d is RegimeDirectional.RANGE:
            return 0.6
        return 0.25

    weights = {
        Timeframe.D1: 0.32,
        Timeframe.H4: 0.30,
        Timeframe.H1: 0.18,
        Timeframe.M15: 0.14,
        Timeframe.M5: 0.06,
    }
    total = sum(weights.get(tf, 0.0) for tf in per_tf)
    if total <= 0:
        return 0.0
    return sum(weights.get(tf, 0.0) * term(c) for tf, c in per_tf.items()) / total


__all__ = [
    "MTF_TF_ORDER",
    "DataQualityTerms",
    "MtfContext",
    "MtfError",
    "MtfParams",
    "TimeframeContext",
    "build_mtf_context",
]
