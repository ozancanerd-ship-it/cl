#!/usr/bin/env python3
"""Bringt ein groesseres, breiter gestreutes Universum mehr? — vorab registrierte Pruefung.

Der Anlass ist die Recherchelage, nicht eine Ahnung: Quantica (2025) misst fuer dieselbe
Trendfolgeregel einen erwarteten Sharpe von 0.33 bei 10 Maerkten und 0.72 bei 69. Der
Gewinn entsteht im Portfolio, nicht im einzelnen Trade. Wenn das stimmt, ist ein
groesseres Universum der staerkste verfuegbare Hebel — staerker als jede Signalaenderung.

Aber: 20 Altcoins sind keine 20 Wetten. Sie laufen alle mit Bitcoin. Deshalb misst dieses
Skript nicht die ANZAHL, sondern die effektive Zahl unabhaengiger Wetten:

    N_eff = 1 / Summe(w_i * w_j * rho_ij)      (bei Gleichgewichtung)

Verglichen werden drei Universen auf identischer Regel, identischen Kosten, identischem
Zeitraum. Geaendert wird ausschliesslich, WAS im Korb liegt.

    python3 scripts/universe_study.py
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from trading_agent.strategy.setups.tsmom import TsmomParams, evaluate_tsmom

sys.path.insert(0, str(Path(__file__).resolve().parent))

HANDELSTAGE = 252

# ── Die Kandidaten ───────────────────────────────────────────────────────────────
# Auswahlregel, vor dem ersten Lauf festgelegt: nur was Ozan mit seinen Konten
# TATSAECHLICH kaufen kann (Binance/Kraken/Bybit fuer Krypto, Trade Republic fuer
# Aktien), und ueber Sektoren gestreut statt acht Technologiewerte.
KRYPTO = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "BNB": "BNBUSDT",
    "SOL": "SOLUSDT",
    "XRP": "XRPUSDT",
    "LINK": "LINKUSDT",
    "ADA": "ADAUSDT",
    "AVAX": "AVAXUSDT",
    "DOT": "DOTUSDT",
    "LTC": "LTCUSDT",
    "PAXG": "PAXGUSDT",
}
# Ein Vertreter je Sektor. Die Sektorzuordnung steht daneben, damit sichtbar ist,
# dass hier gestreut und nicht gesammelt wurde.
AKTIEN = {
    "NVDA": "Halbleiter",
    "AMD": "Halbleiter",
    "MSFT": "Software",
    "GOOGL": "Internet",
    "META": "Internet",
    "AAPL": "Hardware",
    "JNJ": "Pharma",
    "LLY": "Pharma",
    "UNH": "Krankenversicherung",
    "XOM": "Oel",
    "CVX": "Oel",
    "JPM": "Banken",
    "BRK-B": "Beteiligungen",
    "PG": "Konsum",
    "KO": "Getraenke",
    "WMT": "Handel",
    "CAT": "Maschinenbau",
    "HON": "Industrie",
    "NEE": "Versorger",
    "LIN": "Chemie",
    "V": "Zahlungsverkehr",
    "DIS": "Medien",
    "BA": "Luftfahrt",
    "T": "Telekom",
}
# Nur Signalquelle, mangels Broker nicht handelbar — als Vergleichsgruppe.
NICHT_HANDELBAR = {"EURUSD=X": "FX", "GBPUSD=X": "FX", "JPY=X": "FX", "GC=F": "Gold"}

# Die Anlageklassen, die in Ozans Korb komplett fehlen: Anleihen, Rohstoffe, Immobilien.
# Genau die liefern in den Studien den Diversifikationsgewinn — Aktien und Krypto sind
# beide "Risk-on" und laufen in Krisen zusammen. Ueber Trade Republic waeren sie als
# ETF erreichbar; sein Masterplan schliesst ETFs bisher aus. Diese Gruppe misst, was
# diese Regel kostet.
FEHLENDE_KLASSEN = {
    "TLT": "Staatsanleihen lang",
    "IEF": "Staatsanleihen mittel",
    "LQD": "Unternehmensanleihen",
    "TIP": "Inflationsindexiert",
    "DBC": "Rohstoffkorb",
    "SLV": "Silber",
    "USO": "Oel",
    "DBA": "Agrar",
    "VNQ": "Immobilien",
    "UUP": "US-Dollar",
}

HEUTE = {
    "BTCUSDT": "krypto",
    "ETHUSDT": "krypto",
    "BNBUSDT": "krypto",
    "NVDA": "aktien",
    "AAPL": "aktien",
    "MSFT": "aktien",
    "AMD": "aktien",
    "GOOGL": "aktien",
    "META": "aktien",
    "EURUSD=X": "fx",
    "GBPUSD=X": "fx",
    "JPY=X": "fx",
    "GC=F": "gold",
}


def _binance(symbol: str, bars: int = 1000) -> list[tuple[str, float]]:
    import httpx
    from tsmom_forward import BINANCE_HOSTS

    for host in BINANCE_HOSTS:
        try:
            r = httpx.get(
                f"{host}/api/v3/klines",
                params={"symbol": symbol, "interval": "1d", "limit": bars},
                timeout=30,
            ).json()
        except Exception:
            continue
        if isinstance(r, list) and r:
            return [
                (datetime.fromtimestamp(x[0] / 1000, tz=UTC).date().isoformat(), float(x[4]))
                for x in r[:-1]
            ]
    return []


def _yahoo(symbol: str, rng: str = "5y") -> list[tuple[str, float]]:
    import httpx

    try:
        r = httpx.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
            params={"interval": "1d", "range": rng},
            headers={"User-Agent": "Mozilla/5.0 (compatible; trading-agent/0.1; research)"},
            timeout=30,
        ).json()
        res = (r.get("chart", {}).get("result") or [{}])[0]
        ts = res.get("timestamp") or []
        cl = (res.get("indicators", {}).get("quote") or [{}])[0].get("close") or []
    except Exception:
        return []
    heute = datetime.now(UTC).date().isoformat()
    out = []
    for t, c in zip(ts, cl, strict=False):
        if c is None:
            continue
        d = datetime.fromtimestamp(int(t), tz=UTC).date().isoformat()
        if d < heute:
            out.append((d, float(c)))
    return out


def _returns(reihe: list[float]) -> list[float]:
    return [reihe[i] / reihe[i - 1] - 1.0 for i in range(1, len(reihe)) if reihe[i - 1] > 0]


def _korrelation(a: list[float], b: list[float]) -> float:
    n = min(len(a), len(b))
    if n < 30:
        return 0.0
    a, b = a[-n:], b[-n:]
    ma, mb = statistics.fmean(a), statistics.fmean(b)
    sa = math.sqrt(sum((x - ma) ** 2 for x in a))
    sb = math.sqrt(sum((x - mb) ** 2 for x in b))
    if sa == 0 or sb == 0:
        return 0.0
    return sum((x - ma) * (y - mb) for x, y in zip(a, b, strict=True)) / (sa * sb)


def _n_eff(namen: list[str], rets: dict[str, dict[str, float]]) -> tuple[float, float]:
    """Effektive Zahl unabhaengiger Wetten und mittlere Paarkorrelation.

    Bei Gleichgewichtung ist N_eff = n^2 / Summe(rho_ij). Zwei perfekt korrelierte
    Instrumente zaehlen als eines — genau der Unterschied zwischen "20 Coins" und
    "20 Wetten".
    """
    gemeinsam = sorted(set.intersection(*(set(rets[n]) for n in namen))) if namen else []
    if len(gemeinsam) < 60:
        return 0.0, 0.0
    serien = {n: [rets[n][d] for d in gemeinsam] for n in namen}
    summe, paare, n = 0.0, [], len(namen)
    for i, a in enumerate(namen):
        for j, b in enumerate(namen):
            rho = 1.0 if i == j else _korrelation(serien[a], serien[b])
            summe += rho
            if i < j:
                paare.append(rho)
    return (n * n / summe if summe > 0 else 0.0), (statistics.fmean(paare) if paare else 0.0)


def _portfolio(namen: list[str], kurse: dict[str, dict[str, float]], p: TsmomParams) -> dict:
    """Gleichgewichteter TSMOM-Korb. Kein Rebalancing-Trick, keine Optimierung."""
    tage = sorted(set.intersection(*(set(kurse[n]) for n in namen)))
    warm = p.warmup_bars()
    if len(tage) < warm + 60:
        return {}
    reihen = {n: [kurse[n][d] for d in tage] for n in namen}

    tages_r: list[float] = []
    for t in range(warm, len(tage) - 1):
        beitrag = []
        for n in namen:
            w = evaluate_tsmom(reihen[n][: t + 1], params=p).target_weight
            if w <= 0:
                continue
            r = reihen[n][t + 1] / reihen[n][t] - 1.0
            beitrag.append(w * r)
        tages_r.append(sum(beitrag) / len(namen))

    if len(tages_r) < 60:
        return {}
    mu, sd = statistics.fmean(tages_r), statistics.pstdev(tages_r)
    kurve, hoch, dd = 1.0, 1.0, 0.0
    for r in tages_r:
        kurve *= 1 + r
        hoch = max(hoch, kurve)
        dd = max(dd, 1 - kurve / hoch)
    jahre = len(tages_r) / HANDELSTAGE
    return {
        "n": len(namen),
        "tage": len(tages_r),
        "jahre": round(jahre, 2),
        "sharpe": round(mu / sd * math.sqrt(HANDELSTAGE), 3) if sd > 0 else 0.0,
        "rendite_pa": round((kurve ** (1 / jahre) - 1) * 100, 2) if jahre > 0 else 0.0,
        "vol_pa": round(sd * math.sqrt(HANDELSTAGE) * 100, 2),
        "max_dd": round(dd * 100, 2),
        "von": tage[warm],
        "bis": tage[-1],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="docs/UNIVERSUM-STUDIE.json")
    args = ap.parse_args()

    kurse: dict[str, dict[str, float]] = {}
    print("Kurse holen …")
    for name, sym in KRYPTO.items():
        d = _binance(sym)
        if len(d) > 400:
            kurse[name] = dict(d)
        print(f"  {name:<8} {len(d):>5} Tage")
    for sym in list(AKTIEN) + list(NICHT_HANDELBAR) + list(FEHLENDE_KLASSEN):
        d = _yahoo(sym)
        if len(d) > 400:
            kurse[sym] = dict(d)
        print(f"  {sym:<8} {len(d):>5} Tage")
        time.sleep(0.15)

    rets = {
        n: dict(zip(sorted(v)[1:], _returns([v[d] for d in sorted(v)]), strict=False))
        for n, v in kurse.items()
    }

    heute_namen = [n for n in ("BTC", "ETH", "BNB") if n in kurse] + [
        s for s in HEUTE if s in kurse and s not in ("BTCUSDT", "ETHUSDT", "BNBUSDT")
    ]
    handelbar = [n for n in list(KRYPTO) + list(AKTIEN) if n in kurse]
    alles = [n for n in kurse if n not in FEHLENDE_KLASSEN]

    fehlend = [s for s in FEHLENDE_KLASSEN if s in kurse]
    universen = {
        "heute_13": heute_namen,
        "handelbar_breit": handelbar,
        "alles_ohne_etf": alles,
        "mit_anleihen_rohstoffen": handelbar + fehlend,
        "nur_fehlende_klassen": fehlend,
    }

    p = TsmomParams()
    ergebnis = {}
    print(
        f"\n{'Universum':<20}{'n':>4}{'N_eff':>7}{'Ø rho':>8}{'Sharpe':>8}"
        f"{'Rend/J':>9}{'Vol':>7}{'MaxDD':>8}"
    )
    print("-" * 71)
    for label, namen in universen.items():
        if len(namen) < 3:
            continue
        neff, rho = _n_eff(namen, rets)
        perf = _portfolio(namen, kurse, p)
        if not perf:
            print(f"{label:<20} zu wenig gemeinsame Historie")
            continue
        ergebnis[label] = {**perf, "n_eff": round(neff, 2), "rho": round(rho, 3), "namen": namen}
        print(
            f"{label:<20}{perf['n']:>4}{neff:>7.1f}{rho:>8.2f}{perf['sharpe']:>8.2f}"
            f"{perf['rendite_pa']:>8.1f}%{perf['vol_pa']:>6.1f}%{perf['max_dd']:>7.1f}%"
        )

    print("\nEffektive Wetten je Gruppe (zeigt, wo Streuung wirklich entsteht):")
    for gruppe, namen in (
        ("nur Krypto", [n for n in KRYPTO if n in kurse]),
        ("nur Aktien", [s for s in AKTIEN if s in kurse]),
        ("Krypto+Aktien", [n for n in list(KRYPTO) + list(AKTIEN) if n in kurse]),
        ("nur Anleihen/Rohstoffe", [s for s in FEHLENDE_KLASSEN if s in kurse]),
        ("alles zusammen", [n for n in kurse]),
    ):
        if len(namen) >= 3:
            neff, rho = _n_eff(namen, rets)
            print(
                f"  {gruppe:<16} {len(namen):>3} Instrumente -> {neff:>5.1f} Wetten "
                f"(Ø rho {rho:+.2f})"
            )

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(
        json.dumps(
            {"erzeugt": datetime.now(UTC).isoformat(), "universen": ergebnis},
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"\nZahlen: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
