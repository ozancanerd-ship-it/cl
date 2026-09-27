#!/usr/bin/env python3
"""Signal-Replay — der echte Scanner und die echte Wachliste ueber Monate Historie.

    python3 scripts/signal_replay.py bewerten   --npz DIR --out DIR   # Phase A (teuer)
    python3 scripts/signal_replay.py wachliste  --npz DIR --out DIR   # Phase B
    python3 scripts/signal_replay.py auswerten  --out DIR             # Phase C

Warum es das braucht: die eigene Bilanz (Wachliste, seit 13.09.) ist ein Vorlauf aus
wenigen Wochen und einem einzigen Marktabschnitt. Um zu entscheiden, ob eine Regel die
Signale besser macht, braucht es mehr Faelle und mehrere Marktphasen — ohne dabei einen
anderen Scanner zu testen als den, der live laeuft. Deshalb ruft dieses Skript genau
``bewerte_chart`` und genau die Klasse ``Wachliste`` auf, mit denselben Kerzenfenstern.

Die Studie und ihre Entscheidungsregeln stehen VORAB in docs/SIGNAL-STUDIE-2026-09.md.

**Phase A** bewertet jeden Coin alle ``--takt`` Stunden (Punkt-in-Zeit: nur Kerzen, die
zu diesem Zeitpunkt geschlossen waren) und schreibt je Coin eine JSONL-Datei mit den
Scan-Zeilen. Das ist der teure Teil (rund 0,3 s je Bewertung) und laeuft parallel.

**Phase B** spielt die Zeit stuendlich ab: zu den Bewertungszeiten relative Staerke,
Invalidierung und Aufnahme wie in ``watch_levels.py --vollstaendig``; dazwischen die
Pruefung gegen Hoch/Tief der M15-Kerzen der letzten Stunde samt Einstiegsbestaetigung.
Jede Wache wird mit ihrem Endzustand und den Marktumstaenden bei der Aufnahme
(Bitcoin-Lage, eigener Trend, relative Staerke) festgehalten.

**Phase C** rechnet das Ergebnis je Trade in R (Plan: Drittel an jedem Ziel, Schutz-Stop,
Ausstieg zum Kurs; Kosten abgezogen) und vergleicht die vorab festgelegten Filter.

Kerzen kommen als npz je Coin und Zeitebene (``{COIN}_{5m,15m,1h,4h,1d}.npz`` mit den
Feldern t(ms), o, h, l, c, v, q, n) — so, wie ``data.binance.vision`` sie liefert.
"""

from __future__ import annotations

import argparse
import bisect
import json
import math
import os
import random
import sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trading_agent.core.enums import Timeframe

TF_DATEI = {
    Timeframe.M5: "5m",
    Timeframe.M15: "15m",
    Timeframe.H1: "1h",
    Timeframe.H4: "4h",
    Timeframe.D1: "1d",
}
TF_DAUER = {
    Timeframe.M5: timedelta(minutes=5),
    Timeframe.M15: timedelta(minutes=15),
    Timeframe.H1: timedelta(hours=1),
    Timeframe.H4: timedelta(hours=4),
    Timeframe.D1: timedelta(days=1),
}
#: Wie live bei Kraken: hoechstens 720 Kerzen je Abfrage, dazu die Fenster aus scan_runner.
MAX_KERZEN = 720
FENSTER = {
    Timeframe.M5: timedelta(days=4),
    Timeframe.M15: timedelta(days=8),
    Timeframe.H1: timedelta(days=20),
    Timeframe.H4: timedelta(days=45),
    Timeframe.D1: timedelta(days=400),
}
KOSTEN_PCT = 0.5  # Hin- und Rueckweg in Prozent vom Einsatz


def _zeit(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000.0, tz=UTC)


class Reihe:
    """Kerzen einer Zeitebene, einmal gebaut, danach nur noch geschnitten."""

    def __init__(self, npz: Path, coin: str, tf: Timeframe) -> None:
        import numpy as np

        from trading_agent.core.models import OHLCV

        # Einmal entpacken. ``np.load`` auf eine npz-Datei ist faul: jeder Zugriff ueber den
        # Schluessel entpackt das ganze Feld neu — bei 170 000 Kerzen und acht Feldern je
        # Kerze sind das Minuten statt Sekunden.
        with np.load(npz) as roh:
            d = {k: roh[k].tolist() for k in roh.files}
        dauer = TF_DAUER[tf]
        self.bars: list[Any] = []
        self.schluss: list[datetime] = []
        name = f"{coin}USD"
        for i in range(len(d["t"])):
            ot = _zeit(int(d["t"][i]))
            ct = ot + dauer
            o, h, lo, c = (float(d[k][i]) for k in ("o", "h", "l", "c"))
            self.bars.append(
                OHLCV.model_construct(
                    instrument=name,
                    timeframe=tf,
                    open_time=ot,
                    close_time=ct,
                    open=o,
                    high=max(h, o, c),
                    low=min(lo, o, c),
                    close=c,
                    volume=float(d["v"][i]),
                    quote_volume=float(d["q"][i]),
                    trades=int(d["n"][i]),
                    source="binance-vision",
                    ingested_at=None,
                )
            )
            self.schluss.append(ct)

    def bis(self, t: datetime, fenster: timedelta | None = None, n: int | None = None) -> list[Any]:
        """Alle Kerzen, die zum Zeitpunkt ``t`` geschlossen waren (Punkt-in-Zeit)."""
        j = bisect.bisect_right(self.schluss, t)
        i = 0
        if fenster is not None:
            i = bisect.bisect_left(self.schluss, t - fenster)
        if n is not None:
            i = max(i, j - n)
        return self.bars[i:j]

    def zwischen(self, a: datetime, b: datetime) -> list[Any]:
        """Kerzen mit Schluss in (a, b]."""
        i = bisect.bisect_right(self.schluss, a)
        j = bisect.bisect_right(self.schluss, b)
        return self.bars[i:j]


def _takte(start: datetime, ende: datetime, stunden: int) -> list[datetime]:
    t = start
    aus = []
    while t <= ende:
        aus.append(t)
        t += timedelta(hours=stunden)
    return aus


# =================================================================== Phase A
def _bewerte_coin(args: tuple[str, str, str, str, str, int]) -> str:
    coin, npz_dir, out_dir, start, ende, takt = args
    from trading_agent.analysis.mtf import build_mtf_context
    from trading_agent.core.enums import AssetClass
    from trading_agent.scanner.chart_score import bewerte_chart
    from trading_agent.scanner.grading import Profil
    from trading_agent.utils.logging import configure_logging

    configure_logging("ERROR")
    reihen = {tf: Reihe(Path(npz_dir) / f"{coin}_{TF_DATEI[tf]}.npz", coin, tf) for tf in TF_DATEI}
    ziel = Path(out_dir) / f"{coin}.jsonl"
    name = f"{coin}USD"
    n = 0
    with ziel.open("w", encoding="utf-8") as fh:
        for t in _takte(datetime.fromisoformat(start), datetime.fromisoformat(ende), takt):
            m5 = reihen[Timeframe.M5].bis(t, FENSTER[Timeframe.M5], MAX_KERZEN)
            if len(m5) < 200:
                continue
            hoeher = {
                tf: reihen[tf].bis(t, FENSTER[tf], MAX_KERZEN)
                for tf in (Timeframe.M15, Timeframe.H1, Timeframe.H4, Timeframe.D1)
            }
            if len(hoeher[Timeframe.D1]) < 60:
                continue
            letzte = m5[-288:]
            zusatz = {
                "umsatz_24h": sum(float(b.quote_volume or 0) for b in letzte),
                "bewegung_24h_pct": (letzte[-1].close / letzte[0].open - 1) * 100,
                "spanne_24h_pct": (max(b.high for b in letzte) / min(b.low for b in letzte) - 1)
                * 100,
                "trades_24h": sum(int(b.trades or 0) for b in letzte),
            }
            try:
                mtf = build_mtf_context(
                    m5,
                    instrument=name,
                    asset_class=AssetClass.CRYPTO,
                    now=m5[-1].close_time,
                    native_higher=hoeher,
                )
                c = bewerte_chart(name, mtf, m5[-1].close, zusatz=zusatz, profil=Profil.AGGRESSIV)
            except Exception as exc:  # ein kaputter Zeitpunkt kippt nicht die Studie
                fh.write(
                    json.dumps({"t": t.isoformat(), "instrument": name, "fehler": str(exc)}) + "\n"
                )
                continue
            d = c.as_dict()
            d.pop("faktoren", None)
            d["klasse"] = "krypto"
            d["t"] = t.isoformat()
            if not d.get("richtung"):
                # Fuer die relative Staerke braucht jeder Wert seine Renditen — der Rest
                # einer Zeile ohne Richtung wird nicht gebraucht.
                d = {
                    k: d.get(k)
                    for k in (
                        "t",
                        "instrument",
                        "klasse",
                        "richtung",
                        "urteil",
                        "note",
                        "score",
                        "handelbar",
                        "zusatz",
                        "kurs",
                    )
                }
            fh.write(json.dumps(d, ensure_ascii=False, separators=(",", ":")) + "\n")
            n += 1
    return f"{coin}: {n} Bewertungen"


def phase_a(args: argparse.Namespace) -> None:
    out = Path(args.out)
    (out / "zeilen").mkdir(parents=True, exist_ok=True)
    coins = sorted({p.name.split("_")[0] for p in Path(args.npz).glob("*_5m.npz")})
    if args.coins:
        coins = [c for c in coins if c in set(args.coins.split(","))]
    auftraege = [
        (c, args.npz, str(out / "zeilen"), args.start, args.ende, args.takt) for c in coins
    ]
    with ProcessPoolExecutor(max_workers=args.prozesse) as ex:
        for zeile in ex.map(_bewerte_coin, auftraege):
            print(zeile, flush=True)


# =================================================================== Phase B
def _ema(werte: list[float], n: int) -> float | None:
    if len(werte) < n:
        return None
    k = 2.0 / (n + 1)
    e = sum(werte[:n]) / n
    for v in werte[n:]:
        e = v * k + e * (1 - k)
    return e


def _trend(d1: list[Any]) -> dict[str, Any]:
    schluss = [float(b.close) for b in d1[-260:]]
    if len(schluss) < 60:
        return {}
    e20, e50 = _ema(schluss, 20), _ema(schluss, 50)
    e50_alt = _ema(schluss[:-10], 50)
    return {
        "ueber_ema50": schluss[-1] > (e50 or 0),
        "ema20_ueber_50": (e20 or 0) > (e50 or 0),
        "ema50_steigt": (e50 or 0) > (e50_alt or 0),
    }


def phase_b(args: argparse.Namespace) -> None:
    from trading_agent.scanner.relative_strength import anwenden as rs_anwenden
    from trading_agent.scanner.watchlist import ENDZUSTAENDE, Wachliste
    from trading_agent.utils.logging import configure_logging

    configure_logging("ERROR")
    out = Path(args.out)
    je_zeit: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for f in sorted((out / "zeilen").glob("*.jsonl")):
        for zeile in f.read_text(encoding="utf-8").splitlines():
            d = json.loads(zeile)
            if "fehler" not in d:
                je_zeit[d["t"]].append(d)
    coins = sorted({p.name.split(".")[0] for p in (out / "zeilen").glob("*.jsonl")})
    npz = Path(args.npz)
    m15 = {f"{c}USD": Reihe(npz / f"{c}_15m.npz", c, Timeframe.M15) for c in coins}
    d1 = {f"{c}USD": Reihe(npz / f"{c}_1d.npz", c, Timeframe.D1) for c in coins}
    print(f"{len(je_zeit)} Bewertungszeitpunkte, {len(coins)} Coins")

    liste = Wachliste()
    archiv: dict[str, dict[str, Any]] = {}
    umstaende: dict[str, dict[str, Any]] = {}
    start = datetime.fromisoformat(args.start)
    ende = datetime.fromisoformat(args.ende)
    takte = set(je_zeit)
    t = start
    schritt = timedelta(hours=1)
    n_ev = 0
    while t <= ende:
        iso = t.isoformat()
        if iso in takte:
            zeilen = je_zeit[iso]
            rs_anwenden({"krypto": zeilen})
            gerichtet = [z for z in zeilen if z.get("richtung")]
            vorher = set(liste.wachen)
            vorher_auf = {k: w.aufgenommen for k, w in liste.wachen.items()}
            for k, w in liste.wachen.items():
                archiv[f"{k}|{w.aufgenommen}"] = w.as_dict()
            n_ev += len(liste.gegen_scan(gerichtet, jetzt=t))
            n_ev += len(liste.aufnehmen(gerichtet, jetzt=t))
            btc = _trend(d1["BTCUSD"].bis(t)) if "BTCUSD" in d1 else {}
            zeile_je = {z["instrument"]: z for z in gerichtet}
            for k, w in liste.wachen.items():
                if k in vorher and vorher_auf.get(k) == w.aufgenommen:
                    continue
                z = zeile_je.get(k) or {}
                umstaende[f"{k}|{w.aufgenommen}"] = {
                    "btc": btc,
                    "eigen": _trend(d1[k].bis(t)) if k in d1 else {},
                    "rs": z.get("rs"),
                    "headline": z.get("headline"),
                }
        offen = liste.offen
        if offen:
            kurse: dict[str, dict[str, float]] = {}
            kerzen: dict[str, list[Any]] = {}
            for w in offen:
                r = m15.get(w.instrument)
                if r is None:
                    continue
                bars = r.zwischen(t - schritt, t)
                if not bars:
                    continue
                kurse[w.instrument] = {
                    "hoch": max(b.high for b in bars),
                    "tief": min(b.low for b in bars),
                    "letzter": bars[-1].close,
                }
                kerzen[w.instrument] = bars
            n_ev += len(liste.pruefen(kurse, jetzt=t, kerzen=kerzen))
        for k, w in liste.wachen.items():
            if w.zustand in ENDZUSTAENDE or w.einstiegskurs is not None:
                archiv[f"{k}|{w.aufgenommen}"] = w.as_dict()
        t += schritt
    for k, w in liste.wachen.items():
        archiv[f"{k}|{w.aufgenommen}"] = w.as_dict()
    zeilen_aus = []
    for schluessel, w in archiv.items():
        w = dict(w)
        w["umstaende"] = umstaende.get(schluessel, {})
        zeilen_aus.append(w)
    (out / "wachen.json").write_text(json.dumps(zeilen_aus, ensure_ascii=False), encoding="utf-8")
    print(f"{len(zeilen_aus)} Wachen, {n_ev} Ereignisse -> {out / 'wachen.json'}")


# =================================================================== Phase C
def _r_trade(w: dict[str, Any], regel: str) -> float | None:
    """Ergebnis eines eingegangenen, abgeschlossenen Trades in R, nach Kosten."""
    if w.get("einstiegskurs") is None:
        return None
    zustand = str(w.get("zustand"))
    basis = float(w["einstiegskurs"])
    stop = float(w["stop"])
    risiko = abs(float(w["einstieg"]) - stop)
    if risiko <= 0:
        return None
    lang = w.get("richtung") == "long"

    def r_bei(kurs: float) -> float:
        weg = (kurs - basis) if lang else (basis - kurs)
        return weg / risiko

    kosten = (KOSTEN_PCT / 100.0) * basis / risiko
    erreicht = [m for m in ("TP1", "TP2", "TP3") if m in (w.get("erreicht") or [])]
    raus_e = w.get("raus_erreicht")
    if regel != "ganz" and w.get("raus") and isinstance(raus_e, list):
        # Laut Plan draussen: nur die Ziele bis zum Schutz-Stop, Rest am Schutz-Stop.
        # Was die Wache danach noch sieht, hat der Plan nicht mehr mitgemacht.
        erreicht = [m for m in ("TP1", "TP2", "TP3") if m in raus_e]
    ziele = {m: w.get(m.lower()) for m in ("TP1", "TP2", "TP3")}
    if regel == "ganz":
        if zustand == "ziel_erreicht":
            letzte = next(m for m in ("TP3", "TP2", "TP1") if ziele.get(m) is not None)
            return r_bei(float(ziele[letzte])) - kosten
        if zustand == "stop":
            return -1.0 - kosten
        if zustand == "invalidiert" and w.get("ausstiegskurs") is not None:
            return r_bei(float(w["ausstiegskurs"])) - kosten
        return None
    # Plan: je Ziel ein Drittel, Rest am Schutz-Stop / Stop / Ausstieg.
    teile = [m for m in ("TP1", "TP2", "TP3") if ziele.get(m) is not None]
    anteil = 1.0 / len(teile) if teile else 1.0
    gewinn = sum(anteil * r_bei(float(ziele[m])) for m in erreicht)
    rest = 1.0 - anteil * len(erreicht)
    if rest <= 1e-9:
        return gewinn - kosten
    if w.get("raus"):
        schutz = w.get("raus_kurs") if w.get("raus_kurs") is not None else w.get("schutz")
        rest_r = r_bei(float(schutz)) if schutz is not None else 0.0
        return gewinn + rest * rest_r - kosten
    if zustand == "stop":
        return gewinn + rest * -1.0 - kosten
    if zustand == "invalidiert" and w.get("ausstiegskurs") is not None:
        return gewinn + rest * r_bei(float(w["ausstiegskurs"])) - kosten
    if zustand == "ziel_erreicht":
        return gewinn - kosten
    return None  # noch offen oder ohne Ergebnis


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
    }


def _bootstrap(werte: list[float], n: int = 5000, seed: int = 7) -> tuple[float, float]:
    if len(werte) < 5:
        return (math.nan, math.nan)
    rnd = random.Random(seed)
    mittel = []
    for _ in range(n):
        stich = [werte[rnd.randrange(len(werte))] for _ in werte]
        mittel.append(sum(stich) / len(stich))
    mittel.sort()
    return (round(mittel[int(0.05 * n)], 3), round(mittel[int(0.95 * n)], 3))


def phase_c(args: argparse.Namespace) -> dict[str, Any]:
    from trading_agent.scanner import alarm_tor

    out = Path(args.out)
    wachen = json.loads((out / "wachen.json").read_text(encoding="utf-8"))
    grenze = datetime.fromisoformat(args.oos)
    trades = []
    for w in wachen:
        r0 = _r_trade(w, "plan")
        if r0 is None:
            continue
        w["r_plan"] = r0
        w["r_ganz"] = _r_trade(w, "ganz")
        w["oos"] = datetime.fromisoformat(w["aufgenommen"]) >= grenze
        trades.append(w)
    trades.sort(key=lambda w: w["aufgenommen"])

    def tor_fest(w: dict[str, Any]) -> bool:
        return alarm_tor.pruefe(
            note=w.get("note") or "",
            setup=w.get("setup") or "",
            klasse=w.get("klasse") or "",
            richtung=w.get("richtung") or "",
            crv=w.get("rr"),
            ziel1_pct=alarm_tor.ziel1_prozent(w.get("einstieg"), w.get("tp1")),
            stand={},
        ).ja

    # F2: Tor mit Bilanz — nur, was VOR dem Aufnahmezeitpunkt entschieden war.
    tor_bilanz: dict[int, bool] = {}
    for i, w in enumerate(trades):
        frueher = [x for x in trades[:i] if x.get("zuletzt") and x["zuletzt"] < w["aufgenommen"]]
        stand = alarm_tor.bilanz(frueher)
        tor_bilanz[id(w)] = alarm_tor.pruefe(
            note=w.get("note") or "",
            setup=w.get("setup") or "",
            klasse=w.get("klasse") or "",
            richtung=w.get("richtung") or "",
            crv=w.get("rr"),
            ziel1_pct=alarm_tor.ziel1_prozent(w.get("einstieg"), w.get("tp1")),
            stand=stand,
        ).ja

    def lang(w: dict[str, Any]) -> bool:
        return w.get("richtung") == "long"

    def btc_ok(w: dict[str, Any]) -> bool:
        b = (w.get("umstaende") or {}).get("btc") or {}
        if "ueber_ema50" not in b:
            return True
        return b["ueber_ema50"] if lang(w) else not b["ueber_ema50"]

    def eigen_ok(w: dict[str, Any]) -> bool:
        e = (w.get("umstaende") or {}).get("eigen") or {}
        if "ueber_ema50" not in e:
            return True
        if lang(w):
            return e["ueber_ema50"] and e["ema20_ueber_50"]
        return (not e["ueber_ema50"]) and (not e["ema20_ueber_50"])

    def rs_ok(w: dict[str, Any]) -> bool:
        rs = w.get("rs")
        if rs is None:
            rs = (w.get("umstaende") or {}).get("rs")
        if rs is None:
            return True
        return rs >= 50 if lang(w) else rs <= 50

    filter_ = {
        "F0 alle": lambda w: True,
        "F1 Tor fest": tor_fest,
        "F2 Tor + Bilanz": lambda w: tor_fest(w) and tor_bilanz[id(w)],
        "F3 Tor + BTC-Lage": lambda w: tor_fest(w) and btc_ok(w),
        "F4 Tor + eigener Trend": lambda w: tor_fest(w) and eigen_ok(w),
        "F5 Tor + RS": lambda w: tor_fest(w) and rs_ok(w),
        "F6 Tor + nur Long": lambda w: tor_fest(w) and lang(w),
    }
    ergebnis: dict[str, Any] = {"trades": len(trades), "filter": {}}
    for name, f in filter_.items():
        teil = [w for w in trades if f(w)]
        zeile: dict[str, Any] = {}
        for regel, feld in (("X0 Plan", "r_plan"), ("X1 ganz", "r_ganz")):
            werte_is = [w[feld] for w in teil if not w["oos"] and w[feld] is not None]
            werte_oos = [w[feld] for w in teil if w["oos"] and w[feld] is not None]
            zeile[regel] = {
                "IS": _kennzahlen(werte_is),
                "OOS": _kennzahlen(werte_oos),
                "OOS_90": _bootstrap(werte_oos),
            }
        ergebnis["filter"][name] = zeile
    # Je Setup-Art und Richtung (F0), damit man sieht, woher es kommt.
    gruppen: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for w in trades:
        gruppen[f"{w.get('setup') or '(ohne Namen)'} | {w.get('richtung')}"].append(w)
    ergebnis["je_setup"] = {
        k: {
            "IS": _kennzahlen([w["r_plan"] for w in v if not w["oos"]]),
            "OOS": _kennzahlen([w["r_plan"] for w in v if w["oos"]]),
        }
        for k, v in sorted(gruppen.items())
    }
    (out / "ergebnis.json").write_text(
        json.dumps(ergebnis, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return ergebnis


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("phase", choices=("bewerten", "wachliste", "auswerten"))
    ap.add_argument("--npz", default="data/replay/npz")
    ap.add_argument("--out", default="data/replay/lauf")
    ap.add_argument("--start", default="2025-03-01T00:00:00+00:00")
    ap.add_argument("--ende", default="2026-09-20T00:00:00+00:00")
    ap.add_argument("--oos", default="2026-01-01T00:00:00+00:00")
    ap.add_argument("--takt", type=int, default=8, help="Stunden zwischen zwei Bewertungen")
    ap.add_argument("--prozesse", type=int, default=max(1, (os.cpu_count() or 2)))
    ap.add_argument("--coins", default="", help="Kommagetrennt, leer = alle")
    args = ap.parse_args()
    if args.phase == "bewerten":
        phase_a(args)
    elif args.phase == "wachliste":
        phase_b(args)
    else:
        e = phase_c(args)
        for name, z in e["filter"].items():
            for regel, v in z.items():
                i, o = v["IS"], v["OOS"]
                print(
                    f"{name:<24}{regel:<9} IS n={i.get('n', 0):>4} E={i.get('erwartung_r', 0):+.3f}  "
                    f"OOS n={o.get('n', 0):>4} E={o.get('erwartung_r', 0):+.3f}  90%={v['OOS_90']}"
                )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
