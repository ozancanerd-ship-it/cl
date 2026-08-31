#!/usr/bin/env python
"""Einzelaktien-Analyse (Masterplan §45–§52) — Trend/Struktur/Momentum/rel. Stärke/Volumen/
Volatilität (+ Fundamentals/Earnings, wenn Daten da) → 0–100-Score + Verdikt.

Liest D1-Reihen aus dem Repo (z. B. ``NVDA-YF`` via ``scripts/ingest_yahoo.py --equity``),
Benchmark ``SPX-YF``. **Nur Einzelwerte — ETFs werden abgelehnt.** Kein Broker, keine Order.

    uv run python scripts/ingest_yahoo.py --equity --instruments NVDA AAPL MSFT AMD
    uv run python scripts/ingest_yahoo.py --instruments SPX
    uv run python scripts/stock_analysis.py --symbols NVDA AAPL MSFT AMD
    uv run python scripts/stock_analysis.py --symbols NVDA --json
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime

from trading_agent.core.enums import Timeframe
from trading_agent.data.repository import MarketDataRepository
from trading_agent.investment.stock_analysis import StockAnalysisEngine


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", default="data/repository_real")
    ap.add_argument("--symbols", nargs="+", required=True, help="blanke Ticker, intern <SYM>-YF")
    ap.add_argument("--benchmark", default="SPX-YF")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    repo = MarketDataRepository(args.repo)
    as_of = datetime.now(UTC)
    lo, hi = datetime(2000, 1, 1, tzinfo=UTC), datetime(2100, 1, 1, tzinfo=UTC)
    bench = repo.read_ohlcv(args.benchmark, Timeframe.D1, lo, hi) or None
    engine = StockAnalysisEngine()

    rows: list[dict[str, object]] = []
    for sym in args.symbols:
        dest = sym if sym.endswith("-YF") else f"{sym}-YF"
        d1 = repo.read_ohlcv(dest, Timeframe.D1, lo, hi)
        a = engine.analyze(dest, d1, as_of=as_of, benchmark_d1=bench)
        rows.append(a.as_dict())

    rows.sort(key=lambda r: r["score"], reverse=True)  # type: ignore[arg-type,return-value]
    if args.json:
        print(json.dumps({"as_of": as_of.isoformat(), "ranking": rows}, indent=2, default=str))
        return 0

    print(f"\n{'=' * 68}\n  EINZELAKTIEN-ANALYSE  ·  {as_of.date()}  ·  Benchmark {args.benchmark}")
    print(f"{'=' * 68}")
    for i, r in enumerate(rows, 1):
        print(f"\n  #{i}  {str(r['symbol']).replace('-YF', ''):<8} {r['score']:>5.1f}/100  "
              f"{str(r['verdict']).upper()}")
        for f in r["factors"]:  # type: ignore[union-attr]
            bar = "█" * round(f["value"] * 10)
            print(f"      {f['name']:<18} {f['value']:.2f} {bar:<10} (w{f['weight']:.2f})  {f['detail']}")
        if r["excluded"]:
            print(f"      ausgeschlossen: {', '.join(r['excluded'])}")  # type: ignore[arg-type]
        for n in r["notes"]:  # type: ignore[union-attr]
            print(f"      · {n}")
    print(f"\n{'=' * 68}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
