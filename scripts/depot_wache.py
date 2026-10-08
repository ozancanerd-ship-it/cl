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
import asyncio
import base64
import binascii
import contextlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trading_agent.ops.notify import (
    EmailSink,
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
#: Von der App versiegelt hochgeladenes Depot (Auto-Abgleich, Schluessel DEPOT_SCHLUESSEL).
SYNC = "data/repository_real/live/depot_sync.siegel"
SYNC_ZWECK = "depot-sync-v1"

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
    "entwarnung": "früherer Stop-Alarm hinfällig — nicht deswegen verkaufen",
    "ueberdehnt": "deutlich im Plus und überdehnt — Rückschlags-Alarm statt Verkauf",
    "gewinn_rueckgang": "Gewinn fällt vom Hoch zurück — jetzt Teil verkaufen",
}
#: Diese Arten verlangen JETZT eine Entscheidung und bekommen die Klingel auch oeffentlich.
DRINGEND = {
    "stop",
    "ziel",
    "ko",
    "puffer_kritisch",
    "puffer_eng",
    "zahlen_ko",
    "entwarnung",
    "gewinn_rueckgang",
}
#: Was die oeffentliche Klingel im Betreff sagt — die Handlung, nie der Wert.
HANDLUNG = {
    "stop": "VERKAUFEN",
    "ko": "VERKAUFEN",
    "puffer_kritisch": "VERKAUFEN",
    "ziel": "TEIL VERKAUFEN",
    "puffer_eng": "TEIL VERKAUFEN",
    "zahlen_ko": "PRÜFEN",
    "entwarnung": "ENTWARNUNG",
    "gewinn_rueckgang": "TEIL VERKAUFEN",
}


def oeffentlicher_titel(neu: list[Ereignis]) -> str:
    """Betreff der Mail / GitHub-Meldung: welche Handlung, wie oft — ohne Namen.

    Bis 01.10. hiess er immer „DEPOT — 1 Position(en) brauchen eine Entscheidung". Ob das
    ein Verkauf, ein Teilverkauf oder nur ein Termin ist, sah man erst in der App. Ozan will
    gerade bei Verkäufen und Teilverkäufen extra benachrichtigt werden — also steht die
    Handlung jetzt vorn im Betreff.
    """
    zaehl: dict[str, int] = {}
    for e in neu:
        h = HANDLUNG.get(e.art)
        if h:
            zaehl[h] = zaehl.get(h, 0) + 1
    reihenfolge = ["VERKAUFEN", "TEIL VERKAUFEN", "PRÜFEN", "ENTWARNUNG"]
    teile = [f"{h} ({zaehl[h]})" if zaehl[h] > 1 else h for h in reihenfolge if h in zaehl]
    return "DEPOT · " + " · ".join(teile) if teile else "DEPOT · Meldung"


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


def sync_lesen(pfad: str | Path, geheim: str) -> str:
    """Das von der App versiegelte Depot oeffnen. Leer, wenn nichts da ist oder der Schluessel nicht passt."""
    p = Path(pfad)
    if not geheim or not p.exists():
        return ""
    try:
        klar = oeffnen(p.read_text(encoding="utf-8"), schluessel_aus(geheim, SYNC_ZWECK))
    except OSError:
        return ""
    if klar is None:
        print("::warning::depot_sync.siegel passt nicht zu DEPOT_SCHLUESSEL — es gilt DEPOT_CODE.")
        return ""
    return klar.decode("utf-8", "replace")


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


def stand_sichern(pfad: str | Path, stand: dict[str, Any], schluessel: bytes) -> bool:
    """Versiegelt schreiben — aber nur, wenn sich INHALTLICH etwas geaendert hat.

    Jede Versiegelung hat eine neue Zufallszahl, die Datei saehe also bei jedem Lauf
    anders aus. Ohne diesen Vergleich gaebe es alle fuenf Minuten einen Commit, der nichts
    sagt. Gibt zurueck, ob geschrieben wurde."""
    p = Path(pfad)
    klar = json.dumps(stand, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if p.exists():
        alt = oeffnen(p.read_text(encoding="utf-8"), schluessel)
        if alt is not None and alt.decode("utf-8", "replace") == klar:
            return False
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(versiegeln(klar.encode("utf-8"), schluessel) + "\n", encoding="utf-8")
    return True


# --------------------------------------------------------------------------- Livekurse


def _boerse_offen(jetzt: datetime) -> bool:
    """US-Boerse offen (Mo–Fr 9:30–16:00 New Yorker Zeit). Ausserhalb gilt der Schlusskurs.

    Vorher fest 13:30–20:00 UTC — das stimmt nur in der amerikanischen Sommerzeit. Ab
    November (EST) oeffnet die Boerse erst 14:30 UTC; die erste Handelsstunde fehlte dann,
    und nach 20:00 UTC haette der Waechter noch eine Stunde lang Schlusskurse fuer Livekurse
    gehalten. Die Zeitzone rechnet die Umstellung selbst."""
    if jetzt.tzinfo is None:
        jetzt = jetzt.replace(tzinfo=UTC)
    ny = jetzt.astimezone(ZoneInfo("America/New_York"))
    if ny.weekday() >= 5:
        return False
    minuten = ny.hour * 60 + ny.minute
    return 9 * 60 + 30 <= minuten <= 16 * 60 + 5


async def livekurse(
    scan: dict[str, Any], positionen: list[dict[str, Any]], jetzt: datetime
) -> dict[str, float]:
    """Aktuelle Kurse fuer genau die Werte, die das Depot braucht (Coins und Basiswerte).

    Coins ueber Kraken (oeffentlicher Ticker), Aktien ueber Yahoo nur waehrend der
    Boersenzeit. Faellt eine Quelle aus, bleibt fuer diesen Wert der Scan-Kurs — lieber
    zehn Minuten alt als gar keiner."""
    import httpx

    from trading_agent.portfolio_intel.depot_stops import ScanIndex, lage_fuer

    idx = ScanIndex(scan)
    brauche: dict[str, str] = {}
    for pos in positionen:
        lg = lage_fuer(pos, idx)
        if lg.row and lg.row.get("instrument"):
            brauche[str(lg.row["instrument"])] = str(lg.row.get("klasse") or "")
    aus: dict[str, float] = {}
    async with httpx.AsyncClient(timeout=15.0) as c:
        for inst, klasse in brauche.items():
            if klasse not in ("krypto", "gold") or not inst.endswith("USD"):
                continue
            try:
                r = await c.get("https://api.kraken.com/0/public/Ticker", params={"pair": inst})
                d = r.json()
                werte = list((d.get("result") or {}).values())
                if werte:
                    aus[inst] = float(werte[0]["c"][0])
            except Exception:
                continue
    aktien = [i for i, k in brauche.items() if k == "aktien"]
    if aktien and _boerse_offen(jetzt):
        from trading_agent.data.providers.yahoo_finance import YahooFinanceProvider

        prov = YahooFinanceProvider()
        try:
            for inst in aktien:
                try:
                    aus[inst] = float((await prov.latest_indicative(inst)).price)
                except Exception:
                    continue
        finally:
            with contextlib.suppress(Exception):
                await prov.aclose()
    return aus


def scan_holen(pfad: str, url: str) -> dict[str, Any] | None:
    """Die Scan-Datei — lokal, oder (im leichten Lauf) die veroeffentlichte von der Seite."""
    try:
        return json.loads(Path(pfad).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    if not url:
        return None
    import httpx

    try:
        r = httpx.get(url.rstrip("/") + "/scan.json", timeout=30.0)
        if r.status_code == 200:
            d = r.json()
            return d if isinstance(d, dict) else None
    except Exception:
        return None
    return None


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
    elif len(ruhig) > 1 and all(e.art in {"stop_neu", "stop_nach"} for e in ruhig):
        titel = f"{len(ruhig)} Stop(s) gesetzt oder nachgezogen"
    else:
        titel = (
            f"{len(ruhig)} Meldungen"
            if len(ruhig) > 1
            else f"{ART_TITEL.get(ruhig[0].art, ruhig[0].art)} · {ruhig[0].name}"
        )
    zeilen = []
    for e in dringend + ruhig:
        zeilen.append(f"• {ART_TITEL.get(e.art, e.art)} — {e.name}: {e.text}")
    if ruhig:
        zeilen.append(
            "Die App überwacht diese Stops und meldet sich, wenn einer fällt. Die Order beim "
            "Broker musst du selbst anpassen — die App führt keine Orders aus."
        )
    liste = "\n".join(
        f"• **{HANDLUNG.get(e.art, 'PRÜFEN')}** — {ART_TITEL.get(e.art, e.art)}" for e in dringend
    )
    klingel = (
        f"In deinem Depot steht jetzt:\n\n{liste}\n\n"
        "Welche Position es ist und wie viel, steht **in der App** unter Depot — hier nicht, "
        "weil dieses Repository öffentlich ist und dein Depot niemanden etwas angeht.\n\n"
        "https://ozancanerd-ship-it.github.io/cl/"
    )
    return titel, "\n".join(zeilen), klingel


# --------------------------------------------------------------------------- Lauf


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--scan", default="web/scan.json")
    ap.add_argument("--stand", default=STAND)
    ap.add_argument("--sync", default=SYNC)
    ap.add_argument("--oeffentlich", default=OEFFENTLICH)
    ap.add_argument("--send", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="Stand NICHT fortschreiben")
    ap.add_argument(
        "--live",
        action="store_true",
        help="aktuelle Kurse holen (leichter Lauf alle fuenf Minuten) statt der Scan-Kurse",
    )
    ap.add_argument(
        "--seite",
        default="",
        help="Adresse der App — fehlt die lokale scan.json, wird die veroeffentlichte geholt",
    )
    args = ap.parse_args()
    jetzt = datetime.now(UTC)

    roh = os.environ.get("DEPOT_CODE", "").strip()
    sync_geheim = os.environ.get("DEPOT_SCHLUESSEL", "").strip()
    sync_roh = sync_lesen(args.sync, sync_geheim)
    if sync_roh:
        # Der automatische Abgleich aus der App ist neuer als jedes von Hand gesetzte DEPOT_CODE.
        roh = sync_roh
    push = WebPushSink(min_severity=Severity.INFO)
    tg = TelegramSink(min_severity=Severity.INFO)
    mail = EmailSink(min_severity=Severity.INFO)
    gh = GitHubIssueSink(erwaehnen="ozancanerd-ship-it")
    kanaele = [n for n, s in (("push", push), ("telegram", tg), ("email", mail), ("mail", gh)) if s.available()]

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

    scan = scan_holen(args.scan, args.seite)
    if scan is None:
        print("::warning::Scan nicht lesbar — keine Depotpruefung.")
        return 0
    if args.live:
        try:
            live = asyncio.run(livekurse(scan, positionen, jetzt))
        except Exception as exc:
            live = {}
            print(f"  (Livekurse nicht ladbar: {type(exc).__name__} — es gelten die Scan-Kurse)")
        if live:
            scan = {
                **scan,
                "gesamt": [
                    {**r, "kurs": live[str(r.get("instrument"))]}
                    if str(r.get("instrument")) in live
                    else r
                    for r in (scan.get("gesamt") or [])
                ],
            }
        print(f"  Livekurse: {len(live)} Wert(e)")

    # Mit Auto-Abgleich aendert sich der Depot-Text bei jedem Kauf — der Stand-Schluessel muss
    # aus etwas Festem kommen, sonst finge der Waechter nach jeder Aenderung bei null an.
    schluessel = schluessel_aus(sync_geheim if sync_roh else roh, ZWECK)
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
        if mail.available():
            sinks.append(mail)
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
                    title=oeffentlicher_titel(neu),
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
        geschrieben = stand_sichern(args.stand, neuer_stand, schluessel)
        marker = Path(os.environ.get("GITHUB_OUTPUT", ""))
        if marker.name:
            with open(marker, "a", encoding="utf-8") as fh:
                fh.write(f"geaendert={'1' if geschrieben else '0'}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
