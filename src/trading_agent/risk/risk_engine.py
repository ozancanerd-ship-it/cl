"""Risk Engine — die **harte, letzte** Instanz vor einer Paper-/Live-Order.

```
strategy.evaluate() → Decision(BUY/SELL/WAIT/NO_TRADE)
                          │
                          ▼
              RiskEngine.review(decision, account, portfolio, kill_switch)
                          │
                          ▼
     RiskVerdict:  APPROVED(+ PositionSize)  |  REJECTED(reasons)  |  PASS_THROUGH
```

**Unumstößliche Regeln (Projekt-Constraint):**

* Die Risk Engine kann eine ``BUY``/``SELL``-Decision **nur ablehnen oder verkleinern** — sie
  macht **niemals** aus ``WAIT``/``NO_TRADE`` einen Trade. ``review`` einer nicht-aktionierbaren
  Decision ist immer ``PASS_THROUGH``.
* **Score und Confidence werden hier nicht konsultiert** (außer zur Wahl des Basis-Risikobands).
  Ein hoher Score darf ein Limit **nicht** überstimmen.
* Kein Martingale, keine Verlust-Progression, kein Averaging-in, kein Revenge-Trading — die
  Größe folgt allein aus Equity · Risiko · SL-Distanz (``risk.position_sizing``).
"""

from __future__ import annotations

import dataclasses
from enum import StrEnum

from trading_agent.core.enums import DecisionType
from trading_agent.core.types import PortfolioContext
from trading_agent.risk.limits import RiskLimits
from trading_agent.risk.position_sizing import PositionSize, SizingInputs, size_position
from trading_agent.safety.kill_switch import KillSwitchState
from trading_agent.strategy.decision import Decision


@dataclasses.dataclass(frozen=True, slots=True)
class AccountState:
    """Konto-Zustand aus dem Portfolio-Ledger (Phase 4). Alle Felder optional — was fehlt,
    wird **nicht** geprüft (kein Fake), blockiert aber auch nicht."""

    equity: float | None = None
    daily_loss_pct: float | None = None  # >0 = Verlust
    weekly_loss_pct: float | None = None
    drawdown_pct: float | None = None
    trades_today: int | None = None
    consecutive_losses: int = 0
    available_margin: float | None = None


class RiskOutcome(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"
    PASS_THROUGH = "pass_through"  # nichts zu tun (WAIT/NO_TRADE)


@dataclasses.dataclass(frozen=True, slots=True)
class RiskVerdict:
    outcome: RiskOutcome
    decision: Decision  # unverändert — die Risk Engine schreibt die Decision nicht um
    size: PositionSize | None
    reasons: tuple[str, ...]
    not_checked: tuple[str, ...]

    @property
    def approved(self) -> bool:
        return self.outcome is RiskOutcome.APPROVED

    @property
    def blocks(self) -> bool:
        return self.outcome is RiskOutcome.REJECTED


class RiskEngine:
    def __init__(self, limits: RiskLimits | None = None) -> None:
        self.limits = limits or RiskLimits()

    def review(
        self,
        decision: Decision,
        *,
        account: AccountState | None = None,
        portfolio: PortfolioContext | None = None,
        kill_switch: KillSwitchState | None = None,
        atr: float | None = None,
        size_multiplier: float = 1.0,
    ) -> RiskVerdict:
        acc = account or AccountState()
        pf = portfolio or PortfolioContext()
        ks = kill_switch or KillSwitchState()
        lim = self.limits
        nc: list[str] = []

        # 0) Nicht-aktionierbare Decision → die Risk Engine tut nichts (und kann nichts „hochstufen").
        if decision.decision not in (DecisionType.BUY, DecisionType.SELL):
            return RiskVerdict(RiskOutcome.PASS_THROUGH, decision, None, (), ())

        reject: list[str] = []

        # 1) Kill-Switch (hierarchisch) — härteste Schranke.
        for level, tripped in ks.tripped_levels().items():
            if tripped:
                reject.append(f"kill_switch:{level}")

        # 2) Konto-Verlustlimits.
        if acc.daily_loss_pct is not None:
            if acc.daily_loss_pct >= lim.max_daily_loss_pct:
                reject.append("daily_loss_limit")
        else:
            nc.append("daily_loss")
        if acc.weekly_loss_pct is not None:
            if acc.weekly_loss_pct >= lim.max_weekly_loss_pct:
                reject.append("weekly_loss_limit")
        else:
            nc.append("weekly_loss")
        if acc.drawdown_pct is not None:
            if acc.drawdown_pct >= lim.max_drawdown_pct:
                reject.append("max_drawdown")
        else:
            nc.append("drawdown")
        if acc.trades_today is not None and acc.trades_today >= lim.max_trades_today:
            reject.append("max_trades_today")
        if acc.consecutive_losses >= lim.loss_streak_halt:
            reject.append("loss_streak_halt")

        # 3) Portfolio-Struktur.
        inst = decision.instrument.upper()
        open_same = [p for p in pf.open_positions if p.instrument.upper() == inst]
        if len(pf.open_positions) >= lim.max_open_positions:
            reject.append("max_open_positions")
        opp = pf.open_direction(inst)
        want = decision.direction
        if opp is not None and want is not None and opp is not want:
            reject.append("opposite_position_open")
        if open_same and opp is want:
            reject.append("duplicate_position")

        if reject:
            return RiskVerdict(RiskOutcome.REJECTED, decision, None, tuple(reject), tuple(nc))

        # 4) Größe — mit Portfolio-Heat-Headroom.
        equity = acc.equity
        if equity is None or equity <= 0.0:
            # Ohne Equity kann nicht sauber sizes werden → kein Auto-Entry (fail-safe, kein Fake).
            return RiskVerdict(
                RiskOutcome.REJECTED, decision, None, ("no_account_equity",), tuple(nc)
            )

        headroom = _heat_headroom_pct(decision, pf, lim)
        assert decision.entry is not None and decision.sl is not None and decision.tier is not None
        size = size_position(
            SizingInputs(
                equity=equity,
                entry=decision.entry,
                stop_loss=decision.sl,
                tier=decision.tier,
                atr=atr,
                size_multiplier=size_multiplier,
                available_margin=acc.available_margin,
                portfolio_risk_headroom_pct=headroom,
            ),
            lim,
        )
        if not size.tradable:
            return RiskVerdict(
                RiskOutcome.REJECTED, decision, size, tuple(size.capped_by), tuple(nc)
            )

        reasons = tuple(f"sized:{c}" for c in size.capped_by)
        return RiskVerdict(RiskOutcome.APPROVED, decision, size, reasons, tuple(nc))


def _heat_headroom_pct(decision: Decision, pf: PortfolioContext, lim: RiskLimits) -> float | None:
    """Verbleibendes 1R-Budget (% Equity) — das strengste der drei Portfolio-Deckel."""
    have_total = pf.total_open_risk_pct > 0.0 or bool(pf.open_positions)
    if not have_total:
        return None
    total_room = max(0.0, lim.max_total_open_risk_pct - pf.total_open_risk_pct)
    cluster_room = max(0.0, lim.max_cluster_open_risk_pct - pf.cluster_open_risk_pct)
    # korrelierte Exposure: Summe 1R über Instrumente mit correlation ≥ threshold
    corr_risk = 0.0
    for p in pf.open_positions:
        if pf.correlation(p.instrument, decision.instrument) >= lim.correlation_threshold:
            corr_risk += p.open_risk_pct
    corr_room = max(0.0, lim.max_correlated_open_risk_pct - corr_risk)
    return min(total_room, cluster_room, corr_room)


__all__ = ["AccountState", "RiskEngine", "RiskOutcome", "RiskVerdict"]
