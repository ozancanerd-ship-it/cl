#!/usr/bin/env python3
"""Gegenthese-Studie — vorab registriert in docs/GEGENTHESE-STUDIE-2026-09.md.

    python3 scripts/gegenthese_studie.py --lauf DIR --npz DIR

Liest die Bewertungen der Signal-Studie (``DIR/zeilen/*.jsonl``, Phase A von
``signal_replay.py``), rechnet je Zeitpunkt die relative Staerke wie live und misst, wie
Coins in der Lage „handelbares Short-Setup bei relativer Staerke unter 45" danach im
Vergleich zu ihrer Klasse liefen.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

OOS_AB = datetime(2026, 1, 1, tzinfo=UTC)
RS_GRENZE = 45.0
ABSTAND = timedelta(days=7)


def main() -> int:
    from trading_agent.scanner.relative_strength import anwenden as rs_anwenden

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--lauf", default="/tmp/claude-0/bt/lauf")
    ap.add_argument("--npz", default="/tmp/claude-0/bt/npz")
    args = ap.parse_args()

    je_zeit: dict[str, list[dict]] = defaultdict(list)
    for f in sorted(Path(args.lauf, "zeilen").glob("*.jsonl")):
        for zeile in f.read_text(encoding="utf-8").splitlines():
            d = json.loads(zeile)
            if "fehler" not in d:
                je_zeit[d["t"]].append(d)
    coins = sorted({p.stem for p in Path(args.lauf, "zeilen").glob("*.jsonl")})
    schluss: dict[str, dict] = {}
    for c in coins:
        z = np.load(Path(args.npz) / f"{c}_1d.npz")
        schluss[f"{c}USD"] = {
            datetime.fromtimestamp(int(t) / 1000, tz=UTC).date(): float(v)
            for t, v in zip(z["t"], z["c"], strict=True)
        }
    print(f"{len(coins)} Coins, {len(je_zeit)} Zeitpunkte")

    def fwd(inst: str, t: datetime, h: int) -> float | None:
        a = schluss[inst].get(t.date())
        b = schluss[inst].get((t + timedelta(days=h)).date())
        return (b / a - 1) * 100 if a and b else None

    zuletzt: dict[str, datetime] = {}
    erg: dict[tuple[str, int], list[float]] = defaultdict(list)
    for iso in sorted(je_zeit):
        t = datetime.fromisoformat(iso)
        zeilen = je_zeit[iso]
        rs_anwenden({"krypto": zeilen})
        for h in (7, 14):
            alle = [(z["instrument"], fwd(z["instrument"], t, h)) for z in zeilen]
            alle = [(i, r) for i, r in alle if r is not None]
            if len(alle) < 8:
                continue
            mittel = float(np.mean([r for _, r in alle]))
            je = dict(alle)
            for z in zeilen:
                inst = z["instrument"]
                lage = (
                    z.get("richtung") == "short"
                    and z.get("handelbar")
                    and z.get("rs") is not None
                    and float(z["rs"]) < RS_GRENZE
                )
                if not lage or inst not in je:
                    continue
                if h == 7:
                    if inst in zuletzt and t - zuletzt[inst] < ABSTAND:
                        continue
                    zuletzt[inst] = t
                elif (inst, iso) not in _gezaehlt:
                    continue
                teil = "IS" if t < OOS_AB else "OOS"
                erg[(teil, h)].append(je[inst] - mittel)
                if h == 7:
                    _gezaehlt.add((inst, iso))
    aus = {}
    for (teil, h), v in sorted(erg.items()):
        a = np.array(v)
        aus[f"{teil}_{h}"] = {
            "n": len(a),
            "relativ_mittel": round(float(a.mean()), 2) if len(a) else None,
            "relativ_median": round(float(np.median(a)), 2) if len(a) else None,
            "anteil_schlechter": round(float((a < 0).mean()), 3) if len(a) else None,
        }
        print(teil, h, aus[f"{teil}_{h}"])
    ok = all(
        (aus.get(f"{t}_7") or {}).get("n", 0) >= 20
        and ((aus.get(f"{t}_7") or {}).get("relativ_mittel") or 0) <= -1.0
        for t in ("IS", "OOS")
    )
    aus["entscheidung"] = "Regel bleibt" if ok else "Regel faellt (nur noch Hinweis)"
    print(aus["entscheidung"])
    Path(args.lauf, "gegenthese.json").write_text(json.dumps(aus, indent=1), encoding="utf-8")
    return 0


_gezaehlt: set[tuple[str, str]] = set()

if __name__ == "__main__":
    raise SystemExit(main())
