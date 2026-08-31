#!/usr/bin/env python
"""Multi-Asset-Scan JETZT — eine vollständige Bewertung je Instrument auf Live-Daten.

Wie ``xau_now.py``, aber über eine ganze Watchlist: REST-Warmup (read-only) →
``strategy.evaluate`` (SMC + Breakout-Retest) je Symbol → ``apply_live_gate`` →
``MarketScanner``/``score_opportunity`` → **Opportunity-Ranking** + je Symbol
Entscheidung, Setup-State, Score, und — bei BUY/SELL — der konkrete Signal-Report
(Entry/SL/TP1-3/RR/Confidence/Warum/Invalidation, LIVE oder ⚠️ SHADOW).

Ein Aufruf = passt in enge Prozess-Zeitfenster (kein Dauer-Daemon). Kein Broker,
keine Order. Für 24/7 nutzt der Live-Daemon (``run_live_daemon.py``) dieselben Bausteine.

    uv run python scripts/market_scan.py --symbols BTCUSDT ETHUSDT SOLUSDT --asset-class crypto
    uv run python scripts/market_scan.py --symbols XAUUSDT --asset-class gold --json
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import dataclasses as _dc
import json

from trading_agent.core.enums import AssetClass, Timeframe
from trading_agent.governance import ValidationRegistry, apply_live_gate
from trading_agent.runtime.live_pipeline import (
    LivePipeline,
    LivePipelineConfig,
    build_rest_provider,
)
from trading_agent.scanner.market_scanner import MarketScanner, ScannerConfig
from trading_agent.strategy.evaluate import EvaluateParams, evaluate
from trading_agent.strategy.signal_report import build_signal_report
from trading_agent.utils.logging import configure_logging

_DEC_TAG = {"buy": "🔥 BUY", "sell": "🔥 SELL", "wait": "⏳ WAIT", "no_trade": "· NO TRADE"}


async def _evaluate_all(
    symbols: list[str], exchange: str, asset_class: str, registry: ValidationRegistry
) -> list[dict[str, object]]:
    ac = AssetClass(asset_class)
    cfg = LivePipelineConfig(
        exchange=exchange,
        instruments=tuple(s.upper() for s in symbols),
        asset_class=ac,
        news_gate=False,
    )
    rest = build_rest_provider(exchange)
    pipe = LivePipeline(cfg, rest_provider=rest)
    contexts: dict[str, object] = {}
    try:
        await pipe.warmup()
        for s in cfg.instruments:
            with contextlib.suppress(Exception):
                bars = pipe._m5.get(s)
                if not bars:
                    continue
                cut = max(b.close_time for b in bars)
                contexts[s] = pipe._build_context(s, cut)
    finally:
        with contextlib.suppress(Exception):
            await rest.aclose()

    ep = EvaluateParams(asset_class=ac)
    ep = _dc.replace(
        ep,
        no_trade=_dc.replace(ep.no_trade, require_news_feed=False),
        veto=_dc.replace(ep.veto, require_news_feed=False),
    )

    # Cross-Asset-Kontext (DXY / US10Y / VIX) aus dem Repo — keylos (ingest_yahoo.py).
    from trading_agent.data.providers.cross_asset import build_cross_asset_from_repo
    from trading_agent.data.repository import MarketDataRepository

    _repo = MarketDataRepository("data/repository_real")
    for s, mc in list(contexts.items()):
        cut = getattr(mc, "information_cutoff", None)
        if cut is None:
            continue
        ca = build_cross_asset_from_repo(_repo, as_of=cut)
        if ca.as_of is not None:
            contexts[s] = _dc.replace(mc, cross_asset=ca)
    scanner = MarketScanner(ScannerConfig(asset_class=dict.fromkeys(contexts, asset_class)))

    rows: list[dict[str, object]] = []
    for s, mc in contexts.items():
        gated = apply_live_gate(evaluate(mc, params=ep), registry=registry)
        opp = scanner.feed(s, gated)
        d = gated.decision
        price = mc.series[Timeframe.M5][-1].close  # type: ignore[attr-defined]
        bo = gated.breakout
        lg = gated.live_gate
        row: dict[str, object] = {
            "symbol": s,
            "price": price,
            "decision": d.decision.value,
            "setup_id": d.setup_id,
            "setup_state": d.setup_state.value,
            "chain_progress": d.chain_progress,
            "reason_codes": [r.value for r in d.reason_codes],
            "opportunity_score": round(opp.score, 1),
            "opportunity_headline": opp.headline,
            "breakout_state": bo.state.value if bo is not None else None,
            "live_eligibility": (lg.eligibility.value if lg is not None else "live"),
            "signal": None,
        }
        if d.decision.value in ("buy", "sell"):
            rep = build_signal_report(gated, opportunity=opp, risk_pct=1.0)
            if rep is not None:
                row["signal"] = rep.as_dict()
        rows.append(row)

    rows.sort(key=lambda r: r["opportunity_score"], reverse=True)  # type: ignore[arg-type,return-value]
    return rows


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbols", nargs="+", default=["BTCUSDT", "ETHUSDT", "SOLUSDT"])
    ap.add_argument("--exchange", default="binance")
    ap.add_argument("--asset-class", default="crypto")
    ap.add_argument("--validation-config", default="config/setup_validation.json")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    configure_logging("WARNING")

    registry = ValidationRegistry.from_file(args.validation_config)
    rows = await _evaluate_all(args.symbols, args.exchange, args.asset_class, registry)

    if args.json:
        print(json.dumps({"ranking": rows}, indent=2, default=str))
        return 0

    print(f"\n{'=' * 64}")
    print(f"  MARKET SCAN  ·  {len(rows)} Instrumente  ·  {args.asset_class}")
    print(f"{'=' * 64}")
    for i, r in enumerate(rows, 1):
        tag = _DEC_TAG.get(str(r["decision"]), str(r["decision"]))
        elig = str(r["live_eligibility"]).upper()
        elig_s = f" [{elig}]" if elig != "LIVE" else ""
        print(
            f"  #{i}  {r['symbol']:<12} {r['opportunity_score']:>5.1f}/100  "
            f"{tag}{elig_s}   {r['setup_id']} ({r['setup_state']})"
        )
        if r["chain_progress"]:
            print(f"        {r['chain_progress']}")
    sigs = [r for r in rows if r["signal"] is not None]
    if not sigs:
        print(f"\n  → Kein aktives BUY/SELL-Setup. NO TRADE über alle {len(rows)}.")
    for r in sigs:
        _render_signal(r)
    print(f"{'=' * 64}\n")
    return 0


def _render_signal(r: dict[str, object]) -> None:
    s = r["signal"]
    assert isinstance(s, dict)
    elig = str(r["live_eligibility"]).upper()
    head = "🔥" if elig == "LIVE" else "⚠️ SHADOW —"
    print(f"\n  {'-' * 60}")
    print(f"  {head}  {str(r['decision']).upper()} · {r['symbol']}")
    print(
        f"  Entry {s['entry']}  ·  SL {s['stop_loss']}  ·  TP1 {s['tp1']}  TP2 {s['tp2']}  TP3 {s['tp3']}"
    )
    print(
        f"  R:R→TP2 {s['rr_to_tp2']}  ·  Blended {s['blended_rr']}  ·  "
        f"Score {s['opportunity_score']}/100  ·  Conf {s['confidence_pct']}%"
    )
    why = s.get("why") or []
    print(f"  Warum: {'; '.join(why) if why else '—'}")  # type: ignore[arg-type]
    print(f"  Invalidation: {s['invalidation']}")
    risks = s.get("risks") or []
    if risks:
        print(f"  Risiken: {'; '.join(risks)}")  # type: ignore[arg-type]


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
