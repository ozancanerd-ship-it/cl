#!/usr/bin/env python
"""cTrader / Pepperstone — READ-ONLY Account-Verknüpfung (OAuth2, Scope ``accounts``).

Führt den OAuth-Flow lokal durch und schreibt die Tokens in ``.env``. **Kein Trading-Scope.**

Voraussetzung: ``CTRADER_CLIENT_ID`` + ``CTRADER_CLIENT_SECRET`` in ``.env`` (von der
App-Registrierung auf https://openapi.ctrader.com).

    python scripts/ctrader_link.py --redirect-uri http://localhost/

Ablauf:
1. Skript zeigt die Autorisierungs-URL (Scope ``accounts`` — nur lesen).
2. Du öffnest sie im Browser, loggst dich mit deiner cTrader-ID ein, wählst dein
   **Pepperstone-Demo-Konto**, bestätigst.
3. Der Browser wird auf ``{redirect_uri}?code=...`` umgeleitet (die Seite lädt nicht — egal).
4. Du fügst die **vollständige umgeleitete URL** (oder nur den ``code``) hier ein.
5. Skript tauscht den Code gegen Access-/Refresh-Token, listet deine Konten,
   und schreibt ``CTRADER_ACCESS_TOKEN`` / ``CTRADER_REFRESH_TOKEN`` / ``CTRADER_ACCOUNT_ID``
   nach ``.env`` (chmod 600). Tokens werden **nicht** auf dem Bildschirm ausgegeben.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from trading_agent.data.providers.ctrader import (
    CTraderClient,
    CTraderError,
    authorize_url,
    exchange_code,
)
from trading_agent.security.secrets import Secret, get_secret

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


def _update_env(updates: dict[str, str]) -> None:
    lines = _ENV_PATH.read_text().splitlines() if _ENV_PATH.exists() else []
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        m = re.match(r"^([A-Z0-9_]+)=", line)
        if m and m.group(1) in updates:
            out.append(f"{m.group(1)}='{updates[m.group(1)]}'")
            seen.add(m.group(1))
        else:
            out.append(line)
    for k, v in updates.items():
        if k not in seen:
            out.append(f"{k}='{v}'")
    _ENV_PATH.write_text("\n".join(out) + "\n")
    _ENV_PATH.chmod(0o600)


def _extract_code(text: str) -> str:
    text = text.strip()
    if text.startswith("http"):
        qs = parse_qs(urlparse(text).query)
        if "code" in qs:
            return qs["code"][0]
    return text


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--redirect-uri",
        default="http://localhost/",
        help="muss exakt mit der in der cTrader-App registrierten Redirect-URI übereinstimmen",
    )
    ap.add_argument("--demo", action="store_true", default=True, help="Demo-Konto (Default)")
    ap.add_argument("--live", dest="demo", action="store_false", help="Live-Konto statt Demo")
    args = ap.parse_args()

    _load_env_file()
    cid = get_secret("CTRADER_CLIENT_ID", allow_keychain=True)
    csec = get_secret("CTRADER_CLIENT_SECRET", allow_keychain=True)
    if not (cid.present and csec.present):
        print(
            "FEHLT: CTRADER_CLIENT_ID / CTRADER_CLIENT_SECRET in .env eintragen (App-Registrierung)."
        )
        return 2

    url = authorize_url(cid.reveal(), args.redirect_uri, scope="accounts")
    print("\n1) Öffne diese URL im Browser und autorisiere dein Pepperstone-(Demo-)Konto:\n")
    print(f"   {url}\n")
    print("2) Der Browser wird umgeleitet auf")
    print(f"   {args.redirect_uri}?code=...   (Seite lädt nicht — normal)\n")
    pasted = input("3) Vollständige umgeleitete URL (oder nur den code) hier einfügen: ").strip()
    code = _extract_code(pasted)
    if not code:
        print("Kein code erkannt.")
        return 2

    print("\n… tausche Code gegen Token …")
    try:
        tokens = await exchange_code(
            client_id=cid.reveal(),
            client_secret=csec.reveal(),
            code=code,
            redirect_uri=args.redirect_uri,
        )
    except CTraderError as exc:
        print(f"FEHLER: {exc}")
        return 1
    print(f"   OK — Token gültig für ~{tokens.expires_in // 3600} h, Refresh-Token erhalten.")

    print("… frage verknüpfte Konten ab …")
    client = CTraderClient(
        client_id=cid,
        client_secret=csec,
        access_token=Secret(tokens.access_token, name="CTRADER_ACCESS_TOKEN"),
        account_id=0,
        demo=args.demo,
    )
    try:
        await client.connect(authenticate_account=False)
        accounts = await client.list_accounts()
    except CTraderError as exc:
        print(f"FEHLER bei der Kontoabfrage: {exc}")
        await client.aclose()
        return 1
    finally:
        await client.aclose()

    if not accounts:
        print("Keine Konten mit diesem Token verknüpft.")
        return 1
    if len(accounts) == 1:
        account_id = accounts[0]
        print(f"   Ein Konto gefunden: ctidTraderAccountId={account_id}")
    else:
        print(f"   Mehrere Konten: {accounts}")
        account_id = int(input("   ctidTraderAccountId auswählen: ").strip())

    _update_env(
        {
            "CTRADER_ACCESS_TOKEN": tokens.access_token,
            "CTRADER_REFRESH_TOKEN": tokens.refresh_token,
            "CTRADER_ACCOUNT_ID": str(account_id),
            "CTRADER_ENV": "demo" if args.demo else "live",
        }
    )
    print("\n✔ .env aktualisiert (CTRADER_ACCESS_TOKEN / _REFRESH_TOKEN / _ACCOUNT_ID / _ENV).")
    print("  Tokens wurden NICHT angezeigt. .env ist chmod 600 + in .gitignore.")
    print("\nNächster Schritt:  python scripts/ctrader_account_test.py")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
