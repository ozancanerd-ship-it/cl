#!/usr/bin/env python
"""Shadow-Replay — fährt die **echte** ``strategy.evaluate``-Pipeline (SMC + Breakout-Retest)
über die vorhandene XAUUSDT-Historie und zeigt die konkreten Signale + verfolgt Shadow-Trades.

Kein Broker, keine Order. **H4-getaktet** (Bewertung bei jedem H4-Close statt bei jedem M5-Bar)
→ passt in enge Prozess-Zeitfenster; für den H4-nativen Breakout-Retest verlustfrei.

Für jedes tradebare Signal:  BUY/SELL · Entry · SL · TP1 · TP2 · TP3 · R:R · Score · Confidence ·
Setup · Begründung · Invalidation  — plus Freigabe-Status (LIVE / SHADOW).
Shadow-Trades: Entry → Fill → TP1/Partial → SL-Move → Exit. Alles ins Signal-Journal (JSONL).

    uv run python scripts/shadow_replay.py --symbol XAUUSDT --days 120
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta

from trading_agent.core.enums import AssetClass, Timeframe
from trading_agent.core.types import MarketContext
from trading_agent.data.repository import MarketDataRepository
from trading_agent.governance import ValidationRegistry, apply_live_gate
from trading_agent.runtime.bus import EventBus
from trading_agent.runtime.events import DecisionMade, PaperPositionChanged, SignalRevised
from trading_agent.runtime.signal_journal import SignalJournal
from trading_agent.scanner.opportunity import score_opportunity
from trading_agent.strategy.engine import EngineParams
from trading_agent.strategy.evaluate import EvaluateParams
from trading_agent.strategy.paper_live import PaperLiveRunner
from trading_agent.strategy.signal_report import build_signal_report

_HIGHER = (Timeframe.M15, Timeframe.H4, Timeframe.D1)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", default="data/repository_real")
    ap.add_argument("--symbol", default="XAUUSDT")
    ap.add_argument("--asset-class", default="gold")
    ap.add_argument(
        "--days", type=int, default=120, help="Fenster (Kalendertage) bis heute/Datenende"
    )
    ap.add_argument("--end", default=None)
    ap.add_argument("--journal", default="data/repository_real/live/shadow_replay_journal.jsonl")
    ap.add_argument("--validation-config", default="config/setup_validation.json")
    ap.add_argument("--risk-pct", type=float, default=1.0)
    args = ap.parse_args()

    repo = MarketDataRepository(args.repo)
    m5_all = repo.read_ohlcv(
        args.symbol,
        Timeframe.M5,
        datetime(2000, 1, 1, tzinfo=UTC),
        datetime(2100, 1, 1, tzinfo=UTC),
    )
    if not m5_all:
        print(f"keine M5-Daten für {args.symbol}")
        return 1
    end = (
        datetime.fromisoformat(args.end).replace(tzinfo=UTC) if args.end else m5_all[-1].close_time
    )
    start = end - timedelta(days=args.days)
    warm = end - timedelta(days=args.days + 400)  # HTF-Vorlauf

    m5 = [b for b in m5_all if warm <= b.open_time < end]
    higher = {
        tf: repo.read_ohlcv(args.symbol, tf, datetime(2000, 1, 1, tzinfo=UTC), end)
        for tf in _HIGHER
    }
    h4 = [b for b in higher[Timeframe.H4] if warm <= b.open_time < end]
    print(f"{args.symbol}: M5={len(m5)} H4={len(h4)}  Fenster {start.date()}..{end.date()}")

    registry = ValidationRegistry.from_file(args.validation_config)
    bus = EventBus(raise_on_handler_error=False)
    journal = SignalJournal(args.journal, build_report=build_signal_report)
    journal.configure(risk_pct=args.risk_pct, apply_live_gate=apply_live_gate, registry=registry)
    journal.attach(bus)

    ep = EngineParams(evaluate=EvaluateParams(asset_class=AssetClass(args.asset_class)))
    runner = PaperLiveRunner(engine_params=ep)

    signals: list[dict] = []
    trades: list[dict] = []

    async def _cap_dec(ev: DecisionMade) -> None:
        if ev.decision_type not in ("buy", "sell"):
            return
        gated = apply_live_gate(ev.result, registry=registry)
        rep = build_signal_report(
            gated,
            opportunity=score_opportunity(gated, asset_class=args.asset_class),
            risk_pct=args.risk_pct,
        )
        if rep is not None:
            signals.append({"ts": ev.ts.isoformat(), **rep.as_dict()})

    async def _cap_trade(ev: PaperPositionChanged) -> None:
        trades.append({"ts": ev.ts.isoformat(), "change": ev.change, "realized_r": ev.realized_r})

    bus.subscribe(DecisionMade, _cap_dec)
    bus.subscribe(PaperPositionChanged, _cap_trade)
    bus.subscribe(SignalRevised, lambda e: None)

    import asyncio

    async def run() -> None:
        m5_by_close = sorted(m5, key=lambda b: b.close_time)
        idx = 0
        # ein Tick je H4-Close (nur die im Fenster)
        for hb in [b for b in h4 if start <= b.open_time < end]:
            cutoff = hb.close_time
            while idx < len(m5_by_close) and m5_by_close[idx].close_time <= cutoff:
                idx += 1
            win_m5 = m5_by_close[:idx]
            if len(win_m5) < 250:
                continue
            series: dict[Timeframe, tuple] = {Timeframe.M5: tuple(win_m5[-500:])}
            for tf in _HIGHER:
                series[tf] = tuple(b for b in higher[tf] if b.close_time <= cutoff)
            mc = MarketContext(
                instrument=args.symbol,
                base_timeframe=Timeframe.M5,
                information_cutoff=cutoff,
                series=series,  # type: ignore[arg-type]
            )
            step = runner.feed(mc)
            tick = step.tick
            d = tick.result.decision
            gated = apply_live_gate(tick.result, registry=registry)
            await bus.publish(
                DecisionMade(
                    ts=cutoff,
                    instrument=args.symbol,
                    decision_type=d.decision.value,
                    setup_state=d.setup_state.value,
                    score=d.score,
                    confidence=d.confidence,
                    result=gated,
                )
            )
            if tick.signal is not None and (tick.signal.is_new or tick.signal.changed):
                sig = tick.signal.signal
                await bus.publish(
                    SignalRevised(
                        ts=cutoff,
                        instrument=args.symbol,
                        signal_id=sig.signal_id,
                        state=sig.state.value,
                        change="new" if tick.signal.is_new else "revised",
                        signal=tick.signal,
                    )
                )
            for pos, change in ((tick.opened, "OPENED"), (tick.closed, "CLOSED")):
                if pos is not None:
                    await bus.publish(
                        PaperPositionChanged(
                            ts=cutoff,
                            instrument=args.symbol,
                            change=change,
                            realized_r=getattr(pos, "realized_r", None),
                            position=pos,
                        )
                    )
            if tick.position is not None:
                await bus.publish(
                    PaperPositionChanged(
                        ts=cutoff,
                        instrument=args.symbol,
                        change=str(getattr(tick.position.event, "value", "UPDATE")).upper(),
                        realized_r=getattr(tick.position.position, "realized_r", None),
                        position=tick.position.position,
                    )
                )

    asyncio.run(run())

    print(f"\n=== {len(signals)} tradebare Signal(e) · {len(trades)} Shadow-Trade-Events ===")
    for s in signals:
        r = build_signal_report  # nur für Typ; wir haben schon as_dict
        _ = r
        print(
            f"\n[{s['ts']}]  {s.get('live_eligibility', '?').upper()}  "
            f"{s['action']} {s['instrument']} {s['direction']}  ({s['setup_id']})"
        )
        print(
            f"  Entry {s['entry']}  SL {s['stop_loss']}  TP1 {s['tp1']}  TP2 {s['tp2']}  TP3 {s['tp3']}"
        )
        print(
            f"  R:R→TP2 {s['rr_to_tp2']}  Blended {s['blended_rr']}  "
            f"Score {s['opportunity_score']}  Confidence {s['confidence_pct']}"
        )
        print(f"  Warum: {'; '.join(s['why'])}")
        print(f"  Invalidation: {s['invalidation']}")
    print(f"\nJournal geschrieben: {args.journal}  ({journal.counts})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
