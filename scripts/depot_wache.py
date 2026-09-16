#!/usr/bin/env python3
"""Der Depot-Waechter — meldet, wenn an OZANS EIGENEN Positionen etwas passiert.

    python3 scripts/depot_wache.py --scan web/scan.json --send

WARUM ES DAS GIBT

Ozan: „Ich kriege auch keine Alarme. Ich kriege nur die Alarme, wenn ich auf der App
drauf bin." Das stimmte, und es war keine Schlamperei, sondern eine Luecke im Aufbau:
Das Depot liegt ausschliesslich im Browser (localStorage). Der oeffentliche Ablauf auf
GitHub kannte es nicht — er konnte also unmoeglich melden, dass ein Stop faellt. Die
Signalalarme (Einstieg, Stop, Ziel eines gescannten Setups) liefen laengst; nur an
seinen eigenen Positionen lief nichts.

Dieses Skript schliesst die Luecke. Das Depot kommt als Geheimnis ``DEPOT_CODE`` in den
Lauf — derselbe Text, den die App unter „Sync" ausgibt. Geheimnisse stehen nicht im
oeffentlichen Repository und tauchen auch in den Protokollen nicht auf.

WAS GEMELDET WIRD — UND WAS AUSDRUECKLICH NICHT

Gemeldet wird nur, was eine Entscheidung verlangt:

  * Stop gerissen
  * Ziel erreicht (je Marke einmal)
  * Knock-out- oder Liquidationspuffer unter 6 % — der Fall, der ueber Nacht alles kostet

Nicht gemeldet wird: Kursbewegung, Notenwechsel, „Scan erfolgreich", Rangaenderungen.
Ozan dazu woertlich: „nicht jetzt wie davor mit diesen Scans, dass er jede 15 Minuten
sagt, ja, Scan erfolgreich, sowas interessiert mich nicht."

WARUM IM OEFFENTLICHEN KANAL KEINE DETAILS STEHEN

Der Auffangweg ist ein GitHub-Issue mit Erwaehnung — der einzige Weg, der ohne
eingerichtete Geheimnisse per Mail ankommt. **Das Repository ist oeffentlich.** Ein
Issue „STOP SOLUSD — 1,79 Stueck" wuerde damit verraten, was Ozan haelt und wie viel.
Deshalb traegt der oeffentliche Kanal nur die Klingel: wie viele Positionen eine
Entscheidung verlangen und welcher Art. Die Zahlen stehen in der App.

Wer die Einzelheiten in der Nachricht will, richtet Telegram ein (TELEGRAM_BOT_TOKEN und
TELEGRAM_CHAT_ID) oder Web Push — beide Wege sind privat und bekommen den vollen Text.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trading_agent.ops.notify import (
    FileSink,
    GitHubIssueSink,
    Notification,
    Notifier,
    Severity,
    TelegramSink,
    WebPushSink,
)

STAND = "data/repository_real/live/depot_alarme.json"
PROTOKOLL = "data/repository_real/live/alerts.jsonl"

#: Unter diesem Puffer zum Knock-out bzw. zur Liquidation wird es ernst — dagegen hilft
#: kein Stop, die Schwelle greift auch nachts.
PUFFER_KRITISCH_PCT = 6.0


# --------------------------------------------------------------------------- Depot lesen


def depot_lesen(roh: str) -> list[dict[str, Any]]:
    """Den Text aus dem Sync-Reiter in Positionen verwandeln. Leer bei Unsinn."""
    s = (roh or "").strip()
    if not s:
        return []
    if "depot=" in s:
        s = s.split("depot=", 1)[1].split("&")[0].strip()
    if not s.startswith("{"):
        try:
            s = base64.b64decode(s + "=" * (-len(s) % 4)).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError, ValueError):
            return []
    try:
        d = json.loads(s)
    except json.JSONDecodeError:
        return []
    liste = d if isinstance(d, list) else d.get("p")
    if not isinstance(liste, list):
        return []
    return [p for p in liste if isinstance(p, dict) and p.get("sym")]


# --------------------------------------------------------------------------- Kurse


def _kurstabelle(scan: dict[str, Any]) -> dict[str, float]:
    raus: dict[str, float] = {}
    for r in scan.get("gesamt") or []:
        name = str(r.get("instrument") or "").upper()
        kurs = r.get("kurs")
        if name and isinstance(kurs, int | float):
            raus[name] = float(kurs)
    return raus


#: Wie die App: derselbe Coin heisst bei Bybit USDT, bei Kraken USD. Ein Depoteintrag
#: muss beide finden, sonst faellt ausgerechnet die Position aus der Ueberwachung, die
#: von der anderen Boerse eingetragen wurde.
_ENDUNGEN = ("", "USDT", "USD", "EUR")


def kurs_fuer(sym: str, tabelle: dict[str, float]) -> float | None:
    s = str(sym or "").upper()
    if s in tabelle:
        return tabelle[s]
    stamm = s
    for e in ("USDT", "USD", "EUR"):
        if stamm.endswith(e) and len(stamm) > len(e):
            stamm = stamm[: -len(e)]
            break
    for e in _ENDUNGEN:
        if (stamm + e) in tabelle:
            return tabelle[stamm + e]
    return None


# --------------------------------------------------------------------------- Pruefung


def _lang(pos: dict[str, Any]) -> bool:
    plan = pos.get("plan") or {}
    if plan.get("richtung") == "short":
        return False
    return pos.get("hebel_richtung") != "short"


def ereignisse_fuer(
    pos: dict[str, Any], kurs: float | None, tabelle: dict[str, float] | None = None
) -> list[dict[str, str]]:
    """Was an dieser Position eine Entscheidung verlangt. Leer heisst: nichts."""
    raus: list[dict[str, str]] = []
    sym = str(pos.get("sym") or "")
    plan = pos.get("plan") or {}
    lang = _lang(pos)

    # Knock-out / Liquidation zuerst — davor schuetzt kein Stop.
    ko = pos.get("ko")
    # Die Kurstabelle MUSS hier durchgereicht werden. Zuerst stand hier ein leeres
    # Woerterbuch — damit war der Basiskurs immer None und die Knock-out-Pruefung, also
    # die einzige, die vor einem Totalverlust warnt, lief nie an.
    basis_kurs = kurs_fuer(str(pos.get("basis") or ""), tabelle or {}) if pos.get("basis") else None
    if isinstance(ko, int | float) and basis_kurs:
        abstand = (basis_kurs - float(ko)) if lang else (float(ko) - basis_kurs)
        if abstand > 0 and abstand / basis_kurs * 100 < PUFFER_KRITISCH_PCT:
            raus.append({"art": "puffer", "sym": sym})

    if kurs is None:
        return raus

    stop = plan.get("stop")
    gerissen = isinstance(stop, int | float) and (
        (kurs <= float(stop)) if lang else (kurs >= float(stop))
    )
    if gerissen:
        raus.append({"art": "stop", "sym": sym})
        return raus  # Ist der Stop gerissen, ist der Rest egal.

    erledigt = set(pos.get("erledigt") or [])
    for marke in ("TP1", "TP2", "TP3"):
        ziel = plan.get(marke.lower())
        if not isinstance(ziel, int | float) or marke in erledigt:
            continue
        if (kurs >= float(ziel)) if lang else (kurs <= float(ziel)):
            raus.append({"art": "ziel", "sym": sym, "marke": marke})
            break
    return raus


# --------------------------------------------------------------------------- Stand


def stand_laden(pfad: str) -> dict[str, str]:
    p = Path(pfad)
    if not p.exists():
        return {}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {str(k): str(v) for k, v in (d.get("gemeldet") or {}).items()}


def stand_sichern(pfad: str, gemeldet: dict[str, str]) -> None:
    p = Path(pfad)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps({"gemeldet": gemeldet}, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )


# --------------------------------------------------------------------------- Lauf


ART_TEXT = {
    "stop": "Stop gerissen",
    "ziel": "Ziel erreicht",
    "puffer": "Knock-out bedrohlich nah",
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--scan", default="web/scan.json")
    ap.add_argument("--stand", default=STAND)
    ap.add_argument("--send", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="Stand NICHT fortschreiben")
    args = ap.parse_args()

    roh = os.environ.get("DEPOT_CODE", "")
    if not roh.strip():
        print("kein DEPOT_CODE hinterlegt — der Depot-Waechter bleibt still.")
        print(
            "  Einrichten: App -> Sync -> 'Depot kopieren', dann unter Settings -> Secrets"
            " -> Actions ein Geheimnis DEPOT_CODE anlegen und den Text einfuegen."
        )
        return 0

    positionen = depot_lesen(roh)
    if not positionen:
        print("::warning::DEPOT_CODE liess sich nicht lesen — Text aus dem Sync-Reiter erwartet.")
        return 0

    try:
        scan = json.loads(Path(args.scan).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"::warning::Scan nicht lesbar ({exc}) — keine Depotpruefung.")
        return 0

    tabelle = _kurstabelle(scan)
    jetzt = datetime.now(UTC)
    gemeldet = stand_laden(args.stand)
    neu: list[dict[str, str]] = []

    for pos in positionen:
        kurs = kurs_fuer(str(pos.get("sym") or ""), tabelle)
        for e in ereignisse_fuer(pos, kurs, tabelle):
            # Der Schluessel haelt einen Alarm fest, nicht einen Kurs: derselbe Stop an
            # derselben Position meldet sich genau einmal.
            schluessel = f"{e['sym']}|{e['art']}|{e.get('marke', '')}"
            if schluessel in gemeldet:
                continue
            gemeldet[schluessel] = jetzt.isoformat()
            neu.append(e)

    # Positionen, die es nicht mehr gibt, duerfen ihren Merker wieder verlieren — sonst
    # meldet ein spaeterer Wiedereinstieg nie wieder etwas.
    aktuell = {str(p.get("sym") or "") for p in positionen}
    gemeldet = {k: v for k, v in gemeldet.items() if k.split("|")[0] in aktuell}

    print(f"{len(positionen)} Position(en) geprueft, {len(neu)} neue Meldung(en).")
    for e in neu:
        print(f"  [!] {ART_TEXT.get(e['art'], e['art'])}")

    if args.send and neu:
        # Voller Text nur ueber die privaten Wege. Der oeffentliche Kanal bekommt die
        # Klingel ohne Inhalt — das Repository ist oeffentlich, und was Ozan haelt,
        # geht niemanden etwas an.
        privat = f"{len(neu)} Position(en) im Depot verlangen eine Entscheidung:\n" + "\n".join(
            f"  {ART_TEXT.get(e['art'], e['art'])} — {e['sym']}"
            + (f" ({e['marke']})" if e.get("marke") else "")
            for e in neu
        )
        arten = sorted({ART_TEXT.get(e["art"], e["art"]) for e in neu})
        oeffentlich = (
            f"{len(neu)} Position(en) in deinem Depot verlangen jetzt eine Entscheidung "
            f"({', '.join(arten)}).\n\n"
            "Die Einzelheiten stehen **in der App** unter Portfolio — hier nicht, weil "
            "dieses Repository oeffentlich ist und dein Depot niemanden etwas angeht.\n\n"
            "https://ozancanerd-ship-it.github.io/cl/"
        )

        sinks: list[Any] = [FileSink(PROTOKOLL)]
        tg = TelegramSink(min_severity=Severity.INFO)
        push = WebPushSink(min_severity=Severity.INFO)
        if tg.available():
            sinks.insert(0, tg)
        if push.available():
            sinks.insert(0, push)
        n_privat = Notifier(sinks, max_per_window=10, dedup_window_s=0.0)
        n_privat.notify(
            Notification(
                severity=Severity.CRITICAL,
                title="Depot — Handlung noetig",
                body=privat,
                dedup_key="depot|" + "|".join(sorted(f"{e['sym']}{e['art']}" for e in neu)),
                ts=jetzt,
            )
        )

        gh = GitHubIssueSink(erwaehnen="ozancanerd-ship-it")
        if gh.available():
            n_oeff = Notifier([gh], max_per_window=5, dedup_window_s=0.0)
            n_oeff.notify(
                Notification(
                    severity=Severity.CRITICAL,
                    title=f"DEPOT — {len(neu)} Position(en) brauchen eine Entscheidung",
                    body=oeffentlich,
                    dedup_key="depot-oeffentlich|" + jetzt.strftime("%Y%m%d%H%M"),
                    ts=jetzt,
                )
            )
        else:
            print("::warning::Kein Weg nach draussen — weder Telegram noch Web Push noch Issue.")

    if not args.dry_run:
        stand_sichern(args.stand, gemeldet)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
