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
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trading_agent.core.enums import AssetClass, Timeframe
from trading_agent.scanner.analysis_view import kommentar, mtf_tabelle, zeichnung
from trading_agent.scanner.chart_score import bewerte_chart
from trading_agent.scanner.grading import NOTE_KURZ, NOTEN, Profil
from trading_agent.scanner.patterns import muster_ueber_zeitebenen
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
]

#: Gold ueber Binance-Spot: 1:1 physisch hinterlegt und mit Ozans Konten kaufbar.
#: Der Yahoo-Weg ueber GC=F liefert den Future — ein Signal ohne Ausfuehrungsmoeglichkeit.
GOLD = ["PAXGUSDT", "XAUTUSDT"]

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


async def _krypto(
    profil: Profil, limit: int, verarbeite: Any
) -> tuple[list[Any], dict[str, Any], str | None]:
    from trading_agent.data.providers.binance import BinancePublicDataProvider

    prov = BinancePublicDataProvider(market="spot")
    try:
        eintraege, bericht = await hole_universum(prov, UniversumFilter(max_symbole=limit))
        namen = [e.instrument for e in eintraege]
        zusatz = {e.instrument: e.as_dict() for e in eintraege}
        print(
            f"  Universum: {bericht.nach_liquiditaet} liquide von {bericht.gesamt} → {len(namen)}"
        )
        erg = await scanne(
            prov,
            namen,
            asset_class=AssetClass.CRYPTO,
            bewerter=bewerte_chart,
            zusatz=zusatz,
            profil=profil,
            verarbeite=verarbeite,
            pruefe=_nur_krypto,
            fortschritt=_fortschritt("krypto"),
        )
        info = {
            **bericht.as_dict(),
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


async def _gold(profil: Profil, verarbeite: Any) -> tuple[list[Any], dict[str, Any], str | None]:
    from trading_agent.data.providers.binance import BinancePublicDataProvider

    prov = BinancePublicDataProvider(market="spot")
    try:
        erg = await scanne(
            prov,
            GOLD,
            asset_class=AssetClass.GOLD,
            bewerter=bewerte_chart,
            profil=profil,
            verarbeite=verarbeite,
        )
        return erg.chancen, {"dauer_s": erg.dauer_s, "ausfaelle": len(erg.ausfaelle)}, None
    except Exception as exc:
        return [], {}, f"{type(exc).__name__}: {exc}"
    finally:
        await schliesse(prov)


async def _aktien(
    profil: Profil, limit: int, verarbeite: Any
) -> tuple[list[Any], dict[str, Any], str | None]:
    from trading_agent.data.providers.yahoo_finance import YahooFinanceProvider

    prov = YahooFinanceProvider()
    try:
        erg = await scanne(
            prov,
            AKTIEN[:limit],
            asset_class=AssetClass.EQUITY,
            bewerter=bewerte_chart,
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
    ap.add_argument("--ohne-aktien", action="store_true")
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

    klassen: dict[str, list[Any]] = {}
    fehler: dict[str, str] = {}
    universum: dict[str, Any] = {}

    print("— krypto —", flush=True)
    chancen, info, err = await _krypto(profil, args.krypto, schreiber("krypto"))
    klassen["krypto"] = chancen
    universum["krypto"] = info
    if err:
        fehler["krypto"] = err
        print(f"  ! {err}")

    print("— gold —", flush=True)
    chancen, info, err = await _gold(profil, schreiber("gold"))
    klassen["gold"] = chancen
    universum["gold"] = info
    if err:
        fehler["gold"] = err
        print(f"  ! {err}")

    if not args.ohne_aktien:
        print("— aktien —", flush=True)
        chancen, info, err = await _aktien(profil, args.aktien, schreiber("aktien"))
        klassen["aktien"] = chancen
        universum["aktien"] = info
        if err:
            fehler["aktien"] = err
            print(f"  ! {err}")

    alle: list[Any] = [c for liste in klassen.values() for c in liste]
    alle.sort(key=lambda c: -c.score)

    # Detaildateien nur fuer die besten N behalten — der Rest waere totes Gewicht auf
    # der Seite und wird nie angeklickt.
    behalten = {c.instrument for c in alle[: args.detail]}
    _raeume(ordner, behalten=behalten)

    statistik = dict.fromkeys(NOTEN, 0)
    for c in alle:
        statistik[c.urteil] = statistik.get(c.urteil, 0) + 1

    doc = {
        "erzeugt": datetime.now(UTC).isoformat(),
        "profil": profil.value,
        "dauer_s": round((datetime.now(UTC) - t0).total_seconds(), 1),
        "fehler": fehler,
        "universum": universum,
        "anzahl": {k: len(v) for k, v in klassen.items()},
        "statistik": statistik,
        "detail_vorhanden": sorted(behalten),
        "klassen": {
            k: [
                _kompakt(c, k, muster_je.get(c.instrument, []))
                for c in sorted(v, key=lambda x: -x.score)
            ]
            for k, v in klassen.items()
        },
        "gesamt": [
            _kompakt(c, klasse_je.get(c.instrument, ""), muster_je.get(c.instrument, []))
            for c in alle
        ],
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
