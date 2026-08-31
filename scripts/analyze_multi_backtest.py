#!/usr/bin/env python
"""Aggregiert die Per-Symbol-Reports aus ``scripts/run_multi_backtest.sh`` zu einer
Entry-/Exit-Qualitäts-Übersicht.

    python scripts/analyze_multi_backtest.py data/repository_real/bt_multi

Liest ``{SYMBOL}.json`` (Output von ``scripts/run_backtest.py --json``), rechnet **nichts neu** —
es fasst die vorhandenen `strategy_report`-Felder zusammen. Keine Parameteränderung, keine
erfundenen Zahlen. Fehlt ein Report, wird das gemeldet.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT"]


def _load(d: Path, sym: str) -> dict | None:
    p = d / f"{sym}.json"
    if not p.exists() or p.stat().st_size == 0:
        return None
    txt = "\n".join(ln for ln in p.read_text().splitlines() if not ln.startswith("# generated"))
    try:
        return json.loads(txt)
    except json.JSONDecodeError as e:
        print(f"  ! {sym}: JSON kaputt ({e})")
        return None


def _fmt(v: object) -> str:
    if isinstance(v, float):
        return f"{v:+.3f}"
    return str(v)


def main() -> int:
    d = Path(sys.argv[1] if len(sys.argv) > 1 else "data/repository_real/bt_multi")
    reports = {s: _load(d, s) for s in SYMBOLS}
    have = {s: r for s, r in reports.items() if r is not None}
    if not have:
        print(f"Keine fertigen Reports in {d}. Läuft der Backtest noch?")
        return 1

    print(f"\n=== Multi-Symbol-Backtest — {len(have)}/{len(SYMBOLS)} Reports ===")
    print(f"Quelle: {d}  ·  Modus: Research (News-Gate aus, sofern so gelaufen)\n")

    # 1) Basiskennzahlen je Symbol
    print(
        f"{'Symbol':9} {'Trades':>6} {'Win%':>6} {'PF':>6} {'Exp.R':>7} {'MaxDD.R':>8} "
        f"{'MFE.R':>6} {'MAE.R':>6} {'Total.R':>8}"
    )
    agg_trades = 0
    for s in SYMBOLS:
        r = reports.get(s)
        if r is None:
            print(f"{s:9} {'—':>6}  (kein Report)")
            continue
        m = r["base_metrics"]
        agg_trades += m["n_trades"]
        pf = m["profit_factor"]
        print(
            f"{s:9} {m['n_trades']:>6} {m['win_rate'] * 100:>5.1f}% "
            f"{pf if isinstance(pf, str) else f'{pf:>6.2f}'} {m['expectancy_r']:>7.3f} "
            f"{m['max_drawdown_r']:>8.2f} {m['avg_mfe_r']:>6.2f} {m['avg_mae_r']:>6.2f} "
            f"{m['total_r']:>8.2f}"
        )
    print(f"\n  Trades gesamt: {agg_trades}")

    if agg_trades == 0:
        print("\n  *** 0 Trades über alle Symbole. ***")
        print("  Decision-/No-Trade-Verteilung (aggregiert):")
        dec: dict[str, int] = {}
        ntr: dict[str, int] = {}
        veto: dict[str, int] = {}
        for r in have.values():
            for k, v in r["telemetry"]["decisions"].items():
                dec[k] = dec.get(k, 0) + v
            for k, v in r["telemetry"]["no_trade_reasons_top"].items():
                ntr[k] = ntr.get(k, 0) + v
            for k, v in r["telemetry"].get("veto_frequency", {}).items():
                veto[k] = veto.get(k, 0) + v
        total_dec = sum(dec.values()) or 1
        for k, v in sorted(dec.items(), key=lambda x: -x[1]):
            print(f"    decision {k:12} {v:>9}  ({v / total_dec * 100:.1f}%)")
        print("  No-Trade-Gründe:")
        for k, v in sorted(ntr.items(), key=lambda x: -x[1])[:12]:
            print(f"    {k:28} {v:>9}")
        if veto:
            print("  Vetos:")
            for k, v in sorted(veto.items(), key=lambda x: -x[1]):
                print(f"    {k:6} {v:>9}")
        sig = sum(r["telemetry"]["signals_created"] for r in have.values())
        inv = sum(r["telemetry"]["signals_invalidated"] for r in have.values())
        print(
            f"\n  Signale erzeugt: {sig}  ·  invalidiert: {inv}  "
            f"(WATCH-Level, nie bis ARMED+Fill durchgekommen)"
        )
        print(
            "\n  ⇒ Der Regime-Gate blockt weiter ~100 %. Konsistent mit der Regime-OOS-"
            "Kalibrierung\n     (docs/REGIME-CALIBRATION-2026-08.md): Baseline bleibt, der "
            "Hebel ist NICHT das\n     Gate lockern. Nächster echter Hebel: XAUUSD/FX ins "
            "Universe (anderes Vol-Regime)."
        )
        return 0

    # 2) Exit-Struktur (aggregiert, gewichtet nach Trades)
    print("\n=== Exit-Struktur (Trade-gewichteter Schnitt) ===")
    keys = [
        "tp1_hit_rate",
        "tp2_hit_rate",
        "tp3_hit_rate",
        "stop_rate",
        "breakeven_rate",
        "trail_rate",
        "invalidated_exit_rate",
        "expiry_rate",
        "exit_efficiency",
        "avg_give_back_r",
        "avg_hold_bars",
    ]
    for k in keys:
        num = sum(
            reports[s]["exit_structure"][k] * reports[s]["base_metrics"]["n_trades"]
            for s in have
            if reports[s]["base_metrics"]["n_trades"]
        )
        w = sum(reports[s]["base_metrics"]["n_trades"] for s in have)
        print(f"  {k:22} {num / w:+.3f}" if w else f"  {k:22} —")

    # 3) Score / Confidence / Confluence / Regime / Structure → Outcome
    for band_key, label in [
        ("score_vs_outcome", "Score → Outcome"),
        ("confidence_vs_outcome", "Confidence → Outcome"),
        ("confluence_vs_outcome", "Confluence → Outcome"),
    ]:
        print(f"\n=== {label} (aggregiert) ===")
        bands: dict[str, list[float]] = {}
        for s in have:
            for b in reports[s]["signal_analysis"][band_key]:
                bands.setdefault(b["band"], []).append((b["n"], b["avg_realized_r"], b["win_rate"]))
        for band, rows in bands.items():
            n = sum(x[0] for x in rows)
            if n == 0:
                continue
            avg_r = sum(x[0] * x[1] for x in rows) / n
            wr = sum(x[0] * x[2] for x in rows) / n
            print(f"  {band:20} n={n:>4}  avg_R={avg_r:+.3f}  win%={wr * 100:.1f}")

    for seg_key, label in [
        ("by_score_tier", "Score-Tier"),
        ("by_confidence_tier", "Confidence-Tier"),
        ("by_direction", "Richtung"),
        ("by_exit_reason", "Exit-Grund"),
        ("by_instrument", "Instrument"),
    ]:
        print(f"\n=== nach {label} ===")
        rows: dict[str, list] = {}
        for s in have:
            for seg in reports[s]["segments"][seg_key]:
                rows.setdefault(seg["label"], []).append(seg)
        for lab, segs in rows.items():
            n = sum(x["n"] for x in segs)
            if n == 0:
                continue
            exp = sum(x["n"] * x["expectancy_r"] for x in segs) / n
            wr = sum(x["n"] * x["win_rate"] for x in segs) / n
            tot = sum(x["total_r"] for x in segs)
            print(f"  {lab:16} n={n:>4}  exp_R={exp:+.3f}  win%={wr * 100:.1f}  total_R={tot:+.2f}")

    # 4) Score/Confidence-Outcome-Korrelation
    print("\n=== Score/Confidence-Outcome-Korrelation (Pearson, je Symbol) ===")
    for s in have:
        sa = reports[s]["signal_analysis"]
        print(
            f"  {s:9} score_r={_fmt(sa['score_outcome_correlation'])}  "
            f"confidence_r={_fmt(sa['confidence_outcome_correlation'])}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
