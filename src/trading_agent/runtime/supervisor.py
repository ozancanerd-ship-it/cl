"""Supervisor — der langlebige Prozess, der die Live-Pipeline besitzt.

Zwei Klassen:

* :class:`Supervisor` — **Phase-2B-Backbone** (Ingestion → Scanner-Shell). Bleibt für die
  Ingestion-/Data-Quality-Integrationstests. Kein Strategie-Pfad.
* :class:`LiveSupervisor` — **M-01**: fährt die echte :class:`~trading_agent.runtime.live_pipeline.LivePipeline`
  **24/7**: Kraken/Bybit public → MarketContext → MTF → Strategy → Decision → Signal → Alert →
  Risk → Paper Position. **Keine Echtgeld-Orders** (`orders_sent` bleibt 0, wird geprüft).

``LiveSupervisor``-Garantien:

* **fail-safe Start** — Kill-Switch engaged bis Warmup + Recovery durch sind.
* **Recovery** — Snapshot laden, Positionen wieder einhängen, Daten-Lücke per REST backfillen
  (soweit der öffentliche REST-Verlauf reicht). Kein Fake.
* **24/7** — läuft bis SIGTERM/SIGINT (Cloud-Scale-Down) oder `request_stop()`.
* **WS-Überwachung** — `LivePipeline` startet den WS-Stream nach Abriss selbst neu; der
  Supervisor überwacht zusätzlich die Task und startet sie neu, falls sie ganz stirbt.
* **Watchdog** — periodischer Health-Check: stale Data, WS-Restarts, Feed-Fehler → `SystemHealth`
  + Alert-Events. Heartbeat für einen externen Watchdog.
* **Fehler-Isolation** — ein Instrument-/Handler-Fehler reißt den Loop nicht ab
  (`EventBus(raise_on_handler_error=False)`, Task-Restart, `LivePipeline._feed` gekapselt).
* **sauberer Shutdown** — Snapshot schreiben, Bus leerlaufen lassen, `ShutdownRequested`, Exit 0.
* **cloud-fähig** — nur ein Snapshot-Pfad (Volume-Mount), SIGTERM-Handling, strukturierte Logs,
  `status()` JSON-serialisierbar. Kein lokaler Zwang.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import logging
import signal
from datetime import UTC, datetime, timedelta

from trading_agent.core.clock import Clock, SystemClock
from trading_agent.core.enums import ProviderHealth
from trading_agent.data.ingestion.service import IngestionService
from trading_agent.ops.health import SystemHealth
from trading_agent.ops.metrics import MetricsRegistry
from trading_agent.runtime.bus import EventBus
from trading_agent.runtime.events import Heartbeat, ShutdownRequested
from trading_agent.runtime.live_pipeline import LivePipeline
from trading_agent.state.recovery import backfillable, clamp_backfill_start, gap_bars
from trading_agent.state.store import SnapshotStore

_log = logging.getLogger("trading_agent.runtime.supervisor")

_SNAPSHOT_NAME = "live_supervisor"


class Supervisor:
    """Phase-2B-Backbone: Ingestion + Scanner-Shell. (Für M-01: siehe ``LiveSupervisor``.)"""

    def __init__(
        self,
        bus: EventBus,
        ingestion: IngestionService,
        *,
        health: SystemHealth | None = None,
        metrics: MetricsRegistry | None = None,
        clock: Clock | None = None,
        heartbeat_interval_s: float = 5.0,
    ) -> None:
        self.bus = bus
        self.ingestion = ingestion
        self.health = health or ingestion.health
        self.metrics = metrics or ingestion.metrics
        self._clock = clock or SystemClock()
        self._hb_interval = heartbeat_interval_s
        self._stop = asyncio.Event()
        self.started = False
        self.stopped = False
        self.orders_sent = 0  # invariant: stays 0 in phase 2B

    async def _startup_checks(self) -> bool:
        if self.ingestion is None or self.ingestion.source is None:  # pragma: no cover - defensive
            return False
        _ = self.ingestion.repo.root
        self.health.heartbeat()
        return True

    async def _heartbeat_loop(self) -> None:
        while not self._stop.is_set():
            self.health.heartbeat()
            self.metrics.incr("heartbeats_total")
            await self.bus.publish(Heartbeat(ts=self._clock.now(), component="supervisor"))
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._hb_interval)
            except TimeoutError:
                continue

    async def run(self, *, max_bars: int | None = None) -> None:
        self.health.kill_switch_engaged = True
        ok = await self._startup_checks()
        if not ok:  # pragma: no cover
            _log.error("startup checks failed; staying stopped")
            return
        self.health.kill_switch_engaged = False
        self.started = True
        _log.info("supervisor started; observing market")

        hb = asyncio.create_task(self._heartbeat_loop())
        try:
            await self.ingestion.run(max_bars=max_bars)
        finally:
            await self.shutdown("ingestion finished")
            hb.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await hb

    async def shutdown(self, reason: str) -> None:
        if self.stopped:
            return
        self._stop.set()
        await self.ingestion.stop()
        await self.bus.publish(ShutdownRequested(ts=self._clock.now(), reason=reason))
        self.stopped = True
        assert self.orders_sent == 0, "PAPER_LIVE must never send an order"
        _log.info("supervisor stopped", extra={"reason": reason})

    def status(self) -> dict[str, object]:
        return {
            "started": self.started,
            "stopped": self.stopped,
            "orders_sent": self.orders_sent,
            "bars_ingested": self.ingestion.bars_ingested,
            "bars_blocked": self.ingestion.bars_blocked,
            "health": self.health.snapshot(),
        }


# ------------------------------------------------------------------------------- M-01


class LiveSupervisor:
    """24/7-Supervisor über einer :class:`LivePipeline`. Keine Echtgeld-Orders."""

    def __init__(
        self,
        pipeline: LivePipeline,
        *,
        snapshot_store: SnapshotStore,
        health: SystemHealth | None = None,
        metrics: MetricsRegistry | None = None,
        heartbeat_interval_s: float = 10.0,
        watchdog_interval_s: float = 20.0,
        snapshot_interval_s: float = 60.0,
        install_signal_handlers: bool = True,
    ) -> None:
        self.pipeline = pipeline
        self.store = snapshot_store
        self.health = health or SystemHealth()
        self.metrics = metrics or MetricsRegistry()
        self._hb_interval = heartbeat_interval_s
        self._wd_interval = watchdog_interval_s
        self._snap_interval = snapshot_interval_s
        self._install_signals = install_signal_handlers

        self._stop = asyncio.Event()
        self.started = False
        self.stopped = False
        self.started_at: datetime | None = None
        self.stop_reason: str | None = None
        self.orders_sent = 0  # invariant
        self._pipeline_task: asyncio.Task[None] | None = None
        self._snapshots_written = 0

    # ------------------------------------------------------------------ lifecycle
    def request_stop(self, reason: str = "signal") -> None:
        self.stop_reason = self.stop_reason or reason
        self._stop.set()

    async def _recover(self) -> dict[str, object]:
        payload = self.store.load(_SNAPSHOT_NAME)
        if payload is None:
            _log.info("kein Snapshot — sauberer Kaltstart")
            return {"recovered": False}
        info = self.pipeline.restore(payload)
        _log.info(
            "Snapshot geladen", extra={"info": str(info), "saved_at": payload.get("_saved_at")}
        )
        return {"recovered": bool(info.get("restored")), "detail": info}

    async def _backfill_gap(self) -> dict[str, dict[str, int]]:
        """Nach Recovery: die Lücke zwischen letztem verarbeiteten Bar und jetzt per REST füllen."""
        out: dict[str, dict[str, int]] = {}
        now = datetime.now(UTC)
        tf = self.pipeline.cfg.base_timeframe
        for s in self.pipeline.cfg.instruments:
            last = self.pipeline._last_open.get(s)
            if last is None:
                continue
            gap = gap_bars(last, now, tf)
            if gap <= 0:
                continue
            if not backfillable(gap):
                _log.warning(
                    "Lücke größer als der REST-Verlauf — Teil-Backfill (Datenverlust unvermeidbar)",
                    extra={"instrument": s, "gap_bars": gap},
                )
            start = clamp_backfill_start(last, now, tf)
            fed = await self.pipeline.backfill(s, start, now)
            out[s] = {"gap_bars": gap, "backfilled": fed}
        if out:
            _log.info("Gap-Backfill", extra={"result": str(out)})
        return out

    async def run(self, *, max_seconds: float | None = None) -> None:
        self.health.kill_switch_engaged = True  # fail-safe bis Startup durch
        self.started_at = datetime.now(UTC)

        recovery = await self._recover()
        recovered = bool(recovery.get("recovered"))
        warm = await self.pipeline.warmup(preserve_last_open=recovered)
        _log.info("warmup", extra={"report": str(warm), "recovered": recovered})
        if recovered:
            await self._backfill_gap()
        await self.pipeline.prime()

        self.health.kill_switch_engaged = False
        self.started = True
        self._register_signal_handlers()
        _log.info(
            "LiveSupervisor läuft (24/7)",
            extra={"exchange": self.pipeline.cfg.exchange, "recovered": recovery.get("recovered")},
        )

        deadline = datetime.now(UTC) + timedelta(seconds=max_seconds) if max_seconds else None
        self._pipeline_task = asyncio.create_task(self.pipeline.run())
        tasks = [
            self._pipeline_task,
            asyncio.create_task(self._heartbeat_loop()),
            asyncio.create_task(self._watchdog_loop()),
            asyncio.create_task(self._snapshot_loop()),
        ]
        try:
            while not self._stop.is_set():
                await asyncio.sleep(0.5)
                if deadline is not None and datetime.now(UTC) >= deadline:
                    self.request_stop("max_seconds")
                if self._pipeline_task.done():
                    exc = self._pipeline_task.exception()
                    if exc is not None:
                        _log.exception("pipeline task crashed — Neustart", exc_info=exc)
                        self.pipeline._stopped = False
                        self._pipeline_task = asyncio.create_task(self.pipeline.run())
                        tasks[0] = self._pipeline_task
                    else:
                        self.request_stop("pipeline finished")
        finally:
            await self.shutdown(self.stop_reason or "run() ended")
            for t in tasks:
                t.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.gather(*tasks, return_exceptions=True)

    async def shutdown(self, reason: str) -> None:
        if self.stopped:
            return
        self.stopped = True
        self.stop_reason = self.stop_reason or reason
        with contextlib.suppress(Exception):
            await self.pipeline.stop()
        self._write_snapshot()  # letzter, konsistenter Stand
        await self.pipeline.bus.publish(ShutdownRequested(ts=datetime.now(UTC), reason=reason))
        self.orders_sent = self.pipeline.orders_sent
        assert self.orders_sent == 0, "LIVE PAPER darf niemals eine Order senden"
        _log.info("LiveSupervisor gestoppt", extra={"reason": reason})

    # ------------------------------------------------------------------ loops
    async def _heartbeat_loop(self) -> None:
        while not self._stop.is_set():
            self.health.heartbeat()
            self.metrics.incr("heartbeats_total")
            with contextlib.suppress(Exception):
                await self.pipeline.bus.publish(
                    Heartbeat(ts=datetime.now(UTC), component="live_supervisor")
                )
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=self._hb_interval)

    async def _watchdog_loop(self) -> None:
        ex = self.pipeline.cfg.exchange
        while not self._stop.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=self._wd_interval)
            if self._stop.is_set():
                break
            rep = self.pipeline.health_report()
            # Provider-Health ableiten (Paper-Modus: nur degradieren, NICHT kill-switchen).
            if rep.any_stale and self.pipeline.ws_restarts > 3:
                self.health.set_provider_health(ex, ProviderHealth.UNAVAILABLE)
            elif rep.any_stale or self.pipeline.ws_restarts > 0 or rep.feed_errors_total > 0:
                self.health.set_provider_health(ex, ProviderHealth.DEGRADED)
            else:
                self.health.set_provider_health(ex, ProviderHealth.HEALTHY)
            for s, ih in rep.instruments.items():
                self.health.set_data_block(s, ih.qblocks > 0 and ih.stale)
            self.metrics.incr("watchdog_ticks_total")

    async def _snapshot_loop(self) -> None:
        while not self._stop.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=self._snap_interval)
            if not self._stop.is_set():
                self._write_snapshot()

    def _write_snapshot(self) -> None:
        with contextlib.suppress(Exception):
            self.store.save(_SNAPSHOT_NAME, self.pipeline.snapshot())
            self._snapshots_written += 1

    # ------------------------------------------------------------------ signals
    def _register_signal_handlers(self) -> None:
        if not self._install_signals:
            return
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            name = sig.name
            with contextlib.suppress(NotImplementedError, RuntimeError):
                loop.add_signal_handler(sig, self._on_signal, name)

    def _on_signal(self, name: str) -> None:
        _log.info("signal empfangen — graceful shutdown", extra={"signal": name})
        self.request_stop(f"signal {name}")

    # ------------------------------------------------------------------ status
    def status(self) -> dict[str, object]:
        return {
            "started": self.started,
            "stopped": self.stopped,
            "stop_reason": self.stop_reason,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "uptime_seconds": round((datetime.now(UTC) - self.started_at).total_seconds(), 1)
            if self.started_at
            else 0.0,
            "orders_sent": self.orders_sent,
            "snapshots_written": self._snapshots_written,
            "health": self.health.snapshot(),
            "pipeline": self.pipeline.summary(),
            "pipeline_health": dataclasses.asdict(self.pipeline.health_report()),
        }


__all__ = ["LiveSupervisor", "Supervisor"]
