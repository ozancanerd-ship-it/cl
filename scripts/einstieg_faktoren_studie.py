#!/usr/bin/env python3
"""Welche Merkmale beim Einstieg trennen gute von schlechten Trades? — vorab festgelegt.

Vorab-Regel (vor dem Lauf, docs/EINSTIEG-FAKTOREN-STUDIE-2026-10.md):
  Fall = Wache mit Einstieg, Ergebnis = R nach Plan (X0, Kosten 0,5 %) aus signal_replay.
  IS = Aufnahme bis 2025-12-31, OOS ab 2026-01-01.
  Feste Merkmale: Note, Score, CRV, relative Staerke, Umsatz (je Terzile nach IS), BTC ueber EMA50,
  BTC-EMA50 steigend, Eigen ueber EMA50, Eigen EMA20>50, Richtung, Einstiegsart.
  Ein Merkmal TRENNT, wenn die bessere Gruppe in IS UND OOS im Mittel besser ist als die
  schlechtere, jede Gruppe in OOS n >= 40 hat und im Monats-Bootstrap (OOS, 5000, Seed 7) das
  5-%-Quantil des Unterschieds ueber null liegt.  Bei 11 Merkmalen wird ein einzelner Treffer
  trotzdem nur als Hinweis gelesen (Mehrfachtest), nicht als Beleg, wenn er allein steht.
"""
from __future__ import annotations

import importlib.util
import json
import random
import statistics
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
spec = importlib.util.spec_from_file_location("signal_replay", ROOT / "scripts" / "signal_replay.py")
sr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sr)

OOS = "2026-01-01"


def faelle(lauf):
    w = json.loads((Path(lauf) / "wachen.json").read_text())
    out = []
    for x in w:
        if x.get("einstiegskurs") is None:
            continue
        r = sr._r_trade(x, "plan")
        if r is None:
            continue
        u = x.get("umstaende") or {}
        out.append({
            "r": r, "t": x["aufgenommen"], "monat": x["aufgenommen"][:7], "note": x.get("note"),
            "score": x.get("score"), "rr": x.get("rr"), "rs": x.get("rs"), "umsatz": x.get("umsatz_24h"),
            "btc_ema50": (u.get("btc") or {}).get("ueber_ema50"),
            "btc_steigt": (u.get("btc") or {}).get("ema50_steigt"),
            "eig_ema50": (u.get("eigen") or {}).get("ueber_ema50"),
            "eig_20_50": (u.get("eigen") or {}).get("ema20_ueber_50"),
            "richtung": x.get("richtung"), "art": x.get("einstieg_art"),
        })
    return out


def terzile(werte):
    v = sorted(w for w in werte if w is not None)
    return v[len(v) // 3], v[2 * len(v) // 3]


def boot(a, b, n=5000, seed=7):
    """5-%-Quantil des Unterschieds mean(a) - mean(b) mit Ziehen ganzer Monate."""
    ma, mb = defaultdict(list), defaultdict(list)
    for m, r in a: ma[m].append(r)
    for m, r in b: mb[m].append(r)
    monate = sorted(set(ma) | set(mb))
    if len(monate) < 3: return None
    rnd = random.Random(seed); d = []
    for _ in range(n):
        ziehen = [rnd.choice(monate) for _ in monate]
        xa = [r for m in ziehen for r in ma.get(m, [])]; xb = [r for m in ziehen for r in mb.get(m, [])]
        if xa and xb: d.append(statistics.fmean(xa) - statistics.fmean(xb))
    d.sort(); return round(d[int(0.05 * len(d))], 3) if d else None


def main():
    lauf = sys.argv[1] if len(sys.argv) > 1 else "/root/replay/lauf"
    F = faelle(lauf)
    IS = [f for f in F if f["t"][:10] < OOS]
    print(f"{len(F)} Faelle (IS {len(IS)}, OOS {len(F) - len(IS)}); Plan gesamt E={statistics.fmean(f['r'] for f in F):+.3f}")
    gruppen = []  # (merkmal, gute_fn, schlechte_fn)
    for k in ("score", "rr", "rs", "umsatz"):
        lo, hi = terzile([f[k] for f in IS])
        gruppen.append((f"{k} oben vs. unten", lambda f, k=k, hi=hi: f[k] is not None and f[k] > hi,
                        lambda f, k=k, lo=lo: f[k] is not None and f[k] <= lo))
    for k in ("btc_ema50", "btc_steigt", "eig_ema50", "eig_20_50"):
        gruppen.append((f"{k} ja vs. nein", lambda f, k=k: f[k] is True, lambda f, k=k: f[k] is False))
    gruppen.append(("Note A- vs. B+/B", lambda f: f["note"] in ("A-", "A−", "A", "A+"), lambda f: f["note"] in ("B+", "B")))
    gruppen.append(("Richtung long vs. short", lambda f: f["richtung"] == "long", lambda f: f["richtung"] == "short"))
    arten = defaultdict(int)
    for f in F: arten[f["art"]] += 1
    if arten:
        top = max(arten, key=arten.get)
        gruppen.append((f"Einstiegsart '{top}' vs. andere", lambda f, t=top: f["art"] == t, lambda f, t=top: f["art"] != t))
    ergebnis = {}
    for name, gut, schlecht in gruppen:
        zeile = {}
        for per, sel in (("IS", lambda f: f["t"][:10] < OOS), ("OOS", lambda f: f["t"][:10] >= OOS)):
            g = [f["r"] for f in F if sel(f) and gut(f)]; s = [f["r"] for f in F if sel(f) and schlecht(f)]
            zeile[per] = {"n_gut": len(g), "n_schlecht": len(s),
                          "E_gut": round(statistics.fmean(g), 3) if g else None,
                          "E_schlecht": round(statistics.fmean(s), 3) if s else None}
        a = [(f["monat"], f["r"]) for f in F if f["t"][:10] >= OOS and gut(f)]
        b = [(f["monat"], f["r"]) for f in F if f["t"][:10] >= OOS and schlecht(f)]
        zeile["boot5"] = boot(a, b)
        i, o = zeile["IS"], zeile["OOS"]
        ok = (None not in (i["E_gut"], i["E_schlecht"], o["E_gut"], o["E_schlecht"])
              and i["E_gut"] > i["E_schlecht"] and o["E_gut"] > o["E_schlecht"]
              and o["n_gut"] >= 40 and o["n_schlecht"] >= 40 and zeile["boot5"] is not None and zeile["boot5"] > 0)
        zeile["trennt"] = bool(ok)
        ergebnis[name] = zeile
        print(f"{name:<34} IS {i['E_gut']}/{i['E_schlecht']} (n {i['n_gut']}/{i['n_schlecht']})  OOS {o['E_gut']}/{o['E_schlecht']} (n {o['n_gut']}/{o['n_schlecht']})  boot5={zeile['boot5']}  {'TRENNT' if ok else ''}")
    if len(sys.argv) > 2:
        Path(sys.argv[2]).write_text(json.dumps(ergebnis, indent=1, ensure_ascii=False))


main()
