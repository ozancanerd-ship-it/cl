"""Provider-/Asset-spezifische Kostenprofile — **klar getrennt: gemessen vs. geschätzt**.

``strategy.costs.CostConfig`` hält die Sätze (Default alle ``0.0``). Dieses Modul liefert je
Asset-Klasse ein **konservatives Schätzprofil** und einen Builder, der die **echten** Gebühren
aus ``refdata.FeeSchedule`` (am ``Instrument``) mit geschätzter Slippage/Spread kombiniert.

**Nichts hier ist eine gemessene reale Kostenhistorie.** Jedes Profil trägt eine ``provenance``:

* ``"zero"``                 — alle Sätze 0, Lauf ist **brutto**
* ``"exchange_schedule"``    — Gebühren aus dem echten Plan, Rest 0
* ``"estimate_conservative"``— plausible, bewusst pessimistische Schätzung für Spread/Slippage
* ``"measured"``             — aus echter Fill-/Funding-Historie (noch nicht verfügbar)

Der Backtest weist immer **brutto (``gross_realized_r``) und netto (``realized_r``)** getrennt
aus — welche ``provenance`` benutzt wurde, gehört ins Run-Manifest.
"""

from __future__ import annotations

import dataclasses

from trading_agent.core.enums import AssetClass
from trading_agent.refdata.models import Instrument
from trading_agent.strategy.costs import CostConfig


@dataclasses.dataclass(frozen=True, slots=True)
class CostProfile:
    config: CostConfig
    provenance: str
    note: str

    @property
    def is_measured(self) -> bool:
        return self.provenance == "measured"


ZERO = CostProfile(CostConfig(), "zero", "alle Sätze 0.0 — Lauf ist BRUTTO")


# Konservative Schätzungen je Asset-Klasse. Quellen: öffentliche Standard-Gebührenpläne +
# grobe Spread-/Slippage-Bänder aus der Literatur/Beobachtung. **Keine Messung.** Slippage als
# fixer bps-Teil + ``atr_mult`` (zusätzlich ``mult · ATR/Preis``). Funding bleibt 0 — es braucht
# echte Perp-Funding-Historie, sonst wäre es erfunden.
_ESTIMATES: dict[AssetClass, CostConfig] = {
    AssetClass.CRYPTO: CostConfig(
        taker_fee_bps=5.5,
        maker_fee_bps=2.0,
        half_spread_bps=1.0,
        slippage_bps=1.5,
        slippage_atr_mult=0.03,
        entry_is_maker=True,
        exit_is_maker=False,
    ),
    AssetClass.ALTCOIN: CostConfig(
        taker_fee_bps=6.0,
        maker_fee_bps=2.0,
        half_spread_bps=2.5,
        slippage_bps=4.0,
        slippage_atr_mult=0.05,
        entry_is_maker=True,
        exit_is_maker=False,
    ),
    AssetClass.GOLD: CostConfig(
        taker_fee_bps=3.0,
        maker_fee_bps=0.0,
        half_spread_bps=1.5,
        slippage_bps=1.0,
        slippage_atr_mult=0.02,
        entry_is_maker=False,
        exit_is_maker=False,
    ),
    AssetClass.FOREX: CostConfig(
        taker_fee_bps=0.8,
        maker_fee_bps=0.0,
        half_spread_bps=0.6,
        slippage_bps=0.5,
        slippage_atr_mult=0.02,
        entry_is_maker=False,
        exit_is_maker=False,
    ),
    AssetClass.EQUITY: CostConfig(
        taker_fee_bps=1.0,
        maker_fee_bps=0.0,
        half_spread_bps=2.0,
        slippage_bps=1.5,
        slippage_atr_mult=0.03,
        entry_is_maker=False,
        exit_is_maker=False,
    ),
    AssetClass.ETF: CostConfig(
        taker_fee_bps=1.0,
        maker_fee_bps=0.0,
        half_spread_bps=1.0,
        slippage_bps=1.0,
        slippage_atr_mult=0.02,
        entry_is_maker=False,
        exit_is_maker=False,
    ),
}


def estimate_profile(asset_class: AssetClass) -> CostProfile:
    """Konservatives Schätzprofil je Asset-Klasse. ``provenance='estimate_conservative'``."""
    cfg = _ESTIMATES.get(asset_class)
    if cfg is None:
        return ZERO
    return CostProfile(
        cfg,
        "estimate_conservative",
        f"konservative Schätzung für {asset_class.value} — NICHT gemessen; Funding=0 "
        "(braucht echte Perp-Historie)",
    )


def profile_for(
    instrument: Instrument,
    *,
    use_estimates: bool = False,
    slippage_bps: float | None = None,
    slippage_atr_mult: float | None = None,
    funding_bps_per_day: float | None = None,
) -> CostProfile:
    """Kostenprofil für ein konkretes Instrument.

    * ``use_estimates=False`` (Default): **nur** die echten Gebühren aus ``instrument.fees``
      (``provenance='exchange_schedule'``), Slippage/Spread/Funding = 0.
    * ``use_estimates=True``: echte Gebühren + das konservative Asset-Klassen-Schätzprofil
      für Spread/Slippage (``provenance='exchange_schedule+estimate'``).
    * Explizit übergebene ``slippage_*`` / ``funding_*`` überschreiben (z. B. gemessene Werte);
      dann wird ``provenance`` um ``+measured`` ergänzt.
    """
    fees = instrument.fees
    base = CostConfig(taker_fee_bps=fees.taker_bps, maker_fee_bps=fees.maker_bps)
    provenance = "exchange_schedule"
    note = f"Gebühren aus refdata.FeeSchedule ({instrument.canonical_symbol})"

    if use_estimates:
        est = _ESTIMATES.get(instrument.asset_class)
        if est is not None:
            base = dataclasses.replace(
                base,
                half_spread_bps=est.half_spread_bps,
                slippage_bps=est.slippage_bps,
                slippage_atr_mult=est.slippage_atr_mult,
                entry_is_maker=est.entry_is_maker,
                exit_is_maker=est.exit_is_maker,
            )
            provenance = "exchange_schedule+estimate"
            note += " + konservative Spread/Slippage-Schätzung (NICHT gemessen)"

    measured = False
    if slippage_bps is not None:
        base = dataclasses.replace(base, slippage_bps=slippage_bps)
        measured = True
    if slippage_atr_mult is not None:
        base = dataclasses.replace(base, slippage_atr_mult=slippage_atr_mult)
        measured = True
    if funding_bps_per_day is not None:
        base = dataclasses.replace(base, funding_bps_per_day=funding_bps_per_day)
        measured = True
    if measured:
        provenance += "+measured"
        note += " + übergebene gemessene Werte"

    return CostProfile(base, provenance, note)


__all__ = ["ZERO", "CostProfile", "estimate_profile", "profile_for"]
