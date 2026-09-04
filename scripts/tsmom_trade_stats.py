#!/usr/bin/env python3
"""Was die Regel historisch je Position getan hat — Haltedauer, Ertrag, Trefferquote.

TSMOM ist eine Allokationsregel: sie liefert Gewichte, keine Trades mit Kursziel. Trotzdem
laesst sich aus der Gewichtsreihe ablesen, was faktisch passiert ist — eine Position beginnt,
wenn das Gewicht von 0 auf > 0 geht, und endet, wenn es auf 0 zurueckfaellt.

Daraus entstehen die drei Zahlen, die beim Handeln zaehlen:

    Wie lange dauert das ueblicherweise?   -> Haltedauer, Median und Spanne
    Wie viel kommt dabei heraus?           -> Ertrag je Position, Median und Quartile
    Wie oft geht es gut?                   -> Trefferquote

**Das sind Statistiken aus der Vergangenheit, keine Prognosen.** Der Median sagt, was in der
Haelfte der Faelle uebertroffen wurde — nicht, was der naechste Trade bringt. Die Spanne ist
wichtiger als der Mittelwert: sie zeigt, womit man realistisch rechnen muss.

Der Ausstieg ist keine Kurszielfrage. Ausgestiegen wird, wenn das Signal dreht — deshalb
steht bei jeder offenen Position, wie weit sie im Vergleich zur ueblichen Dauer ist.

    python3 scripts/tsmom_trade_stats.py
    python3 scripts/tsmom_trade_stats.py --json-out data/.../trade_stats.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from trading_agent.strategy.setups.tsmom import TsmomParams, evaluate_tsmom

sys.path.insert(0, str(Path(__file__).parent))
from tsmom_forward import UNIVERSE, _repo_closes

COST_PCT = 0.20


@dataclass(frozen=True, slots=True)
class Episode:
    """Eine zusammenhaengende Phase, in der die Regel investiert war."""

    instrument: str
    entry: str
    exit: str | None
    days: int
    ret_pct: float
    peak_pct: float
    trough_pct: float
    avg_weight: float
    open_now: bool


def episodes_for(
    instrument: str, times: list[datetime], closes: list[float], params: TsmomParams
) -> list[Episode]:
    warm = params.warmup_bars()
    if len(closes) < warm + 30:
        return []
    out: list[Episode] = []
    in_pos = False
    eq = 1.0
    peak = trough = 1.0
    start_i = 0
    weights: list[float] = []
    prev_w = 0.0

    for i in range(warm, len(closes)):
        w = evaluate_tsmom(closes[:i], params=params).target_weight
        r_mkt = closes[i] / closes[i - 1] - 1.0
        # Ertrag der Position: gewichtete Marktrendite abzueglich Umschichtungskosten
        r = prev_w * r_mkt - abs(w - prev_w) * (COST_PCT / 100.0)

        if not in_pos and w > 0:
            in_pos, eq, peak, trough, start_i, weights = True, 1.0, 1.0, 1.0, i, []
        elif in_pos:
            eq *= 1 + r
            peak = max(peak, eq)
            trough = min(trough, eq)
            weights.append(prev_w)
            if w <= 0:
                out.append(
                    Episode(
                        instrument=instrument,
                        entry=times[start_i].date().isoformat(),
                        exit=times[i].date().isoformat(),
                        days=(times[i] - times[start_i]).days,
                        ret_pct=(eq - 1) * 100,
                        peak_pct=(peak - 1) * 100,
                        trough_pct=(trough - 1) * 100,
                        avg_weight=statistics.fmean(weights) if weights else 0.0,
                        open_now=False,
                    )
                )
                in_pos = False
        prev_w = w

    if in_pos:
        out.append(
            Episode(
                instrument=instrument,
                entry=times[start_i].date().isoformat(),
                exit=None,
                days=(times[-1] - times[start_i]).days,
                ret_pct=(eq - 1) * 100,
                peak_pct=(peak - 1) * 100,
                trough_pct=(trough - 1) * 100,
                avg_weight=statistics.fmean(weights) if weights else 0.0,
                open_now=True,
            )
        )
    return out


def _q(vals: list[float], p: float) -> float:
    if not vals:
        return 0.0
    srt = sorted(vals)
    return srt[min(len(srt) - 1, max(0, int(len(srt) * p)))]


# Nach Haltedauer geschnitten. Ohne diese Trennung ist jede Kennzahl irrefuehrend:
# der Median wird von kurzen Rauschphasen dominiert, waehrend der Ertrag fast
# vollstaendig aus den wenigen langen Laeufen kommt.
BUCKETS: tuple[tuple[str, int, int], ...] = (
    ("unter 1 Woche", 0, 7),
    ("1 bis 4 Wochen", 7, 28),
    ("1 bis 3 Monate", 28, 91),
    ("ueber 3 Monate", 91, 10**6),
)


def bucket_of(days: int) -> str:
    for name, lo, hi in BUCKETS:
        if lo <= days < hi:
            return name
    return BUCKETS[-1][0]


def summarise(eps: list[Episode]) -> dict[str, float]:
    closed = [e for e in eps if not e.open_now]
    if not closed:
        return {}
    rets = [e.ret_pct for e in closed]
    days = [float(e.days) for e in closed]
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r <= 0]
    # Beitrag zum Gesamtergebnis: Summe der Ertraege, nicht deren Median.
    return {
        "n": float(len(closed)),
        "win_rate": len(wins) / len(closed) * 100,
        "days_median": statistics.median(days),
        "days_q75": _q(days, 0.75),
        "days_max": max(days),
        "ret_median": statistics.median(rets),
        "ret_sum": sum(rets),
        "ret_best": max(rets),
        "ret_worst": min(rets),
        "ret_mean_win": statistics.fmean(wins) if wins else 0.0,
        "ret_mean_loss": statistics.fmean(losses) if losses else 0.0,
        "worst_dip": min(e.trough_pct for e in closed),
    }


def by_bucket(eps: list[Episode]) -> dict[str, dict[str, float]]:
    closed = [e for e in eps if not e.open_now]
    out: dict[str, dict[str, float]] = {}
    for name, _lo, _hi in BUCKETS:
        sel = [e for e in closed if bucket_of(e.days) == name]
        if sel:
            out[name] = summarise(sel)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", default="data/repository_real")
    ap.add_argument("--json-out", default="data/repository_real/research/tsmom_trade_stats.json")
    args = ap.parse_args()

    params = TsmomParams()
    all_eps: list[Episode] = []
    per_instrument: dict[str, dict[str, float]] = {}

    for canon in UNIVERSE:
        hist = _repo_closes(args.repo, canon)
        if len(hist) < params.warmup_bars() + 30:
            continue
        times = [t for t, _ in hist]
        closes = [c for _, c in hist]
        eps = episodes_for(canon, times, closes, params)
        all_eps += eps
        s = summarise(eps)
        if s:
            per_instrument[canon] = s

    if not all_eps:
        print("keine Positionen gefunden — Daten pruefen")
        return 1

    overall = summarise(all_eps)
    buckets = by_bucket(all_eps)
    closed = [e for e in all_eps if not e.open_now]
    total_gain = sum(e.ret_pct for e in closed if e.ret_pct > 0) or 1.0

    print("=" * 74)
    print("  WAS DIE REGEL HISTORISCH JE POSITION GETAN HAT")
    print("=" * 74)
    print("  Statistik aus der Vergangenheit, KEINE Prognose.")
    print()
    print(
        f"  {overall['n']:.0f} abgeschlossene Positionen · Trefferquote {overall['win_rate']:.0f} %"
    )
    print()
    print("  DAS WICHTIGSTE ZUERST: die Verteilung, nicht der Durchschnitt")
    print()
    print(
        f"  {'Haltedauer':<18}{'Anzahl':>7}{'Trefferq.':>11}{'Median':>9}{'Bester':>10}{'Anteil am Gewinn':>19}"
    )
    print("  " + "-" * 72)
    for name, _lo, _hi in BUCKETS:
        b = buckets.get(name)
        if not b:
            continue
        gain = sum(e.ret_pct for e in closed if bucket_of(e.days) == name and e.ret_pct > 0)
        print(
            f"  {name:<18}{b['n']:>7.0f}{b['win_rate']:>10.0f}%{b['ret_median']:>+8.1f}%"
            f"{b['ret_best']:>+9.1f}%{gain / total_gain * 100:>18.0f} %"
        )
    print()
    long_share = sum(e.ret_pct for e in closed if e.days >= 28 and e.ret_pct > 0) / total_gain * 100
    n_long = sum(1 for e in closed if e.days >= 28)
    print(
        f"  Lies das so: {n_long} von {overall['n']:.0f} Positionen liefen laenger als einen Monat"
    )
    print(
        f"  ({n_long / overall['n'] * 100:.0f} %) — und sie tragen {long_share:.0f} % des gesamten Gewinns."
    )
    print()
    print("  Die kurzen Positionen sind ueberwiegend kleine Verluste. Das ist kein Fehler,")
    print("  sondern die Funktionsweise: die Regel probiert oft an, bricht schnell ab, wenn")
    print("  kein Trend entsteht, und laesst die wenigen laufen, die tragen.")
    print()
    print("  WAS DAS FUERS HANDELN HEISST")
    print(
        f"    Rechne damit, dass etwa {100 - overall['win_rate']:.0f} % der Positionen im Minus enden."
    )
    print(f"    Der typische Verlust ist klein ({overall['ret_mean_loss']:+.1f} % im Schnitt),")
    print(
        f"    der typische Gewinn deutlich groesser ({overall['ret_mean_win']:+.1f} % im Schnitt)."
    )
    print(f"    Tiefster Zwischenstand einer Position: {overall['worst_dip']:+.1f} % —")
    print("    so weit ging es ins Minus, bevor die Position geschlossen wurde.")
    print()
    print("    Der haeufigste Fehler waere, die langen Laeufe frueh zu schliessen.")
    print("    Genau die machen das Ergebnis.")

    open_now = [e for e in all_eps if e.open_now]
    if open_now:
        long_days = [float(e.days) for e in closed if e.days >= 28]
        med_long = statistics.median(long_days) if long_days else 0.0
        print()
        print("  AKTUELL OFFEN")
        print("  Vergleichsmassstab: Positionen, die laenger als einen Monat liefen,")
        print(f"  dauerten im Median {med_long:.0f} Tage.")
        print()
        print(f"  {'Instrument':<13}{'seit':<12}{'Tage':>6}{'Stand':>9}  Einordnung")
        print("  " + "-" * 60)
        for e in sorted(open_now, key=lambda x: -x.days):
            if e.days < 7:
                note = "frisch — noch nichts zu sehen"
            elif e.days < 28:
                note = "in der Anlaufphase"
            elif med_long and e.days > med_long * 1.5:
                note = "ein langer Laeufer"
            else:
                note = "etablierter Trend"
            print(f"  {e.instrument:<13}{e.entry:<12}{e.days:>6}{e.ret_pct:>+8.1f}%  {note}")

    print()
    print("  WANN WIRD VERKAUFT")
    print("  Nicht bei einem Kursziel. Die Position endet, wenn das Signal dreht — wenn die")
    print("  Zustimmung der fuenf Rueckblickfenster unter 20 % faellt. Deshalb gibt es keinen")
    print("  festen Take-Profit: laufende Gewinne werden nicht abgeschnitten, und der Ausstieg")
    print("  kommt, wenn der Trend endet, nicht wenn eine Zahl erreicht ist.")
    print("=" * 74)

    Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.json_out, "w", encoding="utf-8") as fh:
        json.dump(
            {
                "generated": datetime.now(UTC).isoformat(),
                "note": "Historische Statistik, keine Prognose.",
                "overall": overall,
                "by_duration_bucket": buckets,
                "per_instrument": per_instrument,
                "open_positions": [asdict(e) for e in open_now],
            },
            fh,
            indent=2,
            ensure_ascii=False,
        )
    print(f"\n  geschrieben: {args.json_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
