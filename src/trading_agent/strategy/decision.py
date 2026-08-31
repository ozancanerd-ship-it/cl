"""``Decision`` — die einzige Ausgabe von ``strategy.evaluate(MarketContext) -> Decision``.

``DecisionType ∈ {BUY, SELL, WAIT, NO_TRADE}`` (``SPEC-ADDENDUM-0.1.1.md`` §1).

* **BUY / SELL** — vollständige Kette, ``state == ARMED``, alle Gates + Vetos bestanden,
  ``tier ∈ {A+, A, B}``. Enthält Entry / SL / TP1-3 / RR / Score / Confidence / ``reason_codes``.
* **WAIT** — Kette lebt (``state`` zwischen ``BIAS_SET`` und ``ARMED``), kein hartes Veto, kein
  harter No-Trade-Grund. Kein Entry/SL/TP; ``chain_progress`` erklärt den Stand.
* **NO_TRADE** — hartes Veto **oder** harter No-Trade-Grund **oder** abgebrochene Kette **oder**
  Expiry **oder** vollständige Kette ohne ausreichende Tier-Qualität. ``reason_codes`` nennt
  **alle** zutreffenden Gründe.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from trading_agent.core.enums import (
    DecisionType,
    Direction,
    DisplayAlias,
    NoTradeReason,
    RiskTier,
    SetupState,
    VetoId,
)
from trading_agent.core.version import STRATEGY_VERSION


@dataclass(frozen=True, slots=True)
class Decision:
    decision: DecisionType
    instrument: str
    information_cutoff: datetime
    setup_state: SetupState
    setup_id: str = "SMC-SWEEP-REV-01"
    strategy_version: str = STRATEGY_VERSION

    direction: Direction | None = None
    chain_progress: str = ""
    reason_codes: tuple[NoTradeReason, ...] = ()
    vetoes: tuple[VetoId, ...] = ()

    # --- nur bei BUY / SELL ---
    entry: float | None = None
    sl: float | None = None
    tp1: float | None = None
    tp2: float | None = None
    tp3_ref: str | None = None
    rr_to_tp2: float | None = None
    blended_rr: float | None = None
    score: float | None = None
    confidence: float | None = None
    tier: RiskTier | None = None

    # strukturierte Detail-Snapshots (fürs Decision Ledger); Schema folgt mit den Modulen
    score_detail: Mapping[str, Any] | None = None
    confidence_detail: Mapping[str, Any] | None = None
    context_ref: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.decision in (DecisionType.BUY, DecisionType.SELL):
            if self.direction is None:
                raise ValueError("BUY/SELL braucht eine direction")
            if self.decision is not DecisionType.entry(self.direction):
                raise ValueError(
                    f"decision {self.decision} passt nicht zu direction {self.direction}"
                )
            for name in ("entry", "sl", "tp1", "tp2"):
                if getattr(self, name) is None:
                    raise ValueError(f"BUY/SELL braucht {name}")
            if self.tier in (None, RiskTier.NO_TRADE):
                raise ValueError("BUY/SELL braucht ein Tier A+/A/B")
            if self.setup_state is not SetupState.ARMED:
                raise ValueError("BUY/SELL nur aus setup_state ARMED")
        if self.decision is DecisionType.WAIT:
            if not self.setup_state.is_forming:
                raise ValueError(f"WAIT nur aus einem Forming-State, nicht {self.setup_state}")
            if self.reason_codes or self.vetoes:
                raise ValueError("WAIT hat keine reason_codes / vetoes")
        if self.decision is DecisionType.NO_TRADE and not (self.reason_codes or self.vetoes):
            raise ValueError("NO_TRADE braucht mindestens einen reason_code oder ein Veto")

    # ---- Eigenschaften -----------------------------------------------------------------

    @property
    def is_actionable(self) -> bool:
        return self.decision in (DecisionType.BUY, DecisionType.SELL)

    @property
    def display_alias(self) -> DisplayAlias:
        return DisplayAlias.of(self.setup_state)

    @property
    def r_distance(self) -> float | None:
        if self.entry is None or self.sl is None:
            return None
        return abs(self.entry - self.sl)

    # ---- Fabriken ---------------------------------------------------------------------

    @classmethod
    def no_trade(
        cls,
        instrument: str,
        information_cutoff: datetime,
        reasons: Sequence[NoTradeReason],
        *,
        setup_state: SetupState = SetupState.SCANNING,
        vetoes: Sequence[VetoId] = (),
        direction: Direction | None = None,
        chain_progress: str = "",
        **kw: Any,
    ) -> Decision:
        return cls(
            decision=DecisionType.NO_TRADE,
            instrument=instrument,
            information_cutoff=information_cutoff,
            setup_state=setup_state,
            direction=direction,
            chain_progress=chain_progress,
            reason_codes=tuple(dict.fromkeys(reasons)),
            vetoes=tuple(dict.fromkeys(vetoes)),
            **kw,
        )

    @classmethod
    def wait(
        cls,
        instrument: str,
        information_cutoff: datetime,
        setup_state: SetupState,
        *,
        direction: Direction | None = None,
        chain_progress: str = "",
        **kw: Any,
    ) -> Decision:
        return cls(
            decision=DecisionType.WAIT,
            instrument=instrument,
            information_cutoff=information_cutoff,
            setup_state=setup_state,
            direction=direction,
            chain_progress=chain_progress,
            **kw,
        )

    @classmethod
    def trade(
        cls,
        instrument: str,
        information_cutoff: datetime,
        direction: Direction,
        *,
        entry: float,
        sl: float,
        tp1: float,
        tp2: float,
        tier: RiskTier,
        tp3_ref: str | None = None,
        rr_to_tp2: float | None = None,
        blended_rr: float | None = None,
        score: float | None = None,
        confidence: float | None = None,
        chain_progress: str = "",
        **kw: Any,
    ) -> Decision:
        return cls(
            decision=DecisionType.entry(direction),
            instrument=instrument,
            information_cutoff=information_cutoff,
            setup_state=SetupState.ARMED,
            direction=direction,
            chain_progress=chain_progress,
            entry=entry,
            sl=sl,
            tp1=tp1,
            tp2=tp2,
            tp3_ref=tp3_ref,
            rr_to_tp2=rr_to_tp2,
            blended_rr=blended_rr,
            score=score,
            confidence=confidence,
            tier=tier,
            **kw,
        )


__all__ = ["Decision"]
