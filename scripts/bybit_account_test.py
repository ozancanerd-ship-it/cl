#!/usr/bin/env python
"""BYBIT ACCOUNT — READ-ONLY Verbindungstest (v5, **Bybit EU** — ``https://api.bybit.eu``).

    python scripts/bybit_account_test.py            # Klartext-Report
    python scripts/bybit_account_test.py --json      # JSON
    python scripts/bybit_account_test.py --show-balances   # Beträge NICHT maskieren

Kein Demo-, kein Testnet-Host. Prüft mit einem **Read-Only** Bybit-EU-API-Key:
  1. ENV-Check — fehlt ein Wert, exakte Namen ausgeben und abbrechen (kein Fake).
  2. Server-Zeit + Clock-Skew.
  3. API-Key-Info (`/v5/user/query-api`) — Rechte des Keys direkt.
  4. Wallet-Balance — Konto-Equity-Summary (Beträge default maskiert).
  5. OpenOrders / Positionen — Anzahl (erwartet 0).
  6. Transaktions-Log — Lesezugriff bestätigt.
  7. SICHERHEITS-ASSERTION: weder Trade- noch Withdraw-Berechtigung gesetzt.

KEIN Order-Pfad. `orders_sent` bleibt 0. Secrets werden nie geloggt/ausgegeben.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from typing import Any

from trading_agent.data.providers.bybit_account import BybitAccountAdapter, BybitAccountError


def _mask(value: float | None, *, show: bool) -> Any:
    return (value if value is not None else 0.0) if show else "***"


async def run(*, as_json: bool, show_balances: bool) -> int:
    adapter = BybitAccountAdapter()  # Default-Host: https://api.bybit.eu
    report: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": "READ-ONLY (keine Order, kein Withdraw)",
        "base_url": adapter.base_url,
        "orders_sent": 0,
    }

    missing = adapter.missing_credentials()
    if missing:
        report["status"] = "BLOCKED"
        report["missing_env"] = missing
        report["hint"] = (
            "Setze die Variablen in .env (chmod 600) oder im macOS-Keychain. "
            "Bybit-Key im Modus 'Read-Only'. KEINE Trade-, KEINE Withdraw-Rechte."
        )
        _emit(report, as_json)
        return 2

    checks: dict[str, Any] = {}
    report["checks"] = checks
    try:
        server, skew = await adapter.server_time()
        checks["server_time"] = {
            "bybit_utc": server.isoformat(),
            "clock_skew_seconds": round(skew, 3),
        }

        info = await adapter.get_api_key_info()
        checks["api_key"] = {
            "read_only_flag": info.read_only,
            "trade_permissions": info.trade_permissions,
            "can_withdraw": info.can_withdraw,
            "ip_allowlist": info.ip_allowlist or "(keine — offen)",
            "expires_at": info.expires_at or "(kein Ablauf)",
            "permissions": info.permissions,
        }

        summary = await adapter.get_wallet_balance()
        checks["account"] = {
            "currency": summary.currency,
            "equity": _mask(summary.equity, show=show_balances),
            "wallet_balance": _mask(summary.balance, show=show_balances),
            "assets_with_balance": {
                k: _mask(v, show=show_balances) for k, v in summary.nonzero_balances.items()
            },
        }

        oo = await adapter.get_open_orders()
        pos = await adapter.get_positions()
        checks["open_orders_count"] = oo["count"]
        checks["open_positions_count"] = pos["count"]

        tx = await adapter.get_transaction_log(limit=3)
        checks["transaction_log_read_ok"] = True
        checks["transaction_log_recent"] = [
            {"type": r.get("type"), "currency": r.get("currency"), "time": r.get("transactionTime")}
            for r in tx
        ]

        proof = await adapter.assert_read_only()
        checks["read_only_assertion"] = {
            "confirmed_no_trade_no_withdraw": proof.confirmed,
            "detail": proof.detail,
            "probed_with": proof.probed_with,
        }

        report["status"] = "CONNECTED" if proof.confirmed else "CONNECTED_BUT_CHECK_PERMISSIONS"
        report["provider_health"] = adapter.status().health.value
    except BybitAccountError as exc:
        report["status"] = "ERROR"
        report["error"] = str(exc)  # der Adapter redigiert Secrets bereits
        report["provider_health"] = adapter.status().health.value
        if "10003" in str(exc):
            report["hint"] = (
                "API key is invalid — Key stammt nicht von api.bybit.eu (falsche Region) "
                "oder Tippfehler beim Kopieren."
            )
        elif "10010" in str(exc):
            report["hint"] = "IP nicht in der Allowlist des Keys — IP-Beschränkung anpassen."
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
    print(f"\n=== BYBIT ACCOUNT — READ-ONLY TEST — {report['generated_at']} ===")
    print(f"Status: {report['status']}")
    if report.get("missing_env"):
        print(f"  Fehlende ENV-Variablen: {', '.join(report['missing_env'])}")
        print(f"  {report['hint']}")
        return
    if report.get("error"):
        print(f"  Host: {report['base_url']}")
        print(f"  Fehler: {report['error']}")
        if report.get("hint"):
            print(f"  Hinweis: {report['hint']}")
        return
    c = report["checks"]
    st = c["server_time"]
    print(f"  Server-Zeit: {st['bybit_utc']}  (Clock-Skew {st['clock_skew_seconds']}s)")
    k = c["api_key"]
    print(
        f"  API-Key: read_only_flag={k['read_only_flag']}  trade_permissions={k['trade_permissions']}  "
        f"can_withdraw={k['can_withdraw']}"
    )
    print(f"           IP-Allowlist={k['ip_allowlist']}  expires={k['expires_at']}")
    a = c["account"]
    print(f"  Konto ({a['currency']}): equity={a['equity']}  wallet_balance={a['wallet_balance']}")
    print(f"  Assets mit Guthaben: {a['assets_with_balance']}")
    print(
        f"  Offene Orders: {c['open_orders_count']}   Offene Positionen: {c['open_positions_count']}"
    )
    print(
        f"  Transaktions-Log lesbar: {c['transaction_log_read_ok']}  "
        f"(letzte {len(c['transaction_log_recent'])})"
    )
    ro = c["read_only_assertion"]
    print(
        f"  READ-ONLY-Assertion: no_trade_no_withdraw={ro['confirmed_no_trade_no_withdraw']} — "
        f"{ro['detail']}"
    )
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
