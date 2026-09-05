#!/usr/bin/env python
"""LIVE-DAEMON (M-01) — 24/7 ``LiveSupervisor`` über Kraken/Bybit **public** market data.

    python scripts/run_live_daemon.py --exchange bybit --symbols BTCUSDT ETHUSDT \
        --snapshot-dir data/repository_real/live/state

Kraken/Bybit → LiveData → MarketContext → MTF → Strategy → Decision → Dynamic Signal → Alert
→ Risk → Paper Position.  **READ-ONLY. Keine Keys, keine Trading-/Withdraw-Rechte, keine Order.**

Läuft bis SIGTERM/SIGINT (Cloud-Scale-Down / Ctrl-C) oder `--max-seconds`. Schreibt periodisch
einen atomaren Snapshot; ein Neustart lädt ihn, hängt offene Paper-Positionen wieder ein und
backfillt die Daten-Lücke per REST (soweit der öffentliche Verlauf reicht).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
from datetime import UTC, datetime

from trading_agent.core.enums import AssetClass
from trading_agent.ops.health import SystemHealth
from trading_agent.ops.metrics import MetricsRegistry
from trading_agent.runtime.events import (
    AlertRaised,
    DataQualityAlert,
    DecisionMade,
    PaperPositionChanged,
    ShutdownRequested,
)
from trading_agent.runtime.live_pipeline import (
    LivePipeline,
    LivePipelineConfig,
    build_rest_provider,
)
from trading_agent.runtime.supervisor import LiveSupervisor
from trading_agent.state.store import SnapshotStore
from trading_agent.utils.logging import configure_logging, get_logger

_log = get_logger("run_live_daemon")


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--exchange", choices=["kraken", "bybit", "binance", "binance_spot"], default="bybit"
    )
    ap.add_argument("--symbols", nargs="+", default=["BTCUSDT", "ETHUSDT"])
    ap.add_argument("--asset-class", default="crypto")
    ap.add_argument("--news-gate", choices=["on", "off"], default="off")
    ap.add_argument("--risk-pct", type=float, default=1.0)
    ap.add_argument("--derivatives", action="store_true", help="Bybit Funding/OI (nur wenn valide)")
    ap.add_argument("--snapshot-dir", default="data/repository_real/live/state")
    ap.add_argument("--snapshot-interval", type=float, default=60.0)
    ap.add_argument(
        "--decision-ledger",
        default="data/repository_real/live/decision_ledger.sqlite",
        help="SQLite-Pfad für den Decision-Log (Masterplan §64). '' zum Deaktivieren.",
    )
    ap.add_argument(
        "--audit-log",
        default="data/repository_real/live/audit.jsonl",
        help="Hash-Chain-Audit-Log (Masterplan §51). '' zum Deaktivieren.",
    )
    ap.add_argument(
        "--dashboard-json",
        default=None,
        help="Dashboard-State (10 Tabs) am Ende hierhin schreiben (Masterplan §63).",
    )
    ap.add_argument(
        "--validation-config",
        default="config/setup_validation.json",
        help="Setup-Freigabe-Registry (Masterplan-Regel 3). Fehlt sie → alles SHADOW.",
    )
    ap.add_argument(
        "--signal-journal",
        default="data/repository_real/live/signal_journal.jsonl",
        help="JSONL — jedes Signal + jede Revision + jeder Shadow-Trade-Schritt (Masterplan §24/§30). '' zum Deaktivieren.",
    )
    ap.add_argument(
        "--economic-calendar",
        default="config/economic_calendar.csv",
        help="Lokaler CSV-Kalender → HIGH_IMPACT_NEWS-Kontext-Alerts. '' zum Deaktivieren.",
    )
    ap.add_argument(
        "--notify",
        action="store_true",
        help=(
            "Alerts zusaetzlich per Telegram/Datei rausschicken (ops/notify). Ohne diesen "
            "Schalter landen sie nur im Audit-Log — also auf keinem Telefon."
        ),
    )
    ap.add_argument(
        "--notify-min",
        choices=["info", "warning", "critical"],
        default="warning",
        help="ab welcher Stufe geschickt wird. Standard: warning.",
    )
    ap.add_argument("--max-seconds", type=float, default=None, help="Test-Deckel; sonst 24/7")
    ap.add_argument("--status-json", default=None, help="Endstand als JSON hierhin schreiben")
    args = ap.parse_args()

    configure_logging("INFO")
    cfg = LivePipelineConfig(
        exchange=args.exchange,
        instruments=tuple(s.upper() for s in args.symbols),
        asset_class=AssetClass(args.asset_class),
        news_gate=args.news_gate == "on",
        derivatives=args.derivatives,
    )
    rest = build_rest_provider(args.exchange)
    pipe = LivePipeline(cfg, rest_provider=rest)

    recorder = None
    if args.decision_ledger:
        from trading_agent.journal.decision_ledger import DecisionLedgerRecorder
        from trading_agent.journal.ledger import Ledger

        recorder = DecisionLedgerRecorder(Ledger(args.decision_ledger))
        recorder.attach(pipe.bus)

    # 24/7 Opportunity-Ranking über die vorhandene Pipeline (Masterplan §4/§5)
    from trading_agent.scanner.market_scanner import MarketScanner, ScannerConfig

    scanner = MarketScanner(
        ScannerConfig(asset_class=dict.fromkeys(cfg.instruments, args.asset_class))
    )
    top_opps = scanner.attach(pipe.bus)

    # konkretes strukturiertes BUY/SELL-Signal bei tradebarer Entscheidung (Masterplan §24)
    from trading_agent.governance import ValidationRegistry, apply_live_gate
    from trading_agent.strategy.signal_report import build_signal_report

    # Freigabe-Autorität (Masterplan-Regel 3: "die Strategie dafür validiert ist").
    # Ohne config/setup_validation.json: konservativer Default — alles UNVALIDATED → SHADOW.
    validation = ValidationRegistry.from_file(args.validation_config)
    emitted_signals: list[dict] = []
    shadow_signals: list[dict] = []

    # JSONL-Journal: jedes Signal, jede Revision, jeder Shadow-Trade-Schritt — sofort persistiert
    journal = None
    if args.signal_journal:
        from trading_agent.runtime.signal_journal import SignalJournal

        journal = SignalJournal(args.signal_journal, build_report=build_signal_report)
        journal.configure(
            opportunity_for=scanner.score_for,
            risk_pct=args.risk_pct,
            apply_live_gate=apply_live_gate,
            registry=validation,
        )
        journal.attach(pipe.bus)

    # Hash-verkettetes Audit-Log (Masterplan §51) — sicherheitsrelevante Ereignisse
    audit = None
    if args.audit_log:
        from trading_agent.safety.audit_log import AuditLog

        audit = AuditLog(args.audit_log)
        audit.record(
            "daemon", "startup", {"exchange": args.exchange, "symbols": list(cfg.instruments)}
        )

    async def _on_tradeable(ev: DecisionMade) -> None:
        if ev.decision_type not in ("buy", "sell"):
            return
        gated = apply_live_gate(ev.result, registry=validation)
        rep = build_signal_report(
            gated,
            opportunity=scanner.score_for(ev.instrument),
            risk_pct=args.risk_pct,
        )
        if rep is None:
            return
        row = rep.as_dict()
        (emitted_signals if rep.is_live else shadow_signals).append(row)
        _log.info("SIGNAL [%s]\n%s", rep.live_eligibility.upper(), rep.as_text())
        if audit is not None:
            audit.record(
                "strategy",
                "signal_emitted" if rep.is_live else "shadow_signal",
                {
                    "instrument": rep.instrument,
                    "action": rep.action,
                    "tier": rep.tier,
                    "live_eligibility": rep.live_eligibility,
                },
            )

    pipe.bus.subscribe(DecisionMade, _on_tradeable)

    async def _on_alert(ev: AlertRaised) -> None:
        if audit is not None:
            audit.record("alert", "raised", {"instrument": ev.instrument, "type": ev.alert_type})

    pipe.bus.subscribe(AlertRaised, _on_alert)

    # Alerts wirklich rausschicken. Bis hierher endeten sie im Audit-Log; ein Alarm, den
    # niemand sieht, ist kein Alarm (Masterplan Punkt 21).
    bruecke = None
    if args.notify:
        from trading_agent.ops.notify import (
            ConsoleSink,
            FileSink,
            Notifier,
            Severity,
            TelegramSink,
        )
        from trading_agent.scanner.alerting import AlertBruecke

        stufe = {"info": Severity.INFO, "warning": Severity.WARNING, "critical": Severity.CRITICAL}[
            args.notify_min
        ]
        tg = TelegramSink(min_severity=Severity.INFO)
        if not tg.available():
            _log.warning("Telegram nicht konfiguriert — Alerts gehen nur in Datei und Konsole")
        sinks: list = [
            ConsoleSink(min_severity=stufe),
            FileSink("data/repository_real/live/alerts.jsonl"),
        ]
        if tg.available():
            sinks.insert(0, tg)
        # dedup_window: derselbe Alert-Typ je Instrument hoechstens alle 15 Minuten.
        notifier = Notifier(sinks, dedup_window_s=900.0, rate_window_s=300.0, max_per_window=10)
        bruecke = AlertBruecke(notifier, min_severity=stufe).attach(pipe.bus)

    # --- Kontext-Alerts: HIGH_IMPACT_NEWS (Wirtschaftskalender) + RE_ENTRY_SETUP (§38/§51) ---
    # (Portfolio-Risk läuft im One-Shot-Pfad portfolio_hub.py — braucht die Konto-Adapter.)
    from trading_agent.runtime.context_alert_bridge import ContextAlertBridge

    calendar_events: list = []
    if args.economic_calendar:
        try:
            calendar_events = ContextAlertBridge.load_calendar(args.economic_calendar)
            _log.info("economic calendar loaded", extra={"events": len(calendar_events)})
        except Exception:
            _log.warning("economic calendar unavailable — no news context alerts", exc_info=True)

    ctx_bridge = ContextAlertBridge(
        cfg.instruments,
        cfg.asset_class,
        calendar_events=calendar_events,
        audit=(audit.record if audit is not None else None),
    )
    ctx_bridge.attach(pipe.bus)

    counts = {"decision": 0, "alert": 0, "paper": 0, "quality": 0, "shutdown": 0}
    pipe.bus.subscribe(
        DecisionMade, lambda e: counts.__setitem__("decision", counts["decision"] + 1)
    )
    pipe.bus.subscribe(AlertRaised, lambda e: counts.__setitem__("alert", counts["alert"] + 1))
    pipe.bus.subscribe(
        PaperPositionChanged, lambda e: counts.__setitem__("paper", counts["paper"] + 1)
    )
    pipe.bus.subscribe(
        DataQualityAlert, lambda e: counts.__setitem__("quality", counts["quality"] + 1)
    )
    pipe.bus.subscribe(
        ShutdownRequested, lambda e: counts.__setitem__("shutdown", counts["shutdown"] + 1)
    )

    sup = LiveSupervisor(
        pipe,
        snapshot_store=SnapshotStore(args.snapshot_dir),
        health=SystemHealth(),
        metrics=MetricsRegistry(),
        snapshot_interval_s=args.snapshot_interval,
    )

    t0 = datetime.now(UTC)
    await sup.run(max_seconds=args.max_seconds)
    with contextlib.suppress(Exception):
        await rest.aclose()

    status = sup.status()
    status["_event_counts"] = counts
    status["_context_alerts"] = {**ctx_bridge.counts, "active_watches": ctx_bridge.active_watches}
    if recorder is not None:
        status["_decision_ledger_rows"] = recorder.rows_written
    status["_scanner_evaluations"] = scanner.evaluations
    if bruecke is not None:
        status["_notify"] = {
            "alerts_gesehen": bruecke.gesehen,
            "alerts_geschickt": bruecke.geschickt,
            "kanaele": bruecke.notifier.active_sinks,
            "dedup": bruecke.notifier.deduped,
            "rate_limited": bruecke.notifier.rate_limited,
        }
    status["_signals_emitted"] = emitted_signals
    status["_shadow_signals"] = shadow_signals
    status["_validation"] = [sv.as_dict() for sv in validation.all()]
    if journal is not None:
        status["_journal"] = {"path": str(journal.path), "counts": journal.counts}
    status["_top_opportunities"] = [
        {
            "rank": r.rank,
            "instrument": r.instrument,
            "score": r.score,
            "tier": r.tier,
            "setup_state": r.setup_state,
            "direction": r.direction,
            "headline": r.headline,
        }
        for r in top_opps.top(10)
    ]
    status["_wall_runtime_s"] = round((datetime.now(UTC) - t0).total_seconds(), 1)
    assert sup.orders_sent == 0, "LIVE DAEMON hat eine Order gesendet — darf nie passieren"

    if audit is not None:
        audit.record(
            "daemon",
            "shutdown",
            {"signals": len(emitted_signals), "orders_sent": sup.orders_sent},
        )
        status["_audit_log_entries"] = audit.count
        status["_audit_log_ok"] = audit.verify().ok

    if args.dashboard_json:
        from trading_agent.api.dashboard import DashboardInputs, build_dashboard_state

        dash = build_dashboard_state(
            DashboardInputs(
                as_of=datetime.now(UTC),
                top_opportunities=status["_top_opportunities"],
                scanner_evaluations=scanner.evaluations,
                signals=emitted_signals,
                shadow_signals=shadow_signals,
                validation=[sv.as_dict() for sv in validation.all()],
                blockers=[],
            )
        )
        with open(args.dashboard_json, "w", encoding="utf-8") as fh:
            json.dump(dash.as_dict(), fh, indent=2, default=str)

    out = json.dumps(status, indent=2, default=str)
    if args.status_json:
        with open(args.status_json, "w", encoding="utf-8") as fh:
            fh.write(out)
    print(out)
    print(f"\norders_sent = {sup.orders_sent}  ·  snapshots = {sup._snapshots_written}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
