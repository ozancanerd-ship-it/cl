#!/usr/bin/env python
"""Ingest H1-Historie (Gold + FX) von Yahoo Finance in die Repo als ``<SYM>-YF``.

Keylos, sofort. Yahoo begrenzt Stundendaten hart auf ~730 Tage → deckt ~2024-04 bis heute ab.
**Indikativ** (Yahoo-Close, kein echter Bid/Ask) — nur fuer Struktur-/Swing-Forschung, nicht
fuer Live-Trading. Eigene IDs ``XAUUSD-YF`` / ``EURUSD-YF`` / ``GBPUSD-YF`` / ``USDJPY-YF``,
damit echte Dukascopy-/Binance-Reihen nicht vermischt werden.

    uv run python scripts/ingest_yahoo.py --instruments XAUUSD EURUSD GBPUSD USDJPY
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime

from trading_agent.core.enums import Timeframe
from trading_agent.core.time import parse_timestamp
from trading_agent.data.providers.yahoo_finance import DEFAULT_SYMBOL_MAP, YahooFinanceProvider
from trading_agent.data.repository import MarketDataRepository
from trading_agent.data.resample import resample_ohlcv

# Cross-Asset-Proxies (keylos): Broad-USD-Index, 10Y-/2Y-Rendite (×10), VIX, S&P 500.
# Für den Makro-/Confluence-Layer (CrossAssetContext) — v. a. Gold ist real-yield-/DXY-getrieben.
_INDEX_MAP = {
    "DXY": "DX-Y.NYB",
    "US10Y": "^TNX",
    "US02Y": "^FVX",  # 5Y als 2Y-Proxy (Yahoo hat kein ^IRX-Äquivalent für 2Y stabil)
    "VIX": "^VIX",
    "SPX": "^GSPC",
}
_YAHOO = {"XAUUSD": "GC=F", **DEFAULT_SYMBOL_MAP, **_INDEX_MAP}


async def _one(
    inst: str, start: datetime, end: datetime, repo: MarketDataRepository
) -> dict[str, object]:
    dest = f"{inst}-YF"
    yahoo_sym = _YAHOO.get(inst) or (inst if inst.startswith("^") else f"{inst}=X")
    provider = YahooFinanceProvider(symbol_map={dest: yahoo_sym})
    try:
        h1 = await provider.fetch_ohlcv(dest, Timeframe.H1, start, end)
    finally:
        await provider.aclose()
    if len(h1) < 500:
        return {"instrument": dest, "error": f"nur {len(h1)} H1-Bars"}
    h1 = [b.model_copy(update={"instrument": dest, "source": "yahoo_indicative"}) for b in h1]
    repo.write_ohlcv(h1)
    written: dict[str, int] = {"H1": len(h1)}
    for tf, complete in ((Timeframe.H4, True), (Timeframe.D1, False)):
        res = resample_ohlcv(
            h1, Timeframe.H1, tf, require_complete=complete, source_name="yahoo_h1"
        )
        res = [b.model_copy(update={"instrument": dest}) for b in res]
        if res:
            repo.write_ohlcv(res)
            written[tf.value] = len(res)
    return {
        "instrument": dest,
        "timeframes_written": written,
        "h1_span": [h1[0].open_time.isoformat(), h1[-1].open_time.isoformat()],
    }


async def _run(args: argparse.Namespace) -> int:
    start = parse_timestamp(args.start)
    end = parse_timestamp(args.end)
    repo = MarketDataRepository(args.repo)
    out = [await _one(inst, start, end, repo) for inst in args.instruments]
    print(json.dumps({"source": "Yahoo Finance (indicative, no key)", "results": out}, indent=2))
    return 0 if all("error" not in r for r in out) else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", default="data/repository_real")
    ap.add_argument("--instruments", nargs="+", default=["XAUUSD", "EURUSD", "GBPUSD", "USDJPY"])
    ap.add_argument("--start", default="2023-01-01")
    ap.add_argument("--end", default=datetime.now(UTC).date().isoformat())
    return asyncio.run(_run(ap.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
