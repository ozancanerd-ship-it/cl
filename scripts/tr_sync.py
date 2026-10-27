#!/usr/bin/env python3
"""Trade-Republic-Depot lesend abgleichen (inoffizielle pytr-Schnittstelle, KEINE Orders).

Ablauf auf DEINEM Rechner (der Login ist deine Sache — PIN und Handy-Bestaetigung gibt nie
ein Dritter ein):

    uv tool install pytr          # oder: pipx install pytr
    pytr login --v2 --store_credentials
    python3 scripts/tr_sync.py --pull          # holt das Depot und schreibt den Import-Text

``--csv datei.csv`` liest eine vorhandene ``pytr portfolio -o``-Datei (Semikolon,
Spalten Name;ISIN;quantity;price;avgCost;netValue). Das Ergebnis ist der Depot-Text fuer
App -> Sync -> Depot-Text einfuegen -> Abgleichen. Er nennt Trade Republic als vollstaendig
beschriebenes Konto: was dort fehlt, wird in der App als verkauft ANGEZEIGT, nie still
geloescht. Manuelle Plaene (Stop, Ziele) bleiben beim Zusammenfuehren erhalten.

Grenzen (nicht belegt = nicht behauptet): Das Format ist das von ``pytr portfolio``, wie
im Quelltext gelesen; Basiswert und Knock-out-Schwelle von Scheinen liefert es nicht.
Die Sitzung laeuft irgendwann ab; dann bricht dieses Skript mit klarer Meldung ab.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path

KONTO = "Trade Republic"
#: Nur Zuordnungen, die ich sicher kenne. Unbekannte ISIN -> Kuerzel "TR-<ISIN>".
ISIN_KUERZEL = {
    "US67066G1040": "NVDA",
    "US69608A1088": "PLTR",
    "US0378331005": "AAPL",
    "US5949181045": "MSFT",
    "US0231351067": "AMZN",
    "US30303M1027": "META",
    "US02079K3059": "GOOGL",
    "US88160R1014": "TSLA",
    "US64110L1061": "NFLX",
    "US0079031078": "AMD",
}
SPALTEN = ["Name", "ISIN", "quantity", "price", "avgCost", "netValue"]


def _dec(s: str) -> Decimal | None:
    try:
        return Decimal(str(s).strip().replace(",", ".")) if str(s).strip() else None
    except InvalidOperation:
        return None


def parse_csv(text: str) -> list[dict]:
    """pytr-Portfolio-CSV -> Positionsliste. Zeilen mit unlesbaren Zahlen werden NICHT
    erraten, sondern uebersprungen und gemeldet (siehe ``fehler``)."""
    zeilen = list(csv.reader(text.splitlines(), delimiter=";"))
    if not zeilen or [c.strip() for c in zeilen[0]] != SPALTEN:
        raise ValueError(f"unerwarteter Kopf {zeilen[0] if zeilen else '(leer)'} — erwartet {SPALTEN}")
    aus: list[dict] = []
    for z in zeilen[1:]:
        if not z or not "".join(z).strip():
            continue
        if len(z) != 6:
            raise ValueError(f"Zeile mit {len(z)} statt 6 Spalten: {z}")
        name, isin, qty, price, avg, wert = (c.strip() for c in z)
        q, p, a, w = _dec(qty), _dec(price), _dec(avg), _dec(wert)
        if None in (q, p, a, w) or q <= 0:
            raise ValueError(f"Zahlen nicht lesbar/ungueltig fuer {isin or name}: {z}")
        aus.append({"name": name, "isin": isin, "menge": q, "kurs": p, "einstand": a, "wert": w})
    return aus


def depot_text(pos: list[dict], jetzt_ms: int | None = None, quelle: str = "pytr") -> str:
    p = []
    for x in pos:
        sym = ISIN_KUERZEL.get(x["isin"], f"TR-{x['isin']}")
        p.append(
            {
                "sym": sym,
                "isin": x["isin"],
                "menge": float(x["menge"]),
                "einstieg": float(x["einstand"]),
                "wert_manuell": float(x["wert"]),
                "konto": KONTO,
                "platzhalter": False,
                "notiz": f"{x['name']} (aus {quelle}, Basiswert/Knock-out nicht enthalten)",
            }
        )
    doc = {"v": 1, "t": jetzt_ms if jetzt_ms is not None else int(time.time() * 1000),
           "q": f"Trade Republic {quelle}", "k": [KONTO], "p": p}
    return json.dumps(doc, ensure_ascii=False)


def pull(pytr: str | None = None) -> str:
    exe = pytr or os.environ.get("PYTR") or shutil.which("pytr")
    if not exe:
        raise SystemExit("pytr nicht gefunden. Installieren: uv tool install pytr (oder pipx install pytr)")
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "portfolio.csv"
        r = subprocess.run([exe, "portfolio", "--v2", "-o", str(out)], capture_output=True, text=True, timeout=300)
        if r.returncode != 0 or not out.exists():
            raise SystemExit(
                "Abruf fehlgeschlagen — vermutlich Sitzung abgelaufen. Neu anmelden: "
                "pytr login --v2 --store_credentials\n" + (r.stderr or r.stdout)[-400:]
            )
        return out.read_text(encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--csv")
    g.add_argument("--pull", action="store_true")
    ap.add_argument("--leer-erlauben", action="store_true", help="auch ein leeres Depot melden (sonst Abbruch)")
    ap.add_argument("--out", help="Import-Text in diese Datei schreiben (sonst Ausgabe)")
    a = ap.parse_args()
    text = pull() if a.pull else Path(a.csv).read_text(encoding="utf-8")
    pos = parse_csv(text)
    if not pos and not a.leer_erlauben:
        raise SystemExit("Depot leer — vermutlich Abruffehler; mit --leer-erlauben trotzdem melden")
    res = depot_text(pos)
    if a.out:
        Path(a.out).write_text(res, encoding="utf-8")
        print(f"{len(pos)} Positionen -> {a.out}")
    else:
        print(res)
    print(f"{len(pos)} Positionen, Summe {sum(x['wert'] for x in pos)} EUR", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
