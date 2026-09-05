#!/usr/bin/env python3
"""Alarm nur bei Aenderung — der Wachposten ueber dem Gesamtmarkt-Scan.

    python3 scripts/scan_alert.py --scan web/scan.json --send

Liest den Scan, vergleicht ihn mit dem letzten Stand und schickt **nur die Aenderung**:
ein neues A+/A-Setup, ein weggebrochenes Setup, eine neue Nummer 1, ein Chart der anzieht.
Aendert sich nichts, passiert nichts — kein Alarm ist hier das normale Ergebnis.

Der Stand liegt in einer Datei und wird vom CI-Lauf mitcommittet. Ohne ihn wuerde jeder
Lauf alles neu melden.

Exit-Code ist immer 0: ein fehlender Telegram-Schluessel darf den CI-Lauf nicht killen
(dann wuerde die Seite nicht mehr gebaut). Fehlt er, steht es als ``::warning::`` im Log.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

STAND = "data/repository_real/live/scan_alert_state.json"
PROTOKOLL = "data/repository_real/live/alerts.jsonl"


def _laden(pfad: str) -> dict | None:
    p = Path(pfad)
    if not p.exists():
        return None
    try:
        daten = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return daten if isinstance(daten, dict) else None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--scan", default="web/scan.json")
    ap.add_argument("--stand", default=STAND)
    ap.add_argument("--send", action="store_true", help="per Telegram schicken")
    ap.add_argument(
        "--min",
        choices=["info", "warning", "critical"],
        default="warning",
        help="ab welcher Stufe aufs Telefon. Standard: warning (A+/A-Setups, Wegbrueche).",
    )
    ap.add_argument("--dry-run", action="store_true", help="Stand NICHT fortschreiben")
    args = ap.parse_args()

    from trading_agent.ops.notify import FileSink, Notifier, Severity, TelegramSink
    from trading_agent.scanner.alerting import ScanWaechter, als_text

    doc = _laden(args.scan)
    if doc is None:
        print(f"::warning::Scan nicht lesbar: {args.scan} — kein Alarm")
        return 0

    stumm = [k for k, n in (doc.get("anzahl") or {}).items() if not n]
    if stumm:
        print(f"! stumme Klassen: {', '.join(stumm)} — deren Instrumente gelten als unbekannt")

    jetzt = datetime.now(UTC)
    alarme, neuer_stand = ScanWaechter().pruefen(doc, _laden(args.stand), jetzt=jetzt)

    print(f"Scan vom {doc.get('erzeugt')} · {len(doc.get('gesamt', []))} Instrumente")
    print(f"{len(alarme)} Aenderung(en)\n")
    print(als_text(alarme))

    schwelle = {"info": Severity.INFO, "warning": Severity.WARNING, "critical": Severity.CRITICAL}[
        args.min
    ]
    zu_senden = [a for a in alarme if a.severity >= schwelle]

    if args.send and zu_senden:
        tg = TelegramSink(min_severity=Severity.INFO)
        if not tg.available():
            print("::warning::Telegram nicht konfiguriert — TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID")
        # Der FileSink laeuft immer mit: die Alarm-Historie ist Teil der Forward-Datenspur.
        sinks = [FileSink(PROTOKOLL)]
        if tg.available():
            sinks.insert(0, tg)
        n = Notifier(sinks, max_per_window=6, dedup_window_s=0.0)
        geschickt = sum(1 for a in zu_senden if n.notify(a.as_notification(jetzt)))
        print(f"\n{geschickt} von {len(zu_senden)} Meldung(en) rausgegangen ({n.active_sinks})")
    elif args.send:
        print("\nnichts zu senden")

    if not args.dry_run:
        p = Path(args.stand)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(neuer_stand, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8"
        )
        print(f"Stand fortgeschrieben: {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
