#!/usr/bin/env python
"""Diagnose: WARUM performt SETUP-BREAKOUT-RETEST-01 auf echtem Spot-Gold schlecht?

Rekonstruiert jedes ARMED-Signal (gleiche Maschinerie wie ``xau_shadow.py``) und misst
pro Trade: MFE/MAE (in R), Exit unter aktuellem Management **und** unter Alternativen
(struktureller Stop / breiterer Stop / Runner ohne BE / grösserer TP), D1-Trend,
Konsolidierungs-Breite, Ausbruch-Wucht, Retest-Tiefe, Entry-Stunde/Session, Forward-Return.

Rein diagnostisch — schreibt nichts, ändert keine Strategie. Grundlage für die
OOS-geprüften Verbesserungen in ``setup_research.py``.

    uv run python scripts/diag_gold_breakout.py --symbol XAUUSD --start 2023-01-01 --end 2024-02-01
"""

from __future__ import annotations

import argparse
import bisect
import json
import statistics
from dataclasses import dataclass, field
from datetime import UTC, datetime

from trading_agent.core.enums import Direction, Timeframe
from trading_agent.data.repository import MarketDataRepository
from trading_agent.strategy.primitives.atr import atr_series
from trading_agent.strategy.primitives.structure import derive_structure_state
from trading_agent.strategy.primitives.swings import detect_swings
from trading_agent.strategy.setups.breakout_retest import (
    BreakoutRetestParams,
    BreakoutState,
    detect_breakout_retest,
)

_H4, _D1 = Timeframe.H4, Timeframe.D1


class _NS:
    def __init__(self, **kw: object) -> None:
        self.__dict__.update(kw)


@dataclass
class Diag:
    ts: datetime
    direction: int
    entry: float
    sl: float
    tp1: float
    tp2: float
    atr: float
    r_dist: float
    r_atr: float
    cons_low: float
    cons_high: float
    cons_width_atr: float
    broken_level: float
    thrust_atr: float
    retest_depth_atr: float
    d1_trend: str
    confidence: float
    entry_hour: int
    # forward
    mfe_r: float = 0.0
    mae_r: float = 0.0
    bars_to_exit: int = 0
    fwd20_r: float = 0.0
    fwd40_r: float = 0.0
    exits: dict[str, float] = field(default_factory=dict)


def _sim_exit(
    bars: list,
    start: int,
    d: int,
    entry: float,
    sl: float,
    r_unit: float,
    *,
    tp2_r: float,
    be_at_r: float | None,
    max_hold: int = 60,
) -> tuple[float, int]:
    """Ein Management-Modell durchsimulieren (worst-case: SL vor TP auf derselben Bar).
    50% bei +1R (optional SL→BE), Rest bis +tp2_r R. be_at_r=None ⇒ kein BE-Move."""
    part_left = 1.0
    realized = 0.0
    cur_sl = sl
    tp1 = entry + d * 1.0 * r_unit
    tp2 = entry + d * tp2_r * r_unit
    moved = False
    for k in range(start, min(len(bars), start + max_hold)):
        hi, lo = bars[k].high, bars[k].low
        hit_sl = (lo <= cur_sl) if d > 0 else (hi >= cur_sl)
        hit_tp1 = (hi >= tp1) if d > 0 else (lo <= tp1)
        hit_tp2 = (hi >= tp2) if d > 0 else (lo <= tp2)
        if hit_sl:
            realized += part_left * (d * (cur_sl - entry) / r_unit)
            return realized, k - start
        if hit_tp2:
            realized += part_left * tp2_r
            return realized, k - start
        if hit_tp1 and part_left == 1.0:
            realized += 0.5 * 1.0
            part_left = 0.5
            if be_at_r is not None and be_at_r <= 1.0:
                cur_sl = entry
                moved = True
        if not moved and be_at_r is not None and part_left < 1.0:
            # BE-Move nachziehen, sobald +be_at_r erreicht (für be_at_r>1)
            fav = d * (bars[k].close - entry) / r_unit
            if fav >= be_at_r:
                cur_sl = entry
                moved = True
    # max-hold: zum Close glattstellen
    realized += part_left * (
        d * (bars[min(len(bars) - 1, start + max_hold - 1)].close - entry) / r_unit
    )
    return realized, max_hold


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", default="data/repository_real")
    ap.add_argument("--symbol", default="XAUUSD")
    ap.add_argument("--start", default="2023-01-01")
    ap.add_argument("--end", default="2024-02-01")
    ap.add_argument("--json", action="store_true")
    ap.add_argument(
        "--confluence",
        action="store_true",
        help="S9-HTF-BOS-Konfluenz aktivieren (Standard aus: diagnostiziert die Ur-6-Trades)",
    )
    args = ap.parse_args()

    repo = MarketDataRepository(args.repo)
    a0 = datetime.fromisoformat(args.start).replace(tzinfo=UTC)
    b0 = datetime.fromisoformat(args.end).replace(tzinfo=UTC)
    h4 = repo.read_ohlcv(args.symbol, _H4, a0, b0)
    d1 = repo.read_ohlcv(args.symbol, _D1, a0, b0)
    if len(h4) < 200 or len(d1) < 40:
        print(f"zu wenig Daten (H4={len(h4)} D1={len(d1)})")
        return 1

    sw = detect_swings(d1, _D1, left=2, right=2, min_leg_atr=0.5)
    sw.sort(key=lambda s: s.confirmed_at)
    conf = [s.confirmed_at for s in sw]
    atr = atr_series(h4, 14)
    # Standard: Konfluenz AUS — dieses Skript diagnostiziert die ursprünglichen 6 S4-Trades.
    # Der Fake-mtf hat keine D1-structure_breaks; mit Konfluenz an gäbe es 0 Signale.
    p = BreakoutRetestParams(require_htf_bos_confluence=args.confluence)

    def d1_trend(ts: datetime) -> object:
        v = sw[: bisect.bisect_right(conf, ts)]
        return derive_structure_state(v, _D1, min_swings=2).directional if len(v) >= 4 else None

    diags: list[Diag] = []
    last_exit_idx = -1

    for i in range(40, len(h4) - 1):
        if i <= last_exit_idx:
            continue
        ts = h4[i].close_time
        trend = d1_trend(ts)
        mtf = _NS(
            instrument=args.symbol,
            information_cutoff=ts,
            h4=_NS(bars=tuple(h4[: i + 1]), atr=atr[i] or 0.0),
            d1=_NS(structure=_NS(directional=trend), regime=_NS(directional_score=0.72)),
        )
        rep = detect_breakout_retest(mtf, params=p)  # type: ignore[arg-type]
        if rep.state is not BreakoutState.ARMED or rep.direction is None or rep.sl is None:
            continue

        d = 1 if rep.direction is Direction.LONG else -1
        entry, sl = float(rep.entry), float(rep.sl)
        r_unit = abs(entry - sl)
        atr_i = atr[i] or 1.0
        cons_lo, cons_hi = float(rep.consolidation_low), float(rep.consolidation_high)
        level = float(rep.broken_level)
        # forward walk from i+1 (fill an der nächsten Bar-Open ~ entry)
        start = i + 1
        mfe = mae = 0.0
        for k in range(start, min(len(h4), start + 60)):
            fav = d * (h4[k].high if d > 0 else h4[k].low) - d * entry
            adv = d * (h4[k].low if d > 0 else h4[k].high) - d * entry
            mfe = max(mfe, fav / r_unit)
            mae = min(mae, adv / r_unit)
        fwd20 = d * (h4[min(len(h4) - 1, start + 20)].close - entry) / r_unit
        fwd40 = d * (h4[min(len(h4) - 1, start + 40)].close - entry) / r_unit

        # struktureller Stop: hinter die andere Konsolidierungs-Grenze + 0.3 ATR
        struct_sl = (cons_lo - 0.3 * atr_i) if d > 0 else (cons_hi + 0.3 * atr_i)
        struct_r = abs(entry - struct_sl)
        exits = {}
        exits["current(BE@1R,TP2=3)"] = _sim_exit(
            h4, start, d, entry, sl, r_unit, tp2_r=3.0, be_at_r=1.0
        )[0]
        exits["noBE(TP2=3)"] = _sim_exit(h4, start, d, entry, sl, r_unit, tp2_r=3.0, be_at_r=None)[
            0
        ]
        exits["BE@2R(TP2=4)"] = _sim_exit(h4, start, d, entry, sl, r_unit, tp2_r=4.0, be_at_r=2.0)[
            0
        ]
        exits["wideSL1.6x_noBE_TP2=3"] = _sim_exit(
            h4, start, d, entry, entry - d * 1.6 * r_unit, 1.6 * r_unit, tp2_r=3.0, be_at_r=None
        )[0]
        r_struct, _ = _sim_exit(h4, start, d, entry, struct_sl, struct_r, tp2_r=3.0, be_at_r=None)
        exits["structSL_noBE_TP2=3"] = r_struct
        exits["structSL_BE@2R_TP2=5"] = _sim_exit(
            h4, start, d, entry, struct_sl, struct_r, tp2_r=5.0, be_at_r=2.0
        )[0]
        _, bexit = _sim_exit(h4, start, d, entry, sl, r_unit, tp2_r=3.0, be_at_r=1.0)

        # Kontext-Metriken: Ausbruch-Bar suchen (der Bar, dessen open_time == rep.breakout_bar)
        thrust = 0.0
        for bb in h4[max(0, i - 20) : i + 1]:
            if rep.breakout_bar is not None and bb.open_time == rep.breakout_bar:
                thrust = (d * (bb.close - level)) / atr_i
                break
        retest_depth = (d * (level - (h4[i].low if d > 0 else h4[i].high))) / atr_i

        diags.append(
            Diag(
                ts=ts,
                direction=d,
                entry=entry,
                sl=sl,
                tp1=float(rep.tp1),
                tp2=float(rep.tp2),
                atr=atr_i,
                r_dist=r_unit,
                r_atr=r_unit / atr_i,
                cons_low=cons_lo,
                cons_high=cons_hi,
                cons_width_atr=(cons_hi - cons_lo) / atr_i,
                broken_level=level,
                thrust_atr=round(thrust, 2),
                retest_depth_atr=round(retest_depth, 2),
                d1_trend=str(getattr(trend, "value", trend)),
                confidence=float(rep.confidence or 0),
                entry_hour=ts.hour,
                mfe_r=round(mfe, 2),
                mae_r=round(mae, 2),
                bars_to_exit=bexit,
                fwd20_r=round(fwd20, 2),
                fwd40_r=round(fwd40, 2),
                exits={k: round(v, 3) for k, v in exits.items()},
            )
        )
        last_exit_idx = i + max(1, bexit)

    _report(diags, args)
    return 0


def _report(diags: list[Diag], args: argparse.Namespace) -> None:
    if args.json:
        print(json.dumps([d.__dict__ for d in diags], default=str, indent=2))
        return
    n = len(diags)
    print(
        f"\n{'=' * 74}\n  GOLD BREAKOUT-RETEST · DIAGNOSE · {args.symbol} {args.start}..{args.end}"
    )
    print(f"  {n} ARMED-Signale\n{'=' * 74}")
    if not n:
        print("  keine Signale.")
        return
    for d in diags:
        print(
            f"\n  [{d.ts.date()} {d.entry_hour:02d}h] {'LONG ' if d.direction > 0 else 'SHORT'} "
            f"@ {d.entry:.2f}  SL {d.sl:.2f}  (R={d.r_dist:.2f} = {d.r_atr:.2f}·ATR)"
        )
        print(
            f"      D1-Trend={d.d1_trend}  cons_width={d.cons_width_atr:.2f}·ATR  "
            f"thrust={d.thrust_atr}·ATR  retest_depth={d.retest_depth_atr}·ATR  conf={d.confidence:.2f}"
        )
        print(
            f"      MFE={d.mfe_r:+.2f}R  MAE={d.mae_r:+.2f}R  bars_to_exit={d.bars_to_exit}  "
            f"fwd20={d.fwd20_r:+.2f}R  fwd40={d.fwd40_r:+.2f}R"
        )
        print("      Exits: " + "  ".join(f"{k}={v:+.2f}" for k, v in d.exits.items()))

    def col(key: str) -> list[float]:
        return [d.exits[key] for d in diags]

    print(f"\n{'-' * 74}\n  AGGREGAT ({n} Trades)\n{'-' * 74}")
    for key in diags[0].exits:
        rs = col(key)
        wr = sum(1 for x in rs if x > 0.02) / n
        pf_num = sum(x for x in rs if x > 0)
        pf_den = -sum(x for x in rs if x < 0)
        pf = pf_num / pf_den if pf_den > 0 else 99.0
        print(
            f"  {key:24} total={sum(rs):+6.2f}R  exp={statistics.mean(rs):+.3f}R  "
            f"WR={wr:.0%}  PF={pf:.2f}"
        )
    mfes = [d.mfe_r for d in diags]
    maes = [d.mae_r for d in diags]
    print(
        f"\n  MFE  median={statistics.median(mfes):+.2f}R  max={max(mfes):+.2f}R  "
        f"<1R: {sum(1 for x in mfes if x < 1.0)}/{n}"
    )
    print(
        f"  MAE  median={statistics.median(maes):+.2f}R  "
        f"trades mit MAE<=-1R (voller Stop getroffen): {sum(1 for x in maes if x <= -1.0)}/{n}"
    )
    print(
        f"  R-Distanz  median={statistics.median([d.r_atr for d in diags]):.2f}·ATR  "
        f"(<1·ATR: {sum(1 for d in diags if d.r_atr < 1.0)}/{n})"
    )
    longs = sum(1 for d in diags if d.direction > 0)
    print(f"  Richtung: {longs} LONG / {n - longs} SHORT")
    print(f"  Entry-Stunden: {sorted(d.entry_hour for d in diags)}")
    fast = sum(1 for d in diags if d.bars_to_exit <= 3)
    print(f"  Schnelle Stopouts (<=3 Bars = 12h): {fast}/{n}")
    print(f"{'=' * 74}\n")


if __name__ == "__main__":
    raise SystemExit(main())
