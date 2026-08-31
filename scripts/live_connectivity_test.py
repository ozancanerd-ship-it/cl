#!/usr/bin/env python
"""READ-ONLY Connectivity-Test für Kraken + Bybit **public market data**.

    python scripts/live_connectivity_test.py --symbols BTCUSDT ETHUSDT --json

Prüft je Exchange, **ohne** API-Key, ohne Order-/Trading-/Withdraw-Rechte:

* Server-Zeit (REST)          → Clock-Skew gegen die lokale UTC-Uhr
* OHLCV M1 + M5 (REST)        → letzte confirmed Bars, Latenz, Data-Quality
* Bester Bid/Ask (REST)       → Quote, Spread
* WebSocket connect+subscribe → N Live-Trades, Verbindungslatenz, Reconnect-Fähigkeit (Vertrag)

Nicht unterstützte Funktionen werden als **NOT_AVAILABLE / DEGRADED** gemeldet — **nichts wird
simuliert**. Kein Order-Pfad, kein Broker.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from trading_agent.core.enums import Timeframe
from trading_agent.core.time import ensure_utc
from trading_agent.data.providers.bybit_public import BybitPublicDataProvider
from trading_agent.data.providers.exchange_ws import BybitWSSource, KrakenWSSource
from trading_agent.data.providers.kraken import KrakenDataProvider
from trading_agent.data.quality import check_ohlcv_series

_KRAKEN_TIME = "https://api.kraken.com/0/public/Time"
_BYBIT_TIME = "https://api.bybit.com/v5/market/time"


async def _timed(coro: Any) -> tuple[Any, float, str | None]:
    t0 = time.monotonic()
    try:
        res = await coro
        return res, (time.monotonic() - t0) * 1000.0, None
    except Exception as exc:
        return None, (time.monotonic() - t0) * 1000.0, f"{type(exc).__name__}: {exc}"


async def _server_time(url: str) -> dict[str, Any]:
    import httpx

    async with httpx.AsyncClient(timeout=12.0) as c:
        r = await c.get(url)
        r.raise_for_status()
        return r.json()  # type: ignore[no-any-return]


def _future_check(times: list[datetime], now: datetime) -> list[str]:
    return [t.isoformat() for t in times if ensure_utc(t) > now + timedelta(seconds=2)]


async def _test_rest(name: str, provider: Any, symbol: str, time_url: str) -> dict[str, Any]:
    out: dict[str, Any] = {"exchange": name, "symbol": symbol}
    now = datetime.now(UTC)

    st, st_ms, st_err = await _timed(_server_time(time_url))
    if st_err:
        out["server_time"] = {"ok": False, "error": st_err, "latency_ms": round(st_ms)}
    else:
        if name == "kraken":
            srv = datetime.fromtimestamp(int(st["result"]["unixtime"]), tz=UTC)
        else:
            srv = datetime.fromtimestamp(int(st["result"]["timeSecond"]), tz=UTC)
        out["server_time"] = {
            "ok": True,
            "latency_ms": round(st_ms),
            "clock_skew_s": round((srv - now).total_seconds(), 2),
        }

    end = datetime.now(UTC)
    for tf in (Timeframe.M1, Timeframe.M5):
        span = timedelta(hours=6 if tf is Timeframe.M5 else 2)
        bars, ms, err = await _timed(provider.fetch_ohlcv(symbol, tf, end - span, end))
        key = f"ohlcv_{tf.value}"
        if err:
            out[key] = {"ok": False, "error": err, "latency_ms": round(ms)}
            continue
        q = check_ohlcv_series(list(bars), instrument=symbol, timeframe=tf, now=datetime.now(UTC))
        future = _future_check([b.close_time for b in bars], datetime.now(UTC))
        last = bars[-1] if bars else None
        out[key] = {
            "ok": bool(bars) and not q.blocks_trading and not future,
            "bars": len(bars),
            "latency_ms": round(ms),
            "last_close_time": last.close_time.isoformat() if last else None,
            "last_close": last.close if last else None,
            "age_s": round((datetime.now(UTC) - last.close_time).total_seconds()) if last else None,
            "quality": {
                "blocks_trading": q.blocks_trading,
                "issues": [i.code.value for i in q.issues],
            },
            "future_timestamps": future,
        }

    quote, qms, qerr = await _timed(provider.fetch_quote(symbol))
    if qerr:
        out["quote"] = {
            "ok": False,
            "status": "NOT_AVAILABLE",
            "error": qerr,
            "latency_ms": round(qms),
        }
    else:
        out["quote"] = {
            "ok": True,
            "latency_ms": round(qms),
            "bid": quote.bid,
            "ask": quote.ask,
            "spread": round(quote.spread, 6),
            "spread_bps": round(quote.spread / quote.mid * 1e4, 3),
            "ts": quote.ts.isoformat(),
            "future": bool(_future_check([quote.ts], datetime.now(UTC))),
        }

    out["provider_health"] = provider.status().health.value
    return out


async def _test_ws(name: str, source: Any, seconds: float, n_target: int) -> dict[str, Any]:
    t0 = time.monotonic()
    bars: list[Any] = []
    err: str | None = None

    async def _drain() -> None:
        async for bar in source.stream():
            bars.append(bar)
            if len(bars) >= 2:
                return

    try:
        # rohe Trades zählen wir über die Aggregatoren; ein confirmed Bar braucht > 1 Minute,
        # daher hier primär: Verbindung steht + Nachrichten kommen an.
        task = asyncio.create_task(_drain())
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline and source.messages_seen < n_target:
            await asyncio.sleep(0.25)
        await source.stop()
        with __import__("contextlib").suppress(asyncio.CancelledError, asyncio.TimeoutError):
            await asyncio.wait_for(task, timeout=2.0)
    except Exception as exc:
        err = f"{type(exc).__name__}: {exc}"

    conn_ms = None
    total = source.messages_seen
    return {
        "exchange": name,
        "connected": total > 0 and err is None,
        "messages_seen": total,
        "confirmed_bars": len(bars),
        "reconnects": source.reconnects,
        "elapsed_s": round(time.monotonic() - t0, 1),
        "error": err,
        "note": "confirmed M1-Bar braucht >60s Trades; hier zählt: Verbindung + Nachrichtenfluss",
        "conn_latency_ms": conn_ms,
    }


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbols", nargs="+", default=["BTCUSDT", "ETHUSDT"])
    ap.add_argument("--ws-seconds", type=float, default=15.0)
    ap.add_argument("--ws-messages", type=int, default=8)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-ws", action="store_true", help="nur REST testen")
    args = ap.parse_args()

    report: dict[str, Any] = {"started_at": datetime.now(UTC).isoformat(), "rest": {}, "ws": {}}

    kr = KrakenDataProvider()
    by = BybitPublicDataProvider()
    try:
        report["rest"]["kraken"] = await _test_rest("kraken", kr, args.symbols[0], _KRAKEN_TIME)
        report["rest"]["bybit"] = await _test_rest("bybit", by, args.symbols[0], _BYBIT_TIME)
    finally:
        await kr.aclose()
        await by.aclose()

    if not args.no_ws:
        report["ws"]["kraken"] = await _test_ws(
            "kraken", KrakenWSSource(args.symbols, Timeframe.M1), args.ws_seconds, args.ws_messages
        )
        report["ws"]["bybit"] = await _test_ws(
            "bybit", BybitWSSource(args.symbols, Timeframe.M1), args.ws_seconds, args.ws_messages
        )

    report["finished_at"] = datetime.now(UTC).isoformat()

    def _verdict(ex: str) -> str:
        r = report["rest"].get(ex, {})
        w = report["ws"].get(ex, {})
        rest_ok = r.get("ohlcv_M5", {}).get("ok") and r.get("server_time", {}).get("ok")
        ws_ok = w.get("connected", False) if not args.no_ws else None
        if rest_ok and (ws_ok or args.no_ws):
            return "CONNECTED"
        if rest_ok:
            return "CONNECTED (REST) / WS DEGRADED"
        return "BLOCKED"

    report["verdict"] = {ex: _verdict(ex) for ex in ("kraken", "bybit")}

    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(json.dumps(report["verdict"], indent=2))
        print("\n--- REST ---")
        for ex, r in report["rest"].items():
            st = r.get("server_time", {})
            m5 = r.get("ohlcv_M5", {})
            m1 = r.get("ohlcv_M1", {})
            qt = r.get("quote", {})
            print(
                f"{ex:8} skew={st.get('clock_skew_s')}s  "
                f"M5={m5.get('bars')}bars/{m5.get('latency_ms')}ms/age{m5.get('age_s')}s "
                f"M1={m1.get('bars')}bars  "
                f"quote bid={qt.get('bid')} ask={qt.get('ask')} spr={qt.get('spread_bps')}bps  "
                f"health={r.get('provider_health')}"
            )
        if not args.no_ws:
            print("\n--- WebSocket ---")
            for ex, w in report["ws"].items():
                print(
                    f"{ex:8} connected={w.get('connected')}  msgs={w.get('messages_seen')}  "
                    f"reconnects={w.get('reconnects')}  {w.get('elapsed_s')}s  err={w.get('error')}"
                )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
