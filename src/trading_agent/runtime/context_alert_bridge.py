"""``ContextAlertBridge`` — verbindet den EventBus mit dem :class:`ContextAlertEmitter`
(Masterplan „Alert-Emitter vollständig verdrahten", §38/§39/§51).

Ein EventBus-Subscriber nach demselben Muster wie ``SignalJournal`` / ``MarketScanner``:

* **``DecisionMade``** → (a) News-Lage je Instrument aus einem lokalen Wirtschaftskalender
  (``assess_news``) → ``HIGH_IMPACT_NEWS``-Alerts; (b) falls eine Re-Entry-Watch aktiv ist,
  wird sie gegen die frische ``EvaluationResult`` neu bewertet → ``RE_ENTRY_SETUP``-Alerts.
* **``PaperPositionChanged`` (CLOSED)** → :meth:`ReEntryEngine.register_exit`. War die These
  intakt (Trail-Stop im Gewinn, Teil-TP, Shakeout) entsteht eine Watch; war sie gebrochen
  (``structure_invalidation``) wird ein evtl. offener Re-Entry-Alert geschlossen.

Gelieferte Alerts gehen als ``AlertRaised`` zurück auf den Bus (Audit-Log, Zähler, Journal
greifen wie bei Signal-Alerts). Dedup/Cooldown/Fingerprint-Anti-Spam kommt aus dem
``ContextAlertEmitter`` + der geteilten ``AlertEngine``.

**Kein Auto-Kauf, keine Order.** Point-in-time: ``now`` == ``event.ts``.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any

from trading_agent.core.enums import AssetClass
from trading_agent.core.models import NewsEvent
from trading_agent.runtime.bus import EventBus
from trading_agent.runtime.events import AlertRaised, DecisionMade, PaperPositionChanged
from trading_agent.strategy.alerts import AlertEngine
from trading_agent.strategy.context_alerts import ContextAlertEmitter, ContextAlertParams


class ContextAlertBridge:
    def __init__(
        self,
        instruments: Sequence[str],
        asset_class: AssetClass,
        *,
        calendar_events: Sequence[NewsEvent] = (),
        params: ContextAlertParams | None = None,
        audit: Callable[[str, str, dict[str, Any]], Any] | None = None,
    ) -> None:
        self._asset_class = asset_class
        self._calendar = list(calendar_events)
        self._audit = audit
        self._engine = AlertEngine()
        self._emitters = {s: ContextAlertEmitter(self._engine, params=params) for s in instruments}
        from trading_agent.portfolio_intel.reentry import ReEntryEngine

        self._reentry = ReEntryEngine()
        self.counts: dict[str, int] = {
            "news_alerts": 0,
            "reentry_alerts": 0,
            "watches_registered": 0,
            "watches_dropped": 0,
            "reentry_assessed": 0,
        }

    # ---- Zugriff ------------------------------------------------------------
    @property
    def active_watches(self) -> int:
        return len(self._reentry.watches)

    @property
    def reentry_engine(self) -> Any:
        return self._reentry

    # ---- Verdrahtung -----------------------------------------------------------
    def attach(self, bus: EventBus) -> None:
        self._bus = bus
        bus.subscribe(DecisionMade, self._on_decision)
        bus.subscribe(PaperPositionChanged, self._on_position)

    async def _publish(self, instrument: str, ts: datetime, events: Sequence[Any]) -> None:
        for ae in events:
            if not ae.delivered:
                continue
            if ae.alert.type.value == "high_impact_news":
                self.counts["news_alerts"] += 1
            elif ae.alert.type.value == "re_entry_setup":
                self.counts["reentry_alerts"] += 1
            if self._audit is not None:
                self._audit(
                    "alert",
                    "context_raised",
                    {"instrument": instrument, "type": ae.alert.type.value},
                )
            await self._bus.publish(
                AlertRaised(
                    ts=ts,
                    instrument=instrument,
                    alert_type=ae.alert.type.value,
                    message=ae.alert.title,
                    delivered=True,
                    alert=ae,
                )
            )

    async def _on_decision(self, ev: DecisionMade) -> None:
        emitter = self._emitters.get(ev.instrument)
        if emitter is None:
            return
        if self._calendar:
            from trading_agent.analysis.news import assess_news

            assessment = assess_news(
                self._calendar,
                cutoff=ev.ts,
                asset_class=self._asset_class,
                instrument=ev.instrument,
            )
            await self._publish(ev.instrument, ev.ts, emitter.on_news(assessment, ev.ts))

        mtf = getattr(ev.result, "mtf", None)
        m5c = getattr(mtf, "m5", None)
        price = float(getattr(m5c, "last_close", 0.0) or 0.0)
        a = self._reentry.assess(ev.instrument, evaluation=ev.result, price=price)
        if a is not None:
            self.counts["reentry_assessed"] += 1
            await self._publish(ev.instrument, ev.ts, emitter.on_reentry([a], ev.ts))

    async def _on_position(self, ev: PaperPositionChanged) -> None:
        from trading_agent.strategy.position import PaperPosition

        if ev.change.upper() != "CLOSED" or not isinstance(ev.position, PaperPosition):
            return
        pos = ev.position
        reason = getattr(pos.close_reason, "value", "") or "unknown"
        w = self._reentry.register_exit(
            instrument=ev.instrument,
            direction=pos.direction,
            exited_at=ev.ts,
            exit_price=float(pos.last_price or pos.entry),
            exit_reason=reason,
            level_to_reclaim=float(pos.entry),
            note=f"closed {reason} @ {pos.realized_r:+.2f}R",
        )
        if w is None:
            self.counts["watches_dropped"] += 1
            emitter = self._emitters.get(ev.instrument)
            if emitter is not None:
                await self._publish(ev.instrument, ev.ts, emitter.on_reentry([], ev.ts))
        else:
            self.counts["watches_registered"] += 1

    # ---- Kalender laden (Hilfsfunktion für den Daemon) --------------------------
    @staticmethod
    def load_calendar(path: str) -> list[NewsEvent]:
        from trading_agent.data.providers.news_calendar import CsvEconomicCalendar

        cal = CsvEconomicCalendar(path)
        return list(
            cal.get_calendar(datetime(2023, 1, 1, tzinfo=UTC), datetime(2030, 1, 1, tzinfo=UTC))
        )


__all__ = ["ContextAlertBridge"]
