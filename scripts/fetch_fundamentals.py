#!/usr/bin/env python3
"""Fundamentaldaten der Einzelaktien von der SEC holen und für die App ablegen.

    python3 scripts/fetch_fundamentals.py --out web/fundamentals.json

WARUM EIN EIGENER SCHRITT UND NICHT IM SCAN

Fundamentaldaten ändern sich an rund vier Tagen im Jahr. Der Scan läuft alle zehn
Minuten. Beides im selben Schritt zu erledigen hieße, hundertfünfzigmal am Tag eine
Antwort zu holen, die sich seit gestern nicht bewegt hat — gegenüber einer Behörde, die
ausdrücklich um Zurückhaltung bittet.

Deshalb: eigener Schritt, einmal am Tag, Ergebnis als Datei. Der Scan liest die Datei.
Fehlt sie oder ist sie alt, läuft der Scan trotzdem — die technische Bewertung hängt
nicht an den Fundamentaldaten, sie wird durch sie nur ergänzt.

**Der Lauf gibt immer 0 zurück.** Eine stumme Behörde darf den Tagesablauf nicht
anhalten; was nicht kam, steht im Protokoll und fehlt einfach in der Datei.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trading_agent.analysis.fundamentals import assess_fundamentals
from trading_agent.data.providers.sec_edgar import (
    STANDARD_AGENT,
    SecEdgar,
    SecNichtVerfuegbar,
)

#: Dieselbe Liste wie im Scanner. Bewusst dupliziert statt importiert: dieses Skript
#: soll auch dann laufen, wenn am Scanner gerade gearbeitet wird.
AKTIEN: list[str] = [
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
    "XOM",
    "CVX",
    "JPM",
    "V",
    "MA",
    "COST",
    "WMT",
    "HD",
    "PG",
    "KO",
    "PEP",
    "DIS",
    "NFLX",
    "BA",
    "CAT",
    "GE",
    "LMT",
]


def _zusammenfassung(kontext: Any, roh: Any) -> dict[str, Any]:
    """Was in die Datei kommt: das Urteil, die Teilnoten und die Rohzahlen dahinter.

    Die Rohzahlen stehen bewusst mit dabei. Ein Urteil ohne die Zahlen, aus denen es
    entstanden ist, lässt sich nicht nachprüfen — und ein Score, den niemand nachprüfen
    kann, wird früher oder später geglaubt statt verstanden.
    """
    d = dict(kontext.as_dict())
    d["stand"] = roh.as_of_report.isoformat()
    d["kennzahlen"] = {
        "kgv": roh.pe,
        "umsatzwachstum_yoy": roh.revenue_growth_yoy,
        "gewinnwachstum_yoy": roh.eps_growth_yoy,
        "rohmarge": roh.gross_margin,
        "operative_marge": roh.operating_margin,
        "eigenkapitalrendite": roh.roe,
        "freier_cashflow_marge": roh.fcf_margin,
        "nettoschulden_zu_ebitda": roh.net_debt_to_ebitda,
        "liquiditaetsgrad": roh.current_ratio,
        "zinsdeckung": roh.interest_coverage,
    }
    return d


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="web/fundamentals.json")
    ap.add_argument("--agent", default=STANDARD_AGENT, help="User-Agent mit Kontakt (SEC-Pflicht)")
    ap.add_argument(
        "--limit", type=int, default=0, help="nur die ersten N Werte (zum Ausprobieren)"
    )
    ap.add_argument("--cache", default="data/cache/sec_edgar")
    args = ap.parse_args()

    symbole = AKTIEN[: args.limit] if args.limit else AKTIEN
    jetzt = datetime.now(UTC)
    quelle = SecEdgar(agent=args.agent, cache_dir=Path(args.cache))

    print(f"SEC EDGAR — {len(symbole)} Werte, Stand {jetzt:%d.%m.%Y %H:%M} UTC")
    try:
        roh = await quelle.kennzahlen(symbole, as_of=jetzt)
    except SecNichtVerfuegbar as exc:
        print(f"::warning::SEC nicht erreichbar: {exc}")
        return 0
    except Exception as exc:
        print(f"::warning::Fundamentaldaten fehlgeschlagen: {type(exc).__name__}: {exc}")
        return 0

    reihen: dict[str, Any] = {}
    for symbol, k in sorted(roh.items()):
        kontext = assess_fundamentals(k, as_of=jetzt)
        reihen[symbol] = _zusammenfassung(kontext, k)

    fehlend = [s for s in symbole if s not in roh]
    doc = {
        "erzeugt": jetzt.isoformat(),
        "quelle": "SEC EDGAR XBRL companyfacts",
        "hinweis": (
            "Originalzahlen aus den Einreichungen, punktgenau gefiltert auf das "
            "Einreichungsdatum. Keine Schaetzungen, keine Analystenerwartungen. "
            "Nur US-Filer."
        ),
        "werte": reihen,
        "ohne_daten": fehlend,
    }
    ziel = Path(args.out)
    ziel.parent.mkdir(parents=True, exist_ok=True)
    ziel.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    stark = sum(1 for v in reihen.values() if v.get("verdict") == "strong")
    schwach = sum(1 for v in reihen.values() if v.get("verdict") == "weak")
    print(f"  {len(reihen)} mit Daten, {len(fehlend)} ohne · {stark} stark, {schwach} schwach")
    for symbol, v in sorted(reihen.items(), key=lambda x: -(x[1].get("composite") or 0))[:8]:
        k = v["kennzahlen"]
        wachstum = k.get("umsatzwachstum_yoy")
        print(
            f"  {symbol:<6} {str(v['verdict']).upper():<7} "
            f"{(v.get('composite') or 0) * 100:>5.0f}/100"
            + (f"  Umsatz {wachstum * 100:+.0f} % ggü. Vorjahr" if wachstum is not None else "")
        )
    if fehlend:
        print(f"  ohne Daten: {', '.join(fehlend)}")
    print(f"\n{ziel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
