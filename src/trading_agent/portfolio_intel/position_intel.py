"""``PositionIntelligence`` — Bewertung jeder offenen Position 0–100 + Verdikt (Masterplan §36).

Kein Blind-AI: die Bewertung zieht **denselben** ``EvaluationResult`` heran, den die Strategy-
Engine für das Instrument erzeugt (HTF-Trend, Struktur-Confluence, frisches Gegen-Signal), plus
Positions-Fakten (PnL in R, Abstand zur Invalidierung), Portfolio-Kontext (Gewicht, Korrelation).

Verdikt: STRONG_HOLD · HOLD · WATCH · REDUCE · EXIT. (RE_ENTRY_WATCH kommt aus der
``ReEntryEngine`` für bereits geschlossene Positionen.)

**Kein Auto-Verkauf.** ``suggested_action`` ist Text.
"""

from __future__ import annotations

from dataclasses import dataclass

from trading_agent.core.enums import DecisionType, Direction
from trading_agent.portfolio_intel.correlation import CorrelationMatrix
from trading_agent.portfolio_intel.models import Holding, PositionVerdict

_WEIGHTS: dict[str, float] = {
    "pnl_state": 0.15,
    "trend_alignment": 0.22,
    "structure_support": 0.15,
    "invalidation_room": 0.15,
    "opposing_signal": 0.15,
    "concentration": 0.10,
    "correlation_heat": 0.08,
}


@dataclass(frozen=True, slots=True)
class _F:
    name: str
    value: float  # 0..1
    weight: float
    detail: str


@dataclass(frozen=True, slots=True)
class PositionRating:
    instrument: str
    account: str
    verdict: PositionVerdict
    score: float  # 0..100
    unrealized_pct: float
    unrealized_r: float | None
    factors: tuple[_F, ...]
    reasons: tuple[str, ...]
    suggested_action: str
    hard_override: str | None = None  # gesetzt, wenn ein harter Grund das Verdikt erzwungen hat

    def as_dict(self) -> dict[str, object]:
        return {
            "instrument": self.instrument,
            "account": self.account,
            "verdict": self.verdict.value,
            "score": self.score,
            "unrealized_pct": round(self.unrealized_pct, 4),
            "unrealized_r": None if self.unrealized_r is None else round(self.unrealized_r, 3),
            "factors": [
                {"name": f.name, "value": round(f.value, 3), "weight": f.weight, "detail": f.detail}
                for f in self.factors
            ],
            "reasons": list(self.reasons),
            "suggested_action": self.suggested_action,
            "hard_override": self.hard_override,
        }


def _clip01(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


def _group_net(confluence: object, name: str) -> float | None:
    if confluence is None:
        return None
    for g in getattr(confluence, "groups", ()) or ():
        if getattr(getattr(g, "group", None), "name", "") == name and getattr(g, "scored", False):
            return float(getattr(g, "net", 0.0))
    return None


class PositionIntelligence:
    def __init__(
        self,
        *,
        strong_hold: float = 78.0,
        hold: float = 62.0,
        watch: float = 45.0,
        reduce: float = 30.0,
        concentration_soft: float = 0.20,
        concentration_hard: float = 0.35,
    ) -> None:
        self.t_strong = strong_hold
        self.t_hold = hold
        self.t_watch = watch
        self.t_reduce = reduce
        self.conc_soft = concentration_soft
        self.conc_hard = concentration_hard

    def rate(
        self,
        holding: Holding,
        *,
        evaluation: object = None,
        portfolio_weight: float = 0.0,
        correlation: CorrelationMatrix | None = None,
        cohort: tuple[str, ...] = (),
    ) -> PositionRating:
        d = getattr(evaluation, "decision", None)
        mtf = getattr(evaluation, "mtf", None)
        conf = getattr(evaluation, "confluence", None)
        contra = getattr(evaluation, "contradictions", None)
        reasons: list[str] = []
        factors: list[_F] = []
        pos_dir = holding.direction

        # 1) PnL-Zustand
        ur = holding.unrealized_r
        if ur is not None:
            pnl_v = _clip01(0.5 + ur / 6.0)  # +3R → 1.0, -3R → 0.0
            pnl_detail = f"{ur:+.2f}R"
        else:
            pnl_v = _clip01(0.5 + holding.unrealized_pct * 5.0)
            pnl_detail = f"{holding.unrealized_pct:+.2%}"
        factors.append(_F("pnl_state", pnl_v, _WEIGHTS["pnl_state"], pnl_detail))

        # 2) HTF-Trend-Alignment
        htf_val = str(getattr(getattr(mtf, "htf_directional", None), "value", "") or "")
        if htf_val in ("trend_up", "trend_down"):
            trend_dir = Direction.LONG if htf_val == "trend_up" else Direction.SHORT
            aligned = trend_dir is pos_dir
            ta_v = 0.9 if aligned else 0.05
            (
                reasons.append("HTF-Trend trägt die Position")
                if aligned
                else reasons.append("HTF-Trend läuft GEGEN die Position")
            )
        elif htf_val in ("range", ""):
            ta_v = 0.4
        else:  # unclear / conflicting
            ta_v = 0.3
            reasons.append(f"HTF-Regime {htf_val}")
        factors.append(
            _F("trend_alignment", ta_v, _WEIGHTS["trend_alignment"], f"htf={htf_val or '?'}")
        )

        # 3) Struktur-Support (Confluence, Vorzeichen relativ zur Positionsrichtung)
        net = _group_net(conf, "MOMENTUM_STRUCTURE")
        if net is None:
            ss_v = 0.5
            ss_detail = "keine Confluence"
        else:
            signed = net if pos_dir is Direction.LONG else -net
            ss_v = _clip01(0.5 + signed / 2.0)
            ss_detail = f"net={net:+.2f}"
        factors.append(_F("structure_support", ss_v, _WEIGHTS["structure_support"], ss_detail))

        # 4) Abstand zur Invalidierung
        hard_override: str | None = None
        if holding.stop_ref is not None:
            entry_risk = abs(holding.avg_entry_price - holding.stop_ref)
            if entry_risk > 0:
                if pos_dir is Direction.LONG:
                    past = holding.mark_price <= holding.stop_ref
                    room = (holding.mark_price - holding.stop_ref) / entry_risk
                else:
                    past = holding.mark_price >= holding.stop_ref
                    room = (holding.stop_ref - holding.mark_price) / entry_risk
                ir_v = _clip01(room / 2.0)  # 2R Luft → 1.0
                ir_detail = f"{room:+.2f}R bis SL"
                if past:
                    hard_override = "Kurs hat die Invalidierung (SL) durchbrochen"
            else:
                ir_v, ir_detail = 0.5, "SL == Entry"
        else:
            ir_v, ir_detail = 0.4, "kein Stop hinterlegt"
            reasons.append("keine Invalidierung hinterlegt — Risiko unbestimmt")
        factors.append(_F("invalidation_room", ir_v, _WEIGHTS["invalidation_room"], ir_detail))

        # 5) Frisches Gegen-Signal
        opp_v = 0.7
        opp_detail = "kein Gegen-Signal"
        dt = getattr(d, "decision", None)
        d_dir = getattr(d, "direction", None)
        if (
            dt in (DecisionType.BUY, DecisionType.SELL)
            and d_dir is not None
            and d_dir is not pos_dir
        ):
            opp_v = 0.0
            opp_detail = f"frisches {getattr(dt, 'value', dt)}-Signal gegen die Position"
            hard_override = hard_override or "Strategy-Engine gibt ein Gegen-Signal aus"
        else:
            hard_conf = [
                str(getattr(r, "reason", ""))
                for r in getattr(contra, "records", ()) or ()
                if str(getattr(getattr(r, "kind", None), "value", "")) == "hard_conflict"
            ]
            if hard_conf:
                opp_v = 0.2
                opp_detail = f"harter Widerspruch: {hard_conf[0]}"
        factors.append(_F("opposing_signal", opp_v, _WEIGHTS["opposing_signal"], opp_detail))

        # 6) Konzentration
        if portfolio_weight >= self.conc_hard:
            conc_v = 0.0
            reasons.append(
                f"Klumpenrisiko: {portfolio_weight:.0%} des Portfolios in einer Position"
            )
        elif portfolio_weight >= self.conc_soft:
            conc_v = _clip01(
                1.0 - (portfolio_weight - self.conc_soft) / (self.conc_hard - self.conc_soft)
            )
        else:
            conc_v = 1.0
        factors.append(
            _F("concentration", conc_v, _WEIGHTS["concentration"], f"{portfolio_weight:.1%}")
        )

        # 7) Korrelations-Hitze zu anderen Holdings
        if correlation is not None and cohort:
            corrs = [
                abs(correlation.correlation(holding.instrument, o))
                for o in cohort
                if o != holding.instrument
            ]
            peak = max(corrs) if corrs else 0.0
            heat_v = _clip01(1.0 - max(0.0, peak - 0.5) / 0.5)
            heat_detail = f"max|ρ|={peak:.2f}"
            if peak >= 0.7:
                reasons.append(f"stark korreliert mit bestehendem Exposure (ρ={peak:.2f})")
        else:
            heat_v, heat_detail = 0.6, "keine Korrelationsdaten"
        factors.append(_F("correlation_heat", heat_v, _WEIGHTS["correlation_heat"], heat_detail))

        raw = sum(f.value * f.weight for f in factors)
        wsum = sum(f.weight for f in factors)
        score = round(100.0 * raw / wsum, 1) if wsum else 0.0

        if hard_override is not None:
            verdict = PositionVerdict.EXIT
            reasons.insert(0, hard_override)
        elif score >= self.t_strong:
            verdict = PositionVerdict.STRONG_HOLD
        elif score >= self.t_hold:
            verdict = PositionVerdict.HOLD
        elif score >= self.t_watch:
            verdict = PositionVerdict.WATCH
        elif score >= self.t_reduce:
            verdict = PositionVerdict.REDUCE
        else:
            verdict = PositionVerdict.EXIT

        action = {
            PositionVerdict.STRONG_HOLD: "Halten. Stop ggf. nachziehen (Trailing).",
            PositionVerdict.HOLD: "Halten. Beobachten, keine Aktion nötig.",
            PositionVerdict.WATCH: "Enger beobachten. Bei weiterer Schwäche Teilverkauf erwägen.",
            PositionVerdict.REDUCE: "Position verkleinern / Risiko rausnehmen.",
            PositionVerdict.EXIT: "Position schließen — These nicht mehr intakt.",
            PositionVerdict.RE_ENTRY_WATCH: "Auf Wieder-Einstieg warten.",
        }[verdict]

        return PositionRating(
            instrument=holding.instrument,
            account=holding.account,
            verdict=verdict,
            score=score,
            unrealized_pct=holding.unrealized_pct,
            unrealized_r=ur,
            factors=tuple(factors),
            reasons=tuple(reasons),
            suggested_action=action,
            hard_override=hard_override,
        )


__all__ = ["PositionIntelligence", "PositionRating"]
