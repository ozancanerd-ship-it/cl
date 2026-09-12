#!/usr/bin/env python3
"""Der Gesamtmarkt-Scan — dynamisches Universum, Noten, Muster, Zeichnung.

    python3 scripts/build_scan_data.py --out web

Was hier passiert, in der Reihenfolge:

1. **Universum bilden.** Krypto kommt NICHT aus einer Liste im Code, sondern aus der
   Boerse: alle handelbaren USDT-Paare, durch Liquiditaets- und Qualitaetsfilter, nach
   Umsatz sortiert. Wer heute liquide ist, ist drin. Aktien bleiben eine gepflegte Liste
   (Trade Republic handelbar, keine ETFs), Gold laeuft ueber PAXG/XAUT.
2. **Parallel scannen.** Je Instrument M5/M15/H1/H4/D1, daraus der MTF-Kontext.
3. **Bewerten.** Sechs Chart-Faktoren, dann eine Note aus Score, Chance-Risiko-
   Verhaeltnis und erwarteter Bewegung — A+ bis NO_TRADE, im Profil ``aggressiv``.
4. **Ausschreiben.** Eine kompakte Rangliste (``scan.json``) fuer die Uebersicht, und je
   Instrument eine Detaildatei (``asset/<SYM>.json``) mit Zeichnung, MTF-Tabelle,
   Mustern und Kommentar. Getrennt, weil die App sonst mehrere Megabyte laden muesste,
   bevor sie die erste Zeile zeigt.

**Ein Ausfall wird vermerkt, nicht verschwiegen.** Eine Rangliste ohne Krypto sieht
sonst aus wie ein ruhiger Kryptomarkt.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trading_agent.analysis.macro_context import MacroLage, warnungen_fuer
from trading_agent.core.enums import AssetClass, Timeframe
from trading_agent.scanner import erwartung as erw
from trading_agent.scanner.analysis_view import kommentar, mtf_tabelle, zeichnung
from trading_agent.scanner.chart_score import bewerte_chart
from trading_agent.scanner.grading import NOTE_KURZ, NOTEN, Profil
from trading_agent.scanner.handelbarkeit import beschrifte
from trading_agent.scanner.patterns import muster_ueber_zeitebenen
from trading_agent.scanner.relative_strength import anwenden as rs_anwenden
from trading_agent.scanner.scan_runner import (
    FENSTER,
    handelt_durchgehend,
    scanne,
    schliesse,
)
from trading_agent.scanner.universe import UniversumFilter, hole_universum

#: Einzelaktien ueber Trade Republic handelbar, ueber Sektoren gestreut. Keine ETFs.
AKTIEN = [
    "NVDA",
    "AMD",
    "MSFT",
    "GOOGL",
    "META",
    "AAPL",
    "AMZN",
    "TSLA",
    "PLTR",
    "AVGO",
    "MU",
    "SMCI",
    "ARM",
    "CRWD",
    "NOW",
    "ANET",
    "UBER",
    "SHOP",
    "COIN",
    "MSTR",
    "JNJ",
    "LLY",
    "UNH",
    "NVO",
    "XOM",
    "CVX",
    "JPM",
    "V",
    "MA",
    "PG",
    "KO",
    "WMT",
    "CAT",
    "HON",
    "NEE",
    "LIN",
    "DIS",
    "BA",
    "MCD",
    "CRM",
    # Basiswerte aus dem eigenen Depot: auf TSMC, Netflix und Meta laufen
    # Turbo-Zertifikate bei Trade Republic. Der Schein selbst ist nicht scanbar,
    # der Basiswert schon — und daran haengt, ob der Schein noch Sinn ergibt.
    "TSM",
    "NFLX",
]

#: Gold ueber PAXG: 1:1 physisch hinterlegt und bei Kraken wie bei Bybit handelbar.
#: Der Yahoo-Weg ueber GC=F liefert den Future — ein Signal ohne Ausfuehrungsmoeglichkeit.
GOLD = ["PAXGUSD"]

# DOLLAR ZUERST, EURO NUR ALS NOTLOESUNG.
#
# Ozans Korrektur vom 12. September: „Dollar soll bevorzugt werden … die Krypto kann
# ich im Dollar-Markt kaufen und die sind auch besser." Das stimmt sachlich: die
# USD-/USDT-Maerkte sind bei jeder Boerse die tiefen. Bei Kraken stehen 117 liquide
# USD-Paare gegenueber 76 in Euro, und bei Bybit gibt es Euro praktisch gar nicht.
#
# Euro bleibt als letzte Stufe drin — fuer den Fall, dass einmal weder Bybit noch der
# Kraken-Dollarmarkt erreichbar ist. Dann lieber ein Euro-Signal als gar keins.
#
# Einzelaktien bleiben in Euro: die laufen ueber Trade Republic, und dort gibt es
# nichts anderes.
KRYPTO_QUOTE = "USD"
KRYPTO_MIN_UMSATZ = 150_000.0
KRYPTO_MIN_TRADES = 300

#: Die Euro-Notloesung: gleiche Idee, niedrigere Schwelle, weil der Markt duenner ist.
EUR_MIN_UMSATZ = 50_000.0
EUR_MIN_TRADES = 100

#: Bybit rechnet in USDT und ist deutlich groesser — dort darf die Schwelle hoeher
#: liegen. Die Zahl der Abschluesse liefert Bybit nicht, deshalb faellt diese Pruefung
#: dort weg und wird durch den hoeheren Umsatz ausgeglichen.
BYBIT_MIN_UMSATZ = 3_000_000.0

#: Coins, die IMMER im Universum stehen — unabhaengig davon, wo sie gerade in der
#: Umsatzrangliste liegen.
#:
#: Warum das noetig ist: das Universum wird nach 24-Stunden-Umsatz sortiert und unten
#: gekappt. Ein Coin, der an einem ruhigen Tag unter die Schwelle rutscht, verschwindet
#: damit komplett aus dem Scan — und mit ihm jede Bewertung fuer eine Position, die man
#: darin haelt. Das faellt niemandem auf, weil an der Stelle einfach nichts mehr steht.
#: Fuer einen Wert, in dem Geld liegt, ist „heute kein Umsatz" aber gerade kein Grund,
#: weniger hinzusehen, sondern einer, mehr hinzusehen.
#:
#: Die Auswahl folgt Marktkapitalisierung und Bekanntheit — also dem, was ein Scanner
#: fuer Krypto ohnehin abdecken sollte.
KERN_COINS: tuple[str, ...] = (
    "BTC", "ETH", "SOL", "XRP", "ADA", "DOGE", "LINK", "AVAX", "DOT", "LTC",
    "ARB", "OP", "INJ", "SEI", "RENDER", "FET", "NEAR", "ATOM", "TIA",
    "SUI", "APT", "KAS", "TAO", "UNI", "AAVE", "FIL", "ICP", "ETC", "BCH",
)

#: Fenster fuer Yahoo: kein natives H4, also muss M5 lang genug sein, damit die
#: MTF-Schicht H4 daraus bilden kann (55 Tage M5 ≈ 330 H4-Kerzen).
FENSTER_YAHOO = {
    Timeframe.M5: timedelta(days=55),
    Timeframe.M15: timedelta(days=55),
    Timeframe.H1: timedelta(days=90),
    Timeframe.D1: timedelta(days=730),
}
EBENEN_YAHOO = (Timeframe.M5, Timeframe.M15, Timeframe.H1, Timeframe.D1)


def _nur_krypto(name: str, reihen: dict[Any, Any]) -> str | None:
    """Tokenisierte Aktien und ETFs aus dem Krypto-Ranking halten.

    Die Boerse fuehrt NVDAB, TSLAB, QQQB, SOXLB und Aehnliches als USDT-Paare. Im
    Universum sehen sie aus wie Altcoins. Sie sind aber Aktien- bzw. ETF-Nachbildungen —
    doppeln also den Aktienscan, handeln nur zu Boersenzeiten und schliessen bei den
    gehebelten ETF-Token genau das ein, was hier nicht gehandelt werden soll.

    Erkannt wird das an den Daten, nicht an einer Namensliste: was am Wochenende keine
    Tageskerze hat, ist kein 24/7-Markt.
    """
    from trading_agent.core.enums import Timeframe as _TF

    if not handelt_durchgehend(reihen.get(_TF.D1) or []):
        return "handelt nur zu Boersenzeiten — tokenisierte Aktie oder ETF, kein Coin"
    return None


def _fortschritt(name: str) -> Any:
    def melde(fertig: int, gesamt: int) -> None:
        print(f"    {name}: {fertig}/{gesamt}", flush=True)

    return melde


def _bewerter_mit_makro(lage: MacroLage | None, klasse: str) -> Any:
    """``bewerte_chart``, danach die Makro-Hinweise angehaengt.

    Erst nach der Bewertung, weil die Richtung vorher nicht feststeht: ob „risk-off"
    gegen ein Setup spricht, haengt daran, ob es long oder short ist. Und bewusst als
    Warnung neben dem Score, nicht als Abzug darin — ein heimlicher Punktabzug waere
    nicht nachvollziehbar, und die Makrolage ist ein Zusammenhang, kein Gesetz.
    """

    def bewerte(name: str, mtf: Any, kurs: float, **kw: Any) -> Any:
        chance = bewerte_chart(name, mtf, kurs, **kw)
        if lage is None or chance.richtung is None:
            return chance
        saetze = warnungen_fuer(lage, klasse, chance.richtung.value)
        if not saetze:
            return chance
        return replace(chance, warnungen=tuple(saetze) + tuple(chance.warnungen))

    return bewerte


async def _krypto_quelle(limit: int) -> tuple[Any, str, list[Any], Any]:
    """Bybit versuchen, sonst Kraken. Gibt Anbieter, Name, Universum und Bericht zurueck."""
    from trading_agent.data.providers.bybit_public import BybitPublicDataProvider
    from trading_agent.data.providers.kraken import KrakenDataProvider

    versuche: list[tuple[str, Any, str, float, int]] = [
        ("Bybit", BybitPublicDataProvider(category="spot"), "USDT", BYBIT_MIN_UMSATZ, 0),
        ("Kraken", KrakenDataProvider(), "USD", KRYPTO_MIN_UMSATZ, KRYPTO_MIN_TRADES),
        # Letzte Stufe: Euro. Nur, wenn beide Dollarmaerkte ausfallen.
        ("Kraken (Euro)", KrakenDataProvider(), "EUR", EUR_MIN_UMSATZ, EUR_MIN_TRADES),
    ]
    letzter: Exception | None = None
    for name, prov, quote, min_umsatz, min_trades in versuche:
        try:
            eintraege, bericht = await hole_universum(
                prov,
                UniversumFilter(
                    quote=quote,
                    max_symbole=limit,
                    min_umsatz=min_umsatz,
                    min_trades=min_trades,
                    immer_dabei=tuple(f"{c}{quote}" for c in KERN_COINS),
                ),
            )
            if eintraege:
                print(f"  Quelle: {name} ({quote})")
                return prov, name, eintraege, bericht
            print(f"  {name} lieferte ein leeres Universum — naechste Quelle")
        except Exception as exc:
            letzter = exc
            print(f"  {name} nicht erreichbar: {type(exc).__name__}: {str(exc)[:120]}")
        await schliesse(prov)
    raise RuntimeError(f"keine Krypto-Quelle erreichbar: {letzter}")


async def _krypto(
    profil: Profil, limit: int, verarbeite: Any, lage: MacroLage | None
) -> tuple[list[Any], dict[str, Any], str | None]:
    # Kraken statt Binance — und in Euro.
    #
    # Ozan handelt bei Bybit, Kraken und Trade Republic. Binance ist keins davon. Ein
    # Signal auf ein Paar, das es bei seinen Boersen nicht gibt, ist kein Signal,
    # sondern Arbeit fuer nichts — und genau das war der Scan bisher zu einem guten
    # Teil. Bybit waere die erste Wahl (0,1 % statt 0,4 % Gebuehr), ist aus der CI
    # aber per CloudFront gesperrt (HTTP 403); dieselben Coins liegen dort ohnehin als
    # USDT-Paar. Also: Kraken als Quelle, Euro als Waehrung, Bybit als Alternative
    # beim Ausfuehren.
    # ERST BYBIT, DANN KRAKEN.
    #
    # Ozan kauft die meisten Coins lieber bei Bybit: mehr Paare, und 0,10 % statt
    # 0,40 % Gebuehr — bei einem Swing-Trade mit 4 % erwartetem Gewinn ist das der
    # Unterschied zwischen 5 % und 20 % des Gewinns. Bybit sperrt die API allerdings
    # aus mehreren Laendern per CloudFront (HTTP 403), und aus welchem Land der
    # CI-Laeufer kommt, entscheidet GitHub, nicht wir.
    #
    # Also wird es jedes Mal versucht und nicht einmal vorab entschieden: geht Bybit,
    # kommt das groessere und billigere Universum; geht es nicht, uebernimmt Kraken mit
    # Euro-Paaren. Welcher Weg es war, steht danach im Scan und in der App — eine
    # Ausweichloesung, die niemand sieht, ist eine Falle.
    prov, quelle, eintraege, bericht = await _krypto_quelle(limit)
    try:
        namen = [e.instrument for e in eintraege]
        zusatz = {e.instrument: e.as_dict() for e in eintraege}
        print(
            f"  Universum: {bericht.nach_liquiditaet} liquide von {bericht.gesamt} → {len(namen)}"
        )
        erg = await scanne(
            prov,
            namen,
            asset_class=AssetClass.CRYPTO,
            bewerter=_bewerter_mit_makro(lage, "krypto"),
            zusatz=zusatz,
            profil=profil,
            verarbeite=verarbeite,
            pruefe=_nur_krypto,
            fortschritt=_fortschritt("krypto"),
        )
        info = {
            **bericht.as_dict(),
            "quelle": quelle,
            "dauer_s": erg.dauer_s,
            "ausfaelle": len(erg.ausfaelle),
            "abgelehnt": len(erg.abgelehnt),
        }
        if erg.ausfaelle:
            print(f"  {len(erg.ausfaelle)} Ausfall/Ausfaelle: {list(erg.ausfaelle)[:5]}")
        if erg.abgelehnt:
            print(f"  {len(erg.abgelehnt)} aussortiert: {list(erg.abgelehnt)[:8]}")
        return erg.chancen, info, None
    except Exception as exc:
        return [], {}, f"{type(exc).__name__}: {exc}"
    finally:
        await schliesse(prov)


async def _gold(
    profil: Profil, verarbeite: Any, lage: MacroLage | None
) -> tuple[list[Any], dict[str, Any], str | None]:
    from trading_agent.data.providers.kraken import KrakenDataProvider

    prov = KrakenDataProvider()
    try:
        # Kraken braucht die Paarliste einmal, um BTCUSD -> XXBTZUSD aufloesen zu koennen.
        await prov.list_symbol_info(quote="USD")
        erg = await scanne(
            prov,
            GOLD,
            asset_class=AssetClass.GOLD,
            bewerter=_bewerter_mit_makro(lage, "gold"),
            profil=profil,
            verarbeite=verarbeite,
        )
        return erg.chancen, {"dauer_s": erg.dauer_s, "ausfaelle": len(erg.ausfaelle)}, None
    except Exception as exc:
        return [], {}, f"{type(exc).__name__}: {exc}"
    finally:
        await schliesse(prov)


async def _aktien(
    profil: Profil, limit: int, verarbeite: Any, lage: MacroLage | None
) -> tuple[list[Any], dict[str, Any], str | None]:
    from trading_agent.data.providers.yahoo_finance import YahooFinanceProvider

    prov = YahooFinanceProvider()
    # Ein Limit unter der Listenlaenge schneidet hinten ab — und zwar lautlos. Genau so
    # sind TSM und NFLX aus dem Scan gefallen, kaum dass sie drin standen: die Liste hatte
    # 42 Eintraege, der Lauf holte 40. In der App stand dann bei den Turbos auf TSMC und
    # Netflix weiter „keine Bewertung", ohne dass irgendwo ein Fehler zu sehen war.
    if limit < len(AKTIEN):
        fehlt = ", ".join(AKTIEN[limit:])
        print(f"::warning::Aktienlimit {limit} < {len(AKTIEN)} — nicht gescannt: {fehlt}")
    try:
        erg = await scanne(
            prov,
            AKTIEN[:limit],
            asset_class=AssetClass.EQUITY,
            bewerter=_bewerter_mit_makro(lage, "aktien"),
            zeitebenen=EBENEN_YAHOO,
            fenster={**FENSTER, **FENSTER_YAHOO},
            nebenlaeufig=5,
            profil=profil,
            verarbeite=verarbeite,
            fortschritt=_fortschritt("aktien"),
        )
        if erg.ausfaelle:
            print(f"  {len(erg.ausfaelle)} Ausfall/Ausfaelle: {list(erg.ausfaelle)[:5]}")
        return erg.chancen, {"dauer_s": erg.dauer_s, "ausfaelle": len(erg.ausfaelle)}, None
    except Exception as exc:
        return [], {}, f"{type(exc).__name__}: {exc}"
    finally:
        await schliesse(prov)


def _raeume(ordner: Path, *, behalten: set[str] | None) -> tuple[int, int]:
    """Detaildateien aufraeumen. ``behalten=None`` heisst: alles weg.

    Loeschen kann fehlschlagen (manche Mounts verbieten es). Das darf den Lauf nicht
    kippen — eine ueberzaehlige Datei ist ein Schoenheitsfehler, ein abgebrochener Scan
    nicht. Die Zahl der Blockierten wird zurueckgegeben, damit es sichtbar bleibt.
    """
    entfernt = blockiert = 0
    for datei in ordner.glob("*.json"):
        if behalten is not None and datei.stem in behalten:
            continue
        try:
            datei.unlink()
            entfernt += 1
        except OSError:
            blockiert += 1
    return entfernt, blockiert


#: Reihenfolge in der Rangliste: erst die Note, dann die relative Staerke, dann der
#: Score. Vorher entschied allein der Score — dadurch stand ein B-Setup mit Score 61
#: ueber einem A-Setup mit Score 58, obwohl die Note genau die Zusammenfassung ist,
#: die entscheiden soll.
_NOTE_RANG = {n: i for i, n in enumerate(NOTEN)}


def _rang(r: dict[str, Any]) -> tuple[int, float, float]:
    note = _NOTE_RANG.get(str(r.get("urteil")), len(NOTEN))
    rs = r.get("rs")
    richtung = r.get("richtung")
    # Bei Short zaehlt die umgekehrte Staerke: dort ist der schwaechste Wert der beste.
    staerke = 50.0 if rs is None else (float(rs) if richtung != "short" else 100.0 - float(rs))
    return (note, -staerke, -float(r.get("score") or 0.0))


def _kompakt(chance: Any, klasse: str, muster: list[Any]) -> dict[str, Any]:
    """Die Zeile fuer die Rangliste — alles, was ohne Klick sichtbar sein soll.

    Die Einzelfaktoren mit ihren Begruendungstexten fliegen hier raus: sie machen etwa
    die Haelfte der Datei aus und werden erst in der Detailansicht gebraucht, wo sie
    ohnehin mitkommen. Die Rangliste ist das erste, was geladen wird — sie soll klein sein.
    """
    d = chance.as_dict()
    d.pop("faktoren", None)
    d["klasse"] = klasse
    d["muster"] = [m.as_dict() for m in muster[:2]]
    return d


async def _eurusd() -> float | None:
    """Der Eurokurs, fuer die Umrechnung der Aktienkurse.

    Faellt er aus, gibt es **keinen** Ersatzwert. Ein geschaetzter Wechselkurs waere
    schlimmer als gar keiner: er sieht aus wie eine Angabe, auf die man eine Order
    legen kann.
    """
    try:
        from trading_agent.data.providers.yahoo_finance import YahooFinanceProvider

        prov = YahooFinanceProvider()
        try:
            # Ohne Suffix: der Anbieter bildet die kanonischen Namen selbst auf die
            # Yahoo-Schreibweise ab (EURUSD -> EURUSD=X). Mit "-YFD" kam ein 404, und
            # weil es keinen Ersatzwert gibt, blieben die Aktien stumm in Dollar.
            bars = await prov.fetch_ohlcv(
                "EURUSD",
                Timeframe.D1,
                datetime.now(UTC) - timedelta(days=10),
                datetime.now(UTC),
            )
        finally:
            with contextlib.suppress(Exception):
                await prov.aclose()
        reihe = [b for b in bars if b is not None]
        return float(reihe[-1].close) if reihe else None
    except Exception as exc:
        print(f"  EURUSD nicht ladbar: {type(exc).__name__}: {exc}")
        return None


def _quotentabelle() -> dict[str, erw.Quote]:
    """Die Trefferhaeufigkeiten aus der eigenen Wachliste.

    Bewusst aus dem Zustand der Wachliste und nicht aus ``web/performance.json``: die
    Bilanz wird im Tagesablauf **nach** dem Scan gerechnet, die Datei waere also einen
    Lauf alt. Dieselbe Quelle, nur ohne den Umweg.
    """
    quelle = Path("data/repository_real/live/watchlist.json")
    if not quelle.is_file():
        return {}
    try:
        daten = json.loads(quelle.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"  ::warning::Wachliste fuer Trefferquoten nicht lesbar: {exc}")
        return {}
    from trading_agent.scanner.performance import bericht

    b = bericht(daten)
    tab = erw.quoten([dict(t) for t in b.trades])
    if tab:
        alle = tab.get("alle")
        if alle:
            print(
                f"  Trefferquoten aus {alle.n} abgeschlossenen Signalen "
                f"(TP1 {alle.tp1:.0%}, Stop {alle.stop:.0%})"
            )
    return tab


def _erwartung_anhaengen(r: dict[str, Any], tabelle: dict[str, erw.Quote]) -> None:
    """Haengt ``erwartung`` an eine Zeile — nur dort, wo es einen Plan gibt.

    Ohne Einstieg und Ziel gibt es keine Strecke zu rechnen, und eine Trefferquote ohne
    Trade danebenzustellen waere Deko.
    """
    plan = r.get("plan") if isinstance(r.get("plan"), dict) else None
    einstieg = (plan or {}).get("einstieg") or r.get("einstieg") or r.get("kurs")
    stop = (plan or {}).get("stop") or r.get("invalidierung")
    tp1 = (plan or {}).get("tp1") or r.get("ziel")
    tp3 = (plan or {}).get("tp3") or r.get("tp3")
    if not einstieg or not tp1:
        return
    setup = r.get("setup") if isinstance(r.get("setup"), dict) else None
    e = erw.rechne(
        einstieg=float(einstieg),
        stop=float(stop) if stop else None,
        tp1=float(tp1),
        tp3=float(tp3) if tp3 else None,
        lang=str(r.get("richtung") or "long") == "long",
        note=str(r.get("note") or "") or None,
        setup=str((setup or {}).get("art") or "") or None,
        tabelle=tabelle,
    )
    r["erwartung"] = e.as_dict()


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="web", help="Ausgabeordner (scan.json + asset/)")
    ap.add_argument("--krypto", type=int, default=110, help="Deckel fuer das Krypto-Universum")
    ap.add_argument("--aktien", type=int, default=40)
    ap.add_argument(
        "--profil",
        choices=[p.value for p in Profil],
        default=Profil.AGGRESSIV.value,
        help="Notenschema. Standard: aggressiv (Ozans Vorgabe).",
    )
    ap.add_argument("--detail", type=int, default=60, help="fuer wie viele Werte Detaildateien")
    ap.add_argument("--makro", default="web/macro.json", help="Makrolage aus fetch_macro.py")
    ap.add_argument("--ohne-aktien", action="store_true")
    ap.add_argument(
        "--nur",
        default="",
        help=(
            "Nur diese Klassen scannen (krypto,gold,aktien). Die uebrigen werden aus der "
            "vorhandenen scan.json uebernommen. Damit kann Krypto alle fuenf Minuten "
            "laufen, waehrend Aktien seltener geholt werden — sie bewegen sich ohnehin "
            "nur zu Boersenzeiten."
        ),
    )
    args = ap.parse_args()

    from trading_agent.utils.logging import configure_logging

    configure_logging("WARNING")
    profil = Profil(args.profil)
    t0 = datetime.now(UTC)

    out = Path(args.out)
    ordner = out / "asset"
    ordner.mkdir(parents=True, exist_ok=True)
    _, blockiert = _raeume(ordner, behalten=None)
    if blockiert:
        print(f"  ({blockiert} alte Detaildatei(en) nicht loeschbar — Dateisystem verbietet es)")

    # Makrolage, falls vorhanden. Fehlt sie, laeuft alles wie bisher — nur ohne die
    # Hinweise. Ein Scan darf nicht daran haengen, dass Yahoo gerade schweigt.
    lage = None
    mp = Path(args.makro)
    if mp.exists():
        try:
            lage = MacroLage.from_dict(json.loads(mp.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            lage = None
    if lage is not None:
        print(f"Makrolage: {lage.regime.upper()} ({lage.punkte:+.2f}) — {lage.begruendung[0]}")

    muster_je: dict[str, list[Any]] = {}
    klasse_je: dict[str, str] = {}

    def schreiber(klasse: str) -> Any:
        """Detaildatei sofort schreiben, solange der Kontext noch lebt.

        Der erste Versuch hat alle MTF-Kontexte gesammelt und wurde vom Kernel wegen
        Speichermangels abgeraeumt — ohne Fehlermeldung, der Lauf war einfach weg. Jetzt
        wird je Instrument sofort geschrieben und der Kontext freigegeben.
        """

        def schreibe(chance: Any, mtf: Any) -> None:
            per_tf = dict(getattr(mtf, "per_tf", {}) or {})
            muster = muster_ueber_zeitebenen(per_tf, (Timeframe.D1, Timeframe.H4, Timeframe.H1))
            muster_je[chance.instrument] = muster
            klasse_je[chance.instrument] = klasse
            zeilen = mtf_tabelle(mtf, chance.kurs)
            detail = {
                "erzeugt": datetime.now(UTC).isoformat(),
                "instrument": chance.instrument,
                "klasse": klasse,
                "chance": chance.as_dict(),
                "mtf": zeilen,
                "muster": [m.as_dict() for m in muster],
                "kommentar": kommentar(chance, zeilen, muster, zusatz=chance.zusatz),
                "zeichnung": zeichnung(mtf),
                # Krypto laeuft in der App live ueber die Boerse weiter; Aktien nicht
                # (kein frei zugaengliches Live-Feed mit CORS). Der Unterschied muss
                # sichtbar sein, sonst haelt man einen Stand von vor einer Stunde fuer live.
                "live_quelle": "binance" if klasse in ("krypto", "gold") else None,
            }
            (ordner / f"{chance.instrument}.json").write_text(
                json.dumps(detail, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
            )

        return schreibe

    nur = {t.strip() for t in args.nur.split(",") if t.strip()}
    alt_doc: dict[str, Any] = {}
    if nur:
        alt = out / "scan.json"
        if alt.exists():
            try:
                alt_doc = json.loads(alt.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                alt_doc = {}
        if not alt_doc:
            print("  (kein alter Scan zum Ergaenzen — es wird alles gescannt)")
            nur = set()

    klassen: dict[str, list[Any]] = {}
    fehler: dict[str, str] = {}
    universum: dict[str, Any] = {}
    uebernommen: dict[str, list[dict[str, Any]]] = {}

    def ueberspringen(name: str) -> bool:
        if not nur or name in nur:
            return False
        reihen = (alt_doc.get("klassen") or {}).get(name) or []
        uebernommen[name] = reihen
        universum[name] = ((alt_doc.get("universum") or {}).get(name)) or {}
        if reihen:
            print(f"— {name} — aus dem letzten Scan uebernommen ({len(reihen)})")
        return True

    print("— krypto —", flush=True)
    if not ueberspringen("krypto"):
        chancen, info, err = await _krypto(profil, args.krypto, schreiber("krypto"), lage)
        klassen["krypto"] = chancen
        universum["krypto"] = info
        if err:
            fehler["krypto"] = err
            print(f"  ! {err}")

    print("— gold —", flush=True)
    if not ueberspringen("gold"):
        chancen, info, err = await _gold(profil, schreiber("gold"), lage)
        klassen["gold"] = chancen
        universum["gold"] = info
        if err:
            fehler["gold"] = err
            print(f"  ! {err}")

    if not args.ohne_aktien:
        print("— aktien —", flush=True)
        if not ueberspringen("aktien"):
            chancen, info, err = await _aktien(profil, args.aktien, schreiber("aktien"), lage)
            klassen["aktien"] = chancen
            universum["aktien"] = info
            if err:
                fehler["aktien"] = err
                print(f"  ! {err}")

    alle: list[Any] = [c for liste in klassen.values() for c in liste]
    alle.sort(key=lambda c: -c.score)

    # Detaildateien nur fuer die besten N behalten — der Rest waere totes Gewicht auf
    # der Seite und wird nie angeklickt.
    kompakt_alt_frueh = [r for reihen in uebernommen.values() for r in reihen]
    behalten = {c.instrument for c in alle[: args.detail]}
    # Detaildateien der uebernommenen Klassen bleiben — sie sind nicht neu gebaut
    # worden, waeren aber sonst weg, und die App zeigte fuer diese Werte nichts mehr.
    behalten |= {str(r.get("instrument")) for r in kompakt_alt_frueh if r.get("instrument")}
    _raeume(ordner, behalten=behalten)

    # Die Zeilen werden GENAU EINMAL gebaut und dann ueberall wiederverwendet. Vorher
    # entstanden sie dreimal getrennt (Rangliste, Klassenliste, Gesamtliste) — was
    # bedeutet haette, dass eine nachtraegliche Anpassung wie die relative Staerke nur
    # in einer der drei Listen ankommt.
    zeile_je: dict[str, dict[str, Any]] = {
        c.instrument: _kompakt(c, klasse_je.get(c.instrument, ""), muster_je.get(c.instrument, []))
        for c in alle
    }
    kompakt_neu = [zeile_je[c.instrument] for c in alle]
    kompakt_alt = [r for reihen in uebernommen.values() for r in reihen]

    # Relative Staerke: jede Klasse gegen sich selbst. Muss hier passieren und nicht
    # im Scan — sie braucht alle Werte der Klasse gleichzeitig.
    nach_klasse: dict[str, list[dict[str, Any]]] = {}
    for r in kompakt_neu + kompakt_alt:
        nach_klasse.setdefault(str(r.get("klasse") or "?"), []).append(r)
    rs_anwenden(nach_klasse)

    # Name, Boerse, Waehrung — und bei Aktien der Euro-Preis.
    #
    # Ozan handelt Einzelaktien ueber Trade Republic, dort stehen sie in Euro. Ein
    # Signal mit Dollarkursen ist fuer ihn nicht ausfuehrbar; er muesste jedes Mal
    # selbst umrechnen und vorher nachschlagen, welche Firma hinter dem Kuerzel steckt.
    # Analysiert wird trotzdem die US-Notierung: dort entsteht die Struktur, die
    # deutsche Notierung hat einen Bruchteil des Umsatzes und Luecken im Chart.
    eurusd = await _eurusd()
    if eurusd:
        print(f"  EURUSD {eurusd:.4f} — Aktienkurse zusaetzlich in Euro")
    else:
        print("  ::warning::EURUSD nicht ladbar — Aktien bleiben in Dollar")
    for r in kompakt_neu + kompakt_alt:
        beschrifte(r, eurusd=eurusd)

    # Wie viel es bringen kann — und wie oft so etwas bisher aufging.
    #
    # Die zweite Zahl kommt aus der eigenen Wachliste, nicht aus einem Modell. Sie wird
    # hier angehaengt und NICHT in den Score eingerechnet: sonst wuerde sich das System
    # an seiner eigenen sechs Wochen langen Vergangenheit festbeissen. Faellt die
    # Wachliste aus, bleibt die Karte ohne Quote — mit einem Satz, der das sagt.
    tabelle = _quotentabelle()
    for r in kompakt_neu + kompakt_alt:
        _erwartung_anhaengen(r, tabelle)

    # Welche Coins stehen nur als Kuerzel da? Das Universum ist dynamisch — heute ist
    # ein Coin liquide, morgen ein anderer. Die Namenstabelle laeuft dem hinterher.
    # Statt das stillschweigend hinzunehmen, sagt es der Lauf: dann laesst es sich
    # nachtragen, bevor Ozan wieder suchen muss.
    ohne_namen = sorted(
        {
            str(r.get("instrument"))
            for r in kompakt_neu
            if str(r.get("klasse")) in ("krypto", "gold")
            and str(r.get("name") or "")
            == str(r.get("instrument") or "").upper().removesuffix("EUR").removesuffix("USDT")
        }
    )
    if ohne_namen:
        print(f"  ::warning::ohne ausgeschriebenen Namen: {', '.join(ohne_namen)}")

    kompakt_alle = sorted(kompakt_neu + kompakt_alt, key=_rang)

    statistik = dict.fromkeys(NOTEN, 0)
    for r in kompakt_alle:
        statistik[str(r.get("urteil"))] = statistik.get(str(r.get("urteil")), 0) + 1

    doc = {
        "erzeugt": datetime.now(UTC).isoformat(),
        "profil": profil.value,
        "dauer_s": round((datetime.now(UTC) - t0).total_seconds(), 1),
        "fehler": fehler,
        "universum": universum,
        "anzahl": {
            **{k: len(v) for k, v in klassen.items()},
            **{k: len(v) for k, v in uebernommen.items()},
        },
        "uebernommen": sorted(uebernommen),
        "statistik": statistik,
        "detail_vorhanden": sorted(behalten),
        "makro": lage.as_dict() if lage is not None else None,
        "eurusd": eurusd,
        "klassen": {
            k: sorted((zeile_je[c.instrument] for c in v), key=_rang) for k, v in klassen.items()
        },
        "gesamt": sorted(kompakt_neu, key=_rang),
    }
    (out / "scan.json").write_text(
        json.dumps(doc, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )

    handelbar = [c for c in alle if c.handelbar]
    print(f"\n{len(alle)} Instrumente · {len(handelbar)} handelbar · {doc['dauer_s']:.0f} s")
    print("  " + " · ".join(f"{NOTE_KURZ[n]} {statistik[n]}" for n in NOTEN if statistik[n]))
    for c in alle[:12]:
        rr = f"1:{c.rr:.1f}" if c.rr else "—"
        mv = f"{c.erwartete_bewegung_pct:+.1f} %" if c.erwartete_bewegung_pct else "—"
        seite = (
            "LONG" if c.richtung and c.richtung.value == "long" else "SHORT" if c.richtung else "—"
        )
        print(
            f"  {NOTE_KURZ[c.urteil]:>5}  {c.instrument:<14}{c.score:>6.1f}  "
            f"{klasse_je.get(c.instrument, ''):<7}{seite:<6}{rr:>7}{mv:>9}  conf {c.confidence:.2f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
