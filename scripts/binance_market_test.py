#!/usr/bin/env python
"""BINANCE — READ-ONLY Marktdaten-Test (kein API-Key nötig).

    python scripts/binance_market_test.py [--symbol XAUUSDT] [--json]

Prüft über den **public** REST-Adapter (USD-M-Futures):
  1. Server-Zeit + Clock-Skew.
  2. Symbol verfügbar? (XAUUSDT liegt auf USD-M-Futures, nicht Spot — Spot hat PAXGUSDT.)
  3. Bid/Ask (bookTicker) + 24h-Ticker.
  4. Historische Candles M1/M5/M15/H1/H4/D1.
  5. Mark Price, Funding (Historie), Open Interest (Historie).
  6. Durchstich: Symbol → LivePipeline-Warmup (M5+M15/H4/D1) → MarketContext → evaluate().

Kein Order-Pfad, keine Credentials.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from trading_agent.core.enums import AssetClass, Timeframe
from trading_agent.data.providers.binance import BinancePublicDataProvider
from trading_agent.refdata.seed import seed_sessions
from trading_agent.runtime.live_pipeline import (
    LivePipeline,
    LivePipelineConfig,
    build_rest_provider,
)


async def run(*, symbol: str, as_json: bool) -> int:
    symbol = symbol.upper()
    p = BinancePublicDataProvider(market="futures_usdm")
    report: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "market": "binance USD-M futures",
        "symbol": symbol,
        "orders_sent": 0,
    }
    checks: dict[str, Any] = {}
    report["checks"] = checks
    try:
        server, skew = await p.server_time()
        checks["server_time"] = {"utc": server.isoformat(), "clock_skew_seconds": round(skew, 3)}

        available = await p.has_symbol(symbol)
        checks["symbol_available"] = available
        if not available:
            report["status"] = "SYMBOL_NOT_FOUND"
            _emit(report, as_json)
            await p.aclose()
            return 1

        q = await p.fetch_quote(symbol)
        checks["quote"] = {
            "bid": q.bid,
            "ask": q.ask,
            "spread": round(q.ask - q.bid, 5),
            "ts": q.ts.isoformat(),
        }
        t = await p.fetch_ticker_24h(symbol)
        checks["ticker_24h"] = {"last": t["last"], "volume": t["volume"]}

        now = datetime.now(UTC)
        candles: dict[str, Any] = {}
        for tf in (
            Timeframe.M1,
            Timeframe.M5,
            Timeframe.M15,
            Timeframe.H1,
            Timeframe.H4,
            Timeframe.D1,
        ):
            span = timedelta(hours=6) if tf.seconds <= 900 else timedelta(days=30)
            bars = await p.fetch_ohlcv(symbol, tf, now - span, now)
            candles[tf.value] = {
                "bars": len(bars),
                "last_close": bars[-1].close if bars else None,
                "last_open": bars[-1].open_time.isoformat() if bars else None,
            }
        checks["candles"] = candles

        mp = await p.fetch_mark_price(symbol)
        checks["mark_price"] = {
            "mark": mp["mark_price"],
            "last_funding_rate": mp["last_funding_rate"],
            "next_funding_time": mp["next_funding_time"].isoformat()
            if mp["next_funding_time"]
            else None,
        }
        fund = await p.fetch_funding(symbol, now - timedelta(days=3), now)
        checks["funding_history"] = {
            "rows": len(fund),
            "latest_rate": fund[-1].rate if fund else None,
        }
        oi = await p.fetch_open_interest(symbol, now - timedelta(days=2), now)
        checks["open_interest"] = {"rows": len(oi), "latest": oi[-1].oi if oi else None}
    finally:
        await p.aclose()

    # --- Durchstich durch die bestehende Pipeline ---
    try:
        cfg = LivePipelineConfig(
            exchange="binance",
            instruments=(symbol,),
            asset_class=AssetClass.GOLD if "XAU" in symbol else AssetClass.CRYPTO,
            session_specs=tuple(seed_sessions()) if "XAU" in symbol else (),
            derivatives=True,
        )
        rest = build_rest_provider("binance")
        pipe = LivePipeline(cfg, rest_provider=rest)
        warm = await pipe.warmup()
        await pipe.prime()
        step = pipe.steps[-1]
        d = step.tick.result.decision
        dv = pipe._derivatives[symbol]
        checks["pipeline"] = {
            "warmup": warm.get(symbol),
            "decision": d.decision.value,
            "setup_state": d.setup_state.value,
            "reason_codes": [x.value for x in d.reason_codes],
            "derivatives_ctx": {
                "funding_rate": dv.funding_rate,
                "open_interest": dv.open_interest,
                "open_interest_as_of": dv.open_interest_as_of.isoformat()
                if dv.open_interest_as_of
                else None,
            },
            "orders_sent": pipe.orders_sent,
        }
        await rest.aclose()
        assert pipe.orders_sent == 0
    except Exception as exc:
        checks["pipeline"] = {"error": f"{type(exc).__name__}: {exc}"}

    ok_candles = all(c["bars"] > 0 for c in checks["candles"].values())
    report["status"] = "CONNECTED" if ok_candles else "PARTIAL"
    _emit(report, as_json)
    return 0


def _emit(report: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(report, indent=2, default=str))
        return
    c = report["checks"]
    print(f"\n=== BINANCE MARKET TEST — {report['symbol']} — {report['generated_at']} ===")
    print(f"Status: {report['status']}   ({report['market']})")
    if "server_time" in c:
        print(
            f"  Server-Zeit: {c['server_time']['utc']}  skew={c['server_time']['clock_skew_seconds']}s"
        )
    print(f"  Symbol verfügbar: {c.get('symbol_available')}")
    if "quote" in c:
        print(
            f"  Bid/Ask: {c['quote']['bid']} / {c['quote']['ask']}  spread={c['quote']['spread']}"
        )
        print(f"  24h: last={c['ticker_24h']['last']}  vol={c['ticker_24h']['volume']}")
    if "candles" in c:
        for tf, v in c["candles"].items():
            print(
                f"    {tf:>3}: {v['bars']:>4} bars  last_close={v['last_close']} @ {v['last_open']}"
            )
    if "mark_price" in c:
        m = c["mark_price"]
        print(
            f"  Mark: {m['mark']}  Funding: {m['last_funding_rate']}  next={m['next_funding_time']}"
        )
        print(
            f"  Funding-Historie: {c['funding_history']['rows']} rows (latest {c['funding_history']['latest_rate']})"
        )
        print(
            f"  Open Interest: {c['open_interest']['rows']} rows (latest {c['open_interest']['latest']})"
        )
    if "pipeline" in c:
        pl = c["pipeline"]
        if "error" in pl:
            print(f"  Pipeline: FEHLER {pl['error']}")
        else:
            print(f"  Pipeline: {pl['warmup']}")
            print(
                f"           decision={pl['decision']}/{pl['setup_state']}  reasons={pl['reason_codes']}"
            )
            print(
                f"           derivatives={pl['derivatives_ctx']}  orders_sent={pl['orders_sent']}"
            )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbol", default="XAUUSDT")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    return asyncio.run(run(symbol=args.symbol, as_json=args.json))


if __name__ == "__main__":
    raise SystemExit(main())
