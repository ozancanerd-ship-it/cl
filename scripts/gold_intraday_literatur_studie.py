#!/usr/bin/env python3
"""Gold-Tageshandel nach der Literatur — dritte Gold-Intraday-Studie (12.10.2026).

Anlass — Ozan, 12.10. 15:19: "Hauptfokus ist Swing Trading, aber jetzt haben wir auch Gold
drin und das ist Day Trading, da muss man anders vorgehen, anders analysieren ... recherchieren
... dass wir realistisch auch damit Gewinn erzielen koennen".

Zwei eigene Gold-Studien fanden keinen Vorteil (Asien-Range/Sweep 15 Min.; 48-Std.-Ausbruch).
Diese Studie prueft die zwei am besten DOKUMENTIERTEN Tageshandels-Regeln aus der Literatur —
unveraendert uebertragen, Parameter aus den Papieren, nichts optimiert:

NM  "Noise Area"-Momentum — Zarattini, Aziz & Barbon (2024), "Beat the Market: An Effective
    Intraday Momentum Strategy for S&P500 ETF (SPY)", Swiss Finance Institute RP 24-97
    (netto 19,6 % p.a., Sharpe 1,33 auf SPY 2007–2024). Regeln:
    * Rauschzone je Uhrzeit: sigma(HH:MM) = Mittel der letzten 14 Tage von
      |Kurs(HH:MM) / Eroeffnung − 1|. Obergrenze = max(Eroeffnung, Vortagesschluss)·(1+sigma),
      Untergrenze = min(Eroeffnung, Vortagesschluss)·(1−sigma).
    * Nur zur vollen und halben Stunde: Kurs ueber Obergrenze -> long, unter Untergrenze -> short.
    * Ausstieg: long, sobald Kurs < max(Obergrenze, VWAP seit Eroeffnung); short spiegelbildlich.
      Kreuzt der Kurs die Gegengrenze: drehen. Am Ende der Sitzung alles schliessen.
    Uebertragung auf Gold: "Eroeffnung" = 08:30 New Yorker Zeit (US-Daten 08:30, COMEX-
    Praesenz ab 08:20 ET), Sitzung 08:30–15:00 ET (6,5 Std. wie SPY 09:30–16:00), erste
    Pruefung 09:00 ET. Sommerzeit ueber America/New_York.

ORB "5-Minuten-Opening-Range-Breakout" — Zarattini & Aziz (2023), "Can Day Trading Really Be
    Profitable?" (SSRN 4416622). Erste 5-Min.-Kerze ab Eroeffnung gruen -> long zur
    naechsten Eroeffnung, Stop am Kerzentief; rot -> short, Stop am Hoch; Doji -> nichts.
    Ziel 10 R, sonst Schluss zum Sitzungsende. Eine unabhaengige Nachrechnung auf fuenf
    Indizes (mql5, 2026) fand brutto +0,05 bis +0,13 R, netto nach Spread ~0 — die Kosten sind
    hier der Prueffall.

DATEN: PAXG/USDT 5 Min., Binance Vision, 01.2022–09.2026 (goldgedeckter Token, folgt Gold eng,
handelt 24/7 — nur Wochentage werden gehandelt). IS 2022–2024, OOS 01.2025–09.2026.
KOSTEN je Runde (Kauf + Verkauf), festgelegt nach den Gebuehren der Handelsplaetze:
  0,04 % Limit-Orders auf einem Gold-Perp (z. B. Binance XAUUSDT, 2 x 0,02 % Maker),
  0,12 % Marktorders auf einem Gold-Perp (2 x 0,05 % Taker + 0,02 % Slippage)  <- Hauptfall,
  0,80 % Kraken-Spot-Marktorder (2 x 0,40 % Taker).
BESTANDEN nur bei 0,12 %: IS Ø > 0, OOS Ø > 0, OOS-Bootstrap-5-%-Grenze > 0.

    python3 scripts/gold_intraday_literatur_studie.py --cache /tmp/bv5m --out docs/gold_intraday_literatur.json
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import random
import statistics
import urllib.request
import zipfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
START, ENDE = (2022, 1), (2026, 9)
OOS = datetime(2025, 1, 1, tzinfo=UTC)
KOSTEN = {"limit_perp": 0.0004, "markt_perp": 0.0012, "kraken": 0.008}
HAUPT = "markt_perp"
OFFEN = time(8, 30)
SCHLUSS = time(15, 0)
LOOKBACK = 14
ORB_ZIEL_R = 10.0


# ----------------------------------------------------------------------------- Daten
def _monate():
    y, m = START
    while (y, m) <= ENDE:
        yield y, m
        m += 1
        if m > 12:
            y, m = y + 1, 1


def _hole(y: int, m: int, cache: Path) -> Path | None:
    name = f"PAXGUSDT-5m-{y}-{m:02d}.zip"
    ziel = cache / name
    if ziel.exists() and ziel.stat().st_size > 0:
        return ziel
    url = f"https://data.binance.vision/data/spot/monthly/klines/PAXGUSDT/5m/{name}"
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            ziel.write_bytes(r.read())
        return ziel
    except Exception:
        return None


def lade(cache: Path) -> list[tuple]:
    """(t_utc, o, h, l, c, volumen) je 5-Min.-Kerze, t = Kerzenbeginn."""
    cache.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(8) as ex:
        dateien = list(ex.map(lambda ym: _hole(ym[0], ym[1], cache), list(_monate())))
    out = []
    for z in dateien:
        if z is None:
            continue
        with zipfile.ZipFile(z) as zf:
            for n in zf.namelist():
                for r in csv.reader(io.TextIOWrapper(zf.open(n))):
                    t = int(r[0])
                    if t > 10**14:
                        t //= 1000
                    out.append(
                        (
                            datetime.fromtimestamp(t / 1000, UTC),
                            float(r[1]),
                            float(r[2]),
                            float(r[3]),
                            float(r[4]),
                            float(r[5]),
                        )
                    )
    out.sort()
    return out


def sitzungen(k: list[tuple]) -> dict[date, list[tuple]]:
    """Kerzen 08:30–15:00 New Yorker Zeit je Werktag (Kerzenbeginn >= 08:30, < 15:00)."""
    tage: dict[date, list[tuple]] = defaultdict(list)
    for x in k:
        lt = x[0].astimezone(ET)
        if lt.weekday() >= 5:
            continue
        if OFFEN <= lt.time() < SCHLUSS:
            tage[lt.date()].append(x)
    # nur vollstaendige Sitzungen (78 Kerzen = 6,5 Std.)
    return {d: v for d, v in sorted(tage.items()) if len(v) >= 76}


def _min_seit_offen(x: tuple) -> int:
    lt = x[0].astimezone(ET)
    return (lt.hour * 60 + lt.minute) - (OFFEN.hour * 60 + OFFEN.minute)


# ----------------------------------------------------------------------------- NM
PRUEF = list(range(30, 390, 30))  # 09:00 ... 14:30 ET (Minuten seit 08:30) — Kurs am Kerzenende


def _kurs_bei(tag: list[tuple]) -> dict[int, tuple]:
    """Minute seit Eroeffnung (Kerzen-ENDE) -> Kerze. Ende = Beginn + 5."""
    return {_min_seit_offen(x) + 5: x for x in tag}


def nm_tage(tage: dict[date, list[tuple]]) -> list[dict]:
    daten = list(tage.items())
    aus = []
    for j in range(LOOKBACK + 1, len(daten)):
        d, tag = daten[j]
        vor = daten[j - 1][1]
        o = tag[0][1]
        prev_c = vor[-1][4]
        hist = []
        for _, t in daten[j - LOOKBACK : j]:
            kb = _kurs_bei(t)
            hist.append((t[0][1], kb))
        sigma = {}
        for m in PRUEF:
            v = [abs(kb[m][4] / o0 - 1) for o0, kb in hist if m in kb]
            if len(v) >= LOOKBACK - 2:
                sigma[m] = statistics.mean(v)
        kb = _kurs_bei(tag)
        pos, ein, trades = 0, None, []
        cum_pv = cum_v = 0.0
        vwap_bis: dict[int, float] = {}
        for x in tag:
            tp = (x[2] + x[3] + x[4]) / 3
            cum_pv += tp * x[5]
            cum_v += x[5]
            vwap_bis[_min_seit_offen(x) + 5] = cum_pv / cum_v if cum_v > 0 else x[4]
        for m in PRUEF:
            if m not in kb or m not in sigma:
                continue
            p = kb[m][4]
            ub = max(o, prev_c) * (1 + sigma[m])
            lb = min(o, prev_c) * (1 - sigma[m])
            vw = vwap_bis.get(m, p)
            if pos == 1 and p < max(ub, vw):
                trades.append((1, ein, p))
                pos = 0
            elif pos == -1 and p > min(lb, vw):
                trades.append((-1, ein, p))
                pos = 0
            if pos == 0:
                if p > ub:
                    pos, ein = 1, p
                elif p < lb:
                    pos, ein = -1, p
        if pos != 0:
            trades.append((pos, ein, tag[-1][4]))
        brutto = sum(r * (aus_ / e - 1) for r, e, aus_ in trades)
        aus.append(
            {
                "tag": d,
                "t": datetime.combine(d, OFFEN, ET).astimezone(UTC),
                "n": len(trades),
                "brutto": brutto,
                **{k: brutto - len(trades) * c for k, c in KOSTEN.items()},
            }
        )
    return aus


# ----------------------------------------------------------------------------- ORB
def orb_trades(tage: dict[date, list[tuple]]) -> list[dict]:
    aus = []
    for d, tag in tage.items():
        k0 = tag[0]
        if k0[4] == k0[1]:
            continue
        r = 1 if k0[4] > k0[1] else -1
        ein = tag[1][1]
        stop = k0[3] if r == 1 else k0[2]
        risiko = (ein - stop) * r
        if risiko <= 0:
            continue
        ziel = ein + r * ORB_ZIEL_R * risiko
        exit_p = tag[-1][4]
        for x in tag[1:]:
            if (r == 1 and x[3] <= stop) or (r == -1 and x[2] >= stop):
                exit_p = stop
                break
            if (r == 1 and x[2] >= ziel) or (r == -1 and x[3] <= ziel):
                exit_p = ziel
                break
        brutto_r = (exit_p - ein) * r / risiko
        aus.append(
            {
                "tag": d,
                "t": datetime.combine(d, OFFEN, ET).astimezone(UTC),
                "risiko_pct": risiko / ein * 100,
                "brutto": brutto_r,
                **{k: brutto_r - c * ein / risiko for k, c in KOSTEN.items()},
            }
        )
    return aus


# ----------------------------------------------------------------------------- Auswertung
def bootstrap_5(v: list[float], n: int = 3000) -> float | None:
    if len(v) < 30:
        return None
    rnd = random.Random(1)
    m = sorted(statistics.mean(rnd.choices(v, k=len(v))) for _ in range(n))
    return m[int(0.05 * n)]


def fasse(zeilen: list[dict], einheit: str) -> dict:
    out = {"einheit": einheit}
    for teil, f in (("is", lambda z: z["t"] < OOS), ("oos", lambda z: z["t"] >= OOS)):
        sel = [z for z in zeilen if f(z)]
        blk = {"n": len(sel)}
        for k in ("brutto", *KOSTEN):
            v = [z[k] for z in sel]
            if not v:
                continue
            m = statistics.mean(v)
            sd = statistics.pstdev(v)
            blk[k] = {
                "schnitt": round(m * (100 if einheit == "tag_pct" else 1), 4),
                "sharpe_jahr": round(m / sd * math.sqrt(252), 2)
                if sd and einheit == "tag_pct"
                else None,
                "treffer": round(sum(x > 0 for x in v) / len(v), 3),
            }
        v = [z[HAUPT] for z in sel]
        b = bootstrap_5(v)
        blk["bootstrap5_haupt"] = (
            None if b is None else round(b * (100 if einheit == "tag_pct" else 1), 4)
        )
        out[teil] = blk
    je_jahr = defaultdict(list)
    for z in zeilen:
        je_jahr[z["t"].year].append(z[HAUPT])
    out["jahre_haupt"] = {
        str(y): round(statistics.mean(v) * (100 if einheit == "tag_pct" else 1), 4)
        for y, v in sorted(je_jahr.items())
    }
    i, o = out["is"].get(HAUPT, {}), out["oos"].get(HAUPT, {})
    out["bestanden"] = bool(
        i.get("schnitt", -1) > 0
        and o.get("schnitt", -1) > 0
        and (out["oos"]["bootstrap5_haupt"] or -1) > 0
    )
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cache", default="/tmp/bv5m")
    ap.add_argument("--out", default="docs/gold_intraday_literatur.json")
    args = ap.parse_args()
    k = lade(Path(args.cache))
    print(f"{len(k)} Kerzen 5 Min., {k[0][0]} – {k[-1][0]}")
    tage = sitzungen(k)
    print(f"{len(tage)} vollstaendige Sitzungen 08:30–15:00 ET")
    nm = nm_tage(tage)
    orb = orb_trades(tage)
    erg = {
        "erzeugt": datetime.now(UTC).isoformat(),
        "daten": f"PAXG/USDT 5 Min. Binance Vision, {len(tage)} Sitzungen 08:30–15:00 ET",
        "kosten_je_runde": KOSTEN,
        "hauptfall": HAUPT,
        "nm": fasse(nm, "tag_pct"),
        "nm_trades_je_tag": round(statistics.mean(z["n"] for z in nm), 2),
        "nm_tage_ohne_trade": round(sum(z["n"] == 0 for z in nm) / len(nm), 3),
        "orb": fasse(orb, "r"),
        "orb_risiko_median_pct": round(statistics.median(z["risiko_pct"] for z in orb), 4),
        "doc": "docs/GOLD-INTRADAY-LITERATUR-STUDIE-2026-10.md",
    }
    print(json.dumps(erg, indent=1, default=str))
    Path(args.out).write_text(
        json.dumps(erg, indent=1, ensure_ascii=False, default=str), encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
