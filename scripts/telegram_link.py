#!/usr/bin/env python3
"""Telegram-Chat-ID holen und in ``.env`` eintragen.

Der Bot-Token allein reicht nicht: Telegram gibt die Chat-ID erst heraus, NACHDEM der
Nutzer dem Bot einmal geschrieben hat. Das ist Absicht — so kann kein Bot ungefragt
Nachrichten an Fremde schicken.

Ablauf:

1. In Telegram den Bot suchen, Chat oeffnen, ``/start`` druecken
2. ``python3 scripts/telegram_link.py``

Das Skript liest die ID aus ``getUpdates``, schreibt sie als ``TELEGRAM_CHAT_ID`` in
``.env`` (Dateirechte bleiben 0600) und schickt zur Bestaetigung eine Testnachricht.

    python3 scripts/telegram_link.py            # verbinden
    python3 scripts/telegram_link.py --check    # nur pruefen, nichts schreiben
    python3 scripts/telegram_link.py --test     # Testnachricht an bestehende ID
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.request
from pathlib import Path

ENV = ".env"


def _read_env(path: str) -> dict[str, str]:
    out: dict[str, str] = {}
    p = Path(path)
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _set_env(path: str, key: str, value: str) -> None:
    """Schluessel setzen oder ersetzen, ohne den Rest der Datei anzufassen."""
    p = Path(path)
    lines = p.read_text(encoding="utf-8").splitlines() if p.exists() else []
    replaced = False
    for i, line in enumerate(lines):
        if line.strip().startswith(f"{key}="):
            lines[i] = f"{key}={value}"
            replaced = True
            break
    if not replaced:
        lines.append(f"{key}={value}")
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(p, 0o600)


def _api(token: str, method: str, payload: dict | None = None) -> dict:
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = json.dumps(payload).encode() if payload else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--env", default=ENV)
    ap.add_argument("--check", action="store_true", help="nur anzeigen, nichts schreiben")
    ap.add_argument("--test", action="store_true", help="Testnachricht an die bekannte ID")
    args = ap.parse_args()

    env = _read_env(args.env)
    token = env.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        print("TELEGRAM_BOT_TOKEN fehlt in .env — erst den Token von @BotFather eintragen.")
        return 1

    me = _api(token, "getMe")
    if not me.get("ok"):
        print(f"Token wird abgelehnt: {me}")
        return 1
    bot = me["result"]
    print(f"Bot:  @{bot['username']}  ({bot.get('first_name', '')})")

    known = env.get("TELEGRAM_CHAT_ID", "")

    if args.test:
        if not known:
            print("Noch keine TELEGRAM_CHAT_ID in .env.")
            return 1
        _api(token, "sendMessage", {"chat_id": known, "text": "Testnachricht vom Trading-Agent."})
        print(f"Testnachricht an {known} geschickt.")
        return 0

    upd = _api(token, "getUpdates")
    chats: dict[str, str] = {}
    for u in upd.get("result", []):
        msg = u.get("message") or u.get("edited_message") or u.get("channel_post") or {}
        chat = msg.get("chat") or {}
        if chat.get("id") is not None:
            name = chat.get("first_name") or chat.get("title") or chat.get("username") or "?"
            chats[str(chat["id"])] = name

    if not chats:
        print()
        print("Noch keine Nachricht angekommen.")
        print(f"  1. In Telegram nach  @{bot['username']}  suchen")
        print("  2. Chat oeffnen, START druecken (oder irgendwas schreiben)")
        print("  3. dieses Skript nochmal starten")
        return 2

    for cid, name in chats.items():
        mark = "  <- schon eingetragen" if cid == known else ""
        print(f"Chat: {cid}  ({name}){mark}")

    if args.check:
        return 0

    chat_id = next(iter(chats))
    if len(chats) > 1:
        print(f"\nMehrere Chats gefunden — nehme den ersten: {chat_id}")

    _set_env(args.env, "TELEGRAM_CHAT_ID", chat_id)
    print(f"\nTELEGRAM_CHAT_ID={chat_id} in {args.env} eingetragen (Rechte 0600).")

    _api(
        token,
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": (
                "Verbunden.\n\n"
                "Ab jetzt bekommst du hier den Tagesplan: was kaufen, was verkaufen, "
                "wie viel Euro. Nur wenn sich etwas aendert — kein taegliches Rauschen.\n\n"
                "Es wird nichts automatisch gehandelt. Du entscheidest jede Order selbst."
            ),
        },
    )
    print("Bestaetigung ins Handy geschickt. Schau nach.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
