#!/usr/bin/env python3
"""Makrolage und Wirtschaftskalender holen — Kontext fuer den Scan und die App.

    python3 scripts/fetch_macro.py --out web/macro.json

Zwei Quellen, beide frei und ohne Schluessel:

* **Kennzahlen** ueber Yahoo: VIX, Dollar-Index, 10-jaehrige US-Rendite, S&P 500.
* **Termine** ueber den oeffentlichen Wochenkalender von faireconomy (die Datei, die
  auch ForexFactory ausliefert). Titel, Land, Zeitpunkt, Wirkung.

Faellt eine Quelle aus, wird das vermerkt und der Rest trotzdem geschrieben. Eine
Makrolage ohne Termine ist brauchbar; eine erfundene waere es nicht.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trading_agent.analysis.macro_context import KENNZAHLEN, Termin, baue_werte, bewerte
from trading_agent.core.enums import Timeframe

#: Nur diese eine Datei existiert dort; ``nextweek`` und ``lastweek`` antworten mit 404.
#: Bei haeufigen Abrufen kommt 429 zurueck — deshalb wird der alte Kalender behalten,
#: statt ihn bei jedem Fehlschlag zu verlieren.
KALENDER_URLS = ("https://nfs.faireconomy.media/ff_calendar_thisweek.json",)


async def _reihen() -> tuple[dict[str, list[float]], dict[str, str], list[str]]:
    from trading_agent.data.providers.yahoo_finance import YahooFinanceProvider

    prov = YahooFinanceProvider()
    reihen: dict[str, list[float]] = {}
    stand: dict[str, str] = {}
    fehler: list[str] = []
    jetzt = datetime.now(UTC)
    try:
        for schluessel, (symbol, _) in KENNZAHLEN.items():
            try:
                bars = await prov.fetch_ohlcv(
                    symbol, Timeframe.D1, jetzt - timedelta(days=90), jetzt
                )
                if not bars:
                    fehler.append(f"{symbol}: keine Bars")
                    continue
                reihen[schluessel] = [float(b.close) for b in bars]
                stand[schluessel] = bars[-1].open_time.date().isoformat()
            except Exception as exc:
                fehler.append(f"{symbol}: {type(exc).__name__}")
    finally:
        with contextlib.suppress(Exception):
            await prov.aclose()
    return reihen, stand, fehler


async def _termine() -> tuple[list[Termin], list[str]]:
    import httpx

    aus: list[Termin] = []
    fehler: list[str] = []
    async with httpx.AsyncClient(timeout=20.0) as client:
        for url in KALENDER_URLS:
            try:
                r = await client.get(url)
                r.raise_for_status()
                roh = r.json()
            except Exception as exc:
                fehler.append(f"{url.rsplit('/', 1)[-1]}: {type(exc).__name__}")
                continue
            for e in roh if isinstance(roh, list) else []:
                try:
                    ts = datetime.fromisoformat(str(e["date"]))
                except (KeyError, ValueError, TypeError):
                    continue
                aus.append(
                    Termin(
                        titel=str(e.get("title") or "").strip(),
                        land=str(e.get("country") or "").strip().upper(),
                        zeitpunkt=ts.astimezone(UTC),
                        wirkung=str(e.get("impact") or "").strip(),
                        prognose=str(e.get("forecast") or "").strip(),
                        vorher=str(e.get("previous") or "").strip(),
                    )
                )
    aus.sort(key=lambda t: t.zeitpunkt)
    return aus, fehler


def _alte_termine(pfad: str) -> list[Termin]:
    from trading_agent.analysis.macro_context import MacroLage

    p = Path(pfad)
    if not p.exists():
        return []
    try:
        lage = MacroLage.from_dict(json.loads(p.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        return []
    return list(lage.termine) if lage else []


def _zusammenfuehren(neu: list[Termin], alt: list[Termin]) -> list[Termin]:
    """Neue Termine gewinnen; alte, noch kommende bleiben erhalten.

    Der Kalender wird stuendlich geholt und antwortet dabei regelmaessig mit einem
    Rate-Limit. Ohne diese Zusammenfuehrung waere die Terminliste dann leer — und eine
    leere Liste sieht aus wie "keine wichtigen Termine", was das Gegenteil der Wahrheit
    sein kann.
    """
    jetzt = datetime.now(UTC)
    nach_schluessel: dict[tuple[str, str], Termin] = {}
    for t in alt:
        if t.zeitpunkt >= jetzt - timedelta(days=2):
            nach_schluessel[(t.titel, t.zeitpunkt.isoformat())] = t
    for t in neu:
        nach_schluessel[(t.titel, t.zeitpunkt.isoformat())] = t
    return sorted(nach_schluessel.values(), key=lambda t: t.zeitpunkt)


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="web/macro.json")
    ap.add_argument("--ohne-kalender", action="store_true")
    ap.add_argument(
        "--alt",
        default="",
        help="bisherige macro.json — ihre noch kommenden Termine werden uebernommen, "
        "damit ein Rate-Limit den Kalender nicht loescht",
    )
    args = ap.parse_args()

    from trading_agent.utils.logging import configure_logging

    configure_logging("WARNING")

    reihen, stand, fehler = await _reihen()
    termine: list[Termin] = []
    if not args.ohne_kalender:
        termine, kfehler = await _termine()
        fehler += kfehler
    termine = _zusammenfuehren(termine, _alte_termine(args.alt or args.out))

    werte = baue_werte(reihen, stand)
    if not werte:
        print("::warning::Keine Makro-Kennzahlen erreichbar — keine Datei geschrieben")
        for f in fehler:
            print(f"  ! {f}")
        return 0

    lage = bewerte(werte, termine)
    doc = lage.as_dict()
    doc["fehler"] = fehler

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    print(f"Makrolage: {lage.regime.upper()}  ({lage.punkte:+.2f})")
    for g in lage.begruendung:
        print(f"  · {g}")
    for w in lage.werte:
        v5 = f"{w.aenderung_5t_pct:+.1f} %" if w.aenderung_5t_pct is not None else "—"
        v20 = f"{w.aenderung_20t_pct:+.1f} %" if w.aenderung_20t_pct is not None else "—"
        print(f"  {w.name:<28} {w.wert:>10.2f}   5T {v5:>8}   20T {v20:>8}")
    hoch = [t for t in lage.naechste_termine(stunden=72)]
    print(f"\n{len(termine)} Termine geladen, {len(hoch)} wichtige in den naechsten 72 h")
    for t in hoch[:6]:
        print(f"  {t.zeitpunkt:%d.%m. %H:%M} UTC  {t.land:<4} {t.titel}")
    if fehler:
        print("\nAusfaelle:")
        for f in fehler:
            print(f"  ! {f}")
    print(f"\n{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
