#!/usr/bin/env python
"""Ingest REAL historical M5 OHLCV (Binance Vision bulk) into the local repository.

Official public bulk source — no API key, no rate limits, SHA-256 verified.

    python scripts/ingest_binance_vision.py \
        --symbols BTCUSDT ETHUSDT --start 2025-01-01 --end 2025-07-01 \
        --warmup-bars 300 --repo data/repository

Writes Parquet + SQLite meta under ``--repo``. Runs the full quality + replay-dataset
validation and prints a complete dataset manifest (source / instrument / period / timezone /
timestamp convention / OHLCV definition / dataset version / fingerprint).

**Nothing is invented.** Missing bulk files are reported, never synthesised. If validation is
red, the exact gaps are printed and the script exits non-zero.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta

from trading_agent.core.enums import Timeframe
from trading_agent.core.time import parse_timestamp
from trading_agent.data.providers.binance_vision import BinanceVisionProvider
from trading_agent.data.quality import check_ohlcv_series
from trading_agent.data.repository import MarketDataRepository
from trading_agent.data.resample import resample_ohlcv
from trading_agent.engine.replay import DatasetRequirements, validate_dataset
from trading_agent.refdata.seed import seed_calendars

_TF = Timeframe.M5
_HIGHER = (Timeframe.M15, Timeframe.H4, Timeframe.D1)
_DATASET_VERSION = "binance-vision-spot-klines-v1"


def _fmt(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbols", nargs="+", default=["BTCUSDT", "ETHUSDT"])
    ap.add_argument("--start", default="2025-01-01")
    ap.add_argument("--end", default="2025-07-01")
    ap.add_argument("--warmup-bars", type=int, default=300)
    ap.add_argument("--repo", default="data/repository")
    ap.add_argument("--cache", default="data/cache/binance_vision")
    ap.add_argument("--no-verify-checksum", action="store_true")
    args = ap.parse_args()

    start = parse_timestamp(args.start)
    end = parse_timestamp(args.end)
    warmup_delta = timedelta(seconds=_TF.seconds * args.warmup_bars)
    fetch_from = start - warmup_delta

    repo = MarketDataRepository(args.repo)
    calendars = seed_calendars()
    provider = BinanceVisionProvider(
        cache_dir=__import__("pathlib").Path(args.cache),
        verify_checksum=not args.no_verify_checksum,
    )

    per_symbol: dict[str, dict[str, object]] = {}
    try:
        for symbol in args.symbols:
            bars = provider.get_ohlcv(symbol, _TF, fetch_from, end)
            missing = provider.missing_files
            if not bars:
                print(f"!! {symbol}: KEINE Bars von Binance Vision — nichts geschrieben.")
                per_symbol[symbol] = {"bars": 0, "missing_files": list(missing)}
                continue

            status = check_ohlcv_series(
                bars,
                instrument=symbol,
                timeframe=_TF,
                now=end,
                calendar=calendars.get("crypto_24_7"),
            )
            repo.write_ohlcv(bars)
            cov = repo.ohlcv_coverage(symbol, _TF)
            fp = repo.dataset_fingerprint(symbol, _TF, as_of=end)

            # M15/H4/D1 aus M5 ableiten und **nativ** speichern — der Replay-Assembler slict
            # dann ein kleines Fenster je TF statt pro Tick 40k M5-Bars neu zu resamplen.
            # require_complete=True ⇒ nur abgeschlossene Ziel-Bars, kein Look-ahead.
            higher: dict[str, int] = {}
            for htf in _HIGHER:
                hbars = resample_ohlcv(bars, _TF, htf, require_complete=True)
                if hbars:
                    repo.write_ohlcv(hbars)
                    higher[htf.value] = len(hbars)

            per_symbol[symbol] = {
                "bars_m5": len(bars),
                "bars_higher": higher,
                "coverage_m5": [_fmt(c) for c in cov] if cov else None,
                "first_open_time": _fmt(bars[0].open_time),
                "last_open_time": _fmt(bars[-1].open_time),
                "quality_blocks_trading": status.blocks_trading,
                "quality_issues": [f"{i.code.value}:{i.severity.value}" for i in status.issues],
                "missing_files": list(missing),
                "fingerprint_sha256_m5": fp,
            }
    finally:
        provider.close()

    req = DatasetRequirements(
        instruments=tuple(args.symbols),
        base_timeframe=_TF,
        min_days=180,
        warmup_bars=args.warmup_bars,
    )
    report = validate_dataset(repo, req, start=start, end=end)

    manifest = {
        "dataset_version": _DATASET_VERSION,
        "source": {
            "name": "Binance Vision (data.binance.vision)",
            "product": "spot / klines (candlesticks)",
            "access": "public bulk download, no API key, SHA-256 CHECKSUM verified",
            "url_pattern": "https://data.binance.vision/data/spot/monthly/klines/{SYMBOL}/5m/",
        },
        "instruments": list(args.symbols),
        "timeframe": _TF.value,
        "backtest_window": {"start": _fmt(start), "end_exclusive": _fmt(end)},
        "fetch_window_incl_warmup": {"start": _fmt(fetch_from), "end_exclusive": _fmt(end)},
        "warmup_bars": args.warmup_bars,
        "timezone": "UTC (all timestamps)",
        "timestamp_convention": (
            "open_time = interval start, inclusive, aligned to 5min; "
            "close_time = open_time + 5min (Binance's own close_time column = open+interval-1ms "
            "is discarded); source epoch ms (µs for files from ~2025-01, auto-normalised)"
        ),
        "ohlcv_definition": (
            "open = first trade price in the interval, high/low = extremes, "
            "close = last trade price, volume = base-asset volume; quote_volume + trades kept"
        ),
        "per_symbol": per_symbol,
        "validation": {
            "ok": report.ok,
            "notes": list(report.notes),
            "missing": [f"{g.instrument}/{g.timeframe.value}: {g.reason}" for g in report.missing],
            "covered": {
                k: ([_fmt(v[0]), _fmt(v[1])] if v else None) for k, v in report.covered.items()
            },
        },
    }
    print(json.dumps(manifest, indent=2))

    if not report.ok:
        print("\n!! DATASET-VALIDIERUNG ROT — siehe validation.missing oben. Nichts erfunden.")
        return 2
    print("\n== Dataset-Validierung GRÜN ==")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
