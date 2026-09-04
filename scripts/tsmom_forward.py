#!/usr/bin/env python3
"""Taegliche Forward-Aufzeichnung fuer SETUP-TSMOM-ENSEMBLE-01.

Warum das existiert: ``docs/TSMOM-MULTIASSET-ERGEBNIS-2026-09-04.md`` zeigt, dass die
historischen Daten inzwischen mehrfach angesehen wurden. Jeder weitere Test auf ihnen ist
kontaminiert. **Forward-Daten sind die einzigen sauberen Daten, die noch kommen** — und sie
entstehen nur, wenn ab heute jeden Tag aufgezeichnet wird.

Das Skript ist bewusst klein und zustandslos:

1. Tagesschlusskurse fuer das registrierte Universum holen (Binance fuer Krypto, Yahoo sonst)
2. die EINGEFRORENE Regel auswerten — derselbe Code, der auch im Backtest lief
3. eine Zeile je Tag in ``data/repository_real/live/tsmom_forward.jsonl`` anhaengen

Es handelt nichts. Es entscheidet nichts. Es schreibt auf, was die Regel gesagt haette —
und zwar bevor der naechste Tag bekannt ist. Genau das macht die Daten sauber.

Idempotent: ein zweiter Lauf am selben Tag ueberschreibt die Zeile, statt sie zu doppeln.

    python3 scripts/tsmom_forward.py
    python3 scripts/tsmom_forward.py --report     # nur den Stand zeigen, nichts schreiben

Taeglich einplanen (launchd, systemd-Timer oder GitHub Actions), nach US-Boersenschluss.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import UTC, datetime
from pathlib import Path

from trading_agent.core.version import STRATEGY_VERSION
from trading_agent.governance.live_gate import evaluate_live_gate
from trading_agent.governance.validation import ValidationRegistry
from trading_agent.strategy.setups.tsmom import (
    SETUP_PARAMS_VERSION,
    SETUP_TSMOM_ENSEMBLE,
    TsmomParams,
    evaluate_tsmom,
)

# Exakt das Universum aus docs/PRAEREGISTRIERUNG-TSMOM-MULTIASSET.md. Aenderungen hier
# sind eine neue Hypothese und gehoeren ins Register, bevor sie wirksam werden.
UNIVERSE: dict[str, tuple[str, str]] = {
    # kanonisch: (Quelle, Symbol bei der Quelle)
    "BTCUSDT": ("binance", "BTCUSDT"),
    "ETHUSDT": ("binance", "ETHUSDT"),
    "BNBUSDT": ("binance", "BNBUSDT"),
    "NVDA-YFD": ("yahoo", "NVDA"),
    "AAPL-YFD": ("yahoo", "AAPL"),
    "MSFT-YFD": ("yahoo", "MSFT"),
    "AMD-YFD": ("yahoo", "AMD"),
    "GOOGL-YFD": ("yahoo", "GOOGL"),
    "META-YFD": ("yahoo", "META"),
    "EURUSD-YFD": ("yahoo", "EURUSD=X"),
    "GBPUSD-YFD": ("yahoo", "GBPUSD=X"),
    "USDJPY-YFD": ("yahoo", "JPY=X"),
    "XAUUSD-YFD": ("yahoo", "GC=F"),
}

JOURNAL = "data/repository_real/live/tsmom_forward.jsonl"


def _repo_closes(repo: str, symbol: str) -> list[tuple[datetime, float]]:
    import pyarrow.parquet as pq

    out: dict[datetime, float] = {}
    for f in sorted(Path(repo).glob(f"ohlcv/instrument={symbol}/timeframe=D1/**/*.parquet")):
        t = pq.read_table(f, columns=["open_time", "close"])
        for ts, c in zip(
            t.column("open_time").to_pylist(), t.column("close").to_pylist(), strict=True
        ):
            if ts is not None and c is not None:
                out[ts if ts.tzinfo else ts.replace(tzinfo=UTC)] = float(c)
    return sorted(out.items())


def _latest_close(source: str, symbol: str) -> tuple[datetime, float] | None:
    """Letzten abgeschlossenen Tagesschluss holen. ``None``, wenn die Quelle nicht antwortet."""
    import httpx

    try:
        if source == "binance":
            r = httpx.get(
                "https://api.binance.com/api/v3/klines",
                params={"symbol": symbol, "interval": "1d", "limit": 2},
                timeout=20,
            ).json()
            if not isinstance(r, list) or len(r) < 2:
                return None
            row = r[-2]  # -1 ist die noch laufende Tageskerze
            return datetime.fromtimestamp(row[0] / 1000, tz=UTC), float(row[4])
        r = httpx.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
            params={"interval": "1d", "range": "5d"},
            headers={"User-Agent": "Mozilla/5.0 (compatible; trading-agent/0.1; research)"},
            timeout=20,
        ).json()
        res = (r.get("chart", {}).get("result") or [{}])[0]
        stamps = res.get("timestamp") or []
        closes = (res.get("indicators", {}).get("quote") or [{}])[0].get("close") or []
        for i in range(len(stamps) - 1, -1, -1):
            if closes[i] is not None:
                d = datetime.fromtimestamp(int(stamps[i]), tz=UTC)
                return d.replace(hour=0, minute=0, second=0, microsecond=0), float(closes[i])
        return None
    except Exception:
        return None


def _load_journal(path: str) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    rows = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            with_err = None
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                with_err = line[:60]
            if with_err:
                print(f"  ! defekte Journalzeile uebersprungen: {with_err}")
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", default="data/repository_real")
    ap.add_argument("--journal", default=JOURNAL)
    ap.add_argument("--report", action="store_true", help="nur anzeigen, nichts schreiben")
    args = ap.parse_args()

    params = TsmomParams()
    reg = ValidationRegistry.from_file("config/setup_validation.json")
    gate = evaluate_live_gate(SETUP_TSMOM_ENSEMBLE, STRATEGY_VERSION, registry=reg)
    print(f"Setup:  {SETUP_TSMOM_ENSEMBLE} · Parameter {SETUP_PARAMS_VERSION}")
    print(f"Gate:   {gate.eligibility.value.upper()} — {gate.reasons[0]}")
    if gate.is_live:
        print("  ! Gate meldet LIVE. Dieses Skript handelt trotzdem nichts.")
    print()

    rows: list[dict] = []
    for canon, (source, sym) in UNIVERSE.items():
        hist = _repo_closes(args.repo, canon)
        if len(hist) < params.warmup_bars():
            print(f"  {canon:<12} zu wenig Historie ({len(hist)} Bars) — uebersprungen")
            continue
        live = _latest_close(source, sym)
        closes = [c for _, c in hist]
        as_of = hist[-1][0]
        if live and live[0] > hist[-1][0]:
            closes.append(live[1])
            as_of = live[0]
        rep = evaluate_tsmom(closes, params=params)
        rows.append(
            {
                "instrument": canon,
                "as_of": as_of.date().isoformat(),
                "close": closes[-1],
                "target_weight": rep.target_weight,
                "agreement": rep.agreement,
                "state": rep.state.value,
                "realized_vol": rep.realized_vol,
                "vol_scalar": rep.vol_scalar,
                "per_lookback": {str(k): v for k, v in rep.per_lookback.items()},
                "reasons": rep.reasons,
            }
        )

    if not rows:
        print("keine auswertbaren Instrumente")
        return 1

    print(f"{'Instrument':<13}{'Stand':<12}{'Zustand':<12}{'Zustimmung':>11}{'Gewicht':>9}")
    print("-" * 58)
    for r in sorted(rows, key=lambda x: -x["target_weight"]):
        print(
            f"{r['instrument']:<13}{r['as_of']:<12}{r['state']:<12}"
            f"{r['agreement'] * 100:>10.0f}%{r['target_weight']:>9.2f}"
        )
    pw = statistics.fmean(r["target_weight"] for r in rows)
    invested = sum(1 for r in rows if r["target_weight"] > 0)
    print("-" * 58)
    print(f"{'PORTFOLIO':<13}{'':<12}{'':<12}{invested:>7}/{len(rows)} long{pw:>9.2f}")

    entry = {
        "ts": datetime.now(UTC).isoformat(),
        "date": max(r["as_of"] for r in rows),
        "setup_id": SETUP_TSMOM_ENSEMBLE,
        "strategy_version": STRATEGY_VERSION,
        "params_version": SETUP_PARAMS_VERSION,
        "eligibility": gate.eligibility.value,
        "portfolio_weight": round(pw, 6),
        "n_long": invested,
        "n_instruments": len(rows),
        "instruments": rows,
    }

    if args.report:
        print("\n(--report: nichts geschrieben)")
        return 0

    path = Path(args.journal)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = _load_journal(args.journal)
    kept = [e for e in existing if e.get("date") != entry["date"]]
    replaced = len(existing) - len(kept)
    kept.append(entry)
    path.write_text(
        "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in kept), encoding="utf-8"
    )
    print(f"\nJournal: {len(kept)} Tage" + (" (heutige Zeile ersetzt)" if replaced else ""))

    days = len(kept)
    need = 100
    print(f"Forward-Sammlung: {days} von ~{need} Tagen bis zur naechsten Auswertung.")
    if days >= 2:
        first, last = kept[0]["date"], kept[-1]["date"]
        print(f"  Zeitraum {first} bis {last}")
    print("\nErinnerung: Waehrend der Sammelphase wird an der Regel nichts geaendert.")
    print("Jede Aenderung setzt den Zaehler zurueck.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
