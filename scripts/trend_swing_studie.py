#!/usr/bin/env python3
"""Trend-Swing-Studie — die Regel aus docs/TREND-SWING-STUDIE-2026-09.md, genau so.

    python3 scripts/trend_swing_studie.py --npz DIR [--zeilen DIR] [--out DATEI]

Die Regel, die Zeitraeume und die Entscheidungsregel stehen VORAB im Dokument. Dieses
Skript rechnet sie nur aus. Wer hier einen Parameter aendert, testet eine andere
Hypothese — die gehoert erst ins Dokument, dann in den Code.

``--zeilen`` sind die Scan-Zeilen aus Phase A der Signal-Studie (je Coin eine JSONL). Sie
werden nur fuer die Einordnung „nur Luecke" gebraucht: hatte der Struktur-Scanner zum
selben Zeitpunkt schon ein handelbares Long?
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

COINS = [
    "BTC",
    "ETH",
    "SOL",
    "XRP",
    "ADA",
    "DOGE",
    "LINK",
    "AVAX",
    "DOT",
    "LTC",
    "BCH",
    "ATOM",
    "NEAR",
    "UNI",
    "AAVE",
    "INJ",
    "SEI",
    "ARB",
    "OP",
    "SUI",
    "FET",
    "HBAR",
    "XLM",
    "TAO",
]

# --- Die Regel (fest, siehe Dokument) -------------------------------------------------
R_LANG = 63
R_KURZ = 21
EMA_SCHNELL = 20
EMA_LANGSAM = 50
ATR_N = 14
STOP_ATR = 2.5
TRAIL_ATR = 3.0
MAX_TAGE = 30
KOSTEN_PCT = 0.5

ZEITRAEUME = {
    "VP": (datetime(2024, 1, 1, tzinfo=UTC), datetime(2025, 3, 1, tzinfo=UTC)),
    "IS": (datetime(2025, 3, 1, tzinfo=UTC), datetime(2026, 1, 1, tzinfo=UTC)),
    "OOS": (datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 9, 21, tzinfo=UTC)),
}
BOOT_N = 5000
BOOT_SEED = 7


@dataclass
class Trade:
    coin: str
    einstieg_t: str
    einstieg: float
    stop0: float
    risiko: float
    ausstieg_t: str | None
    ausstieg: float | None
    grund: str  # stop | zeit | offen
    tage: int
    r: float
    pct: float
    scanner_long: bool | None  # hatte der Struktur-Scanner schon ein handelbares Long?


def _ema_reihe(werte: list[float], n: int) -> list[float | None]:
    aus: list[float | None] = [None] * len(werte)
    if len(werte) < n:
        return aus
    k = 2.0 / (n + 1)
    e = sum(werte[:n]) / n
    aus[n - 1] = e
    for i in range(n, len(werte)):
        e = werte[i] * k + e * (1 - k)
        aus[i] = e
    return aus


def _atr_reihe(h: list[float], lo: list[float], c: list[float], n: int) -> list[float | None]:
    """ATR nach Wilder — nur Kerzen bis einschliesslich i."""
    aus: list[float | None] = [None] * len(c)
    tr = []
    for i in range(len(c)):
        if i == 0:
            tr.append(h[i] - lo[i])
        else:
            tr.append(max(h[i] - lo[i], abs(h[i] - c[i - 1]), abs(lo[i] - c[i - 1])))
    if len(tr) < n:
        return aus
    a = sum(tr[:n]) / n
    aus[n - 1] = a
    for i in range(n, len(tr)):
        a = (a * (n - 1) + tr[i]) / n
        aus[i] = a
    return aus


def _lade_d1(npz: Path, coin: str) -> dict[str, list[Any]]:
    import numpy as np

    with np.load(npz / f"{coin}_1d.npz") as roh:
        d = {k: roh[k].tolist() for k in ("t", "o", "h", "l", "c")}
    # Schlusszeit der Tageskerze = Eroeffnung + 1 Tag. Zu diesem Zeitpunkt wird geprueft.
    d["schluss"] = [datetime.fromtimestamp(t / 1000.0, tz=UTC) + timedelta(days=1) for t in d["t"]]
    return d


def _scanner_long(zeilen: Path | None, coin: str) -> dict[datetime, bool]:
    """Zeitpunkt → hatte der Scanner ein handelbares Long? Nur 00:00-UTC-Zeilen."""
    if zeilen is None:
        return {}
    p = zeilen / f"{coin}.jsonl"
    if not p.exists():
        return {}
    aus: dict[datetime, bool] = {}
    for line in p.open(encoding="utf-8"):
        r = json.loads(line)
        if "t" not in r:
            continue
        t = datetime.fromisoformat(r["t"])
        aus[t] = bool(r.get("handelbar")) and r.get("richtung") == "long"
    return aus


@dataclass
class Position:
    """Eine offene Trend-Swing-Position — genau der Zustand, den der Vorwaertslauf speichert."""

    einstieg_t: str
    einstieg: float
    risiko: float
    stop: float
    hoch: float
    tage: int = 0
    scanner_long: bool | None = None


def indikatoren(d: dict[str, list[Any]]) -> dict[str, list[float | None]]:
    return {
        "e20": _ema_reihe(d["c"], EMA_SCHNELL),
        "e50": _ema_reihe(d["c"], EMA_LANGSAM),
        "atr": _atr_reihe(d["h"], d["l"], d["c"], ATR_N),
    }


ERSTE_KERZE = max(R_LANG, EMA_LANGSAM, ATR_N)


def signal(d: dict[str, list[Any]], ind: dict[str, list[float | None]], i: int) -> bool:
    """Die drei Bedingungen der Regel zum Schluss von Kerze ``i``."""
    if i < ERSTE_KERZE:
        return False
    c = d["c"]
    a, s, l_ = ind["atr"][i], ind["e20"][i], ind["e50"][i]
    if a is None or s is None or l_ is None or a <= 0:
        return False
    return (
        c[i] / c[i - R_LANG] - 1.0 > 0 and c[i] / c[i - R_KURZ] - 1.0 > 0 and c[i] > l_ and s > l_
    )


def _abschluss(
    coin: str,
    pos: Position,
    ausstieg_t: str | None,
    ausstieg: float | None,
    bewertet: float,
    grund: str,
) -> Trade:
    kosten_r = (KOSTEN_PCT / 100.0) * pos.einstieg / pos.risiko
    return Trade(
        coin=coin,
        einstieg_t=pos.einstieg_t,
        einstieg=pos.einstieg,
        stop0=pos.einstieg - pos.risiko,
        risiko=pos.risiko,
        ausstieg_t=ausstieg_t,
        ausstieg=ausstieg,
        grund=grund,
        tage=pos.tage,
        r=(bewertet - pos.einstieg) / pos.risiko - kosten_r,
        pct=(bewertet / pos.einstieg - 1.0) * 100.0 - KOSTEN_PCT,
        scanner_long=pos.scanner_long,
    )


def kerze(
    d: dict[str, list[Any]],
    ind: dict[str, list[float | None]],
    i: int,
    pos: Position | None,
    coin: str,
    scan: dict[datetime, bool] | None = None,
) -> tuple[Position | None, Trade | None]:
    """Eine Tageskerze verarbeiten: erst die offene Position, dann (wenn erlaubt) Einstieg.

    Genau diese Funktion laeuft in der Studie UND im Vorwaertslauf — es gibt nur eine Regel.
    """
    o, lo, c, zt = d["o"], d["l"], d["c"], d["schluss"]
    fertig: Trade | None = None
    if pos is not None:
        pos.tage += 1
        grund = None
        if o[i] <= pos.stop:
            preis, grund = o[i], "stop"
        elif lo[i] <= pos.stop:
            preis, grund = pos.stop, "stop"
        else:
            pos.hoch = max(pos.hoch, c[i])
            a = ind["atr"][i]
            if a is not None:
                pos.stop = max(pos.stop, pos.hoch - TRAIL_ATR * a)
            if pos.tage >= MAX_TAGE:
                preis, grund = c[i], "zeit"
        if grund is not None:
            fertig = _abschluss(coin, pos, zt[i].isoformat(), preis, preis, grund)
            pos = None
            if grund == "zeit":
                # Zeit-Ausstieg zum Schluss dieser Kerze → fruehestens zum naechsten Schluss.
                return None, fertig
    if pos is None and signal(d, ind, i):
        a = ind["atr"][i]
        assert a is not None
        risiko = STOP_ATR * a
        pos = Position(
            einstieg_t=zt[i].isoformat(),
            einstieg=c[i],
            risiko=risiko,
            stop=c[i] - risiko,
            hoch=c[i],
            scanner_long=scan.get(zt[i]) if scan else None,
        )
    return pos, fertig


def simuliere(d: dict[str, list[Any]], coin: str, scan: dict[datetime, bool]) -> list[Trade]:
    ind = indikatoren(d)
    trades: list[Trade] = []
    pos: Position | None = None
    for i in range(ERSTE_KERZE, len(d["c"])):
        pos, fertig = kerze(d, ind, i, pos, coin, scan)
        if fertig is not None:
            trades.append(fertig)
    if pos is not None:
        trades.append(_abschluss(coin, pos, None, None, d["c"][-1], "offen"))
    return trades


def _kennzahlen(werte: list[float]) -> dict[str, Any]:
    n = len(werte)
    if not n:
        return {"n": 0}
    s = sum(werte)
    gew = [v for v in werte if v > 0]
    verl = [v for v in werte if v < 0]
    return {
        "n": n,
        "summe_r": round(s, 2),
        "erwartung_r": round(s / n, 3),
        "trefferquote": round(len(gew) / n, 3),
        "profitfaktor": round(sum(gew) / abs(sum(verl)), 2) if verl else None,
        "schnitt_gewinn_r": round(sum(gew) / len(gew), 2) if gew else None,
    }


def _monats_bootstrap(trades: list[Trade]) -> tuple[float, float]:
    """Ganze Einstiegsmonate ziehen — gleichzeitig laufende Trends sind EIN Beleg, nicht zwanzig."""
    je_monat: dict[str, list[float]] = defaultdict(list)
    for t in trades:
        je_monat[t.einstieg_t[:7]].append(t.r)
    monate = list(je_monat.values())
    if len(monate) < 3:
        return (math.nan, math.nan)
    rnd = random.Random(BOOT_SEED)
    mittel = []
    for _ in range(BOOT_N):
        stich = [monate[rnd.randrange(len(monate))] for _ in monate]
        flach = [v for m in stich for v in m]
        mittel.append(sum(flach) / len(flach))
    mittel.sort()
    return (round(mittel[int(0.05 * BOOT_N)], 3), round(mittel[int(0.95 * BOOT_N)], 3))


def _zeitraum(t: Trade) -> str | None:
    e = datetime.fromisoformat(t.einstieg_t)
    for name, (a, b) in ZEITRAEUME.items():
        if a <= e < b:
            return name
    return None


def _buy_hold(daten: dict[str, dict[str, list[Any]]]) -> dict[str, float]:
    aus = {}
    for name, (a, b) in ZEITRAEUME.items():
        renditen = []
        for d in daten.values():
            zt, c = d["schluss"], d["c"]
            ia = next((k for k, t in enumerate(zt) if t >= a), None)
            ib = max((k for k, t in enumerate(zt) if t < b), default=None)
            if ia is None or ib is None or ib <= ia:
                continue
            renditen.append((c[ib] / c[ia] - 1.0) * 100.0)
        aus[name] = round(sum(renditen) / len(renditen), 1) if renditen else math.nan
    return aus


def _max_gleichzeitig(trades: list[Trade]) -> int:
    ereignisse = []
    for t in trades:
        ereignisse.append((t.einstieg_t, 1))
        ereignisse.append((t.ausstieg_t or "9999", -1))
    ereignisse.sort(key=lambda x: (x[0], x[1]))
    jetzt = spitze = 0
    for _, d in ereignisse:
        jetzt += d
        spitze = max(spitze, jetzt)
    return spitze


def entscheide(ergebnis: dict[str, Any]) -> tuple[str, list[str]]:
    """Die Entscheidungsregel aus dem Dokument, woertlich."""
    g = ergebnis["haupt"]
    gruende = []
    p1 = all((g[z].get("erwartung_r") or -1) > 0 for z in ("VP", "IS", "OOS"))
    gruende.append(
        "1 Erwartung > 0 in VP/IS/OOS: "
        + ", ".join(f"{z} {g[z].get('erwartung_r')}" for z in ("VP", "IS", "OOS"))
        + (" ✓" if p1 else " ✗")
    )
    p2 = g["OOS"].get("n", 0) >= 30
    gruende.append(f"2 OOS-Trades ≥ 30: {g['OOS'].get('n', 0)}" + (" ✓" if p2 else " ✗"))
    unten = ergebnis["oos_bootstrap_90"][0]
    p3 = isinstance(unten, float) and not math.isnan(unten) and unten > 0
    gruende.append(f"3 Monats-Bootstrap OOS 5-%-Quantil > 0: {unten}" + (" ✓" if p3 else " ✗"))
    if p1 and p2 and p3:
        return "A — Alarm freigeben (halbe Positionsgroesse)", gruende
    if p1 and p2:
        return "B — nur anzeigen, kein Handy-Alarm", gruende
    return "C — verwerfen", gruende


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--npz", required=True)
    ap.add_argument("--zeilen", default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    npz = Path(args.npz)
    zeilen = Path(args.zeilen) if args.zeilen else None

    daten = {c: _lade_d1(npz, c) for c in COINS}
    alle: list[Trade] = []
    for coin, d in daten.items():
        alle.extend(simuliere(d, coin, _scanner_long(zeilen, coin)))

    zu: dict[str, list[Trade]] = defaultdict(list)
    offen: dict[str, list[Trade]] = defaultdict(list)
    for t in alle:
        z = _zeitraum(t)
        if z is None:
            continue
        (offen if t.grund == "offen" else zu)[z].append(t)

    ergebnis: dict[str, Any] = {
        "erzeugt": datetime.now(UTC).isoformat(),
        "regel": "docs/TREND-SWING-STUDIE-2026-09.md",
        "haupt": {z: _kennzahlen([t.r for t in zu[z]]) for z in ZEITRAEUME},
        "oos_bootstrap_90": _monats_bootstrap(zu["OOS"]),
        "einordnung": {
            "nur_luecke": {
                z: _kennzahlen([t.r for t in zu[z] if t.scanner_long is False])
                for z in ("IS", "OOS")
            },
            "scanner_hatte_long": {
                z: _kennzahlen([t.r for t in zu[z] if t.scanner_long is True])
                for z in ("IS", "OOS")
            },
            "buy_hold_pct": _buy_hold(daten),
            "trade_pct_schnitt": {
                z: round(sum(t.pct for t in zu[z]) / len(zu[z]), 2) if zu[z] else None
                for z in ZEITRAEUME
            },
            "haltedauer_tage_schnitt": {
                z: round(sum(t.tage for t in zu[z]) / len(zu[z]), 1) if zu[z] else None
                for z in ZEITRAEUME
            },
            "coins_oos_positiv": sum(
                1 for c in COINS if sum(t.r for t in zu["OOS"] if t.coin == c) > 0
            ),
            "coins_oos_mit_trades": len({t.coin for t in zu["OOS"]}),
            "max_gleichzeitig_offen": _max_gleichzeitig(alle),
            "offen_zum_letzten_schluss": {
                z: _kennzahlen([t.r for t in offen[z]]) for z in ZEITRAEUME
            },
            "ausstiegsgruende": {
                z: {g: sum(1 for t in zu[z] if t.grund == g) for g in ("stop", "zeit")}
                for z in ZEITRAEUME
            },
        },
    }
    urteil, gruende = entscheide(ergebnis)
    ergebnis["urteil"] = urteil
    ergebnis["gruende"] = gruende

    print(json.dumps(ergebnis, indent=1, ensure_ascii=False))
    if args.out:
        Path(args.out).write_text(
            json.dumps(
                {**ergebnis, "trades": [asdict(t) for t in alle]}, ensure_ascii=False, indent=1
            ),
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
