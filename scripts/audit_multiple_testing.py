#!/usr/bin/env python3
"""Signifikanz eines Research-Laufs gegen die ECHTE Zahl der Versuche pruefen.

Rekonstruiert je Setup die t-Statistik aus den berichteten OOS-Kennzahlen
(``std = expectancy_r / sharpe_r``, weil ``sharpe_r == mean/pstdev`` auf der R-Reihe) und
stellt sie zwei Korrekturen gegenueber:

* **Bonferroni** — ``alpha / K`` mit K = alle je getesteten Konfigurationen aus
  ``config/hypothesis_registry.json``. Konservativ, aber ohne Annahmen.
* **Deflated Sharpe Ratio** (Bailey/Lopez de Prado) — beruecksichtigt zusaetzlich die
  Streuung der Sharpe-Werte ueber die Versuche sowie Schiefe und Woelbung.

    python3 scripts/audit_multiple_testing.py
    python3 scripts/audit_multiple_testing.py <lauf.json> --alpha 0.05

Hintergrund: docs/INDEPENDENT-METHOD-AUDIT-2026-09-03.md, Befund F1.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys

from trading_agent.research.hypotheses import HypothesisRegistry, norm_cdf

_DEFAULT_RUN = "data/repository_real/research/setup_research_v14_realcosts.json"


def rows_from(path: str) -> tuple[dict, list[tuple[str, int, float, float]], int]:
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    results = doc["results"]
    rr_grid = doc.get("params", {}).get("rr_grid", [2.0])
    rows: list[tuple[str, int, float, float]] = []
    for name, block in results.items():
        oos = block.get("OOS", {})
        n = oos.get("n_trades", 0)
        exp = oos.get("expectancy_r", 0.0)
        sharpe = oos.get("sharpe_r", 0.0)
        if n >= 10 and sharpe:
            rows.append((name, n, exp, abs(exp / sharpe)))
    return doc, rows, len(results) * len(rr_grid)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("run", nargs="?", default=_DEFAULT_RUN)
    ap.add_argument("--registry", default="config/hypothesis_registry.json")
    ap.add_argument("--alpha", type=float, default=0.05)
    ap.add_argument(
        "--extra-cost", type=float, default=0.0, help="Zusatzkosten in R fuer die Sensitivitaet"
    )
    args = ap.parse_args()

    _doc, rows, in_run = rows_from(args.run)
    reg = HypothesisRegistry.load(args.registry)
    k_all = max(reg.n_trials, in_run)
    a_run = args.alpha / max(1, in_run)
    a_all = args.alpha / k_all

    print(f"Lauf: {args.run}")
    print(f"Konfigurationen in diesem Lauf: {in_run}   ->  Bonferroni p < {a_run:.6f}")
    print(f"Konfigurationen INSGESAMT je getestet: {k_all}   ->  Bonferroni p < {a_all:.6f}")
    print(f"Register: {reg.summary()}")
    if reg.n_trials <= in_run:
        print(
            "  ! Register kennt nicht mehr Versuche als dieser Lauf — "
            "scripts/build_hypothesis_registry.py laufen lassen."
        )
    print()

    var_sr = reg.var_sharpe()
    print(f"{'Setup':<30}{'OOSn':>6}{'exp':>8}{'t':>7}{'p':>9}{'DSR':>8}  Urteil")
    print("-" * 84)
    survivors = []
    for name, n, exp, std in sorted(rows, key=lambda r: -r[2]):
        t = ((exp - args.extra_cost) / std) * math.sqrt(n)
        p = 1.0 - norm_cdf(t)
        sharpe = (exp - args.extra_cost) / std
        dsr = reg.deflated(sharpe=sharpe, n_obs=n) if var_sr > 0 else float("nan")
        if p < a_all:
            verdict = "BESTEHT alles"
            survivors.append(name)
        elif p < a_run:
            verdict = "nur laufintern"
        elif p < args.alpha:
            verdict = "nur nominal"
        elif exp < 0 and t < -2:
            verdict = "signifikant NEGATIV"
        else:
            verdict = "nicht signifikant"
        dsr_s = f"{dsr:.3f}" if dsr == dsr else "n/a"
        print(f"{name:<30}{n:>6}{exp:>8.3f}{t:>7.2f}{p:>9.4f}{dsr_s:>8}  {verdict}")

    print()
    print(f"Unter H0 (kein Edge) bei {k_all} Konfigurationen erwartete Zufallstreffer")
    print(f"  auf nominal {args.alpha:.0%}: {args.alpha * k_all:.1f}")
    print(f"  nach Bonferroni:  {args.alpha:.2f}")
    print()
    if survivors:
        print("Bestehen die Korrektur: " + ", ".join(survivors))
    else:
        print("KEIN Setup besteht die Multiple-Testing-Korrektur.")

    print()
    print("=== Kosten-Sensitivitaet (Zusatzkosten in R) ===")
    print(f"{'delta_R':>9}{'nominal':>10}{'Bonf(Lauf)':>13}{'Bonf(alle)':>13}")
    print("-" * 45)
    for dc in (0.0, 0.05, 0.10, 0.15, 0.20, 0.30):
        nom = run_b = all_b = 0
        for _, n, exp, std in rows:
            p = 1.0 - norm_cdf(((exp - dc) / std) * math.sqrt(n))
            nom += p < args.alpha
            run_b += p < a_run
            all_b += p < a_all
        print(f"{dc:>9.2f}{nom:>10}{run_b:>13}{all_b:>13}")

    print()
    print("=== Overfitting-Signatur ===")
    pts = [(math.log(n), exp) for _, n, exp, _ in rows]
    if len(pts) > 2:
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        mx, my = statistics.fmean(xs), statistics.fmean(ys)
        cov = sum((x - mx) * (y - my) for x, y in pts)
        vx = sum((x - mx) ** 2 for x in xs)
        vy = sum((y - my) ** 2 for y in ys)
        r = cov / math.sqrt(vx * vy) if vx and vy else 0.0
        print(f"  Korrelation log(OOS-n) vs. Expectancy: r = {r:+.3f} ueber {len(pts)} Setups")
        print("  Stark negativ = jeder Filter verkleinert die Stichprobe UND hebt die")
        print("  Expectancy: die Signatur von Selektion auf Rauschen.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
