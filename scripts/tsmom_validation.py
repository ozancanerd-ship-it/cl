#!/usr/bin/env python3
"""Pruefkette fuer SETUP-TSMOM-ENSEMBLE-01.

Faehrt die AUSGELIEFERTE Regel (``trading_agent.strategy.setups.tsmom``) — nicht eine
Nachbildung — bar fuer bar ueber die D1-Reihen und wertet sie auf allen Achsen aus, die
der Masterplan verlangt:

    OOS · rollierende Fenster · Symbol-Stabilitaet · Regime-Stabilitaet ·
    Parameter-Robustheit · Bootstrap-Konfidenz · Multiple-Testing-Korrektur

Zwei Besonderheiten gegenueber der SMC-Pruefung:

* **Kein Walk-Forward mit Refitting.** Die Parameter sind vorab festgelegt und eingefroren
  (``config/setup_validation.json`` -> ``preregistered``). Es gibt nichts zu fitten, also
  waere ein Refitting-Walk-Forward eine Attrappe. Was bleibt, ist der ehrliche Test:
  laeuft die eingefrorene Regel ueber Zeit, Symbole und Regime stabil?
* **Die Parameter-Robustheit zaehlt als zusaetzliche Versuche.** Jede Stoerung ist eine
  weitere Konfiguration und wird als solche berichtet — sonst waere die Robustheitspruefung
  selbst eine versteckte Parametersuche (Befund F1).

    python3 scripts/tsmom_validation.py
    python3 scripts/tsmom_validation.py --split 2023-01-01 --out docs/TSMOM-VALIDATION.md
"""

from __future__ import annotations

import argparse
import glob
import json
import math
import statistics
import sys
from dataclasses import replace
from datetime import UTC, datetime

from trading_agent.research.hypotheses import HypothesisRegistry, norm_cdf
from trading_agent.strategy.setups.tsmom import TsmomParams, evaluate_tsmom

TD = 365


# ------------------------------------------------------------------ Daten


def load_d1(repo: str, symbol: str) -> tuple[list[datetime], list[float]]:
    import pyarrow.parquet as pq

    files = sorted(
        glob.glob(f"{repo}/ohlcv/instrument={symbol}/timeframe=D1/**/*.parquet", recursive=True)
    )
    if not files:
        return [], []
    times: list[datetime] = []
    closes: list[float] = []
    for f in files:
        t = pq.read_table(f, columns=["open_time", "close"])
        cols = zip(t.column("open_time").to_pylist(), t.column("close").to_pylist(), strict=True)
        for ts, c in cols:
            if ts is None or c is None:
                continue
            times.append(ts if ts.tzinfo else ts.replace(tzinfo=UTC))
            closes.append(float(c))
    order = sorted(range(len(times)), key=lambda i: times[i])
    seen: set[datetime] = set()
    ot: list[datetime] = []
    oc: list[float] = []
    for i in order:
        if times[i] in seen:
            continue
        seen.add(times[i])
        ot.append(times[i])
        oc.append(closes[i])
    return ot, oc


# ------------------------------------------------------------------ Simulation


def simulate(
    times: list[datetime], closes: list[float], params: TsmomParams, cost_pct: float
) -> tuple[list[datetime], list[float], list[float]]:
    """Gibt (Datum, Tagesrendite, Gewicht) zurueck. Gewicht gilt AB der Folgebar."""
    warm = params.warmup_bars()
    if len(closes) < warm + 30:
        return [], [], []
    out_t: list[datetime] = []
    out_r: list[float] = []
    out_w: list[float] = []
    prev_w = 0.0
    for i in range(warm, len(closes)):
        # Bewertung nur mit Bars bis i-1 -> Position gilt fuer Tag i. Kein Lookahead.
        rep = evaluate_tsmom(closes[:i], params=params)
        w = rep.target_weight
        r_mkt = closes[i] / closes[i - 1] - 1.0
        out_t.append(times[i])
        out_r.append(w * r_mkt - abs(w - prev_w) * (cost_pct / 100.0))
        out_w.append(w)
        prev_w = w
    return out_t, out_r, out_w


def stats(rets: list[float]) -> dict[str, float]:
    if len(rets) < 5:
        return {}
    sd = statistics.pstdev(rets)
    eq, peak, mdd = 1.0, 1.0, 0.0
    for r in rets:
        eq *= 1 + r
        peak = max(peak, eq)
        mdd = max(mdd, 1 - eq / peak)
    return {
        "n": len(rets),
        "sharpe": (statistics.fmean(rets) / sd * math.sqrt(TD)) if sd > 0 else 0.0,
        "cagr": eq ** (TD / len(rets)) - 1.0,
        "vol": sd * math.sqrt(TD),
        "max_dd": mdd,
        "total": eq - 1.0,
    }


def bh_stats(closes: list[float], skip: int) -> dict[str, float]:
    c = closes[skip:]
    if len(c) < 30:
        return {}
    return stats([c[i] / c[i - 1] - 1.0 for i in range(1, len(c))])


def bootstrap_sharpe_ci(rets: list[float], runs: int = 2000, seed: int = 11) -> tuple[float, float]:
    import random

    rng = random.Random(seed)
    n = len(rets)
    vals = []
    for _ in range(runs):
        s = [rets[rng.randrange(n)] for _ in range(n)]
        sd = statistics.pstdev(s)
        vals.append(statistics.fmean(s) / sd * math.sqrt(TD) if sd > 0 else 0.0)
    vals.sort()
    return vals[int(0.025 * runs)], vals[int(0.975 * runs)]


# ------------------------------------------------------------------ Hauptlauf


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", default="data/repository_real")
    ap.add_argument(
        "--symbols",
        nargs="+",
        default=["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT", "LINKUSDT"],
    )
    ap.add_argument("--split", default="2025-01-01", help="alles davor IS, danach OOS")
    ap.add_argument("--cost-pct", type=float, default=0.20)
    ap.add_argument("--registry", default="config/hypothesis_registry.json")
    ap.add_argument("--json-out", default="data/repository_real/research/tsmom_validation.json")
    args = ap.parse_args()

    split = datetime.fromisoformat(args.split).replace(tzinfo=UTC)
    base = TsmomParams()
    report: dict[str, object] = {
        "generated": datetime.now(UTC).isoformat(),
        "params": {
            "lookbacks": list(base.lookbacks),
            "vol_window": base.vol_window,
            "target_vol": base.target_vol,
            "min_agreement": base.min_agreement,
        },
        "cost_pct": args.cost_pct,
        "split": args.split,
    }

    data = {s: load_d1(args.repo, s) for s in args.symbols}
    data = {s: v for s, v in data.items() if len(v[1]) > base.warmup_bars() + 60}
    if not data:
        print("keine D1-Daten gefunden")
        return 1
    print(f"Symbole: {', '.join(data)}")
    print(f"Regel: eingefroren, {base.lookbacks}, Vol-Ziel {base.target_vol:.0%}")
    print(f"Kosten: {args.cost_pct} % je Positionswechsel\n")

    # --- Basislauf je Symbol -----------------------------------------------------
    per_symbol: dict[str, dict[str, object]] = {}
    pooled_is: list[float] = []
    pooled_oos: list[float] = []
    for sym, (times, closes) in data.items():
        t, r, w = simulate(times, closes, base, args.cost_pct)
        if not r:
            continue
        is_r = [x for ts, x in zip(t, r, strict=True) if ts < split]
        oos_r = [x for ts, x in zip(t, r, strict=True) if ts >= split]
        pooled_is += is_r
        pooled_oos += oos_r
        per_symbol[sym] = {
            "all": stats(r),
            "IS": stats(is_r),
            "OOS": stats(oos_r),
            "buy_hold": bh_stats(closes, base.warmup_bars()),
            "avg_weight": round(statistics.fmean(w), 4),
            "time_in_market": round(sum(1 for x in w if x > 0) / len(w), 4),
        }

    print(f"{'Symbol':<10}{'TSMOM Sh':>10}{'B&H Sh':>9}{'TSMOM DD':>10}{'B&H DD':>9}{'OOS Sh':>9}")
    print("-" * 57)
    for sym, blk in per_symbol.items():
        a, b, o = blk["all"], blk["buy_hold"], blk["OOS"]  # type: ignore[index]
        print(
            f"{sym:<10}{a.get('sharpe', 0):>10.2f}{b.get('sharpe', 0):>9.2f}"  # type: ignore[union-attr]
            f"{a.get('max_dd', 0) * 100:>9.1f}%{b.get('max_dd', 0) * 100:>8.1f}%"  # type: ignore[union-attr]
            f"{o.get('sharpe', 0):>9.2f}"  # type: ignore[union-attr]
        )

    # --- Symbol-Stabilitaet ------------------------------------------------------
    beats = sum(
        1
        for b in per_symbol.values()
        if b["all"].get("sharpe", 0) > b["buy_hold"].get("sharpe", 0)  # type: ignore[union-attr]
    )
    dd_better = sum(
        1
        for b in per_symbol.values()
        if b["all"].get("max_dd", 1) < b["buy_hold"].get("max_dd", 0)  # type: ignore[union-attr]
    )
    oos_pos = sum(1 for b in per_symbol.values() if b["OOS"].get("sharpe", 0) > 0)  # type: ignore[union-attr]
    print(f"\nSymbol-Stabilitaet ({len(per_symbol)} Symbole)")
    print(f"  Sharpe > Buy & Hold:      {beats}/{len(per_symbol)}")
    print(f"  Drawdown < Buy & Hold:    {dd_better}/{len(per_symbol)}")
    print(f"  OOS-Sharpe positiv:       {oos_pos}/{len(per_symbol)}")
    report["symbol_stability"] = {
        "n_symbols": len(per_symbol),
        "sharpe_beats_bh": beats,
        "drawdown_better": dd_better,
        "oos_sharpe_positive": oos_pos,
    }
    report["per_symbol"] = per_symbol

    # --- IS/OOS gepoolt ----------------------------------------------------------
    s_is, s_oos = stats(pooled_is), stats(pooled_oos)
    lo, hi = bootstrap_sharpe_ci(pooled_oos) if len(pooled_oos) > 50 else (0.0, 0.0)
    print("\nGepoolt")
    print(f"  IS  Sharpe {s_is.get('sharpe', 0):.2f}  ({s_is.get('n', 0)} Tage)")
    print(f"  OOS Sharpe {s_oos.get('sharpe', 0):.2f}  ({s_oos.get('n', 0)} Tage)")
    print(f"  OOS 95%-Bootstrap-Intervall fuer den Sharpe: [{lo:.2f}, {hi:.2f}]")
    print(f"  {'schliesst 0 EIN' if lo <= 0 <= hi else 'schliesst 0 AUS'}")
    report["pooled"] = {"IS": s_is, "OOS": s_oos, "oos_sharpe_ci95": [lo, hi]}

    # --- Regime-Stabilitaet (Jahre) ---------------------------------------------
    by_year: dict[int, list[float]] = {}
    for _sym, (times, closes) in data.items():
        t, r, _ = simulate(times, closes, base, args.cost_pct)
        for ts, x in zip(t, r, strict=True):
            by_year.setdefault(ts.year, []).append(x)
    print("\nRegime-Stabilitaet (je Kalenderjahr, alle Symbole gepoolt)")
    print(f"{'Jahr':<7}{'Tage':>7}{'Sharpe':>9}{'Rendite':>10}{'MaxDD':>9}")
    print("-" * 42)
    years: dict[str, dict[str, float]] = {}
    for y in sorted(by_year):
        st = stats(by_year[y])
        if not st:
            continue
        years[str(y)] = st
        print(
            f"{y:<7}{st['n']:>7.0f}{st['sharpe']:>9.2f}"
            f"{st['total'] * 100:>9.1f}%{st['max_dd'] * 100:>8.1f}%"
        )
    pos_years = sum(1 for v in years.values() if v["sharpe"] > 0)
    print(f"  Jahre mit positivem Sharpe: {pos_years}/{len(years)}")
    report["regime_stability"] = {"by_year": years, "positive_years": pos_years}

    # --- Parameter-Robustheit ----------------------------------------------------
    print("\nParameter-Robustheit (jede Zeile ist ein ZUSAETZLICHER Versuch)")
    print(f"{'Variante':<32}{'Sharpe':>9}{'MaxDD':>9}")
    print("-" * 50)
    # (Anzeigename, Register-Slug, Parameter). Der Slug verbindet die Variante mit ihrem
    # Eintrag in config/hypothesis_registry.json — sonst wuerde sie doppelt gezaehlt.
    variants = [
        ("Basis (eingefroren)", "basis_eingefroren", base),
        ("Fenster -20 %", "fenster_minus20", replace(base, lookbacks=(22, 45, 72, 96, 144))),
        ("Fenster +20 %", "fenster_plus20", replace(base, lookbacks=(34, 67, 108, 144, 216))),
        ("nur 3 Fenster", "nur_3_fenster", replace(base, lookbacks=(28, 90, 180))),
        ("Vol-Fenster 30", "vol_fenster_30", replace(base, vol_window=30)),
        ("Vol-Fenster 90", "vol_fenster_90", replace(base, vol_window=90)),
        ("Vol-Ziel 30 %", "vol_ziel_30", replace(base, target_vol=0.30)),
        ("Vol-Ziel 50 %", "vol_ziel_50", replace(base, target_vol=0.50)),
        ("Zustimmung >= 60 %", "zustimmung_60", replace(base, min_agreement=0.60)),
    ]
    robustness: dict[str, dict[str, float]] = {}
    for label, _slug, p in variants:
        pooled: list[float] = []
        for _sym, (times, closes) in data.items():
            _t, r, _w = simulate(times, closes, p, args.cost_pct)
            pooled += r
        st = stats(pooled)
        if not st:
            continue
        robustness[label] = st
        print(f"{label:<32}{st['sharpe']:>9.2f}{st['max_dd'] * 100:>8.1f}%")
    sh = [v["sharpe"] for v in robustness.values()]
    print(f"  Spanne {min(sh):.2f} bis {max(sh):.2f} · alle positiv: {all(x > 0 for x in sh)}")
    report["robustness"] = robustness

    # --- Multiple Testing --------------------------------------------------------
    reg = HypothesisRegistry.load(args.registry)
    # Robustheitsvarianten nur zaehlen, solange sie noch nicht im Register stehen —
    # sonst waechst die Zahl bei jedem Lauf, und die Schwelle wandert.
    known = {e.id for e in reg.entries}
    unregistered = sum(1 for _l, slug, _p in variants if f"tsmom_validation:{slug}" not in known)
    k = reg.n_trials + unregistered
    oos_daily_sharpe = (
        s_oos.get("sharpe", 0.0) / math.sqrt(TD) if s_oos.get("sharpe") is not None else 0.0
    )
    dsr = reg.deflated(sharpe=oos_daily_sharpe, n_obs=int(s_oos.get("n", 0)))
    z = oos_daily_sharpe * math.sqrt(max(1, s_oos.get("n", 0)))
    p_val = 1.0 - norm_cdf(z)
    print("\nMultiple-Testing-Korrektur")
    print(
        f"  Versuche im Register: {reg.n_trials}"
        + (f"  + {unregistered} noch nicht registrierte = {k}" if unregistered else "")
    )
    print(f"  Bonferroni-Schwelle:  p < {0.05 / k:.6f}")
    print(
        f"  OOS p-Wert:           {p_val:.4f}   -> {'besteht' if p_val < 0.05 / k else 'BESTEHT NICHT'}"
    )
    print(f"  Deflated Sharpe:      {dsr:.3f}")
    report["multiple_testing"] = {
        "n_trials": k,
        "bonferroni_alpha": 0.05 / k,
        "oos_p_value": p_val,
        "deflated_sharpe": dsr,
        "passes": bool(p_val < 0.05 / k),
    }

    with open(args.json_out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, ensure_ascii=False)
    print(f"\ngeschrieben: {args.json_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
