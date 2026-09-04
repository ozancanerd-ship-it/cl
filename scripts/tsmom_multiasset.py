#!/usr/bin/env python3
"""TSMOM auf einem Universum ueber vier Assetklassen.

Vorab registriert in ``docs/PRAEREGISTRIERUNG-TSMOM-MULTIASSET.md`` — Universum, Regel,
Split und Kriterien standen fest, bevor dieser Code lief. Die Regel selbst kommt
unveraendert aus ``trading_agent.strategy.setups.tsmom``.

    python3 scripts/tsmom_multiasset.py
"""

from __future__ import annotations

import argparse
import glob
import json
import math
import statistics
import sys
from datetime import UTC, datetime

from trading_agent.research.hypotheses import HypothesisRegistry, norm_cdf
from trading_agent.strategy.setups.tsmom import TsmomParams, evaluate_tsmom

TD = 365

UNIVERSE: dict[str, list[str]] = {
    "Krypto": ["BTCUSDT", "ETHUSDT", "BNBUSDT"],
    "Aktien": ["NVDA-YFD", "AAPL-YFD", "MSFT-YFD", "AMD-YFD", "GOOGL-YFD", "META-YFD"],
    "Waehrungen": ["EURUSD-YFD", "GBPUSD-YFD", "USDJPY-YFD"],
    "Rohstoffe": ["XAUUSD-YFD"],
}
CRYPTO_ONLY = UNIVERSE["Krypto"]


def load_d1(repo: str, symbol: str) -> dict[datetime, float]:
    import pyarrow.parquet as pq

    files = sorted(
        glob.glob(f"{repo}/ohlcv/instrument={symbol}/timeframe=D1/**/*.parquet", recursive=True)
    )
    out: dict[datetime, float] = {}
    for f in files:
        t = pq.read_table(f, columns=["open_time", "close"])
        for ts, c in zip(
            t.column("open_time").to_pylist(), t.column("close").to_pylist(), strict=True
        ):
            if ts is None or c is None:
                continue
            out[ts if ts.tzinfo else ts.replace(tzinfo=UTC)] = float(c)
    return out


def stats(rets: list[float]) -> dict[str, float]:
    if len(rets) < 20:
        return {}
    sd = statistics.pstdev(rets)
    eq, peak, mdd = 1.0, 1.0, 0.0
    for r in rets:
        eq *= 1 + r
        peak = max(peak, eq)
        mdd = max(mdd, 1 - eq / peak)
    return {
        "n": float(len(rets)),
        "sharpe": (statistics.fmean(rets) / sd * math.sqrt(TD)) if sd > 0 else 0.0,
        "cagr": eq ** (TD / len(rets)) - 1.0,
        "vol": sd * math.sqrt(TD),
        "max_dd": mdd,
        "total": eq - 1.0,
    }


def portfolio(
    series: dict[str, dict[datetime, float]], params: TsmomParams, cost_pct: float
) -> tuple[list[datetime], list[float], list[float], list[float]]:
    """Gleichgewichtetes Portfolio der vol-skalierten Einzelgewichte.

    Handelt ein Instrument an einem Tag nicht (Wochenende bei Aktien/FX), traegt es an
    diesem Tag 0 % Rendite bei und behaelt sein Gewicht. Kein Vorwaerts-Fuellen von Kursen.
    """
    days = sorted(set().union(*(set(s) for s in series.values())))
    hist: dict[str, list[float]] = {k: [] for k in series}
    weights: dict[str, float] = dict.fromkeys(series, 0.0)
    prev_close: dict[str, float] = {}
    out_t: list[datetime] = []
    out_r: list[float] = []
    out_w: list[float] = []
    out_bh: list[float] = []
    warm = params.warmup_bars()

    for day in days:
        day_r: list[float] = []
        day_bh: list[float] = []
        for sym, s in series.items():
            px = s.get(day)
            if px is None:
                continue
            pc = prev_close.get(sym)
            r_mkt = (px / pc - 1.0) if pc else 0.0
            w = weights[sym]
            if pc:
                day_bh.append(r_mkt)
                day_r.append(w * r_mkt)
            # Signal fuer den NAECHSTEN Tag aus Kursen bis EINSCHLIESSLICH heute.
            hist[sym].append(px)
            prev_close[sym] = px
            if len(hist[sym]) >= warm:
                new_w = evaluate_tsmom(hist[sym], params=params).target_weight
                if new_w != w:
                    # Kosten des Wechsels, auf Instrumentenebene. Die Division durch die
                    # Zahl der Instrumente passiert EINMAL, unten beim Portfolio-Mittel —
                    # hier nochmal zu teilen wuerde die Kosten um Faktor N kleinrechnen.
                    day_r.append(-abs(new_w - w) * (cost_pct / 100.0))
                weights[sym] = new_w
        if day_bh:
            out_t.append(day)
            out_r.append(sum(day_r) / len(series))
            out_bh.append(statistics.fmean(day_bh))
            out_w.append(statistics.fmean(weights.values()))
    return out_t, out_r, out_w, out_bh


def mean_pairwise_corr(series: dict[str, dict[datetime, float]]) -> float:
    days = sorted(set.intersection(*(set(s) for s in series.values())))
    if len(days) < 60:
        return float("nan")
    rets = {
        k: [series[k][days[i]] / series[k][days[i - 1]] - 1 for i in range(1, len(days))]
        for k in series
    }
    keys = list(rets)
    vals: list[float] = []
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            a, b = rets[keys[i]], rets[keys[j]]
            ma, mb = statistics.fmean(a), statistics.fmean(b)
            cov = sum((x - ma) * (y - mb) for x, y in zip(a, b, strict=True))
            va = sum((x - ma) ** 2 for x in a)
            vb = sum((y - mb) ** 2 for y in b)
            if va > 0 and vb > 0:
                vals.append(cov / math.sqrt(va * vb))
    return statistics.fmean(vals) if vals else float("nan")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", default="data/repository_real")
    ap.add_argument("--split", default="2025-01-01")
    ap.add_argument("--cost-pct", type=float, default=0.20)
    ap.add_argument("--registry", default="config/hypothesis_registry.json")
    ap.add_argument("--out", default="data/repository_real/research/tsmom_multiasset.json")
    args = ap.parse_args()

    split = datetime.fromisoformat(args.split).replace(tzinfo=UTC)
    params = TsmomParams()
    flat = [s for group in UNIVERSE.values() for s in group]

    series = {s: load_d1(args.repo, s) for s in flat}
    missing = [s for s, v in series.items() if len(v) < params.warmup_bars() + 120]
    if missing:
        print(f"FEHLENDE ODER ZU KURZE REIHEN: {missing}")
        return 1
    print(f"Universum: {len(flat)} Instrumente in {len(UNIVERSE)} Klassen")
    for k, v in UNIVERSE.items():
        print(f"  {k:<12}{len(v):>3}  {', '.join(v)}")
    print(f"Regel: eingefroren {params.lookbacks}, Vol-Ziel {params.target_vol:.0%}")
    print(f"Split: {args.split} · Kosten {args.cost_pct} %\n")

    res: dict[str, object] = {"generated": datetime.now(UTC).isoformat(), "split": args.split}

    # ---- deskriptiv: Korrelation ----
    c_multi = mean_pairwise_corr(series)
    c_crypto = mean_pairwise_corr({s: series[s] for s in CRYPTO_ONLY})
    print("Mittlere paarweise Korrelation der Tagesrenditen (deskriptiv, kein Kriterium)")
    print(f"  nur Krypto:   {c_crypto:+.3f}")
    print(f"  Multi-Asset:  {c_multi:+.3f}\n")
    res["correlation"] = {"crypto_only": c_crypto, "multi_asset": c_multi}

    # ---- Portfolio ----
    t, r, w, bh = portfolio(series, params, args.cost_pct)
    is_r = [x for ts, x in zip(t, r, strict=True) if ts < split]
    oos_r = [x for ts, x in zip(t, r, strict=True) if ts >= split]
    is_b = [x for ts, x in zip(t, bh, strict=True) if ts < split]
    oos_b = [x for ts, x in zip(t, bh, strict=True) if ts >= split]

    s_all, s_is, s_oos = stats(r), stats(is_r), stats(oos_r)
    b_all, b_is, b_oos = stats(bh), stats(is_b), stats(oos_b)
    print(f"{'':<16}{'Sharpe':>9}{'CAGR':>9}{'Vol':>8}{'MaxDD':>8}{'Tage':>7}")
    print("-" * 57)
    for lbl, a, b in (("Gesamt", s_all, b_all), ("In-Sample", s_is, b_is), ("OOS", s_oos, b_oos)):
        print(
            f"{lbl + ' TSMOM':<16}{a.get('sharpe', 0):>9.2f}{a.get('cagr', 0) * 100:>8.1f}%"
            f"{a.get('vol', 0) * 100:>7.1f}%{a.get('max_dd', 0) * 100:>7.1f}%{a.get('n', 0):>7.0f}"
        )
        print(
            f"{lbl + ' B&H':<16}{b.get('sharpe', 0):>9.2f}{b.get('cagr', 0) * 100:>8.1f}%"
            f"{b.get('vol', 0) * 100:>7.1f}%{b.get('max_dd', 0) * 100:>7.1f}%{b.get('n', 0):>7.0f}"
        )
    res["portfolio"] = {"all": s_all, "IS": s_is, "OOS": s_oos}
    res["buy_hold"] = {"all": b_all, "IS": b_is, "OOS": b_oos}
    res["avg_weight"] = statistics.fmean(w)

    # ---- Kriterien ----
    reg = HypothesisRegistry.load(args.registry)
    k = reg.n_trials
    alpha = 0.05 / k
    sd = statistics.pstdev(oos_r) if len(oos_r) > 2 else 0.0
    z = (statistics.fmean(oos_r) / sd * math.sqrt(len(oos_r))) if sd > 0 else 0.0
    p = 1.0 - norm_cdf(z)
    primary = p < alpha
    secondary = s_oos.get("sharpe", 0) > b_oos.get("sharpe", 0)

    print("\n" + "=" * 57)
    print("KRITERIEN (vorab festgelegt)")
    print("=" * 57)
    print(f"  Versuche im Register: {k} -> Schwelle p < {alpha:.7f}")
    print("  PRIMAER   OOS-Sharpe > 0, korrigiert")
    print(
        f"            OOS-Sharpe {s_oos.get('sharpe', 0):+.2f}, p = {p:.4f}"
        f"  -> {'ERFUELLT' if primary else 'NICHT ERFUELLT'}"
    )
    print("  SEKUNDAER OOS-Sharpe > Buy & Hold")
    print(
        f"            {s_oos.get('sharpe', 0):+.2f} gegen {b_oos.get('sharpe', 0):+.2f}"
        f"  -> {'ERFUELLT' if secondary else 'NICHT ERFUELLT'}"
    )
    print()
    if primary:
        print("  Hypothese NICHT verworfen. Status bleibt in_validation / SHADOW —")
        print("  ein historischer Befund ist kein Freigabesignal.")
    else:
        print("  HYPOTHESE VERWORFEN. Kein Nachjustieren des Universums, kein anderer")
        print("  Split, keine Gewichtungsvariante. So stand es in der Registrierung.")
    res["criteria"] = {
        "n_trials": k,
        "alpha": alpha,
        "oos_p_value": p,
        "primary_met": primary,
        "secondary_met": secondary,
    }

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=2, ensure_ascii=False)
    print(f"\ngeschrieben: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
