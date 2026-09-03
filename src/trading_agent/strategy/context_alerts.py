"""``ContextAlertEmitter`` — verwandelt Portfolio-Intelligence, News-Lage und Re-Entry-Watches
in deduplizierte, **nur bei echter Änderung** ausgelöste Kontext-Alerts (Masterplan „Alert-
Emitter vollständig verdrahten").

Kein Signal-Lifecycle (das macht :meth:`AlertEngine.on_signal_update`); hier geht es um die
*Umgebung* einer Position bzw. eines Universums:

* **PORTFOLIO_RISK** — Portfolio-Health kippt auf ``YELLOW``/``RED``, oder eine Position bekommt
  ein hartes ``EXIT``/``REDUCE``-Verdikt (``hard_override``).
* **HIGH_IMPACT_NEWS** — harter News-Blackout aktiv, oder ein HIGH-Impact-Termin steht innerhalb
  des Vorlauf-Fensters an.
* **RE_ENTRY_SETUP** — eine Re-Entry-Watch erreicht Readiness ≥ Schwelle.

Anti-Spam auf **zwei** Ebenen:

1. je ``dedup_key`` reicht der Emitter ein Ereignis nur dann an die :class:`AlertEngine` weiter,
   wenn sich sein *Fingerprint* geändert hat (semantische Änderungserkennung);
2. die :class:`AlertEngine` selbst dedupliziert (ein aktiver Alert je Schlüssel) und wendet den
   Cooldown an.

Kippt ein Zustand zurück in den unkritischen Bereich (Health ``GREEN``, Blackout vorbei,
Verdikt wieder ``HOLD``), verwirft der Emitter den zugehörigen aktiven Alert
(:meth:`AlertEngine.dismiss_context_alert`) — genau **ein** Übergang, kein Dauerfeuer.

Deterministisch, point-in-time (``now`` == ``information_cutoff``), kein Wall-Clock.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable, Mapping
from datetime import datetime

from trading_agent.analysis.news import NewsAssessment
from trading_agent.core.time import ensure_utc
from trading_agent.portfolio_intel.models import PositionVerdict
from trading_agent.portfolio_intel.reentry import ReEntryAssessment
from trading_agent.portfolio_intel.report import PortfolioIntelligenceReport
from trading_agent.strategy.alerts import AlertEngine, AlertEvent, AlertType

_Evidence = Mapping[str, str | float | int | bool | None]
_CRITICAL_VERDICTS = frozenset({PositionVerdict.EXIT, PositionVerdict.REDUCE})


@dataclasses.dataclass(frozen=True, slots=True)
class ContextAlertParams:
    news_lead_minutes: float = 60.0  # HIGH-Impact-Termin meldet ab diesem Vorlauf
    news_lead_bucket_minutes: float = 15.0  # Alert-Update-Raster im Anmarsch
    reentry_min_readiness: float = 0.6  # ab hier ist die Watch „setup forming"
    alert_on_yellow_health: bool = True  # False = nur RED löst PORTFOLIO_RISK aus


class ContextAlertEmitter:
    """Zustandsbehaftet. Eine Instanz pro Universum, teilt sich die :class:`AlertEngine`
    mit dem Signal-Pfad, damit Cooldown/Dedup global konsistent bleiben."""

    def __init__(self, engine: AlertEngine, *, params: ContextAlertParams | None = None) -> None:
        self._engine = engine
        self._p = params or ContextAlertParams()
        self._fingerprints: dict[str, str] = {}

    # ---- öffentliche Einstiege ------------------------------------------------
    def on_portfolio_report(
        self, report: PortfolioIntelligenceReport, now: datetime
    ) -> tuple[AlertEvent, ...]:
        now = ensure_utc(now)
        out: list[AlertEvent] = []

        grade = report.health.grade.upper()
        critical = grade == "RED" or (self._p.alert_on_yellow_health and grade == "YELLOW")
        health_key = "portfolio:health"
        if critical:
            fp = f"{grade}|{'|'.join(sorted(report.health.flags))}"
            ev = self._maybe_raise(
                AlertType.PORTFOLIO_RISK,
                health_key,
                fp,
                now,
                title=f"Portfolio-Health {grade} · Score {report.health.score:.0f}",
                body="; ".join(report.health.flags) or f"Health-Grade {grade}",
                evidence={"grade": grade, "score": report.health.score},
            )
            if ev is not None:
                out.append(ev)
        else:
            out.extend(self._clear(health_key, now))

        # je-Position: hartes EXIT/REDUCE
        seen: set[str] = set()
        for r in report.ratings:
            key = f"portfolio:position:{r.instrument}"
            hard = r.hard_override is not None or r.verdict in _CRITICAL_VERDICTS
            if hard:
                seen.add(key)
                fp = f"{r.verdict.value}|{r.hard_override or ''}"
                ev = self._maybe_raise(
                    AlertType.PORTFOLIO_RISK,
                    key,
                    fp,
                    now,
                    title=f"{r.instrument}: Positions-Verdikt {r.verdict.value.upper()}",
                    body=(r.hard_override or r.suggested_action or "; ".join(r.reasons)),
                    evidence={
                        "instrument": r.instrument,
                        "verdict": r.verdict.value,
                        "score": r.score,
                        "hard_override": r.hard_override,
                    },
                )
                if ev is not None:
                    out.append(ev)
        # Positionen, die nicht mehr kritisch sind → Alert schließen
        for key in [k for k in self._fingerprints if k.startswith("portfolio:position:")]:
            if key not in seen:
                out.extend(self._clear(key, now))

        out.extend(self.on_reentry(report.reentry, now))
        return tuple(out)

    def on_news(self, assessment: NewsAssessment, now: datetime) -> tuple[AlertEvent, ...]:
        now = ensure_utc(now)
        out: list[AlertEvent] = []
        ctx = assessment.context

        block_key = "news:blackout"
        if ctx.blocking_event_id is not None:
            fp = f"block:{ctx.blocking_event_id}"
            ev = self._maybe_raise(
                AlertType.HIGH_IMPACT_NEWS,
                block_key,
                fp,
                now,
                title=f"{assessment.instrument or assessment.asset_class.value}: News-Blackout aktiv",
                body="; ".join(assessment.reasons) or f"Blackout {ctx.blocking_event_id}",
                evidence={"blocking_event_id": ctx.blocking_event_id, "risk_off": ctx.risk_off},
            )
            if ev is not None:
                out.append(ev)
        else:
            out.extend(self._clear(block_key, now))

        lead = self._p.news_lead_minutes
        active_upcoming: set[str] = set()
        for up in assessment.upcoming_high:
            if up.minutes_until > lead:
                continue
            ev_id = up.event.event_id
            key = f"news:upcoming:{ev_id}"
            active_upcoming.add(key)
            bucket = int(up.minutes_until // max(1.0, self._p.news_lead_bucket_minutes))
            fp = f"{ev_id}:{bucket}"
            ev = self._maybe_raise(
                AlertType.HIGH_IMPACT_NEWS,
                key,
                fp,
                now,
                title=(
                    f"{assessment.instrument or assessment.asset_class.value}: "
                    f"{up.event.event_type} in {up.minutes_until:.0f} min"
                ),
                body=f"HIGH-Impact-Termin {up.event.event_id} — Entry-Fenster schließt",
                evidence={"event_id": ev_id, "minutes_until": round(up.minutes_until, 1)},
            )
            if ev is not None:
                out.append(ev)
        for key in [k for k in self._fingerprints if k.startswith("news:upcoming:")]:
            if key not in active_upcoming:
                out.extend(self._clear(key, now))
        return tuple(out)

    def on_reentry(
        self, assessments: Iterable[ReEntryAssessment], now: datetime
    ) -> tuple[AlertEvent, ...]:
        now = ensure_utc(now)
        out: list[AlertEvent] = []
        active: set[str] = set()
        for a in assessments:
            if (
                a.verdict is not PositionVerdict.RE_ENTRY_WATCH
                or a.readiness < self._p.reentry_min_readiness
            ):
                continue
            key = f"reentry:{a.instrument}:{a.direction.value}"
            active.add(key)
            bucket = round(a.readiness, 1)
            fp = f"{bucket}:{a.ready}"
            ev = self._maybe_raise(
                AlertType.RE_ENTRY_SETUP,
                key,
                fp,
                now,
                title=(
                    f"{a.instrument}: Re-Entry-Watch {a.direction.value.upper()} "
                    f"· Readiness {a.readiness:.0%}"
                ),
                body=f"Trigger: {a.trigger}" + ("" if not a.reasons else f" — {a.reasons[0]}"),
                evidence={
                    "instrument": a.instrument,
                    "direction": a.direction.value,
                    "readiness": a.readiness,
                    "ready": a.ready,
                },
            )
            if ev is not None:
                out.append(ev)
        for key in [k for k in self._fingerprints if k.startswith("reentry:")]:
            if key not in active:
                out.extend(self._clear(key, now))
        return tuple(out)

    # ---- intern -------------------------------------------------------------
    def _maybe_raise(
        self,
        atype: AlertType,
        key: str,
        fingerprint: str,
        now: datetime,
        *,
        title: str,
        body: str,
        evidence: _Evidence,
    ) -> AlertEvent | None:
        if self._fingerprints.get(key) == fingerprint:
            return None  # nichts Neues — kein Alert
        self._fingerprints[key] = fingerprint
        return self._engine.raise_context_alert(
            atype, key=key, title=title, body=body, now=now, evidence=evidence
        )

    def _clear(self, key: str, now: datetime) -> list[AlertEvent]:
        if key not in self._fingerprints:
            return []
        del self._fingerprints[key]
        ev = self._engine.dismiss_context_alert(key, now)
        return [ev] if ev is not None else []


__all__ = ["ContextAlertEmitter", "ContextAlertParams"]
