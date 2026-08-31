"""Positionsgröße aus Risiko% · SL-Distanz · Equity — mit allen harten Deckeln.

``sizing.md`` §1/§2: die Größe folgt aus **Equity · erlaubtem Risiko · SL-Distanz**, nie aus dem
gewünschten Gewinn. Der SL definiert ``1R``; ``1R`` ist die **Verlust-Obergrenze**, punkt. Der
Hebel vergrößert nur die Notional/Margin-Effizienz, niemals den erlaubten Verlust.

Reihenfolge (Spec §1a): ``effective_risk_pct = base_risk_pct[tier] · Π(faktoren)``, dann hart
``min(…, hard_max_risk_pct)``; danach Portfolio-Deckel; danach Größe/Hebel/Margin.
"""

from __future__ import annotations

import dataclasses

from trading_agent.core.enums import RiskTier, Side
from trading_agent.risk.limits import RiskLimits
from trading_agent.risk.margin import estimate_liquidation, max_leverage_for_liq_distance


@dataclasses.dataclass(frozen=True, slots=True)
class SizingInputs:
    equity: float  # Konto-Equity in Account-Währung
    entry: float
    stop_loss: float
    tier: RiskTier
    atr: float | None = None
    size_multiplier: float = 1.0  # Π(faktoren) aus dem dynamischen Multiplikator (Spec §1a)
    available_margin: float | None = None  # None ⇒ Margin-Check übersprungen (kein Broker-State)
    portfolio_risk_headroom_pct: float | None = None  # verbleibendes Heat-Budget (% Equity)
    contract_multiplier: float = 1.0
    min_notional: float = 0.0
    max_leverage_override: float | None = None  # echtes Broker-Limit für dieses Instrument
    side: Side = Side.BUY  # Positionsrichtung (nur für den Liquidationspreis)
    maintenance_margin_rate: float = 0.0  # aus echten Kontrakt-Tiers; 0 ⇒ konservative Näherung


@dataclasses.dataclass(frozen=True, slots=True)
class PositionSize:
    quantity: float  # Kontrakte / Coins
    notional: float  # quantity · entry · contract_multiplier
    risk_amount: float  # tatsächlicher 1R-Verlust in Account-Währung
    risk_pct: float  # risk_amount / equity · 100
    leverage: float  # notional / benötigte Margin (dynamisch)
    r_unit: float  # |entry - stop_loss|
    capped_by: tuple[str, ...]  # welche Deckel griffen
    tradable: bool

    @classmethod
    def not_tradable(cls, reason: str, r_unit: float) -> PositionSize:
        return cls(0.0, 0.0, 0.0, 0.0, 0.0, r_unit, (reason,), tradable=False)


def size_position(inp: SizingInputs, limits: RiskLimits | None = None) -> PositionSize:
    lim = limits or RiskLimits()
    r_unit = abs(inp.entry - inp.stop_loss)
    if r_unit <= 0.0 or inp.equity <= 0.0:
        return PositionSize.not_tradable("invalid_r_unit_or_equity", r_unit)

    capped: list[str] = []

    # 1) effektives Risiko-% — Basisband × Multiplikator, dann harte Schranke
    base = lim.base_risk_pct(inp.tier)
    if base <= 0.0:
        return PositionSize.not_tradable("tier_no_trade", r_unit)
    eff_pct = base * max(0.0, inp.size_multiplier)
    if eff_pct > lim.hard_max_risk_pct:
        eff_pct = lim.hard_max_risk_pct
        capped.append("hard_max_risk_pct")

    # 2) Portfolio-Heat-Headroom (Summe offener 1R darf max_total_open_risk_pct nicht sprengen)
    if inp.portfolio_risk_headroom_pct is not None and eff_pct > inp.portfolio_risk_headroom_pct:
        eff_pct = max(0.0, inp.portfolio_risk_headroom_pct)
        capped.append("portfolio_heat")

    if eff_pct < lim.min_risk_pct:
        return PositionSize.not_tradable("risk_below_min", r_unit)

    # 3) Größe aus Risiko-Budget und SL-Distanz
    risk_amount = inp.equity * eff_pct / 100.0
    per_unit_loss = r_unit * inp.contract_multiplier
    quantity = risk_amount / per_unit_loss
    notional = quantity * inp.entry * inp.contract_multiplier

    if inp.min_notional > 0.0 and notional < inp.min_notional:
        return PositionSize.not_tradable("below_min_notional", r_unit)

    # 4) Hebel dynamisch: zuerst Risiko/Größe, DANN Hebel — begrenzt durch reale Constraints.
    #    Wir wählen den kleinsten Hebel, der die (aus dem Risiko folgende) Notional bei der
    #    verfügbaren Margin trägt, gedeckelt durch das Broker-Limit.
    max_lev = inp.max_leverage_override or lim.max_leverage
    if inp.available_margin is not None and inp.available_margin > 0.0:
        usable_margin = inp.available_margin / (1.0 + lim.margin_buffer_pct / 100.0)
        needed_lev = notional / usable_margin if usable_margin > 0 else max_lev
        if needed_lev > max_lev:
            # Broker-Hebel reicht nicht — Position auf die tragbare Notional herunterskalieren.
            scale = (usable_margin * max_lev) / notional
            quantity *= scale
            notional *= scale
            risk_amount *= scale
            eff_pct *= scale
            capped.append("available_margin")
            leverage = max_lev
        else:
            leverage = max(1.0, needed_lev)
    else:
        leverage = max_lev  # ohne Margin-State: Broker-Limit annehmen (nicht überschreitbar)

    # 5) Liquidationsabstand (nur mit ATR bewertbar) — konservativ: Position untradebar,
    #    wenn der Mindestabstand nicht eingehalten werden kann. Isolated-linear via
    #    ``risk.margin``; ``maintenance_margin_rate=0`` ⇒ identisch zur bisherigen ``entry/lev``-
    #    Näherung, echte Tier-Sätze machen die Prüfung nur konservativer.
    if inp.atr is not None and inp.atr > 0.0:
        min_dist = lim.min_liq_distance_atr * inp.atr
        liq = estimate_liquidation(
            entry=inp.entry,
            side=inp.side,
            leverage=leverage,
            maintenance_margin_rate=inp.maintenance_margin_rate,
            atr=inp.atr,
        )
        if liq.distance_price < min_dist:
            capped.append("liq_distance_tight")
            safe_lev = max_leverage_for_liq_distance(
                entry=inp.entry,
                min_distance=min_dist,
                maintenance_margin_rate=inp.maintenance_margin_rate,
            )
            if safe_lev < 1.0:
                return PositionSize.not_tradable("liq_distance_unreachable", r_unit)
            leverage = min(leverage, safe_lev)

    return PositionSize(
        quantity=round(quantity, 10),
        notional=round(notional, 8),
        risk_amount=round(risk_amount, 8),
        risk_pct=round(risk_amount / inp.equity * 100.0, 6),
        leverage=round(leverage, 4),
        r_unit=r_unit,
        capped_by=tuple(capped),
        tradable=True,
    )


__all__ = ["PositionSize", "SizingInputs", "size_position"]
