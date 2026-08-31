#!/usr/bin/env python
"""GOLD / FX — READ-ONLY Live-Datentest.

    python scripts/gold_fx_readonly_test.py [--json]

Zeigt für XAUUSD / EURUSD / GBPUSD / USDJPY den aktuellen Stand **ohne jede Order**:
Bid/Ask, Spread, Zeitstempel, Datenalter, Provider-Health, REST/WS-Status.

Quellen (Stand jetzt, ohne Broker-Credentials):
* **XAUUSD** → Bybit ``XAUTUSDT`` (tokenisiertes Gold) — echtes Bid/Ask + WebSocket + REST.
  Basis-Spread zu XAU/USD beachten; am Wochenende driftet der Crypto-Proxy vom (dann
  geschlossenen) Spot-Goldmarkt weg.
* **EUR/GBP/USDJPY** → Yahoo Finance — **nur indikativ / ~15 min verzögert / kein Bid/Ask**.
  Produktiv: cTrader Open API (``status()`` unten) oder OANDA v20.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime, timedelta

from trading_agent.core.enums import Timeframe
from trading_agent.data.providers.bybit_public import BybitPublicDataProvider
from trading_agent.data.providers.ctrader import CTraderAdapter
from trading_agent.data.providers.exchange_ws import BybitWSSource
from trading_agent.data.providers.yahoo_finance import YahooFinanceProvider

FX = ["EURUSD", "GBPUSD", "USDJPY"]


async def _bybit_gold() -> dict:
    p = BybitPublicDataProvider()
    out: dict = {"symbol": "XAUUSD", "provider": "bybit:XAUTUSDT", "kind": "tokenized_gold"}
    try:
        q = await p.fetch_quote("XAUTUSDT")
        now = datetime.now(UTC)
        m5 = await p.fetch_ohlcv("XAUTUSDT", Timeframe.M5, now - timedelta(hours=8), now)
        age = (datetime.now(UTC) - q.ts).total_seconds()
        out.update(
            live_data=True,
            bid=q.bid,
            ask=q.ask,
            spread=round(q.ask - q.bid, 4),
            ts=q.ts.isoformat(),
            data_age_s=round(age, 1),
            rest_ohlcv_m5_bars=len(m5),
            last_m5_close=m5[-1].close if m5 else None,
            health=p.status().health.value,
        )
    except Exception as exc:
        out.update(
            live_data=False, error=f"{type(exc).__name__}: {exc}", health=p.status().health.value
        )
    finally:
        await p.aclose()

    # WebSocket-Erreichbarkeit (nur Connect + erste Bar, dann schließen)
    ws = BybitWSSource(["XAUTUSDT"], Timeframe.M5, max_reconnects=0)
    out["ws"] = "not_tested"
    try:
        agen = ws.stream()
        bar = await asyncio.wait_for(agen.__anext__(), timeout=90)
        out["ws"] = f"connected (bar {bar.close_time.isoformat()})"
        await agen.aclose()
    except TimeoutError:
        out["ws"] = "connected_no_bar_within_90s"
    except Exception as exc:
        out["ws"] = f"error: {type(exc).__name__}: {exc}"
    return out


async def _yahoo_fx() -> list[dict]:
    p = YahooFinanceProvider()
    rows: list[dict] = []
    for sym in FX:
        row: dict = {"symbol": sym, "provider": "yahoo_indicative", "kind": "indicative_delayed"}
        try:
            ip = await p.latest_indicative(sym)
            now = datetime.now(UTC)
            m15 = await p.fetch_ohlcv(sym, Timeframe.M15, now - timedelta(days=4), now)
            age = (datetime.now(UTC) - ip.ts).total_seconds()
            row.update(
                live_data=True,
                indicative_price=ip.price,
                bid=None,
                ask=None,
                spread=None,
                note="kein Bid/Ask verfügbar (indikativ)",
                ts=ip.ts.isoformat(),
                data_age_s=round(age, 1),
                rest_ohlcv_m15_bars=len(m15),
                health=p.status().health.value,
            )
        except Exception as exc:
            row.update(live_data=False, error=f"{type(exc).__name__}: {exc}")
        rows.append(row)
    await p.aclose()
    return rows


def _ctrader_status() -> dict:
    a = CTraderAdapter()
    st = a.status()
    return {
        "provider": "ctrader_open_api",
        "health": st.health.value,
        "detail": st.detail,
        "note": "produktive FX/XAUUSD-Live-Quelle — aktiv, sobald CTRADER_* ENV gesetzt",
    }


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    gold, fx = await asyncio.gather(_bybit_gold(), _yahoo_fx())
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": "READ-ONLY (keine Order, keine Keys)",
        "gold": gold,
        "fx": fx,
        "ctrader": _ctrader_status(),
    }

    if args.json:
        print(json.dumps(report, indent=2, default=str))
        return 0

    print(f"\n=== GOLD / FX READ-ONLY LIVE TEST — {report['generated_at']} ===\n")
    g = gold
    print(f"XAUUSD  via {g['provider']}")
    if g.get("live_data"):
        print(
            f"  bid={g['bid']} ask={g['ask']} spread={g['spread']}  ts={g['ts']}  "
            f"age={g['data_age_s']}s  M5-bars={g['rest_ohlcv_m5_bars']}  health={g['health']}"
        )
        print(f"  WS: {g['ws']}")
    else:
        print(f"  KEINE Live-Daten: {g.get('error')}")
    print()
    for r in fx:
        if r.get("live_data"):
            print(
                f"{r['symbol']}  via {r['provider']}  (indikativ, verzögert, KEIN Bid/Ask)\n"
                f"  price~{r['indicative_price']}  ts={r['ts']}  age={r['data_age_s']}s  "
                f"M15-bars={r['rest_ohlcv_m15_bars']}  health={r['health']}"
            )
        else:
            print(f"{r['symbol']}  KEINE Live-Daten: {r.get('error')}")
    c = report["ctrader"]
    print(f"\ncTrader Open API: {c['health']} — {c['detail']}")
    print(f"  {c['note']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
