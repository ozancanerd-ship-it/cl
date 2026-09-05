#!/usr/bin/env python3
"""Der Waechter — meldet, wenn ein Kurs eine Marke tatsaechlich trifft.

    python3 scripts/watch_levels.py --send                 # leichte Pruefung
    python3 scripts/watch_levels.py --send --vollstaendig  # nach einem Scan

Ozans Vorgabe: „und er gibt mir dann das buy signal oder wenn der wert getroffen ist,
will nicht selber alarme erstellen." Genau das macht dieser Lauf.

**Vollstaendig** (nach dem Scan): neue handelbare Setups auf die Wachliste nehmen und
solche verwerfen, deren Richtung der neue Scan nicht mehr hergibt.

**Leicht** (dazwischen, alle 15 Minuten): nur die Kurse der beobachteten Werte holen
und pruefen, ob eine Marke getroffen wurde. Ein voller Scan waere dafuer Verschwendung —
es geht um eine Handvoll Instrumente, nicht um den Markt.

Geprueft wird gegen **Hoch und Tief seit der letzten Pruefung**, nicht gegen den
Schlusskurs. Sonst rutscht ein Treffer um 14:23 durch, weil der Kurs um 14:30 wieder
darunter steht — und das ist genau der Moment, um den es geht.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trading_agent.core.enums import Timeframe
from trading_agent.scanner.watchlist import Wachliste

STAND = "data/repository_real/live/watchlist.json"
PROTOKOLL = "data/repository_real/live/alerts.jsonl"
#: Wie weit zurueck Kerzen geholt werden, wenn kein letzter Stand bekannt ist.
RUECKBLICK_MAX = timedelta(hours=6)

#: Und mindestens so weit — auch wenn die letzte Pruefung gerade erst war.
#:
#: Der Grund ist unscheinbar und war beim ersten Lauf sofort da: bei einem Abstand von
#: neun Minuten liegt keine abgeschlossene M15-Kerze im Fenster, und die Pruefung sah
#: null Kurse fuer achtzehn Wachen. Ein Fenster von 45 Minuten deckt immer mehrere
#: Kerzen ab. Doppelt hinzusehen kostet nichts: der Zustandsautomat meldet jeden
#: Uebergang ohnehin nur einmal.
RUECKBLICK_MIN = timedelta(minutes=45)


def _laden(pfad: str) -> dict[str, Any] | None:
    p = Path(pfad)
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return d if isinstance(d, dict) else None


async def _extrema(
    namen_je_klasse: dict[str, list[str]], seit: datetime, bis: datetime
) -> dict[str, dict[str, float]]:
    """Hoch, Tief und letzter Kurs je Instrument im Fenster ``seit``..``bis``."""
    aus: dict[str, dict[str, float]] = {}

    async def sammle(prov: Any, namen: list[str]) -> None:
        for name in namen:
            try:
                bars = await prov.fetch_ohlcv(name, Timeframe.M15, seit, bis)
            except Exception as exc:
                print(f"  {name:<14} keine Kurse ({type(exc).__name__})")
                continue
            bars = [b for b in bars if b is not None]
            if not bars:
                continue
            aus[name] = {
                "hoch": max(float(b.high) for b in bars),
                "tief": min(float(b.low) for b in bars),
                "letzter": float(bars[-1].close),
                "bars": float(len(bars)),
            }

    krypto = namen_je_klasse.get("krypto", []) + namen_je_klasse.get("gold", [])
    if krypto:
        from trading_agent.data.providers.binance import BinancePublicDataProvider

        prov = BinancePublicDataProvider(market="spot")
        try:
            await sammle(prov, krypto)
        finally:
            with contextlib.suppress(Exception):
                await prov.aclose()

    aktien = namen_je_klasse.get("aktien", [])
    if aktien:
        from trading_agent.data.providers.yahoo_finance import YahooFinanceProvider

        prov2 = YahooFinanceProvider()
        try:
            await sammle(prov2, aktien)
        finally:
            with contextlib.suppress(Exception):
                await prov2.aclose()
    return aus


def _seit(stand: dict[str, Any] | None, jetzt: datetime) -> datetime:
    """Ab wann geprueft wird: seit der letzten Pruefung, hoechstens einige Stunden.

    Der Deckel ist wichtig. Nach einer laengeren Pause wuerde ein Fenster von Tagen
    jede Marke „treffen", die irgendwann einmal beruehrt wurde — und dann kaeme eine
    Lawine alter Meldungen, die nichts mehr mit der Lage zu tun haben.
    """
    roh = (stand or {}).get("stand")
    if roh:
        try:
            t = datetime.fromisoformat(str(roh))
            return min(max(t, jetzt - RUECKBLICK_MAX), jetzt - RUECKBLICK_MIN)
        except ValueError:
            pass
    return jetzt - RUECKBLICK_MAX


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--scan", default="web/scan.json")
    ap.add_argument("--stand", default=STAND)
    ap.add_argument("--wachliste-out", default="web/watchlist.json")
    ap.add_argument("--send", action="store_true")
    ap.add_argument(
        "--vollstaendig",
        action="store_true",
        help="neue Setups aufnehmen und ueberholte verwerfen (nach einem Scan)",
    )
    ap.add_argument(
        "--alle-setups",
        action="store_true",
        help=(
            "Auch B- und B+-Setups aufs Telefon schicken. Standard: nur A−/A/A+. "
            "Alles Handelbare steht so oder so auf der Wachliste und in der App — "
            "aber jede Stunde ein paar B-Setups zu melden waere genau der Spam, den "
            "wir nicht wollen. Einstieg, Ziel und Stop werden IMMER gemeldet, "
            "unabhaengig von der Note: da bist du dann im Trade."
        ),
    )
    ap.add_argument("--dry-run", action="store_true", help="Stand NICHT fortschreiben")
    args = ap.parse_args()

    from trading_agent.ops.notify import FileSink, Notification, Notifier, Severity, TelegramSink
    from trading_agent.utils.logging import configure_logging

    configure_logging("WARNING")
    jetzt = datetime.now(UTC)
    stand = _laden(args.stand)
    liste = Wachliste.from_dict(stand)
    scan = _laden(args.scan) or {}
    zeilen = scan.get("gesamt") or []

    ereignisse = []
    if args.vollstaendig and zeilen:
        ereignisse += liste.gegen_scan(zeilen, jetzt=jetzt)
        ereignisse += liste.aufnehmen(zeilen, jetzt=jetzt)

    offen = liste.offen
    print(f"{len(offen)} offene Wache(n) von {len(liste.wachen)} insgesamt")

    if offen:
        je_klasse: dict[str, list[str]] = {}
        for w in offen:
            je_klasse.setdefault(w.klasse or "krypto", []).append(w.instrument)
        seit = _seit(stand, jetzt)
        print(f"Fenster: {seit:%d.%m. %H:%M} – {jetzt:%H:%M} UTC")
        kurse = await _extrema(je_klasse, seit, jetzt)
        print(f"Kurse fuer {len(kurse)} von {len(offen)} Werten")
        ereignisse += liste.pruefen(kurse, jetzt=jetzt)

    # Neue Setups unterhalb von A− landen auf der Wachliste und in der App, aber nicht
    # aufs Telefon. Alles, was einen laufenden Trade betrifft, geht immer raus.
    zu_senden = [e for e in ereignisse if e.art != "NEUES_SETUP" or e.dringend or args.alle_setups]
    still = len(ereignisse) - len(zu_senden)

    print(
        f"\n{len(ereignisse)} Ereignis(se)" + (f", davon {still} nur in der App" if still else "")
    )
    for e in ereignisse:
        print(f"\n[{'!' if e.dringend else ' '}] {e.titel}\n{e.text}")

    if args.send and zu_senden:
        tg = TelegramSink(min_severity=Severity.INFO)
        if not tg.available():
            print(
                "\n::warning::Telegram nicht konfiguriert — TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID"
            )
        sinks: list[Any] = [FileSink(PROTOKOLL)]
        if tg.available():
            sinks.insert(0, tg)
        # dedup_window 0: die Ereignisschluessel sind schon einmalig je Wache.
        n = Notifier(sinks, max_per_window=10, dedup_window_s=0.0)
        raus = 0
        for e in zu_senden:
            if n.notify(
                Notification(
                    severity=Severity.CRITICAL if e.dringend else Severity.WARNING,
                    title=e.titel,
                    body=e.text,
                    dedup_key=e.dedup_key,
                    ts=jetzt,
                )
            ):
                raus += 1
        print(f"\n{raus} von {len(zu_senden)} verschickt ({n.active_sinks})")

    # Hat sich am Zustand etwas geaendert? Nur dann muss der Stand gesichert werden.
    # Sonst wuerde die CI viermal pro Stunde einen Commit erzeugen, der nichts sagt
    # ausser "ich war hier".
    vorher = {
        k: (v.get("zustand"), tuple(v.get("erreicht") or []))
        for k, v in ((stand or {}).get("wachen") or {}).items()
    }
    nachher = {k: (w.zustand, tuple(w.erreicht)) for k, w in liste.wachen.items()}
    geaendert = vorher != nachher

    entfernt = liste.aufraeumen()
    if entfernt:
        print(f"{entfernt} alte Wache(n) entfernt")

    if not args.dry_run:
        p = Path(args.stand)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(liste.as_dict(), indent=2, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        print(f"Stand fortgeschrieben: {p}")
        marker = Path(os.environ.get("GITHUB_OUTPUT", ""))
        if marker.name:
            with open(marker, "a", encoding="utf-8") as fh:
                fh.write(f"geaendert={'1' if geaendert else '0'}\n")
        print(f"Zustandsaenderung: {'ja' if geaendert else 'nein'}")
        if args.wachliste_out:
            o = Path(args.wachliste_out)
            o.parent.mkdir(parents=True, exist_ok=True)
            o.write_text(
                json.dumps(
                    {
                        "erzeugt": jetzt.isoformat(),
                        "wachen": [w.as_dict() for w in liste.wachen.values()],
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                encoding="utf-8",
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
