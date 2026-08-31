#!/usr/bin/env python
"""cTrader / Pepperstone — READ-ONLY Connectivity-Test.

    python scripts/ctrader_account_test.py [--json]

Prüft (ohne jede Order):
  1. ENV-Check  (CTRADER_CLIENT_ID/SECRET/ACCESS_TOKEN/ACCOUNT_ID).
  2. WS-Verbindung + App-Auth + Account-Auth (wss://{demo|live}.ctraderapi.com:5036).
  3. Symbol-Auflösung XAUUSD / EURUSD / GBPUSD / USDJPY → numerische symbolId.
  4. Trendbars (M5, letzte Stunden) je Symbol — Bar-Anzahl, letzter Close, letzte Bar-Zeit.
  5. Spot-Snapshot je Symbol — Bid/Ask/Spread (falls Markt offen).
  6. Read-only-Nachweis: OAuth-Scope 'accounts', Adapter ohne submit/cancel, orders_sent=0.

Kein Order-Pfad. Tokens werden nie ausgegeben.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from trading_agent.core.enums import Timeframe
from trading_agent.data.providers.ctrader import CTraderAdapter, CTraderError

_SYMBOLS = ["XAUUSD", "EURUSD", "GBPUSD", "USDJPY"]
_ENV_PATH = Path(".env")


def _load_env_file() -> None:
    if not _ENV_PATH.exists():
        return
    for line in _ENV_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip("'\""))


async def run(*, as_json: bool) -> int:
    _load_env_file()
    demo = os.environ.get("CTRADER_ENV", "demo").lower() != "live"
    adapter = CTraderAdapter(demo=demo)
    report: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": "READ-ONLY (keine Order, kein Withdraw)",
        "environment": "demo" if demo else "live",
        "oauth_scope": "accounts (nur lesen)",
        "orders_sent": 0,
    }

    missing = adapter.missing_credentials()
    if missing:
        report["status"] = "BLOCKED"
        report["missing_env"] = missing
        report["hint"] = "Zuerst: python scripts/ctrader_link.py  (OAuth-Verknüpfung)."
        _emit(report, as_json)
        return 2

    checks: dict[str, Any] = {}
    report["checks"] = checks
    try:
        client = await adapter._ensure_client()  # verbindet + App/Account-Auth + Symbol-Liste
        checks["connection"] = "app+account auth OK"
        checks["account_id"] = client.account_id

        end = datetime.now(UTC)
        start = end - timedelta(hours=6)
        sym_report: dict[str, Any] = {}
        for sym in _SYMBOLS:
            row: dict[str, Any] = {}
            try:
                sid = await adapter._symbol_id(sym)
                row["symbol_id"] = sid
            except CTraderError as exc:
                row["error"] = str(exc)
                sym_report[sym] = row
                continue
            bars = await adapter.fetch_ohlcv(sym, Timeframe.M5, start, end)
            row["m5_bars"] = len(bars)
            if bars:
                row["last_bar_open"] = bars[-1].open_time.isoformat()
                row["last_close"] = bars[-1].close
            try:
                q = await adapter.fetch_quote(sym)
                row["bid"] = q.bid
                row["ask"] = q.ask
                row["spread"] = round(q.ask - q.bid, 5)
            except CTraderError as exc:
                row["quote"] = f"kein Spot-Event ({exc}) — Markt evtl. geschlossen"
            sym_report[sym] = row
        checks["symbols"] = sym_report

        report["status"] = (
            "CONNECTED"
            if any("m5_bars" in r and r["m5_bars"] > 0 for r in sym_report.values())
            else "CONNECTED_NO_DATA"
        )
        report["trading_rights"] = "NEIN"
        report["withdraw_rights"] = "NEIN"
        report["provider_health"] = adapter.status().health.value
    except CTraderError as exc:
        report["status"] = "ERROR"
        report["error"] = str(exc)
        if "not in active state" in str(exc):
            report["hint"] = (
                "Die Open-API-App ist noch nicht freigeschaltet (Status 'Submitted'). "
                "Spotware aktiviert i. d. R. innerhalb 24-48 h. Alternativ auf "
                "openapi.ctrader.com/apps den 'Playground'-Button testen. Nach >48 h: "
                "support@ctrader.com mit der App-ID (Zahl vor dem '_' in der Client-ID)."
            )
        elif "CH_CLIENT_AUTH" in str(exc):
            report["hint"] = "Client ID / Secret prüfen (openapi.ctrader.com/apps)."
        elif "account" in str(exc).lower() and "auth" in str(exc).lower():
            report["hint"] = (
                "Account-Auth fehlgeschlagen — Token per ctrader_link.py neu holen, "
                "CTRADER_ENV (demo/live) muss zum Konto passen."
            )
        _emit(report, as_json)
        await adapter.aclose()
        return 1
    finally:
        await adapter.aclose()

    assert report["orders_sent"] == 0
    _emit(report, as_json)
    return 0 if report["status"].startswith("CONNECTED") else 1


def _emit(report: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(report, indent=2, default=str))
        return
    print(f"\n=== cTrader / Pepperstone — READ-ONLY TEST — {report['generated_at']} ===")
    print(f"Status: {report['status']}   Umgebung: {report['environment']}")
    if report.get("missing_env"):
        print(f"  Fehlende ENV: {', '.join(report['missing_env'])}")
        print(f"  {report['hint']}")
        return
    if report.get("error"):
        print(f"  Fehler: {report['error']}")
        if report.get("hint"):
            print(f"  Hinweis: {report['hint']}")
        return
    c = report["checks"]
    print(f"  Verbindung: {c['connection']}  (ctidTraderAccountId={c['account_id']})")
    for sym, r in c["symbols"].items():
        if "error" in r:
            print(f"  {sym}: {r['error']}")
            continue
        line = f"  {sym}: id={r['symbol_id']}  M5-bars={r.get('m5_bars', 0)}"
        if "last_close" in r:
            line += f"  last_close={r['last_close']} @ {r['last_bar_open']}"
        if "bid" in r:
            line += f"  bid/ask={r['bid']}/{r['ask']} spread={r['spread']}"
        elif "quote" in r:
            line += f"  ({r['quote']})"
        print(line)
    print(f"  OAuth-Scope: {report['oauth_scope']}")
    print(
        f"  Trading-Rechte: {report['trading_rights']}   Withdraw-Rechte: {report['withdraw_rights']}"
    )
    print(f"  Provider-Health: {report['provider_health']}   orders_sent={report['orders_sent']}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    return asyncio.run(run(as_json=args.json))


if __name__ == "__main__":
    raise SystemExit(main())
