#!/usr/bin/env python
"""XAUUSDT JETZT — eine einzige vollständige Bewertung auf den aktuellen Live-Daten.

Fragt die AI: **„Wie sieht der Markt JETZT aus, und was ist die Entscheidung?"**
REST-Warmup (Binance USD-M Futures, read-only) → **ein** ``MarketContext`` am letzten
bestätigten M5-Close → ``strategy.evaluate`` (SMC + Breakout-Retest) → ``apply_live_gate`` →
vollständige Ausgabe:

* Entscheidung + Begründung (BUY / SELL / ⏳ WAIT / NO_TRADE)
* SMC-Kette: FSM-State + chain_progress + No-Trade/Veto-Gründe
* Breakout-Retest: State (SCANNING / AWAIT_RETEST / ARMED) + chain_progress
* Opportunity-Score + Faktor-Bilanz
* bei BUY/SELL: der konkrete Signal-Report (Entry/SL/TP1/TP2/TP3/RR/Score/Confidence/…)

Kein Broker, keine Order. Ein Aufruf = passt in enge Prozess-Zeitfenster.

    uv run python scripts/xau_now.py --symbol XAUUSDT
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
from datetime import UTC, datetime

from trading_agent.core.enums import AssetClass, Timeframe
from trading_agent.governance import ValidationRegistry, apply_live_gate
from trading_agent.runtime.live_pipeline import (
    LivePipeline,
    LivePipelineConfig,
    build_rest_provider,
)
from trading_agent.scanner.opportunity import score_opportunity
from trading_agent.strategy.signal_report import build_signal_report
from trading_agent.utils.logging import configure_logging


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbol", default="XAUUSDT")
    ap.add_argument("--exchange", default="binance")
    ap.add_argument("--asset-class", default="gold")
    ap.add_argument("--validation-config", default="config/setup_validation.json")
    ap.add_argument("--repo", default="data/repository_real")
    ap.add_argument("--risk-pct", type=float, default=1.0)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    configure_logging("WARNING")

    cfg = LivePipelineConfig(
        exchange=args.exchange,
        instruments=(args.symbol.upper(),),
        asset_class=AssetClass(args.asset_class),
        news_gate=False,
    )
    rest = build_rest_provider(args.exchange)
    pipe = LivePipeline(cfg, rest_provider=rest)
    try:
        await pipe.warmup()
        cut = max(b.close_time for b in pipe._m5[args.symbol.upper()])
        mc = pipe._build_context(args.symbol.upper(), cut)
    finally:
        with contextlib.suppress(Exception):
            await rest.aclose()

    import dataclasses as _dc

    # Cross-Asset-Kontext (DXY / US10Y / VIX) aus dem Repo — keylos via ingest_yahoo.py.
    # Speist die Confluence-/Makro-Bewertung; fehlt eine Reihe, bleibt ihr Feld None.
    from trading_agent.data.providers.cross_asset import build_cross_asset_from_repo
    from trading_agent.data.repository import MarketDataRepository

    ca = build_cross_asset_from_repo(MarketDataRepository(args.repo), as_of=cut)
    if ca.as_of is not None:
        mc = _dc.replace(mc, cross_asset=ca)

    from trading_agent.strategy.evaluate import EvaluateParams, evaluate

    ep = EvaluateParams(asset_class=AssetClass(args.asset_class))
    # Research-/Shadow-Modus: kein News-Feed vorhanden → den Fail-safe-Block deaktivieren
    # (wie im Live-Daemon mit news_gate=False). News wird als 'nicht geprüft' im Report vermerkt.
    ep = _dc.replace(
        ep,
        no_trade=_dc.replace(ep.no_trade, require_news_feed=False),
        veto=_dc.replace(ep.veto, require_news_feed=False),
    )
    result = evaluate(mc, params=ep)
    registry = ValidationRegistry.from_file(args.validation_config)
    gated = apply_live_gate(result, registry=registry)

    d = gated.decision
    opp = score_opportunity(gated, asset_class=args.asset_class)
    m5 = mc.series[Timeframe.M5]
    price = m5[-1].close
    bo = gated.breakout

    out: dict[str, object] = {
        "symbol": args.symbol.upper(),
        "as_of": cut.isoformat(),
        "last_price": price,
        "decision": d.decision.value.upper(),
        "setup_id": d.setup_id,
        "setup_state": d.setup_state.value,
        "chain_progress": d.chain_progress,
        "reason_codes": [r.value for r in d.reason_codes],
        "vetoes": [v.value for v in d.vetoes],
        "htf_directional": gated.mtf.htf_directional.value,
        "regime_ok": gated.mtf.regime_ok,
        "opportunity_score": round(opp.score, 1),
        "opportunity_headline": opp.headline,
        "breakout": None
        if bo is None
        else {
            "state": bo.state.value,
            "direction": bo.direction.value if bo.direction else None,
            "d1_trend": bo.d1_trend.value,
            "chain_progress": bo.chain_progress,
            "reasons": [r.value for r in bo.reasons],
            "broken_level": bo.broken_level,
        },
        "live_gate": gated.live_gate.as_dict() if gated.live_gate else None,
        "signal": None,
    }

    if d.decision.value in ("buy", "sell"):
        rep = build_signal_report(gated, opportunity=opp, risk_pct=args.risk_pct)
        if rep is not None:
            out["signal"] = rep.as_dict()

    if args.json:
        print(json.dumps(out, indent=2, default=str))
        return 0

    _render(out)
    return 0


def _render(o: dict[str, object]) -> None:
    print(f"\n{'=' * 62}")
    print(f"  {o['symbol']}  ·  {o['as_of']}  ·  Preis {o['last_price']}")
    print(f"{'=' * 62}")
    dec = str(o["decision"])
    tag = {"BUY": "🔥 BUY", "SELL": "🔥 SELL", "WAIT": "⏳ WAIT", "NO_TRADE": "NO TRADE"}.get(
        dec, dec
    )
    lg = o.get("live_gate") or {}
    elig = str(lg.get("eligibility", "")).upper() if isinstance(lg, dict) else ""
    print(f"\n  ENTSCHEIDUNG:  {tag}" + (f"   [{elig}]" if elig and elig != "LIVE" else ""))
    print(f"  Setup:         {o['setup_id']}  ({o['setup_state']})")
    print(f"  Kette:         {o['chain_progress'] or '—'}")
    if o["reason_codes"]:
        print(f"  Gründe:        {', '.join(o['reason_codes'])}")  # type: ignore[arg-type]
    if o["vetoes"]:
        print(f"  Vetos:         {', '.join(o['vetoes'])}")  # type: ignore[arg-type]
    print(
        f"  HTF-Regime:    {o['htf_directional']}  (regime_ok={o['regime_ok']})  "
        f"·  Opportunity-Score {o['opportunity_score']}/100"
    )
    print(f"  {o['opportunity_headline']}")

    bo = o.get("breakout")
    if isinstance(bo, dict):
        print(
            f"\n  2. Setup-Typ (Breakout-Retest):  {bo['state'].upper()}"
            f"   D1-Trend {bo['d1_trend']}"
        )
        print(f"    {bo['chain_progress'] or '—'}")
        if bo["reasons"]:
            print(f"    Gründe: {', '.join(bo['reasons'])}")  # type: ignore[arg-type]

    sig = o.get("signal")
    if isinstance(sig, dict):
        print(f"\n  {'-' * 58}")
        print(f"  Entry:        {sig['entry']}")
        print(f"  Stop Loss:    {sig['stop_loss']}")
        print(f"  TP1:          {sig['tp1']}")
        print(f"  TP2:          {sig['tp2']}")
        print(f"  TP3:          {sig['tp3']}")
        print(f"  R:R (→TP2):   {sig['rr_to_tp2']}   ·   Blended R:R  {sig['blended_rr']}")
        print(f"  Score:        {sig['opportunity_score']}/100")
        print(f"  Confidence:   {sig['confidence_pct']}%")
        print(f"  Risk:         {sig['risk_pct']}%")
        why = sig.get("why") or []
        print(f"  Warum:        {'; '.join(why) if why else '—'}")  # type: ignore[arg-type]
        print(f"  Invalidation: {sig['invalidation']}")
        risks = sig.get("risks") or []
        print(f"  Risiken:      {'; '.join(risks) if risks else 'keine erkannt'}")  # type: ignore[arg-type]
    elif dec == "NO_TRADE":
        print("\n  → Kein hochwertiges Setup. Kein Signal. (lieber kein Trade als ein schlechter)")
    print(f"\n{'=' * 62}\n")
    _ = datetime.now(UTC)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
