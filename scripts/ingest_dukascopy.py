#!/usr/bin/env python
"""Ingest REAL historical FX / XAUUSD data (Dukascopy tick bulk) into the local repository.

Öffentliche Bulk-Quelle, **kein API-Key**. ``.bi5`` = LZMA-komprimierte 20-Byte-Tick-Records
(``>IIIff``: ms, ask_pts, bid_pts, ask_vol, bid_vol). Preis = points × point_factor.

    python scripts/ingest_dukascopy.py \
        --symbols XAUUSD EURUSD GBPUSD USDJPY \
        --start 2023-01-01 --end 2025-01-01 --repo data/repository_real

Aggregiert Ticks (Mid-Preis) zu M5, leitet M15/H4/D1 ab, schreibt Parquet + SQLite, führt die
Quality- + Replay-Dataset-Validierung aus und druckt ein vollständiges Dataset-Manifest
(inkl. mittlerem/max. Spread je Symbol).

**Nichts wird erfunden.** Fehlende Stunden (Wochenende / Feiertag / 404 / nach Retries) werden
gemeldet, nie synthetisiert. Wochenend-Lücken bei FX/XAU sind erwartet (``weekend_gap``-Kalender).
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from trading_agent.core.enums import AssetClass, Timeframe
from trading_agent.core.time import parse_timestamp
from trading_agent.data.providers.dukascopy import DukascopyProvider
from trading_agent.data.quality import check_ohlcv_series
from trading_agent.data.repository import MarketDataRepository
from trading_agent.data.resample import resample_ohlcv
from trading_agent.engine.replay import DatasetRequirements, validate_dataset
from trading_agent.refdata.seed import seed_calendars, seed_instruments

_TF = Timeframe.M5
_HIGHER = (Timeframe.M15, Timeframe.H4, Timeframe.D1)
_DATASET_VERSION = "dukascopy-tick-mid-m5-v1"


def _fmt(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbols", nargs="+", default=["XAUUSD", "EURUSD", "GBPUSD", "USDJPY"])
    ap.add_argument("--start", default="2023-01-01")
    ap.add_argument("--end", default="2025-01-01")
    ap.add_argument("--repo", default="data/repository_real")
    ap.add_argument("--cache", default="data/cache/dukascopy")
    args = ap.parse_args()

    start = parse_timestamp(args.start)
    end = parse_timestamp(args.end)
    repo = MarketDataRepository(args.repo)
    calendars = seed_calendars()
    inst_by_sym = {i.canonical_symbol: i for i in seed_instruments()}
    provider = DukascopyProvider(cache_dir=Path(args.cache))

    per_symbol: dict[str, dict[str, object]] = {}
    try:
        for symbol in args.symbols:
            inst = inst_by_sym.get(symbol)
            cal_id = inst.calendar_id if inst else "fx_weekday_24h"
            is_gold = inst is not None and inst.asset_class is AssetClass.GOLD

            bars = provider.get_ohlcv(symbol, _TF, start, end)
            spreads = provider.last_spread_stats
            missing = provider.missing_files
            if not bars:
                print(f"!! {symbol}: KEINE Bars von Dukascopy — nichts geschrieben.")
                per_symbol[symbol] = {"bars": 0, "missing_hours": len(missing)}
                continue

            status = check_ohlcv_series(
                bars,
                instrument=symbol,
                timeframe=_TF,
                now=end,
                calendar=calendars.get(cal_id),
            )
            repo.write_ohlcv(bars)
            cov = repo.ohlcv_coverage(symbol, _TF)
            fp = repo.dataset_fingerprint(symbol, _TF, as_of=end)

            higher: dict[str, int] = {}
            for htf in _HIGHER:
                hbars = resample_ohlcv(bars, _TF, htf, require_complete=True)
                if hbars:
                    repo.write_ohlcv(hbars)
                    higher[htf.value] = len(hbars)

            mean_spread = sum(s.mean_spread for s in spreads) / len(spreads) if spreads else None
            max_spread = max((s.max_spread for s in spreads), default=None)
            per_symbol[symbol] = {
                "asset_class": inst.asset_class.value if inst else "forex",
                "calendar_id": cal_id,
                "bars_m5": len(bars),
                "bars_higher": higher,
                "coverage_m5": [_fmt(c) for c in cov] if cov else None,
                "first_open_time": _fmt(bars[0].open_time),
                "last_open_time": _fmt(bars[-1].open_time),
                "mean_spread": mean_spread,
                "max_spread": max_spread,
                "spread_unit": "USD" if is_gold else "price",
                "quality_blocks_trading": status.blocks_trading,
                "quality_issues": [f"{i.code.value}:{i.severity.value}" for i in status.issues],
                "missing_hours": len(missing),
                "fingerprint_sha256_m5": fp,
            }
    finally:
        provider.close()

    # FX/XAU: Wochenend-Lücken sind normal → höhere Continuity-Toleranz, sonst nur Rauschen.
    req = DatasetRequirements(
        instruments=tuple(args.symbols),
        base_timeframe=_TF,
        min_days=180,
        warmup_bars=300,
        continuity_tolerance=0.35,
    )
    report = validate_dataset(repo, req, start=start, end=end)

    manifest = {
        "dataset_version": _DATASET_VERSION,
        "source": {
            "name": "Dukascopy Bank (datafeed.dukascopy.com)",
            "product": "historical tick data (.bi5, LZMA, 20-byte records)",
            "access": "public bulk download, no API key",
            "url_pattern": "https://datafeed.dukascopy.com/datafeed/{SYM}/{YYYY}/{MM0}/{DD}/{HH}h_ticks.bi5",
            "note": "MM0 is 0-indexed; one file per hour; 404 = no data for that hour",
        },
        "instruments": list(args.symbols),
        "timeframe": _TF.value,
        "backtest_window": {"start": _fmt(start), "end_exclusive": _fmt(end)},
        "timezone": "UTC (all timestamps)",
        "timestamp_convention": (
            "tick ts = hour_start + ms offset (ms precision); "
            "M5 open_time = interval start aligned to 5min; close_time = open_time + 5min; "
            "bar known only from close_time (PIT, no look-ahead); forming bar at window end excluded"
        ),
        "ohlcv_definition": (
            "price = MID (bid+ask)/2 per tick; open = first tick mid, high/low = extremes, "
            "close = last tick mid; volume = sum(bid_vol+ask_vol); trades = tick count; "
            "per-bar mean/max spread reported separately (not part of OHLCV model)"
        ),
        "point_factors": {"XAUUSD/JPY-pairs": 1e-3, "5-digit majors": 1e-5},
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
