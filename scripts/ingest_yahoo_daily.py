#!/usr/bin/env python3
"""Lange TAGES-Historie von Yahoo als ``<SYM>-YFD`` ingestieren.

Warum ein eigenes Skript neben ``ingest_yahoo.py``: jenes holt H1 und leitet D1 daraus ab,
und Yahoo begrenzt Stundendaten hart auf ~730 Tage. Fuer eine Tagesstrategie ist das die
falsche Beschraenkung — Tagesbars gibt Yahoo ueber Jahrzehnte.

Eigene Instrument-IDs mit Suffix ``-YFD`` (Yahoo Daily), damit die aus H1 abgeleiteten
``-YF``-Reihen unberuehrt bleiben. Zwei Ableitungswege duerfen nicht in derselben Reihe
landen.

**Indikativ.** Yahoo-Close, kein echter Bid/Ask, bei FX kein Interbank-Kurs. Fuer
Struktur- und Allokationsforschung brauchbar, nicht fuer Live-Ausfuehrung. Das
Kostenmodell fuehrt diese Reihen als ``tradeable: false``.

    python3 scripts/ingest_yahoo_daily.py
    python3 scripts/ingest_yahoo_daily.py --instruments NVDA AAPL --years 5
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta

from trading_agent.core.enums import Timeframe
from trading_agent.data.providers.yahoo_finance import YahooFinanceProvider
from trading_agent.data.repository import MarketDataRepository

# kanonischer Name -> Yahoo-Ticker. Aktien sind blanke Ticker.
_MAP: dict[str, str] = {
    # Gold / FX
    "XAUUSD": "GC=F",
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "JPY=X",
    # Einzelaktien (keine ETFs — Masterplan-Vorgabe)
    "NVDA": "NVDA",
    "AAPL": "AAPL",
    "MSFT": "MSFT",
    "AMD": "AMD",
    "GOOGL": "GOOGL",
    "META": "META",
    # Makro-Kontext, NICHT handelbar
    "SPX": "^GSPC",
    "VIX": "^VIX",
    "DXY": "DX-Y.NYB",
    "US10Y": "^TNX",
}

_DEFAULT = [
    "XAUUSD",
    "EURUSD",
    "GBPUSD",
    "USDJPY",
    "NVDA",
    "AAPL",
    "MSFT",
    "AMD",
    "GOOGL",
    "META",
    "SPX",
    "VIX",
    "DXY",
    "US10Y",
]


async def _one(
    inst: str, start: datetime, end: datetime, repo: MarketDataRepository
) -> dict[str, object]:
    dest = f"{inst}-YFD"
    ticker = _MAP.get(inst, inst)
    provider = YahooFinanceProvider(symbol_map={dest: ticker})
    try:
        bars = await provider.fetch_ohlcv(dest, Timeframe.D1, start, end)
    except Exception as exc:
        return {"instrument": dest, "error": f"{type(exc).__name__}: {exc}"}
    finally:
        await provider.aclose()
    if len(bars) < 200:
        return {"instrument": dest, "error": f"nur {len(bars)} D1-Bars"}

    # Yahoo stempelt Tagesbars auf die HANDELSEROEFFNUNG (US-Aktien 13:30 UTC, VIX 07:00,
    # DXY 04:00). Die Repository-Konvention fuer D1 ist Mitternacht UTC. Alle beobachteten
    # Stempel liegen im selben UTC-Kalendertag, das Abschneiden auf 00:00 verschiebt also
    # keinen Bar auf einen anderen Tag. Dokumentierte Normalisierung, keine Erfindung —
    # OHLCV-Werte bleiben unveraendert.
    shifted = 0
    normed = []
    for b in bars:
        ot = b.open_time
        day = ot.replace(hour=0, minute=0, second=0, microsecond=0)
        if day != ot:
            shifted += 1
        normed.append(
            b.model_copy(
                update={
                    "instrument": dest,
                    "source": "yahoo_daily_indicative",
                    "open_time": day,
                    "close_time": day + timedelta(days=1),
                }
            )
        )
    bars = normed
    repo.write_ohlcv(bars)
    return {
        "instrument": dest,
        "ticker": ticker,
        "d1_bars": len(bars),
        "normalised_timestamps": shifted,
        "span": [bars[0].open_time.date().isoformat(), bars[-1].open_time.date().isoformat()],
    }


async def _run(args: argparse.Namespace) -> int:
    end = datetime.now(UTC)
    start = end - timedelta(days=int(args.years * 365.25))
    repo = MarketDataRepository(args.repo)
    out = []
    for inst in args.instruments:
        res = await _one(inst, start, end, repo)
        out.append(res)
        if "error" in res:
            print(f"  ! {res['instrument']}: {res['error']}")
        else:
            print(
                f"  {res['instrument']:<12} {res['d1_bars']:>5} D1  {res['span'][0]} -> {res['span'][1]}"
            )
        await asyncio.sleep(0.4)  # hoeflich gegen Yahoo
    ok = sum(1 for r in out if "error" not in r)
    print(f"\n{ok}/{len(out)} Reihen geschrieben")
    if args.json:
        print(json.dumps({"source": "Yahoo Finance daily (indicative)", "results": out}, indent=2))
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", default="data/repository_real")
    ap.add_argument("--instruments", nargs="+", default=_DEFAULT)
    ap.add_argument("--years", type=float, default=8.0)
    ap.add_argument("--json", action="store_true")
    return asyncio.run(_run(ap.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
