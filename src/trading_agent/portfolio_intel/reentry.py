"""``ReEntryEngine`` — Wieder-Einstiegs-Überwachung nach einem Exit (Masterplan §38).

Wenn eine Position geschlossen wird, obwohl die übergeordnete These *nicht* gebrochen war
(Trailing-Stop im Gewinn, Teil-Exit, Shakeout), registriert die Engine einen
``ReEntryWatch``. Bei jeder neuen Analyse prüft sie, ob die Bedingungen für einen
Wieder-Einstieg *in dieselbe Richtung* wieder zusammenkommen — und liefert ein
erklärbares Verdikt (RE_ENTRY_WATCH) mit Readiness 0..1 und konkretem Trigger.

**Kein Auto-Kauf.** Nur Signal + Begründung.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from trading_agent.core.enums import DecisionType, Direction
from trading_agent.portfolio_intel.models import PositionVerdict

# Exit-Gründe, nach denen ein Wieder-Einstieg grundsätzlich sinnvoll bleiben kann.
_THESIS_INTACT_REASONS = frozenset(
    {"trail_stop", "partial", "take_profit", "time_stop", "shakeout"}
)


@dataclass(frozen=True, slots=True)
class ReEntryWatch:
    instrument: str
    direction: Direction
    exited_at: datetime
    exit_price: float
    exit_reason: str
    level_to_reclaim: float | None  # Preis, dessen Rückeroberung die These re-bestätigt
    note: str = ""
    thesis_intact: bool = True


@dataclass(frozen=True, slots=True)
class ReEntryAssessment:
    instrument: str
    direction: Direction
    verdict: PositionVerdict  # RE_ENTRY_WATCH oder HOLD ("noch nicht") — nie ein Kauf
    readiness: float  # 0..1
    conditions: tuple[tuple[str, bool], ...]
    trigger: str
    reasons: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return self.readiness >= 0.9

    def as_dict(self) -> dict[str, object]:
        return {
            "instrument": self.instrument,
            "direction": self.direction.value,
            "verdict": self.verdict.value,
            "readiness": round(self.readiness, 3),
            "conditions": [{"name": n, "met": m} for n, m in self.conditions],
            "trigger": self.trigger,
            "reasons": list(self.reasons),
        }


class ReEntryEngine:
    def __init__(self) -> None:
        self._watches: dict[str, ReEntryWatch] = {}

    # -- Lifecycle ----------------------------------------------------------------------

    def register_exit(
        self,
        *,
        instrument: str,
        direction: Direction,
        exited_at: datetime,
        exit_price: float,
        exit_reason: str,
        level_to_reclaim: float | None = None,
        note: str = "",
    ) -> ReEntryWatch | None:
        reason = exit_reason.strip().lower()
        intact = any(k in reason for k in _THESIS_INTACT_REASONS)
        if not intact and "invalidat" not in reason and "opposite" not in reason:
            intact = True  # unklarer Grund → konservativ weiter beobachten
        if not intact:
            self._watches.pop(instrument.upper(), None)
            return None
        w = ReEntryWatch(
            instrument=instrument.upper(),
            direction=direction,
            exited_at=exited_at,
            exit_price=exit_price,
            exit_reason=reason,
            level_to_reclaim=level_to_reclaim,
            note=note,
            thesis_intact=intact,
        )
        self._watches[w.instrument] = w
        return w

    def drop(self, instrument: str) -> None:
        self._watches.pop(instrument.upper(), None)

    @property
    def watches(self) -> tuple[ReEntryWatch, ...]:
        return tuple(self._watches.values())

    # -- Assessment ---------------------------------------------------------------------

    def assess(
        self, instrument: str, *, evaluation: object, price: float
    ) -> ReEntryAssessment | None:
        w = self._watches.get(instrument.upper())
        if w is None:
            return None

        d = getattr(evaluation, "decision", None)
        mtf = getattr(evaluation, "mtf", None)
        htf_val = str(getattr(getattr(mtf, "htf_directional", None), "value", "") or "")
        want_up = w.direction is Direction.LONG

        trend_ok = (htf_val == "trend_up") if want_up else (htf_val == "trend_down")

        setup_state = str(getattr(getattr(d, "setup_state", None), "value", "") or "")
        setup_dir = getattr(d, "direction", None)
        setup_arming = setup_state in ("structure_shifted", "armed", "triggered") and (
            setup_dir is w.direction
        )

        dt = getattr(d, "decision", None)
        fresh_signal = dt in (DecisionType.BUY, DecisionType.SELL) and setup_dir is w.direction

        if w.level_to_reclaim is None:
            reclaimed = True
        elif want_up:
            reclaimed = price >= w.level_to_reclaim
        else:
            reclaimed = price <= w.level_to_reclaim

        no_opposite = not (
            dt in (DecisionType.BUY, DecisionType.SELL)
            and setup_dir is not None
            and setup_dir is not w.direction
        )

        conditions = (
            ("these_noch_intakt", w.thesis_intact),
            ("htf_trend_gleiche_richtung", trend_ok),
            ("level_zurueckerobert", reclaimed),
            ("frisches_setup_gleiche_richtung", setup_arming),
            ("kein_gegen_signal", no_opposite),
        )
        met = sum(1 for _, ok in conditions if ok)
        readiness = met / len(conditions)
        if fresh_signal and trend_ok and reclaimed:
            readiness = 1.0

        reasons: list[str] = []
        if not trend_ok:
            reasons.append(
                f"HTF-Trend noch nicht {('up' if want_up else 'down')} ({htf_val or '?'})"
            )
        if not reclaimed:
            reasons.append(f"Level {w.level_to_reclaim:g} noch nicht zurückerobert")
        if not no_opposite:
            reasons.append("aktuell Gegen-Signal aktiv — Watch pausiert")
        if readiness >= 0.9:
            reasons.append(
                "Wieder-Einstiegs-Bedingungen erfüllt — neues Signal abwarten/bestätigen"
            )

        trigger = (
            f"reclaim {w.level_to_reclaim:g} + ARMED {w.direction.value}"
            if w.level_to_reclaim is not None
            else f"ARMED {w.direction.value} im HTF-Trend"
        )
        verdict = PositionVerdict.RE_ENTRY_WATCH if readiness >= 0.6 else PositionVerdict.HOLD
        return ReEntryAssessment(
            instrument=w.instrument,
            direction=w.direction,
            verdict=verdict,
            readiness=round(readiness, 3),
            conditions=conditions,
            trigger=trigger,
            reasons=tuple(reasons),
        )


__all__ = ["ReEntryAssessment", "ReEntryEngine", "ReEntryWatch"]
