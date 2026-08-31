#!/usr/bin/env python
"""Ingest REAL historical USD-M-Futures OHLCV (Binance REST) into the local repository.

    python scripts/ingest_binance_futures.py --symbols XAUUSDT \
        --start 2025-12-11 --end 2026-09-01 --repo data/repository_real

Paginiert ``/fapi/v1/klines`` (1500 Bars/Request) über den vorhandenen
``BinancePublicDataProvider`` (kein API-Key). M5 = Basis; M15/H4/D1 werden PIT-sauber aus M5
**abgeleitet** (``require_complete=True`` — nur abgeschlossene Ziel-Bars, kein Look-ahead) und
zusätzlich **nativ** gespeichert. Danach: Quality-Check + Replay-Dataset-Validierung +
vollständiges Manifest.

**Nichts wird erfunden.** Fehlt ein Fenster, wird es protokolliert, nicht synthetisiert.
XAUUSDT ist ein junger ``TRADIFI_PERPETUAL`` (Handelsstart ~2025-12-11) — mehr Historie als
das existiert nicht.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime, timedelta

from trading_agent.core.enums import Timeframe
from trading_agent.core.models import OHLCV
from trading_agent.core.time import parse_timestamp
from trading_agent.data.providers.binance import BinancePublicDataProvider
from trading_agent.data.quality import check_ohlcv_series, deduplicate_ohlcv, sort_ohlcv
from trading_agent.data.repository import MarketDataRepository
from trading_agent.data.resample import resample_ohlcv
from trading_agent.engine.replay import DatasetRequirements, validate_dataset
from trading_agent.refdata.seed import seed_calendars, seed_instruments

_TF = Timeframe.M5
_HIGHER = (Timeframe.M15, Timeframe.H4, Timeframe.D1)
_DATASET_VERSION = "binance-fapi-klines-m5-v1"


def _fmt(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat()


async def _fetch_all_m5(
    p: BinancePublicDataProvider, symbol: str, start: datetime, end: datetime
) -> list[OHLCV]:
    """Paginiert M5-Klines über [start, end). Advance am letzten open_time + 5min."""
    out: list[OHLCV] = []
    cursor = start
    step = timedelta(seconds=_TF.seconds)
    hard_end = min(end, datetime.now(UTC))
    while cursor < hard_end:
        window_end = min(cursor + step * 1500, hard_end)
        bars = await p.fetch_ohlcv(symbol, _TF, cursor, window_end)
        if not bars:
            cursor = window_end  # Loch — weiter, nicht synthetisieren
            continue
        out.extend(bars)
        nxt = bars[-1].open_time + step
        if nxt <= cursor:  # kein Fortschritt → abbrechen
            break
        cursor = nxt
    # nur abgeschlossene Bars — die noch formende Kerze (close_time > jetzt) wird verworfen
    now = datetime.now(UTC)
    window = [b for b in out if start <= b.open_time < end and b.close_time <= now]
    cleaned, _conflicts = deduplicate_ohlcv(window)
    return list(sort_ohlcv(cleaned))


async def _run(args: argparse.Namespace) -> int:
    start = parse_timestamp(args.start)
    end = parse_timestamp(args.end)
    repo = MarketDataRepository(args.repo)
    calendars = seed_calendars()
    inst_by_sym = {i.canonical_symbol: i for i in seed_instruments()}
    p = BinancePublicDataProvider(market="futures_usdm")

    per_symbol: dict[str, dict[str, object]] = {}
    try:
        for symbol in args.symbols:
            inst = inst_by_sym.get(symbol)
            cal_id = inst.calendar_id if inst else "crypto_24_7"

            m5 = await _fetch_all_m5(p, symbol, start, end)
            if not m5:
                print(f"!! {symbol}: KEINE M5-Bars von Binance — nichts geschrieben.")
                per_symbol[symbol] = {"bars_m5": 0}
                continue

            # Staleness gegen die echte Uhr prüfen, nicht gegen das (evtl. in der Zukunft
            # liegende) Fensterende — sonst meldet der frischeste Datensatz "stale".
            status = check_ohlcv_series(
                m5,
                instrument=symbol,
                timeframe=_TF,
                now=min(end, datetime.now(UTC)),
                calendar=calendars.get(cal_id),
            )
            repo.write_ohlcv(m5)
            cov = repo.ohlcv_coverage(symbol, _TF)
            fp = repo.dataset_fingerprint(symbol, _TF, as_of=end)

            higher: dict[str, int] = {}
            for htf in _HIGHER:
                hbars = resample_ohlcv(m5, _TF, htf, require_complete=True)
                if hbars:
                    repo.write_ohlcv(hbars)
                    higher[htf.value] = len(hbars)

            # interne Lücken (nach Kalender) grob quantifizieren
            span_bars = int((m5[-1].open_time - m5[0].open_time).total_seconds() // _TF.seconds) + 1
            gap_bars = span_bars - len(m5)

            per_symbol[symbol] = {
                "asset_class": inst.asset_class.value if inst else "crypto",
                "calendar_id": cal_id,
                "bars_m5": len(m5),
                "bars_higher": higher,
                "coverage_m5": [_fmt(c) for c in cov] if cov else None,
                "first_open_time": _fmt(m5[0].open_time),
                "last_open_time": _fmt(m5[-1].open_time),
                "internal_gap_bars_m5": gap_bars,
                "completeness_pct": round(100.0 * len(m5) / span_bars, 3),
                "quality_blocks_trading": status.blocks_trading,
                "quality_issues": [f"{i.code.value}:{i.severity.value}" for i in status.issues],
                "fingerprint_sha256_m5": fp,
            }
    finally:
        await p.aclose()

    # Validierung gegen den *effektiv nutzbaren* Backtest-Start (erster M5-Bar + Warmup) bis
    # zum letzten M5-Bar. ``require_native_higher`` bleibt aus — die höheren TFs sind da, aber
    # ein junger Perp hat naturgemäß keine 200 D1-Bars *vor* seinem Listing. Der Backtest
    # steuert den Vorlauf über ``--start``.
    first = min(
        datetime.fromisoformat(v["first_open_time"])
        for v in per_symbol.values()
        if v.get("bars_m5")
    )
    last = max(
        datetime.fromisoformat(v["last_open_time"]) for v in per_symbol.values() if v.get("bars_m5")
    )
    usable_start = first + timedelta(seconds=_TF.seconds * (args.warmup_bars + 1))
    req = DatasetRequirements(
        instruments=tuple(args.symbols),
        base_timeframe=_TF,
        min_days=args.min_days,
        warmup_bars=args.warmup_bars,
        require_native_higher=False,
        continuity_tolerance=0.02,
    )
    report = validate_dataset(repo, req, start=usable_start, end=last)
    manifest_extra = {
        "recommended_backtest_window": {
            "start_after_m5_warmup": _fmt(usable_start),
            "end": _fmt(last),
            "note": "Für vollen HTF-Vorlauf (D1) später starten; XAUUSDT-Listing ~2025-12-11.",
        }
    }

    manifest = {
        "dataset_version": _DATASET_VERSION,
        "source": {
            "name": "Binance USD-M Futures REST (fapi.binance.com)",
            "product": "perpetual futures klines",
            "access": "public REST, no API key, paginated 1500/request",
            "endpoint": "https://fapi.binance.com/fapi/v1/klines",
        },
        "instruments": list(args.symbols),
        "timeframe": _TF.value,
        "backtest_window": {"start": _fmt(start), "end_exclusive": _fmt(end)},
        "timezone": "UTC (all timestamps)",
        "timestamp_convention": (
            "open_time = interval start, aligned to 5min; close_time = open_time + 5min "
            "(Binance closeTime = open+interval-1ms wird verworfen); Epoch ms; "
            "M15/H4/D1 aus M5 resampled mit require_complete=True (nur abgeschlossene Ziel-Bars, "
            "kein Look-ahead)"
        ),
        "ohlcv_definition": (
            "open = erster Trade-Preis im Intervall, high/low = Extrema, close = letzter "
            "Trade-Preis, volume = Basis-Asset-Menge; quote_volume + trades erhalten"
        ),
        "per_symbol": per_symbol,
        **manifest_extra,
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
    wrote = any(v.get("bars_m5") for v in per_symbol.values())
    if not wrote:
        print("\n!! KEINE Daten geschrieben.")
        return 2
    print(
        "\n== Ingest abgeschlossen ==  "
        + ("Dataset-Validierung GRÜN" if report.ok else "Validierung: siehe notes/missing")
    )
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbols", nargs="+", default=["XAUUSDT"])
    ap.add_argument("--start", default="2025-12-11")
    ap.add_argument("--end", default=datetime.now(UTC).date().isoformat())
    ap.add_argument("--repo", default="data/repository_real")
    ap.add_argument("--warmup-bars", type=int, default=300)
    ap.add_argument("--min-days", type=int, default=120)
    args = ap.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
