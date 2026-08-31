"""``ExitIntelligence`` — konkreter Ausstiegs-Plan für eine offene Position (Masterplan §37).

Übersetzt ein ``PositionRating`` + Positions-Fakten in eine **umsetzbare** Empfehlung:
nichts tun · Stop nachziehen · Teilverkauf X % · komplett schließen — jeweils mit Trigger,
Begründung und (bei Trailing) einem konkreten neuen Stop-Level.

**Kein Auto-Verkauf. Kein Order-Versand.**
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from trading_agent.core.enums import Direction
from trading_agent.portfolio_intel.models import Holding, PositionVerdict
from trading_agent.portfolio_intel.position_intel import PositionRating


class ExitKind(StrEnum):
    NONE = "none"
    TRAIL_STOP = "trail_stop"
    PARTIAL = "partial"
    FULL = "full"


@dataclass(frozen=True, slots=True)
class ExitPlan:
    instrument: str
    account: str
    kind: ExitKind
    size_fraction: float  # Anteil der Position, der reduziert werden soll (0..1)
    trigger: str
    reasons: tuple[str, ...]
    suggested_stop: float | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "instrument": self.instrument,
            "account": self.account,
            "kind": self.kind.value,
            "size_fraction": round(self.size_fraction, 3),
            "trigger": self.trigger,
            "reasons": list(self.reasons),
            "suggested_stop": self.suggested_stop,
        }


class ExitIntelligence:
    def __init__(self, *, breakeven_at_r: float = 1.0, trail_giveback_r: float = 1.0) -> None:
        self.breakeven_at_r = breakeven_at_r
        self.trail_giveback_r = trail_giveback_r

    def plan(self, holding: Holding, rating: PositionRating) -> ExitPlan:
        inst, acc = holding.instrument, holding.account
        ur = holding.unrealized_r

        if rating.verdict is PositionVerdict.EXIT:
            return ExitPlan(
                instrument=inst,
                account=acc,
                kind=ExitKind.FULL,
                size_fraction=1.0,
                trigger="sofort" if rating.hard_override else "nächster Marktschluss (M15/H1)",
                reasons=rating.reasons or ("Rating < EXIT-Schwelle",),
            )

        if rating.verdict is PositionVerdict.REDUCE:
            return ExitPlan(
                instrument=inst,
                account=acc,
                kind=ExitKind.PARTIAL,
                size_fraction=0.5,
                trigger="nächster Marktschluss",
                reasons=rating.reasons or ("Rating im REDUCE-Band",),
                suggested_stop=self._breakeven_stop(holding),
            )

        if rating.verdict in (PositionVerdict.STRONG_HOLD, PositionVerdict.HOLD):
            # Gewinn absichern: sobald > breakeven_at_r im Plus, Stop mindestens auf Einstand
            if ur is not None and ur >= self.breakeven_at_r:
                trail = self._trail_stop(holding, ur)
                if trail is not None and self._is_tighter(holding, trail):
                    return ExitPlan(
                        instrument=inst,
                        account=acc,
                        kind=ExitKind.TRAIL_STOP,
                        size_fraction=0.0,
                        trigger=f"Position +{ur:.1f}R",
                        reasons=(f"Gewinn absichern: Stop auf {trail:g} nachziehen",),
                        suggested_stop=trail,
                    )
            return ExitPlan(
                instrument=inst,
                account=acc,
                kind=ExitKind.NONE,
                size_fraction=0.0,
                trigger="—",
                reasons=("These intakt — halten",),
            )

        # WATCH
        return ExitPlan(
            instrument=inst,
            account=acc,
            kind=ExitKind.NONE,
            size_fraction=0.0,
            trigger="bei weiterer Schwäche → REDUCE",
            reasons=rating.reasons or ("Rating im WATCH-Band — nur beobachten",),
            suggested_stop=self._breakeven_stop(holding),
        )

    # -- Stop-Helfer ---------------------------------------------------------------------

    def _breakeven_stop(self, h: Holding) -> float | None:
        return h.avg_entry_price if h.unrealized_r is not None and h.unrealized_r > 0 else None

    def _trail_stop(self, h: Holding, ur: float) -> float | None:
        if h.stop_ref is None:
            return None
        risk = abs(h.avg_entry_price - h.stop_ref)
        if risk == 0:
            return None
        locked_r = ur - self.trail_giveback_r
        if locked_r <= 0:
            return h.avg_entry_price
        if h.direction is Direction.LONG:
            return h.avg_entry_price + locked_r * risk
        return h.avg_entry_price - locked_r * risk

    @staticmethod
    def _is_tighter(h: Holding, new_stop: float) -> bool:
        if h.stop_ref is None:
            return True
        return new_stop > h.stop_ref if h.direction is Direction.LONG else new_stop < h.stop_ref


__all__ = ["ExitIntelligence", "ExitKind", "ExitPlan"]
