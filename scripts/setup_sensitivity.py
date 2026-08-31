#!/usr/bin/env python
"""Parameter-Sensitivität von SETUP-BREAKOUT-RETEST-01 — Overfitting-Test.

Repliziert den **integrierten** ``detect_breakout_retest`` über die Yahoo-H4-Historie
(XAUUSD-YF + FX-YF), variiert die Kernparameter je ±30 % und misst, ob die OOS-Edge hält.
Bricht der Vorteil bei kleinen Störungen zusammen → Knife-Edge-Fit → Vorsicht.

    uv run python scripts/setup_sensitivity.py --split 2025-04-01
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import statistics
from datetime import datetime, timedelta
from types import SimpleNamespace as NS

from trading_agent.core.enums import Direction, Timeframe
from trading_agent.core.time import parse_timestamp
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
_MAX_HOLD = 60
_COST_R = 0.06


def _d1_states(h4: list, d1: list) -> list:
    import bisect

    sw = detect_swings(d1, _D1, left=2, right=2, min_leg_atr=0.5)
    sw.sort(key=lambda s: s.confirmed_at)
    conf = [s.confirmed_at for s in sw]
    out = []
    for b in h4:
        vis = sw[: bisect.bisect_right(conf, b.close_time)]
        out.append(
            derive_structure_state(vis, _D1, min_swings=2).directional if len(vis) >= 4 else None
        )
    return out


def _simulate(h4: list, at: int, direction: int, stop: float, rr: float) -> tuple[float, int]:
    """Scaled-Management wie in setup_research.py: 50 % @ +1R, SL→BE, Rest bis +rr R (Runner).
    Rückgabe: (realized_r, bars_held) — bars_held für die exakte Positions-Sperre."""
    ei = at + 1
    if ei >= len(h4):
        return 0.0, 0
    entry = h4[ei].open
    r = abs(entry - stop)
    if r <= 0:
        return 0.0, 0
    d = direction
    tp1 = entry + d * 1.0 * r
    tp = entry + d * rr * r
    sl = stop
    booked = 0.0
    part_left = 1.0
    be = False
    last = min(len(h4) - 1, ei + _MAX_HOLD)
    end = last
    for j in range(ei, last + 1):
        b = h4[j]
        hit_sl = (b.low <= sl) if d > 0 else (b.high >= sl)
        hit_tp1 = (b.high >= tp1) if d > 0 else (b.low <= tp1)
        hit_tp = (b.high >= tp) if d > 0 else (b.low <= tp)
        if hit_sl:
            booked += part_left * (d * (sl - entry) / r)
            end = j
            part_left = 0.0
            break
        if hit_tp:
            booked += part_left * rr
            end = j
            part_left = 0.0
            break
        if hit_tp1 and part_left == 1.0:
            booked += 0.5
            part_left = 0.5
            sl = entry
            be = True
    if part_left > 0:
        booked += part_left * (d * (h4[last].close - entry) / r)
    _ = be
    return booked - _COST_R, end - ei


def _run(
    h4: list, d1st: list, atr: list, params: BreakoutRetestParams
) -> list[tuple[datetime, float]]:
    trades: list[tuple[datetime, float]] = []
    busy = -1
    for i in range(40, len(h4) - 1):
        if i <= busy:
            continue
        mtf = NS(
            instrument="X",
            information_cutoff=h4[i].close_time,
            h4=NS(bars=tuple(h4[: i + 1]), atr=atr[i] or 0.0),
            d1=NS(structure=NS(directional=d1st[i]), regime=NS(directional_score=0.7)),
        )
        rep = detect_breakout_retest(mtf, params=params)
        if rep.state is not BreakoutState.ARMED or rep.direction is None or rep.sl is None:
            continue
        d = 1 if rep.direction is Direction.LONG else -1
        r, held = _simulate(h4, i, d, float(rep.sl), params.tp2_r)
        trades.append((h4[i].open_time, r))
        busy = i + 1 + held  # exakte Positions-Sperre (eine Position je Symbol)
    return trades


def _metrics(rs: list[float]) -> dict[str, float]:
    if len(rs) < 3:
        return {"n": len(rs), "exp": 0.0, "pf": 0.0, "total": round(sum(rs), 2)}
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r < 0]
    gp, gl = sum(wins), -sum(losses)
    return {
        "n": len(rs),
        "exp": round(statistics.fmean(rs), 4),
        "pf": round(gp / gl, 3) if gl > 0 else 99.0,
        "total": round(sum(rs), 2),
        "win_rate": round(len(wins) / len(rs), 3),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", default="data/repository_real")
    ap.add_argument(
        "--symbols", nargs="+", default=["XAUUSD-YF", "EURUSD-YF", "GBPUSD-YF", "USDJPY-YF"]
    )
    ap.add_argument("--start", default="2023-01-01")
    ap.add_argument("--split", default="2025-04-01")
    ap.add_argument("--end", default="2026-08-29")
    ap.add_argument("--out", default="data/repository_real/research/setup_sensitivity.json")
    args = ap.parse_args()

    start, split, end = (parse_timestamp(x) for x in (args.start, args.split, args.end))
    repo = MarketDataRepository(args.repo)
    series = []
    for s in args.symbols:
        h4 = repo.read_ohlcv(s, _H4, start, end)
        d1 = repo.read_ohlcv(s, _D1, start, end)
        if len(h4) < 200 or len(d1) < 40:
            continue
        series.append((s, h4, _d1_states(h4, d1), atr_series(h4, 14)))

    base = BreakoutRetestParams()
    perturbations: dict[str, BreakoutRetestParams] = {"baseline": base}
    for field, lo, hi in [
        ("consolidation_bars", 10, 18),
        ("breakout_displacement_atr", 0.2, 0.45),
        ("retest_touch_atr", 0.35, 0.7),
        ("stop_buffer_atr", 0.2, 0.45),
        ("retest_window_bars", 8, 16),
        ("tp2_r", 2.5, 3.5),
    ]:
        perturbations[f"{field}={lo}"] = dataclasses.replace(base, **{field: lo})
        perturbations[f"{field}={hi}"] = dataclasses.replace(base, **{field: hi})

    embargo = timedelta(days=12)
    results: dict[str, object] = {}
    for name, p in perturbations.items():
        all_t: list[tuple[datetime, float]] = []
        for _s, h4, d1st, atr in series:
            all_t.extend(_run(h4, d1st, atr, p))
        is_r = [r for (t, r) in all_t if t < split - embargo]
        oos_r = [r for (t, r) in all_t if t >= split + embargo]
        results[name] = {"IS": _metrics(is_r), "OOS": _metrics(oos_r)}
        o = results[name]["OOS"]  # type: ignore[index]
        print(f"  {name:34s} OOS n={o['n']:>3} exp={o['exp']:+.3f} pf={o['pf']} total={o['total']}")

    oos_exps = [
        v["OOS"]["exp"]  # type: ignore[index]
        for k, v in results.items()
        if v["OOS"]["n"] >= 8  # type: ignore[index]
    ]
    verdict = {
        "oos_exp_min": round(min(oos_exps), 4) if oos_exps else None,
        "oos_exp_max": round(max(oos_exps), 4) if oos_exps else None,
        "all_perturbations_positive": all(e > 0 for e in oos_exps) if oos_exps else False,
        "n_perturbations_evaluated": len(oos_exps),
    }
    report = {"params": vars(args), "verdict": verdict, "results": results}
    with open(args.out, "w") as fh:
        json.dump(report, fh, indent=2, default=str)
    print(json.dumps(verdict, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
