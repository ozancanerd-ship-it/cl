"""Corporate Actions — **Point-in-Time-Backadjustment + Symbol-Historie** (Aktien / ETFs).

Konsument der ``refdata.models.CorporateAction``-Records. Strikt PIT über ``available_time``:
eine Maßnahme wird **nie vor** ihrer Bekanntgabe angewandt (``available_time <= as_of``), auch
wenn ihr ``ex_date`` im Backtest-Fenster liegt.

Funktionen:

* :func:`adjust_ohlcv` — rückwirkend split-/dividenden-angepasste OHLCV-Serie. Preise **vor**
  ``ex_date`` werden mit dem kumulierten Faktor skaliert, Volumen invers. Total-Return-Modus
  (``adjust_dividends=True``) zieht Bardividenden preisproportional ab.
* :class:`CorporateActionBook` — indexierte Sammlung je Symbol; PIT-Abfragen, Symbol-Ketten
  (``SYMBOL_CHANGE``), Delisting-Kenntnis.
* :func:`resolve_symbol_at` — kanonisches Symbol zu einem Zeitpunkt (folgt Umbenennungen).

**Kein Netz, kein Fake.** Ohne ``CorporateAction``-Daten ist die Serie unverändert (roh) — die
Herkunft (``adjusted`` vs. ``raw``) gehört ins Run-Manifest.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable, Sequence
from datetime import datetime

from trading_agent.core.enums import CorporateActionType
from trading_agent.core.models import OHLCV
from trading_agent.core.time import ensure_utc
from trading_agent.refdata.models import CorporateAction

_SPLIT_TYPES = (CorporateActionType.SPLIT, CorporateActionType.REVERSE_SPLIT)


@dataclasses.dataclass(frozen=True, slots=True)
class AdjustmentResult:
    bars: tuple[OHLCV, ...]
    applied: tuple[CorporateAction, ...]  # nach ex_date sortiert
    provenance: str  # "raw" | "split_adjusted" | "total_return"

    @property
    def factor_note(self) -> str:
        return (
            ", ".join(f"{a.action_type.value}@{a.ex_date.date().isoformat()}" for a in self.applied)
            or "keine"
        )


def _scale_bar(b: OHLCV, price_factor: float) -> OHLCV:
    if price_factor == 1.0:
        return b
    return b.model_copy(
        update={
            "open": b.open * price_factor,
            "high": b.high * price_factor,
            "low": b.low * price_factor,
            "close": b.close * price_factor,
            "volume": b.volume / price_factor if price_factor > 0 else b.volume,
        }
    )


def adjust_ohlcv(
    bars: Sequence[OHLCV],
    actions: Iterable[CorporateAction],
    *,
    as_of: datetime,
    adjust_dividends: bool = False,
) -> AdjustmentResult:
    """Rückwirkend angepasste Serie. Nur Actions mit ``available_time <= as_of`` (PIT).

    Der Faktor jeder Maßnahme wirkt auf **alle Bars mit ``open_time < ex_date``**. Mehrere
    Maßnahmen multiplizieren sich (von neu nach alt kumuliert) — Standard-Backadjustment.
    """
    as_of = ensure_utc(as_of)
    src = list(bars)
    known = sorted(
        (
            a
            for a in actions
            if ensure_utc(a.available_time) <= as_of
            and a.action_type in (*_SPLIT_TYPES, CorporateActionType.DIVIDEND)
        ),
        key=lambda a: ensure_utc(a.ex_date),
    )
    if not src or not known:
        return AdjustmentResult(tuple(src), (), "raw")

    applied: list[CorporateAction] = []
    tr = False
    # rückwärts anwenden: jüngste Maßnahme zuerst, damit ältere Bars den kumulierten Faktor tragen
    for act in reversed(known):
        ex = ensure_utc(act.ex_date)
        if act.action_type in _SPLIT_TYPES:
            assert act.ratio is not None
            factor = 1.0 / act.ratio
        else:  # DIVIDEND
            if not adjust_dividends or not act.cash_amount:
                continue
            prev = [b for b in src if b.close_time <= ex]
            ref = prev[-1].close if prev else None
            if not ref or ref <= 0:
                continue
            factor = 1.0 - act.cash_amount / ref
            tr = True
        if factor <= 0:
            continue
        src = [_scale_bar(b, factor) if ensure_utc(b.open_time) < ex else b for b in src]
        applied.append(act)

    applied.sort(key=lambda a: ensure_utc(a.ex_date))
    prov = "total_return" if tr else ("split_adjusted" if applied else "raw")
    return AdjustmentResult(tuple(src), tuple(applied), prov)


def resolve_symbol_at(symbol: str, actions: Iterable[CorporateAction], at: datetime) -> str:
    """Folgt ``SYMBOL_CHANGE``-Maßnahmen (PIT): welches Symbol war zu ``at`` gültig?

    Beispiel: FB→META am 2022-06-09. ``resolve_symbol_at("FB", ..., 2023-01-01)`` ⇒ ``"META"``.
    """
    at = ensure_utc(at)
    cur = symbol.upper()
    changes = sorted(
        (
            a
            for a in actions
            if a.action_type is CorporateActionType.SYMBOL_CHANGE
            and ensure_utc(a.available_time) <= at
        ),
        key=lambda a: ensure_utc(a.ex_date),
    )
    for ch in changes:
        if ch.symbol.upper() == cur and ensure_utc(ch.ex_date) <= at and ch.new_symbol:
            cur = ch.new_symbol.upper()
    return cur


class CorporateActionBook:
    """Symbol → chronologische ``CorporateAction``-Liste, mit PIT-Abfragen."""

    def __init__(self, actions: Iterable[CorporateAction] = ()) -> None:
        self._by_symbol: dict[str, list[CorporateAction]] = {}
        for a in actions:
            self._by_symbol.setdefault(a.symbol.upper(), []).append(a)
        for lst in self._by_symbol.values():
            lst.sort(key=lambda x: (ensure_utc(x.ex_date), ensure_utc(x.available_time)))

    def symbols(self) -> tuple[str, ...]:
        return tuple(sorted(self._by_symbol))

    def for_symbol(self, symbol: str, *, as_of: datetime | None = None) -> list[CorporateAction]:
        lst = self._by_symbol.get(symbol.upper(), [])
        if as_of is None:
            return list(lst)
        cut = ensure_utc(as_of)
        return [a for a in lst if ensure_utc(a.available_time) <= cut]

    def adjust(
        self,
        symbol: str,
        bars: Sequence[OHLCV],
        *,
        as_of: datetime,
        adjust_dividends: bool = False,
    ) -> AdjustmentResult:
        return adjust_ohlcv(
            bars, self.for_symbol(symbol), as_of=as_of, adjust_dividends=adjust_dividends
        )

    def canonical_symbol(self, symbol: str, at: datetime) -> str:
        return resolve_symbol_at(symbol, self._by_symbol.get(symbol.upper(), []), at)

    def is_delisted(self, symbol: str, at: datetime) -> bool:
        at = ensure_utc(at)
        return any(
            a.action_type is CorporateActionType.DELISTING
            and ensure_utc(a.ex_date) <= at
            and ensure_utc(a.available_time) <= at
            for a in self._by_symbol.get(symbol.upper(), [])
        )


__all__ = [
    "AdjustmentResult",
    "CorporateActionBook",
    "adjust_ohlcv",
    "resolve_symbol_at",
]
