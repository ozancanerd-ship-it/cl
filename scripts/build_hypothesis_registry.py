#!/usr/bin/env python3
"""Hypothesen-Register aus allen vorhandenen Research-Laeufen befuellen (Befund F1).

Liest jede ``setup_research_*.json`` unter ``data/repository_real/research/`` und traegt
jede Setup-x-RR-Kombination als eigene Konfiguration ein. Zusaetzlich die von Hand
gefahrenen TSMOM-Konfigurationen aus ``scripts/tsmom_research.py``.

Idempotent: wiederholte Laeufe fuegen nichts doppelt hinzu.

    python3 scripts/build_hypothesis_registry.py
    python3 scripts/build_hypothesis_registry.py --dry-run
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from datetime import UTC, datetime

from trading_agent.research.hypotheses import Hypothesis, HypothesisRegistry

_NOTE = (
    "Jede je getestete Konfiguration, auch die verworfenen. Diese Zahl geht in die "
    "Multiple-Testing-Korrektur ein (Bonferroni und Deflated Sharpe Ratio). Eine "
    "Hypothese, die hier fehlt, macht jedes Ergebnis zu gut. Siehe "
    "docs/INDEPENDENT-METHOD-AUDIT-2026-09-03.md, Befund F1."
)

# Vor dem Register von Hand gefahren, hier nachgetragen (scripts/tsmom_research.py).
_TSMOM = [
    ("28", {"lookback": 28}),
    ("56", {"lookback": 56}),
    ("90", {"lookback": 90}),
    ("120", {"lookback": 120}),
    ("180", {"lookback": 180}),
    ("ensemble", {"lookbacks": [28, 56, 90, 120, 180]}),
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--research-dir", default="data/repository_real/research")
    ap.add_argument("--registry", default="config/hypothesis_registry.json")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    reg = HypothesisRegistry.load(args.registry)
    reg.note = _NOTE
    before = len(reg.entries)

    for path in sorted(glob.glob(os.path.join(args.research_dir, "setup_research_*.json"))):
        run = os.path.basename(path).replace(".json", "")
        try:
            with open(path, encoding="utf-8") as fh:
                doc = json.load(fh)
        except Exception as exc:
            print(f"  ! {run}: nicht lesbar ({exc})")
            continue
        params = doc.get("params", {})
        rr_grid = params.get("rr_grid") or [None]
        date = datetime.fromtimestamp(os.path.getmtime(path), UTC).date().isoformat()
        for setup, block in (doc.get("results") or {}).items():
            oos = block.get("OOS", {})
            reg.add(
                Hypothesis(
                    id=f"{run}:{setup}",
                    setup=setup,
                    run=run,
                    date=date,
                    # Jede RR-Stufe ist ein eigener Versuch: das RR wurde auf IS gewaehlt.
                    n_configs=len(rr_grid),
                    params={
                        "rr_grid": rr_grid,
                        "chosen_rr_on_is": block.get("chosen_rr_on_is"),
                        "split": params.get("split"),
                        "cost_r": params.get("cost_r"),
                        "start": params.get("start"),
                        "end": params.get("end"),
                    },
                    result={
                        "oos_n": oos.get("n_trades"),
                        "oos_expectancy_r": oos.get("expectancy_r"),
                        "sharpe_r": oos.get("sharpe_r"),
                        "profit_factor": oos.get("profit_factor"),
                    },
                    verdict="getestet",
                )
            )

    for name, p in _TSMOM:
        reg.add(
            Hypothesis(
                id=f"tsmom_research:{name}",
                setup="TSMOM",
                run="scripts/tsmom_research.py",
                date="2026-09-04",
                n_configs=1,
                params=p,
                result={},
                verdict="Erstbefund, nicht validiert",
                note="long-only, Vol-Ziel 40 %, 0.2 % Kosten je Wechsel, BTC/ETH/BNB/XRP 2017-2026",
            )
        )

    added = len(reg.entries) - before
    print(f"{added} neue Eintraege · {reg.summary()}")
    if args.dry_run:
        print("(dry-run — nichts geschrieben)")
        return 0
    reg.save(args.registry)
    print(f"geschrieben: {args.registry}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
