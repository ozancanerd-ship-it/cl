#!/usr/bin/env python
"""KRAKEN ACCOUNT — READ-ONLY Verbindungstest.

    python scripts/kraken_account_test.py           # Klartext-Report
    python scripts/kraken_account_test.py --json     # JSON
    python scripts/kraken_account_test.py --show-balances   # Beträge NICHT maskieren

Prüft mit einem **read-only** Kraken-API-Key (nur Query-Rechte):
  1. ENV-Check — fehlt ein Wert, exakte Namen ausgeben und abbrechen (kein Fake).
  2. Server-Zeit + Clock-Skew.
  3. Balance / TradeBalance — Konto-Equity-Summary (Beträge default maskiert).
  4. OpenOrders / OpenPositions — Anzahl (erwartet 0).
  5. Ledgers — Lesezugriff bestätigt.
  6. SICHERHEITS-ASSERTION: AddOrder(validate=true) → erwartet 'Permission denied'
     ⇒ beweist, dass der Key NICHT handeln kann. (validate=true platziert nie eine Order.)

KEIN Order-Pfad. `orders_sent` bleibt 0. Secrets werden nie geloggt/ausgegeben.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from typing import Any

from trading_agent.data.providers.kraken_account import (
    KrakenAccountAdapter,
    KrakenAccountError,
)


def _mask(value: float, *, show: bool) -> Any:
    return value if show else "***"


async def run(*, as_json: bool, show_balances: bool) -> int:
    adapter = KrakenAccountAdapter()
    report: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": "READ-ONLY (keine Order, kein Withdraw)",
        "orders_sent": 0,
    }

    missing = adapter.missing_credentials()
    if missing:
        report["status"] = "BLOCKED"
        report["missing_env"] = missing
        report["hint"] = (
            "Setze die Variablen in .env (chmod 600) oder im macOS-Keychain "
            "(security add-generic-password -s trading-agent -a <NAME> -w). "
            "Key-Rechte: NUR Query (Funds/Orders/Ledger). KEINE Order-, KEINE Withdraw-Rechte."
        )
        _emit(report, as_json)
        return 2

    checks: dict[str, Any] = {}
    report["checks"] = checks
    try:
        server, skew = await adapter.server_time()
        checks["server_time"] = {
            "kraken_utc": server.isoformat(),
            "clock_skew_seconds": round(skew, 3),
        }

        summary = await adapter.get_trade_balance()
        checks["account"] = {
            "currency": summary.currency,
            "equity": _mask(summary.equity or 0.0, show=show_balances),
            "balance": _mask(summary.balance or 0.0, show=show_balances),
            "free_margin": _mask(summary.free_margin or 0.0, show=show_balances),
            "margin_level_pct": summary.margin_level_pct,
            "assets_with_balance": (
                {k: _mask(v, show=show_balances) for k, v in summary.nonzero_balances.items()}
            ),
        }

        oo = await adapter.get_open_orders()
        op = await adapter.get_open_positions()
        checks["open_orders_count"] = oo["count"]
        checks["open_positions_count"] = op["count"]

        ledgers = await adapter.get_ledgers(limit=3)
        checks["ledger_read_ok"] = True
        checks["ledger_recent"] = [
            {"type": r.get("type"), "asset": r.get("asset"), "time": r.get("time")} for r in ledgers
        ]

        proof = await adapter.assert_read_only()
        checks["read_only_assertion"] = {
            "confirmed_cannot_trade": proof.confirmed,
            "detail": proof.detail,
            "probed_with": proof.probed_with,
        }

        report["status"] = "CONNECTED" if proof.confirmed else "CONNECTED_BUT_CHECK_PERMISSIONS"
        report["provider_health"] = adapter.status().health.value
    except KrakenAccountError as exc:
        report["status"] = "ERROR"
        report["error"] = str(exc)  # der Adapter redigiert Secrets bereits
        report["provider_health"] = adapter.status().health.value
        _emit(report, as_json)
        await adapter.aclose()
        return 1
    finally:
        await adapter.aclose()

    assert report["orders_sent"] == 0
    _emit(report, as_json)
    return 0 if report["status"] == "CONNECTED" else 1


def _emit(report: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(report, indent=2, default=str))
        return
    print(f"\n=== KRAKEN ACCOUNT — READ-ONLY TEST — {report['generated_at']} ===")
    print(f"Status: {report['status']}")
    if report.get("missing_env"):
        print(f"  Fehlende ENV-Variablen: {', '.join(report['missing_env'])}")
        print(f"  {report['hint']}")
        return
    if report.get("error"):
        print(f"  Fehler: {report['error']}")
        return
    c = report["checks"]
    st = c["server_time"]
    print(f"  Server-Zeit: {st['kraken_utc']}  (Clock-Skew {st['clock_skew_seconds']}s)")
    a = c["account"]
    print(
        f"  Konto ({a['currency']}): equity={a['equity']} balance={a['balance']} "
        f"free_margin={a['free_margin']} margin_level%={a['margin_level_pct']}"
    )
    print(f"  Assets mit Guthaben: {a['assets_with_balance']}")
    print(
        f"  Offene Orders: {c['open_orders_count']}   Offene Positionen: {c['open_positions_count']}"
    )
    print(f"  Ledger-Lesezugriff: {c['ledger_read_ok']}  (letzte {len(c['ledger_recent'])})")
    ro = c["read_only_assertion"]
    print(f"  READ-ONLY-Assertion: cannot_trade={ro['confirmed_cannot_trade']} — {ro['detail']}")
    print(f"  Provider-Health: {report['provider_health']}   orders_sent={report['orders_sent']}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true")
    ap.add_argument(
        "--show-balances", action="store_true", help="Beträge im Klartext statt maskiert"
    )
    args = ap.parse_args()
    return asyncio.run(run(as_json=args.json, show_balances=args.show_balances))


if __name__ == "__main__":
    raise SystemExit(main())
