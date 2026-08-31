#!/usr/bin/env python
"""Struktur-Klassifikator-Kalibrierung — ``derive_structure_state()`` **isoliert** vom Regime-Gate.

Frage: klassifiziert ``detect_swings`` + ``derive_structure_state`` den *tatsächlichen*
Struktur-Zustand (Trend / Range) über einen vollen Marktzyklus zuverlässig — und trägt die
Klassifikation Information über das spätere Ergebnis?

**Nicht** auf Trade-Count optimieren. Kriterium = robuster OOS-Vorteil (Accuracy-Proxy +
Expectancy), sonst Baseline behalten.

Methodik: IS → OOS → Sensitivity-Sweep → Walk-Forward. Truth-Proxy = realisierte Vorwärts-
Richtung auf D1 (ATR-normiert + Pfad-Geradlinigkeit). R-Probe = standardisierter Entry in
Klassifikations-Richtung (worst-case Fill), rein als Signalqualitäts-Maß.

    uv run python scripts/structure_calibration.py \
        --repo data/repository_real \
        --symbols BTCUSDT ETHUSDT SOLUSDT BNBUSDT XRPUSDT DOGEUSDT \
        --start 2023-01-01 --split 2024-05-01 --end 2025-06-30 --tf D1
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import math
import statistics
from collections.abc import Sequence
from datetime import datetime

from trading_agent.core.enums import RegimeDirectional, Timeframe
from trading_agent.core.models import OHLCV
from trading_agent.core.time import parse_timestamp
from trading_agent.data.repository import MarketDataRepository
from trading_agent.strategy.primitives.structure import derive_structure_state
from trading_agent.strategy.primitives.swings import detect_swings

# --------------------------------------------------------------------------------------------
# Klassifikator-Varianten (nur die Struktur-Ebene — kein Slope, kein Vol, kein Gate)
# --------------------------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class StructVariant:
    key: str
    left: int
    right: int
    min_leg_atr: float
    min_swings: int
    note: str


VARIANTS: tuple[StructVariant, ...] = (
    StructVariant("V0_baseline", 2, 2, 0.5, 2, "aktueller Default"),
    StructVariant("V1_min_swings_1", 2, 2, 0.5, 1, "lockerer: nur 2 monotone Paare"),
    StructVariant("V2_min_swings_3", 2, 2, 0.5, 3, "strenger: 4 monotone Paare"),
    StructVariant("V3_fractal_3", 3, 3, 0.5, 2, "signifikantere Swings (left/right 3)"),
    StructVariant("V4_fractal_3_ms_3", 3, 3, 0.5, 3, "signifikantere Swings + strenger"),
    StructVariant("V5_leg_1atr", 2, 2, 1.0, 2, "größere Mindest-Leg (1·ATR)"),
    StructVariant("V6_fractal_3_leg_1atr", 3, 3, 1.0, 2, "beides: signifikantere + größere Legs"),
)


def classify(bars: Sequence[OHLCV], tf: Timeframe, v: StructVariant) -> RegimeDirectional:
    if len(bars) < 4 * (v.min_swings + 2):
        return RegimeDirectional.UNCLEAR
    sw = detect_swings(bars, tf, left=v.left, right=v.right, min_leg_atr=v.min_leg_atr)
    return derive_structure_state(sw, tf, min_swings=v.min_swings).directional


# --------------------------------------------------------------------------------------------
# Truth-Proxy + R-Probe (auf der Klassifikations-TF, meist D1)
# --------------------------------------------------------------------------------------------


def _atr(bars: Sequence[OHLCV], i: int, period: int = 14) -> float:
    if i < period:
        return 0.0
    trs = []
    for k in range(i - period + 1, i + 1):
        h, low, pc = bars[k].high, bars[k].low, bars[k - 1].close
        trs.append(max(h - low, abs(h - pc), abs(low - pc)))
    return statistics.fmean(trs)


def realized_direction(
    bars: Sequence[OHLCV], i: int, atr: float, *, horizon: int, thr_atr: float
) -> tuple[str, float, float]:
    """(realisierte Richtung, net_move_atr, Geradlinigkeit 0..1) über die nächsten ``horizon``
    Bars. Geradlinigkeit = |net| / Summe(|bar-zu-bar|) — 1.0 = perfekt gerichtet, ~0 = Chop."""
    if atr <= 0 or i + horizon >= len(bars):
        return "n/a", 0.0, 0.0
    c0 = bars[i].close
    seg = [b.close for b in bars[i : i + horizon + 1]]
    net = seg[-1] - c0
    path = sum(abs(seg[j] - seg[j - 1]) for j in range(1, len(seg))) or 1e-9
    straightness = abs(net) / path
    net_atr = net / atr
    if net_atr >= thr_atr:
        return "trend_up", net_atr, straightness
    if net_atr <= -thr_atr:
        return "trend_down", net_atr, straightness
    return "range", net_atr, straightness


def probe_r(
    bars: Sequence[OHLCV],
    i: int,
    atr: float,
    *,
    direction: int,
    sl_atr: float,
    rr: float,
    hold: int,
) -> float:
    if atr <= 0 or i + 1 >= len(bars):
        return 0.0
    entry = bars[i].close
    r_unit = sl_atr * atr
    sl = entry - direction * r_unit
    tp = entry + direction * rr * r_unit
    end = min(len(bars), i + 1 + hold)
    for j in range(i + 1, end):
        b = bars[j]
        hit_sl = b.low <= sl if direction > 0 else b.high >= sl
        hit_tp = b.high >= tp if direction > 0 else b.low <= tp
        if hit_sl:
            return -1.0
        if hit_tp:
            return rr
    return direction * (bars[end - 1].close - entry) / r_unit


# --------------------------------------------------------------------------------------------
# Sammeln
# --------------------------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class Sample:
    ts: datetime
    symbol: str
    realized: str  # trend_up | trend_down | range | n/a
    net_atr: float
    straightness: float
    # je Variante: (classified_dir, r_in_call_direction, flipped_vs_prev)
    per_variant: dict[str, tuple[str, float, bool]]
    # MTF: H4-Klassifikation (nur V0) für Disagreement
    h4_v0: str


def collect(
    repo: MarketDataRepository,
    symbol: str,
    start: datetime,
    end: datetime,
    *,
    tf: Timeframe,
    every: int,
    horizon: int,
    thr_atr: float,
    sl_atr: float,
    rr: float,
    hold: int,
) -> list[Sample]:
    bars = repo.read_ohlcv(symbol, tf, start, end, as_of=end)
    h4 = repo.read_ohlcv(symbol, Timeframe.H4, start, end, as_of=end)
    if len(bars) < 60:
        return []
    out: list[Sample] = []
    prev_dir: dict[str, str] = {}
    warm = 4 * 6  # genug Bars für die strengste Variante
    for k in range(warm, len(bars), every):
        cutoff = bars[k].close_time
        visible = bars[: k + 1]
        atr = _atr(bars, k)
        realized, net_atr, straight = realized_direction(
            bars, k, atr, horizon=horizon, thr_atr=thr_atr
        )
        pv: dict[str, tuple[str, float, bool]] = {}
        for v in VARIANTS:
            d = classify(visible, tf, v)
            dir_int = (
                1
                if d is RegimeDirectional.TREND_UP
                else (-1 if d is RegimeDirectional.TREND_DOWN else 0)
            )
            r = (
                probe_r(bars, k, atr, direction=dir_int, sl_atr=sl_atr, rr=rr, hold=hold)
                if dir_int
                else 0.0
            )
            flipped = (
                v.key in prev_dir
                and prev_dir[v.key] != d.value
                and d is not RegimeDirectional.UNCLEAR
            )
            pv[v.key] = (d.value, r, flipped)
            prev_dir[v.key] = d.value
        # H4-Klassifikation (V0) zum selben cutoff
        h4_visible = [b for b in h4 if b.close_time <= cutoff]
        h4d = (
            classify(h4_visible, Timeframe.H4, VARIANTS[0]).value
            if len(h4_visible) >= 30
            else "unclear"
        )
        out.append(Sample(cutoff, symbol, realized, round(net_atr, 3), round(straight, 3), pv, h4d))
    return out


# --------------------------------------------------------------------------------------------
# Auswertung
# --------------------------------------------------------------------------------------------


def stats(rs: Sequence[float]) -> dict[str, object]:
    if not rs:
        return {"n": 0}
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r < 0]
    gp, gl = sum(wins), -sum(losses)
    pf = gp / gl if gl > 0 else (math.inf if gp > 0 else 0.0)
    eq = peak = mdd = 0.0
    for r in rs:
        eq += r
        peak = max(peak, eq)
        mdd = max(mdd, peak - eq)
    return {
        "n": len(rs),
        "expectancy_r": round(statistics.fmean(rs), 4),
        "win_rate": round(len(wins) / len(rs), 4),
        "profit_factor": round(pf, 3) if pf != math.inf else "inf",
        "max_dd_r": round(mdd, 2),
        "total_r": round(sum(rs), 2),
    }


def eval_variant(samples: Sequence[Sample], key: str) -> dict[str, object]:
    """Accuracy-Proxy + Coverage + Stabilität + Expectancy für eine Variante."""
    n = len(samples)
    calls = [(s, *s.per_variant[key]) for s in samples]  # (s, dir, r, flipped)
    directed = [(s, d, r, f) for (s, d, r, f) in calls if d != "unclear"]
    coverage = len(directed) / n if n else 0.0

    # Accuracy nur wo realized bekannt
    judged = [(s, d, r, f) for (s, d, r, f) in directed if s.realized != "n/a"]
    correct = sum(1 for (s, d, _r, _f) in judged if d == s.realized)
    false_trend = sum(1 for (s, d, _r, _f) in judged if s.realized == "range")
    wrong_way = sum(1 for (s, d, _r, _f) in judged if {d, s.realized} == {"trend_up", "trend_down"})
    acc = correct / len(judged) if judged else 0.0

    # false range: als UNCLEAR klassifiziert, aber realer starker Trend
    unclear = [s for s in samples if s.per_variant[key][0] == "unclear" and s.realized != "n/a"]
    missed_trend = sum(1 for s in unclear if s.realized in ("trend_up", "trend_down"))
    false_range = missed_trend / len(unclear) if unclear else 0.0

    flips = sum(1 for (_s, _d, _r, f) in directed if f)
    churn = flips / len(directed) if directed else 0.0

    rs = [r for (_s, _d, r, _f) in directed]
    return {
        "coverage_pct": round(coverage * 100, 2),
        "directional_accuracy": round(acc, 4),  # P(realized == call | call directed)
        "false_trend_rate": round(false_trend / len(judged), 4) if judged else 0.0,
        "wrong_direction_rate": round(wrong_way / len(judged), 4) if judged else 0.0,
        "false_range_rate": round(false_range, 4),  # P(strong trend | called unclear)
        "flip_churn": round(churn, 4),  # Anteil Richtungswechsel zwischen Samples
        "probe": stats(rs),
    }


def mtf_disagreement(samples: Sequence[Sample]) -> dict[str, object]:
    d = {"aligned": 0, "one_unclear": 0, "conflict": 0}
    for s in samples:
        a, b = s.per_variant["V0_baseline"][0], s.h4_v0
        if a == "unclear" and b == "unclear":
            continue
        if a == "unclear" or b == "unclear":
            d["one_unclear"] += 1
        elif a != b:
            d["conflict"] += 1
        else:
            d["aligned"] += 1
    tot = sum(d.values()) or 1
    return {k: round(v / tot, 4) for k, v in d.items()}


def _q(ts: datetime) -> str:
    return f"{ts.year}Q{(ts.month - 1) // 3 + 1}"


def report_block(samples: Sequence[Sample]) -> dict[str, object]:
    return {
        "n": len(samples),
        "realized_mix": _mix(s.realized for s in samples),
        "variants": {v.key: eval_variant(samples, v.key) for v in VARIANTS},
        "mtf_disagreement_v0": mtf_disagreement(samples),
    }


def _mix(vals: object) -> dict[str, float]:
    xs = list(vals)  # type: ignore[arg-type]
    n = len(xs) or 1
    out: dict[str, int] = {}
    for x in xs:
        out[str(x)] = out.get(str(x), 0) + 1
    return {k: round(v / n * 100, 2) for k, v in sorted(out.items(), key=lambda kv: -kv[1])}


def walk_forward(samples: list[Sample], folds: int = 4) -> list[dict[str, object]]:
    if not samples:
        return []
    samples = sorted(samples, key=lambda s: s.ts)
    lo, hi = samples[0].ts, samples[-1].ts
    span = (hi - lo) / (folds + 1)
    out = []
    for f in range(folds):
        te0 = lo + span * (f + 1)
        te1 = lo + span * (f + 2)
        test = [s for s in samples if te0 <= s.ts < te1]
        if len(test) < 30:
            continue
        out.append(
            {
                "fold": f,
                "test_window": [te0.date().isoformat(), te1.date().isoformat()],
                "n": len(test),
                "variants": {
                    v.key: {
                        "coverage_pct": eval_variant(test, v.key)["coverage_pct"],
                        "directional_accuracy": eval_variant(test, v.key)["directional_accuracy"],
                        "probe_expectancy_r": eval_variant(test, v.key)["probe"].get(
                            "expectancy_r"
                        ),  # type: ignore[union-attr]
                    }
                    for v in VARIANTS
                },
            }
        )
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", default="data/repository_real")
    ap.add_argument(
        "--symbols",
        nargs="+",
        default=["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT"],
    )
    ap.add_argument("--start", default="2023-01-01")
    ap.add_argument("--split", default="2024-05-01", help="IS < split <= OOS")
    ap.add_argument("--end", default="2025-06-30")
    ap.add_argument("--tf", default="D1", choices=["D1", "H4"])
    ap.add_argument("--every", type=int, default=1, help="jede N-te TF-Bar")
    ap.add_argument("--horizon", type=int, default=15, help="Truth-Proxy-Fenster in TF-Bars")
    ap.add_argument("--thr-atr", type=float, default=1.5, help="net-move-Schwelle (ATR) für Trend")
    ap.add_argument("--sl-atr", type=float, default=1.5)
    ap.add_argument("--rr", type=float, default=2.0)
    ap.add_argument("--hold", type=int, default=20, help="R-Probe max-hold in TF-Bars")
    ap.add_argument("--out", default="data/repository_real/structure_calibration.json")
    args = ap.parse_args()

    tf = Timeframe[args.tf]
    start, split, end = (parse_timestamp(x) for x in (args.start, args.split, args.end))
    repo = MarketDataRepository(args.repo)

    all_s: list[Sample] = []
    per_symbol: dict[str, int] = {}
    for sym in args.symbols:
        ss = collect(
            repo,
            sym,
            start,
            end,
            tf=tf,
            every=args.every,
            horizon=args.horizon,
            thr_atr=args.thr_atr,
            sl_atr=args.sl_atr,
            rr=args.rr,
            hold=args.hold,
        )
        per_symbol[sym] = len(ss)
        all_s.extend(ss)

    is_s = [s for s in all_s if s.ts < split]
    oos_s = [s for s in all_s if s.ts >= split]
    quarters: dict[str, list[Sample]] = {}
    for s in all_s:
        quarters.setdefault(_q(s.ts), []).append(s)

    report = {
        "params": vars(args),
        "n_samples": len(all_s),
        "per_symbol": per_symbol,
        "is_window": [args.start, args.split],
        "oos_window": [args.split, args.end],
        "IS": report_block(is_s),
        "OOS": report_block(oos_s),
        "by_quarter": {
            q: {
                "n": len(v),
                "realized_mix": _mix(s.realized for s in v),
                "V0": eval_variant(v, "V0_baseline"),
            }
            for q, v in sorted(quarters.items())
        },
        "by_symbol": {
            sym: report_block([s for s in all_s if s.symbol == sym]) for sym in args.symbols
        },
        "walk_forward": walk_forward(all_s),
    }
    with open(args.out, "w") as fh:
        json.dump(report, fh, indent=2, default=str)
    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
