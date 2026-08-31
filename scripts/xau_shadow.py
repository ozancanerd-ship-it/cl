#!/usr/bin/env python
"""XAUUSDT Shadow — findet alle Breakout-Retest-Signale auf der vorhandenen Gold-Historie,
rendert sie konkret (Entry/SL/TP1/TP2/TP3/RR/Score/Confidence/Warum/Invalidation) und
**verfolgt jeden Shadow-Trade** bar-für-bar: Entry → Fill → TP1 (+SL→BE) → TP2 → Exit.

Nutzt den **integrierten** Detektor ``detect_breakout_retest`` + ``build_signal_report`` +
``apply_live_gate`` (Freigabe = SHADOW, da IN_VALIDATION) + ``SignalJournal`` (JSONL).
Schnell (H4-nativ, kein M5-Rescan) → passt in enge Prozess-Zeitfenster.

**Kein Broker, keine Order.** Das ist der Shadow-/Forward-Validierungs-Lauf für S4.

    uv run python scripts/xau_shadow.py --symbol XAUUSD-YF --journal data/repository_real/live/xau_shadow.jsonl
"""

from __future__ import annotations

import argparse
import bisect
import json
from dataclasses import dataclass
from datetime import UTC, datetime

from trading_agent.core.enums import Direction, RiskTier, Timeframe
from trading_agent.data.repository import MarketDataRepository
from trading_agent.governance import ValidationRegistry, apply_live_gate
from trading_agent.scanner.opportunity import OppFactor, OpportunityScore
from trading_agent.strategy.decision import Decision
from trading_agent.strategy.evaluate import EvaluationResult
from trading_agent.strategy.primitives.atr import atr_series
from trading_agent.strategy.primitives.structure import derive_structure_state, structure_breaks
from trading_agent.strategy.primitives.swings import detect_swings
from trading_agent.strategy.setups.breakout_retest import BreakoutState, detect_breakout_retest
from trading_agent.strategy.signal_report import build_signal_report

_H4, _D1 = Timeframe.H4, Timeframe.D1


class _NS:
    def __init__(self, **kw: object) -> None:
        self.__dict__.update(kw)


@dataclass
class ShadowTrade:
    signal_ts: datetime
    direction: int
    entry: float
    sl: float
    tp1: float
    tp2: float
    r_unit: float
    events: list[dict]
    state: str = "PENDING"
    realized_r: float = 0.0
    part_left: float = 1.0
    be_moved: bool = False


def _score_for(rep: object, d1_trend: str) -> OpportunityScore:
    """Kompakter Opportunity-Score für ein ARMED Breakout-Retest (Setup-Reife 1.0)."""
    conf = float(getattr(rep, "confidence", 0.6) or 0.6)
    factors = (
        OppFactor("htf_bias_clarity", 0.85 if "trend" in d1_trend else 0.3, 12.0, True),
        OppFactor("structure_shift", 0.75, 10.0, True),
        OppFactor("entry_location", 0.7, 8.0, True),
        OppFactor("risk_reward", 0.75, 8.0, True),
        OppFactor("volatility_regime", 0.8, 8.0, True),
        OppFactor("data_confidence", conf, 6.0, True),
    )
    ctx = sum(f.value * f.weight for f in factors) / sum(f.weight for f in factors)
    score = 100.0 * (0.6 * ctx + 0.4 * (1.0 * conf))
    return OpportunityScore(
        instrument=str(getattr(rep, "instrument", "?")),
        information_cutoff=getattr(rep, "information_cutoff", datetime.now(UTC)),
        score=round(score, 1),
        direction=getattr(rep, "direction", None),
        setup_state="armed",
        setup_readiness=1.0,
        tier="B",
        strategy_score=None,
        factors=factors,
        unavailable=("news", "macro", "fundamentals", "correlation"),
        headline=f"ARMED {getattr(getattr(rep, 'direction', None), 'value', '')} · Breakout-Retest",
        asset_class="gold",
    )


def _manage(tr: ShadowTrade, bar: object, ts: datetime) -> None:
    d = tr.direction
    hi, lo = bar.high, bar.low  # type: ignore[attr-defined]
    if tr.state == "PENDING":
        # Fill, sobald der Preis den Entry berührt (Limit an der Bruchkante / Retest-Close)
        if (lo <= tr.entry <= hi) or (d > 0 and lo <= tr.entry) or (d < 0 and hi >= tr.entry):
            tr.state = "OPEN"
            tr.events.append(
                {"kind": "trade", "change": "FILLED", "ts": ts.isoformat(), "price": tr.entry}
            )
        else:
            return
    if tr.state in ("OPEN", "TP1"):
        hit_sl = (lo <= tr.sl) if d > 0 else (hi >= tr.sl)
        hit_tp1 = (hi >= tr.tp1) if d > 0 else (lo <= tr.tp1)
        hit_tp2 = (hi >= tr.tp2) if d > 0 else (lo <= tr.tp2)
        if hit_sl:
            tr.realized_r += tr.part_left * (d * (tr.sl - tr.entry) / tr.r_unit)
            tr.state = "CLOSED"
            tr.events.append(
                {
                    "kind": "trade",
                    "change": "BE_EXIT" if tr.be_moved else "SL",
                    "ts": ts.isoformat(),
                    "realized_r": round(tr.realized_r, 3),
                }
            )
            return
        if hit_tp2:
            tr.realized_r += tr.part_left * 3.0
            tr.state = "CLOSED"
            tr.events.append(
                {
                    "kind": "trade",
                    "change": "TP2",
                    "ts": ts.isoformat(),
                    "realized_r": round(tr.realized_r, 3),
                }
            )
            return
        if hit_tp1 and tr.state == "OPEN":
            tr.realized_r += 0.5 * 1.0
            tr.part_left = 0.5
            tr.sl = tr.entry
            tr.be_moved = True
            tr.state = "TP1"
            tr.events.append(
                {
                    "kind": "trade",
                    "change": "TP1_PARTIAL",
                    "ts": ts.isoformat(),
                    "sl_moved_to": tr.entry,
                    "realized_r": round(tr.realized_r, 3),
                }
            )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", default="data/repository_real")
    ap.add_argument("--symbol", default="XAUUSD-YF")
    ap.add_argument("--start", default="2023-01-01")
    ap.add_argument("--end", default="2026-08-29")
    ap.add_argument("--journal", default="data/repository_real/live/xau_shadow.jsonl")
    ap.add_argument("--validation-config", default="config/setup_validation.json")
    ap.add_argument("--max-hold", type=int, default=60)
    args = ap.parse_args()

    repo = MarketDataRepository(args.repo)
    a = datetime.fromisoformat(args.start).replace(tzinfo=UTC)
    b = datetime.fromisoformat(args.end).replace(tzinfo=UTC)
    h4 = repo.read_ohlcv(args.symbol, _H4, a, b)
    d1 = repo.read_ohlcv(args.symbol, _D1, a, b)
    if len(h4) < 200 or len(d1) < 40:
        print(f"zu wenig Daten für {args.symbol} (H4={len(h4)} D1={len(d1)})")
        return 1

    sw = detect_swings(d1, _D1, left=2, right=2, min_leg_atr=0.5)
    sw.sort(key=lambda s: s.confirmed_at)
    conf = [s.confirmed_at for s in sw]
    atr = atr_series(h4, 14)
    d1_brk = structure_breaks(d1, sw, _D1, min_swings=2)
    d1_brk.sort(key=lambda b: b.break_bar_timestamp)
    brk_ts = [b.break_bar_timestamp for b in d1_brk]
    d1_open = [b.open_time for b in d1]

    def d1_trend(ts: datetime) -> object:
        v = sw[: bisect.bisect_right(conf, ts)]
        return derive_structure_state(v, _D1, min_swings=2).directional if len(v) >= 4 else None

    def d1_ctx_at(ts: datetime) -> _NS:
        """PIT-D1-Kontext: Bars + Struktur-Breaks ≤ ts (für die S9-HTF-BOS-Konfluenz)."""
        nd = bisect.bisect_right(d1_open, ts)
        nb = bisect.bisect_right(brk_ts, ts)
        return _NS(
            bars=tuple(d1[:nd]),
            structure=_NS(directional=d1_trend(ts)),
            structure_breaks=tuple(d1_brk[:nb]),
            regime=_NS(directional_score=0.72),
        )

    registry = ValidationRegistry.from_file(args.validation_config)
    from pathlib import Path

    jpath = Path(args.journal)
    jpath.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []

    def emit(row: dict) -> None:
        row["logged_at"] = datetime.now(UTC).isoformat()
        lines.append(json.dumps(row, default=str))

    trades: list[ShadowTrade] = []
    rendered: list[str] = []
    open_trade: ShadowTrade | None = None

    for i in range(40, len(h4) - 1):
        ts = h4[i].close_time
        if open_trade is not None and open_trade.state != "CLOSED":
            _manage(open_trade, h4[i], ts)
            if open_trade.state == "CLOSED" or (i - _idx(h4, open_trade.signal_ts)) > args.max_hold:
                if open_trade.state != "CLOSED":
                    d = open_trade.direction
                    open_trade.realized_r += open_trade.part_left * (
                        d * (h4[i].close - open_trade.entry) / open_trade.r_unit
                    )
                    open_trade.state = "CLOSED"
                    open_trade.events.append(
                        {
                            "kind": "trade",
                            "change": "MAX_HOLD_EXIT",
                            "ts": ts.isoformat(),
                            "realized_r": round(open_trade.realized_r, 3),
                        }
                    )
                for e in open_trade.events:
                    emit({"instrument": args.symbol, **e})
                open_trade.events.clear()
                open_trade = None
        if open_trade is not None:
            continue

        trend = d1_trend(ts)
        mtf = _NS(
            instrument=args.symbol,
            information_cutoff=ts,
            h4=_NS(bars=tuple(h4[: i + 1]), atr=atr[i] or 0.0),
            d1=d1_ctx_at(ts),
        )
        rep = detect_breakout_retest(mtf)  # type: ignore[arg-type]
        if rep.state is not BreakoutState.ARMED or rep.direction is None or rep.sl is None:
            continue

        dec = Decision.trade(
            args.symbol,
            ts,
            rep.direction,
            entry=float(rep.entry),
            sl=float(rep.sl),
            tp1=float(rep.tp1),
            tp2=float(rep.tp2),  # type: ignore[arg-type]
            tier=RiskTier.B,
            tp3_ref=rep.tp3_ref,
            rr_to_tp2=rep.rr_to_tp2,
            blended_rr=rep.blended_rr,
            confidence=rep.confidence,
            chain_progress=rep.chain_progress,
            setup_id="SETUP-BREAKOUT-RETEST-01",
        )
        res = EvaluationResult(
            decision=dec,
            mtf=_NS(per_tf={}, htf_directional=_NS(value=getattr(trend, "value", "unclear"))),  # type: ignore[arg-type]
            scan=_NS(no_trade_reason=None),  # type: ignore[arg-type]
            no_trade=_NS(records=(), blocked=False, reasons=()),  # type: ignore[arg-type]
            breakout=rep,
            confluence=None,
            contradictions=None,
        )
        gated = apply_live_gate(res, registry=registry)
        opp = _score_for(rep, str(getattr(trend, "value", "")))
        sr = build_signal_report(gated, opportunity=opp, risk_pct=1.0)
        if sr is None:
            continue

        emit(
            {
                "kind": "signal",
                "instrument": args.symbol,
                "ts": ts.isoformat(),
                "eligibility": sr.live_eligibility,
                "report": sr.as_dict(),
                "live_gate": gated.live_gate.as_dict() if gated.live_gate else None,
            }
        )
        rendered.append(f"[{ts.date()}] {sr.as_text()}")

        d = 1 if rep.direction is Direction.LONG else -1
        r_unit = abs(float(rep.entry) - float(rep.sl))  # type: ignore[arg-type]
        open_trade = ShadowTrade(
            signal_ts=ts,
            direction=d,
            entry=float(rep.entry),
            sl=float(rep.sl),  # type: ignore[arg-type]
            tp1=float(rep.tp1),
            tp2=float(rep.tp2),
            r_unit=r_unit,
            events=[],  # type: ignore[arg-type]
        )
        trades.append(open_trade)

    jpath.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")

    closed = [t for t in trades if t.state == "CLOSED"]
    total_r = sum(t.realized_r for t in closed)
    wins = sum(1 for t in closed if t.realized_r > 0.02)
    print(f"\n{'=' * 64}")
    print(f"  {args.symbol} SHADOW  ·  {args.start} .. {args.end}")
    print(f"{'=' * 64}")
    for txt in rendered[-6:]:
        print("\n" + txt)
    print(f"\n{'=' * 64}")
    print(f"  {len(trades)} Shadow-Signale · {len(closed)} abgeschlossen")
    if closed:
        print(
            f"  Total {total_r:+.2f} R · Win-Rate {wins / len(closed):.0%} · "
            f"Expectancy {total_r / len(closed):+.3f} R"
        )
    print(f"  Journal: {jpath}  ({len(lines)} Zeilen)")
    print(f"{'=' * 64}\n")
    return 0


def _idx(bars: list, ts: datetime) -> int:
    for i, x in enumerate(bars):
        if x.close_time == ts:
            return i
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
