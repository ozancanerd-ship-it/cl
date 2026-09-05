#!/usr/bin/env python3
"""Scan ueber alle drei Anlageklassen in eine Datei — Grundlage fuer die App.

Krypto ueber die Boerse, Aktien und Gold ueber Yahoo. Eine Datei, ein Zeitstempel,
damit die App nicht drei Quellen mit drei Staenden mischt.

Faellt eine Klasse aus (Geosperre, stumme Quelle), wird das im Ergebnis vermerkt statt
verschwiegen — eine Rangliste ohne Krypto sieht sonst aus wie ein ruhiger Kryptomarkt.

    python3 scripts/build_scan_data.py --out web/scan.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="web/scan.json")
    ap.add_argument("--krypto", type=int, default=28, help="wie viele Coins")
    ap.add_argument("--aktien", type=int, default=30)
    args = ap.parse_args()

    from opportunity_scan import AKTIEN, PRESETS, _scan, _scan_yahoo

    from trading_agent.utils.logging import configure_logging

    configure_logging("WARNING")

    klassen: dict[str, list] = {}
    fehler: dict[str, str] = {}

    for name, coro in (
        ("krypto", _scan(PRESETS["krypto"][: args.krypto], "binance_spot", "crypto")),
        ("aktien", _scan_yahoo(AKTIEN[: args.aktien], "equity")),
        # Gold ueber Binance-Spot als PAXG/XAUT: 1:1 physisch hinterlegt, und vor allem
        # das, was Ozan mit seinen Konten tatsaechlich kaufen kann. Der Yahoo-Weg ueber
        # GC=F liefert den Future — Signal ohne Ausfuehrungsmoeglichkeit.
        ("gold", _scan(["PAXGUSDT", "XAUTUSDT"], "binance_spot", "gold")),
    ):
        print(f"— {name} —")
        try:
            klassen[name] = await coro
        except Exception as exc:
            fehler[name] = f"{type(exc).__name__}: {exc}"
            klassen[name] = []
            print(f"  ! {name} fehlgeschlagen: {exc}")

    alle = [c for v in klassen.values() for c in v]
    alle.sort(key=lambda c: -c.score)

    doc = {
        "erzeugt": datetime.now(UTC).isoformat(),
        "fehler": fehler,
        "anzahl": {k: len(v) for k, v in klassen.items()},
        "klassen": {
            k: [c.as_dict() for c in sorted(v, key=lambda x: -x.score)] for k, v in klassen.items()
        },
        "gesamt": [c.as_dict() for c in alle],
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")

    kandidaten = [c for c in alle if c.urteil in ("A_PLUS", "A")]
    print(f"\n{len(alle)} Instrumente · {len(kandidaten)} Kandidat(en) · {out}")
    for c in alle[:5]:
        print(f"  {c.instrument:<10} {c.score:>5.1f}  {c.urteil}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
