"""Earnings Engine (Masterplan §20) — Event-Risiko rund um Quartalszahlen.

Zwei Dinge, die eine Swing-Entscheidung direkt betreffen:

* **Blackout**: kein *neuer* Swing-Einstieg in den ``n`` Handelstagen **vor** einem
  bestätigten Earnings-Termin (Gap-Risiko über Nacht). Offene Positionen bekommen eine Warnung.
* **Post-Earnings-Drift**: nach einer starken Überraschung tendiert der Kurs mehrere Tage in
  Überraschungsrichtung — ein *unterstützender* (nicht auslösender) Faktor.

Kein Look-ahead: nur Termine/Reports mit bekanntem ``as_of``. Kein Termin bekannt → ``UNKNOWN``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from trading_agent.core.time import ensure_utc


class EarningsState(StrEnum):
    CLEAR = "clear"  # kein Termin in Reichweite
    BLACKOUT = "blackout"  # Termin innerhalb des Vorlauf-Fensters → kein neuer Einstieg
    JUST_REPORTED = "just_reported"  # kürzlich berichtet → Drift-Fenster
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class EarningsEvent:
    symbol: str
    when: datetime  # geplanter/bestätigter Zeitpunkt
    confirmed: bool = False
    session: str = "amc"  # "bmo" (before market open) | "amc" (after market close)
    eps_estimate: float | None = None
    eps_actual: float | None = None  # nur wenn bereits berichtet
    revenue_estimate: float | None = None
    revenue_actual: float | None = None


@dataclass(frozen=True, slots=True)
class EarningsContext:
    symbol: str
    as_of: datetime
    state: EarningsState
    next_event: EarningsEvent | None
    days_until: float | None
    last_event: EarningsEvent | None
    days_since: float | None
    surprise_pct: float | None  # (actual-estimate)/|estimate| des letzten Reports
    blocks_new_entry: bool
    drift_bias: int  # -1 / 0 / +1 (Post-Earnings-Drift-Richtung)
    evidence: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "as_of": self.as_of.isoformat(),
            "state": self.state.value,
            "days_until": self.days_until,
            "days_since": self.days_since,
            "surprise_pct": self.surprise_pct,
            "blocks_new_entry": self.blocks_new_entry,
            "drift_bias": self.drift_bias,
            "evidence": list(self.evidence),
        }


def _surprise(ev: EarningsEvent) -> float | None:
    if ev.eps_actual is None or ev.eps_estimate is None or ev.eps_estimate == 0:
        return None
    return (ev.eps_actual - ev.eps_estimate) / abs(ev.eps_estimate)


def assess_earnings(
    symbol: str,
    events: Sequence[EarningsEvent],
    *,
    as_of: datetime,
    blackout_days: int = 5,
    drift_days: int = 3,
    drift_surprise_threshold: float = 0.05,
) -> EarningsContext:
    now = ensure_utc(as_of)
    rel = [e for e in events if e.symbol.upper() == symbol.upper()]
    past = sorted((e for e in rel if ensure_utc(e.when) <= now), key=lambda e: e.when)
    future = sorted((e for e in rel if ensure_utc(e.when) > now), key=lambda e: e.when)

    if not past and not future:
        return EarningsContext(
            symbol=symbol,
            as_of=now,
            state=EarningsState.UNKNOWN,
            next_event=None,
            days_until=None,
            last_event=None,
            days_since=None,
            surprise_pct=None,
            blocks_new_entry=False,
            drift_bias=0,
            evidence=("kein Earnings-Kalender",),
        )

    nxt = future[0] if future else None
    last = past[-1] if past else None
    days_until = (ensure_utc(nxt.when) - now).total_seconds() / 86400.0 if nxt else None
    days_since = (now - ensure_utc(last.when)).total_seconds() / 86400.0 if last else None

    ev: list[str] = []
    blocks = False
    state = EarningsState.CLEAR
    if days_until is not None and days_until <= blackout_days:
        state = EarningsState.BLACKOUT
        blocks = True
        conf = "bestätigt" if nxt and nxt.confirmed else "geschätzt"
        ev.append(f"Earnings in {days_until:.1f} Tagen ({conf}) → kein neuer Swing-Einstieg")

    surprise = _surprise(last) if last else None
    drift_bias = 0
    if (
        days_since is not None
        and days_since <= drift_days
        and surprise is not None
        and abs(surprise) >= drift_surprise_threshold
    ):
        if state is EarningsState.CLEAR:
            state = EarningsState.JUST_REPORTED
        drift_bias = 1 if surprise > 0 else -1
        ev.append(
            f"Post-Earnings-Drift: EPS-Überraschung {surprise:+.0%} vor {days_since:.1f} Tagen "
            f"→ Bias {'long' if drift_bias > 0 else 'short'}"
        )

    return EarningsContext(
        symbol=symbol,
        as_of=now,
        state=state,
        next_event=nxt,
        days_until=round(days_until, 2) if days_until is not None else None,
        last_event=last,
        days_since=round(days_since, 2) if days_since is not None else None,
        surprise_pct=round(surprise, 4) if surprise is not None else None,
        blocks_new_entry=blocks,
        drift_bias=drift_bias,
        evidence=tuple(ev),
    )


__all__ = ["EarningsContext", "EarningsEvent", "EarningsState", "assess_earnings"]
