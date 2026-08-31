"""Aktien-/ETF-Daten-Adapter — **Vertrag + Corporate-Action-Anpassung**, keine feste Quelle.

Bewusst **nicht** an einen Feed hartverdrahtet. Ein konkreter Provider (Polygon, Tiingo,
EODHD, IBKR, …) implementiert ``fetch_ohlcv`` + liefert Corporate Actions; dieser Layer:

* rechnet **Splits / Dividenden** in eine rückwirkend angepasste Serie um (``adjust_for_actions``);
* kennt **Market Hours** + **Pre-/Post-Market** über ``refdata.SessionSpec`` / Trading-Kalender;
* trennt **RTH** (regular trading hours) von erweiterten Sitzungen.

Point-in-Time: eine Anpassung darf nur Actions mit ``ex_date <= as_of`` verwenden (sonst
Look-ahead). Real-time vs. historisch ist eine Provider-Eigenschaft (``AdapterInfo.modes``).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from datetime import datetime

from trading_agent.core.enums import DataKind
from trading_agent.core.models import OHLCV
from trading_agent.core.time import ensure_utc
from trading_agent.data.providers.adapter_base import AdapterInfo, CredentialSpec, LiveDataAdapter


@dataclasses.dataclass(frozen=True, slots=True)
class CorporateAction:
    symbol: str
    ex_date: datetime
    kind: str  # "split" | "dividend"
    ratio: float = 1.0  # split: neue/alte Aktien (2:1 → 2.0); dividend: 1.0
    cash_amount: float = 0.0  # dividend je Aktie in Notierungswährung


def adjust_for_actions(
    bars: Sequence[OHLCV],
    actions: Sequence[CorporateAction],
    *,
    as_of: datetime,
    adjust_dividends: bool = True,
) -> list[OHLCV]:
    """Rückwirkend angepasste Serie. Nur Actions mit ``ex_date <= as_of`` werden angewandt
    (Look-ahead-Schutz). Preise **vor** dem Ex-Datum werden skaliert, Volumen invers."""
    as_of = ensure_utc(as_of)
    acts = sorted(
        (a for a in actions if ensure_utc(a.ex_date) <= as_of),
        key=lambda a: ensure_utc(a.ex_date),
    )
    if not acts:
        return list(bars)
    out = list(bars)
    for act in acts:
        ex = ensure_utc(act.ex_date)
        if act.kind == "split" and act.ratio > 0:
            factor = 1.0 / act.ratio
        elif act.kind == "dividend" and adjust_dividends and act.cash_amount > 0:
            # Preisfaktor aus dem letzten Close vor ex_date
            prev = [b for b in out if b.close_time <= ex]
            ref = prev[-1].close if prev else None
            factor = (1.0 - act.cash_amount / ref) if ref and ref > 0 else 1.0
        else:
            continue
        out = [_scale(b, factor) if b.open_time < ex else b for b in out]
    return out


def _scale(b: OHLCV, factor: float) -> OHLCV:
    return b.model_copy(
        update={
            "open": b.open * factor,
            "high": b.high * factor,
            "low": b.low * factor,
            "close": b.close * factor,
            "volume": b.volume / factor if factor > 0 else b.volume,
        }
    )


class EquityDataAdapter(LiveDataAdapter):
    """Basis für einen konkreten Aktien-/ETF-Provider."""

    def __init__(self, *, name: str, env_vars: tuple[str, ...] = ()) -> None:
        super().__init__(
            AdapterInfo(
                name=name,
                asset_classes=("equity", "etf"),
                data_kinds=(DataKind.OHLCV,),
                modes=("historical",),
                credentials=CredentialSpec(provider=name, env_vars=env_vars, read_only=True),
                redistribution_allowed=False,
                note="Corporate Actions, Splits, Dividenden, Market Hours, Pre/Post",
            )
        )

    def fetch_ohlcv(
        self, symbol: str, timeframe: str, start: datetime, end: datetime, *, session: str = "rth"
    ) -> list[OHLCV]:  # pragma: no cover - Provider-Impl Phase 9+
        raise NotImplementedError("Aktien-Feed: Phase 9+ (Provider-Wahl offen)")

    def fetch_corporate_actions(
        self, symbol: str, start: datetime, end: datetime
    ) -> list[CorporateAction]:  # pragma: no cover
        raise NotImplementedError("Corporate-Action-Feed: Phase 9+")


__all__ = ["CorporateAction", "EquityDataAdapter", "adjust_for_actions"]
