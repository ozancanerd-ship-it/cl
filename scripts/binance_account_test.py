#!/usr/bin/env python
"""BINANCE ACCOUNT — READ-ONLY Verbindungstest.

    python scripts/binance_account_test.py [--json] [--show-balances]

Prüft mit einem **read-only** Binance-API-Key (nur „Enable Reading"):
  1. ENV-Check  (BINANCE_API_KEY / BINANCE_API_SECRET).
  2. Server-Zeit + Clock-Skew.
  3. API-Key-Berechtigungen (`/sapi/v1/account/apiRestrictions`).
  4. Spot-Guthaben (Beträge default maskiert).
  5. Offene Orders — Anzahl (erwartet 0).
  6. SICHERHEITS-ASSERTION: weder Withdraw noch Transfer aktiv.

KEIN Order-Pfad. `orders_sent` bleibt 0. Secrets werden nie geloggt/ausgegeben.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from trading_agent.data.providers.binance_account import BinanceAccountAdapter, BinanceAccountError

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


def _mask(v: float, *, show: bool) -> Any:
    return v if show else "***"


async def run(*, as_json: bool, show_balances: bool) -> int:
    _load_env_file()
    adapter = BinanceAccountAdapter()
    report: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": "READ-ONLY (keine Order, kein Withdraw, kein Transfer)",
        "orders_sent": 0,
    }
    missing = adapter.missing_credentials()
    if missing:
        report["status"] = "BLOCKED"
        report["missing_env"] = missing
        report["hint"] = (
            "Setze die Variablen in .env (chmod 600) oder im macOS-Keychain. "
            "Binance-Key: nur 'Enable Reading'. KEINE Trade-/Withdraw-/Transfer-Rechte."
        )
        _emit(report, as_json)
        return 2

    checks: dict[str, Any] = {}
    report["checks"] = checks
    try:
        server, skew = await adapter.server_time()
        checks["server_time"] = {"utc": server.isoformat(), "clock_skew_seconds": round(skew, 3)}

        perms = await adapter.get_api_permissions()
        checks["api_key"] = {
            "can_read": perms.can_read,
            "can_withdraw": perms.can_withdraw,
            "can_internal_transfer": perms.can_internal_transfer,
            "can_universal_transfer": perms.can_universal_transfer,
            "can_spot_margin_trade": perms.can_spot_margin_trade,
            "can_futures_trade": perms.can_futures_trade,
            "ip_restricted": perms.ip_restricted,
        }

        acct = await adapter.get_spot_balances()
        checks["spot_account"] = {
            "account_type": acct.account_type,
            "can_trade_flag": acct.can_trade,
            "assets_with_balance": {
                k: _mask(v, show=show_balances) for k, v in acct.nonzero_balances.items()
            },
        }

        oo = await adapter.get_open_orders()
        checks["open_orders_count"] = oo["count"]

        proof = await adapter.assert_read_only()
        checks["read_only_assertion"] = {
            "confirmed_no_withdraw_no_transfer": proof.confirmed,
            "detail": proof.detail,
        }
        report["status"] = "CONNECTED" if proof.confirmed else "CONNECTED_CHECK_PERMISSIONS"
        report["trading_rights"] = (
            "NEIN" if not (perms.can_spot_margin_trade or perms.can_futures_trade) else "AKTIV"
        )
        report["withdraw_rights"] = "NEIN"
        report["provider_health"] = adapter.status().health.value
    except BinanceAccountError as exc:
        report["status"] = "ERROR"
        report["error"] = str(exc)  # Adapter redigiert Secrets bereits
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
    print(f"\n=== BINANCE ACCOUNT — READ-ONLY TEST — {report['generated_at']} ===")
    print(f"Status: {report['status']}")
    if report.get("missing_env"):
        print(f"  Fehlende ENV: {', '.join(report['missing_env'])}")
        print(f"  {report['hint']}")
        return
    if report.get("error"):
        print(f"  Fehler: {report['error']}")
        return
    c = report["checks"]
    st = c["server_time"]
    print(f"  Server-Zeit: {st['utc']}  (skew {st['clock_skew_seconds']}s)")
    k = c["api_key"]
    print(
        f"  API-Key: read={k['can_read']}  withdraw={k['can_withdraw']}  "
        f"internal_transfer={k['can_internal_transfer']}  universal_transfer={k['can_universal_transfer']}"
    )
    print(
        f"           spot/margin_trade={k['can_spot_margin_trade']}  futures_trade={k['can_futures_trade']}  "
        f"ip_restricted={k['ip_restricted']}"
    )
    a = c["spot_account"]
    print(f"  Spot-Konto ({a['account_type']}): {a['assets_with_balance']}")
    print(f"  Offene Orders: {c['open_orders_count']}")
    ro = c["read_only_assertion"]
    print(f"  READ-ONLY-Assertion: {ro['confirmed_no_withdraw_no_transfer']} — {ro['detail']}")
    print(
        f"  Trading-Rechte: {report['trading_rights']}   Withdraw-Rechte: {report['withdraw_rights']}   "
        f"orders_sent={report['orders_sent']}"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--show-balances", action="store_true")
    args = ap.parse_args()
    return asyncio.run(run(as_json=args.json, show_balances=args.show_balances))


if __name__ == "__main__":
    raise SystemExit(main())
