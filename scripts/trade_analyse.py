#!/usr/bin/env python3
"""Verlustanalyse der Forward-Paper-Trades (nur Lesen, keine Aenderung am System).

Nimmt dieselben Trades wie die Bilanz (Wachliste + Archiv), haengt die Merkmale aus der
Wache an und schluesselt R nach Setup, Richtung, Klasse, Entry-Art, Stop-Distanz,
Haltedauer, Note, Uhrzeit und Zeitraum auf. Kosten werden als EIGENE Spalte gerechnet
(Annahme je Klasse, steht im Kopf) — die Bilanz im System enthaelt bisher keine.

Aufruf: python scripts/trade_analyse.py [--json out.json]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from trading_agent.scanner.performance import (  # noqa: E402
    ABGESCHLOSSEN,
    _ist_eingestiegen,
    aus_wachliste,
)
from trading_agent.scanner.watchlist import archiv_laden, mit_archiv  # noqa: E402

#: Roundtrip-Kosten je Klasse in Prozent des Einstiegskurses (Gebuehr + Spread + Slippage).
#: ANNAHME, nicht gemessen: Krypto-Taker ~0,10 % je Seite + Spread/Slippage bei duennen
#: Coins; Aktien (Trade Republic) ~1 EUR Fremdkosten + Spread. Absichtlich konservativ-
#: realistisch statt null; die Spalte "netto" zeigt, was uebrig bleibt.
KOSTEN_PCT = {"krypto": 0.35, "aktien": 0.10, "gold": 0.05}


def laden() -> list[dict]:
    wl = json.loads((ROOT / "data/repository_real/live/watchlist.json").read_text())
    voll = mit_archiv(
        wl,
        archiv_laden(
            ROOT / "data/repository_real/archiv/wachen_historie_2026-10-02.json",
            ROOT / "data/repository_real/live/wachen_archiv.jsonl",
        ),
    )
    wachen = voll["wachen"]
    # gleiche Filter wie aus_wachliste -> gleiche Trades wie die Bilanz
    ergebnisse = aus_wachliste(voll)
    schluessel = {(e.instrument, e.begonnen): e for e in ergebnisse}
    zeilen = []
    for w in wachen.values():
        e = schluessel.get((str(w.get("instrument")), str(w.get("aufgenommen") or "")))
        if e is None or not e.gezaehlt:
            continue
        try:
            risiko_pct = abs(float(w["einstieg"]) - float(w["stop"])) / float(w["einstieg"]) * 100
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            risiko_pct = None
        kosten_pct = KOSTEN_PCT.get(e.klasse, 0.35)
        kosten_r = kosten_pct / risiko_pct if risiko_pct else None
        zeilen.append(
            {
                "instrument": e.instrument,
                "klasse": e.klasse,
                "setup": e.setup or "ohne",
                "richtung": w.get("richtung") or "long",
                "einstieg_art": w.get("einstieg_art") or "?",
                "note": e.note,
                "zustand": e.zustand,
                "r": e.r_drittel,
                "r_ganz": e.r_ganz,
                "mfe": e.mfe,
                "mae": e.mae,
                "dauer_h": e.dauer_h,
                "risiko_pct": risiko_pct,
                "erwartet_pct": w.get("erwartet_pct"),
                "rr": w.get("rr"),
                "score": w.get("score"),
                "begonnen": e.begonnen,
                "beendet": e.beendet,
                "gemeldet": e.gemeldet,
                "kosten_r": kosten_r,
                "umsatz": w.get("umsatz_24h"),
            }
        )
    return zeilen


def stat(rows: list[dict], key: str = "r") -> dict:
    v = [x[key] for x in rows if x.get(key) is not None]
    if not v:
        return {"n": 0}
    gew = [x for x in v if x > 0]
    verl = [-x for x in v if x < 0]
    return {
        "n": len(v),
        "summe": round(sum(v), 1),
        "ev": round(sum(v) / len(v), 3),
        "treffer": round(len(gew) / len(v), 3),
        "pf": round(sum(gew) / sum(verl), 2) if verl else None,
    }


def netto(rows: list[dict]) -> dict:
    v = [x["r"] - x["kosten_r"] for x in rows if x.get("kosten_r") is not None]
    if not v:
        return {"n": 0}
    gew = [x for x in v if x > 0]
    verl = [-x for x in v if x < 0]
    return {
        "n": len(v),
        "summe": round(sum(v), 1),
        "ev": round(sum(v) / len(v), 3),
        "pf": round(sum(gew) / sum(verl), 2) if verl else None,
    }


def gruppe(rows: list[dict], f) -> dict:
    g: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        g[str(f(r))].append(r)
    return {
        k: {**stat(v), "netto_ev": netto(v).get("ev")}
        for k, v in sorted(g.items(), key=lambda kv: -len(kv[1]))
    }


def eimer(x, grenzen, fmt="{:g}"):
    if x is None:
        return "?"
    for g in grenzen:
        if x < g:
            return f"<{fmt.format(g)}"
    return f">={fmt.format(grenzen[-1])}"


def woche(r):
    try:
        d = datetime.fromisoformat(r["begonnen"])
        return f"{d.isocalendar().year}-KW{d.isocalendar().week:02d}"
    except ValueError:
        return "?"


def stunde(r):
    try:
        return eimer(datetime.fromisoformat(r["begonnen"]).hour, [6, 12, 18], "{}h")
    except ValueError:
        return "?"


def haupt() -> dict:
    z = laden()
    out = {
        "gesamt": {"brutto": stat(z), "netto": netto(z)},
        "setup": gruppe(z, lambda r: r["setup"]),
        "richtung": gruppe(z, lambda r: r["richtung"]),
        "klasse": gruppe(z, lambda r: r["klasse"]),
        "einstieg_art": gruppe(z, lambda r: r["einstieg_art"]),
        "note": gruppe(z, lambda r: r["note"]),
        "stop_distanz_pct": gruppe(z, lambda r: eimer(r["risiko_pct"], [1, 2, 4, 8], "{}%")),
        "dauer_h": gruppe(z, lambda r: eimer(r["dauer_h"], [6, 24, 72, 168], "{}h")),
        "erwartet_pct": gruppe(z, lambda r: eimer(r["erwartet_pct"], [3, 6, 10], "{}%")),
        "rr_plan": gruppe(z, lambda r: eimer(r["rr"], [1.5, 2, 3], "{}")),
        "score": gruppe(z, lambda r: eimer(r["score"], [40, 50, 60, 70], "{}")),
        "uhrzeit_start": gruppe(z, stunde),
        "woche": dict(sorted(gruppe(z, woche).items())),
        "gemeldet": gruppe(z, lambda r: "alarm" if r["gemeldet"] else "still"),
        "zustand": gruppe(z, lambda r: r["zustand"]),
        "mfe_nie_im_plus": round(sum(1 for r in z if (r["mfe"] or 0) < 0.3) / len(z), 3),
        "kosten_annahme_pct": KOSTEN_PCT,
        "kosten_r_median": sorted(r["kosten_r"] for r in z if r["kosten_r"] is not None)[
            len([r for r in z if r["kosten_r"] is not None]) // 2
        ],
    }
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--json")
    a = ap.parse_args()
    res = haupt()
    txt = json.dumps(res, ensure_ascii=False, indent=1)
    if a.json:
        Path(a.json).write_text(txt)
    else:
        print(txt)
