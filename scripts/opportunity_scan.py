#!/usr/bin/env python3
"""Gesamtmarkt-Scan: welche Assets haben JETZT den besten Chartzustand?

Der Unterschied zu ``market_scan.py``: dort entscheidet ein fertiges Setup der
eingefrorenen Regel. Solange keines geformt ist — und das ist der Normalfall — bekommen
alle Instrumente fast denselben Wert, und das Ranking sagt nichts.

Hier wird stattdessen der CHARTZUSTAND bewertet (``scanner.chart_score``): Ausrichtung
der Zeitebenen, frischer Strukturbruch, Weg zum naechsten Liquiditaetsziel, offene Zonen,
Momentum, Premium/Discount. Das braucht kein Setup und unterscheidet trotzdem.

Was der Score IST: eine Beschreibung dessen, was am Chart gerade zusammenpasst.
Was er NICHT ist: ein belegter Gewinnhinweis. Die Gewichte sind gesetzt, nicht an
historische Ergebnisse angepasst — das waere Overfitting. Ob ein hoher Chart-Score
tatsaechlich Geld bringt, ist eine eigene Frage und wird getrennt geprueft.

    python3 scripts/opportunity_scan.py --preset krypto
    python3 scripts/opportunity_scan.py --symbols BTCUSDT ETHUSDT --top 5 --json
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
from typing import Any

from trading_agent.core.enums import AssetClass, Direction, Timeframe
from trading_agent.runtime.live_pipeline import (
    LivePipeline,
    LivePipelineConfig,
    build_rest_provider,
)
from trading_agent.scanner.chart_score import bewerte_chart
from trading_agent.strategy.evaluate import EvaluateParams, _build_mtf
from trading_agent.utils.logging import configure_logging

# Handelbar ueber Binance/Kraken/Bybit. Bewusst breit — die Frage ist ja gerade,
# wo etwas passiert, nicht ob BTC heute gut aussieht.
PRESETS: dict[str, list[str]] = {
    "krypto": [
        "BTCUSDT",
        "ETHUSDT",
        "BNBUSDT",
        "SOLUSDT",
        "XRPUSDT",
        "ADAUSDT",
        "LINKUSDT",
        "AVAXUSDT",
        "DOTUSDT",
        "LTCUSDT",
        "TRXUSDT",
        "ATOMUSDT",
        "NEARUSDT",
        "APTUSDT",
        "ARBUSDT",
        "OPUSDT",
        "INJUSDT",
        "SUIUSDT",
        "FILUSDT",
        "UNIUSDT",
        "AAVEUSDT",
        "TIAUSDT",
        "SEIUSDT",
        "FETUSDT",
        "RENDERUSDT",
        "JUPUSDT",
        "TAOUSDT",
        "DOGEUSDT",
    ],
    "gold": ["PAXGUSDT", "XAUTUSDT"],
}


async def _scan(symbols: list[str], exchange: str, asset_class: str) -> list[Any]:
    ac = AssetClass(asset_class)
    cfg = LivePipelineConfig(
        exchange=exchange,
        instruments=tuple(s.upper() for s in symbols),
        asset_class=ac,
        news_gate=False,
    )
    rest = build_rest_provider(exchange)
    pipe = LivePipeline(cfg, rest_provider=rest)
    ergebnis = []
    try:
        await pipe.warmup()
        p = EvaluateParams(asset_class=ac)
        for s in cfg.instruments:
            try:
                bars = pipe._m5.get(s)
                if not bars:
                    print(f"  {s:<12} keine Daten")
                    continue
                mc = pipe._build_context(s, max(b.close_time for b in bars))
                mtf = _build_mtf(mc, p)
                kurs = mc.series[Timeframe.M5][-1].close
                ergebnis.append(bewerte_chart(s, mtf, kurs))
            except Exception as exc:  # eine stumme Reihe darf den Scan nicht kippen
                print(f"  {s:<12} uebersprungen ({type(exc).__name__})")
    finally:
        with contextlib.suppress(Exception):
            await rest.aclose()
    return ergebnis


def _render(chancen: list[Any], top: int) -> None:
    chancen.sort(key=lambda c: -c.score)
    print(f"\n{'═' * 78}")
    print(f"  TOP OPPORTUNITIES  ·  {len(chancen)} Instrumente gescannt")
    print("═" * 78)
    print(
        f"  {'#':<4}{'Instrument':<12}{'Score':>7}{'Richtung':>10}{'Bewegung':>11}{'R:R':>7}   Ziel"
    )
    print("  " + "-" * 74)
    for i, c in enumerate(chancen[:top], 1):
        r = "—" if c.richtung is None else ("LONG" if c.richtung is Direction.LONG else "SHORT")
        bew = f"{c.bewegung_pct:.1f} %" if c.bewegung_pct else "—"
        rr = f"1:{c.rr:.1f}" if c.rr else "—"
        ziel = f"{c.ziel:,.2f} ({c.ziel_art})".replace(",", " ") if c.ziel else "—"
        u = {"A_PLUS": "A+", "A": "A", "WATCH": "WATCH", "NO_TRADE": "—"}[c.urteil]
        print(f"  {i:<4}{c.instrument:<12}{c.score:>6.1f}{u:>10}{r:>10}{bew:>11}{rr:>7}   {ziel}")

    kandidaten = [c for c in chancen if c.urteil in ("A_PLUS", "A")]
    print()
    if kandidaten:
        print(f"  {len(kandidaten)} Kandidat(en) mit ausreichendem Chance-Risiko-Verhaeltnis.")
    else:
        print("  Kein Kandidat: nirgends liegt das naechste Ziel weiter weg als die")
        print("  Invalidierung. NO TRADE ist hier ein Ergebnis, kein Ausfall.")

    print(f"\n{'─' * 78}")
    for c in chancen[: min(3, top)]:
        print(f"\n  {c.instrument}   {c.kurs:,.2f}".replace(",", " ") + f"   ·   {c.headline}")
        for f in c.faktoren:
            n = round(f.anteil * 10)
            balken = "█" * n + "·" * (10 - n)
            print(f"    {f.name:<18} {balken} {f.punkte:>4.1f}/{f.max_punkte:<4.0f} {f.detail}")
        if c.ziel and c.invalidierung:
            print(
                f"    → Ziel {c.ziel:,.2f}".replace(",", " ")
                + f"   Invalidierung {c.invalidierung:,.2f}".replace(",", " ")
                + (f"   R:R 1:{c.rr:.1f}" if c.rr else "")
            )
    print(f"\n{'═' * 78}")
    print("  Der Score beschreibt den Chartzustand. Er ist KEIN belegter Gewinnhinweis —")
    print("  ob daraus Geld folgt, ist eine eigene Frage und noch nicht geprueft.")
    print(f"{'═' * 78}\n")


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--preset", choices=sorted(PRESETS), default=None)
    ap.add_argument("--symbols", nargs="+", default=None)
    ap.add_argument("--exchange", default="binance")
    ap.add_argument("--asset-class", default="crypto")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    configure_logging("WARNING")

    symbols = args.symbols or PRESETS.get(args.preset or "krypto", [])
    chancen = await _scan(symbols, args.exchange, args.asset_class)
    if not chancen:
        print("nichts auswertbar")
        return 1

    if args.json:
        chancen.sort(key=lambda c: -c.score)
        print(json.dumps([c.as_dict() for c in chancen], indent=2, ensure_ascii=False))
        return 0
    _render(chancen, args.top)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
