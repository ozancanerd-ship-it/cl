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
from trading_agent.data import markt_extras as mx
from trading_agent.data.providers.nasdaq_info import tage_bis
from trading_agent.refdata import zahlen as zahlen_ref
from trading_agent.scanner import alarm_tor
from trading_agent.scanner import erwartung as erw
from trading_agent.scanner import gleichlauf as gl
from trading_agent.scanner.analysis_view import kommentar, mtf_tabelle, zeichnung
from trading_agent.scanner.chart_score import bewerte_chart
from trading_agent.scanner.grading import NOTE_KURZ, NOTEN, Profil
from trading_agent.scanner.halte_stop import halte_werte
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
    # 26.09.: Turbo long auf Mercado Libre im Depot (Screenshot) — der Basiswert stand
    # nicht im Scan, der Schein bekam deshalb keine Bewertung.
    "MELI",
    # 27.09.: Turbo long auf Axon Enterprise im Depot (Knock-out 304,53 $).
    "AXON",
    # Breiteres Aktienuniversum. Vorher waren es 42 Werte — bei der Haelfte dessen,
    # was Ozan bei Trade Republic kaufen kann, stand deshalb 'keine Daten'. Ein
    # Scanner, der nur seine eigene Liste kennt, findet auch nur seine eigene Liste.
    "ORCL",
    "ADBE",
    "INTC",
    "QCOM",
    "TXN",
    "IBM",
    "CSCO",
    "AMAT",
    "LRCX",
    "KLAC",
    "PANW",
    "SNOW",
    "DDOG",
    "NET",
    "ABNB",
    "BKNG",
    # Block hiess an der Boerse bis Januar 2025 SQ, seitdem XYZ. Unter SQ kam bei jedem
    # Lauf nur noch ein Fehler — die Aktie stand still und stumm nicht im Scan.
    "XYZ",
    "PYPL",
    "INTU",
    "ISRG",
    "ABBV",
    "MRK",
    "PFE",
    "TMO",
    "DHR",
    "AMGN",
    "GILD",
    "BMY",
    "VRTX",
    "BAC",
    "GS",
    "MS",
    "BLK",
    "AXP",
    "SCHW",
    "COP",
    "SLB",
    "EOG",
    "FCX",
    "NEM",
    "DE",
    "LMT",
    "RTX",
    "GE",
    "UNP",
    "UPS",
    "COST",
    "HD",
    "NKE",
    "SBUX",
    "PEP",
    "MDLZ",
    "TGT",
    "LOW",
    "MAR",
    "T",
    "VZ",
    "TMUS",
    "CMCSA",
    "SPOT",
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
#: 26.09.: von 150 000 / 300 auf 100 000 / 200 gesenkt. Ozan: „komplett alle Cryptos,
#: Altcoins". Bei Kraken sind das rund 160 statt 140 Paare — alles, worin eine Position
#: von ein paar hundert Euro noch ein Tropfen ist. Darunter wird es duenn: dort ist der
#: eigene Auftrag schon ein spuerbarer Teil des Tagesumsatzes, und der Spread frisst den
#: Plan. Die Warnung „duenn fuer schnelle Ausstiege" steht bei solchen Werten weiter dran.
KRYPTO_MIN_UMSATZ = 100_000.0
KRYPTO_MIN_TRADES = 200

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
    "BTC",
    "ETH",
    "SOL",
    "XRP",
    "ADA",
    "DOGE",
    "LINK",
    "AVAX",
    "DOT",
    "LTC",
    "ARB",
    "OP",
    "INJ",
    "SEI",
    "RENDER",
    "FET",
    "NEAR",
    "ATOM",
    "TIA",
    "SUI",
    "APT",
    "KAS",
    "TAO",
    "UNI",
    "AAVE",
    "FIL",
    "ICP",
    "ETC",
    "BCH",
)

#: Fenster fuer Yahoo: kein natives H4, also muss M5 lang genug sein, damit die
#: MTF-Schicht H4 daraus bilden kann (55 Tage M5 ≈ 330 H4-Kerzen).
FENSTER_YAHOO = {
    Timeframe.M5: timedelta(days=55),
    Timeframe.M15: timedelta(days=55),
    Timeframe.H1: timedelta(days=90),
    Timeframe.D1: timedelta(days=730),
    # Vier Jahre Wochenkerzen — rund 208 Stueck. Ozan: "fuer Aktien reicht nicht nur M, H
    # und D, schau auf Wochen und Jahr." Vier Jahre decken einen kompletten Zyklus ab,
    # ohne dass eine Kursspanne von 2019 die heutige Struktur mitbestimmt.
    Timeframe.W1: timedelta(days=1460),
}
#: Nur Aktien bekommen die Wochenebene. Krypto laeuft durchgehend und in kuerzeren
#: Wellen; dort waere die Woche eine traege Stimme ohne Mehrwert — und sie wuerde die
#: Bewertung aller Coins veraendern, ohne dass dafuer ein Grund vorliegt.
EBENEN_YAHOO = (Timeframe.M5, Timeframe.M15, Timeframe.H1, Timeframe.D1, Timeframe.W1)


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


WACHLISTE = Path("data/repository_real/live/watchlist.json")


def _wachliste_roh() -> dict[str, Any]:
    """Der gespeicherte Zustand der Wachliste — Quelle fuer Trefferquoten UND Alarm-Tor.

    Bewusst aus dem Zustand der Wachliste und nicht aus ``web/performance.json``: die
    Bilanz wird im Tagesablauf **nach** dem Scan gerechnet, die Datei waere also einen
    Lauf alt. Dieselbe Quelle, nur ohne den Umweg.
    """
    from trading_agent.scanner.watchlist import archiv_laden, mit_archiv

    daten: dict[str, Any] = {}
    if WACHLISTE.is_file():
        try:
            roh = json.loads(WACHLISTE.read_text(encoding="utf-8"))
            daten = roh if isinstance(roh, dict) else {}
        except (OSError, json.JSONDecodeError) as exc:
            print(f"  ::warning::Wachliste nicht lesbar: {exc}")
    # Mit Archiv (02.10.): Alarm-Tor und Trefferquoten zaehlen jeden eingegangenen Trade,
    # nicht nur die, deren Wache zufaellig noch auf der Liste steht.
    archiv = archiv_laden()
    if not daten and not archiv:
        return {}
    return mit_archiv(daten, archiv)


def _alarm_stand() -> dict[str, alarm_tor.Stand]:
    """Die Bilanz je Setup-Art fuer das Alarm-Tor — dieselbe, die der Waechter benutzt."""
    wachen = (_wachliste_roh().get("wachen") or {}).values()
    return alarm_tor.bilanz(w for w in wachen if isinstance(w, dict))


def _quotentabelle() -> dict[str, erw.Quote]:
    """Die Trefferhaeufigkeiten aus der eigenen Wachliste."""
    daten = _wachliste_roh()
    if not daten:
        return {}
    from trading_agent.scanner.performance import bericht

    b = bericht(daten)
    tab = erw.quoten([dict(t) for t in b.trades if t.get("gezaehlt", True)])
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
        # Der NAME, nicht das Kuerzel: die Trades der Wachliste tragen den Namen
        # („Rueckeroberung nach Liquiditaetsgriff"), mit „RUECKEROBERUNG" fand sich nie etwas.
        setup=str((setup or {}).get("name") or "") or None,
        klasse=str(r.get("klasse") or "") or None,
        tabelle=tabelle,
    )
    r["erwartung"] = e.as_dict()


#: Ein Vorlauf, der aelter ist, wird nicht uebernommen — dann lieber alles neu scannen.
VORLAUF_MAX_STUNDEN = 3


def _frisch_genug(doc: dict[str, Any]) -> bool:
    try:
        t = datetime.fromisoformat(str(doc.get("erzeugt")))
    except (TypeError, ValueError):
        return False
    return datetime.now(UTC) - t < timedelta(hours=VORLAUF_MAX_STUNDEN)


#: So alt darf eine uebernommene Makrolage hoechstens sein. Der volle Lauf holt sie
#: halbstuendlich; fallen mehrere aus, laeuft Krypto lieber ohne Makro als mit altem.
MAKRO_MAX_STUNDEN = 6


def _makro_frisch(erzeugt: str) -> bool:
    try:
        t = datetime.fromisoformat(str(erzeugt))
    except (TypeError, ValueError):
        return False
    if t.tzinfo is None:
        return False
    return datetime.now(UTC) - t < timedelta(hours=MAKRO_MAX_STUNDEN)


async def _vorlauf_holen(url: str) -> dict[str, Any]:
    """Den zuletzt veroeffentlichten Scan holen.

    WARUM DAS NOETIG IST: ``--nur krypto,gold`` sollte die Aktien aus dem letzten Scan
    uebernehmen. Der liegt aber in ``web/``, und ``web/scan.json`` ist nicht im Repo — in
    der CI war er also nie da. Jeder Zehn-Minuten-Lauf fiel deshalb auf „alles scannen"
    zurueck, OHNE ``--aktien 120``: Aktien mit dem Standarddeckel 40. Sechs von acht
    Laeufen je Stunde kosteten drei Minuten Aktienscan fuer eine Liste, in der 61 der
    101 Aktien fehlten — auch TSMC und Netflix, auf die Ozan Turbos haelt.
    """
    import httpx

    ziel = url.rstrip("/") + "/scan.json"
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as c:
            r = await c.get(ziel, params={"v": datetime.now(UTC).strftime("%H%M%S")})
            r.raise_for_status()
            doc = r.json()
    except Exception as exc:
        print(f"  Vorlauf von {ziel} nicht ladbar: {type(exc).__name__}: {str(exc)[:120]}")
        return {}
    return doc if isinstance(doc, dict) else {}


async def _details_holen(url: str, namen: list[str], ordner: Path) -> int:
    """Detaildateien der uebernommenen Werte von der Seite holen. Gibt die Anzahl zurueck."""
    import httpx

    basis = url.rstrip("/") + "/asset/"
    geholt = 0
    sem = asyncio.Semaphore(8)

    async def eins(c: Any, name: str) -> None:
        nonlocal geholt
        async with sem:
            try:
                r = await c.get(basis + f"{name}.json")
                r.raise_for_status()
                (ordner / f"{name}.json").write_bytes(r.content)
                geholt += 1
            except Exception as exc:
                print(f"    {name}: Detaildatei nicht ladbar ({type(exc).__name__})")

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as c:
        await asyncio.gather(*(eins(c, n) for n in namen))
    return geholt


#: Ab so vielen Kalendertagen vor den Quartalszahlen steht der Termin als Warnung in der
#: Zeile; innerhalb von TERMIN_SPERRE_TAGE gibt es keinen Einstiegs-Alarm (Masterplan §8:
#: kein neuer Trade, wenn wichtige News unmittelbar bevorstehen).
TERMIN_WARNUNG_TAGE = 14
TERMIN_SPERRE_TAGE = 3


def _aktien_info(pfad: str) -> dict[str, Any]:
    p = Path(pfad)
    if not p.exists():
        return {}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return d if isinstance(d, dict) else {}


def _datum_de(iso: str) -> str:
    try:
        d = datetime.fromisoformat(iso).date()
    except ValueError:
        return iso
    tage = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")
    return f"{tage[d.weekday()]} {d.day:02d}.{d.month:02d}."


def aktien_info_anhaengen(r: dict[str, Any], info: dict[str, Any], heute: Any) -> None:
    """Sektor, Kursziel, Analysten und den naechsten Zahlen-Termin an eine Aktienzeile.

    Der Termin wird zur Warnung, wenn er in den naechsten zwei Wochen liegt, und sperrt den
    Einstiegs-Alarm, wenn er in den naechsten drei Tagen liegt: an so einem Tag springt der
    Kurs oft ueber jeden Stop hinweg — das R, mit dem der Plan rechnet, gilt dann nicht.
    """
    if not info:
        return
    kompakt = {
        k: info[k]
        for k in (
            "sektor",
            "branche",
            "kursziel",
            "kursziel_tief",
            "kursziel_hoch",
            "kursziel_3m_pct",
            "analysten",
            "hoch52",
            "tief52",
            "marktwert",
            "dividende_pct",
        )
        if info.get(k) is not None
    }
    z = info.get("zahlen") or {}
    tage = tage_bis(z.get("datum"), heute)
    if tage is not None and tage >= 0:
        kompakt["zahlen"] = {
            **{k: z[k] for k in ("datum", "zeit", "eps_prognose") if z.get(k)},
            "tage": tage,
        }
        # Wie weit diese Aktie an ihren letzten Terminen gesprungen ist (gemessen, 2024–26).
        bew = zahlen_ref.je_aktie(str(r.get("instrument") or ""))
        if bew:
            kompakt["zahlen"]["bewegung"] = bew
        wann = _datum_de(str(z.get("datum")))
        zeit = f", {z['zeit']}" if z.get("zeit") else ""
        if tage <= TERMIN_SPERRE_TAGE:
            r["termin_sperre"] = (
                f"Quartalszahlen {wann}{zeit} — kein neuer Einstieg davor: an so einem Tag "
                "springt der Kurs oft über den Stop"
            )
        if tage <= TERMIN_WARNUNG_TAGE:
            satz = f"Quartalszahlen {wann}{zeit} (in {tage} Tagen) — Kurslücke über Nacht möglich"
            if bew:
                satz += (
                    f"; zuletzt im Mittel ±{bew['median']:.1f} % in zwei Tagen, "
                    f"höchstens {bew['max']:.1f} %"
                ).replace(".", ",")
            w = [x for x in (r.get("warnungen") or []) if not str(x).startswith("Quartalszahlen")]
            r["warnungen"] = [satz, *w]
    r["info"] = kompakt


def sektoren_rechnen(zeilen: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Welche Branchen tragen gerade? Mittel der relativen Staerke je Sektor.

    Nur Sektoren mit mindestens drei gescannten Aktien — mit einer einzigen waere das die
    Staerke dieser einen Aktie, nicht die ihres Sektors.
    """
    je: dict[str, list[dict[str, Any]]] = {}
    for r in zeilen:
        sek = (r.get("info") or {}).get("sektor")
        if r.get("klasse") == "aktien" and sek and r.get("rs") is not None:
            je.setdefault(str(sek), []).append(r)
    aus = []
    for sek, rs in je.items():
        if len(rs) < 3:
            continue
        ren21 = sorted(
            float(x["zusatz"]["renditen"]["r21"])
            for x in rs
            if (x.get("zusatz") or {}).get("renditen", {}).get("r21") is not None
        )
        ren63 = sorted(
            float(x["zusatz"]["renditen"]["r63"])
            for x in rs
            if (x.get("zusatz") or {}).get("renditen", {}).get("r63") is not None
        )
        aus.append(
            {
                "sektor": sek,
                "n": len(rs),
                "rs": round(sum(float(x["rs"]) for x in rs) / len(rs), 1),
                "r21_median": round(ren21[len(ren21) // 2], 2) if ren21 else None,
                "r63_median": round(ren63[len(ren63) // 2], 2) if ren63 else None,
                "fuehrer": [x["instrument"] for x in sorted(rs, key=lambda y: -float(y["rs"]))[:3]],
            }
        )
    aus.sort(key=lambda x: -x["rs"])
    for i, e in enumerate(aus, start=1):
        e["rang"] = i
    rang = {e["sektor"]: (e["rang"], len(aus)) for e in aus}
    for r in zeilen:
        sek = (r.get("info") or {}).get("sektor")
        if sek in rang:
            r["info"]["sektor_rang"], r["info"]["sektor_von"] = rang[sek]
    return aus


async def krypto_extras(zeilen: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Finanzierung und offene Positionen je Coin, dazu Angst & Gier — ein Abruf je Quelle."""
    derivate_roh, fng_roh = await asyncio.gather(
        mx.hole_json(mx.KRAKEN_FUTURES_TICKER), mx.hole_json(mx.ANGST_GIER)
    )
    tabelle = mx.derivate_tabelle(derivate_roh or {})
    coins = []
    for r in zeilen:
        if r.get("klasse") != "krypto":
            continue
        c = mx.muenze(str(r.get("instrument") or ""))
        d = tabelle.get(c)
        if d is None:
            continue
        coins.append(c)
        z = r.setdefault("zusatz", {})
        # Nur Kontext, KEINE Warnung: die vorab registrierte Studie (docs/FUNDING-STUDIE-
        # 2026-09.md) fand keinen Vorhersagewert — hohe Finanzierung lief danach im Mittel
        # nicht schlechter. Was die eigenen Daten nicht hergeben, steht nicht unter
        # „Was dagegen spricht".
        z["derivate"] = d
    stimmung: dict[str, Any] = {"stand": datetime.now(UTC).isoformat()}
    zus = mx.stimmung_zusammenfassen(tabelle, coins)
    if zus:
        stimmung["derivate"] = {**zus, "quelle": "Kraken Futures"}
    fng = mx.angst_gier_aus(fng_roh or {})
    if fng:
        stimmung["angst_gier"] = {**fng, "quelle": "alternative.me"}
    print(
        f"  Terminmarkt: {len(coins)} Coins mit Finanzierung"
        + (f", Median {zus['median_funding_jahr_pct']:+.1f} %/Jahr" if zus else "")
        + (f" · Angst & Gier {fng['wert']:.0f} ({fng['text']})" if fng else "")
    )
    return stimmung if len(stimmung) > 1 else None


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="web", help="Ausgabeordner (scan.json + asset/)")
    ap.add_argument("--krypto", type=int, default=200, help="Deckel fuer das Krypto-Universum")
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
        "--aktien-info",
        default="web/aktien_info.json",
        help="Termine, Sektor, Kursziele aus fetch_aktien_info.py (fehlt sie: ohne)",
    )
    ap.add_argument(
        "--vorlauf-url",
        default="",
        help=(
            "Adresse der veroeffentlichten App. Fehlt bei --nur der lokale Vorlauf "
            "(in der CI immer: web/ liegt nicht im Repo), wird der letzte Scan samt "
            "Detaildateien von dort geholt, statt alles neu zu scannen."
        ),
    )
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
    # Tagesschlusskurse fuer die Gleichlauf-Rechnung. Nur Datum und Kurs, kein
    # Kerzenobjekt — der Lauf ist schon einmal am Speicher gestorben.
    kurse_je: dict[str, dict[Any, float]] = {}
    # Der Halte-Stop je Wert (Chandelier aus den Tageskerzen). Er gehoert in JEDE Zeile,
    # nicht nur in die mit Setup: das Depot braucht einen Stop auch fuer Werte, in denen
    # es laengst drin ist und fuer die heute kein Einstieg ansteht.
    halte_je: dict[str, dict[str, Any]] = {}

    def schreiber(klasse: str) -> Any:
        """Detaildatei sofort schreiben, solange der Kontext noch lebt.

        Der erste Versuch hat alle MTF-Kontexte gesammelt und wurde vom Kernel wegen
        Speichermangels abgeraeumt — ohne Fehlermeldung, der Lauf war einfach weg. Jetzt
        wird je Instrument sofort geschrieben und der Kontext freigegeben.
        """

        def schreibe(chance: Any, mtf: Any) -> None:
            per_tf = dict(getattr(mtf, "per_tf", {}) or {})
            d1 = per_tf.get(Timeframe.D1)
            reihe = gl.sammle(getattr(d1, "bars", ()) if d1 is not None else ())
            if len(reihe) >= gl.MIN_TAGE:
                kurse_je[chance.instrument] = reihe
            with contextlib.suppress(Exception):
                hw = halte_werte(getattr(d1, "bars", ()) if d1 is not None else ())
                if hw is not None:
                    halte_je[chance.instrument] = hw
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
    alt_quelle = ""
    if nur:
        alt = out / "scan.json"
        if alt.exists():
            try:
                alt_doc = json.loads(alt.read_text(encoding="utf-8"))
                alt_quelle = "lokal"
            except (OSError, json.JSONDecodeError):
                alt_doc = {}
        if not alt_doc and args.vorlauf_url:
            alt_doc = await _vorlauf_holen(args.vorlauf_url)
            alt_quelle = "veroeffentlicht" if alt_doc else ""
        if alt_doc and not _frisch_genug(alt_doc):
            print(
                f"  (letzter Scan von {alt_doc.get('erzeugt')} ist aelter als "
                f"{VORLAUF_MAX_STUNDEN} Std. — es wird alles gescannt)"
            )
            alt_doc = {}
        if not alt_doc:
            print("  (kein alter Scan zum Ergaenzen — es wird alles gescannt)")
            nur = set()
        else:
            print(f"  Vorlauf: Scan von {alt_doc.get('erzeugt')} ({alt_quelle})")

    # Die Makrolage aus dem Vorlauf, wenn dieser Lauf keine eigene hat. Bis zum 27.09.
    # holte nur der volle Lauf (:25/:55) die Makrodaten; jeder Zehn-Minuten-Lauf dazwischen
    # bewertete Krypto OHNE Makro und schrieb ``makro: null`` in die App. Dieselbe Münze
    # bekam damit je nach Uhrzeit zwei verschiedene Noten, und die Makro-Anzeige war die
    # meiste Zeit leer. Uebernommen wird nur mit Zeitstempel und nur, solange sie frisch ist.
    if lage is None and alt_doc.get("makro"):
        kandidat = MacroLage.from_dict(alt_doc.get("makro"))
        if kandidat is not None and _makro_frisch(kandidat.erzeugt):
            lage = kandidat
            print(f"  Makrolage aus dem Vorlauf ({kandidat.erzeugt}): {lage.regime.upper()}")

    klassen: dict[str, list[Any]] = {}
    fehler: dict[str, str] = {}
    universum: dict[str, Any] = {}
    uebernommen: dict[str, list[dict[str, Any]]] = {}

    async def ueberspringen(name: str) -> bool:
        if not nur or name in nur:
            return False
        reihen = (alt_doc.get("klassen") or {}).get(name) or []
        # Jede uebernommene Zeile traegt, von wann ihre Analyse ist. Sonst sieht eine
        # Aktienzeile von vor einer halben Stunde in der App aus wie eine von eben.
        for r in reihen:
            r.setdefault("aus_vorlauf", alt_doc.get("erzeugt"))
        uebernommen[name] = reihen
        universum[name] = ((alt_doc.get("universum") or {}).get(name)) or {}
        if reihen:
            print(f"— {name} — aus dem letzten Scan uebernommen ({len(reihen)})")
        if alt_quelle == "veroeffentlicht" and reihen:
            # Die Detaildateien (Chart, Analyse) liegen dann auch nur auf der Seite —
            # ohne sie zeigte die App fuer jede uebernommene Aktie ein leeres Chartfeld.
            vorhanden = set(alt_doc.get("detail_vorhanden") or [])
            namen = [str(r.get("instrument")) for r in reihen if r.get("instrument") in vorhanden]
            geholt = await _details_holen(args.vorlauf_url, namen, ordner)
            print(f"  {geholt} von {len(namen)} Detaildateien von der Seite geholt")
        return True

    print("— gold —", flush=True)
    if not await ueberspringen("gold"):
        chancen, info, err = await _gold(profil, schreiber("gold"), lage)
        klassen["gold"] = chancen
        universum["gold"] = info
        if err:
            fehler["gold"] = err
            print(f"  ! {err}")

    # Krypto und Aktien GLEICHZEITIG. Die beiden haengen an verschiedenen Anbietern mit
    # eigener Ratenbremse (Kraken, Yahoo) und warten fast nur aufs Netz. Nacheinander
    # dauerte ein voller Lauf bei 101 Aktien und 160 Coins rund eine Viertelstunde —
    # laenger als der Abstand zum naechsten Lauf. Nebeneinander so lange wie der
    # langsamere der beiden.
    aufgaben: dict[str, Any] = {}
    print("— krypto —", flush=True)
    if not await ueberspringen("krypto"):
        aufgaben["krypto"] = _krypto(profil, args.krypto, schreiber("krypto"), lage)
    if not args.ohne_aktien:
        print("— aktien —", flush=True)
        if not await ueberspringen("aktien"):
            aufgaben["aktien"] = _aktien(profil, args.aktien, schreiber("aktien"), lage)
    if aufgaben:
        ergebnisse = await asyncio.gather(*aufgaben.values())
        for name, (chancen, info, err) in zip(aufgaben, ergebnisse, strict=True):
            klassen[name] = chancen
            universum[name] = info
            if err:
                fehler[name] = err
                print(f"  ! {name}: {err}")

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
    # Uebernommene Zeilen behalten ihren Halte-Stop aus dem Vorlauf — er haengt an
    # Tageskerzen und aendert sich in einer halben Stunde nicht.
    for r in kompakt_neu:
        hw = halte_je.get(str(r.get("instrument")))
        if hw is not None:
            r["halte"] = hw

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

    # Aktien: Quartalszahlen-Termin, Sektor, Analystenkonsens. Nur fuer frisch gescannte
    # Zeilen — uebernommene bringen ihren Stand aus dem letzten vollen Lauf mit.
    ai = _aktien_info(args.aktien_info)
    ai_werte = ai.get("werte") or {}
    if ai_werte:
        heute = datetime.now(UTC).date()
        n_ai = 0
        for r in kompakt_neu:
            if r.get("klasse") == "aktien" and str(r.get("instrument")) in ai_werte:
                aktien_info_anhaengen(r, ai_werte[str(r.get("instrument"))], heute)
                n_ai += 1
        gesperrt_ai = [str(r.get("instrument")) for r in kompakt_neu if r.get("termin_sperre")]
        print(
            f"  Aktien-Info: {n_ai} Zeilen ergaenzt"
            + (f" · Zahlen in den naechsten Tagen: {', '.join(gesperrt_ai)}" if gesperrt_ai else "")
        )
    sektoren = sektoren_rechnen(kompakt_neu + kompakt_alt)
    if sektoren:
        print("  Sektoren: " + " · ".join(f"{s['sektor']} {s['rs']:.0f}" for s in sektoren[:5]))

    # Krypto: Terminmarkt und Stimmung — nur, wenn Krypto in diesem Lauf gescannt wurde.
    stimmung = None
    if klassen.get("krypto"):
        try:
            stimmung = await krypto_extras(kompakt_neu)
        except Exception as exc:
            print(f"  ::warning::Terminmarkt/Stimmung nicht ladbar: {type(exc).__name__}")
    if stimmung is None and alt_doc.get("stimmung"):
        stimmung = alt_doc.get("stimmung")

    # Das Alarm-Tor — dieselbe Pruefung wie im Waechter (watch_levels.py). Damit steht in
    # der App unter „Jetzt einsteigen" genau das, was auch aufs Handy geht, und bei allem
    # anderen der Grund, warum nicht. Zwei Rechnungen fuer dieselbe Frage hatten wir
    # schon einmal (16.09.), mit einem echten Verkauf als Folge.
    alarm_stand = _alarm_stand()
    for r in kompakt_neu + kompakt_alt:
        if r.get("handelbar") and r.get("einstieg") is not None:
            # pruefe_zeile beachtet auch die Termin-Sperre (Quartalszahlen).
            r["alarm"] = alarm_tor.pruefe_zeile(r, alarm_stand).as_dict()
        else:
            r.pop("alarm", None)
    gesperrt = [
        k for k, v in alarm_stand.items() if k.startswith("setup:") and v.urteil == "gesperrt"
    ]
    if gesperrt:
        print(f"  Alarm-Tor: gesperrt — {', '.join(g.removeprefix('setup:') for g in gesperrt)}")

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
        "stimmung": stimmung,
        "sektoren": sektoren,
        "eurusd": eurusd,
        "alarm_regeln": alarm_tor.regeln_uebersicht(alarm_stand),
        # Uebernommene Klassen gehoeren in BEIDE Listen. Bis zum 26.09. standen sie in
        # keiner — was nie auffiel, weil die Uebernahme in der CI nie geklappt hat (siehe
        # ``--vorlauf-url``): jeder Zehn-Minuten-Lauf hat stattdessen alles neu gescannt,
        # Aktien mit dem Standarddeckel 40 statt 101.
        "klassen": {
            **{
                k: sorted((zeile_je[c.instrument] for c in v), key=_rang)
                for k, v in klassen.items()
            },
            **{k: sorted(v, key=_rang) for k, v in uebernommen.items() if k not in klassen},
        },
        "gesamt": kompakt_alle,
    }

    # Gleichlauf: die gemessenen Tagesrenditen, standardisiert und auf ein Byte je Tag
    # quantisiert. Damit rechnet die App im Browser jede Korrelation selbst — zwischen
    # zwei Depotpositionen und zwischen einer neuen Chance und dem, was schon da ist.
    #
    # Bei einem Teillauf (--nur krypto, alle zehn Minuten) fehlen die Aktien. Einen
    # halben Block zu schreiben waere schlechter als der alte: die App saehe fuer die
    # Aktien gar keinen Gleichlauf mehr und meldete faelschlich ein breites Depot.
    # 90-Tage-Korrelationen aendern sich in einer halben Stunde nicht — also bleibt in
    # dem Fall der Block des letzten vollen Laufs stehen.
    neuer_gl = gl.baue(kurse_je) if kurse_je else None
    alter_gl = alt_doc.get("gleichlauf") if alt_doc else None
    gesamt_universum = len(kompakt_alle) or 1
    # Seit 26.09. klappt die Uebernahme wirklich, und das Krypto-Universum ist groesser:
    # ein reiner Kryptolauf deckt dann ueber 60 % des Universums ab und haette den vollen
    # Block ueberschrieben — ohne Aktien. Also: bei einem Teillauf bleibt der alte Block.
    teillauf = bool(uebernommen)
    if neuer_gl and not (teillauf and alter_gl) and len(neuer_gl["z"]) >= gesamt_universum * 0.6:
        doc["gleichlauf"] = neuer_gl
        print(
            f"Gleichlauf: {len(neuer_gl['z'])} Reihen ueber {neuer_gl['tage']} Handelstage "
            f"bis {neuer_gl['bis']}"
        )
    elif alter_gl:
        doc["gleichlauf"] = alter_gl
        print(
            f"Gleichlauf: aus dem letzten vollen Lauf uebernommen ({len(alter_gl.get('z') or {})})"
        )
    elif neuer_gl:
        doc["gleichlauf"] = neuer_gl
        print(f"Gleichlauf: nur {len(neuer_gl['z'])} Reihen — mehr gibt es gerade nicht")
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
