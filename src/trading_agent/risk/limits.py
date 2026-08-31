"""Risiko-Limits — die harten Schranken der Risk Engine.

**Grundsatz (Projekt-Constraint):** Diese Limits stehen **über** der Strategie. Ein hoher
Score / eine hohe Confidence darf sie **niemals** überstimmen. Score/Confidence bestimmen nur,
*welches* Basis-Risikoband (A+/A/B) gilt — die Limits selbst sind reine Zahlenschranken.

Alle Prozentwerte sind **PROPOSED DEFAULT** (``sizing.md`` §1, `DECISIONS-0.1.1.md`) und
validierungspflichtig (OOS / Walk-Forward / Monte-Carlo, Ruin-Wahrscheinlichkeit < 5 %).
"""

from __future__ import annotations

import dataclasses

from trading_agent.core.enums import RiskTier

# Basis-Risikobudget je Setup-Stufe (% der Equity) — 0.1.1, „kontrolliert aggressiv".
_BASE_RISK_PCT: dict[RiskTier, float] = {
    RiskTier.A_PLUS: 1.00,
    RiskTier.A: 0.65,
    RiskTier.B: 0.40,
    RiskTier.NO_TRADE: 0.0,
}


class LimitBreach(str):
    """Marker-Typ; die konkreten Gründe sind Strings in ``RiskVerdict.reasons``."""


@dataclasses.dataclass(frozen=True, slots=True)
class RiskLimits:
    # --- pro Trade ---
    hard_max_risk_pct: float = 2.0  # absolute Obergrenze pro Trade, egal was die Faktoren sagen
    min_risk_pct: float = 0.05  # darunter lohnt der Trade nicht (Gebühren)
    # --- Konto ---
    max_daily_loss_pct: float = 3.0
    max_weekly_loss_pct: float = 6.0
    max_drawdown_pct: float = 10.0
    max_trades_today: int = 6
    loss_streak_halt: int = 4  # nach N Verlusten in Folge: nur noch Review, kein Auto-Entry
    # --- Portfolio ---
    max_open_positions: int = 3
    max_total_open_risk_pct: float = 3.0  # Summe offener 1R-Risiken (% Equity) — „Portfolio Heat"
    max_correlated_open_risk_pct: float = 1.5  # Summe 1R über hoch-korrelierte Instrumente
    max_cluster_open_risk_pct: float = 2.0  # Summe 1R je Cluster (BTC/ETH, FX-Majors, …)
    correlation_threshold: float = 0.70  # ab hier gilt „korreliert"
    max_instrument_concentration_pct: float = 2.0  # 1R je Einzelinstrument
    # --- Hebel / Margin (real-constraint, kein Strategieparameter) ---
    max_leverage: float = 20.0  # Broker-/Exchange-Obergrenze (nicht vom Score beeinflusst)
    min_liq_distance_atr: float = 3.0  # Mindestabstand Liquidation ↔ Entry in ATR
    margin_buffer_pct: float = 20.0  # freie Margin muss ≥ (1 + buffer) · benötigte Margin sein

    def base_risk_pct(self, tier: RiskTier) -> float:
        return min(_BASE_RISK_PCT.get(tier, 0.0), self.hard_max_risk_pct)


__all__ = ["LimitBreach", "RiskLimits"]
