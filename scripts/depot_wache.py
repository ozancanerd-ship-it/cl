#!/usr/bin/env python3
"""Der Depot-Waechter — Stops fuer OZANS EIGENE Positionen, rund um die Uhr.

    python3 scripts/depot_wache.py --scan web/scan.json --send

WARUM ES DAS GIBT

Ozan: „Ich kriege nur die Alarme, wenn ich auf der App drauf bin." Und am 27.09.: „Die
Stops auf meinem Portfolio 24/7 analysieren und immer neu setzen und mir Bescheid sagen."

Das Depot liegt im Browser (localStorage). Damit der oeffentliche Ablauf auf GitHub es
kennt, kommt es als Geheimnis ``DEPOT_CODE`` herein — derselbe Text, den die App unter
„Sync" ausgibt. Geheimnisse stehen nicht im Repository und nicht in den Protokollen.

WAS BEI JEDEM LAUF PASSIERT (alle zehn Minuten)

Fuer jede Position (``portfolio_intel/depot_stops.py``):
  * Knock-out-Puffer pruefen (Turbos),
  * Stop gerissen? Ziel erreicht?
  * Stop setzen, wo keiner ist, und nachziehen, wo die Analyse es hergibt — aus der
    Invalidierung der laufenden Analyse und dem Halte-Stop (``scanner/halte_stop.py``).

WAS GEMELDET WIRD

  * sofort und dringend: Stop gerissen, Ziel erreicht, Knock-out nah oder beruehrt
  * gebuendelt: Stop neu gesetzt, Stop nachgezogen (erst ab 2 % Abstand zur zuletzt
    gemeldeten Marke — sonst kaeme fast taeglich eine Nachricht je Position)

Jede Meldung genau einmal. Keine Kursbewegung, kein „Scan erfolgreich".

WAS NIRGENDS OEFFENTLICH STEHT

Das Repository ist oeffentlich. Deshalb:
  * der Stand (welcher Stop gilt, was gemeldet ist) liegt VERSIEGELT im Repo
    (``security/siegel.py``, Schluessel aus ``DEPOT_CODE``),
  * der oeffentliche Kanal (GitHub-Issue → Mail) traegt nur die Klingel ohne Werte,
  * ``web/waechter.json`` fuer die App enthaelt nur Zaehler und einen Fingerabdruck,
    an dem die App erkennt, ob der Waechter denselben Depotstand kennt wie das Geraet.
Den vollen Text bekommen nur die privaten Wege (Web Push, Telegram).
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
    GitHubIssueSink,
    Notification,
    Notifier,
    Severity,
    TelegramSink,
    WebPushSink,
)
from trading_agent.portfolio_intel.depot_stops import (
    Ereignis,
    pruefe_depot,
    stand_kennung,
)
from trading_agent.security.siegel import oeffnen, schluessel_aus, versiegeln

STAND = "data/repository_real/live/depot_stand.siegel"
OEFFENTLICH = "web/waechter.json"
ZWECK = "depot-waechter-stand-v1"

ART_TITEL = {
    "stop": "Stop gerissen — verkaufen",
    "ziel": "Ziel erreicht — Teil verkaufen",
    "ko": "Knock-out berührt",
    "puffer_kritisch": "Knock-out bedrohlich nah — raus",
    "puffer_eng": "Knock-out-Puffer wird eng",
    "stop_neu": "Stop gesetzt",
    "stop_nach": "Stop nachziehen",
    "zahlen": "Quartalszahlen stehen an",
    "zahlen_ko": "Quartalszahlen — Knock-out-Risiko",
}
#: Diese Arten verlangen JETZT eine Entscheidung und bekommen die Klingel auch oeffentlich.
DRINGEND = {"stop", "ziel", "ko", "puffer_kritisch", "puffer_eng", "zahlen_ko"}


# --------------------------------------------------------------------------- Depot lesen


def depot_lesen(roh: str) -> list[dict[str, Any]]:
    """Den Text aus dem Sync-Reiter in Positionen verwandeln. Leer bei Unsinn."""
    s = (roh or "").strip()
    if not s:
        return []
    if "depot=" in s:
        s = s.split("depot=", 1)[1].split("&")[0].strip()
    if not s.startswith("{") and not s.startswith("["):
        try:
            s = base64.b64decode(s + "=" * (-len(s) % 4)).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError, ValueError):
            return []
    try:
        d = json.loads(s)
    except json.JSONDecodeError:
        return []
    liste = d if isinstance(d, list) else d.get("p") if isinstance(d, dict) else None
    if not isinstance(liste, list):
        return []
    return [p for p in liste if isinstance(p, dict) and p.get("sym")]


# --------------------------------------------------------------------------- Stand


def stand_laden(pfad: str | Path, schluessel: bytes) -> dict[str, Any]:
    p = Path(pfad)
    if not p.exists():
        return {}
    try:
        klar = oeffnen(p.read_text(encoding="utf-8"), schluessel)
    except OSError:
        return {}
    if klar is None:
        # Anderer Schluessel (DEPOT_CODE neu hinterlegt) oder beschaedigt: neu anfangen.
        # Die Stops aus dem neuen Depot-Text gelten dann als Untergrenze.
        print("  (alter Waechter-Stand passt nicht zum Depot-Code — es wird neu begonnen)")
        return {}
    try:
        d = json.loads(klar.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return d if isinstance(d, dict) else {}


def stand_sichern(pfad: str | Path, stand: dict[str, Any], schluessel: bytes) -> None:
    p = Path(pfad)
    p.parent.mkdir(parents=True, exist_ok=True)
    klar = json.dumps(stand, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    p.write_text(versiegeln(klar.encode("utf-8"), schluessel) + "\n", encoding="utf-8")


def oeffentlich_schreiben(pfad: str | Path, inhalt: dict[str, Any]) -> None:
    p = Path(pfad)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(inhalt, ensure_ascii=False, indent=1), encoding="utf-8")


# --------------------------------------------------------------------------- Auswahl


def neue_meldungen(
    ereignisse: list[Ereignis], gemeldet: dict[str, str], jetzt: datetime
) -> tuple[list[Ereignis], dict[str, str]]:
    """Was davon ist neu? Gibt die neuen Ereignisse und den fortgeschriebenen Merker zurueck.

    Knock-out-Warnungen vergessen ihren Merker, sobald der Puffer wieder in Ordnung ist —
    naehert sich der Kurs spaeter erneut der Schwelle, ist das eine neue Lage.
    """
    neu: list[Ereignis] = []
    merker = dict(gemeldet)
    heute = {e.merker for e in ereignisse}
    for k in list(merker):
        teile = k.split("|")
        if len(teile) >= 3 and teile[2].startswith("puffer") and k not in heute:
            del merker[k]
    for e in ereignisse:
        if e.merker in merker:
            continue
        merker[e.merker] = jetzt.isoformat()
        neu.append(e)
    return neu, merker


def texte(neu: list[Ereignis]) -> tuple[str, str, str]:
    """Titel und Text fuer die privaten Wege, dazu die oeffentliche Klingel."""
    dringend = [e for e in neu if e.art in DRINGEND]
    ruhig = [e for e in neu if e.art not in DRINGEND]
    if dringend:
        e = dringend[0]
        titel = f"{ART_TITEL.get(e.art, e.art)} · {e.name}"
        if len(dringend) > 1:
            titel += f" (+{len(dringend) - 1})"
    else:
        titel = (
            f"{len(ruhig)} Stop(s) gesetzt oder nachgezogen"
            if len(ruhig) > 1
            else f"{ART_TITEL.get(ruhig[0].art)} · {ruhig[0].name}"
        )
    zeilen = []
    for e in dringend + ruhig:
        zeilen.append(f"• {ART_TITEL.get(e.art, e.art)} — {e.name}: {e.text}")
    if ruhig:
        zeilen.append(
            "Die App überwacht diese Stops und meldet sich, wenn einer fällt. Die Order beim "
            "Broker musst du selbst anpassen — die App führt keine Orders aus."
        )
    arten = sorted({ART_TITEL.get(e.art, e.art) for e in dringend})
    klingel = (
        f"{len(dringend)} Position(en) in deinem Depot verlangen jetzt eine Entscheidung "
        f"({', '.join(arten)}).\n\n"
        "Die Einzelheiten stehen **in der App** unter Portfolio und kommen per Push — hier "
        "nicht, weil dieses Repository oeffentlich ist und dein Depot niemanden etwas angeht.\n\n"
        "https://ozancanerd-ship-it.github.io/cl/"
    )
    return titel, "\n".join(zeilen), klingel


# --------------------------------------------------------------------------- Lauf


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--scan", default="web/scan.json")
    ap.add_argument("--stand", default=STAND)
    ap.add_argument("--oeffentlich", default=OEFFENTLICH)
    ap.add_argument("--send", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="Stand NICHT fortschreiben")
    args = ap.parse_args()
    jetzt = datetime.now(UTC)

    roh = os.environ.get("DEPOT_CODE", "").strip()
    push = WebPushSink(min_severity=Severity.INFO)
    tg = TelegramSink(min_severity=Severity.INFO)
    gh = GitHubIssueSink(erwaehnen="ozancanerd-ship-it")
    kanaele = [n for n, s in (("push", push), ("telegram", tg), ("mail", gh)) if s.available()]

    if not roh:
        print("kein DEPOT_CODE hinterlegt — der Depot-Waechter bleibt still.")
        print(
            "  Einrichten: App -> Sync -> 'Depot kopieren', dann unter Settings -> Secrets"
            " -> Actions ein Geheimnis DEPOT_CODE anlegen und den Text einfuegen."
        )
        oeffentlich_schreiben(
            args.oeffentlich,
            {
                "aktiv": False,
                "geprueft": jetzt.isoformat(),
                "grund": "kein DEPOT_CODE",
                "kanaele": kanaele,
            },
        )
        return 0

    positionen = depot_lesen(roh)
    if not positionen:
        print("::warning::DEPOT_CODE liess sich nicht lesen — Text aus dem Sync-Reiter erwartet.")
        oeffentlich_schreiben(
            args.oeffentlich,
            {
                "aktiv": False,
                "geprueft": jetzt.isoformat(),
                "grund": "DEPOT_CODE unlesbar",
                "kanaele": kanaele,
            },
        )
        return 0

    try:
        scan = json.loads(Path(args.scan).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"::warning::Scan nicht lesbar ({exc}) — keine Depotpruefung.")
        return 0

    schluessel = schluessel_aus(roh, ZWECK)
    stand = stand_laden(args.stand, schluessel)
    neuer_stand, ereignisse, uebersicht = pruefe_depot(positionen, scan, stand)

    # Merker von Positionen, die es nicht mehr gibt, fallen weg — sonst meldete ein
    # spaeterer Wiedereinstieg nie wieder etwas.
    aktuell = set(neuer_stand["positionen"])
    gemeldet = {
        k: v for k, v in neuer_stand["gemeldet"].items() if "|".join(k.split("|")[:2]) in aktuell
    }
    neu, gemeldet = neue_meldungen(ereignisse, gemeldet, jetzt)
    neuer_stand["gemeldet"] = gemeldet
    neuer_stand["geprueft"] = jetzt.isoformat()

    print(
        f"{uebersicht['positionen']} Position(en) geprueft · {uebersicht['mit_stop']} mit Stop · "
        f"{uebersicht['ohne_kurs']} ohne Kurs · {len(neu)} neue Meldung(en)"
    )
    for e in neu:
        # Im Protokoll nur die Art — das Protokoll ist oeffentlich einsehbar.
        print(f"  [{'!' if e.art in DRINGEND else '·'}] {ART_TITEL.get(e.art, e.art)}")

    if args.send and neu:
        titel, text, klingel = texte(neu)
        dringend = any(e.art in DRINGEND for e in neu)
        # Bewusst KEIN FileSink: alerts.jsonl fliesst in die oeffentliche Seite ein.
        sinks: list[Any] = []
        if push.available():
            sinks.append(push)
        if tg.available():
            sinks.append(tg)
        if sinks:
            Notifier(sinks, max_per_window=10, dedup_window_s=0.0).notify(
                Notification(
                    severity=Severity.CRITICAL if dringend else Severity.WARNING,
                    title=titel,
                    body=text,
                    dedup_key="depot|" + "|".join(sorted(e.merker for e in neu)),
                    ts=jetzt,
                )
            )
        if dringend and gh.available():
            Notifier([gh], max_per_window=5, dedup_window_s=0.0).notify(
                Notification(
                    severity=Severity.CRITICAL,
                    title=f"DEPOT — {sum(e.art in DRINGEND for e in neu)} Position(en) brauchen eine Entscheidung",
                    body=klingel,
                    dedup_key="depot-oeffentlich|" + jetzt.strftime("%Y%m%d%H%M"),
                    ts=jetzt,
                )
            )
        if not sinks and not (dringend and gh.available()):
            print("  (kein privater Kanal eingerichtet — die Meldung steht nur in der App)")

    oeffentlich_schreiben(
        args.oeffentlich,
        {
            "aktiv": True,
            "geprueft": jetzt.isoformat(),
            "stand": stand_kennung(positionen),
            **uebersicht,
            "kanaele": kanaele,
        },
    )
    if not args.dry_run:
        stand_sichern(args.stand, neuer_stand, schluessel)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
