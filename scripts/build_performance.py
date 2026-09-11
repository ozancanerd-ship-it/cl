#!/usr/bin/env python3
"""Die eigene Trefferbilanz ausrechnen und für die App ablegen.

    python3 scripts/build_performance.py

Liest den Zustand der Wachliste und schreibt ``web/performance.json``. Läuft in jedem
vollen Lauf mit, direkt nach der Wachliste — die Auswertung soll nicht davon abhängen,
dass jemand sie anstößt.

WARUM DAS EIN FESTER SCHRITT IST

Ein Scanner, der jeden Tag neue Chancen ausruft und nie nachrechnet, ob die alten
aufgegangen sind, erzeugt Zuversicht statt Wissen. Der erste Durchlauf über die echten
Daten ergab **−23,5 R über 58 abgeschlossene Signale**, bei genau einem Treffer. Diese
Zahl war die ganze Zeit auf der Platte und hat niemand angesehen. Deshalb steht sie
jetzt in jedem Lauf und in der App.

Der Lauf gibt immer 0 zurück: eine fehlende oder kaputte Wachliste darf den Tagesablauf
nicht anhalten.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trading_agent.scanner import erwartung as erw
from trading_agent.scanner.performance import bericht

STAND = "data/repository_real/live/watchlist.json"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--stand", default=STAND, help="Zustand der Wachliste")
    ap.add_argument("--out", default="web/performance.json")
    args = ap.parse_args()

    quelle = Path(args.stand)
    daten = None
    if quelle.is_file():
        try:
            daten = json.loads(quelle.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"::warning::Wachliste nicht lesbar: {exc}")

    b = bericht(daten, jetzt=datetime.now(UTC))

    # Die Trefferhaeufigkeiten kommen mit in die Datei — dieselbe Tabelle, die im Scan
    # neben jedem Signal steht. Damit laesst sich in der App nachschlagen, worauf die
    # Quote auf einer Signalkarte beruht, statt sie glauben zu muessen.
    doc = b.as_dict()
    doc["quoten"] = {k: q.as_dict() for k, q in erw.quoten([dict(t) for t in b.trades]).items()}

    ziel = Path(args.out)
    ziel.parent.mkdir(parents=True, exist_ok=True)
    ziel.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    ganz = b.je_regel["ganz"]
    drittel = b.je_regel["drittel"]
    print(f"{b.abgeschlossen} abgeschlossen · {b.offen} offen")
    if b.abgeschlossen:
        print(
            f"  alles-oder-nichts   {ganz.summe_r:+7.2f} R"
            f"  ·  {ganz.erwartungswert:+.3f} R je Trade"
            f"  ·  Treffer {(ganz.trefferquote or 0) * 100:.0f} %"
        )
        print(
            f"  mit Teilverkauf     {drittel.summe_r:+7.2f} R"
            f"  ·  {drittel.erwartungswert:+.3f} R je Trade"
            f"  ·  Treffer {(drittel.trefferquote or 0) * 100:.0f} %"
        )
        print(f"  tiefster Rueckgang  {ganz.max_rueckgang_r:+7.2f} R  ·  Serie {ganz.verlustserie}")
        if b.nie_im_plus is not None:
            print(f"  nie im Plus         {b.nie_im_plus * 100:.0f} % der Trades")
    print()
    for satz in b.saetze:
        print(f"  · {satz}")
    print(f"\n{ziel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
