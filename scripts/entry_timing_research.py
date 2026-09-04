#!/usr/bin/env python3
"""Bringt Warten auf einen besseren Einstieg etwas? — vorab registrierte Pruefung.

Ozans Frage: "man kann nicht einfach sofort rein, man braucht den richtigen Zeitpunkt".
Das ist eine pruefbare Behauptung, also wird sie geprueft statt beantwortet.

Getestet wird auf denselben Signalen wie SETUP-TSMOM-ENSEMBLE-01: jedes Mal, wenn ein
Instrument von "kein Gewicht" auf "Gewicht > 0" springt, ist das ein Einstiegssignal.
Verglichen wird, was aus derselben Position wird, je nachdem WANN man kauft.

DIE SECHS VARIANTEN — vor dem ersten Lauf festgelegt, danach nicht mehr geaendert:

  sofort      am naechsten Schlusskurs kaufen (Grundlinie)
  limit_2     Kauflimit 2 % unter dem Signalkurs, 10 Tage gueltig, sonst Markt an Tag 10
  limit_5     dasselbe mit 5 %
  tranchen_3  drei gleiche Teile an Tag 1, 6 und 11
  dip_5d      warten bis ein Schluss unter dem 5-Tage-Mittel liegt, spaetestens Tag 10
  warte_5t    einfach 5 Tage warten (Kontrolle: hilft Warten an sich?)

Die Kontrollvariante ist wichtig. Wenn "warte_5t" genauso gut abschneidet wie die
klugen Regeln, misst man keinen Einstiegsvorteil, sondern nur Zufall.

Ausstieg ist bei allen gleich: wenn das Signal dreht. Es wird also NUR der Einstieg
verglichen, nichts anderes.

    python3 scripts/entry_timing_research.py --repo data/repository_real
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from trading_agent.strategy.setups.tsmom import TsmomParams, evaluate_tsmom

sys.path.insert(0, str(Path(__file__).resolve().parent))

VARIANTEN = ("sofort", "limit_2", "limit_5", "tranchen_3", "dip_5d", "warte_5t")
MAX_WARTE = 10  # Tage, nach denen jede wartende Variante zum Marktpreis kauft


@dataclass(frozen=True, slots=True)
class Bar:
    ts: datetime
    close: float
    low: float
    high: float


def _bars(repo: str, symbol: str) -> list[Bar]:
    import pyarrow.parquet as pq

    out: dict[datetime, Bar] = {}
    for f in sorted(Path(repo).glob(f"ohlcv/instrument={symbol}/timeframe=D1/**/*.parquet")):
        t = pq.read_table(f, columns=["open_time", "close", "low", "high"])
        for ts, c, lo, hi in zip(
            t.column("open_time").to_pylist(),
            t.column("close").to_pylist(),
            t.column("low").to_pylist(),
            t.column("high").to_pylist(),
            strict=True,
        ):
            if ts is None or c is None:
                continue
            d = ts if ts.tzinfo else ts.replace(tzinfo=UTC)
            out[d] = Bar(d, float(c), float(lo if lo is not None else c), float(hi or c))
    return [out[k] for k in sorted(out)]


def _entry_price(variante: str, bars: list[Bar], i: int) -> tuple[float, int] | None:
    """Einstiegskurs und Verzoegerung in Tagen. ``None``, wenn nicht mehr genug Bars da sind.

    ``i`` ist der Index des Signaltags; gekauft wird fruehestens an ``i+1`` — der Signaltag
    selbst ist erst nach Handelsschluss bekannt.
    """
    n = len(bars)
    if i + 1 >= n:
        return None
    signal = bars[i].close

    if variante == "sofort":
        return bars[i + 1].close, 1

    if variante == "warte_5t":
        j = min(i + 5, n - 1)
        return bars[j].close, j - i

    if variante == "tranchen_3":
        idx = [k for k in (i + 1, i + 6, i + 11) if k < n]
        if not idx:
            return None
        return statistics.fmean(bars[k].close for k in idx), idx[-1] - i

    if variante.startswith("limit_"):
        pct = float(variante.split("_")[1]) / 100.0
        limit = signal * (1 - pct)
        for k in range(i + 1, min(i + 1 + MAX_WARTE, n)):
            if bars[k].low <= limit:
                # Konservativ: zum Limitkurs, nicht zum Tagestief.
                return limit, k - i
        j = min(i + MAX_WARTE, n - 1)
        return bars[j].close, j - i

    if variante == "dip_5d":
        for k in range(i + 1, min(i + 1 + MAX_WARTE, n)):
            fenster = [b.close for b in bars[max(0, k - 5) : k]]
            if fenster and bars[k].close < statistics.fmean(fenster):
                return bars[k].close, k - i
        j = min(i + MAX_WARTE, n - 1)
        return bars[j].close, j - i

    raise ValueError(variante)


def _trades(bars: list[Bar], params: TsmomParams) -> list[tuple[int, int]]:
    """(Signalindex, Ausstiegsindex) fuer jeden Wechsel von flach auf long."""
    warm = params.warmup_bars()
    if len(bars) <= warm + 2:
        return []
    closes = [b.close for b in bars]
    long_ = [False] * len(bars)
    for i in range(warm, len(bars)):
        long_[i] = evaluate_tsmom(closes[: i + 1], params=params).target_weight > 0

    out: list[tuple[int, int]] = []
    i = warm + 1
    while i < len(bars):
        if long_[i] and not long_[i - 1]:
            j = i + 1
            while j < len(bars) and long_[j]:
                j += 1
            out.append((i, min(j, len(bars) - 1)))
            i = j
        else:
            i += 1
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", default="data/repository_real")
    ap.add_argument("--out", default="docs/EINSTIEGS-TIMING-ERGEBNIS.md")
    args = ap.parse_args()

    from tsmom_forward import UNIVERSE

    params = TsmomParams()
    ergebnisse: dict[str, list[float]] = {v: [] for v in VARIANTEN}
    verzug: dict[str, list[int]] = {v: [] for v in VARIANTEN}
    nicht_gefuellt = dict.fromkeys(VARIANTEN, 0)
    n_trades = 0
    je_symbol: dict[str, dict[str, float]] = {}

    for canon in UNIVERSE:
        bars = _bars(args.repo, canon)
        if len(bars) < params.warmup_bars() + 30:
            print(f"  {canon:<12} zu wenig Historie ({len(bars)}) — uebersprungen")
            continue
        trades = _trades(bars, params)
        print(f"  {canon:<12} {len(bars):>5} Bars, {len(trades):>3} Einstiege")
        n_trades += len(trades)
        sym_ret: dict[str, list[float]] = {v: [] for v in VARIANTEN}

        for i, j in trades:
            exit_px = bars[j].close
            for v in VARIANTEN:
                got = _entry_price(v, bars, i)
                if got is None:
                    continue
                entry, d = got
                if entry <= 0:
                    continue
                ergebnisse[v].append(exit_px / entry - 1.0)
                sym_ret[v].append(exit_px / entry - 1.0)
                verzug[v].append(d)
                if v.startswith("limit_") and d >= MAX_WARTE:
                    nicht_gefuellt[v] += 1
        je_symbol[canon] = {
            v: (statistics.fmean(sym_ret[v]) if sym_ret[v] else 0.0) for v in VARIANTEN
        }

    if n_trades == 0:
        print("keine Trades — Daten pruefen")
        return 1

    print(f"\n{n_trades} Einstiegssignale ueber {len(je_symbol)} Instrumente\n")
    kopf = (
        f"{'Variante':<12}{'n':>5}{'Ø Rendite':>11}{'Median':>9}{'Trefferq.':>10}{'Ø Verzug':>10}"
    )
    print(kopf)
    print("-" * len(kopf))
    tabelle: list[dict[str, object]] = []
    basis = statistics.fmean(ergebnisse["sofort"])
    for v in VARIANTEN:
        r = ergebnisse[v]
        if not r:
            continue
        mean = statistics.fmean(r)
        row = {
            "variante": v,
            "n": len(r),
            "mean": mean,
            "median": statistics.median(r),
            "winrate": sum(1 for x in r if x > 0) / len(r),
            "verzug": statistics.fmean(verzug[v]),
            "vs_sofort": mean - basis,
            "besser_in": sum(1 for s in je_symbol.values() if s[v] > s["sofort"]),
        }
        tabelle.append(row)
        print(
            f"{v:<12}{len(r):>5}{mean * 100:>10.2f}%{statistics.median(r) * 100:>8.2f}%"
            f"{row['winrate'] * 100:>9.0f}%{row['verzug']:>9.1f}d"
        )

    print("\nUnterschied zu 'sofort' (Prozentpunkte je Trade) und auf wie vielen Symbolen besser:")
    for row in tabelle:
        if row["variante"] == "sofort":
            continue
        d = float(row["vs_sofort"]) * 100
        # Gepaarter t-Test gegen die Grundlinie: dieselben Trades, andere Einstiege.
        paare = [
            a - b
            for a, b in zip(ergebnisse[str(row["variante"])], ergebnisse["sofort"], strict=False)
        ]
        t = (
            statistics.fmean(paare) / (statistics.stdev(paare) / math.sqrt(len(paare)))
            if len(paare) > 2 and statistics.stdev(paare) > 0
            else 0.0
        )
        print(
            f"  {row['variante']:<12}{d:+7.2f} pp   t = {t:+6.2f}   "
            f"besser auf {row['besser_in']}/{len(je_symbol)} Symbolen"
        )
        row["t"] = t

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out.replace(".md", ".json")).write_text(
        json.dumps(
            {
                "erzeugt": datetime.now(UTC).isoformat(),
                "n_signale": n_trades,
                "max_warte_tage": MAX_WARTE,
                "varianten": tabelle,
                "je_symbol": je_symbol,
                "nicht_gefuellt": nicht_gefuellt,
            },
            indent=2,
            default=float,
        ),
        encoding="utf-8",
    )
    print(f"\nZahlen: {args.out.replace('.md', '.json')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
