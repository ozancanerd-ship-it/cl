"""Confidence & Unsicherheits-Modell (``confidence.md``).

**Confidence ist getrennt von Score und Confluence.** Der Score misst *„wie gut ist die
Konstellation"*, die Confluence *„welche Faktoren stützen die Richtung"* — die **Confidence** misst
*„wie sicher sind wir, dass wir die Konstellation korrekt erkannt haben"*. Ein Setup braucht
**alle drei**.

```
1. DATA CONFIDENCE      = min(completeness, freshness, consistency, source_term)      (§2, bewusst min)
2. ANALYSIS CONFIDENCE  = Σ wᵢ·termᵢ  über swing_confirmation / structure_clarity /
                          sweep_unambiguity / regime_clarity / htf_mtf_agreement / fvg_integrity  (§3)
3. SETUP CONFIDENCE     = (0.40·data + 0.60·analysis) · floor_penalty                 (§4)
```

**Harte Floors (Spec):** ``data_confidence < 0.50`` ⇒ blockierender Zustand (Veto V6); ``setup_confidence
< 0.60`` ⇒ ``CONFIDENCE_BELOW_MIN``. Zusätzlich (§5): ein **unbestätigter beteiligter Swing**
(``bars_since_confirmation < swing.right``) ⇒ ``unconfirmed_swing`` — der Swing „existiert für
Entscheidungen nicht".

**Kein Double-Counting.** Jeder Analyse-Term misst einen **eigenen** Erkennungs-Aspekt (Zeit /
Bruch-Sauberkeit / Sweep-Eindeutigkeit / Regime-Klarheit / TF-Kohärenz / Zonen-Frische). Dass
Confidence dieselben *Roh*-Eingaben wie Confluence/Score nutzt (z. B. ``break_distance_atr``), ist
**kein** Double-Count: die Fragen sind orthogonal (*„erkannt?"* vs. *„stützt die Richtung?"*).

**Confidence löst kein BUY/SELL aus** — sie geht später zusammen mit Confluence / Veto / Score /
Risk / Setup-State in ``strategy.evaluate()`` ein.

**Point-in-time / look-ahead-frei / deterministisch:** alle Eingaben aus ``MtfContext``
(≤ ``information_cutoff``) + den reinen Gate-/Confluence-Ergebnissen. Rein funktional.
**Long/Short-symmetrisch** (alle Terme sind richtungs-agnostische Erkennungs-Maße).

Alle Zahlen ``PROPOSED DEFAULT`` — Gewichtung 40/60, ``soft_floor`` 0.60, die 6 Analyse-Gewichte,
``single_source_value`` 0.80 usw. sind Startwerte und OOS/Sensitivity zu validieren (``confidence.md``
§9). Log-Odds-/Bayes-Aggregation bleibt Backlog-Kandidat (``docs/CONTINUOUS_IMPROVEMENT.md`` §6c).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from datetime import datetime

from trading_agent.analysis.mtf import MtfContext
from trading_agent.core.enums import (
    RegimeVolatility,
    SwingLabel,
    Timeframe,
    ZoneState,
)
from trading_agent.core.version import STRATEGY_VERSION
from trading_agent.strategy.price_action import ConfirmationScan
from trading_agent.strategy.primitives.models import OrderBlock, SwingPoint
from trading_agent.strategy.setup_detection import SetupCandidate

_Evidence = Mapping[str, str | float | int | bool | None]

_ANALYSIS_TERMS = (
    "swing_confirmation",
    "structure_clarity",
    "sweep_unambiguity",
    "regime_clarity",
    "htf_mtf_agreement",
    "fvg_integrity",
)


def _default_analysis_weights() -> dict[str, float]:
    return {
        "swing_confirmation": 0.20,
        "structure_clarity": 0.20,
        "sweep_unambiguity": 0.20,
        "regime_clarity": 0.15,
        "htf_mtf_agreement": 0.15,
        "fvg_integrity": 0.10,
    }


# --------------------------------------------------------------------------------- Parameter


@dataclasses.dataclass(frozen=True, slots=True)
class ConfidenceParams:
    wd: float = 0.40  # Gewicht data_confidence
    wa: float = 0.60  # Gewicht analysis_confidence
    soft_floor: float = 0.60  # schwache Einzelkomponente ⇒ floor_penalty
    floor_penalty: float = 0.50
    data_hard_floor: float = 0.50  # < ⇒ Veto V6
    min_setup_confidence: float = 0.60  # < ⇒ CONFIDENCE_BELOW_MIN

    single_source_value: float = 0.80  # 1 Quelle
    source_disagree_atr: float = 0.30  # ≥ 2 Quellen, Abweichung darüber ⇒ 0

    swing_right: int = 2  # fraktale Rechts-Bestätigung (primitives.swings)
    structure_min_dist_atr: float = 0.5  # < ⇒ knapper Bruch
    structure_max_dist_atr: float = 4.0  # > ⇒ überdehnter Bruch
    structure_ambiguity_clarity: float = 0.30  # Equal-H/L an der Bruchstelle
    regime_margin_pct: float = 10.0  # Nähe zur Vol-Schwelle
    regime_settle_bars: int = 3  # bars_in_state für „gesetztes" Regime
    mitigation_consumed_threshold: float = 0.50

    analysis_weights: dict[str, float] = dataclasses.field(
        default_factory=_default_analysis_weights
    )
    htf_timeframes: tuple[Timeframe, ...] = (Timeframe.D1, Timeframe.H4)
    sweep_timeframe: Timeframe = Timeframe.M15
    structure_timeframe: Timeframe = Timeframe.M5
    entry_timeframe: Timeframe = Timeframe.M5
    sweep_window_bars: int = 6  # Fenster für „mehrere Pools gesweept"


# --------------------------------------------------------------------------------- Ausgabe


@dataclasses.dataclass(frozen=True, slots=True)
class ConfidenceRecord:
    """Eine Confidence-Komponente (``data`` oder ``analysis``) — ``confidence.md`` §7."""

    kind: str  # "data" | "analysis"
    value: float
    limiting_factor: str  # Name des kleinsten Terms
    terms: Mapping[str, float]
    evidence: _Evidence
    information_cutoff: datetime
    timestamp: datetime | None


@dataclasses.dataclass(frozen=True, slots=True)
class ConfidenceReport:
    instrument: str
    information_cutoff: datetime
    data: ConfidenceRecord
    analysis: ConfidenceRecord
    setup_confidence: float
    floor_penalty_applied: bool
    limiting_factor: str  # global kleinster Term ("data.<t>" / "analysis.<t>")
    blocks_data: bool  # data_confidence < data_hard_floor ⇒ Veto V6
    blocks_setup: bool  # setup_confidence < min_setup_confidence ⇒ CONFIDENCE_BELOW_MIN
    unconfirmed_swing: bool  # §5: beteiligter Swing bars_since_confirmation < swing.right
    strategy_version: str = STRATEGY_VERSION

    @property
    def data_confidence(self) -> float:
        return self.data.value

    @property
    def analysis_confidence(self) -> float:
        return self.analysis.value

    @property
    def blocking(self) -> bool:
        return self.blocks_data or self.blocks_setup or self.unconfirmed_swing


# --------------------------------------------------------------------------------- öffentlich


def assess_confidence(
    mtf: MtfContext,
    candidate: SetupCandidate,
    *,
    confirmation: ConfirmationScan | None = None,
    source_count: int = 1,
    source_disagreement_atr: float | None = None,
    params: ConfidenceParams | None = None,
) -> ConfidenceReport:
    """Berechnet Data- / Analysis- / Setup-Confidence für einen (i. d. R. ``ARMED``) Kandidaten.

    ``confirmation`` wirkt **nicht** auf den Wert (kein Double-Count mit ``structure_clarity``) —
    nur als informatives Evidence-Feld.
    """
    p = params or ConfidenceParams()
    cutoff = mtf.information_cutoff

    data = _data_confidence(mtf, cutoff, source_count, source_disagreement_atr, p)
    analysis, unconfirmed = _analysis_confidence(mtf, candidate, cutoff, confirmation, p)

    weak = data.value < p.soft_floor or analysis.value < p.soft_floor
    floor_penalty = p.floor_penalty if weak else 1.0
    setup_conf = (p.wd * data.value + p.wa * analysis.value) * floor_penalty

    # global kleinster Term
    d_lim, d_val = _min_term(data.terms)
    a_lim, a_val = _min_term(analysis.terms)
    limiting = f"data.{d_lim}" if d_val <= a_val else f"analysis.{a_lim}"

    return ConfidenceReport(
        instrument=mtf.instrument,
        information_cutoff=cutoff,
        data=data,
        analysis=analysis,
        setup_confidence=round(setup_conf, 6),
        floor_penalty_applied=weak,
        limiting_factor=limiting,
        blocks_data=data.value < p.data_hard_floor,
        blocks_setup=setup_conf < p.min_setup_confidence,
        unconfirmed_swing=unconfirmed,
    )


# --------------------------------------------------------------------------------- data_confidence


def _data_confidence(
    mtf: MtfContext,
    cutoff: datetime,
    source_count: int,
    source_disagreement_atr: float | None,
    p: ConfidenceParams,
) -> ConfidenceRecord:
    tfcs = [c for c in mtf.per_tf.values()]
    if not tfcs:
        completeness = freshness = consistency = 0.0
        worst_tf = "-"
    else:
        completeness = min(c.data_terms.completeness for c in tfcs)
        freshness = min(c.data_terms.freshness for c in tfcs)
        consistency = min(c.data_terms.consistency for c in tfcs)
        worst_tf = min(tfcs, key=lambda c: c.data_terms.value).timeframe.value

    if source_count <= 1:
        source_term = p.single_source_value
        source_note = "einzelne Datenquelle"
    elif source_disagreement_atr is not None and source_disagreement_atr > p.source_disagree_atr:
        source_term = 0.0
        source_note = f"Quellen weichen {source_disagreement_atr:.2f}ATR ab"
    else:
        source_term = 1.0
        source_note = f"{source_count} übereinstimmende Quellen"

    terms = {
        "completeness": round(completeness, 6),
        "freshness": round(freshness, 6),
        "consistency": round(consistency, 6),
        "source_term": round(source_term, 6),
    }
    limiting, _ = _min_term(terms)
    return ConfidenceRecord(
        kind="data",
        value=round(min(terms.values()), 6),
        limiting_factor=limiting,
        terms=terms,
        evidence={
            "worst_timeframe": worst_tf,
            "source_count": source_count,
            "source_note": source_note,
            "mtf_data_confidence": round(mtf.data_confidence, 6),
            "issues": "; ".join(mtf.issues[:5]) if mtf.issues else None,
        },
        information_cutoff=cutoff,
        timestamp=_latest_bar_time(mtf, cutoff),
    )


# --------------------------------------------------------------------------------- analysis_confidence


def _analysis_confidence(
    mtf: MtfContext,
    candidate: SetupCandidate,
    cutoff: datetime,
    confirmation: ConfirmationScan | None,
    p: ConfidenceParams,
) -> tuple[ConfidenceRecord, bool]:
    swing_term, unconfirmed, swing_ev = _swing_confirmation(mtf, candidate, cutoff, p)
    struct_term, struct_ev = _structure_clarity(candidate, p)
    sweep_term, sweep_ev = _sweep_unambiguity(mtf, candidate, p)
    regime_term, regime_ev = _regime_clarity(mtf, p)
    agree_term, agree_ev = _htf_mtf_agreement(mtf)
    fvg_term, fvg_ev = _fvg_integrity(candidate, p)

    terms = {
        "swing_confirmation": round(swing_term, 6),
        "structure_clarity": round(struct_term, 6),
        "sweep_unambiguity": round(sweep_term, 6),
        "regime_clarity": round(regime_term, 6),
        "htf_mtf_agreement": round(agree_term, 6),
        "fvg_integrity": round(fvg_term, 6),
    }
    w = p.analysis_weights
    wsum = sum(w.get(t, 0.0) for t in _ANALYSIS_TERMS) or 1.0
    value = sum(w.get(t, 0.0) * terms[t] for t in _ANALYSIS_TERMS) / wsum
    limiting, _ = _min_term(terms)

    return (
        ConfidenceRecord(
            kind="analysis",
            value=round(value, 6),
            limiting_factor=limiting,
            terms=terms,
            evidence={
                **swing_ev,
                **struct_ev,
                **sweep_ev,
                **regime_ev,
                **agree_ev,
                **fvg_ev,
                "weights_sum": round(wsum, 4),
                "confirmation_present": confirmation is not None and confirmation.confirmed,
            },
            information_cutoff=cutoff,
            timestamp=_latest_bar_time(mtf, cutoff),
        ),
        unconfirmed,
    )


def _swing_confirmation(
    mtf: MtfContext, candidate: SetupCandidate, cutoff: datetime, p: ConfidenceParams
) -> tuple[float, bool, dict[str, str | float | int | bool | None]]:
    involved: list[SwingPoint] = []
    if candidate.structure_break is not None and candidate.structure_break.broken_swing is not None:
        involved.append(candidate.structure_break.broken_swing)
    involved.extend(candidate.liquidity.members)
    for tf in (p.structure_timeframe, p.sweep_timeframe):
        c = mtf.tf(tf)
        if c is None:
            continue
        highs = [s for s in c.swings if s.is_high]
        lows = [s for s in c.swings if not s.is_high]
        involved.extend(highs[-1:])
        involved.extend(lows[-1:])

    if not involved:
        return 1.0, False, {"swing_involved": 0, "swing_note": "keine swing-basierten Anker"}

    ratios: list[float] = []
    unconfirmed = False
    for s in involved:
        bars_since = (cutoff - s.confirmed_at).total_seconds() / s.timeframe.seconds
        if bars_since < p.swing_right:
            unconfirmed = True
        ratios.append(_clip(bars_since / max(p.swing_right, 1), 0.0, 1.0))
    return (
        min(ratios),
        unconfirmed,
        {
            "swing_involved": len(involved),
            "swing_min_ratio": round(min(ratios), 4),
            "swing_unconfirmed": unconfirmed,
        },
    )


def _structure_clarity(
    candidate: SetupCandidate, p: ConfidenceParams
) -> tuple[float, dict[str, str | float | int | bool | None]]:
    brk = candidate.structure_break
    if brk is None:
        return 0.5, {"structure_note": "kein Struktur-Bruch am Kandidaten"}
    d = brk.break_distance_atr
    if d < p.structure_min_dist_atr:  # knapper Bruch — Wick-nah
        raw = _clip(0.3 + 0.7 * d / p.structure_min_dist_atr, 0.3, 1.0)
        note = "knapper Bruch"
    elif d > p.structure_max_dist_atr:  # überdehnt (FSM lehnt das i. d. R. schon ab)
        raw = _clip(1.0 - (d - p.structure_max_dist_atr) / p.structure_max_dist_atr, 0.0, 1.0)
        note = "überdehnter Bruch"
    else:
        raw = 1.0
        note = "sauberer Bruch"
    ambiguous = brk.broken_swing is not None and brk.broken_swing.label is SwingLabel.EQUAL
    if ambiguous:
        raw = min(raw, p.structure_ambiguity_clarity)
        note = "Equal-H/L an der Bruchstelle"
    return raw, {
        "structure_break_distance_atr": round(d, 4),
        "structure_kind": brk.kind.value,
        "structure_ambiguous": ambiguous,
        "structure_note": note,
    }


def _sweep_unambiguity(
    mtf: MtfContext, candidate: SetupCandidate, p: ConfidenceParams
) -> tuple[float, dict[str, str | float | int | bool | None]]:
    sweep = candidate.sweep
    if sweep is None:
        return 0.4, {"sweep_note": "kein Sweep am Kandidaten"}
    tfc = mtf.tf(p.sweep_timeframe)
    n_pools = 1
    if tfc is not None:
        window = p.sweep_window_bars * p.sweep_timeframe.seconds
        own_key = (candidate.liquidity.type, round(candidate.liquidity.price, 6))
        for lv in tfc.liquidity:
            if (lv.type, round(lv.price, 6)) == own_key:
                continue
            if lv.swept_at is None:
                continue
            if abs((lv.swept_at - sweep.reclaim_bar).total_seconds()) <= window:
                n_pools += 1
    pool_term = {1: 1.0, 2: 0.5}.get(n_pools, 0.2)
    pen_term = _clip(1.0 - abs(sweep.penetration_depth_atr - 0.525) / 0.475, 0.0, 1.0)
    wick_term = _clip((sweep.wick_ratio - 1.0) / 2.0, 0.0, 1.0)
    raw = pool_term * (pen_term + wick_term) / 2.0
    return raw, {
        "sweep_pools_in_window": n_pools,
        "sweep_penetration_depth_atr": round(sweep.penetration_depth_atr, 4),
        "sweep_wick_ratio": round(sweep.wick_ratio, 4),
        "sweep_pool_term": pool_term,
    }


def _regime_clarity(
    mtf: MtfContext, p: ConfidenceParams
) -> tuple[float, dict[str, str | float | int | bool | None]]:
    vol_clear = {
        RegimeVolatility.NORMAL: 1.0,
        RegimeVolatility.HIGH: 0.8,
        RegimeVolatility.LOW: 0.2,
        RegimeVolatility.EXTREME: 0.0,
    }
    per_tf: list[float] = []
    worst_tf = "-"
    worst_val = 2.0
    for tf in p.htf_timeframes:
        c = mtf.tf(tf)
        if c is None:
            continue
        r = c.regime
        settled = _clip(r.bars_in_state / max(p.regime_settle_bars, 1), 0.0, 1.0)
        vol_term = vol_clear.get(r.volatility, 0.5)
        val = (r.directional_score + settled + vol_term) / 3.0
        per_tf.append(val)
        if val < worst_val:
            worst_val, worst_tf = val, tf.value
    if not per_tf:
        return 0.5, {"regime_note": "keine HTF-Regime-Daten"}
    return min(per_tf), {
        "regime_worst_timeframe": worst_tf,
        "regime_min_value": round(min(per_tf), 4),
    }


def _htf_mtf_agreement(
    mtf: MtfContext,
) -> tuple[float, dict[str, str | float | int | bool | None]]:
    dis = mtf.htf_regime_gate.disagreement
    return _clip(1.0 - dis, 0.0, 1.0), {"mtf_disagreement": round(dis, 4)}


def _fvg_integrity(
    candidate: SetupCandidate, p: ConfidenceParams
) -> tuple[float, dict[str, str | float | int | bool | None]]:
    zone = candidate.entry_zone
    if zone is None:
        return 0.4, {"fvg_note": "keine Entry-Zone"}
    if zone.state is ZoneState.STALE:
        return 0.0, {"fvg_state": "stale"}
    raw = _clip(1.0 - zone.fill_fraction / max(p.mitigation_consumed_threshold, 1e-9), 0.0, 1.0)
    return raw, {
        "fvg_kind": "OB" if isinstance(zone, OrderBlock) else "FVG",
        "fvg_fill_fraction": round(zone.fill_fraction, 4),
        "fvg_state": zone.state.value,
    }


# --------------------------------------------------------------------------------- intern


def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _min_term(terms: Mapping[str, float]) -> tuple[str, float]:
    return min(terms.items(), key=lambda kv: kv[1])


def _latest_bar_time(mtf: MtfContext, fallback: datetime) -> datetime:
    times = [c.bars[-1].close_time for c in mtf.per_tf.values() if c.bars]
    return max(times) if times else fallback


__all__ = [
    "ConfidenceParams",
    "ConfidenceRecord",
    "ConfidenceReport",
    "assess_confidence",
]
