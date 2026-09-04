#!/usr/bin/env python3
"""Time-Series-Momentum (TSMOM) auf dem lokalen Repository — ehrlicher Erstcheck.

Hypothese (vorab festgelegt, NICHT aus den Daten gesucht):
    Moskowitz/Ooi/Pedersen (JFE 2012) finden ueber 58 Futures und 25 Jahre einen
    robusten Time-Series-Momentum-Effekt: Vorzeichen der Rendite ueber ein
    Rueckblickfenster prognostiziert die Rendite der Folgeperiode; Positionen werden
    auf konstante Volatilitaet skaliert. Han/Kang/Ryu (2024) finden denselben Effekt
    im Kryptomarkt (bestes Fenster 28 Tage).

Testaufbau — bewusst simpel, damit es nichts zu ueberfitten gibt:
    * D1-Bars aus dem Repository
    * Signal am Schluss von Tag t: Rendite ueber die letzten L Tage > 0  ->  long,
      sonst flat.  Position gilt ab Tag t+1 (kein Lookahead).
    * Long-only. Der Kryptomarkt hat keinen belastbaren Short-Edge (Han et al.).
    * Volatilitaets-Skalierung: Gewicht = ziel_vol / realisierte_vol(60d), gekappt.
    * Kosten: pro Positionswechsel, in Prozent des Nominals.

Bewusst NICHT enthalten: Parameter-Suche, Symbol-Auswahl, Regime-Filter. Wer die
Fenster durchprobiert, landet wieder beim Multiple-Testing-Problem aus
docs/INDEPENDENT-METHOD-AUDIT-2026-09-03.md. Die Fensterliste hier dient dem
Nachweis von STABILITAET ueber Fenster, nicht der Auswahl des besten.

    python3 scripts/tsmom_research.py
"""

from __future__ import annotations

import argparse
import glob
import math
import statistics

TRADING_DAYS = 365  # Krypto handelt durchgehend


def load_d1(repo: str, symbol: str) -> tuple[list, list[float]]:
    import pandas as pd

    files = sorted(
        glob.glob(f"{repo}/ohlcv/instrument={symbol}/timeframe=D1/**/*.parquet", recursive=True)
    )
    if not files:
        return [], []
    df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    df = df.sort_values("open_time").drop_duplicates("open_time")
    return list(pd.to_datetime(df["open_time"], utc=True)), [float(x) for x in df["close"]]


def backtest(
    closes: list[float],
    *,
    lookback: int,
    vol_window: int = 60,
    target_vol: float = 0.40,
    max_weight: float = 1.0,
    cost_pct: float = 0.20,
) -> dict[str, float]:
    """Long-only TSMOM mit Volatilitaets-Skalierung. Gibt Kennzahlen zurueck."""
    n = len(closes)
    warm = max(lookback, vol_window) + 1
    if n < warm + 60:
        return {}

    rets = [0.0] + [closes[i] / closes[i - 1] - 1.0 for i in range(1, n)]
    equity = [1.0]
    prev_w = 0.0
    turnover = 0.0
    daily: list[float] = []
    days_long = 0

    for i in range(warm, n):
        # --- Signal aus Information bis EINSCHLIESSLICH i-1 ---
        past = closes[i - 1] / closes[i - 1 - lookback] - 1.0
        window = rets[i - vol_window : i]
        vol = statistics.pstdev(window) * math.sqrt(TRADING_DAYS) if len(window) > 1 else 0.0
        w = 0.0
        if past > 0 and vol > 0:
            w = min(max_weight, target_vol / vol)
        if w > 0:
            days_long += 1

        # --- Rendite von Tag i mit der Position, die vorher feststand ---
        r = w * rets[i] - abs(w - prev_w) * (cost_pct / 100.0)
        turnover += abs(w - prev_w)
        daily.append(r)
        equity.append(equity[-1] * (1.0 + r))
        prev_w = w

    if not daily:
        return {}
    mean_d = statistics.fmean(daily)
    sd_d = statistics.pstdev(daily)
    cagr = equity[-1] ** (TRADING_DAYS / len(daily)) - 1.0
    peak = equity[0]
    mdd = 0.0
    for e in equity:
        peak = max(peak, e)
        mdd = max(mdd, 1.0 - e / peak)
    return {
        "days": len(daily),
        "total_return": equity[-1] - 1.0,
        "cagr": cagr,
        "vol": sd_d * math.sqrt(TRADING_DAYS),
        "sharpe": (mean_d / sd_d * math.sqrt(TRADING_DAYS)) if sd_d > 0 else 0.0,
        "max_dd": mdd,
        "time_in_market": days_long / len(daily),
        "turnover_pa": turnover / len(daily) * TRADING_DAYS,
    }


def buy_hold(closes: list[float], skip: int) -> dict[str, float]:
    c = closes[skip:]
    if len(c) < 60:
        return {}
    rets = [c[i] / c[i - 1] - 1.0 for i in range(1, len(c))]
    sd = statistics.pstdev(rets)
    eq, peak, mdd = 1.0, 1.0, 0.0
    for r in rets:
        eq *= 1 + r
        peak = max(peak, eq)
        mdd = max(mdd, 1 - eq / peak)
    return {
        "total_return": eq - 1.0,
        "cagr": eq ** (TRADING_DAYS / len(rets)) - 1.0,
        "vol": sd * math.sqrt(TRADING_DAYS),
        "sharpe": (statistics.fmean(rets) / sd * math.sqrt(TRADING_DAYS)) if sd > 0 else 0.0,
        "max_dd": mdd,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", default="data/repository_real")
    ap.add_argument(
        "--symbols",
        nargs="+",
        default=["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT", "LINKUSDT"],
    )
    ap.add_argument("--lookbacks", nargs="+", type=int, default=[28, 56, 90, 120, 180])
    ap.add_argument("--cost-pct", type=float, default=0.20)
    ap.add_argument("--split", default=None, help="ISO-Datum: alles davor IS, danach OOS")
    args = ap.parse_args()

    data = {s: load_d1(args.repo, s) for s in args.symbols}
    data = {s: v for s, v in data.items() if v[1]}
    if not data:
        print("keine D1-Daten gefunden")
        return 1

    print(f"TSMOM · long-only · Vol-Ziel 40% · Kosten {args.cost_pct}% je Positionswechsel")
    print(f"Symbole: {', '.join(data)}")
    print()
    print(
        f"{'Lookback':>9}{'Sharpe':>9}{'CAGR':>9}{'Vol':>8}{'MaxDD':>8}{'im Markt':>10}{'Umschlag':>10}"
    )
    print("-" * 63)

    per_lb: dict[int, list[dict[str, float]]] = {}
    for lb in args.lookbacks:
        stats = []
        for _s, (_t, closes) in data.items():
            r = backtest(closes, lookback=lb, cost_pct=args.cost_pct)
            if r:
                stats.append(r)
        if not stats:
            continue
        per_lb[lb] = stats

        def m(k: str, _stats: list[dict[str, float]] = stats) -> float:
            return statistics.fmean(x[k] for x in _stats)

        print(
            f"{lb:>9}{m('sharpe'):>9.2f}{m('cagr') * 100:>8.1f}%{m('vol') * 100:>7.1f}%"
            f"{m('max_dd') * 100:>7.1f}%{m('time_in_market') * 100:>9.1f}%{m('turnover_pa'):>9.1f}x"
        )

    print()
    print("Vergleich Buy & Hold (gleiche Symbole, gleicher Zeitraum):")
    print(f"{'':>9}{'Sharpe':>9}{'CAGR':>9}{'Vol':>8}{'MaxDD':>8}")
    print("-" * 43)
    bh = [r for _s, (_t, c) in data.items() if (r := buy_hold(c, max(args.lookbacks) + 61))]
    if bh:

        def m(k: str, _stats: list[dict[str, float]] = bh) -> float:
            return statistics.fmean(x[k] for x in _stats)

        print(
            f"{'B&H':>9}{m('sharpe'):>9.2f}{m('cagr') * 100:>8.1f}%"
            f"{m('vol') * 100:>7.1f}%{m('max_dd') * 100:>7.1f}%"
        )

    print()
    print("Je Symbol beim mittleren Lookback:")
    mid = args.lookbacks[len(args.lookbacks) // 2]
    print(f"{'Symbol':<11}{'TSMOM Sh':>10}{'B&H Sh':>9}{'TSMOM DD':>10}{'B&H DD':>9}")
    print("-" * 49)
    for s, (_t, closes) in data.items():
        r = backtest(closes, lookback=mid, cost_pct=args.cost_pct)
        b = buy_hold(closes, max(args.lookbacks) + 61)
        if r and b:
            print(
                f"{s:<11}{r['sharpe']:>10.2f}{b['sharpe']:>9.2f}"
                f"{r['max_dd'] * 100:>9.1f}%{b['max_dd'] * 100:>8.1f}%"
            )

    print()
    print("STABILITAET ueber Fenster ist das Kriterium, nicht der beste Einzelwert.")
    if per_lb:
        sh = [statistics.fmean(s["sharpe"] for s in v) for v in per_lb.values()]
        print(
            f"  Sharpe ueber alle Fenster: min {min(sh):.2f} · median {statistics.median(sh):.2f} · max {max(sh):.2f}"
        )
        print(
            f"  Fenster mit Sharpe > B&H: {sum(1 for x in sh if bh and x > statistics.fmean(s['sharpe'] for s in bh))}/{len(sh)}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
