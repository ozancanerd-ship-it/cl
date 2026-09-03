#!/usr/bin/env python
"""Finale S4-Validierung — automatisch, sobald echte Spot-Gold-Historie vollständig ist.

Prüft die Daten-Vollständigkeit (`XAUUSD`, ≥ 30 Monate, keine Lücke > 20 Tage), dann:
`xau_shadow` (integrierter Detektor + Trade-Management) auf **echtem** Spot-Gold, IS/OOS-Split,
plus die Yahoo-Referenz. Verdikt:

* **VALIDATED-kandidat** — Real-Gold OOS-Expectancy > +0.10 R, PF > 1.3, ≥ 20 Trades → bleibt
  ``IN_VALIDATION`` (VALIDATED erst nach ≥ 100 Forward-Trades), Baseline aktualisiert.
* **RETIRED** — Real-Gold OOS ≤ 0 über ≥ 20 Trades → schreibt ``status: retired`` in
  ``config/setup_validation.json``. Der Setup-Code bleibt, erzeugt aber keine Signale mehr.
* **INCONCLUSIVE** — zu wenige Trades / gemischt → keine Änderung, Report.

    uv run python scripts/validate_s4.py            # nur Report
    uv run python scripts/validate_s4.py --write    # Verdikt in die Registry schreiben
"""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from trading_agent.core.enums import Timeframe
from trading_agent.data.repository import MarketDataRepository

_MIN_MONTHS = 30
_MAX_GAP_DAYS = 20
_SPLIT = "2025-01-01"


def _coverage(repo: MarketDataRepository, sym: str) -> tuple[int, float, list[str]]:
    # M5 bevorzugt; ist die M5-Parquet unlesbar (bekannter XAUUSD-Thrift-Defekt) oder leer,
    # auf H4 zurückfallen — der xau_shadow-Runner selbst arbeitet ohnehin auf H4/D1.
    bars: list[object] = []
    for tf in (Timeframe.M5, Timeframe.H4):
        try:
            bars = repo.read_ohlcv(
                sym, tf, datetime(2000, 1, 1, tzinfo=UTC), datetime(2100, 1, 1, tzinfo=UTC)
            )
        except Exception:  # korrupte Parquet → nächster TF
            bars = []
        if bars:
            break
    if not bars:
        return 0, 999.0, []
    months = sorted({(b.open_time.year, b.open_time.month) for b in bars})
    # größte Lücke zwischen aufeinanderfolgenden Bars (Handelstage)
    gap = 0.0
    from itertools import pairwise

    for a, b in pairwise(bars):
        dt = (b.open_time - a.open_time).total_seconds() / 86400.0
        if dt > gap:
            gap = dt
    return len(months), gap, [f"{y}-{m:02d}" for y, m in months]


def _shadow(sym: str, start: str, end: str, tmp: Path) -> dict:
    jr = tmp / f"val_{sym}.jsonl"
    r = subprocess.run(
        [
            "uv",
            "run",
            "python",
            "scripts/xau_shadow.py",
            "--symbol",
            sym,
            "--start",
            start,
            "--end",
            end,
            "--journal",
            str(jr),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    rows = []
    if jr.exists():
        rows = [json.loads(x) for x in jr.read_text().splitlines() if x.strip()]
    trades = [
        x
        for x in rows
        if x.get("kind") == "trade"
        and "realized_r" in x
        and x["change"]
        in (
            "TP2",
            "SL",
            "BE_EXIT",
            "MAX_HOLD_EXIT",
        )
    ]
    rs = [float(t["realized_r"]) for t in trades]
    n = len(rs)
    wins = [x for x in rs if x > 0.02]
    losses = [x for x in rs if x < -0.02]
    gp, gl = sum(wins), -sum(losses)
    return {
        "n": n,
        "total_r": round(sum(rs), 2),
        "expectancy_r": round(sum(rs) / n, 4) if n else 0.0,
        "win_rate": round(len(wins) / n, 3) if n else 0.0,
        "profit_factor": round(gp / gl, 3) if gl > 0 else (99.0 if gp > 0 else 0.0),
        "stdout_tail": r.stdout.strip().splitlines()[-4:],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", default="data/repository_real")
    ap.add_argument("--config", default="config/setup_validation.json")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--tmp", default="data/repository_real/research")
    args = ap.parse_args()

    repo = MarketDataRepository(args.repo)
    n_months, gap, months = _coverage(repo, "XAUUSD")
    ready = n_months >= _MIN_MONTHS and gap <= _MAX_GAP_DAYS
    print(
        f"XAUUSD (echt): {n_months} Monate, größte Lücke {gap:.1f} Tage  →  "
        f"{'VOLLSTÄNDIG' if ready else 'NOCH UNVOLLSTÄNDIG'}"
    )
    if not ready:
        print(f"  Monate: {months}")
        print("  Warte auf vollständige Historie (scripts/ingest_dukascopy_full.sh).")
        print(json.dumps({"ready": False, "months": n_months, "max_gap_days": round(gap, 1)}))
        return 0

    tmp = Path(args.tmp)
    tmp.mkdir(parents=True, exist_ok=True)
    real_is = _shadow("XAUUSD", "2023-01-01", _SPLIT, tmp)
    real_oos = _shadow("XAUUSD", _SPLIT, "2027-01-01", tmp)
    yf = _shadow("XAUUSD-YF", "2023-01-01", "2027-01-01", tmp)

    verdict = "INCONCLUSIVE"
    if real_oos["n"] >= 20:
        if real_oos["expectancy_r"] > 0.10 and real_oos["profit_factor"] > 1.3:
            verdict = "VALIDATED_CANDIDATE"
        elif real_oos["expectancy_r"] <= 0.0:
            verdict = "RETIRE"

    report = {
        "as_of": datetime.now(UTC).isoformat(),
        "data": {"months": n_months, "max_gap_days": round(gap, 1)},
        "real_gold_IS": real_is,
        "real_gold_OOS": real_oos,
        "yahoo_ref": yf,
        "verdict": verdict,
    }
    print("\n=== S4 FINALE VALIDIERUNG ===")
    print(
        f"  Real-Gold IS  : n={real_is['n']} exp={real_is['expectancy_r']:+.3f} PF={real_is['profit_factor']}"
    )
    print(
        f"  Real-Gold OOS : n={real_oos['n']} exp={real_oos['expectancy_r']:+.3f} PF={real_oos['profit_factor']} WR={real_oos['win_rate']:.0%}"
    )
    print(f"  Yahoo-Ref     : n={yf['n']} exp={yf['expectancy_r']:+.3f} PF={yf['profit_factor']}")
    print(f"  → VERDIKT: {verdict}")

    if args.write and verdict in ("RETIRE", "VALIDATED_CANDIDATE"):
        cfg = json.loads(Path(args.config).read_text())
        for sv in cfg["setups"]:
            if sv["setup_id"] == "SETUP-BREAKOUT-RETEST-01":
                if verdict == "RETIRE":
                    sv["status"] = "retired"
                    sv["notes"] = (
                        f"RETIRED {datetime.now(UTC).date()} — echtes Spot-Gold OOS "
                        f"exp={real_oos['expectancy_r']:+.3f}R / PF={real_oos['profit_factor']} / "
                        f"n={real_oos['n']} nach vollständiger Dukascopy-Historie. Yahoo-Futures-"
                        f"Edge trug nicht auf Spot. Setup-Code bleibt, erzeugt keine Signale mehr."
                    )
                else:
                    sv["status"] = "in_validation"
                    sv["baseline"] = {
                        "expectancy_r": real_oos["expectancy_r"],
                        "profit_factor": min(real_oos["profit_factor"], 5.0),
                        "win_rate": real_oos["win_rate"],
                        "max_drawdown_r": 9.0,
                        "n_trades": real_oos["n"],
                    }
                    sv["notes"] = (
                        f"Real-Gold-OOS bestätigt {datetime.now(UTC).date()}: exp "
                        f"{real_oos['expectancy_r']:+.3f}R / PF {real_oos['profit_factor']} / "
                        f"n={real_oos['n']}. VALIDATED erst nach ≥100 Forward-Trades."
                    )
        Path(args.config).write_text(json.dumps(cfg, indent=2) + "\n")
        report["written"] = verdict
        print(f"  → config/setup_validation.json aktualisiert ({verdict})")

    (Path(args.tmp) / "s4_final_validation.json").write_text(
        json.dumps(report, indent=2, default=str)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
