"""(8) Wirtschaftskalender / News-Sperrfenster — **Analyse-Schicht, strikt Point-in-Time**.

Wandelt eine Liste ``NewsEvent`` (aus ``data.providers.news_calendar`` /
``data.providers.fred_alfred`` / ``MarketDataRepository.read_news``) in einen
:class:`~trading_agent.core.types.NewsContext` um, den ``strategy.evaluate`` (No-Trade §NEWS,
Veto V4) direkt konsumiert.

Regeln (``news-rules.md`` / ``docs/CONTINUOUS_IMPROVEMENT.md`` §6j, C10):

* **PIT zwingend** — nur Events mit ``available_time <= cutoff``. Ein geplanter Termin ist Wochen
  vorher im Kalender (``available_time`` << ``scheduled_time``); der Ist-Wert (``actual``) kommt
  als neue Revision mit ``available_time >= scheduled_time``.
* **Asset-spezifisch** — welche Event-Typen zählen, kommt aus
  ``strategy.news_relevance.relevance_for(asset_class)``. Zusätzlich zählt jedes Event, dessen
  ``affected_symbols`` das Instrument nennt.
* **News ist Evidence/Context, kein Trade-Auslöser.** Dieses Modul kann einen Entry **blockieren**
  (Blackout / Pre-Positioning / risk_off) — es erzeugt **nie** ein Long/Short-Signal.
* **Kein Fake.** Fehlt ein Feed, ruft der Aufrufer dieses Modul gar nicht auf und übergibt
  ``NewsContext()`` (``feed_as_of=None`` ⇒ Fail-safe NO_TRADE). ``assess_news`` setzt immer
  ``feed_as_of`` (Feed ist per Definition vorhanden, wenn analysiert wird).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from datetime import datetime, timedelta

from trading_agent.core.enums import AssetClass, NewsImpact
from trading_agent.core.models import NewsEvent
from trading_agent.core.time import ensure_utc
from trading_agent.core.types import NewsContext
from trading_agent.strategy.news_relevance import relevance_for

_EPS = 1e-9
_IMPACT_RANK = {NewsImpact.LOW: 0, NewsImpact.MEDIUM: 1, NewsImpact.HIGH: 2}


@dataclasses.dataclass(frozen=True, slots=True)
class NewsWindowParams:
    """Sperrfenster-Parameter — alle ``PROPOSED DEFAULT`` (``CALIBRATION_BACKLOG.md``)."""

    pre_high_blackout_min: float = 30.0  # harter Blackout VOR einem HIGH-Impact-Termin
    post_high_blackout_min: float = 15.0  # harter Blackout NACH der Veröffentlichung
    watch_horizon_min: float = 240.0  # minutes_to_next_high_impact nur bis hierhin melden
    risk_off_lookback_min: float = 180.0  # wie weit zurück ein risk_off-Event wirkt
    # Event-Typen, die für sich genommen einen risk_off-Zustand setzen (geopolitisch / Notfall).
    risk_off_event_types: frozenset[str] = frozenset(
        {
            "GEOPOLITICS",
            "RISK_OFF",
            "EMERGENCY_RATE",
            "LIQUIDITY_EVENT",
            "BANK_STRESS",
            "SOVEREIGN_STRESS",
        }
    )
    restrict_to_relevant: bool = True  # nur asset-relevante + instrument-spezifische Events


@dataclasses.dataclass(frozen=True, slots=True)
class UpcomingEvent:
    event: NewsEvent
    minutes_until: float  # scheduled_time - cutoff, in Minuten (> 0)


@dataclasses.dataclass(frozen=True, slots=True)
class ReleasedEvent:
    event: NewsEvent
    minutes_since: float  # cutoff - available_time, in Minuten (>= 0)
    surprise_rel: float | None  # |actual - forecast| / max(|forecast|, eps), None wenn unbekannt


@dataclasses.dataclass(frozen=True, slots=True)
class NewsAssessment:
    """Vollständige, erklärbare News-Lage zu einem ``cutoff`` für ein Instrument/Asset."""

    instrument: str | None
    asset_class: AssetClass
    cutoff: datetime
    context: NewsContext  # was ``strategy.evaluate`` sieht
    relevant: tuple[NewsEvent, ...]  # alle PIT-sichtbaren, asset-relevanten Events
    upcoming_high: tuple[UpcomingEvent, ...]  # geplante HIGH-Termine nach cutoff, aufsteigend
    recent_released: tuple[ReleasedEvent, ...]  # kürzlich veröffentlichte Events (im Lookback)
    reasons: tuple[str, ...]  # menschenlesbare Begründung jeder gesetzten Flag

    @property
    def blocks_entry(self) -> bool:
        c = self.context
        return c.blocking_event_id is not None or c.risk_off


# --------------------------------------------------------------------------------- intern


def _base_symbols(instrument: str) -> set[str]:
    """``BTCUSDT`` ⇒ {BTCUSDT, BTCUSD, BTC}. Grobe, deterministische Aufweitung fürs Matching."""
    s = instrument.upper().strip()
    out = {s}
    for qc in ("USDT", "USDC", "USD", "PERP", "EUR"):
        if s.endswith(qc) and len(s) > len(qc):
            out.add(s[: -len(qc)])
    return {x for x in out if x}


def _is_relevant(
    ev: NewsEvent, asset_class: AssetClass, symbols: set[str] | None, restrict: bool
) -> bool:
    if not restrict:
        return True
    if ev.event_type.strip().upper() in relevance_for(asset_class).relevant_event_types:
        return True
    return symbols is not None and bool({s.upper() for s in ev.affected_symbols} & symbols)


def _dedupe_pit(events: Sequence[NewsEvent], cutoff: datetime) -> list[NewsEvent]:
    """Pro ``event_id`` die zum ``cutoff`` neueste bekannte Revision (``available_time <= cutoff``)."""
    best: dict[str, NewsEvent] = {}
    for ev in events:
        if ensure_utc(ev.available_time) > cutoff:
            continue
        cur = best.get(ev.event_id)
        if cur is None or ensure_utc(ev.available_time) >= ensure_utc(cur.available_time):
            best[ev.event_id] = ev
    return sorted(best.values(), key=lambda e: (ensure_utc(e.scheduled_time), e.event_id))


def _surprise_rel(ev: NewsEvent) -> float | None:
    if ev.actual is None or ev.forecast is None:
        return None
    return abs(ev.actual - ev.forecast) / max(abs(ev.forecast), _EPS)


# --------------------------------------------------------------------------------- öffentlich


def assess_news(
    events: Sequence[NewsEvent],
    *,
    cutoff: datetime,
    asset_class: AssetClass,
    instrument: str | None = None,
    feed_as_of: datetime | None = None,
    params: NewsWindowParams | None = None,
) -> NewsAssessment:
    """Erklärbare News-Lage + fertiger ``NewsContext``.

    ``events`` darf roh sein (mehrere Revisionen je ``event_id``, auch Events nach ``cutoff``) —
    dieses Modul filtert PIT und dedupliziert. ``feed_as_of`` default = ``cutoff`` (der Feed ist
    vorhanden, sonst würde man nicht analysieren); explizit ``None`` übergeben nur, um den
    Fail-safe zu erzwingen.
    """
    p = params or NewsWindowParams()
    cutoff = ensure_utc(cutoff)
    feed_ts = cutoff if feed_as_of is None else ensure_utc(feed_as_of)
    symbols = _base_symbols(instrument) if instrument else None

    visible = _dedupe_pit(events, cutoff)
    relevant = tuple(
        ev
        for ev in visible
        if ev.event_type.strip().upper() in p.risk_off_event_types
        or _is_relevant(ev, asset_class, symbols, p.restrict_to_relevant)
    )

    reasons: list[str] = []

    # 1) geplante HIGH-Termine nach dem cutoff
    upcoming: list[UpcomingEvent] = []
    for ev in relevant:
        if ev.impact is not NewsImpact.HIGH:
            continue
        delta_min = (ensure_utc(ev.scheduled_time) - cutoff).total_seconds() / 60.0
        if delta_min > 0:
            upcoming.append(UpcomingEvent(ev, delta_min))
    upcoming.sort(key=lambda u: u.minutes_until)

    minutes_to_next: float | None = None
    if upcoming and upcoming[0].minutes_until <= p.watch_horizon_min:
        minutes_to_next = round(upcoming[0].minutes_until, 1)
        reasons.append(
            f"nächster HIGH-Termin {upcoming[0].event.event_type} in "
            f"{minutes_to_next:.0f} min ({upcoming[0].event.scheduled_time.isoformat()})"
        )

    # 2) kürzlich veröffentlichte Events (Lookback)
    lookback_start = cutoff - timedelta(minutes=p.risk_off_lookback_min)
    recent: list[ReleasedEvent] = []
    for ev in relevant:
        avail = ensure_utc(ev.available_time)
        released = ev.actual is not None or ensure_utc(ev.scheduled_time) <= cutoff
        if released and lookback_start <= avail <= cutoff:
            recent.append(
                ReleasedEvent(ev, (cutoff - avail).total_seconds() / 60.0, _surprise_rel(ev))
            )
    recent.sort(key=lambda r: r.minutes_since)

    # 3) Blackout — blockendes Event: HIGH-Termin im [scheduled - pre, scheduled + post]-Fenster
    blocking_id: str | None = None
    best_gap = float("inf")
    for ev in relevant:
        if ev.impact is not NewsImpact.HIGH:
            continue
        sched = ensure_utc(ev.scheduled_time)
        window_lo = sched - timedelta(minutes=p.pre_high_blackout_min)
        window_hi = sched + timedelta(minutes=p.post_high_blackout_min)
        if window_lo <= cutoff <= window_hi:
            gap = abs((sched - cutoff).total_seconds())
            if gap < best_gap:
                best_gap, blocking_id = gap, ev.event_id
                reasons.append(
                    f"Blackout: {ev.event_type} um {ev.scheduled_time.isoformat()} "
                    f"(±{p.pre_high_blackout_min:.0f}/{p.post_high_blackout_min:.0f} min)"
                )

    # 4) risk_off — nur aus explizit geopolitischen / Notfall-Event-Typen (konservativ)
    risk_off = False
    for r in recent:
        if r.event.event_type.strip().upper() in p.risk_off_event_types:
            risk_off = True
            reasons.append(
                f"risk_off: {r.event.event_type} vor {r.minutes_since:.0f} min ({r.event.event_id})"
            )
            break

    ctx = NewsContext(
        events=tuple(relevant),
        feed_as_of=feed_ts,
        risk_off=risk_off,
        blocking_event_id=blocking_id,
        minutes_to_next_high_impact=minutes_to_next,
    )
    return NewsAssessment(
        instrument=instrument,
        asset_class=asset_class,
        cutoff=cutoff,
        context=ctx,
        relevant=relevant,
        upcoming_high=tuple(upcoming),
        recent_released=tuple(recent),
        reasons=tuple(reasons),
    )


def build_news_context(
    events: Sequence[NewsEvent],
    *,
    cutoff: datetime,
    asset_class: AssetClass,
    instrument: str | None = None,
    feed_as_of: datetime | None = None,
    params: NewsWindowParams | None = None,
) -> NewsContext:
    """Nur der ``NewsContext`` (Kurzform für die Assembler-Verdrahtung)."""
    return assess_news(
        events,
        cutoff=cutoff,
        asset_class=asset_class,
        instrument=instrument,
        feed_as_of=feed_as_of,
        params=params,
    ).context


def no_feed_context() -> NewsContext:
    """Expliziter Fail-safe-Kontext (``feed_as_of=None`` ⇒ NO_TRADE / V4). Kein Fake."""
    return NewsContext()


__all__ = [
    "NewsAssessment",
    "NewsWindowParams",
    "ReleasedEvent",
    "UpcomingEvent",
    "assess_news",
    "build_news_context",
    "no_feed_context",
]
