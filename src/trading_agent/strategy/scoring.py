"""Setup-Scoring — 0..100 (``scoring-rubric.md`` §1–§4, ``SMC-SWEEP-REV-01`` §21).

**Der Score misst *„wie gut ist die gesamte Trading-Konstellation"*.** Er ist **nicht** Confidence
(*„korrekt erkannt?"*), **nicht** Confluence (*„welche Faktoren stützen die Richtung?"*), **nicht**
Veto (*harte Barriere*), **nicht** Risk. Diese Ebenen bleiben getrennt.

```
raw          = Σ (wᵢ · fᵢ)                       # nur verfügbare WEIGHTED-Faktoren, fᵢ ∈ [0,1]
score_0_100  = 100 · raw / Σ wᵢ
final_score  = clip(score_0_100 − Σ penalties, 0, 100)
```

**MVP (C2 / ``DECISIONS-0.1.0.md`` #4):** **alle** WEIGHTED-Faktoren ``wᵢ = 10``, **alle**
Penalties ``= 0``. Die gestaffelte Gewichtung (``scoring-rubric.md`` §4: 20/14/13/…) ist das
spätere Kalibrierungsziel — **keine Gewichts-Optimierung in Phase 3.**

**Rollen-Exklusivität (Audit R-06):** diese Engine berechnet **ausschließlich** die 12
``WEIGHTED``-Faktoren. ``entry_location`` / ``rr`` / ``sl`` sind je einmal ``GATE`` (an anderer
Stelle) **und** einmal ``WEIGHTED`` (``entry_location_depth`` / ``risk_reward``) — verschiedene
benannte Faktoren, keiner doppelt.

**Keine neuen Indikatoren.** Jeder Faktor bezieht seinen Wert aus einem bereits vorhandenen
``ConfluenceFactor``, aus ``EntryGeometry`` (RR-Gate), aus dem ``MtfContext``-Regime oder aus dem
``ConfidenceReport``. Korrelierte WEIGHTED-Faktoren (z. B. ``liquidity_quality`` + ``sweep_clarity``
+ ``reclaim_quality``) sind in ``correlated_factor_groups`` ausgewiesen — Hinweis für die spätere
Kalibrierung (Gruppen-Cap).

**Tier (§21, aus Score × ``setup_confidence``):**

| Stufe | ``final_score ≥`` | **und** ``setup_confidence ≥`` |
|-------|-------------------|-------------------------------|
| A+    | 85                | 0.80 |
| A     | 75                | 0.70 |
| B     | 65                | 0.60 |
| NO_TRADE | sonst          | — (``SCORE_BELOW_B``) |

**Ein Score überstimmt kein hartes Veto und keine schlechte Datenqualität.** ``vetoed=True`` oder
``data_confidence < 0.50`` ⇒ Tier ``NO_TRADE``, unabhängig von der Punktzahl (der Score wird
trotzdem für das Ledger berechnet).

**Point-in-time / look-ahead-frei / deterministisch:** rein funktional über die (bereits
look-ahead-freien) Eingaben. **Long/Short-symmetrisch** (alle Faktoren sind richtungs-neutrale
Qualitätsmaße).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from datetime import datetime

from trading_agent.analysis.mtf import MtfContext
from trading_agent.core.enums import RegimeDirectional, RegimeVolatility, RiskTier, Timeframe
from trading_agent.core.version import STRATEGY_VERSION
from trading_agent.strategy.confidence import ConfidenceReport
from trading_agent.strategy.confluence import (
    ConfluenceDataQuality,
    ConfluenceFactor,
    ConfluenceGroup,
    ConfluenceReport,
    FactorDirection,
)
from trading_agent.strategy.gates import GateOutcome, GateReport
from trading_agent.strategy.setup_detection import SetupCandidate

# Name → Confluence-Faktor, aus dem der WEIGHTED-Wert übernommen wird (1:1 wiederverwendet).
_FROM_CONFLUENCE: dict[str, str] = {
    "liquidity_quality": "swept_level_quality",
    "sweep_clarity": "sweep_clarity",
    "displacement_strength": "displacement_strength",
    "structure_shift_quality": "structure_shift",
    "fvg_quality": "entry_zone_quality",
    "reclaim_quality": "reclaim_quality",
    "session_context": "session_context",
}
_WEIGHTED_FACTORS: tuple[str, ...] = (
    "htf_bias_strength",
    "liquidity_quality",
    "sweep_clarity",
    "displacement_strength",
    "structure_shift_quality",
    "risk_reward",
    "entry_location_depth",
    "fvg_quality",
    "reclaim_quality",
    "regime_alignment",
    "session_context",
    "data_confidence_bonus",
)
# WEIGHTED-Faktor → Confluence-Gruppe (für correlated_factor_groups)
_FACTOR_GROUP: dict[str, ConfluenceGroup] = {
    "htf_bias_strength": ConfluenceGroup.HTF_BIAS,
    "regime_alignment": ConfluenceGroup.HTF_BIAS,
    "liquidity_quality": ConfluenceGroup.LIQUIDITY_EVENT,
    "sweep_clarity": ConfluenceGroup.LIQUIDITY_EVENT,
    "reclaim_quality": ConfluenceGroup.LIQUIDITY_EVENT,
    "displacement_strength": ConfluenceGroup.MOMENTUM_STRUCTURE,
    "structure_shift_quality": ConfluenceGroup.MOMENTUM_STRUCTURE,
    "fvg_quality": ConfluenceGroup.ENTRY_ZONE,
    "entry_location_depth": ConfluenceGroup.LOCATION,
    "risk_reward": ConfluenceGroup.RISK_REWARD,
    "session_context": ConfluenceGroup.SESSION,
    "data_confidence_bonus": ConfluenceGroup.DATA_QUALITY,
}


def _equal_weights() -> dict[str, float]:
    return {name: 10.0 for name in _WEIGHTED_FACTORS}


# --------------------------------------------------------------------------------- Parameter


@dataclasses.dataclass(frozen=True, slots=True)
class ScoreParams:
    weights: dict[str, float] = dataclasses.field(default_factory=_equal_weights)  # MVP: alle 10
    penalties: dict[str, float] = dataclasses.field(default_factory=dict)  # MVP: leer (= 0)

    tier_score_min: dict[str, float] = dataclasses.field(
        default_factory=lambda: {"A+": 85.0, "A": 75.0, "B": 65.0}
    )
    tier_confidence_min: dict[str, float] = dataclasses.field(
        default_factory=lambda: {"A+": 0.80, "A": 0.70, "B": 0.60}
    )

    rr_min_to_tp2: float = 2.0  # §3.9 Normierung
    max_pd_position: float = 0.50  # §3.8 Normierung
    data_confidence_floor: float = 0.50  # §3.12 + harte Sperre
    htf_timeframes: tuple[Timeframe, ...] = (Timeframe.D1, Timeframe.H4)
    regime_timeframes: tuple[Timeframe, ...] = (Timeframe.D1, Timeframe.H4, Timeframe.M15)


# --------------------------------------------------------------------------------- Ausgabe


@dataclasses.dataclass(frozen=True, slots=True)
class ScoreFactor:
    name: str
    weight: float
    value: float  # fᵢ ∈ [0, 1]
    contribution: float  # weight · value
    source: str
    available: bool
    reason: str


@dataclasses.dataclass(frozen=True, slots=True)
class ScoreReport:
    setup_id: str
    instrument: str
    information_cutoff: datetime
    factors: tuple[ScoreFactor, ...]
    raw: float
    weight_sum: float
    score_0_100: float
    penalties: Mapping[str, float]
    penalties_total: float
    final_score: float
    setup_confidence: float
    tier: RiskTier
    tier_reason: str
    correlated_factor_groups: Mapping[str, tuple[str, ...]]  # Confluence-Gruppe → WEIGHTED-Faktoren
    strategy_version: str = STRATEGY_VERSION

    @property
    def is_tradeable_tier(self) -> bool:
        return self.tier in (RiskTier.A_PLUS, RiskTier.A, RiskTier.B)


# --------------------------------------------------------------------------------- öffentlich


def score_setup(
    mtf: MtfContext,
    candidate: SetupCandidate,
    *,
    confluence: ConfluenceReport,
    confidence: ConfidenceReport,
    gates: GateReport | None = None,
    vetoed: bool = False,
    params: ScoreParams | None = None,
) -> ScoreReport:
    """Berechnet den 0..100-Score + die Risikostufe. Wird **nach** No-Trade/Regime-Gate/Ketten-Gates
    /Vetos aufgerufen (``contradictions.md`` §6, Schritt 6). ``vetoed`` = ein hartes Veto liegt vor
    (Score wird berechnet, Tier ist dann ``NO_TRADE``)."""
    p = params or ScoreParams()
    cutoff = mtf.information_cutoff
    cf = {f.factor: f for f in confluence.factors}

    factors = tuple(
        _factor(name, p, mtf, candidate, cf, confidence, gates) for name in _WEIGHTED_FACTORS
    )
    avail = [f for f in factors if f.available]
    raw = sum(f.contribution for f in avail)
    weight_sum = sum(f.weight for f in avail)
    score_0_100 = 100.0 * raw / weight_sum if weight_sum > 0.0 else 0.0
    penalties_total = float(sum(p.penalties.values()))
    final_score = _clip(score_0_100 - penalties_total, 0.0, 100.0)

    tier, reason = _tier(final_score, confidence, vetoed, p)

    return ScoreReport(
        setup_id=candidate.setup_id,
        instrument=mtf.instrument,
        information_cutoff=cutoff,
        factors=factors,
        raw=round(raw, 6),
        weight_sum=round(weight_sum, 6),
        score_0_100=round(score_0_100, 4),
        penalties=dict(p.penalties),
        penalties_total=round(penalties_total, 4),
        final_score=round(final_score, 4),
        setup_confidence=confidence.setup_confidence,
        tier=tier,
        tier_reason=reason,
        correlated_factor_groups=_correlated_groups(),
    )


# --------------------------------------------------------------------------------- Faktoren


def _factor(
    name: str,
    p: ScoreParams,
    mtf: MtfContext,
    candidate: SetupCandidate,
    cf: Mapping[str, ConfluenceFactor],
    confidence: ConfidenceReport,
    gates: GateReport | None,
) -> ScoreFactor:
    w = p.weights.get(name, 0.0)

    if name in _FROM_CONFLUENCE:
        src_name = _FROM_CONFLUENCE[name]
        val, avail, reason = _confluence_value(cf, src_name)
        return _mk(name, w, val, f"confluence:{src_name}", avail, reason)

    if name == "htf_bias_strength":
        val, reason = _htf_bias_strength(mtf, p)
        return _mk(name, w, val, "regime", True, reason)

    if name == "regime_alignment":
        val, reason = _regime_alignment(mtf, cf, p)
        return _mk(name, w, val, "regime+confluence:phase_alignment", True, reason)

    if name == "risk_reward":
        if gates is None or gates.rr is None or gates.rr.geometry is None:
            return _mk(name, w, 0.0, "rr_gate", False, "RR-Gate/Geometrie nicht verfügbar")
        rr = gates.rr.geometry.rr_to_tp2
        val = _clip((rr - p.rr_min_to_tp2) / p.rr_min_to_tp2, 0.0, 1.0)
        return _mk(name, w, val, "geometry", True, f"RR_to_TP2={rr:.2f}")

    if name == "entry_location_depth":
        if gates is None or gates.location.outcome is not GateOutcome.ALLOW:
            return _mk(name, w, 0.0, "location_gate", False, "Location-Gate nicht ALLOW")
        pos = gates.location.pd_position
        if pos is None:
            return _mk(name, w, 0.0, "location_gate", False, "keine pd_position")
        d = (
            (p.max_pd_position - pos)
            if candidate.direction.sign > 0
            else (pos - (1.0 - p.max_pd_position))
        )
        val = _clip(d / p.max_pd_position, 0.0, 1.0)
        return _mk(name, w, val, "location_gate", True, f"pd_position={pos:.3f}")

    if name == "data_confidence_bonus":
        dc = confidence.data_confidence
        val = _clip(
            (dc - p.data_confidence_floor) / max(1.0 - p.data_confidence_floor, 1e-9), 0.0, 1.0
        )
        return _mk(name, w, val, "confidence", True, f"data_confidence={dc:.2f}")

    return _mk(name, w, 0.0, "-", False, "unbekannter Faktor")  # pragma: no cover


def _confluence_value(cf: Mapping[str, ConfluenceFactor], src: str) -> tuple[float, bool, str]:
    f = cf.get(src)
    if f is None:
        return 0.0, False, f"Confluence-Faktor {src} fehlt"
    if f.data_quality is ConfluenceDataQuality.UNAVAILABLE:
        return 0.0, False, f"{src}: data_quality=unavailable"
    if f.direction is FactorDirection.CONTRADICT:
        return 0.0, True, f"{src}: widersprüchlich (contribution={f.contribution:.3f})"
    return _clip(f.contribution, 0.0, 1.0), True, f"{src}: contribution={f.contribution:.3f}"


def _htf_bias_strength(mtf: MtfContext, p: ScoreParams) -> tuple[float, str]:
    scores = [
        c.regime.directional_score for tf in p.htf_timeframes if (c := mtf.tf(tf)) is not None
    ]
    trend_strength = sum(scores) / len(scores) if scores else 0.0
    dis = mtf.htf_regime_gate.disagreement
    val = _clip(0.6 * trend_strength + 0.4 * (1.0 - dis), 0.0, 1.0)
    return val, f"trend_strength={trend_strength:.2f} disagreement={dis:.2f}"


def _regime_alignment(
    mtf: MtfContext, cf: Mapping[str, ConfluenceFactor], p: ScoreParams
) -> tuple[float, str]:
    dir_scores = [
        c.regime.directional_score for tf in p.htf_timeframes if (c := mtf.tf(tf)) is not None
    ]
    directional = min(dir_scores) if dir_scores else 0.0

    vol_clear = {
        RegimeVolatility.NORMAL: 1.0,
        RegimeVolatility.HIGH: 0.6,
        RegimeVolatility.LOW: 0.2,
        RegimeVolatility.EXTREME: 0.0,
    }
    vols = [c.regime.volatility for tf in p.regime_timeframes if (c := mtf.tf(tf)) is not None]
    vol_term = min((vol_clear.get(v, 0.5) for v in vols), default=0.5)

    phase_f = cf.get("phase_alignment")
    phase_c = phase_f.contribution if phase_f is not None else 0.0
    phase_term = _clip(0.5 * (phase_c + 1.0), 0.0, 1.0)

    val = _clip((directional + vol_term + phase_term) / 3.0, 0.0, 1.0)
    merged = (
        mtf.htf_directional.value
        if mtf.htf_directional is not RegimeDirectional.UNCLEAR
        else "unclear"
    )
    return (
        val,
        f"directional={directional:.2f} vol={vol_term:.2f} phase={phase_term:.2f} ({merged})",
    )


# --------------------------------------------------------------------------------- Tier


def _tier(
    score: float, confidence: ConfidenceReport, vetoed: bool, p: ScoreParams
) -> tuple[RiskTier, str]:
    sc = confidence.setup_confidence
    if vetoed:
        return RiskTier.NO_TRADE, "hartes Veto liegt vor — Score nicht ausschlaggebend"
    if confidence.blocks_data:
        return RiskTier.NO_TRADE, f"data_confidence {confidence.data_confidence:.2f} < 0.50 (V6)"
    if confidence.unconfirmed_swing:
        return RiskTier.NO_TRADE, "unbestätigter beteiligter Swing (confidence.md §5)"
    if score >= p.tier_score_min["A+"] and sc >= p.tier_confidence_min["A+"]:
        return RiskTier.A_PLUS, f"score {score:.1f} ≥ 85 ∧ confidence {sc:.2f} ≥ 0.80"
    if score >= p.tier_score_min["A"] and sc >= p.tier_confidence_min["A"]:
        return RiskTier.A, f"score {score:.1f} ≥ 75 ∧ confidence {sc:.2f} ≥ 0.70"
    if score >= p.tier_score_min["B"] and sc >= p.tier_confidence_min["B"]:
        return RiskTier.B, f"score {score:.1f} ≥ 65 ∧ confidence {sc:.2f} ≥ 0.60"
    return RiskTier.NO_TRADE, f"score {score:.1f} / confidence {sc:.2f} unter B (SCORE_BELOW_B)"


# --------------------------------------------------------------------------------- intern


def _mk(
    name: str, weight: float, value: float, source: str, available: bool, reason: str
) -> ScoreFactor:
    v = _clip(value, 0.0, 1.0)
    return ScoreFactor(
        name=name,
        weight=weight,
        value=round(v, 6),
        contribution=round(weight * v, 6) if available else 0.0,
        source=source,
        available=available,
        reason=reason,
    )


def _correlated_groups() -> dict[str, tuple[str, ...]]:
    by_group: dict[str, list[str]] = {}
    for factor, group in _FACTOR_GROUP.items():
        by_group.setdefault(group.value, []).append(factor)
    return {g: tuple(sorted(members)) for g, members in by_group.items() if len(members) > 1}


def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


__all__ = [
    "ScoreFactor",
    "ScoreParams",
    "ScoreReport",
    "score_setup",
]
