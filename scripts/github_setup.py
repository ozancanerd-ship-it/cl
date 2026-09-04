#!/usr/bin/env python3
"""Richtet GitHub in einem Rutsch ein: Repo, Push, Secrets, Pages, erster Lauf.

Warum als Skript und nicht von Hand: es sind sechs Schritte, die aufeinander aufbauen,
und jeder einzelne kann still danebengehen (Repo existiert schon, Secret falsch
verschluesselt, Pages-Quelle auf "Branch" statt "Actions"). Ein Skript macht sie in der
richtigen Reihenfolge, prueft jeden Schritt und sagt am Ende die fertige Adresse.

Der Token wird ueber die Umgebung uebergeben und **nie ausgegeben oder geloggt**:

    GITHUB_TOKEN=github_pat_... python3 scripts/github_setup.py --user OZANS_NAME

    --repo NAME       Repository-Name (Standard: AI-Trading-Agent)
    --private         privates Repo (dann faellt GitHub Pages im Gratis-Tarif weg)
    --dry-run         nur zeigen, was passieren wuerde

Braucht ``pynacl`` fuer die Secret-Verschluesselung (GitHub verlangt libsodium-Sealed-Box).
Fehlt es, werden die Secrets uebersprungen und der Rest laeuft trotzdem durch — der
taegliche Job baut dann die Seite, verschickt aber kein Telegram.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from base64 import b64encode
from pathlib import Path

API = "https://api.github.com"


def _api(
    token: str, method: str, path: str, body: dict | None = None
) -> tuple[int, dict | list | None]:
    req = urllib.request.Request(
        API + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "trading-agent-setup",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw.strip() else None)
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {"message": raw[:300]}


def _sh(*args: str, cwd: Path | None = None) -> tuple[int, str]:
    p = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    return p.returncode, (p.stdout + p.stderr).strip()


def _env_value(key: str, env_file: Path) -> str | None:
    if os.environ.get(key):
        return os.environ[key]
    if not env_file.exists():
        return None
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def _put_secret(token: str, full: str, name: str, value: str) -> str:
    """Ein Actions-Secret setzen. GitHub verlangt eine libsodium-Sealed-Box."""
    try:
        from nacl import encoding, public
    except ImportError:
        return "uebersprungen (pynacl fehlt)"

    code, key = _api(token, "GET", f"/repos/{full}/actions/secrets/public-key")
    if code != 200 or not isinstance(key, dict):
        return f"Schluessel nicht lesbar (HTTP {code})"
    sealed = public.SealedBox(
        public.PublicKey(key["key"].encode(), encoding.Base64Encoder())
    ).encrypt(value.encode())
    code, _ = _api(
        token,
        "PUT",
        f"/repos/{full}/actions/secrets/{name}",
        {"encrypted_value": b64encode(sealed).decode(), "key_id": key["key_id"]},
    )
    return "gesetzt" if code in (201, 204) else f"fehlgeschlagen (HTTP {code})"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--user", required=True, help="GitHub-Benutzername")
    ap.add_argument("--repo", default="AI-Trading-Agent")
    ap.add_argument("--private", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    repo_dir = Path(__file__).resolve().parents[1]
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        print("GITHUB_TOKEN fehlt. Aufruf: GITHUB_TOKEN=... python3 scripts/github_setup.py ...")
        return 1

    full = f"{args.user}/{args.repo}"
    print(f"Ziel: github.com/{full}\n")

    # 1 ── Token pruefen
    code, me = _api(token, "GET", "/user")
    if code != 200 or not isinstance(me, dict):
        print(f"  Token wird abgelehnt (HTTP {code}). Ist er abgelaufen oder falsch kopiert?")
        return 1
    print(f"  1/6  Token gueltig — angemeldet als {me.get('login')}")
    if me.get("login", "").lower() != args.user.lower():
        print(f"       ! Token gehoert zu '{me.get('login')}', nicht zu '{args.user}'.")
        return 1

    if args.dry_run:
        print("\n(--dry-run: hier waere Schluss)")
        return 0

    # 2 ── Repo anlegen, falls es noch nicht existiert
    code, _ = _api(token, "GET", f"/repos/{full}")
    if code == 200:
        print("  2/6  Repo existiert bereits")
    else:
        code, res = _api(
            token,
            "POST",
            "/user/repos",
            {"name": args.repo, "private": args.private, "auto_init": False},
        )
        if code != 201:
            msg = res.get("message") if isinstance(res, dict) else res
            print(f"  2/6  Repo anlegen fehlgeschlagen (HTTP {code}): {msg}")
            print("       Leg es von Hand an: github.com/new")
            return 1
        print(f"  2/6  Repo angelegt ({'privat' if args.private else 'oeffentlich'})")

    # 3 ── Push. Der Token steht in der Remote-URL, deshalb wird sie danach ersetzt.
    push_url = f"https://x-access-token:{token}@github.com/{full}.git"
    _sh("git", "remote", "remove", "origin", cwd=repo_dir)
    rc, out = _sh("git", "remote", "add", "origin", push_url, cwd=repo_dir)
    if rc != 0:
        print(f"  3/6  Remote setzen fehlgeschlagen: {out}")
        return 1
    try:
        rc, out = _sh("git", "push", "-u", "origin", "HEAD:main", cwd=repo_dir)
    finally:
        # Muss IMMER laufen: sonst bliebe der Token im Klartext in .git/config stehen —
        # auch bei Abbruch mit Strg-C. Masterplan §24: Keys nie dauerhaft auf Platte.
        _sh("git", "remote", "set-url", "origin", f"https://github.com/{full}.git", cwd=repo_dir)
    out = out.replace(token, "***")  # der Token darf in keiner Fehlermeldung stehen
    if rc != 0:
        print(f"  3/6  Push fehlgeschlagen:\n{out}")
        return 1
    print("  3/6  Code hochgeladen")

    # 4 ── Secrets fuer Telegram
    env_file = repo_dir / ".env"
    for name in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        value = _env_value(name, env_file)
        if not value:
            print(f"  4/6  {name}: nicht in .env gefunden — uebersprungen")
            continue
        print(f"  4/6  {name}: {_put_secret(token, full, name, value)}")

    # 5 ── Pages auf "GitHub Actions" stellen
    code, _ = _api(token, "POST", f"/repos/{full}/pages", {"build_type": "workflow"})
    if code in (201, 204):
        print("  5/6  GitHub Pages eingeschaltet (Quelle: Actions)")
    elif code == 409:
        code2, _ = _api(token, "PUT", f"/repos/{full}/pages", {"build_type": "workflow"})
        print(
            "  5/6  Pages war schon an"
            + (", Quelle auf Actions gestellt" if code2 in (200, 204) else "")
        )
    else:
        print(f"  5/6  Pages konnte nicht eingeschaltet werden (HTTP {code})")
        print("       Von Hand: Settings -> Pages -> Source: GitHub Actions")

    # 6 ── Ersten Lauf anstossen
    code, _ = _api(
        token, "POST", f"/repos/{full}/actions/workflows/daily.yml/dispatches", {"ref": "main"}
    )
    print(
        "  6/6  Erster Lauf gestartet"
        if code == 204
        else f"  6/6  Lauf konnte nicht gestartet werden (HTTP {code}) — Actions-Tab, 'Run workflow'"
    )

    print(f"\nLaeuft:  https://github.com/{full}/actions")
    print(f"App:     https://{args.user.lower()}.github.io/{args.repo}/")
    print("\nDie Adresse ist erst nach dem ersten gruenen Lauf erreichbar (ca. 2 Minuten).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
