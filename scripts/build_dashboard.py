#!/usr/bin/env python
"""One-shot Dashboard-Snapshot → ``web/dashboard.json`` (für ``web/dashboard.html``).

Baut ``DashboardState`` (Masterplan §63–§70) aus **einem** Live-Durchlauf, ohne den
Dauer-Daemon: Multi-Asset-Scan (``market_scan._evaluate_all``) für Ranking + Signale,
Portfolio-Hub (``portfolio_hub``) für ``my_portfolios``, ValidationRegistry für den
Freigabe-Status. Schreibt JSON neben das statische HTML — ``open web/dashboard.html``.

Für echtes 24/7 nutzt ``run_live_daemon.py --dashboard-json web/dashboard.json`` dieselbe
``build_dashboard_state``-Funktion; dieses Skript ist der prozesszeit-schonende Snapshot.

    uv run python scripts/build_dashboard.py
    uv run python scripts/build_dashboard.py --crypto BTCUSDT ETHUSDT --no-portfolio
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from trading_agent.api.dashboard import DashboardInputs, build_dashboard_state  # noqa: E402
from trading_agent.governance import ValidationRegistry  # noqa: E402


async def _run(args: argparse.Namespace) -> dict[str, object]:
    import market_scan  # scripts/market_scan.py

    registry = ValidationRegistry.from_file(args.validation_config)
    top: list[dict[str, object]] = []
    signals: list[dict[str, object]] = []
    shadow: list[dict[str, object]] = []
    blockers: list[str] = []

    groups: list[tuple[str, list[str]]] = []
    if args.crypto:
        groups.append(("crypto", args.crypto))
    if args.gold:
        groups.append(("gold", args.gold))

    for asset_class, syms in groups:
        try:
            rows = await market_scan._evaluate_all(syms, args.exchange, asset_class, registry)
        except Exception as exc:  # noqa: BLE001
            blockers.append(f"scan {asset_class}: {exc}")
            continue
        for r in rows:
            top.append(
                {
                    "rank": 0,
                    "instrument": r["symbol"],
                    "score": r["opportunity_score"],
                    "tier": None,
                    "setup_state": r["setup_state"],
                    "direction": None,
                    "headline": r["opportunity_headline"],
                    "decision": r["decision"],
                }
            )
            sig = r.get("signal")
            if isinstance(sig, dict):
                (signals if r["live_eligibility"] == "live" else shadow).append(
                    {**sig, "instrument": r["symbol"], "eligibility": r["live_eligibility"]}
                )

    top.sort(key=lambda o: o["score"], reverse=True)  # type: ignore[arg-type,return-value]
    for i, o in enumerate(top, 1):
        o["rank"] = i

    portfolio: dict[str, object] | None = None
    if not args.no_portfolio:
        try:
            import portfolio_hub  # scripts/portfolio_hub.py
            from trading_agent.portfolio_intel import (
                AccountPortfolio,
                PortfolioIntelligenceEngine,
            )

            portfolio_hub._load_env_file()
            as_of = datetime.now(UTC)
            prices: dict[str, float] = {}
            accs = await asyncio.gather(
                portfolio_hub._kraken(as_of, prices),
                portfolio_hub._bybit(as_of),
                portfolio_hub._binance(as_of, prices),
                return_exceptions=True,
            )
            live = [a for a in accs if isinstance(a, AccountPortfolio)]
            if live:
                rep = PortfolioIntelligenceEngine().assess(live, as_of=as_of)
                portfolio = rep.as_dict()
            else:
                blockers.append("portfolio: kein Account lesbar (kein Key)")
        except Exception as exc:  # noqa: BLE001
            blockers.append(f"portfolio: {exc}")
            portfolio = None

    dash = build_dashboard_state(
        DashboardInputs(
            as_of=datetime.now(UTC),
            top_opportunities=top,
            scanner_evaluations=len(top),
            signals=signals,
            shadow_signals=shadow,
            validation=[sv.as_dict() for sv in registry.all()],
            portfolio=portfolio,
            blockers=blockers,
        )
    )
    return dash.as_dict()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--crypto", nargs="*", default=["BTCUSDT", "ETHUSDT", "SOLUSDT"])
    ap.add_argument("--gold", nargs="*", default=["XAUUSDT"])
    ap.add_argument("--exchange", default="binance")
    ap.add_argument("--no-portfolio", action="store_true")
    ap.add_argument("--validation-config", default="config/setup_validation.json")
    ap.add_argument("--out", default="web/dashboard.json")
    args = ap.parse_args()

    state = asyncio.run(_run(args))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(state, indent=2, default=str) + "\n")
    tabs = state["tabs"]
    assert isinstance(tabs, dict)
    print(f"→ {out}  ·  {tabs['overview']['headline']}")
    if tabs["overview"].get("blockers"):
        print(f"  Blocker: {tabs['overview']['blockers']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
