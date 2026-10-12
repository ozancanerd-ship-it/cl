#!/usr/bin/env python3
"""Stundenprofil + Spannen-Prognose fuer Gold (PAXG) und Coins — Studie 12.10.2026.

Anlass: Ozan, 12.10. 10:50: "jetzt musst du die analysen verbessern bzw perfektionieren,
nutze dafuer alle ressourcen internet buecher etc". Die Analyse sagte bisher nur, WO der
Kurs steht. Was fehlte: WIE WEIT er in den naechsten Stunden realistisch laufen kann — die
Grundlage fuer Stop-Abstand, Ziel und "lohnt sich das noch heute?".

LITERATUR (festgelegt, bevor gerechnet wurde):
* Andersen & Bollerslev (1997), "Intraday periodicity and volatility persistence in
  financial markets": Volatilitaet folgt einem starken Tagesmuster; fuer Prognosen wird es
  MULTIPLIKATIV herausgerechnet (Rendite / typische Schwankung dieser Tageszeit), die
  Persistenz auf dem bereinigten Rest geschaetzt und das Muster wieder aufgelegt.
* RiskMetrics (J.P. Morgan 1996/2006): EWMA-Varianz als robuste, einfache Prognose.
  Renditen sind fett-schwaenzig -> Bandbreiten nicht aus der Normalverteilung, sondern aus
  empirischen Quantilen (hier: aus IS, geprueft auf OOS).
* Hansen, Kim & Kimbrough (2021), "Periodicity in Cryptocurrency Volatility and Liquidity":
  Krypto-Volatilitaet am hoechsten um 16 UTC, am tiefsten um 5 UTC, am Wochenende niedriger.
* "Gold price dynamics around the clock" (CBS): Gold steigt eher in oestlichen
  Handelsstunden und faellt eher im Rest des Tages; nach Kosten kaum handelbar.

FESTGELEGT: 1-Std.-Kerzen Binance Vision 01.2022–09.2026 (Cache der Daytrade-Studie).
IS 2022–2024, OOS 01.2025–09.2026. Profil = mittlere |Rendite| je Wochenstunde (168 Faecher,
UTC), geglaettet 1/4-1/2-1/4 mit den Nachbarstunden, normiert auf Mittel 1. Coins gepoolt
(jede Coin-Rendite vorher durch die IS-Durchschnittsschwankung des Coins geteilt).
EWMA-lambda aus {0.97, 0.98, 0.99, 0.995, 0.998} nach IS-QLIKE. Horizonte 1/4/24 Std.
Bandbreiten-Faktoren = IS-Quantile 68 % / 95 % von |Rendite / Prognose|; Spannen-Faktoren =
IS-Median und 90-%-Quantil von (Hoch − Tief) / (Kurs · Prognose).
Bestanden, wenn OOS-Abdeckung 68 %-Band in 63–73 % und 95 %-Band in 92–98 % liegt, und das
Profil die QLIKE im OOS gegenueber "ohne Profil" verbessert.

    python3 scripts/stundenprofil_studie.py --cache /tmp/bv1h --out docs/stundenprofil.json
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import statistics
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


import daytrade_studie as st

LAMBDAS = (0.97, 0.98, 0.99, 0.995, 0.998)
HORIZONTE = (1, 4, 24)
SITZUNGEN = (("asien", 0, 7), ("london", 7, 13), ("newyork", 13, 21), ("spaet", 21, 24))


def wochenstunde(t: datetime) -> int:
    return t.weekday() * 24 + t.hour


def renditen(k: list[tuple]) -> list[tuple[datetime, float, float, float, float]]:
    """(t, r, close_vorher, hoch, tief) je Stunde; nur lueckenlose Stundenpaare."""
    out = []
    for a, b in itertools.pairwise(k):
        if (b[0] - a[0]).total_seconds() != 3600 or a[4] <= 0 or b[4] <= 0:
            continue
        out.append((b[0], math.log(b[4] / a[4]), a[4], b[2], b[3]))
    return out


def glaetten(p: list[float]) -> list[float]:
    n = len(p)
    g = [0.25 * p[(i - 1) % n] + 0.5 * p[i] + 0.25 * p[(i + 1) % n] for i in range(n)]
    m = statistics.mean(g)
    return [x / m for x in g]


def profil(reihen: list[list[tuple]]) -> list[float]:
    """Mittlere normierte |Rendite| je Wochenstunde aus IS-Daten mehrerer Reihen."""
    summe, anzahl = [0.0] * 168, [0] * 168
    for rr in reihen:
        isr = [x for x in rr if x[0] < st.OOS]
        if not isr:
            continue
        mittel = statistics.mean(abs(x[1]) for x in isr) or 1e-12
        for t, r, *_ in isr:
            w = wochenstunde(t)
            summe[w] += abs(r) / mittel
            anzahl[w] += 1
    roh = [summe[i] / anzahl[i] if anzahl[i] else 1.0 for i in range(168)]
    return glaetten(roh)


def prognosen(rr: list[tuple], prof: list[float], lam: float) -> list[tuple]:
    """Je Zeitpunkt i: (t, sigma2_z) — EWMA-Varianz der profilbereinigten Rendite, die
    NACH Stunde i fuer die folgenden Stunden gilt (nur Information bis i)."""
    if len(rr) < 300:
        return []
    z2 = [(x[1] / prof[wochenstunde(x[0])]) ** 2 for x in rr]
    s2 = statistics.mean(z2[:200])
    out = []
    for i in range(len(rr)):
        s2 = lam * s2 + (1 - lam) * z2[i]
        out.append(s2)
    return out


def bewerte(rr: list[tuple], prof: list[float], lam: float, h: int) -> list[tuple]:
    """Je Zeitpunkt: (t, R_h, sigma_h, spanne_rel) fuer die naechsten h Stunden."""
    s2 = prognosen(rr, prof, lam)
    out = []
    for i in range(200, len(rr) - h):
        # nur, wenn die naechsten h Stunden lueckenlos folgen
        if (rr[i + h][0] - rr[i][0]).total_seconds() != 3600 * h:
            continue
        summe_p = sum(prof[wochenstunde(rr[i + j][0])] ** 2 for j in range(1, h + 1))
        sig = math.sqrt(s2[i] * summe_p)
        if sig <= 0:
            continue
        r_h = sum(rr[i + j][1] for j in range(1, h + 1))
        kurs = rr[i + 1][2]  # Schluss der Stunde i
        hoch = max(rr[i + j][3] for j in range(1, h + 1))
        tief = min(rr[i + j][4] for j in range(1, h + 1))
        out.append((rr[i][0], r_h, sig, (hoch - tief) / kurs))
    return out


def prognose_jetzt(k: list[tuple], prof: list[float], lam: float, h: int) -> float | None:
    """Sigma (Log-Rendite) fuer die naechsten ``h`` Stunden ab der letzten Kerze —
    genau so, wie die App es live rechnet (Paritaetstest)."""
    from datetime import timedelta

    rr = renditen(k)
    s2 = prognosen(rr, prof, lam)
    if not s2:
        return None
    t = rr[-1][0]
    summe = sum(prof[wochenstunde(t + timedelta(hours=j))] ** 2 for j in range(1, h + 1))
    return math.sqrt(s2[-1] * summe)


def qlike(werte: list[tuple]) -> float:
    return statistics.mean(math.log(s * s) + (r * r) / (s * s) for _, r, s, _ in werte)


def quantil(v: list[float], q: float) -> float:
    v = sorted(v)
    i = q * (len(v) - 1)
    lo, hi = math.floor(i), math.ceil(i)
    return v[lo] + (v[hi] - v[lo]) * (i - lo)


def gruppe(name: str, reihen: list[list[tuple]]) -> dict:
    prof = profil(reihen)
    flach = [1.0] * 168

    # lambda nach IS-QLIKE (24 Std., Hauptanwendung)
    def q_is(lam, p):
        w = [x for rr in reihen for x in bewerte(rr, p, lam, 24) if x[0] < st.OOS]
        return qlike(w)

    lam = min(LAMBDAS, key=lambda lv: q_is(lv, prof))
    erg = {"lambda": lam, "horizonte": {}}
    for h in HORIZONTE:
        alle = [x for rr in reihen for x in bewerte(rr, prof, lam, h)]
        ohne = [x for rr in reihen for x in bewerte(rr, flach, lam, h)]
        is_, oos = [x for x in alle if x[0] < st.OOS], [x for x in alle if x[0] >= st.OOS]
        is0, oos0 = [x for x in ohne if x[0] < st.OOS], [x for x in ohne if x[0] >= st.OOS]
        u_is = [abs(r / s) for _, r, s, _ in is_]
        q68, q95 = quantil(u_is, 0.68), quantil(u_is, 0.95)
        sp_is = [sp / s for _, _, s, sp in is_]
        sp50, sp90 = quantil(sp_is, 0.5), quantil(sp_is, 0.9)
        u_oos = [abs(r / s) for _, r, s, _ in oos]
        sp_oos = [sp / s for _, _, s, sp in oos]
        erg["horizonte"][str(h)] = {
            "faktor68": round(q68, 4),
            "faktor95": round(q95, 4),
            "spanne50": round(sp50, 4),
            "spanne90": round(sp90, 4),
            "n_is": len(is_),
            "n_oos": len(oos),
            "abdeckung68_oos": round(sum(u <= q68 for u in u_oos) / len(u_oos), 4),
            "abdeckung95_oos": round(sum(u <= q95 for u in u_oos) / len(u_oos), 4),
            "spanne_unter50_oos": round(sum(s <= sp50 for s in sp_oos) / len(sp_oos), 4),
            "spanne_unter90_oos": round(sum(s <= sp90 for s in sp_oos) / len(sp_oos), 4),
            "qlike_is": round(qlike(is_), 4),
            "qlike_is_ohne_profil": round(qlike(is0), 4),
            "qlike_oos": round(qlike(oos), 4),
            "qlike_oos_ohne_profil": round(qlike(oos0), 4),
            "abdeckung95_oos_normal": round(sum(u <= 1.96 for u in u_oos) / len(u_oos), 4),
        }
        hh = erg["horizonte"][str(h)]
        # je Horizont: haelt das Band im OOS, was es verspricht? (Kriterium wie oben)
        hh["gueltig"] = bool(
            0.63 <= hh["abdeckung68_oos"] <= 0.73 and 0.92 <= hh["abdeckung95_oos"] <= 0.98
        )
    # Richtung je Sitzung: je KALENDERTAG eine Zahl (bei Coins der Schnitt ueber alle Coins).
    # Coin-Tage einzeln zu zaehlen wuerde 24 stark gleichlaufende Reihen als unabhaengig
    # behandeln und die t-Werte um ein Vielfaches aufblaehen.
    sitz = {}
    for sname, a, b in SITZUNGEN:
        for teil, f in (("is", lambda t: t < st.OOS), ("oos", lambda t: t >= st.OOS)):
            je_tag: dict = {}
            for rr in reihen:
                summe: dict = {}
                for t, r, *_ in rr:
                    if f(t) and a <= t.hour < b:
                        summe[t.date()] = summe.get(t.date(), 0.0) + r
                for tag, v in summe.items():
                    je_tag.setdefault(tag, []).append(v)
            v = [statistics.mean(x) for x in je_tag.values()]
            m = statistics.mean(v)
            sd = statistics.pstdev(v)
            sitz.setdefault(sname, {})[teil] = {
                "mittel_bp": round(m * 1e4, 2),
                "t": round(m / (sd / math.sqrt(len(v))), 2) if sd else None,
                "anteil_plus": round(sum(x > 0 for x in v) / len(v), 3),
                "n": len(v),
            }
    erg["sitzungen"] = sitz
    erg["profil"] = [round(x, 4) for x in prof]
    # Bestanden?
    h24 = erg["horizonte"]["24"]
    erg["bestanden"] = bool(
        0.63 <= h24["abdeckung68_oos"] <= 0.73
        and 0.92 <= h24["abdeckung95_oos"] <= 0.98
        and h24["qlike_oos"] < h24["qlike_oos_ohne_profil"]
    )
    print(name, json.dumps({k: v for k, v in erg.items() if k != "profil"}, indent=1))
    return erg


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cache", default="/tmp/bv1h")
    ap.add_argument("--out", default="docs/stundenprofil.json")
    args = ap.parse_args()
    cache = Path(args.cache)
    gold = renditen(st.lade(st.GOLD, cache))
    coins = [renditen(st.lade(s, cache)) for s in st.COINS]
    out = {
        "erzeugt": datetime.now(UTC).isoformat(),
        "daten": "Binance Vision 1 Std., 01.2022–09.2026; IS 2022–2024, OOS 01.2025–09.2026",
        "methode": "Profil je Wochenstunde (UTC) multiplikativ (Andersen & Bollerslev 1997), "
        "EWMA auf bereinigten Renditen (RiskMetrics), Bandbreiten aus IS-Quantilen",
        "doc": "docs/STUNDENPROFIL-STUDIE-2026-10.md",
        "gold": gruppe("gold", [gold]),
        "coins": gruppe("coins", coins),
    }
    Path(args.out).write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
