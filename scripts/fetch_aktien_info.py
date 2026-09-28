#!/usr/bin/env python3
"""Quartalszahlen-Termine, Sektor und Analystenkonsens fuer die Aktien im Scan.

    python3 scripts/fetch_aktien_info.py --out web/aktien_info.json \\
        --vorlauf-url https://ozancanerd-ship-it.github.io/cl

EINMAL AM TAG, NICHT BEI JEDEM LAUF

Termine und Kursziele aendern sich ueber Tage, nicht ueber Minuten. Der volle Scan laeuft
zweimal je Stunde; bei jedem Lauf rund 250 Anfragen an Nasdaq zu schicken waere unhoeflich
und wuerde frueher oder später gesperrt. Deshalb: liegt auf der veroeffentlichten Seite
eine Datei, die juenger als ``--max-stunden`` ist, wird sie uebernommen und nichts geholt.

Faellt Nasdaq aus, bleibt die letzte gute Datei stehen (bis sieben Tage), mit Vermerk.
Ein veralteter Termin ist besser als gar keiner — solange er als veraltet dasteht.

**Der Lauf gibt immer 0 zurueck.** Ein stummer Anbieter darf den Scan nicht anhalten.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trading_agent.data.providers.nasdaq_info import (
    BASIS,
    KOPF,
    kalender_zeilen,
    kursziele,
    zusammenfassung,
)

#: So weit reicht der Terminkalender nach vorn (Werktage). Neun Wochen decken die
#: laengste uebliche Haltedauer (Aktien) mehrfach ab.
WERKTAGE_VORAUS = 45


def _aktien() -> list[str]:
    from build_scan_data import AKTIEN

    return list(dict.fromkeys(AKTIEN))


def _alter_h(doc: dict[str, Any]) -> float | None:
    try:
        t = datetime.fromisoformat(str(doc.get("erzeugt")))
    except (TypeError, ValueError):
        return None
    if t.tzinfo is None:
        return None
    return (datetime.now(UTC) - t).total_seconds() / 3600


async def _hole(client: Any, url: str, versuche: int = 3) -> dict[str, Any] | None:
    for i in range(versuche):
        try:
            r = await client.get(url, headers=KOPF)
            if r.status_code == 200:
                d = r.json()
                return d if isinstance(d, dict) else None
            if r.status_code in (403, 404):
                return None
        except Exception:
            pass
        await asyncio.sleep(1.5 * (i + 1))
    return None


async def _vorlauf(url: str) -> dict[str, Any]:
    import httpx

    if not url:
        return {}
    try:
        async with httpx.AsyncClient(timeout=20.0) as c:
            r = await c.get(url.rstrip("/") + "/aktien_info.json")
            if r.status_code == 200:
                d = r.json()
                return d if isinstance(d, dict) else {}
    except Exception:
        return {}
    return {}


async def sammle(aktien: list[str], *, heute: date, nebenlaeufig: int = 4) -> dict[str, Any]:
    import httpx

    werte: dict[str, dict[str, Any]] = {a: {} for a in aktien}
    wanted = set(aktien)
    sem = asyncio.Semaphore(nebenlaeufig)
    fehler = 0
    # HTTP/1.1 — ueber HTTP/2 bricht der Server die Verbindung ab.
    async with httpx.AsyncClient(timeout=30.0, http2=False) as client:

        async def tag(d: date) -> list[dict[str, Any]]:
            nonlocal fehler
            async with sem:
                a = await _hole(client, f"{BASIS}/calendar/earnings?date={d.isoformat()}")
                if a is None:
                    fehler += 1
                return kalender_zeilen(a, d)

        async def eins(sym: str) -> None:
            nonlocal fehler
            async with sem:
                s = await _hole(client, f"{BASIS}/quote/{sym}/summary?assetclass=stocks")
                k = await _hole(client, f"{BASIS}/analyst/{sym}/targetprice")
            if s is None and k is None:
                fehler += 1
            werte[sym].update(zusammenfassung(s))
            werte[sym].update(kursziele(k))

        tage: list[date] = []
        d = heute
        while len(tage) < WERKTAGE_VORAUS:
            if d.weekday() < 5:
                tage.append(d)
            d += timedelta(days=1)
        kalender = await asyncio.gather(*(tag(t) for t in tage))
        for zeilen in kalender:
            for z in zeilen:
                sym = z.pop("symbol")
                if sym in wanted and "zahlen" not in werte[sym]:
                    werte[sym]["zahlen"] = z
        await asyncio.gather(*(eins(s) for s in aktien))
    return {
        "werte": {k: v for k, v in werte.items() if v},
        "fehler": fehler,
        "anfragen": len(tage) + 2 * len(aktien),
    }


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="web/aktien_info.json")
    ap.add_argument("--vorlauf-url", default="")
    ap.add_argument("--max-stunden", type=float, default=20.0)
    ap.add_argument("--erzwingen", action="store_true", help="auch bei frischer Datei neu holen")
    args = ap.parse_args()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    alt = await _vorlauf(args.vorlauf_url)
    if not alt and out.exists():
        try:
            alt = json.loads(out.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            alt = {}
    alter = _alter_h(alt) if alt else None
    if alt.get("werte") and alter is not None and alter < args.max_stunden and not args.erzwingen:
        out.write_text(json.dumps(alt, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        print(
            f"Aktien-Info vom Vorlauf uebernommen ({alter:.1f} Std. alt, "
            f"{len(alt['werte'])} Werte) — nichts geholt."
        )
        return 0

    aktien = _aktien()
    heute = datetime.now(UTC).date()
    print(f"Nasdaq: {len(aktien)} Aktien, Termine {WERKTAGE_VORAUS} Werktage voraus …", flush=True)
    try:
        erg = await sammle(aktien, heute=heute)
    except Exception as exc:
        print(f"::warning::Nasdaq fehlgeschlagen: {type(exc).__name__}: {exc}")
        erg = {"werte": {}, "fehler": -1, "anfragen": 0}

    werte = erg["werte"]
    mit_termin = sum(1 for v in werte.values() if v.get("zahlen"))
    mit_sektor = sum(1 for v in werte.values() if v.get("sektor"))
    print(
        f"  {len(werte)} Werte · {mit_termin} mit Termin · {mit_sektor} mit Sektor · "
        f"{erg['fehler']} Fehlschlaege bei {erg['anfragen']} Anfragen"
    )

    if mit_sektor < len(aktien) * 0.5:
        # Zu duenn — vermutlich gesperrt. Dann lieber die letzte gute Datei behalten.
        if alt.get("werte") and alter is not None and alter < 24 * 7:
            alt["veraltet"] = True
            out.write_text(
                json.dumps(alt, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
            )
            print(f"::warning::Nasdaq lieferte zu wenig — letzte Datei ({alter:.0f} Std.) bleibt")
            return 0
        if not werte:
            print("::warning::Keine Aktien-Info — der Scan laeuft ohne Termine und Sektoren")
            return 0

    doc = {
        "erzeugt": datetime.now(UTC).isoformat(),
        "quelle": "Nasdaq (Termine von Zacks, teils geschätzt; Analystenkonsens)",
        "werte": werte,
    }
    out.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    naechste = sorted(
        (
            (v["zahlen"]["datum"], k, v["zahlen"].get("zeit") or "")
            for k, v in werte.items()
            if v.get("zahlen")
        ),
    )[:10]
    for d, k, z in naechste:
        print(f"  {d}  {k:<6} {z}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
