#!/usr/bin/env python3
"""Die vollstaendige Marktanalyse sichtbar machen — nicht nur das Ergebnis.

WARUM ES DIESES SKRIPT GIBT

Das System berechnet seit Langem alles, was ein Trader anschauen wuerde: Struktur je
Timeframe, BOS/CHoCH, Swing-Punkte, Liquiditaet ueber und unter dem Kurs, Sweeps, Fair
Value Gaps, Order Blocks, Premium/Discount, Momentum, Regime, Konfluenz. Angezeigt wurde
davon fast nichts — nur eine Zeile "NO TRADE".

Damit sah das System aus wie eine Blackbox, die gruen oder rot sagt. Genau der Vorwurf,
und er war berechtigt. Dieses Skript aendert nichts an der Analyse. Es zeigt sie.

WICHTIGE UNTERSCHEIDUNG

Mustererkennung ist nicht dasselbe wie Strategie. Dass das Setup SMC-SWEEP-REV-01 im
OOS-Test keinen Edge hatte, heisst: **diese Handelsregel** wird nicht scharf geschaltet.
Es heisst NICHT, dass Struktur, Liquiditaet und Zonen keine nuetzliche Beschreibung des
Marktes sind. Das Live-Gate regelt Geld, nicht Wahrnehmung — deshalb zeigt dieser Report
die Analyse unabhaengig vom Validierungsstatus und schreibt den Status dazu.

    python3 scripts/trader_analysis.py --symbols BTCUSDT ETHUSDT --asset-class crypto
    python3 scripts/trader_analysis.py --symbols BTCUSDT --json
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import dataclasses as _dc
import json
import sys
from pathlib import Path
from typing import Any

from trading_agent.core.enums import AssetClass, Timeframe
from trading_agent.governance import ValidationRegistry, apply_live_gate
from trading_agent.runtime.live_pipeline import (
    LivePipeline,
    LivePipelineConfig,
    build_rest_provider,
)
from trading_agent.scanner.market_scanner import MarketScanner, ScannerConfig
from trading_agent.strategy.evaluate import EvaluateParams, evaluate
from trading_agent.utils.logging import configure_logging

sys.path.insert(0, str(Path(__file__).resolve().parent))

TF_REIHE = (Timeframe.D1, Timeframe.H4, Timeframe.H1, Timeframe.M15)


def _p(x: float | None, d: int = 2) -> str:
    return "—" if x is None else f"{x:,.{d}f}".replace(",", " ")


def _pct(x: float | None) -> str:
    return "—" if x is None else f"{x * 100:.0f} %"


def _stellen(preis: float) -> int:
    return 0 if preis >= 1000 else 2 if preis >= 10 else 4


# ── Bausteine des Reports ────────────────────────────────────────────────────────
def _struktur_zeile(tfc: Any, d: int) -> str:
    """Eine Zeile je Timeframe: Trend, letzter Bruch, Swing-Zustand."""
    reg = getattr(getattr(tfc, "regime", None), "directional", None)
    trend = getattr(reg, "value", "?") if reg is not None else "?"
    st = getattr(tfc, "structure", None)
    zustand = getattr(getattr(st, "directional", None), "value", "?") if st else "?"

    brueche = list(getattr(tfc, "structure_breaks", ()) or ())
    if brueche:
        b = brueche[-1]
        pfeil = "hoch" if getattr(b.direction, "value", "") == "long" else "runter"
        letzter = (
            f"{getattr(b.kind, 'value', '?').upper()} {pfeil} bei {_p(b.broken_level_price, d)}"
        )
    else:
        letzter = "kein Bruch"
    return f"{trend:<10} {zustand:<14} {letzter}"


def _liquiditaet(tfc: Any, preis: float, d: int) -> tuple[list[str], list[str], list[str]]:
    """Liquiditaet ueber und unter dem Kurs, plus erledigte Sweeps."""
    oben, unten, sweeps = [], [], []
    for lv in getattr(tfc, "liquidity", ()) or ():
        zustand = getattr(getattr(lv, "state", None), "value", "")
        txt = (
            f"{_p(lv.price, d):>12}  {getattr(lv.type, 'value', '?'):<14}"
            f" Staerke {lv.strength:.2f}  {lv.touch_count}x beruehrt"
        )
        if zustand == "swept":
            sweeps.append(f"{_p(lv.price, d)} ({getattr(lv.type, 'value', '?')})")
        elif lv.price > preis:
            oben.append(txt)
        else:
            unten.append(txt)
    oben.sort()
    unten.sort(reverse=True)
    return oben[:4], unten[:4], sweeps[-4:]


# Zonen, die noch etwas bedeuten. "mitigated", "inverted" und "stale" sind erledigt —
# eine zu 100 % gefuellte Luecke von vor 450 Bars ist keine Zone mehr, sondern Rauschen.
_ZONE_OFFEN = ("unmitigated", "partial")


def _zonen(tfc: Any, preis: float, d: int) -> list[str]:
    """Offene FVGs und Order Blocks, nach Naehe zum Kurs — das sind die Wartezonen."""
    kand: list[tuple[float, str]] = []
    for f in getattr(tfc, "fvgs", ()) or ():
        if getattr(getattr(f, "state", None), "value", "") not in _ZONE_OFFEN:
            continue
        richtung = "bullish" if getattr(f.direction, "value", "") == "long" else "bearish"
        mitte = (f.zone_low + f.zone_high) / 2
        wo = "ueber" if mitte > preis else "unter"
        kand.append(
            (
                abs(mitte - preis),
                f"FVG {richtung:<8} {_p(f.zone_low, d)} – {_p(f.zone_high, d)}"
                f"  ({wo} Kurs, {f.fill_fraction:.0%} gefuellt, {f.age_bars} Bars)",
            )
        )
    for b in getattr(tfc, "order_blocks", ()) or ():
        if getattr(getattr(b, "state", None), "value", "") not in _ZONE_OFFEN:
            continue
        richtung = "bullish" if getattr(b.direction, "value", "") == "long" else "bearish"
        mitte = (b.zone_low + b.zone_high) / 2
        wo = "ueber" if mitte > preis else "unter"
        kand.append(
            (
                abs(mitte - preis),
                f"OB  {richtung:<8} {_p(b.zone_low, d)} – {_p(b.zone_high, d)}"
                f"  ({wo} Kurs, {b.age_bars} Bars)",
            )
        )
    kand.sort()
    return [t for _, t in kand[:5]]


def _analysiere(res: Any, opp: Any, preis: float) -> dict[str, Any]:
    """Alles einsammeln, was die Auswertung ohnehin schon berechnet hat."""
    d = _stellen(preis)
    mtf = res.mtf
    dec = res.decision

    tfs = []
    for tf in TF_REIHE:
        tfc = (mtf.per_tf or {}).get(tf)
        if tfc is None:
            continue
        oben, unten, sweeps = _liquiditaet(tfc, preis, d)
        pdp = getattr(tfc, "premium_discount", None)
        tfs.append(
            {
                "tf": tf.value,
                "struktur": _struktur_zeile(tfc, d),
                "atr": getattr(tfc, "atr", None),
                "datenguete": getattr(tfc, "data_confidence", None),
                "liq_oben": oben,
                "liq_unten": unten,
                "sweeps": sweeps,
                "zonen": _zonen(tfc, preis, d),
                "pd": getattr(pdp, "position", None) if pdp else None,
                "pd_zone": getattr(getattr(pdp, "zone", None), "value", None) if pdp else None,
            }
        )

    # Warum kein Trade — die konkreten Gruende, nicht "No-Trade-Gate".
    gruende = []
    for r in getattr(res.no_trade, "records", ()) or ():
        gruende.append(
            {
                "code": getattr(r.reason, "value", str(r.reason)),
                "gruppe": getattr(r.group, "value", str(getattr(r, "group", ""))),
                "detail": r.detail,
            }
        )

    faktoren = []
    if res.score is not None:
        for f in res.score.factors:
            faktoren.append(
                {
                    "name": f.name,
                    "wert": f.value,
                    "gewicht": f.weight,
                    "beitrag": f.contribution,
                    "verfuegbar": f.available,
                    "grund": f.reason,
                }
            )

    konfluenz = []
    if res.confluence is not None:
        for g in res.confluence.groups:
            konfluenz.append(
                {
                    "gruppe": getattr(g.group, "value", str(g.group)),
                    "netto": g.net,
                    "gewicht": g.weight,
                    "anzahl": g.member_count,
                    "notiz": g.note,
                }
            )

    kand = res.candidate
    setup = None
    if kand is not None:
        setup = {
            "typ": kand.setup_type,
            "richtung": getattr(kand.direction, "value", "?"),
            "zustand": getattr(kand.state, "value", "?"),
            "fortschritt": kand.chain_progress,
            "liquiditaet": _p(kand.liquidity.price, d) if kand.liquidity else None,
            "sweep": _p(kand.sweep.penetration_extreme, d) if kand.sweep else None,
            "bruch": _p(kand.structure_break.broken_level_price, d)
            if kand.structure_break
            else None,
            "fvg": (
                f"{_p(kand.entry_fvg.zone_low, d)} – {_p(kand.entry_fvg.zone_high, d)}"
                if kand.entry_fvg
                else None
            ),
            "ob": (
                f"{_p(kand.entry_ob.zone_low, d)} – {_p(kand.entry_ob.zone_high, d)}"
                if kand.entry_ob
                else None
            ),
            "notizen": list(kand.notes or ()),
        }

    lg = res.live_gate
    return {
        "preis": preis,
        "stellen": d,
        "htf_bias": getattr(mtf.htf_bias, "value", "?"),
        "htf_trend": getattr(mtf.htf_directional, "value", "?"),
        "datenguete": mtf.data_confidence,
        "analyseguete": mtf.analysis_confidence,
        "probleme": list(mtf.issues or ()),
        "timeframes": tfs,
        "entscheidung": getattr(dec.decision, "value", "?"),
        "setup_id": dec.setup_id,
        "setup_state": getattr(dec.setup_state, "value", "?"),
        "score": dec.score,
        "konfidenz": dec.confidence,
        "tier": getattr(dec.tier, "value", None) if dec.tier else None,
        "entry": dec.entry,
        "sl": dec.sl,
        "tp1": dec.tp1,
        "tp2": dec.tp2,
        "rr": dec.rr_to_tp2,
        "opportunity": round(getattr(opp, "score", 0.0), 1),
        "opportunity_headline": getattr(opp, "headline", ""),
        "gruende": gruende,
        "score_faktoren": faktoren,
        "konfluenz": konfluenz,
        "setup": setup,
        "validierung": getattr(getattr(lg, "validation_status", None), "value", "?"),
        "handelbar": getattr(getattr(lg, "eligibility", None), "value", "?"),
    }


def _render(sym: str, a: dict[str, Any]) -> None:
    d = a["stellen"]
    print(f"\n{'═' * 72}")
    print(f"  {sym}   {_p(a['preis'], d)}")
    print("═" * 72)

    print("\n  KONTEXT")
    print(f"    HTF-Bias        {a['htf_bias']}  ·  Trend {a['htf_trend']}")
    print(f"    Datenguete      {_pct(a['datenguete'])}  ·  Analyse {_pct(a['analyseguete'])}")
    if a["probleme"]:
        print(f"    Probleme        {'; '.join(a['probleme'][:3])}")

    print("\n  STRUKTUR JE TIMEFRAME")
    print(f"    {'TF':<5}{'Trend':<11}{'Zustand':<15}letzter Bruch")
    for t in a["timeframes"]:
        print(f"    {t['tf']:<5}{t['struktur']}")

    for t in a["timeframes"]:
        hat = t["liq_oben"] or t["liq_unten"] or t["sweeps"] or t["zonen"]
        if not hat:
            continue
        print(f"\n  {t['tf']} — LIQUIDITAET UND ZONEN")
        if t["liq_oben"]:
            print("    ueber dem Kurs (Ziele nach oben):")
            for x in t["liq_oben"]:
                print(f"      {x}")
        if t["liq_unten"]:
            print("    unter dem Kurs (Ziele nach unten / Risiko):")
            for x in t["liq_unten"]:
                print(f"      {x}")
        if t["sweeps"]:
            print(f"    bereits abgeholt: {', '.join(t['sweeps'])}")
        if t["zonen"]:
            print("    offene Zonen:")
            for z in t["zonen"]:
                print(f"      {z}")
        if t["pd"] is not None:
            print(f"    Premium/Discount: {t['pd']:.2f} ({t['pd_zone']})")
        if t["atr"]:
            print(f"    ATR: {_p(t['atr'], d)}")

    if a["setup"]:
        s = a["setup"]
        print(f"\n  SETUP-KANDIDAT  ({s['typ']}, {s['richtung']}, {s['zustand']})")
        print(f"    Kette:          {s['fortschritt']}")
        for k, label in (
            ("liquiditaet", "Ziel-Liquiditaet"),
            ("sweep", "Sweep bei"),
            ("bruch", "Strukturbruch"),
            ("fvg", "Einstiegs-FVG"),
            ("ob", "Einstiegs-OB"),
        ):
            if s.get(k):
                print(f"    {label:<16}{s[k]}")

    print("\n  BEWERTUNG")
    print(
        f"    Opportunity     {a['opportunity']}/100"
        + (f"  ·  {a['opportunity_headline']}" if a["opportunity_headline"] else "")
    )
    if a["score"] is not None:
        print(f"    Setup-Score     {a['score']:.1f}  ·  Konfidenz {_pct(a['konfidenz'])}")
    if a["konfluenz"]:
        teile = [f"{g['gruppe']} {g['netto']:+.2f}" for g in a["konfluenz"] if g["anzahl"]]
        if teile:
            print(f"    Konfluenz       {'  ·  '.join(teile[:6])}")

    kurz = a["gruende"][0]["detail"] if a["gruende"] else ""
    print(
        f"\n  ENTSCHEIDUNG:  {a['entscheidung'].upper().replace('_', ' ')}"
        + (f"   —   {kurz}" if kurz else "")
    )
    if a["entry"]:
        print(
            f"    Entry {_p(a['entry'], d)}  SL {_p(a['sl'], d)}  "
            f"TP1 {_p(a['tp1'], d)}  TP2 {_p(a['tp2'], d)}  R:R {a['rr']}"
        )
    if a["gruende"]:
        print("    Warum nicht:")
        for g in a["gruende"][:6]:
            print(f"      · {g['code']}: {g['detail']}")
    print(
        f"    Regel-Status:   {a['validierung']} → Ausfuehrung {a['handelbar']}"
        + ("  (Analyse gilt trotzdem)" if a["handelbar"] != "live" else "")
    )


async def _lauf(symbols: list[str], exchange: str, asset_class: str, reg: ValidationRegistry):
    ac = AssetClass(asset_class)
    cfg = LivePipelineConfig(
        exchange=exchange,
        instruments=tuple(s.upper() for s in symbols),
        asset_class=ac,
        news_gate=False,
    )
    rest = build_rest_provider(exchange)
    pipe = LivePipeline(cfg, rest_provider=rest)
    contexts: dict[str, Any] = {}
    try:
        await pipe.warmup()
        for s in cfg.instruments:
            with contextlib.suppress(Exception):
                bars = pipe._m5.get(s)
                if not bars:
                    continue
                contexts[s] = pipe._build_context(s, max(b.close_time for b in bars))
    finally:
        with contextlib.suppress(Exception):
            await rest.aclose()

    ep = EvaluateParams(asset_class=ac)
    ep = _dc.replace(
        ep,
        no_trade=_dc.replace(ep.no_trade, require_news_feed=False),
        veto=_dc.replace(ep.veto, require_news_feed=False),
    )
    scanner = MarketScanner(ScannerConfig(asset_class=dict.fromkeys(contexts, asset_class)))

    out = {}
    for s, mc in contexts.items():
        gated = apply_live_gate(evaluate(mc, params=ep), registry=reg)
        opp = scanner.feed(s, gated)
        preis = mc.series[Timeframe.M5][-1].close
        out[s] = _analysiere(gated, opp, preis)
    return out


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbols", nargs="+", default=["BTCUSDT"])
    ap.add_argument("--exchange", default="binance")
    ap.add_argument("--asset-class", default="crypto")
    ap.add_argument("--validation-config", default="config/setup_validation.json")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    configure_logging("WARNING")

    reg = ValidationRegistry.from_file(args.validation_config)
    res = await _lauf(args.symbols, args.exchange, args.asset_class, reg)
    if not res:
        print("keine Daten — Quelle pruefen")
        return 1

    if args.json:
        print(json.dumps(res, indent=2, default=str, ensure_ascii=False))
        return 0
    for sym, a in sorted(res.items(), key=lambda kv: -kv[1]["opportunity"]):
        _render(sym, a)
    print(f"\n{'═' * 72}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
