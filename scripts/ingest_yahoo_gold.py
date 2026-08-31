#!/usr/bin/env python
"""Ingest gold (XAUUSD ~ GC=F) H1 history from Yahoo Finance → repo als ``XAUUSD-YF``.

Keylos, sofort. Yahoo begrenzt Stundendaten hart auf ~730 Tage → deckt ~2024-04 bis heute ab
(2024 Konsolidierung, 2025 Mega-Trend, 2026) — genau die Regime-Vielfalt, die der Binance-
XAUUSDT-Reihe (erst ab 2025-12) fehlt.

**Indikativ** (Yahoo-Futures-Close, kein echter Spot-Bid/Ask) — nur für Struktur-/Swing-
Forschung, **nicht** für Live-Trading. Eigene Instrument-ID ``XAUUSD-YF``, damit die echten
Dukascopy-/Binance-Reihen nicht vermischt werden.

    uv run python scripts/ingest_yahoo_gold.py --repo data/repository_real
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime

from trading_agent.core.enums import Timeframe
from trading_agent.core.time import parse_timestamp
from trading_agent.data.providers.yahoo_finance import YahooFinanceProvider
from trading_agent.data.repository import MarketDataRepository
from trading_agent.data.resample import resample_ohlcv

_DEST = "XAUUSD-YF"


async def _run(args: argparse.Namespace) -> int:
    start = parse_timestamp(args.start)
    end = parse_timestamp(args.end)
    repo = MarketDataRepository(args.repo)
    provider = YahooFinanceProvider(symbol_map={"XAUUSD-YF": "GC=F"})

    try:
        h1 = await provider.fetch_ohlcv("XAUUSD-YF", Timeframe.H1, start, end)
    finally:
        await provider.aclose()

    if len(h1) < 500:
        print(json.dumps({"error": f"nur {len(h1)} H1-Bars von Yahoo — Abbruch"}, indent=2))
        return 1

    # auf die Ziel-Instrument-ID umschreiben (Yahoo liefert canonical bereits, aber sicher ist sicher)
    h1 = [b.model_copy(update={"instrument": _DEST, "source": "yahoo_indicative"}) for b in h1]
    repo.write_ohlcv(h1)

    written: dict[str, int] = {"H1": len(h1)}
    for tf, complete in ((Timeframe.H4, True), (Timeframe.D1, False)):
        # D1: Gold-Futures handeln ~23h/Tag (1h Pause) → kein Tag hat alle 24 H1-Bars →
        # require_complete=False (die Tages-Struktur bleibt für die Regime-Ableitung valide).
        res = resample_ohlcv(
            h1, Timeframe.H1, tf, require_complete=complete, source_name="yahoo_h1"
        )
        res = [b.model_copy(update={"instrument": _DEST}) for b in res]
        if res:
            repo.write_ohlcv(res)
            written[tf.value] = len(res)

    cov = repo.ohlcv_coverage(_DEST, Timeframe.H1)
    manifest = {
        "instrument": _DEST,
        "source": "Yahoo Finance /v8/finance/chart/GC=F (indicative, no key)",
        "timeframes_written": written,
        "h1_coverage": [c.isoformat() for c in cov] if cov else None,
        "h1_span": [h1[0].open_time.isoformat(), h1[-1].open_time.isoformat()],
        "note": (
            "INDIKATIV — Yahoo-Futures-Close, kein Spot-Bid/Ask. Nur Struktur-/Swing-Forschung, "
            "nicht Live-Trading. H4/D1 aus H1 resampled (require_complete, PIT)."
        ),
    }
    print(json.dumps(manifest, indent=2))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", default="data/repository_real")
    ap.add_argument("--start", default="2023-01-01")
    ap.add_argument("--end", default=datetime.now(UTC).date().isoformat())
    return asyncio.run(_run(ap.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
