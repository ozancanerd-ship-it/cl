"""Native M1-Zufuhr für die M1-Confirmation (Schritt 8).

Die Confirmation (``strategy.price_action``) darf **nicht** von manuell eingespeisten M1-Bars
abhängen. Dieses Modul liefert eine schmale, brokerunabhängige Abstraktion, über die die Engine
das benötigte **M1-Fenster** zieht — point-in-time (nur ``close_time <= as_of``), look-ahead-frei,
deterministisch, **ohne Fake-Daten**: ist keine echte M1-Historie vorhanden, kommt ein leeres
Fenster zurück (die Confirmation ist dann schlicht „nicht vorhanden“, kein erfundener Wert).

Implementierungen:

* :class:`RepositoryM1Source` — liest aus ``data.MarketDataRepository`` (echte, ingestete M1).
* :class:`InlineM1Source`   — bereits vorliegende M1-Sequenz (Backtest-Replay / Tests), ebenfalls
  PIT-gefiltert.
* :class:`NullM1Source`     — liefert immer ``()`` (explizit „keine M1 verfügbar“).

Das Fenster beginnt am §7-Strukturbruch des Kandidaten (minus einem kleinen Puffer) und endet am
``information_cutoff``. So sieht die Confirmation genau die Bars, die sie laut Spezifikation
auswerten darf — nicht mehr.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Protocol

from trading_agent.core.enums import Timeframe
from trading_agent.core.models import OHLCV
from trading_agent.core.time import ensure_utc
from trading_agent.strategy.setup_detection import SetupCandidate

_M1 = Timeframe.M1


class M1Source(Protocol):
    """Liefert abgeschlossene M1-Bars für ``[start, end)`` mit ``close_time <= as_of``."""

    def window(
        self, instrument: str, start: datetime, end: datetime, *, as_of: datetime
    ) -> tuple[OHLCV, ...]: ...


@dataclasses.dataclass(frozen=True, slots=True)
class M1FeedParams:
    lookback_before_break: timedelta = timedelta(minutes=30)  # Puffer vor dem Strukturbruch
    max_bars: int = 720  # harte Obergrenze (12 h M1) gegen Ausreißer-Fenster


# --------------------------------------------------------------------------------- Quellen


class NullM1Source:
    """Keine M1-Historie verfügbar — immer leeres Fenster (kein Fake)."""

    def window(
        self, instrument: str, start: datetime, end: datetime, *, as_of: datetime
    ) -> tuple[OHLCV, ...]:
        return ()


@dataclasses.dataclass(frozen=True, slots=True)
class InlineM1Source:
    """Vorliegende M1-Sequenz (Backtest-Replay / Tests). PIT-gefiltert."""

    bars: Sequence[OHLCV]

    def window(
        self, instrument: str, start: datetime, end: datetime, *, as_of: datetime
    ) -> tuple[OHLCV, ...]:
        start, end, as_of = ensure_utc(start), ensure_utc(end), ensure_utc(as_of)
        out = [
            b
            for b in self.bars
            if b.instrument == instrument
            and b.timeframe is _M1
            and start <= b.open_time < end
            and b.close_time <= as_of
        ]
        out.sort(key=lambda b: b.open_time)
        return tuple(out)


class RepositoryM1Source:
    """Liest echte, ingestete M1-Bars aus dem ``MarketDataRepository``."""

    def __init__(self, repository: object) -> None:
        # Duck-typed gegen data.MarketDataRepository.read_ohlcv, um einen harten Import-
        # Zyklus strategy → data zu vermeiden.
        self._repo = repository

    def window(
        self, instrument: str, start: datetime, end: datetime, *, as_of: datetime
    ) -> tuple[OHLCV, ...]:
        read = self._repo.read_ohlcv  # type: ignore[attr-defined]
        bars = read(instrument, _M1, start, end, as_of=as_of)
        return tuple(bars)


# --------------------------------------------------------------------------------- Fensterlogik


def confirmation_window(
    source: M1Source,
    candidate: SetupCandidate,
    *,
    information_cutoff: datetime,
    params: M1FeedParams | None = None,
) -> tuple[OHLCV, ...]:
    """M1-Fenster für die Confirmation eines ``ARMED``-Kandidaten. Beginnt am §7-Strukturbruch
    (minus Puffer), endet am ``information_cutoff``. Leeres Ergebnis, wenn kein Strukturbruch
    vorliegt oder die Quelle nichts hat."""
    p = params or M1FeedParams()
    brk = candidate.structure_break
    if brk is None:
        return ()
    cutoff = ensure_utc(information_cutoff)
    start = ensure_utc(brk.break_bar_timestamp) - p.lookback_before_break
    if start >= cutoff:
        return ()
    bars = source.window(candidate.instrument, start, cutoff, as_of=cutoff)
    # Sicherheitsnetz gegen Look-ahead + Fenstergröße
    bars = tuple(b for b in bars if b.close_time <= cutoff)
    if len(bars) > p.max_bars:
        bars = bars[-p.max_bars :]
    return bars


__all__ = [
    "InlineM1Source",
    "M1FeedParams",
    "M1Source",
    "NullM1Source",
    "RepositoryM1Source",
    "confirmation_window",
]
