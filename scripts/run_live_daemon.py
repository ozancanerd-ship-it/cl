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
    ap.add_argument("--derivatives", action="store_true", help="Bybit Funding/OI (nur wenn valide)")
    ap.add_argument("--snapshot-dir", default="data/repository_real/live/state")
    ap.add_argument("--snapshot-interval", type=float, default=60.0)
    ap.add_argument(
        "--decision-ledger",
        default="data/repository_real/live/decision_ledger.sqlite",
        help="SQLite-Pfad für den Decision-Log (Masterplan §64). '' zum Deaktivieren.",
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
    if recorder is not None:
        status["_decision_ledger_rows"] = recorder.rows_written
    status["_wall_runtime_s"] = round((datetime.now(UTC) - t0).total_seconds(), 1)
    assert sup.orders_sent == 0, "LIVE DAEMON hat eine Order gesendet — darf nie passieren"

    out = json.dumps(status, indent=2, default=str)
    if args.status_json:
        with open(args.status_json, "w", encoding="utf-8") as fh:
            fh.write(out)
    print(out)
    print(f"\norders_sent = {sup.orders_sent}  ·  snapshots = {sup._snapshots_written}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
