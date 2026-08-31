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
import glob
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from trading_agent.api.dashboard import DashboardInputs, build_dashboard_state
from trading_agent.data.repository import MarketDataRepository
from trading_agent.governance import ValidationRegistry


def _load_performance(repo: MarketDataRepository, pattern: str) -> dict[str, object] | None:
    """Shadow-/Paper-Journale → Performance-Kennzahlen (performance_report._load_trades)."""
    import performance_report as pr

    paths = sorted(glob.glob(pattern))
    trades = pr._load_trades(paths, repo)
    if not trades:
        return None
    return {
        "trades": pr._block(trades),
        "by_asset": pr._grouped(trades, lambda t: t.instrument),
        "by_setup": pr._grouped(trades, lambda t: t.setup_id),
        "by_direction": pr._grouped(trades, lambda t: t.direction.value),
        "by_score_bucket": pr._grouped(trades, pr._score_bucket),
        "by_eligibility": pr._grouped(trades, lambda t: pr._meta(t).get("elig") or "n/a"),
    }


def _load_stocks(repo: MarketDataRepository, symbols: list[str], benchmark: str) -> list[dict[str, object]]:
    """Einzelaktien-Analyse für die im Repo vorliegenden ``<SYM>-YF``-D1-Reihen."""
    if not symbols:
        return []
    from datetime import datetime as _dt

    from trading_agent.core.enums import Timeframe
    from trading_agent.investment.stock_analysis import StockAnalysisEngine

    lo, hi = _dt(2000, 1, 1, tzinfo=UTC), _dt(2100, 1, 1, tzinfo=UTC)
    bench = repo.read_ohlcv(benchmark, Timeframe.D1, lo, hi) or None
    engine = StockAnalysisEngine()
    now = datetime.now(UTC)
    out: list[dict[str, object]] = []
    for sym in symbols:
        dest = sym if sym.endswith("-YF") else f"{sym}-YF"
        d1 = repo.read_ohlcv(dest, Timeframe.D1, lo, hi)
        if not d1:
            continue
        out.append(engine.analyze(dest, d1, as_of=now, benchmark_d1=bench).as_dict())
    out.sort(key=lambda r: r["score"], reverse=True)  # type: ignore[arg-type,return-value]
    return out


def _load_chart(repo: MarketDataRepository, symbol: str, *, bars: int = 240) -> dict[str, object] | None:
    """Candles (H4) + Swing-/BOS-Marker + FVG/OB-Zonen für den Chart-Tab.
    Rein aus dem Repo + Primitiven — kein Live-Pipeline-Durchlauf nötig."""
    from datetime import datetime as _dt

    from trading_agent.chart.annotations import build_chart_annotations
    from trading_agent.core.enums import Timeframe
    from trading_agent.strategy.primitives.imbalance import find_fvgs
    from trading_agent.strategy.primitives.structure import structure_breaks
    from trading_agent.strategy.primitives.swings import detect_swings

    lo, hi = _dt(2000, 1, 1, tzinfo=UTC), _dt(2100, 1, 1, tzinfo=UTC)
    h4 = repo.read_ohlcv(symbol, Timeframe.H4, lo, hi)
    if len(h4) < 60:
        return None
    window = h4[-bars:]
    sw = detect_swings(h4, Timeframe.H4, left=2, right=2, min_leg_atr=0.5)
    brk = structure_breaks(h4, sw, Timeframe.H4, min_swings=2)
    tick = max(1e-6, round(abs(h4[-1].close) * 1e-5, 8))
    try:
        fvgs = find_fvgs(h4, Timeframe.H4, tick_size=tick)
    except Exception:
        fvgs = []
    cutoff = window[-1].close_time
    ctx = type(
        "Ctx",
        (),
        {
            "timeframe": Timeframe.H4,
            "swings": tuple(s for s in sw if s.confirmed_at <= cutoff),
            "structure_breaks": tuple(b for b in brk if b.break_bar_timestamp <= cutoff),
            "fvgs": tuple(f for f in fvgs if f.created_bar <= cutoff)[-8:],
            "order_blocks": (),
            "liquidity": (),
        },
    )()
    mtf = type("Mtf", (), {"per_tf": {Timeframe.H4: ctx}})()
    sr = type("Sr", (), {"instrument": symbol, "information_cutoff": cutoff, "action": "", "direction": ""})()
    ann = build_chart_annotations(sr, mtf=mtf).as_dict()
    ann["candles"] = [
        {
            "time": int(b.open_time.timestamp()),
            "open": b.open, "high": b.high, "low": b.low, "close": b.close,
        }
        for b in window
    ]
    ann["instrument"] = symbol
    return ann


def _load_reentry(pattern: str, shadow_signals: list[dict[str, object]]) -> list[dict[str, object]]:
    """reentry_watch-Zeilen aus den Journalen; 'setup', wenn ein frisches Shadow-Signal für
    dasselbe Instrument+Richtung existiert, sonst 'watch'."""
    watches: dict[tuple[str, str], dict[str, object]] = {}
    for p in sorted(glob.glob(pattern)):
        for line in Path(p).read_text().splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            if r.get("kind") != "reentry_watch":
                continue
            watches[(str(r["instrument"]), str(r["direction"]))] = r
    fresh = {
        (str(s.get("instrument")), str(s.get("direction", "")).upper())
        for s in shadow_signals
    }
    out: list[dict[str, object]] = []
    for (inst, direction), w in sorted(watches.items()):
        w = {**w, "state": "setup" if (inst, direction.upper()) in fresh else "watch"}
        out.append(w)
    return out


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
        except Exception as exc:
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
        except Exception as exc:
            blockers.append(f"portfolio: {exc}")
            portfolio = None

    perf: dict[str, object] | None = None
    reentry: list[dict[str, object]] = []
    stocks: list[dict[str, object]] = []
    try:
        repo = MarketDataRepository(args.repo)
        perf = _load_performance(repo, args.journals)
        reentry = _load_reentry(args.journals, shadow)
        stocks = _load_stocks(repo, args.stocks, args.benchmark)
    except Exception as exc:
        blockers.append(f"performance/reentry/stocks: {exc}")

    chart: dict[str, object] | None = None
    try:
        chart = _load_chart(MarketDataRepository(args.repo), args.chart_symbol)
    except Exception as exc:
        blockers.append(f"chart: {exc}")

    dash = build_dashboard_state(
        DashboardInputs(
            as_of=datetime.now(UTC),
            top_opportunities=top,
            scanner_evaluations=len(top),
            stocks=stocks,
            signals=signals,
            shadow_signals=shadow,
            reentry=reentry,
            chart_annotations=chart,
            validation=[sv.as_dict() for sv in registry.all()],
            portfolio=portfolio,
            paper_performance=perf,
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
    ap.add_argument("--repo", default="data/repository_real")
    ap.add_argument("--journals", default="data/repository_real/live/*.jsonl")
    ap.add_argument(
        "--stocks", nargs="*", default=["NVDA", "AAPL", "MSFT", "AMD", "GOOGL", "META"]
    )
    ap.add_argument("--benchmark", default="SPX-YF")
    ap.add_argument("--chart-symbol", default="XAUUSD-YF")
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
