"""Opportunity Score — ein **asset-übergreifend vergleichbarer** 0–100-Wert je Instrument
(`Masterplan §5/§6`).

Kein neuer Indikator: der Score verdichtet **vorhandene** Analyse-Ergebnisse aus einem
``EvaluationResult`` (MTF-Kontext, Regime, Confluence, Confidence, Setup-Zustand, Score,
Derivatives, Spread) zu einer Kennzahl + einer erklärbaren Faktor-Bilanz.

    score = 100 · ( w_ctx·Σ(Kontext-Faktoren) + w_setup·(Setup-Reife · Strategie-Score) )

* **Kontext-Faktoren** (immer bewertbar, auch ohne Setup): HTF-Bias-Klarheit, Struktur-Shift,
  Liquidity-Event, Momentum, Entry-Location, Regime-Alignment, Volatilitäts-Regime,
  Derivatives, Spread, Data-Confidence.
* **Setup-Reife** ∈ [0,1] aus dem FSM-State (SCANNING … ARMED). **Strategie-Score** =
  ``score.final_score/100`` (0, wenn kein Kandidat).

Fehlt eine Datenquelle (News, Macro, Fundamentals, Liquidations, Correlation) → der Faktor ist
``available=False`` und fällt aus dem Nenner (kein Fake, keine Annahme).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from trading_agent.core.enums import Direction, RegimeDirectional, RegimeVolatility, Timeframe

_SETUP_READINESS: dict[str, float] = {
    "scanning": 0.05,
    "bias_set": 0.20,
    "liquidity_identified": 0.35,
    "swept": 0.50,
    "reclaimed": 0.65,
    "displaced": 0.78,
    "structure_shifted": 0.90,
    "armed": 1.00,
    "triggered": 1.00,
    "managed": 0.80,
    "closed": 0.0,
    "review": 0.0,
}

# Kontext-Faktor → Gewicht (Summe egal, wird normiert). Masterplan-§6-Faktorliste, soweit
# aus dem EvaluationResult ableitbar.
_CTX_WEIGHTS: dict[str, float] = {
    "htf_bias_clarity": 12.0,
    "structure_shift": 10.0,
    "liquidity_event": 12.0,
    "momentum": 8.0,
    "entry_location": 8.0,
    "regime_alignment": 12.0,
    "volatility_regime": 8.0,
    "mtf_coherence": 8.0,
    "risk_reward": 8.0,
    "derivatives": 6.0,
    "spread_quality": 4.0,
    "data_confidence": 6.0,
}
_W_CTX = 0.6
_W_SETUP = 0.4

# Datenquellen aus dem Masterplan, die es noch nicht gibt → explizit als „nicht bewertet".
_KNOWN_UNAVAILABLE = ("news", "macro", "event_risk", "fundamentals", "liquidations", "correlation")


@dataclass(frozen=True, slots=True)
class OppFactor:
    name: str
    value: float  # 0..1 (bei fehlender Verfügbarkeit ignoriert)
    weight: float
    available: bool
    detail: str = ""


@dataclass(frozen=True, slots=True)
class OpportunityScore:
    instrument: str
    information_cutoff: datetime
    score: float  # 0..100
    direction: Direction | None
    setup_state: str
    setup_readiness: float
    tier: str | None
    strategy_score: float | None
    factors: tuple[OppFactor, ...]
    unavailable: tuple[str, ...]
    headline: str
    asset_class: str = "crypto"
    trading_horizon: str = "swing"

    @property
    def is_actionable(self) -> bool:
        return self.setup_readiness >= 0.90 and (self.tier in ("A+", "A", "B"))


def _clip01(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


def _regime_clarity(directional: RegimeDirectional, score: float) -> float:
    if directional in (RegimeDirectional.TREND_UP, RegimeDirectional.TREND_DOWN):
        return _clip01(0.6 + 0.4 * score)
    if directional is RegimeDirectional.RANGE:
        return _clip01(0.4 + 0.3 * score)
    return 0.15  # UNCLEAR / CONFLICTING


def _vol_quality(v: RegimeVolatility) -> float:
    return {
        RegimeVolatility.LOW: 0.5,
        RegimeVolatility.NORMAL: 1.0,
        RegimeVolatility.HIGH: 0.6,
        RegimeVolatility.EXTREME: 0.15,
    }.get(v, 0.5)


def _group_support(confluence: object, group_name: str) -> float | None:
    """0..1 aus einer Confluence-Gruppe (``support_score``-Skala 0.5=neutral) oder None."""
    if confluence is None:
        return None
    for g in getattr(confluence, "groups", ()) or ():
        if getattr(getattr(g, "group", None), "name", "") == group_name and getattr(
            g, "scored", False
        ):
            # net ∈ [-1,1] → Betrag als „Stärke der Evidenz"
            return _clip01(abs(getattr(g, "net", 0.0)))
    return None


def score_opportunity(
    result: object,
    *,
    spread_atr_ratio: float | None = None,
    asset_class: str = "crypto",
    trading_horizon: str = "swing",
) -> OpportunityScore:
    """``result`` = ``strategy.evaluate.EvaluationResult``."""
    d = getattr(result, "decision", None)
    mtf = getattr(result, "mtf", None)
    conf = getattr(result, "confluence", None)
    confidence = getattr(result, "confidence", None)
    score_rep = getattr(result, "score", None)
    scan = getattr(result, "scan", None)

    instrument = str(getattr(d, "instrument", "") or getattr(mtf, "instrument", "?"))
    _cut = getattr(d, "information_cutoff", None) or getattr(mtf, "information_cutoff", None)
    cutoff: datetime = _cut if isinstance(_cut, datetime) else datetime.now(UTC)
    setup_state = str(getattr(getattr(d, "setup_state", None), "value", "scanning"))
    readiness = _SETUP_READINESS.get(setup_state, 0.1)
    direction = getattr(d, "direction", None) or getattr(scan, "state", None)
    direction = direction if isinstance(direction, Direction) else None
    tier = getattr(getattr(d, "tier", None), "value", None) or getattr(
        getattr(score_rep, "tier", None), "value", None
    )
    strat_score = getattr(score_rep, "final_score", None)

    per_tf = getattr(mtf, "per_tf", {}) or {}
    d1 = per_tf.get(Timeframe.D1)
    h4 = per_tf.get(Timeframe.H4)
    gate = getattr(mtf, "htf_regime_gate", None)

    factors: list[OppFactor] = []

    def add(name: str, value: float | None, detail: str = "") -> None:
        w = _CTX_WEIGHTS[name]
        if value is None:
            factors.append(OppFactor(name, 0.0, w, available=False, detail="nicht verfügbar"))
        else:
            factors.append(OppFactor(name, _clip01(value), w, available=True, detail=detail))

    # HTF-Bias-Klarheit
    htf_dir = getattr(mtf, "htf_directional", None)
    add(
        "htf_bias_clarity",
        _regime_clarity(htf_dir, getattr(getattr(d1, "regime", None), "directional_score", 0.0))
        if htf_dir is not None
        else None,
        f"htf_directional={getattr(htf_dir, 'value', '?')}",
    )
    # Struktur-Shift / Liquidity / Momentum / Location / MTF-Kohärenz aus Confluence
    add("structure_shift", _group_support(conf, "MOMENTUM_STRUCTURE"))
    add("liquidity_event", _group_support(conf, "LIQUIDITY_EVENT"))
    add("momentum", _group_support(conf, "MOMENTUM_STRUCTURE"))
    add("entry_location", _group_support(conf, "LOCATION"))
    add("mtf_coherence", _group_support(conf, "MTF_COHERENCE"))
    # Regime-Alignment aus dem Gate
    if gate is not None:
        add(
            "regime_alignment",
            1.0 - _clip01(getattr(gate, "disagreement", 0.0))
            if getattr(gate, "ok", False)
            else 0.1,
            f"gate_ok={getattr(gate, 'ok', False)} dis={getattr(gate, 'disagreement', 0.0):.2f}",
        )
    else:
        add("regime_alignment", None)
    # Volatilitäts-Regime (H4)
    h4_reg = getattr(h4, "regime", None)
    add(
        "volatility_regime",
        _vol_quality(getattr(h4_reg, "volatility", RegimeVolatility.NORMAL))
        if h4_reg is not None
        else None,
        f"h4_vol={getattr(getattr(h4_reg, 'volatility', None), 'value', '?')}",
    )
    # R:R
    rr = getattr(d, "rr_to_tp2", None) or getattr(d, "blended_rr", None)
    add("risk_reward", _clip01(rr / 4.0) if rr else None, f"rr_to_tp2={rr}")
    # Derivatives
    mc = getattr(mtf, "market_context", None)
    deriv = getattr(mc, "derivatives", None)
    if deriv is not None and getattr(deriv, "funding_rate_as_of", None) is not None:
        fr = abs(getattr(deriv, "funding_rate", 0.0) or 0.0)
        oi_delta = getattr(deriv, "open_interest_delta_pct", None)
        # niedriges Funding + steigendes OI = gesund
        fscore = _clip01(1.0 - fr / 0.001)  # 0.1%+ Funding ⇒ Abzug
        oscore = _clip01(0.5 + (oi_delta or 0.0) / 20.0) if oi_delta is not None else 0.5
        add("derivatives", 0.5 * fscore + 0.5 * oscore, f"funding={fr:.5f} oi_delta={oi_delta}")
    else:
        add("derivatives", None)
    # Spread
    add(
        "spread_quality",
        _clip01(1.0 - (spread_atr_ratio or 0.0) / 0.15) if spread_atr_ratio is not None else None,
        f"spread/atr={spread_atr_ratio}",
    )
    # Data-Confidence
    add(
        "data_confidence",
        getattr(confidence, "data", None)
        if confidence is not None
        else getattr(mtf, "data_confidence", None),
    )

    # --- Aggregation ---
    avail = [f for f in factors if f.available]
    ctx = sum(f.value * f.weight for f in avail) / sum(f.weight for f in avail) if avail else 0.0
    setup_term = readiness * ((strat_score / 100.0) if strat_score is not None else 0.0)
    raw = _W_CTX * ctx + _W_SETUP * setup_term
    score = round(100.0 * _clip01(raw), 1)

    unavailable = tuple([f.name for f in factors if not f.available] + list(_KNOWN_UNAVAILABLE))
    top = sorted(avail, key=lambda f: f.value * f.weight, reverse=True)[:3]
    dir_txt = direction.value if direction else "—"
    headline = (
        f"{setup_state.upper()} {dir_txt} · Kontext {round(ctx * 100)}/100"
        + (f" · Setup-Score {round(strat_score)}" if strat_score is not None else "")
        + (" · " + ", ".join(f.name for f in top) if top else "")
    )

    return OpportunityScore(
        instrument=instrument,
        information_cutoff=cutoff,
        score=score,
        direction=direction,
        setup_state=setup_state,
        setup_readiness=readiness,
        tier=tier,
        strategy_score=strat_score,
        factors=tuple(factors),
        unavailable=unavailable,
        headline=headline,
        asset_class=asset_class,
        trading_horizon=trading_horizon,
    )


@dataclass(slots=True)
class ScoreWeights:
    """Platzhalter für spätere Kalibrierung — aktuell fixe Gewichte (`_CTX_WEIGHTS`)."""

    ctx: dict[str, float] = field(default_factory=lambda: dict(_CTX_WEIGHTS))


__all__ = ["OppFactor", "OpportunityScore", "ScoreWeights", "score_opportunity"]
