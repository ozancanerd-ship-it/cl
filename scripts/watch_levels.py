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
from trading_agent.scanner.watchlist import Wachliste, archiv_anhaengen, archiv_laden

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


#: So lange darf eine offene Wache ohne Kurs bleiben, bevor sie geschlossen wird.
#: Grosszuegig, weil ein einzelner Ausfall der Boerse keine Position beenden soll.
OHNE_KURS_STUNDEN = 30.0

#: Bei Aktien zaehlen nur BOERSENSTUNDEN (US-Handel 13:30–20:00 UTC, Mo–Fr). Zwei volle
#: Handelstage ohne einen einzigen Kurs — das ist ein echter Ausfall, kein Wochenende.
OHNE_KURS_BOERSENSTUNDEN = 13.0


def _boersenstunden(von: datetime, bis: datetime) -> float:
    """Wie viele US-Handelsstunden zwischen ``von`` und ``bis`` lagen.

    Der Grund fuer diese Funktion war der teuerste stille Fehler der Aktienseite: jede
    Aktien-Wache, die am Freitagabend ihren letzten Kurs sah, stand am Sonntag um zwei
    Uhr frueh dreissig Stunden „ohne Kurs" — und wurde als Karteileiche geschlossen.
    Am 20.09. traf das GILD, NEE, WMT, NOW und GOOGL auf einen Schlag, laufende Trades
    im Plus. Aktien konnten damit nie ueber ein Wochenende laufen, obwohl genau das ihre
    Haltedauer ist („in zwei, drei Tagen passiert das nicht").

    Feiertage kennt die Funktion nicht; ein Feiertag zaehlt als Handelstag. Das macht die
    Grenze nur etwas strenger, nie lockerer.
    """
    if bis <= von:
        return 0.0
    stunden = 0.0
    tag = von.replace(hour=0, minute=0, second=0, microsecond=0)
    while tag < bis:
        if tag.weekday() < 5:
            auf = tag.replace(hour=13, minute=30)
            zu = tag.replace(hour=20, minute=0)
            a, b = max(auf, von), min(zu, bis)
            if b > a:
                stunden += (b - a).total_seconds() / 3600.0
        tag += timedelta(days=1)
    return stunden


def _raeume_zombies(liste: Any, kurse: dict[str, dict[str, float]], jetzt: datetime) -> int:
    """Offene Wachen schliessen, fuer die es seit Tagen keinen Kurs mehr gibt.

    Ohne das entstehen Karteileichen: als der Scan von Binance-USDT auf Kraken-EURO
    umgestellt wurde, gab es fuer ``SOLUSDT`` schlagartig keine Kurse mehr. Die Wache
    haette dort bis in alle Ewigkeit gestanden — offen, unbewegt, und in jeder Statistik
    als laufender Trade mitgezaehlt. Eine Position, die niemand mehr beobachten kann,
    ist keine offene Position, sondern eine Luecke in der Buchfuehrung.

    Geschlossen wird als ``abgelaufen`` (also mit 0 R, nicht als Verlust): was nie
    einen Kurs bekam, hat auch nichts gekostet.
    """
    zu = 0
    for w in list(getattr(liste, "wachen", {}).values()):
        if w.zustand in ("stop", "ziel_erreicht", "invalidiert", "abgelaufen"):
            continue
        if w.instrument in kurse:
            continue
        try:
            zuletzt = datetime.fromisoformat(str(w.zuletzt))
        except (TypeError, ValueError):
            continue
        if w.klasse == "aktien":
            if _boersenstunden(zuletzt, jetzt) < OHNE_KURS_BOERSENSTUNDEN:
                continue
            grenze = f"{OHNE_KURS_BOERSENSTUNDEN:.0f} Boersenstunden"
        else:
            if (jetzt - zuletzt).total_seconds() / 3600.0 < OHNE_KURS_STUNDEN:
                continue
            grenze = f"{OHNE_KURS_STUNDEN:.0f} h"
        w.zustand = "abgelaufen"
        w.zuletzt = jetzt.isoformat()
        zu += 1
        print(f"  {w.instrument:<14} geschlossen — seit ueber {grenze} kein Kurs")
    if zu:
        print(f"  {zu} Karteileiche(n) geschlossen")
    return zu


async def _extrema(
    namen_je_klasse: dict[str, list[str]], seit: datetime, bis: datetime
) -> tuple[dict[str, dict[str, float]], dict[str, list[Any]]]:
    """Hoch, Tief und letzter Kurs je Instrument im Fenster ``seit``..``bis``.

    Zusaetzlich die Kerzen selbst. Sie werden ohnehin geholt, um Hoch und Tief zu
    bestimmen — sie danach wegzuwerfen und den Einstieg nur an einer beruehrten Marke
    festzumachen war genau der Fehler, der die Haelfte aller Trades gekostet hat.
    """
    aus: dict[str, dict[str, float]] = {}
    reihen: dict[str, list[Any]] = {}

    async def sammle(prov: Any, namen: list[str]) -> None:
        for name in namen:
            try:
                bars = await prov.fetch_ohlcv(name, Timeframe.M15, seit, bis)
            except Exception as exc:
                print(f"  {name:<14} keine Kurse ({type(exc).__name__})")
                continue
            bars = [b for b in bars if b is not None]
            if not bars:
                # Frueher ein stilles ``continue``. Im Lauf stand dann "Kurse fuer 2 von
                # 6 Werten" und sonst nichts — vier ueberwachte Werte waren unbemerkt aus
                # der Pruefung gefallen. Bei Aktien ausserhalb der Boersenzeit ist das
                # normal (keine Umsaetze, keine Kerzen); bei Krypto ist es ein echter
                # Ausfall. Beides gehoert ins Protokoll, sonst sieht ein blinder
                # Waechter aus wie ein ruhiger Markt.
                print(f"  {name:<14} keine Kerzen im Fenster — nicht geprueft")
                continue
            aus[name] = {
                "hoch": max(float(b.high) for b in bars),
                "tief": min(float(b.low) for b in bars),
                "letzter": float(bars[-1].close),
                "bars": float(len(bars)),
            }
            reihen[name] = list(bars)

    krypto = namen_je_klasse.get("krypto", []) + namen_je_klasse.get("gold", [])
    # Die Wachliste kann Paare aus beiden Quellen enthalten: BTCEUR von Kraken,
    # BTCUSDT von Bybit. Welche Boerse gefragt wird, entscheidet der Name des Paares —
    # das ist die einzige Angabe, die immer stimmt.
    # USDT/USDC gibt es nur bei Bybit, USD und EUR nur bei Kraken. Der Paarname sagt,
    # wer gefragt wird — die einzige Angabe, die immer stimmt.
    usdt = [n for n in krypto if n.upper().endswith(("USDT", "USDC"))]
    krypto = [n for n in krypto if n not in usdt]
    if usdt:
        from trading_agent.data.providers.bybit_public import BybitPublicDataProvider

        prov0 = BybitPublicDataProvider(category="spot")
        try:
            await sammle(prov0, usdt)
        except Exception as exc:
            print(f"  Bybit nicht erreichbar ({type(exc).__name__}) — USDT-Paare ohne Kurs")
        finally:
            with contextlib.suppress(Exception):
                await prov0.aclose()
    if krypto:
        # Kraken, nicht Binance: die Wachliste muss dieselben Kurse sehen wie der
        # Scan, sonst wird eine Marke auf einer Boerse getroffen und auf der anderen
        # nicht — und die Bilanz misst danach etwas, das niemand haette handeln koennen.
        from trading_agent.data.providers.kraken import KrakenDataProvider

        prov = KrakenDataProvider()
        try:
            # Einmal je Waehrung die Paarliste holen, damit BTCUSD -> XXBTZUSD und
            # BTCEUR -> XXBTZEUR aufgeloest werden koennen.
            for q in ("USD", "EUR"):
                with contextlib.suppress(Exception):
                    await prov.list_symbol_info(quote=q)
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
    return aus, reihen


def _alte_trades_uebernehmen(liste: Any, stand: dict[str, Any] | None) -> int:
    """Einmalig beim Umstieg auf das Alarm-Tor (26.09.): laufende Trades einordnen.

    Folgealarme klingeln seitdem nur fuer Trades, deren Einstieg „gemeldet" ist. Die
    Trades, die schon vorher liefen, kennen das Feld nicht — ohne diese Uebernahme waeren
    sie alle auf einen Schlag stumm, auch die, in denen Ozan nach dem alten Alarm vielleicht
    drin ist. Uebernommen werden die, die auch nach heutigem Massstab durchs Tor kaemen;
    die uebrigen bleiben in der App. Danach traegt jede Wache das Feld, und die Funktion
    tut nichts mehr.
    """
    from trading_agent.scanner import alarm_tor

    roh = (stand or {}).get("wachen") or {}
    alt = {k for k, v in roh.items() if isinstance(v, dict) and "gemeldet" not in v}
    if not alt:
        return 0
    bilanz = alarm_tor.bilanz(v for v in roh.values() if isinstance(v, dict))
    n = 0
    for k in alt:
        w = liste.wachen.get(k)
        if w is None or w.zustand != "aktiv" or w.gemeldet:
            continue
        if alarm_tor.pruefe_wache(w, bilanz).ja:
            w.gemeldet = w.aufgenommen
            n += 1
    print(f"Umstieg Alarm-Tor: {n} von {len(alt)} alten Wachen als gemeldet uebernommen")
    return n


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
            "Das Alarm-Tor abschalten und JEDEN Einstieg samt Folgealarmen schicken — nur "
            "fuer Tests von Hand. Standard: Einstiege nur durch das Tor "
            "(scanner/alarm_tor.py), Folgealarme nur fuer gemeldete Trades."
        ),
    )
    ap.add_argument("--dry-run", action="store_true", help="Stand NICHT fortschreiben")
    args = ap.parse_args()

    from trading_agent.ops.notify import (
        FileSink,
        GitHubIssueSink,
        Notification,
        Notifier,
        Severity,
        TelegramSink,
        WebPushSink,
    )
    from trading_agent.utils.logging import configure_logging

    configure_logging("WARNING")
    jetzt = datetime.now(UTC)
    stand = _laden(args.stand)
    liste = Wachliste.from_dict(stand)
    # Abgeschlossene Trades, die nicht mehr auf der Liste stehen — fuer die Bilanz im
    # Alarm-Tor (02.10.: vorher sah das Tor nur die letzten 60, ohne ueberschriebene).
    archiv = archiv_laden()
    scan = _laden(args.scan) or {}
    zeilen = scan.get("gesamt") or []
    # Welche Trades waren VOR diesem Lauf schon draussen (Schutz-Stop, Ausstieg)? Zu
    # denen klingelt nichts mehr — egal, was der Kurs danach noch macht.
    raus_vorher = {k: w.raus for k, w in liste.wachen.items()}
    _alte_trades_uebernehmen(liste, stand)

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
        kurse, reihen = await _extrema(je_klasse, seit, jetzt)
        fehlend = [w.instrument for w in offen if w.instrument not in kurse]
        print(f"Kurse fuer {len(kurse)} von {len(offen)} Werten")
        if fehlend:
            # Sichtbar, nicht versteckt: diese Werte haben in diesem Durchgang KEINE
            # Ueberwachung bekommen. Ein Stop auf einem davon wuerde jetzt nicht melden.
            print(f"::warning::ohne Kurs und damit ungeprueft: {', '.join(sorted(fehlend))}")
        ereignisse += liste.pruefen(kurse, jetzt=jetzt, kerzen=reihen)
        _raeume_zombies(liste, kurse, jetzt)

    # WAS AUFS TELEFON DARF — und was nicht.
    #
    # Ozans Einwand, woertlich: „mein Handy kriegt die ganze Zeit Nachrichten, wenn er
    # sich neu aktualisiert hat. Ich will nur Alarme bekommen, wo ich reingehen kann."
    # Und am 26.09.: „Die Alarme sind irgendwie schlecht geworden … bessere Alarme, da
    # wo es sich wirklich lohnt."
    #
    # Bis zum 26.09. ging hier JEDER bestaetigte Einstieg raus, egal welche Note — und
    # danach Ziel und Stop fuer jeden dieser Trades. Die App behauptete „nur ab A−"; der
    # Waechter hielt sich nicht daran. Von 60 abgeschlossenen Signalen waren 50 B oder B+.
    #
    # Jetzt:
    #   EINSTIEG — nur durch das Alarm-Tor (scanner/alarm_tor.py): benanntes Setup, Note,
    #              eigene Bilanz der Setup-Art, Chance-Risiko, Raum nach Kosten, kein
    #              Doppel, hoechstens drei am Tag. Die besten zuerst.
    #   TP / STOP / SCHUTZ / AUSSTIEG — nur fuer Trades, deren Einstieg gemeldet wurde,
    #              und nur, solange die Position laut Plan noch offen ist.
    # Alles andere steht in der App, wo man es nachliest, wenn man hinsieht.
    AUFS_TELEFON = {"EINSTIEG", "TP", "STOP", "SCHUTZ", "AUSSTIEG"}
    from trading_agent.scanner import alarm_tor

    zu_senden, notizen = alarm_tor.fuers_telefon(
        ereignisse,
        liste.wachen,
        raus_vorher=raus_vorher,
        jetzt=jetzt,
        erlaubt=AUFS_TELEFON,
        alle=args.alle_setups,
        archiv=[*archiv, *liste.archiv_neu],
        # Quartalszahlen in den naechsten Tagen: der Scan sperrt den Einstieg (Masterplan §8).
        sperren={
            str(z.get("instrument")): str(z.get("termin_sperre"))
            for z in zeilen
            if z.get("termin_sperre")
        },
    )
    for satz in notizen:
        print(f"  {satz}")
    still = len(ereignisse) - len(zu_senden)

    print(
        f"\n{len(ereignisse)} Ereignis(se)" + (f", davon {still} nur in der App" if still else "")
    )
    for e in ereignisse:
        print(f"\n[{'!' if e.dringend else ' '}] {e.titel}\n{e.text}")

    if args.send and zu_senden:
        # Zwei Wege aufs Telefon, beide unabhaengig voneinander. Web Push braucht keine
        # fremde App und funktioniert auch bei geschlossener Seite; Telegram ist der
        # einfachere Weg, wenn der Bot schon steht. Fehlt beides, sagt der Lauf das
        # deutlich — ein stiller Ausfall ist der schlimmste Fall.
        tg = TelegramSink(min_severity=Severity.INFO)
        push = WebPushSink(min_severity=Severity.INFO)
        # Der Auffangkanal: braucht kein Geheimnis, das jemand erst setzen muss. Er
        # meldet nur das Dringende — ein Issue je Kursbewegung waere Spam.
        gh = GitHubIssueSink(erwaehnen="ozancanerd-ship-it")
        sinks: list[Any] = [FileSink(PROTOKOLL)]
        if tg.available():
            sinks.insert(0, tg)
        if push.available():
            sinks.insert(0, push)
        if gh.available():
            sinks.append(gh)
        if not tg.available() and not push.available():
            print(
                "\n::warning::Kein echter Push-Weg konfiguriert. Entweder VAPID_PRIVATE_KEY + "
                "PUSH_ABOS (Web Push) oder TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID setzen. "
                + (
                    "Dringende Signale gehen solange als GitHub-Issue raus — die kommen "
                    "per Mail und in der GitHub-App an, sind aber langsamer."
                    if gh.available()
                    else "Es geht gerade gar nichts raus."
                )
            )
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
        for satz in push.fehler:
            print(f"::warning::Web Push: {satz}")
        for satz in gh.fehler:
            print(f"::warning::GitHub-Issue: {satz}")

    # Hat sich am Zustand etwas geaendert? Nur dann muss der Stand gesichert werden.
    # Sonst wuerde die CI viermal pro Stunde einen Commit erzeugen, der nichts sagt
    # ausser "ich war hier".
    # Seit 26.09. gehoeren auch „gemeldet" und „raus" dazu: bleibt einer davon ungesichert,
    # kommt beim naechsten leichten Lauf derselbe Alarm noch einmal.
    vorher = {
        k: (
            v.get("zustand"),
            tuple(v.get("erreicht") or []),
            v.get("gemeldet") or "",
            bool(v.get("raus")),
        )
        for k, v in ((stand or {}).get("wachen") or {}).items()
    }
    nachher = {
        k: (w.zustand, tuple(w.erreicht), w.gemeldet, w.raus) for k, w in liste.wachen.items()
    }
    geaendert = vorher != nachher

    entfernt = liste.aufraeumen()
    if entfernt:
        print(f"{entfernt} alte Wache(n) entfernt")
    if liste.archiv_neu and not args.dry_run:
        n = archiv_anhaengen(liste.archiv_neu)
        print(f"{n} abgeschlossene(r) Trade(s) ins Archiv")

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
