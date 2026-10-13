#!/usr/bin/env python3
"""Gewinnschutz-Studie — V0 (bisher) gegen V2 (bisher + Gewinnschutz) und V1 (nur Gewinnschutz).

    python3 scripts/gewinnschutz_replay.py --daten kraken.json [--json OUT]

Regeln, Kennzahlen und Entscheidungsregel stehen VORAB in docs/GEWINNSCHUTZ-STUDIE-2026-10.md.
``kraken.json``: ``{PAAR: {"240": [[t,o,h,l,c,vwap,vol,n], ...], "1440": [...]}}`` (nur geschlossene Kerzen).
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trading_agent.portfolio_intel import gewinnschutz as gs
from trading_agent.portfolio_intel.depot_stops import trend_von

KOSTEN = 0.0025  # je Seite, vom gehandelten Wert
BREAKOUT_N = 30
PAUSE = 6
ATR_N = 14
STOP_ATR = 2.0
H4 = 4 * 3600


@dataclass
class Trade:
    coin: str
    t_ein: int
    ein: float
    stop0: float
    risiko: float
    stop: float
    rest: float = 1.0
    r_real: float = 0.0
    mfe_r: float = 0.0
    t_aus: int = 0
    verkaeufe: list[tuple[int, float, float]] = field(default_factory=list)  # (bar, preis, anteil)
    erledigt: set[str] = field(default_factory=set)
    pending: list[tuple[str, float]] = field(default_factory=list)  # ("verkauf", anteil_des_rests)
    gs_stufe: str = gs.HOLD
    gs_kand: str | None = None
    gs_kand_bar: int = -1
    zyklus: int = 0
    hoch_pct: float = 0.0
    offen: bool = True

    def verkaufe(self, bar: int, preis: float, anteil_des_rests: float, t: int) -> None:
        a = self.rest * anteil_des_rests
        if a <= 0:
            return
        self.r_real += a * ((preis - self.ein) / self.risiko) - a * (KOSTEN * (preis + self.ein) / self.risiko)
        self.verkaeufe.append((bar, preis, a))
        self.rest -= a
        if self.rest <= 1e-9:
            self.rest, self.offen, self.t_aus = 0.0, False, t


def atr(bars: list[list[float]], i: int) -> float | None:
    if i < ATR_N:
        return None
    tr = [max(bars[k][2] - bars[k][3], abs(bars[k][2] - bars[k - 1][4]), abs(bars[k][3] - bars[k - 1][4])) for k in range(i - ATR_N + 1, i + 1)]
    return sum(tr) / ATR_N


def laden(pfad: str) -> dict[str, dict[str, list[list[float]]]]:
    roh = json.loads(Path(pfad).read_text())
    out: dict[str, dict[str, list[list[float]]]] = {}
    for coin, d in roh.items():
        if "240" in d and "1440" in d:
            out[coin] = {k: [[float(x) for x in r[:7]] for r in v] for k, v in d.items()}
    return out


def kerze(r: list[float]) -> gs.Kerze:
    return (r[1], r[2], r[3], r[4], r[6])


def simuliere(coin: str, d: dict[str, list[list[float]]], variante: str) -> list[Trade]:
    h4, d1 = d["240"], d["1440"]
    trades: list[Trade] = []
    offen: Trade | None = None
    pause_bis = -1
    for i in range(BREAKOUT_N + ATR_N + 1, len(h4)):
        t_open = int(h4[i][0])
        o, h, lo, c = h4[i][1], h4[i][2], h4[i][3], h4[i][4]
        t_close = t_open + H4
        # --- offene Position: Ausfuehrung zur Eroeffnung, dann Stop
        if offen is not None:
            tr = offen
            for _kind, anteil in tr.pending:
                tr.verkaufe(i, o, anteil, t_open)
            tr.pending = []
            if tr.offen:
                if o <= tr.stop:
                    tr.verkaufe(i, o, 1.0, t_open)
                elif lo <= tr.stop:
                    tr.verkaufe(i, tr.stop, 1.0, t_open)
            if tr.offen:
                tr.mfe_r = max(tr.mfe_r, (h - tr.ein) / tr.risiko)
                tr.hoch_pct = max(tr.hoch_pct, (h / tr.ein - 1) * 100)
            if not tr.offen:
                pause_bis = i + PAUSE
                offen = None
            else:
                # --- Entscheidung am Schluss dieser Kerze (wirkt ab der naechsten)
                entscheide(tr, variante, h4, d1, i, c, t_close)
        # --- Einstieg: Schluss ueber dem Hoch der letzten 30 Kerzen => naechste Eroeffnung
        if offen is None and i >= pause_bis and i + 1 < len(h4):
            vorher = max(h4[k][2] for k in range(i - BREAKOUT_N, i))
            a = atr(h4, i)
            if a and c > vorher:
                ein = h4[i + 1][1]
                stop = ein - STOP_ATR * a
                if stop > 0 and ein > stop:
                    offen = Trade(coin, int(h4[i + 1][0]), ein, stop, ein - stop, stop)
                    # Kerze i+1 wird im naechsten Schleifendurchlauf behandelt (Einstieg zur Eroeffnung)
                    trades.append(offen)
                    pause_bis = i + 1
    for tr in trades:
        if tr.offen:  # am Ende der Daten zum letzten Schluss glattstellen
            tr.verkaufe(len(h4) - 1, h4[-1][4], 1.0, int(h4[-1][0]) + H4)
    return trades


def entscheide(tr: Trade, variante: str, h4: list[list[float]], d1: list[list[float]], i: int, c: float, t_close: int) -> None:
    risiko = tr.risiko
    gv = (c / tr.ein - 1) * 100
    # V0 / V2: feste Ziele (nur nach bestaetigtem Schluss)
    if variante in ("V0", "V2"):
        for k, name in ((1, "TP1"), (2, "TP2"), (3, "TP3")):
            ziel = tr.ein + k * risiko
            if name in tr.erledigt:
                continue
            if c >= ziel:
                tr.erledigt.add(name)
                tr.pending.append(("verkauf", 1.0 / (4 - k)))  # 1/3 des Anfangs: 1/3, dann 1/2 vom Rest, dann alles
                if name == "TP1":
                    tr.stop = max(tr.stop, tr.ein)
                if name == "TP2":
                    tr.stop = max(tr.stop, tr.ein + risiko)
            break
    if variante == "V0":
        hoch = max(tr.hoch_pct, gv)
        if hoch >= 10 and hoch - gv >= max(8.0, hoch * 0.35) and "RUECK" not in tr.erledigt and tr.rest > 0:
            tr.erledigt.add("RUECK")
            tr.pending.append(("verkauf", 0.5))
        return
    # V1 / V2: Gewinnschutz-Urteil
    lo_i = max(0, i - 149)
    k4 = [kerze(r) for r in h4[lo_i : i + 1]]
    closes4 = [r[4] for r in h4[max(0, i - 259) : i + 1]]
    d1_bis = [r for r in d1 if int(r[0]) + 86400 <= t_close]
    d1c = [r[4] for r in d1_bis]
    k1d = [kerze(r) for r in d1_bis[-150:]]
    trend = {"d1": trend_von(d1c) if len(d1c) >= 210 else None, "h4": trend_von(closes4) if len(closes4) >= 210 else None}
    ziel_next = None
    if variante == "V2":
        for k, name in ((1, "TP1"), (2, "TP2"), (3, "TP3")):
            if name not in tr.erledigt:
                ziel_next = tr.ein + k * risiko
                break
    else:
        ziel_next = None
    bew = gs.bewerte(
        lang=True,
        kurs=c,
        einstieg=tr.ein,
        hoch_gv_pct=tr.hoch_pct,
        menge=tr.rest,
        stop=tr.stop,
        ziel_next=ziel_next,
        kerzen={"h4": k4, "h1": None, "d1": k1d},
        trend=trend,
    )
    if bew.urteil == gs.UNBEWERTET:
        return
    neu, best = bew.urteil, tr.gs_stufe
    if neu == best:
        tr.gs_kand = None
        return
    if tr.gs_kand == neu and i > tr.gs_kand_bar:  # eine weitere abgeschlossene Kerze unveraendert
        tr.gs_stufe, tr.gs_kand = neu, None
        if gs.RANG[neu] > gs.RANG[best]:
            if bew.trail_stop is not None:
                tr.stop = max(tr.stop, bew.trail_stop)
            if neu == gs.PARTIAL and bew.teil_anteil:
                tr.pending.append(("verkauf", bew.teil_anteil))
            elif neu == gs.EXIT:
                tr.pending.append(("verkauf", 1.0))
    else:
        tr.gs_kand, tr.gs_kand_bar = neu, i


# --------------------------------------------------------------------------- Kennzahlen


def kennzahlen(trades: list[Trade], h4_ende: int) -> dict[str, Any]:
    if not trades:
        return {"n": 0}
    rs = [t.r_real for t in trades]
    gew = sum(r for r in rs if r > 0)
    verl = -sum(r for r in rs if r < 0)
    kurve, dd, spitze = 0.0, 0.0, 0.0
    for t in sorted(trades, key=lambda x: x.t_aus):
        kurve += t.r_real
        spitze = max(spitze, kurve)
        dd = max(dd, spitze - kurve)
    mfe = [t for t in trades if t.mfe_r >= 1.0]
    abgegeben = [(t.mfe_r - t.r_real) / t.mfe_r for t in mfe]
    return {
        "n": len(trades),
        "erwartung_R": sum(rs) / len(rs),
        "profit_factor": (gew / verl) if verl > 0 else None,
        "trefferquote": sum(1 for r in rs if r > 0) / len(rs),
        "max_drawdown_R": dd,
        "mfe_mittel_R": sum(t.mfe_r for t in trades) / len(trades),
        "abgegeben_anteil": (sum(abgegeben) / len(abgegeben)) if abgegeben else None,
        "n_mfe_ge_1R": len(mfe),
        "summe_R": sum(rs),
    }


def zu_frueh(trades_alle: dict[str, list[Trade]], daten: dict[str, dict[str, list[list[float]]]]) -> dict[str, Any]:
    """Anteil der Teil-/Vollverkaeufe (vor dem letzten Verkauf bzw. Vollverkauf), nach denen der Kurs bis
    zum Tradeende noch >= 1R hoeher stieg; mittlere verpasste Weiterbewegung in R."""
    n = frueh = 0
    verpasst = 0.0
    for coin, trades in trades_alle.items():
        h4 = daten[coin]["240"]
        for t in trades:
            if not t.verkaeufe:
                continue
            for bar, preis, _a in t.verkaeufe:
                hoch_danach = max((h4[k][2] for k in range(bar, min(len(h4), bar + 30))), default=preis)  # naechste 5 Tage
                mehr = (hoch_danach - preis) / t.risiko
                n += 1
                verpasst += max(mehr, 0.0)
                if mehr >= 1.0:
                    frueh += 1
    return {"verkaeufe": n, "zu_frueh_anteil": (frueh / n) if n else None, "verpasst_mittel_R": (verpasst / n) if n else None}


def bootstrap(diffs: list[tuple[int, float]], n: int = 2000, seed: int = 7) -> tuple[float, float, float] | None:
    if len(diffs) < 8:
        return None
    bloecke: dict[int, list[float]] = {}
    for woche, d in diffs:
        bloecke.setdefault(woche, []).append(d)
    keys = list(bloecke)
    rng = random.Random(seed)
    erg = []
    for _ in range(n):
        s = []
        for _k in keys:
            s += bloecke[rng.choice(keys)]
        erg.append(sum(s) / len(s))
    erg.sort()
    return erg[int(0.05 * n)], erg[n // 2], erg[int(0.95 * n)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--daten", required=True)
    ap.add_argument("--json")
    a = ap.parse_args()
    daten = laden(a.daten)
    ts = sorted(int(r[0]) for d in daten.values() for r in d["240"])
    t0, t1 = ts[0], ts[-1]
    # Handelbare Zeit beginnt nach der Anlaufphase; IS/OOS/Fenster ueber die Einstiegszeiten
    alle: dict[str, dict[str, list[Trade]]] = {v: {} for v in ("V0", "V1", "V2")}
    for coin, d in daten.items():
        for v in alle:
            alle[v][coin] = simuliere(coin, d, v)
    # Nur Einstiege, die in ALLEN Varianten vorkommen (nach einem anderen Ausstieg kann der naechste
    # Einstieg abweichen) — so vergleichen wir dieselben Faelle.
    gemeinsam = {c: set.intersection(*({t.t_ein for t in alle[v][c]} for v in alle)) for c in daten}
    ergebnis_verworfen = {v: sum(len(alle[v][c]) - len(gemeinsam[c]) for c in daten) for v in alle}
    for v in alle:
        for c in daten:
            alle[v][c] = [t for t in alle[v][c] if t.t_ein in gemeinsam[c]]
    ein_zeiten = sorted(t.t_ein for c in alle["V0"].values() for t in c)
    if not ein_zeiten:
        print("keine Trades")
        return 1
    grenze = ein_zeiten[int(len(ein_zeiten) * 0.6)]
    laenge = (ein_zeiten[-1] - ein_zeiten[0]) / 4
    fenster = [ein_zeiten[0] + laenge * k for k in range(5)]

    def teil(v: str, pred) -> list[Trade]:
        return [t for c in alle[v].values() for t in c if pred(t)]

    ergebnis: dict[str, Any] = {"daten": {"coins": len(daten), "von": t0, "bis": t1, "gepaarte_trades": len(ein_zeiten), "verworfen_je_variante": ergebnis_verworfen}, "teilmengen": {}}
    for name, pred in (("alle", lambda t: True), ("IS", lambda t: t.t_ein < grenze), ("OOS", lambda t: t.t_ein >= grenze)):
        ergebnis["teilmengen"][name] = {v: kennzahlen(teil(v, pred), t1) for v in alle}
    ergebnis["zu_frueh"] = {v: zu_frueh(alle[v], daten) for v in alle}
    # Walk-forward: Unterschied V2 - V0 je Fenster (gepaart ueber dieselben Einstiege)
    wf = []
    for k in range(4):
        lo, hi = fenster[k], fenster[k + 1] + (1 if k == 3 else 0)
        d = []
        for coin in daten:
            for t0_, t2_ in zip(alle["V0"][coin], alle["V2"][coin], strict=True):
                if lo <= t0_.t_ein < hi:
                    d.append(t2_.r_real - t0_.r_real)
        wf.append({"fenster": k + 1, "n": len(d), "diff_V2_minus_V0": (sum(d) / len(d)) if d else None})
    ergebnis["walk_forward"] = wf
    diffs = []
    for coin in daten:
        for t0_, t2_ in zip(alle["V0"][coin], alle["V2"][coin], strict=True):
            if t0_.t_ein >= grenze:
                diffs.append((int(t0_.t_ein // (7 * 86400)), t2_.r_real - t0_.r_real))
    ergebnis["bootstrap_OOS_V2_minus_V0"] = bootstrap(diffs)
    ergebnis["diff_OOS_V2_minus_V0"] = (sum(d for _w, d in diffs) / len(diffs)) if diffs else None
    oos0, oos2 = ergebnis["teilmengen"]["OOS"]["V0"], ergebnis["teilmengen"]["OOS"]["V2"]
    bs = ergebnis["bootstrap_OOS_V2_minus_V0"]
    bed = {
        "1_OOS_erwartung_besser": bool(diffs) and ergebnis["diff_OOS_V2_minus_V0"] > 0,
        "2_bootstrap_q05_ueber_0": bool(bs) and bs[0] > 0,
        "3_mind_3_von_4_fenstern": sum(1 for w in wf if w["diff_V2_minus_V0"] is not None and w["diff_V2_minus_V0"] >= 0) >= 3,
        "4_drawdown_nicht_groesser": bool(oos0.get("n")) and oos2.get("max_drawdown_R", 9e9) <= oos0.get("max_drawdown_R", 0),
    }
    ergebnis["entscheidung"] = {"bedingungen": bed, "im_test_besser": all(bed.values())}
    txt = json.dumps(ergebnis, indent=1, default=str)
    print(txt)
    if a.json:
        Path(a.json).write_text(txt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
