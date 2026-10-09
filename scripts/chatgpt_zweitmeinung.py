#!/usr/bin/env python3
"""ChatGPT-Zweitmeinung — automatisch, ohne dass Ozan extra fragen muss.

Ozan, 07.10. 23:04: „Ich will, dass er mit dir zusammenarbeitet, ohne dass ich ihn
jedes Mal fragen muss ... ich frag dich immer." Dieses Skript laeuft im selben 24/7-Lauf
wie der Depot-Waechter: es nimmt dieselben Zahlen, die auch in der App stehen (gehaltene
Positionen + Top-Chancen aus dem Scan), schickt sie an die OpenAI-API und schreibt die
Antwort nach ``web/chatgpt_meinung.json`` — die App zeigt sie dann einfach an.

Nur aktiv mit dem Secret ``OPENAI_API_KEY``. Fehlt es, schreibt das Skript eine Datei mit
``aktiv: false`` und bricht NICHT den Lauf ab (``continue-on-error`` im Workflow).

    python3 scripts/chatgpt_zweitmeinung.py --scan web/scan.json --out web/chatgpt_meinung.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from depot_wache import depot_lesen, sync_lesen

from trading_agent.ops.zweitmeinung import (
    baue_prompt,
    hole_zweitmeinung,
    letzter_fehler,
    verfuegbar,
)
from trading_agent.portfolio_intel.depot_stops import ScanIndex, ist_bar, lage_fuer

SYNC = "data/repository_real/live/depot_sync.siegel"


def _zahl(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None  # NaN raus


def positions_kontext(positionen: list[dict[str, Any]], scan: dict[str, Any]) -> list[dict[str, Any]]:
    idx = ScanIndex(scan)
    out: list[dict[str, Any]] = []
    for pos in positionen:
        if not isinstance(pos, dict) or not pos.get("sym") or ist_bar(pos):
            continue
        lg = lage_fuer(pos, idx)
        row = lg.row or {}
        einstieg = _zahl(pos.get("einstieg"))
        kurs_direkt = _zahl(row.get("kurs")) if not pos.get("ko") else None
        gv_pct = (
            round((kurs_direkt / einstieg - 1) * 100, 1)
            if einstieg and kurs_direkt and einstieg > 0
            else None
        )
        plan = pos.get("plan") or {}
        out.append(
            {
                "sym": pos.get("sym"),
                "name": row.get("name") or lg.name,
                "einstieg": einstieg,
                "kurs": kurs_direkt if kurs_direkt is not None else row.get("kurs"),
                "gv_pct": gv_pct,
                "note": row.get("note"),
                "score": row.get("score"),
                "begruendung": row.get("begruendung"),
                "stop": plan.get("stop"),
                "tp1": plan.get("tp1"),
            }
        )
    return out


def chancen_kontext(scan: dict[str, Any], imdepot: set[str], *, n: int = 5) -> list[dict[str, Any]]:
    from trading_agent.portfolio_intel.depot_stops import muenze

    gesamt = scan.get("gesamt") or []
    chancen = [
        o
        for o in gesamt
        if isinstance(o, dict)
        and (o.get("alarm") or {}).get("ja") is True
        and o.get("handelbar")
        and muenze(str(o.get("instrument") or "")) not in imdepot
    ]
    chancen.sort(key=lambda o: o.get("score") or 0, reverse=True)
    return [
        {
            "instrument": c.get("instrument"),
            "name": c.get("name"),
            "score": c.get("score"),
            "note": c.get("note"),
            "richtung": c.get("richtung"),
            "begruendung": c.get("begruendung"),
        }
        for c in chancen[:n]
    ]


def schreibe(pfad: str | Path, inhalt: dict[str, Any]) -> None:
    p = Path(pfad)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(inhalt, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--scan", default="web/scan.json")
    ap.add_argument("--out", default="web/chatgpt_meinung.json")
    ap.add_argument("--sync", default=SYNC)
    args = ap.parse_args()
    jetzt = datetime.now(UTC).isoformat()

    if not verfuegbar():
        print("kein OPENAI_API_KEY hinterlegt — keine Zweitmeinung diesen Lauf.")
        schreibe(args.out, {"aktiv": False, "geprueft": jetzt, "grund": "kein OPENAI_API_KEY"})
        return 0

    roh = os.environ.get("DEPOT_CODE", "").strip()
    sync_geheim = os.environ.get("DEPOT_SCHLUESSEL", "").strip()
    sync_roh = sync_lesen(args.sync, sync_geheim)
    if sync_roh:
        roh = sync_roh
    positionen = depot_lesen(roh) if roh else []

    scan_pfad = Path(args.scan)
    if not scan_pfad.exists():
        print("::warning::kein Scan gefunden — keine Zweitmeinung diesen Lauf.")
        schreibe(args.out, {"aktiv": False, "geprueft": jetzt, "grund": "kein Scan"})
        return 0
    scan = json.loads(scan_pfad.read_text(encoding="utf-8"))

    pos_k = positions_kontext(positionen, scan)
    from trading_agent.portfolio_intel.depot_stops import muenze

    imdepot = {muenze(str(p.get("sym") or "")) for p in positionen if not ist_bar(p)}
    chancen_k = chancen_kontext(scan, imdepot)

    if not pos_k and not chancen_k:
        print("weder Positionen noch Top-Chancen — keine Zweitmeinung diesen Lauf.")
        schreibe(args.out, {"aktiv": False, "geprueft": jetzt, "grund": "nichts zu bewerten"})
        return 0

    prompt = baue_prompt(positionen=pos_k, chancen=chancen_k)
    antwort = hole_zweitmeinung(prompt)
    if antwort is None:
        fehler = letzter_fehler()
        grund = fehler.grund if fehler is not None else "Anfrage fehlgeschlagen oder leer"
        print(f"::warning::OpenAI-Anfrage fehlgeschlagen — kein Fake-Text geschrieben. Grund: {grund}")
        schreibe(args.out, {"aktiv": False, "geprueft": jetzt, "grund": grund})
        return 0

    schreibe(
        args.out,
        {
            "aktiv": True,
            "geprueft": jetzt,
            "erzeugt": antwort.erzeugt,
            "modell": antwort.modell,
            "text": antwort.text,
            # Ozan, 09.10. 10:33: seine Meinung soll direkt auf der Buy-/Sell-Karte
            # stehen — je_position/je_chance sind nach genau demselben Schluessel
            # (sym/instrument) sortiert, den auch die App fuer die jeweilige Karte nutzt.
            "je_position": antwort.je_position,
            "je_chance": antwort.je_chance,
            "bezieht_sich_auf": {"positionen": len(pos_k), "chancen": len(chancen_k)},
        },
    )
    print(f"Zweitmeinung geschrieben ({antwort.modell}, {len(pos_k)} Positionen, {len(chancen_k)} Chancen).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
