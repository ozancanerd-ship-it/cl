#!/usr/bin/env python
"""Fetch REAL historical OHLCV into the local repository.

RUN THIS YOURSELF (needs network). No API key required — public market data only.

    python scripts/fetch_history.py BTCUSDT --tf M15 --days 90
    python scripts/fetch_history.py ETHUSDT BTCUSDT --tf H1 --days 365 --provider bybit_public

Data goes to  data/repository/  (Parquet + SQLite meta). Nothing is sent anywhere.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime, timedelta

from trading_agent.core.enums import Timeframe
from trading_agent.data.providers.bybit_public import BybitPublicDataProvider
from trading_agent.data.providers.kraken import KrakenDataProvider
from trading_agent.data.quality import check_ohlcv_series
from trading_agent.data.repository import MarketDataRepository
from trading_agent.refdata.seed import seed_calendars
from trading_agent.utils.logging import configure_logging, get_logger

_log = get_logger("fetch_history")


async def _fetch(provider: str, symbol: str, tf: Timeframe, start: datetime, end: datetime):
    if provider == "kraken":
        p = KrakenDataProvider()
    elif provider == "bybit_public":
        p = BybitPublicDataProvider()
    else:  # pragma: no cover
        raise SystemExit(f"unknown provider {provider!r} (use: kraken | bybit_public)")
    try:
        return await p.fetch_ohlcv(symbol, tf, start, end)
    finally:
        await p.aclose()


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("symbols", nargs="+")
    ap.add_argument("--tf", default="M15", choices=[t.value for t in Timeframe])
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--provider", default="kraken", choices=["kraken", "bybit_public"])
    ap.add_argument("--repo", default="data/repository")
    args = ap.parse_args()

    configure_logging("INFO")
    tf = Timeframe(args.tf)
    end = datetime.now(UTC)
    start = end - timedelta(days=args.days)
    repo = MarketDataRepository(args.repo)
    calendars = seed_calendars()

    total = 0
    for symbol in args.symbols:
        _log.info("fetching", extra={"symbol": symbol, "tf": tf.value, "provider": args.provider})
        bars = await _fetch(args.provider, symbol, tf, start, end)
        if not bars:
            _log.warning("no bars returned", extra={"symbol": symbol})
            continue
        status = check_ohlcv_series(
            bars,
            instrument=symbol.upper(),
            timeframe=tf,
            now=end,
            calendar=calendars.get("crypto_24_7"),
        )
        _log.info(
            "quality",
            extra={
                "symbol": symbol,
                "bars": len(bars),
                "blocks_trading": status.blocks_trading,
                "issues": [i.code.value for i in status.issues],
            },
        )
        repo.write_ohlcv(bars)
        cov = repo.ohlcv_coverage(symbol.upper(), tf)
        _log.info(
            "stored",
            extra={"symbol": symbol, "coverage": [c.isoformat() for c in cov] if cov else None},
        )
        total += len(bars)

    print(f"done: {total} bars stored under {args.repo}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
