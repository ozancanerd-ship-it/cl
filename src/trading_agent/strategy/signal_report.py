"""``SignalReport`` — das konkrete, menschenlesbare BUY/SELL-Signal (`Masterplan §24`).

Rendert ein ``EvaluationResult`` (mit tradebarer ``Decision``) + optional den
``OpportunityScore`` zu der im Masterplan geforderten Struktur:

    🔥 A+ BUY · XAUUSDT · LONG
    Entry / SL / TP1 / TP2 / TP3(Runner) · R:R · Score · Confidence · Risk
    Setup · Warum · Invalidation · Risiken

Kein neuer Analyse-Schritt — nur Aufbereitung vorhandener Reports (`decision`, `confluence`,
`contradictions`, `candidate`, `mtf`). **Erzeugt nichts, wenn ``decision`` nicht BUY/SELL ist.**
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from trading_agent.core.enums import DecisionType, Direction

_TIER_ICON = {"A+": "🔥", "A": "🎯", "B": "•"}


@dataclass(frozen=True, slots=True)
class SignalReport:
    instrument: str
    information_cutoff: datetime
    action: str  # "BUY" | "SELL"
    direction: str  # "LONG" | "SHORT"
    tier: str | None
    entry: float
    stop_loss: float
    tp1: float
    tp2: float
    tp3: str  # Runner-Beschreibung (kein fester Preis — Strategy-Spec)
    tp3_indicative: float | None  # nächste signifikante Liquidität jenseits TP2 (NUR Anzeige)
    rr_to_tp2: float | None
    blended_rr: float | None
    opportunity_score: float | None
    confidence_pct: float | None
    risk_pct: float | None
    setup_id: str
    strategy_version: str
    why: list[str]
    invalidation: str
    risks: list[str]
    trading_horizon: str = "swing"

    def as_text(self) -> str:
        icon = _TIER_ICON.get(self.tier or "", "•")
        lines = [
            f"{icon} {self.tier or ''} {self.action} · {self.instrument} · {self.direction}".strip(),
            "",
            f"Entry:        {self.entry:g}",
            f"Stop Loss:    {self.stop_loss:g}",
            f"TP1:          {self.tp1:g}",
            f"TP2:          {self.tp2:g}",
            f"TP3:          {self.tp3}"
            + (f"  (~{self.tp3_indicative:g})" if self.tp3_indicative else ""),
            f"R:R (→TP2):   {self.rr_to_tp2:.2f}" if self.rr_to_tp2 else "R:R (→TP2):   —",
            f"Blended R:R:  {self.blended_rr:.2f}" if self.blended_rr else "",
            f"Opp. Score:   {self.opportunity_score:g}/100"
            if self.opportunity_score is not None
            else "",
            f"Confidence:   {self.confidence_pct:.0f}%" if self.confidence_pct is not None else "",
            f"Risk:         {self.risk_pct:.2f}%" if self.risk_pct is not None else "",
            "",
            f"Setup:        {self.setup_id}  ({self.trading_horizon})",
            "Warum:        " + ("; ".join(self.why) if self.why else "—"),
            f"Invalidation: {self.invalidation}",
            "Risiken:      " + ("; ".join(self.risks) if self.risks else "keine erkannt"),
        ]
        return "\n".join(x for x in lines if x != "")

    def as_dict(self) -> dict[str, object]:
        return {
            "instrument": self.instrument,
            "information_cutoff": self.information_cutoff.isoformat(),
            "action": self.action,
            "direction": self.direction,
            "tier": self.tier,
            "entry": self.entry,
            "stop_loss": self.stop_loss,
            "tp1": self.tp1,
            "tp2": self.tp2,
            "tp3": self.tp3,
            "tp3_indicative": self.tp3_indicative,
            "rr_to_tp2": self.rr_to_tp2,
            "blended_rr": self.blended_rr,
            "opportunity_score": self.opportunity_score,
            "confidence_pct": self.confidence_pct,
            "risk_pct": self.risk_pct,
            "setup_id": self.setup_id,
            "strategy_version": self.strategy_version,
            "why": self.why,
            "invalidation": self.invalidation,
            "risks": self.risks,
            "trading_horizon": self.trading_horizon,
        }


def _why_from_confluence(conf: object, limit: int = 5) -> list[str]:
    factors = getattr(conf, "factors", ()) or ()
    scored = [
        f
        for f in factors
        if getattr(f, "scored", False) and abs(getattr(f, "contribution", 0.0)) > 0.05
    ]
    scored.sort(key=lambda f: abs(getattr(f, "contribution", 0.0)), reverse=True)
    out: list[str] = []
    for f in scored[:limit]:
        reason = getattr(f, "reason", "") or getattr(getattr(f, "factor", None), "value", "")
        out.append(str(reason))
    return out


def _risks_from_contradictions(contra: object) -> list[str]:
    records = getattr(contra, "records", ()) or ()
    out: list[str] = []
    for r in records:
        kind = getattr(getattr(r, "kind", None), "value", "")
        if kind in ("negative_factor", "hard_conflict"):
            out.append(str(getattr(r, "reason", "")))
    return out


def _indicative_tp3(result: object, direction: Direction, tp2: float) -> float | None:
    """Nächste signifikante opposing-Liquidität jenseits TP2 — **nur zur Anzeige**, kein Hard-TP."""
    mtf = getattr(result, "mtf", None)
    per_tf = getattr(mtf, "per_tf", {}) or {}
    sign = 1.0 if direction is Direction.LONG else -1.0
    best: float | None = None
    for ctx in per_tf.values():
        for lv in getattr(ctx, "liquidity", ()) or ():
            price = getattr(lv, "price", None)
            if price is None:
                continue
            if sign * (price - tp2) > 0 and (best is None or sign * (price - best) < 0):
                best = float(price)
    return best


def build_signal_report(
    result: object,
    *,
    opportunity: object = None,
    risk_pct: float | None = None,
    trading_horizon: str = "swing",
) -> SignalReport | None:
    """Nur wenn ``result.decision.decision`` BUY oder SELL ist — sonst ``None``."""
    d = getattr(result, "decision", None)
    if d is None:
        return None
    dt = getattr(d, "decision", None)
    if dt not in (DecisionType.BUY, DecisionType.SELL):
        return None
    if None in (d.entry, d.sl, d.tp1, d.tp2):
        return None

    direction = d.direction or (Direction.LONG if dt is DecisionType.BUY else Direction.SHORT)
    conf = getattr(result, "confluence", None)
    contra = getattr(result, "contradictions", None)
    mtf = getattr(result, "mtf", None)

    why = _why_from_confluence(conf)
    if not why:
        why = [f"chain: {getattr(d, 'chain_progress', '')}"]

    risks = _risks_from_contradictions(contra)
    htf_dir_val = str(getattr(getattr(mtf, "htf_directional", None), "value", ""))
    if htf_dir_val in ("unclear", "conflicting"):
        risks.append(f"HTF-Regime {htf_dir_val}")
    for name in ("news", "macro", "event_risk"):
        risks.append(f"{name}: nicht geprüft (kein Feed)")

    r_dist = abs(d.entry - d.sl)
    inval_dir = "unter" if direction is Direction.LONG else "über"
    invalidation = f"Close {inval_dir} {d.sl:g} (SL, {r_dist:g} = 1R) ⇒ Setup ungültig" + (
        f"; oder Klasse-A: {getattr(d, 'reason_codes', ())}"
        if getattr(d, "reason_codes", ())
        else ""
    )

    opp_score = getattr(opportunity, "score", None)
    conf_pct = (d.confidence * 100.0) if d.confidence is not None else None

    return SignalReport(
        instrument=d.instrument,
        information_cutoff=d.information_cutoff,
        action="BUY" if dt is DecisionType.BUY else "SELL",
        direction="LONG" if direction is Direction.LONG else "SHORT",
        tier=getattr(getattr(d, "tier", None), "value", None),
        entry=float(d.entry),
        stop_loss=float(d.sl),
        tp1=float(d.tp1),
        tp2=float(d.tp2),
        tp3=str(d.tp3_ref or "Runner: Trailing M15, aktiv nach TP2"),
        tp3_indicative=_indicative_tp3(result, direction, float(d.tp2)),
        rr_to_tp2=d.rr_to_tp2,
        blended_rr=d.blended_rr,
        opportunity_score=opp_score,
        confidence_pct=conf_pct,
        risk_pct=risk_pct,
        setup_id=str(d.setup_id),
        strategy_version=str(d.strategy_version),
        why=why,
        invalidation=invalidation,
        risks=risks,
        trading_horizon=trading_horizon,
    )


__all__ = ["SignalReport", "build_signal_report"]
