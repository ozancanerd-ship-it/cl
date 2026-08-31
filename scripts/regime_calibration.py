#!/usr/bin/env python
"""OOS-Kalibrierungsanalyse des Regime-Gates — **Forschung, kein Parameter-Tuning nach Winrate**.

Frage: Hat das Regime-Signal echten Informationswert für die **Entry-Qualität**? Und wenn ja,
welche Gate-Variante liefert OOS die beste Balance aus Qualität (Expectancy/PF/DD) und Coverage,
**stabil** über die Zeit?

Vorgehen:

1. **Regime-Zustandsreihe** an gesampelten M5-cutoffs (D1/H4/M15 directional/vol/phase, Agreement,
   MTF-Disagreement, Default-Gate-Ergebnis).
2. **Forward-Probe** — standardisierter, NICHT getunter Swing-Test je cutoff: Entry am Close,
   SL = ``sl_atr``·ATR(M5), TP = ``rr``·SL-Distanz, Zeit-Stop ``max_hold``. Worst-case-Fill.
   Ergibt saubere R-Ergebnisse (kein Strategie-Ergebnis — reiner Signal-Qualitäts-Probe).
3. **Aggregation** je Regime-Bucket, getrennt **In-Sample / Out-of-Sample** (zeitlicher Split).
4. **Gate-Varianten** (reine Funktionen der Regime-Zustände) → gefilterte Subsets → Kennzahlen
   IS **und** OOS. Übernahme nur bei OOS-Vorteil.
5. **Walk-Forward**-Folds für die Stabilität.

Kennzahlen je Bucket/Variante: n, expectancy_R, win_rate, profit_factor, avg_R, median_R,
stdev_R, max_dd_R (der geordneten R-Folge), longest_loss_streak.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import math
import statistics
from collections import Counter
from collections.abc import Callable, Sequence
from datetime import datetime

from trading_agent.core.enums import (
    Bias,
    RegimeDirectional,
    Timeframe,
)
from trading_agent.core.models import OHLCV
from trading_agent.core.time import parse_timestamp
from trading_agent.data.repository import MarketDataRepository
from trading_agent.engine.replay import AssemblerConfig, MarketContextAssembler
from trading_agent.strategy.evaluate import EvaluateParams, _build_mtf


@dataclasses.dataclass(frozen=True, slots=True)
class Sample:
    ts: datetime
    symbol: str
    # Regime-Zustände
    d1_dir: str
    d1_vol: str
    d1_phase: str
    d1_dscore: float
    h4_dir: str
    h4_vol: str
    m15_vol: str
    htf_bias: str
    agreement: str  # "aligned" | "one_unclear" | "conflict"
    mtf_disagreement: float
    gate_ok: bool
    gate_reason: str
    # Forward-Probe-Ergebnisse (R), in Bias-Richtung
    fwd_r: float | None  # None wenn htf_bias == none
    fwd_r_long: float
    fwd_r_short: float


# ------------------------------------------------------------------------------- Forward-Probe


def _probe(
    bars: Sequence[OHLCV],
    i: int,
    atr: float,
    *,
    direction: int,
    sl_atr: float,
    rr: float,
    max_hold: int,
) -> float:
    """R-Ergebnis eines standardisierten Entries am Close von ``bars[i]``. ``direction`` ±1."""
    if atr <= 0 or i + 1 >= len(bars):
        return 0.0
    entry = bars[i].close
    r_unit = sl_atr * atr
    sl = entry - direction * r_unit
    tp = entry + direction * rr * r_unit
    end = min(len(bars), i + 1 + max_hold)
    for j in range(i + 1, end):
        b = bars[j]
        hit_sl = b.low <= sl if direction > 0 else b.high >= sl
        hit_tp = b.high >= tp if direction > 0 else b.low <= tp
        if hit_sl and hit_tp:
            return -1.0  # worst-case: SL zuerst
        if hit_sl:
            return -1.0
        if hit_tp:
            return rr
    # Zeit-Stop: mark-to-close
    return direction * (bars[end - 1].close - entry) / r_unit


def _atr_at(bars: Sequence[OHLCV], i: int, period: int = 14) -> float:
    if i < period:
        return 0.0
    trs = []
    for k in range(i - period + 1, i + 1):
        h, low, pc = bars[k].high, bars[k].low, bars[k - 1].close
        trs.append(max(h - low, abs(h - pc), abs(low - pc)))
    return statistics.fmean(trs)


# ------------------------------------------------------------------------------- Sammeln


def _agreement(d1: RegimeDirectional, h4: RegimeDirectional) -> str:
    u = RegimeDirectional.UNCLEAR
    if d1 is u and h4 is u:
        return "both_unclear"
    if d1 is u or h4 is u:
        return "one_unclear"
    if {d1, h4} == {RegimeDirectional.TREND_UP, RegimeDirectional.TREND_DOWN}:
        return "conflict"
    return "aligned"


def collect(
    repo: MarketDataRepository,
    symbol: str,
    start: datetime,
    end: datetime,
    *,
    every: int,
    sl_atr: float,
    rr: float,
    max_hold: int,
) -> list[Sample]:
    from trading_agent.analysis.regime import disagreement

    ep = EvaluateParams()
    asm = MarketContextAssembler(
        repo, AssemblerConfig(instrument=symbol, warmup_bars=300, read_native_higher=True)
    )
    asm.bind(start, end)
    all_m5 = repo.read_ohlcv(symbol, Timeframe.M5, start, end, as_of=end)
    idx = {b.close_time: k for k, b in enumerate(all_m5)}
    out: list[Sample] = []

    for k in range(0, len(all_m5), every):
        bar = all_m5[k]
        cutoff = bar.close_time
        try:
            mtf = _build_mtf(asm.at(cutoff), ep)
        except Exception:
            continue
        d1, h4, m15 = mtf.d1, mtf.h4, mtf.m15
        if d1 is None or h4 is None:
            continue
        atr = _atr_at(all_m5, k)
        fwd_long = _probe(all_m5, k, atr, direction=1, sl_atr=sl_atr, rr=rr, max_hold=max_hold)
        fwd_short = _probe(all_m5, k, atr, direction=-1, sl_atr=sl_atr, rr=rr, max_hold=max_hold)
        bias = mtf.htf_bias
        # Probe-Richtung: HTF-Bias, sonst D1-Richtung, sonst Slope-Vorzeichen (D1). So haben
        # AUCH die ~98 % Samples ohne merged HTF-Bias ein Forward-Ergebnis — nötig, um die
        # Aussagekraft von agreement/vol/phase überhaupt zu messen.
        d1d = d1.regime.directional
        if bias is Bias.LONG:
            fwd_bias = fwd_long
        elif bias is Bias.SHORT:
            fwd_bias = fwd_short
        elif d1d is RegimeDirectional.TREND_UP:
            fwd_bias = fwd_long
        elif d1d is RegimeDirectional.TREND_DOWN:
            fwd_bias = fwd_short
        elif d1.regime.slope_norm > 0.02:
            fwd_bias = fwd_long
        elif d1.regime.slope_norm < -0.02:
            fwd_bias = fwd_short
        else:
            fwd_bias = None
        out.append(
            Sample(
                ts=cutoff,
                symbol=symbol,
                d1_dir=d1.regime.directional.value,
                d1_vol=d1.regime.volatility.value,
                d1_phase=d1.regime.phase.value,
                d1_dscore=round(d1.regime.directional_score, 3),
                h4_dir=h4.regime.directional.value,
                h4_vol=h4.regime.volatility.value,
                m15_vol=m15.regime.volatility.value if m15 is not None else "n/a",
                htf_bias=bias.value,
                agreement=_agreement(d1.regime.directional, h4.regime.directional),
                mtf_disagreement=round(
                    disagreement(d1.regime.directional, h4.regime.directional), 3
                ),
                gate_ok=mtf.regime_ok,
                gate_reason=(
                    mtf.htf_regime_gate.reason.value
                    if mtf.htf_regime_gate.reason is not None
                    else "ok"
                ),
                fwd_r=fwd_bias,
                fwd_r_long=round(fwd_long, 4),
                fwd_r_short=round(fwd_short, 4),
            )
        )
        _ = idx
    return out


# ------------------------------------------------------------------------------- Kennzahlen


def stats(rs: Sequence[float]) -> dict[str, float | int]:
    if not rs:
        return {"n": 0}
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r < 0]
    gp, gl = sum(wins), -sum(losses)
    pf = gp / gl if gl > 0 else (math.inf if gp > 0 else 0.0)
    eq, peak, mdd = 0.0, 0.0, 0.0
    streak = worst = 0
    for r in rs:
        eq += r
        peak = max(peak, eq)
        mdd = max(mdd, peak - eq)
        streak = streak + 1 if r < 0 else 0
        worst = max(worst, streak)
    return {
        "n": len(rs),
        "expectancy_r": round(statistics.fmean(rs), 4),
        "win_rate": round(len(wins) / len(rs), 4),
        "profit_factor": round(pf, 3) if pf != math.inf else "inf",
        "median_r": round(statistics.median(rs), 4),
        "stdev_r": round(statistics.pstdev(rs), 4) if len(rs) > 1 else 0.0,
        "max_dd_r": round(mdd, 3),
        "longest_loss_streak": worst,
        "total_r": round(sum(rs), 3),
    }


def _bias_r(samples: Sequence[Sample]) -> list[float]:
    return [s.fwd_r for s in samples if s.fwd_r is not None]


# ------------------------------------------------------------------------------- Gate-Varianten


def _v0_baseline(s: Sample) -> bool:
    return s.gate_ok


def _v1_allow_one_unclear(s: Sample) -> bool:
    if s.agreement in ("both_unclear", "conflict"):
        return False
    return s.d1_vol != "extreme" and s.h4_vol != "extreme"


def _v2_d1_only(s: Sample) -> bool:
    return s.d1_dir in ("trend_up", "trend_down") and s.d1_vol != "extreme"


def _v3_only_extreme_blocked(s: Sample) -> bool:
    if s.agreement in ("both_unclear", "conflict"):
        return False
    return s.d1_vol != "extreme"  # LOW + HIGH erlaubt, kein M15-Block


def _v4_regime_confidence(s: Sample, *, thr: float = 0.55) -> bool:
    # weiche Regime-Confidence ∈ [0,1]
    dir_term = s.d1_dscore if s.d1_dir != "unclear" else 0.0
    agree_term = {"aligned": 1.0, "one_unclear": 0.5, "both_unclear": 0.0, "conflict": 0.0}[
        s.agreement
    ]
    vol_pen = {"low": 0.85, "normal": 1.0, "high": 0.8, "extreme": 0.0}[s.d1_vol]
    conf = (0.5 * dir_term + 0.5 * agree_term) * vol_pen
    return conf >= thr


def _v5_no_m15_vol_block(s: Sample) -> bool:
    # Default-Gate, aber M15-EXTREME zählt nicht als Block (nur D1/H4)
    if s.agreement in ("both_unclear", "conflict"):
        return False
    if s.d1_vol == "extreme" or s.h4_vol == "extreme":
        return False
    return not (s.d1_vol == "low" or s.h4_vol == "low")  # forbid_low_vol wie Baseline


VARIANTS: dict[str, Callable[[Sample], bool]] = {
    "V0_baseline": _v0_baseline,
    "V1_allow_one_unclear": _v1_allow_one_unclear,
    "V2_d1_only": _v2_d1_only,
    "V3_only_extreme_blocked": _v3_only_extreme_blocked,
    "V4_regime_confidence": _v4_regime_confidence,
    "V5_no_m15_vol_block": _v5_no_m15_vol_block,
}


# ------------------------------------------------------------------------------- Main


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", default="data/repository_real")
    ap.add_argument("--symbols", nargs="+", default=["BTCUSDT", "ETHUSDT"])
    ap.add_argument("--start", default="2024-06-01")
    ap.add_argument("--split", default="2024-12-01", help="IS < split <= OOS")
    ap.add_argument("--end", default="2025-06-30")
    ap.add_argument("--every", type=int, default=12, help="jeder N-te M5 (12 = stündlich)")
    ap.add_argument("--sl-atr", type=float, default=1.5)
    ap.add_argument("--rr", type=float, default=2.0)
    ap.add_argument("--max-hold", type=int, default=96)
    ap.add_argument("--out", default="data/repository_real/regime_calibration.json")
    ap.add_argument(
        "--reuse-samples", action="store_true", help="Sample-Dump wiederverwenden (kein _build_mtf)"
    )
    args = ap.parse_args()

    start, split, end = (
        parse_timestamp(args.start),
        parse_timestamp(args.split),
        parse_timestamp(args.end),
    )

    dump_path = args.out.replace(".json", "_samples.json")
    if args.reuse_samples:
        with open(dump_path) as _fh:
            raw = json.load(_fh)
        all_samples = [Sample(**{**r, "ts": parse_timestamp(r["ts"])}) for r in raw]
    else:
        all_samples = []
        for sym in args.symbols:
            all_samples += collect(
                MarketDataRepository(args.repo),
                sym,
                start,
                end,
                every=args.every,
                sl_atr=args.sl_atr,
                rr=args.rr,
                max_hold=args.max_hold,
            )
        with open(dump_path, "w") as fh:
            json.dump([{**dataclasses.asdict(s), "ts": s.ts.isoformat()} for s in all_samples], fh)

    is_s = [s for s in all_samples if s.ts < split]
    oos_s = [s for s in all_samples if s.ts >= split]

    def coverage(ss: Sequence[Sample]) -> dict:
        n = len(ss) or 1
        return {
            "n": len(ss),
            "gate_ok_pct": round(100 * sum(s.gate_ok for s in ss) / n, 2),
            "gate_reason": {
                k: round(100 * v / n, 2)
                for k, v in Counter(s.gate_reason for s in ss).most_common()
            },
            "d1_dir": {
                k: round(100 * v / n, 2) for k, v in Counter(s.d1_dir for s in ss).most_common()
            },
            "d1_vol": {
                k: round(100 * v / n, 2) for k, v in Counter(s.d1_vol for s in ss).most_common()
            },
            "d1_phase": {
                k: round(100 * v / n, 2) for k, v in Counter(s.d1_phase for s in ss).most_common()
            },
            "agreement": {
                k: round(100 * v / n, 2) for k, v in Counter(s.agreement for s in ss).most_common()
            },
            "htf_bias": {
                k: round(100 * v / n, 2) for k, v in Counter(s.htf_bias for s in ss).most_common()
            },
        }

    def by_bucket(ss: Sequence[Sample], key: Callable[[Sample], str]) -> dict:
        groups: dict[str, list[float]] = {}
        for s in ss:
            if s.fwd_r is None:
                continue
            groups.setdefault(key(s), []).append(s.fwd_r)
        return {k: stats(v) for k, v in sorted(groups.items())}

    def probe_link(ss: Sequence[Sample]) -> dict:
        return {
            "all_bias_directional": stats(_bias_r(ss)),
            "gate_ok_true": stats([s.fwd_r for s in ss if s.gate_ok and s.fwd_r is not None]),
            "gate_ok_false": stats([s.fwd_r for s in ss if not s.gate_ok and s.fwd_r is not None]),
            "by_d1_dir": by_bucket(ss, lambda s: s.d1_dir),
            "by_d1_vol": by_bucket(ss, lambda s: s.d1_vol),
            "by_d1_phase": by_bucket(ss, lambda s: s.d1_phase),
            "by_agreement": by_bucket(ss, lambda s: s.agreement),
            "by_mtf_disagreement": by_bucket(
                ss,
                lambda s: (
                    f"{'lo' if s.mtf_disagreement < 0.34 else 'mid' if s.mtf_disagreement < 0.67 else 'hi'}"
                ),
            ),
        }

    def variant_eval(ss: Sequence[Sample]) -> dict:
        out = {}
        n_total = len(ss) or 1
        for name, fn in VARIANTS.items():
            rs = [s.fwd_r for s in ss if fn(s) and s.fwd_r is not None]
            out[name] = {
                **stats(rs),
                "coverage_pct": round(100 * sum(1 for s in ss if fn(s)) / n_total, 2),
            }
        return out

    # Walk-Forward: 4 Folds über den ganzen Zeitraum
    span_days = (end - start).days
    fold_days = span_days // 5
    folds = []
    from datetime import timedelta

    for f in range(4):
        tr0 = start + timedelta(days=f * fold_days)
        tr1 = tr0 + timedelta(days=2 * fold_days)
        te1 = tr1 + timedelta(days=fold_days)
        tr = [s for s in all_samples if tr0 <= s.ts < tr1]
        te = [s for s in all_samples if tr1 <= s.ts < te1]
        folds.append(
            {
                "fold": f,
                "train": [tr0.date().isoformat(), tr1.date().isoformat()],
                "test": [tr1.date().isoformat(), te1.date().isoformat()],
                "train_variants": variant_eval(tr),
                "test_variants": variant_eval(te),
            }
        )

    report = {
        "params": vars(args),
        "n_samples": len(all_samples),
        "is_window": [start.date().isoformat(), split.date().isoformat()],
        "oos_window": [split.date().isoformat(), end.date().isoformat()],
        "coverage": {"IS": coverage(is_s), "OOS": coverage(oos_s)},
        "quarterly_gate_ok_pct": _quarterly(all_samples),
        "quarterly_probe": {
            q: {
                "n": len(v),
                "all": stats([s.fwd_r for s in v if s.fwd_r is not None]),
                "gate_ok": stats([s.fwd_r for s in v if s.gate_ok and s.fwd_r is not None]),
                "V5": stats(
                    [s.fwd_r for s in v if _v5_no_m15_vol_block(s) and s.fwd_r is not None]
                ),
            }
            for q, v in _by_quarter(all_samples).items()
        },
        "by_symbol": {
            sym: {
                "all": stats(
                    [s.fwd_r for s in all_samples if s.symbol == sym and s.fwd_r is not None]
                ),
                "gate_ok": stats(
                    [
                        s.fwd_r
                        for s in all_samples
                        if s.symbol == sym and s.gate_ok and s.fwd_r is not None
                    ]
                ),
            }
            for sym in args.symbols
        },
        "probe_link": {"IS": probe_link(is_s), "OOS": probe_link(oos_s)},
        "variants": {"IS": variant_eval(is_s), "OOS": variant_eval(oos_s)},
        "walk_forward": folds,
    }
    with open(args.out, "w") as fh:
        json.dump(report, fh, indent=2, default=str)
    print(json.dumps(report, indent=2, default=str))
    return 0


def _by_quarter(samples: Sequence[Sample]) -> dict[str, list[Sample]]:
    q: dict[str, list[Sample]] = {}
    for s in samples:
        q.setdefault(f"{s.ts.year}Q{(s.ts.month - 1) // 3 + 1}", []).append(s)
    return dict(sorted(q.items()))


def _quarterly(samples: Sequence[Sample]) -> dict:
    q: dict[str, list[bool]] = {}
    for s in samples:
        key = f"{s.ts.year}Q{(s.ts.month - 1) // 3 + 1}"
        q.setdefault(key, []).append(s.gate_ok)
    return {
        k: {"n": len(v), "gate_ok_pct": round(100 * sum(v) / len(v), 2)}
        for k, v in sorted(q.items())
    }


if __name__ == "__main__":
    raise SystemExit(main())
