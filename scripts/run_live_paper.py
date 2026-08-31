#!/usr/bin/env python
"""LIVE PAPER — Kraken/Bybit **public** market data → MarketContext → … → Paper Position.

    python scripts/run_live_paper.py --exchange bybit --symbols BTCUSDT ETHUSDT \
        --minutes 12 --json

READ-ONLY. Kein API-Key, keine Trading-/Withdraw-Rechte, **keine Orderausführung**
(``orders_sent`` wird am Ende gegen 0 geprüft).

Ablauf:
1. REST-Warmup (M5 + M15/H4/D1) je Symbol.
2. ``prime()`` — ein sofortiger Pipeline-Durchlauf auf dem Warmup-Stand (Beweis: die volle
   Kette läuft auf echten Daten, ohne 5 min auf die erste WS-Bar zu warten).
3. WebSocket-Stream (Trades → confirmed M5), REST-Poller als Fallback bei WS-Stall.
4. Jede neue confirmed M5-Bar ⇒ neuer ``MarketContext`` ⇒ ``PaperLiveRunner.feed()`` ⇒
   Decision → Dynamic Signal → Alert → Paper Position. Alles über den EventBus sichtbar.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime

from trading_agent.core.enums import AssetClass
from trading_agent.runtime.events import (
    AlertRaised,
    BarClosed,
    DecisionMade,
    PaperPositionChanged,
    SignalRevised,
)
from trading_agent.runtime.live_pipeline import (
    LivePipeline,
    LivePipelineConfig,
    build_rest_provider,
)
from trading_agent.utils.logging import configure_logging


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--exchange", choices=["kraken", "bybit"], default="bybit")
    ap.add_argument("--symbols", nargs="+", default=["BTCUSDT", "ETHUSDT"])
    ap.add_argument("--minutes", type=float, default=12.0, help="Laufzeit nach Warmup")
    ap.add_argument("--max-bars", type=int, default=None, help="stoppt nach N Live-Bars")
    ap.add_argument("--asset-class", default="crypto")
    ap.add_argument(
        "--news-gate",
        choices=["on", "off"],
        default="off",
        help="'off' = Research-Modus (V4-Fail-safe aus); 'on' = live-repräsentativ (blockt ohne PIT-News)",
    )
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    configure_logging("INFO")
    asset_class = AssetClass(args.asset_class)
    # Nicht-24/7-Assets (Gold/FX/Aktien): Session-Fenster als Filter mitgeben, damit die
    # Engine z. B. am Wochenende trotz durchlaufendem Datenstrom NO_TRADE liefert.
    session_specs: tuple = ()
    if asset_class not in (AssetClass.CRYPTO, AssetClass.ALTCOIN):
        from trading_agent.refdata.seed import seed_sessions

        session_specs = tuple(seed_sessions())
    cfg = LivePipelineConfig(
        exchange=args.exchange,
        instruments=tuple(s.upper() for s in args.symbols),
        asset_class=asset_class,
        news_gate=args.news_gate == "on",
        session_specs=session_specs,
    )
    rest = build_rest_provider(args.exchange)
    pipe = LivePipeline(cfg, rest_provider=rest)

    events: list[str] = []

    async def _log_bar(e: BarClosed) -> None:
        events.append(
            f"{e.ts.isoformat()} BAR   {e.instrument} {e.timeframe.value} close={e.bar.close if e.bar else '?'}"
        )

    async def _log_decision(e: DecisionMade) -> None:
        events.append(
            f"{e.ts.isoformat()} DEC   {e.instrument} {e.decision_type} "
            f"state={e.setup_state} score={e.score} conf={e.confidence}"
        )

    async def _log_signal(e: SignalRevised) -> None:
        events.append(
            f"{e.ts.isoformat()} SIG   {e.instrument} {e.signal_id[:12]} {e.state} ({e.change})"
        )

    async def _log_alert(e: AlertRaised) -> None:
        events.append(f"{e.ts.isoformat()} ALERT {e.instrument} {e.alert_type}: {e.message}")

    async def _log_pos(e: PaperPositionChanged) -> None:
        events.append(
            f"{e.ts.isoformat()} PAPER {e.instrument} {e.change} realized_r={e.realized_r}"
        )

    pipe.bus.subscribe(BarClosed, _log_bar)
    pipe.bus.subscribe(DecisionMade, _log_decision)
    pipe.bus.subscribe(SignalRevised, _log_signal)
    pipe.bus.subscribe(AlertRaised, _log_alert)
    pipe.bus.subscribe(PaperPositionChanged, _log_pos)

    t0 = datetime.now(UTC)
    warm = await pipe.warmup()
    await pipe.prime()
    await pipe.run(max_bars=args.max_bars, max_seconds=args.minutes * 60.0)
    import contextlib

    with contextlib.suppress(Exception):
        await rest.aclose()

    summary = pipe.summary()
    report = {
        "exchange": args.exchange,
        "started_at": t0.isoformat(),
        "finished_at": datetime.now(UTC).isoformat(),
        "warmup": warm,
        "summary": summary,
        "events": events,
        "orders_sent": pipe.orders_sent,
    }
    assert pipe.orders_sent == 0, "LIVE PAPER sent an order — must never happen"

    if args.json:
        print(json.dumps(report, indent=2, default=str))  # reine JSON-Ausgabe
    else:
        print(f"\n=== LIVE PAPER — {args.exchange} — {len(events)} Events ===")
        print("Warmup:", json.dumps(warm, indent=2))
        for line in events[-60:]:
            print(" ", line)
        print("\nSummary:", json.dumps(summary, indent=2, default=str))
        print(f"\norders_sent = {pipe.orders_sent}  (Invariante: 0)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
