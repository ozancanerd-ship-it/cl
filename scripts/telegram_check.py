#!/usr/bin/env python3
"""Sagt, ob der Telegram-Kanal steht — und schickt auf Wunsch eine Testnachricht.

    python3 scripts/telegram_check.py          # nur pruefen
    python3 scripts/telegram_check.py --ping   # zusaetzlich eine Testnachricht

Warum es das braucht: „der Bot funktioniert nicht" hat drei moegliche Ursachen, und
ohne Pruefung raet man.

1. Die Schluessel fehlen im Repo (dann steht hier: nicht gesetzt).
2. Der Token stimmt nicht (Telegram antwortet 401).
3. Die Chat-ID stimmt nicht, oder du hast dem Bot noch nie geschrieben — Telegram
   verbietet Bots, unaufgefordert zu schreiben (403, "bot can't initiate conversation").

Der Schritt verraet **nie** die Schluessel selbst, nur ihre Laenge und was Telegram
zu ihnen sagt. Und er beendet sich immer mit 0: eine Diagnose darf den Lauf nicht
abbrechen.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ping", action="store_true", help="Testnachricht schicken")
    args = ap.parse_args()

    from trading_agent.security.secrets import get_secret

    token = get_secret("TELEGRAM_BOT_TOKEN", allow_keychain=True)
    chat = get_secret("TELEGRAM_CHAT_ID", allow_keychain=True)

    print("TELEGRAM-KANAL")
    print(f"  TELEGRAM_BOT_TOKEN  {'gesetzt' if token.present else 'FEHLT'}", end="")
    if token.present:
        roh = token.reveal()
        print(f"  ({len(roh)} Zeichen, endet auf …{roh[-4:]})")
    else:
        print()
    print(f"  TELEGRAM_CHAT_ID    {'gesetzt' if chat.present else 'FEHLT'}", end="")
    if chat.present:
        print(f"  ({chat.reveal()})")
    else:
        print()

    if not (token.present and chat.present):
        print("\n::warning::Telegram-Schluessel fehlen — es kann nichts verschickt werden.")
        print("  Settings -> Secrets and variables -> Actions -> New repository secret")
        return 0

    import httpx

    with httpx.Client(timeout=15.0) as c:
        try:
            r = c.get(f"https://api.telegram.org/bot{token.reveal()}/getMe")
            d = r.json()
            if r.status_code == 200 and d.get("ok"):
                bot = d["result"]
                print(f"\n  Bot erreichbar: @{bot.get('username')} ({bot.get('first_name')})")
            else:
                print(f"\n::error::Telegram lehnt den Token ab: HTTP {r.status_code} {d}")
                return 0
        except Exception as exc:
            print(f"\n::error::Telegram nicht erreichbar: {type(exc).__name__}: {exc}")
            return 0

        if not args.ping:
            print("  (mit --ping wird zusaetzlich eine Testnachricht geschickt)")
            return 0

        text = (
            "Testnachricht vom Trading Desk\n"
            f"{datetime.now(UTC):%d.%m.%Y %H:%M} UTC\n\n"
            "Wenn du das liest, steht der Kanal. Ab jetzt kommen hier die echten "
            "Meldungen an: Einstieg erreicht, Ziel erreicht, Stop beruehrt."
        )
        try:
            r = c.post(
                f"https://api.telegram.org/bot{token.reveal()}/sendMessage",
                json={"chat_id": chat.reveal(), "text": text},
            )
            d = r.json()
        except Exception as exc:
            print(f"::error::Senden fehlgeschlagen: {type(exc).__name__}: {exc}")
            return 0
        if r.status_code == 200 and d.get("ok"):
            print("  Testnachricht verschickt.")
            return 0
        beschreibung = str(d.get("description", ""))
        print(
            f"::error::Telegram nimmt die Nachricht nicht an: HTTP {r.status_code} {beschreibung}"
        )
        if "chat not found" in beschreibung.lower():
            print(
                "  Die Chat-ID stimmt nicht. So findest du die richtige: dem Bot in "
                "Telegram irgendetwas schreiben, dann "
                "https://api.telegram.org/bot<TOKEN>/getUpdates aufrufen und die Zahl "
                "unter result[0].message.chat.id nehmen."
            )
        elif "bot can't initiate" in beschreibung.lower() or "blocked" in beschreibung.lower():
            print(
                "  Telegram verbietet Bots, ein Gespraech zu beginnen. Schreib dem Bot "
                "einmal /start, danach darf er dir schreiben."
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
