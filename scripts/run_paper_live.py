#!/usr/bin/env python
"""PAPER-LIVE daemon — real (or synthetic) live data, 24/7 market observation, NO real orders.

    # synthetic (this environment / no network) — replays repository bars as if live
    python scripts/run_paper_live.py --synthetic BTCUSDT --tf M15 --max-bars 500

    # real crypto WS (needs network; public data, no key)
    python scripts/run_paper_live.py --live kraken BTCUSDT ETHUSDT --tf M1

Pipeline:  Provider -> Ingestion -> Normalization -> Data Quality -> Event Bus -> Scanner(shell)
The Strategy/Risk/Signal stages are placeholders until Phase 3. No BrokerAdapter is live-capable.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime, timedelta

from trading_agent.core.enums import Timeframe, TradingPriority
from trading_agent.data.ingestion.service import IngestionService
from trading_agent.data.ingestion.sources import SyntheticLiveSource
from trading_agent.data.repository import MarketDataRepository
from trading_agent.ops.health import SystemHealth
from trading_agent.ops.metrics import MetricsRegistry
from trading_agent.refdata.seed import build_instrument_master
from trading_agent.runtime.bus import EventBus
from trading_agent.runtime.supervisor import Supervisor
from trading_agent.scanner.scanner import ScannerShell
from trading_agent.utils.logging import configure_logging, get_logger

_log = get_logger("run_paper_live")


async def _build_synthetic_source(repo: MarketDataRepository, symbols: list[str], tf: Timeframe):
    from trading_agent.core.clock import FixedClock
    from trading_agent.data.providers.mock_provider import MockMarketDataProvider

    end = datetime.now(UTC)
    start = end - timedelta(days=20)
    bars = []
    have_any = False
    for sym in symbols:
        cov = repo.ohlcv_coverage(sym.upper(), tf)
        if cov is None:
            mp = MockMarketDataProvider(clock=FixedClock(end), volatility=0.006)
            gen = mp.get_ohlcv(sym.upper(), tf, start, end)
            repo.write_ohlcv(gen)
        rows = repo.read_ohlcv(sym.upper(), tf, start, end)
        bars.extend(rows)
        have_any = have_any or bool(rows)
    if not have_any:
        raise SystemExit("no bars available for synthetic replay")
    return SyntheticLiveSource(bars, delay_s=0.0)


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("symbols", nargs="+")
    ap.add_argument("--tf", default="M15", choices=[t.value for t in Timeframe])
    ap.add_argument("--synthetic", action="store_true", help="replay repository bars as live")
    ap.add_argument(
        "--live", metavar="EXCHANGE", choices=["kraken", "bybit"], help="real public WS"
    )
    ap.add_argument("--repo", default="data/repository")
    ap.add_argument("--max-bars", type=int, default=None)
    args = ap.parse_args()

    configure_logging("INFO")
    tf = Timeframe(args.tf)
    repo = MarketDataRepository(args.repo)
    im = build_instrument_master()
    bus = EventBus(raise_on_handler_error=True)
    health = SystemHealth()
    metrics = MetricsRegistry()

    if args.live:
        from trading_agent.data.providers.exchange_ws import BybitWSSource, KrakenWSSource

        cls = KrakenWSSource if args.live == "kraken" else BybitWSSource
        source = cls(
            [s.upper() for s in args.symbols], Timeframe.M1
        )  # WS builds M1 bars from trades
        _log.info("live WS source", extra={"exchange": args.live, "symbols": args.symbols})
    else:
        source = await _build_synthetic_source(repo, args.symbols, tf)  # type: ignore[assignment]
        _log.info("synthetic-live source", extra={"symbols": args.symbols, "tf": tf.value})

    ingestion = IngestionService(source, repo, bus, health=health, metrics=metrics)
    priority = {
        i.canonical_symbol: (
            1
            if i.trading_priority is TradingPriority.TIER_1
            else 2
            if i.trading_priority is TradingPriority.TIER_2
            else 3
        )
        for i in im.all()
    }
    scanner = ScannerShell(bus, metrics=metrics, priority=priority)
    supervisor = Supervisor(bus, ingestion, health=health, metrics=metrics)

    try:
        await supervisor.run(max_bars=args.max_bars)
    except KeyboardInterrupt:  # pragma: no cover
        await supervisor.shutdown("keyboard interrupt")

    print(json.dumps(supervisor.status(), indent=2, default=str))
    print(json.dumps({"scanner_observations": scanner.observations}, indent=2))
    print(json.dumps(metrics.snapshot(), indent=2, default=str))
    assert supervisor.orders_sent == 0, "PAPER_LIVE sent an order — this must never happen"
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
