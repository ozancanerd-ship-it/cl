#!/usr/bin/env python
"""Edge-Health-Check — schließt die ADAPTATION-Schleife (Masterplan: „Recent Data = Regime-Check").

Liest die realisierten Trades aus einem Trade-Ledger, gruppiert sie je Setup, nimmt die
jüngsten ``--recent`` Trades und prüft mit ``governance.assess_edge_health`` gegen die in der
``ValidationRegistry`` hinterlegte Baseline:

    INTACT     — Edge trägt aktuell noch.
    WEAKENING  — noch positiv, aber deutlich unter Baseline → enger beobachten.
    BROKEN     — Edge auf Recent-Daten weg → Setup sollte auf EDGE_DEGRADED (kein Live-Signal).
    INSUFFICIENT_DATA — zu wenige Recent-Trades.

    uv run python scripts/edge_health_check.py \
        --ledger data/repository_real/strategy_ledger.sqlite \
        --validation-config config/setup_validation.json --recent 60

Mit ``--write`` wird bei BROKEN der Registry-Eintrag auf ``edge_degraded`` gesetzt
(``config/setup_validation.json`` wird überschrieben). Ohne ``--write``: reiner Report.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from trading_agent.governance import (
    EdgeHealth,
    ValidationRegistry,
    ValidationStatus,
    assess_edge_health,
)
from trading_agent.journal.ledger import Ledger, TradeRecord
from trading_agent.research.metrics import compute_metrics


def _baseline_from_trades(trades: list[TradeRecord]) -> dict[str, float | int]:
    m = compute_metrics(trades)
    return {
        "expectancy_r": round(m.expectancy_r, 4),
        "profit_factor": round(m.profit_factor, 3) if m.profit_factor != float("inf") else 999.0,
        "win_rate": round(m.win_rate, 4),
        "max_drawdown_r": round(m.max_drawdown_r, 3),
        "n_trades": m.n_trades,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ledger", default="data/repository_real/strategy_ledger.sqlite")
    ap.add_argument("--validation-config", default="config/setup_validation.json")
    ap.add_argument("--recent", type=int, default=60, help="Anzahl jüngster Trades je Setup")
    ap.add_argument("--min-recent", type=int, default=20)
    ap.add_argument("--write", action="store_true", help="BROKEN → Registry auf edge_degraded")
    ap.add_argument("--out", default=None, help="Report als JSON hierhin")
    args = ap.parse_args()

    lpath = Path(args.ledger)
    if not lpath.exists():
        print(json.dumps({"error": f"kein Ledger unter {lpath}", "setups": []}, indent=2))
        return 1

    registry = ValidationRegistry.from_file(args.validation_config)
    all_trades = Ledger(str(lpath)).trades()
    by_setup: dict[str, list[TradeRecord]] = defaultdict(list)
    for t in sorted(all_trades, key=lambda t: t.entry_ts):
        by_setup[t.setup_id].append(t)

    rows: list[dict[str, object]] = []
    degrade: list[tuple[str, str, str]] = []
    for setup_id, trades in sorted(by_setup.items()):
        sv = registry.get(setup_id, trades[-1].strategy_version)
        recent = trades[-args.recent :]
        base = sv.baseline
        base_source = "registry"
        if base is None:
            # keine hinterlegte Baseline → aus den ÄLTEREN Trades (vor dem Recent-Fenster) bilden
            older = trades[: -args.recent] if len(trades) > args.recent else []
            if len(older) >= args.min_recent:
                from trading_agent.governance import BaselineMetrics

                bm = _baseline_from_trades(older)
                base = BaselineMetrics(
                    expectancy_r=float(bm["expectancy_r"]),
                    profit_factor=float(bm["profit_factor"]),
                    win_rate=float(bm["win_rate"]),
                    max_drawdown_r=float(bm["max_drawdown_r"]),
                    n_trades=int(bm["n_trades"]),
                )
                base_source = "older_trades"
        if base is None:
            rows.append(
                {
                    "setup_id": setup_id,
                    "status": sv.status.value,
                    "note": "keine Baseline (weder Registry noch genug ältere Trades) — kein Check",
                    "total_trades": len(trades),
                }
            )
            continue

        rep = assess_edge_health(base, recent, min_recent=args.min_recent)
        rows.append(
            {
                "setup_id": setup_id,
                "status": sv.status.value,
                "baseline_source": base_source,
                "baseline": base.as_dict(),
                "edge_health": rep.as_dict(),
                "total_trades": len(trades),
            }
        )
        if rep.health is EdgeHealth.BROKEN and sv.status is ValidationStatus.VALIDATED:
            degrade.append((setup_id, sv.strategy_version, "; ".join(rep.reasons[:2])))

    report = {
        "ledger": str(lpath),
        "recent_window": args.recent,
        "setups": rows,
        "degrade_recommended": [
            {"setup_id": s, "strategy_version": v, "reason": r} for s, v, r in degrade
        ],
    }

    if args.write and degrade:
        reg = registry
        for s, v, r in degrade:
            reg = reg.degrade(s, v, reason=r)
        payload = {"setups": [sv.as_dict() for sv in reg.all()]}
        Path(args.validation_config).write_text(json.dumps(payload, indent=2), encoding="utf-8")
        report["written"] = [s for s, _v, _r in degrade]

    out = json.dumps(report, indent=2, default=str)
    if args.out:
        Path(args.out).write_text(out, encoding="utf-8")
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
