#!/usr/bin/env python3
"""Tageshandel Krypto + Gold — zweite, vorab festgelegte Studie (11.10.2026).

Anlass: Ozan, 11.10. 22:33: "ich moechte auch Alarme ... fuer die Daytrades normal und auch
fuer Krypto und fuer Gold". Die erste Gold-Studie (Asien-Range/Sweep, 15 Min) und die
Mean-Reversion-Studie (Coins, 1 Std.) fanden keinen Edge. Diese Studie prueft vier ANDERE,
in der Literatur bzw. Praxis dokumentierte Ideen. Alles hier steht fest, BEVOR gerechnet wird.

DATEN: data.binance.vision, Spot-Klines 1 Std., 01.2022–09.2026. 24 liquide Coins + PAXG
(goldgedeckter Token als Goldspur). IS 01.2022–12.2024, OOS 01.2025–09.2026.

GEMEINSAM: Einstieg zur Eroeffnung der Kerze NACH dem Signal. Stop und Ziel in derselben
Kerze -> Stop (pessimistisch). Kosten je Runde: Coins 0,25 %, PAXG 0,15 % (Gebuehr+Spread+
Slippage). Risiko < 0,3 % vom Kurs -> kein Trade (Kosten wuerden das R auffressen).
Je Symbol und Setup hoechstens ein offener Trade. R = Ergebnis / (Einstieg - Stop), nach Kosten.

SETUPS (Parameter fest, nicht optimiert):
  M1 "Ausreisser-Tag laeuft weiter" (Caporale & Plastun 2019, Momentum nach abnormen
     Tagesrenditen): UTC-Tagesrendite > 2 Standardabweichungen der letzten 30 Tage ->
     Einstieg zur Eroeffnung des Folgetags, Stop 1 Tages-ATR(14), Ausstieg Ende Folgetag.
     Short spiegelbildlich (< -2 sigma).
  M2 "48-Std.-Ausbruch mit Trend": Schluss ueber dem Hoch der letzten 48 Kerzen, Schluss
     ueber EMA 200 (1 Std.); bei Coins zusaetzlich Bitcoin ueber seiner EMA 200. Stop
     2 ATR(24) unter Einstieg. Ausstieg: Schluss unter dem Tief der letzten 24 Kerzen
     (naechste Eroeffnung), spaetestens nach 72 Kerzen. Short spiegelbildlich.
  M3 "Squeeze-Ausbruch": Bollinger(20,2)-Breite der Vorkerze im untersten Fuenftel der
     letzten 240 Kerzen, Schluss ueber dem oberen Band. Stop = Mittellinie, Ziel 2 R,
     spaetestens nach 48 Kerzen. Short spiegelbildlich.
  M4 "Intraday-Momentum" (Shen, Urquhart & Wang 2022, Bitcoin intraday TSMOM): Rendite
     00:00->20:00 UTC > +2 % (Coins) bzw. > +0,6 % (PAXG) -> Einstieg 20:00, Ausstieg 24:00,
     Stop 1,5 ATR(24). Short spiegelbildlich.

BESTANDEN ("belegt") nur, wenn fuer eine Setup-Richtung: IS Ø R > 0, OOS Ø R > 0,
OOS-Bootstrap-5 %-Grenze > 0, OOS n >= 100, und >= 60 % der Symbole im OOS im Plus.
"Kandidat": IS und OOS Ø R > 0, aber Bootstrap-Grenze nicht ueber 0.
Kein Parameter wird nach dem Ergebnis angepasst.

    python3 scripts/daytrade_studie.py --cache /tmp/bv1h --out docs/daytrade_studie_ergebnis.json
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import random
import statistics
import sys
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

COINS = [
    "BTC", "ETH", "SOL", "XRP", "ADA", "DOGE", "AVAX", "LINK", "DOT", "LTC", "BCH", "ATOM",
    "NEAR", "UNI", "AAVE", "FIL", "ETC", "ARB", "OP", "INJ", "SUI", "APT", "TRX", "XLM",
]
GOLD = "PAXG"
START = (2022, 1)
ENDE = (2026, 9)
OOS = datetime(2025, 1, 1, tzinfo=UTC)
KOSTEN = {"coin": 0.0025, "gold": 0.0015}
MIN_RISIKO = 0.003

Kerze = tuple  # (t, o, h, l, c)


# ----------------------------------------------------------------------------- Daten
def _monate():
    y, m = START
    while (y, m) <= ENDE:
        yield y, m
        m += 1
        if m > 12:
            y, m = y + 1, 1


def _hole(sym: str, y: int, m: int, cache: Path) -> Path | None:
    name = f"{sym}USDT-1h-{y}-{m:02d}.zip"
    ziel = cache / name
    if ziel.exists() and ziel.stat().st_size > 0:
        return ziel
    url = f"https://data.binance.vision/data/spot/monthly/klines/{sym}USDT/1h/{name}"
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            ziel.write_bytes(r.read())
        return ziel
    except Exception:
        return None


def lade(sym: str, cache: Path) -> list[Kerze]:
    cache.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(8) as ex:
        dateien = list(ex.map(lambda ym: _hole(sym, ym[0], ym[1], cache), list(_monate())))
    out = []
    for z in dateien:
        if z is None:
            continue
        with zipfile.ZipFile(z) as zf:
            for n in zf.namelist():
                for r in csv.reader(io.TextIOWrapper(zf.open(n))):
                    t = int(r[0])
                    if t > 10**14:
                        t //= 1000  # ab 2025 Mikrosekunden
                    out.append((datetime.fromtimestamp(t / 1000, UTC), float(r[1]), float(r[2]),
                                float(r[3]), float(r[4])))
    out.sort()
    return out


# ----------------------------------------------------------------------------- Indikatoren
def ema(werte: list[float], n: int) -> list[float | None]:
    a, out, e = 2 / (n + 1), [], None
    for i, v in enumerate(werte):
        e = v if e is None else a * v + (1 - a) * e
        out.append(e if i >= n - 1 else None)
    return out


def atr(k: list[Kerze], n: int) -> list[float | None]:
    out, tr_s = [], []
    for i, (_, _, h, lo, c) in enumerate(k):
        pc = k[i - 1][4] if i else c
        tr_s.append(max(h - lo, abs(h - pc), abs(lo - pc)))
        out.append(sum(tr_s[-n:]) / n if i >= n else None)
    return out


def tage(k: list[Kerze]) -> list[Kerze]:
    """1-Std.-Kerzen -> UTC-Tageskerzen (nur volle Tage mit >= 20 Stunden)."""
    g: dict = {}
    for t, o, h, lo, c in k:
        g.setdefault(t.date(), []).append((t, o, h, lo, c))
    res = []
    for d in sorted(g):
        x = g[d]
        if len(x) < 20:
            continue
        res.append((x[0][0], x[0][1], max(v[2] for v in x), min(v[3] for v in x), x[-1][4]))
    return res


# ----------------------------------------------------------------------------- Trade
def ergebnis(k, i_ein, richtung, stop, *, ziel=None, max_bars=None, ende_zeit=None,
             trail_n=None, kosten=0.0025):
    """Ab Kerze i_ein (Eroeffnung = Einstieg). Gibt (R, i_aus) oder None."""
    ein = k[i_ein][1]
    risiko = abs(ein - stop)
    if risiko <= 0 or risiko / ein < MIN_RISIKO:
        return None
    if (stop - ein) * richtung >= 0:
        return None
    kost_r = kosten * ein / risiko
    for j in range(i_ein, len(k)):
        t, o, h, lo, c = k[j]
        if ende_zeit is not None and t >= ende_zeit:
            return richtung * (o - ein) / risiko - kost_r, j
        if (lo <= stop) if richtung > 0 else (h >= stop):
            aus = min(o, stop) if richtung > 0 else max(o, stop)  # Gap durch den Stop
            return richtung * (aus - ein) / risiko - kost_r, j
        if ziel is not None and ((h >= ziel) if richtung > 0 else (lo <= ziel)):
            return richtung * (ziel - ein) / risiko - kost_r, j
        if max_bars is not None and j - i_ein + 1 >= max_bars:
            return richtung * (c - ein) / risiko - kost_r, j
        if trail_n is not None and j >= trail_n and j + 1 < len(k):
            fenster = k[j - trail_n:j]
            if richtung > 0 and c < min(x[3] for x in fenster):
                return richtung * (k[j + 1][1] - ein) / risiko - kost_r, j + 1
            if richtung < 0 and c > max(x[2] for x in fenster):
                return richtung * (k[j + 1][1] - ein) / risiko - kost_r, j + 1
    return None


# ----------------------------------------------------------------------------- Setups
def m1(k, kosten):
    d = tage(k)
    idx = {x[0].date(): i for i, x in enumerate(k) if x[0].hour == 0}
    a = atr(d, 14)
    trades = []
    for i in range(31, len(d) - 1):
        rets = [d[j][4] / d[j - 1][4] - 1 for j in range(i - 29, i)]
        sd = statistics.pstdev(rets)
        r = d[i][4] / d[i - 1][4] - 1
        if not sd or a[i] is None:
            continue
        for richtung in (1, -1):
            if richtung * r <= 2 * sd:
                continue
            folge = d[i + 1][0].date()
            if folge not in idx:
                continue
            i_ein = idx[folge]
            ein = k[i_ein][1]
            ende = datetime.combine(folge, datetime.min.time(), UTC) + timedelta(days=1)
            res = ergebnis(k, i_ein, richtung, ein - richtung * a[i], ende_zeit=ende, kosten=kosten)
            if res:
                trades.append((k[i_ein][0], richtung, res[0]))
    return trades


def m2(k, kosten, btc_ok=None, lookback=48, trail=24):
    e = ema([x[4] for x in k], 200)
    a = atr(k, 24)
    trades, frei_ab = [], {1: 0, -1: 0}
    for i in range(200, len(k) - 1):
        for richtung in (1, -1):
            if i < frei_ab[richtung] or e[i] is None or a[i] is None:
                continue
            c = k[i][4]
            fenster = k[i - lookback:i]
            if richtung > 0:
                ok = c > max(x[2] for x in fenster) and c > e[i]
                if btc_ok is not None:
                    ok = ok and btc_ok.get(k[i][0], False)
            else:
                ok = c < min(x[3] for x in fenster) and c < e[i]
                if btc_ok is not None:
                    ok = ok and not btc_ok.get(k[i][0], True)
            if not ok:
                continue
            ein = k[i + 1][1]
            res = ergebnis(k, i + 1, richtung, ein - richtung * 2 * a[i], max_bars=72,
                           trail_n=trail, kosten=kosten)
            if res:
                trades.append((k[i + 1][0], richtung, res[0]))
                frei_ab[richtung] = res[1] + 1
    return trades


def m3(k, kosten):
    closes = [x[4] for x in k]
    breite, mitte, oben, unten = [None] * len(k), [None] * len(k), [None] * len(k), [None] * len(k)
    for i in range(19, len(k)):
        w = closes[i - 19:i + 1]
        mu = sum(w) / 20
        sd = statistics.pstdev(w)
        mitte[i], oben[i], unten[i] = mu, mu + 2 * sd, mu - 2 * sd
        breite[i] = 4 * sd / mu if mu else None
    trades, frei_ab = [], {1: 0, -1: 0}
    for i in range(260, len(k) - 1):
        vor = breite[i - 1]
        hist = [b for b in breite[i - 240:i] if b is not None]
        if vor is None or len(hist) < 200:
            continue
        if sum(1 for b in hist if b < vor) / len(hist) > 0.2:
            continue
        for richtung in (1, -1):
            if i < frei_ab[richtung]:
                continue
            c = closes[i]
            if (richtung > 0 and c <= oben[i]) or (richtung < 0 and c >= unten[i]):
                continue
            ein = k[i + 1][1]
            stop = mitte[i]
            risiko = abs(ein - stop)
            res = ergebnis(k, i + 1, richtung, stop, ziel=ein + richtung * 2 * risiko,
                           max_bars=48, kosten=kosten)
            if res:
                trades.append((k[i + 1][0], richtung, res[0]))
                frei_ab[richtung] = res[1] + 1
    return trades


def m4(k, kosten, schwelle):
    a = atr(k, 24)
    pos = {x[0]: i for i, x in enumerate(k)}
    trades = []
    for i, x in enumerate(k):
        if x[0].hour != 20 or a[i - 1] is None:
            continue
        start = x[0].replace(hour=0)
        if start not in pos:
            continue
        r = k[i - 1][4] / k[pos[start]][1] - 1
        for richtung in (1, -1):
            if richtung * r <= schwelle:
                continue
            ein = x[1]
            ende = x[0].replace(hour=0) + timedelta(days=1)
            res = ergebnis(k, i, richtung, ein - richtung * 1.5 * a[i - 1], ende_zeit=ende,
                           kosten=kosten)
            if res:
                trades.append((x[0], richtung, res[0]))
    return trades


# ----------------------------------------------------------------------------- Auswertung
def bootstrap_5(werte: list[float], n: int = 2000) -> float | None:
    if len(werte) < 10:
        return None
    rnd = random.Random(7)
    mittel = sorted(sum(rnd.choice(werte) for _ in werte) / len(werte) for _ in range(n))
    return mittel[int(0.05 * n)]


def zusammenfassen(trades_je_sym: dict[str, list]) -> dict:
    alle = [(s, t, r) for s, tr in trades_je_sym.items() for (t, _, r) in tr]
    is_ = [r for _, t, r in alle if t < OOS]
    oos = [r for _, t, r in alle if t >= OOS]
    sym_oos = {}
    for s, t, r in alle:
        if t >= OOS:
            sym_oos[s] = sym_oos.get(s, 0.0) + r
    jahre = {}
    for _, t, r in alle:
        jahre.setdefault(t.year, []).append(r)
    mittel = lambda v: round(sum(v) / len(v), 3) if v else None  # noqa: E731
    out = {
        "is_n": len(is_), "is_r": mittel(is_),
        "oos_n": len(oos), "oos_r": mittel(oos),
        "oos_boot5": None if bootstrap_5(oos) is None else round(bootstrap_5(oos), 3),
        "oos_trefferquote": round(sum(1 for r in oos if r > 0) / len(oos), 3) if oos else None,
        "symbole_plus_oos": f"{sum(1 for v in sym_oos.values() if v > 0)}/{len(sym_oos)}",
        "je_jahr": {y: [len(v), mittel(v)] for y, v in sorted(jahre.items())},
    }
    anteil = (sum(1 for v in sym_oos.values() if v > 0) / len(sym_oos)) if sym_oos else 0
    if (out["is_r"] or -1) > 0 and (out["oos_r"] or -1) > 0 and (out["oos_boot5"] or -1) > 0 \
            and out["oos_n"] >= 100 and anteil >= 0.6:
        out["urteil"] = "BELEGT"
    elif (out["is_r"] or -1) > 0 and (out["oos_r"] or -1) > 0:
        out["urteil"] = "KANDIDAT"
    else:
        out["urteil"] = "KEIN EDGE"
    return out


def welle(daten: dict, btc_ok: dict, min_breite: int) -> dict:
    """M2 Coins long, nur wenn >= ``min_breite`` Coins in den 24 Std. bis zum Signal
    (inklusive) ebenfalls ein Ausbruchssignal hatten. Rechnet mit dem Live-Modul."""
    import bisect
    from datetime import timedelta

    from trading_agent.strategy.setups import daytrade_ausbruch as m

    roh, kand = [], []
    for s, k in daten.items():
        if s == GOLD:
            continue
        e = m.ema([x[4] for x in k], 200)
        a = m.atr(k, 24)
        frei = 0
        for i in range(200, len(k) - 1):
            sig = m.ist_signal(k, i, e, markt_ok=btc_ok.get(k[i][0], False))
            if sig:
                roh.append((k[i][0], s))
            if i < frei or not sig or not a[i]:
                continue
            res = m.verfolge(k, i + 1, k[i + 1][1] - 2 * a[i], kosten=KOSTEN["coin"])
            if res["status"] != "geschlossen":
                continue
            kand.append((k[i][0], s, res["r"]))
            frei = res["i_aus"] + 1
    roh.sort()
    zeiten = [t for t, _ in roh]
    je: dict[str, list] = {}
    for t, s, r in kand:
        lo = bisect.bisect_left(zeiten, t - timedelta(hours=24))
        hi = bisect.bisect_right(zeiten, t)
        if len({x for _, x in roh[lo:hi]}) >= min_breite:
            je.setdefault(s, []).append((t, 1, r))
    return zusammenfassen(je)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cache", default="/tmp/bv1h")
    ap.add_argument("--out", default="docs/daytrade_studie_ergebnis.json")
    args = ap.parse_args()
    cache = Path(args.cache)

    daten = {}
    for s in [*COINS, GOLD]:
        k = lade(s, cache)
        if len(k) > 2000:
            daten[s] = k
        print(f"{s}: {len(k)} Kerzen", flush=True)

    btc = daten["BTC"]
    e_btc = ema([x[4] for x in btc], 200)
    btc_ok = {x[0]: (e_btc[i] is not None and x[4] > e_btc[i]) for i, x in enumerate(btc)}

    ergebnisse: dict[str, dict] = {}
    for setup in ("M1", "M2", "M3", "M4"):
        for klasse in ("coin", "gold"):
            syms = COINS if klasse == "coin" else [GOLD]
            je = {1: {}, -1: {}}
            for s in syms:
                if s not in daten:
                    continue
                k, kost = daten[s], KOSTEN[klasse]
                if setup == "M1":
                    tr = m1(k, kost)
                elif setup == "M2":
                    tr = m2(k, kost, btc_ok if klasse == "coin" else None)
                elif setup == "M3":
                    tr = m3(k, kost)
                else:
                    tr = m4(k, kost, 0.02 if klasse == "coin" else 0.006)
                for richtung in (1, -1):
                    je[richtung][s] = [x for x in tr if x[1] == richtung]
            for richtung in (1, -1):
                schl = f"{setup}-{klasse}-{'long' if richtung > 0 else 'short'}"
                ergebnisse[schl] = zusammenfassen(je[richtung])
                print(schl, json.dumps(ergebnisse[schl], default=str), flush=True)

    # ---- Nachtrag (nach Sicht auf IS, vor Sicht auf OOS festgelegt): Breite der Welle.
    # M2 bei Coins war "KANDIDAT". Eine Aufteilung NUR der IS-Trades nach der Zahl der
    # Coins, die in den 24 Std. davor ebenfalls ausbrachen, zeigte: negativ bei 1-11,
    # deutlich positiv ab 12 von 24. Regel daraus: nur Ausbrueche in einer "Welle" mit
    # >= 12 Coins. OOS prueft diese eine Schwelle — es wurde keine weitere probiert.
    ergebnisse["M2B-coin-long-welle12"] = welle(daten, btc_ok, 12)
    # Formal waere das "BELEGT" — aber die Schwelle wurde nach Sicht auf IS gewaehlt, und in
    # einer Geld-Rechnung mit Positionslimits bleibt OOS nur ein schmaler Vorteil (Doku).
    ergebnisse["M2B-coin-long-welle12"]["urteil"] = "KANDIDAT (Schwelle nach IS-Sicht)"
    print("M2B-coin-long-welle12", json.dumps(ergebnisse["M2B-coin-long-welle12"], default=str))

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps({
        "erstellt": datetime.now(UTC).isoformat(),
        "daten": {s: [len(k), k[0][0].isoformat(), k[-1][0].isoformat()] for s, k in daten.items()},
        "ergebnisse": ergebnisse,
    }, indent=2, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
