#!/usr/bin/env python3
"""Halte-Stop-Studie — vorab registriert in docs/HALTE-STOP-STUDIE-2026-09.md.

Daten: Tageskerzen je Coin als npz (wie fuer signal_replay.py, Felder t,o,h,l,c) unter
``$BT/npz/<COIN>_1d.npz`` und Aktien-Schlusskurse als ``$BT/aktien_d1.json``
({SYMBOL: [[datum, schluss], ...]}). ``BT`` per Umgebungsvariable, Standard /tmp/claude-0/bt."""
import glob, json, os
import numpy as np

BT = os.environ.get("BT", "/tmp/claude-0/bt")

KS = [2.0, 2.5, 3.0, 3.5, 4.0, 5.0]
H = 90
SCHRITT = 5
N_ATR = 14
N_HOCH = 22


def wilder_atr(h, l, c, n=N_ATR):
    tr = np.empty(len(c))
    tr[0] = h[0] - l[0]
    tr[1:] = np.maximum(h[1:] - l[1:], np.maximum(abs(h[1:] - c[:-1]), abs(l[1:] - c[:-1])))
    atr = np.full(len(c), np.nan)
    if len(c) <= n:
        return atr
    atr[n] = tr[1:n + 1].mean()
    for i in range(n + 1, len(c)):
        atr[i] = (atr[i - 1] * (n - 1) + tr[i]) / n
    return atr


def simuliere(o, h, l, c, atr, start, k, kosten, nur_schluss):
    """Rendite nach H Tagen (Geld nach Ausstieg in bar) und Ausstiegsinfo."""
    ein = c[start]
    ende = min(start + H, len(c) - 1)
    if k is None:
        return c[ende] / ein - 1, None
    hoch = max(h[max(0, start - N_HOCH + 1):start + 1])
    stop = hoch - k * atr[start]
    for i in range(start + 1, ende + 1):
        # Pruefen gegen den Stop von gestern (am Morgen bekannt)
        if nur_schluss:
            if c[i] <= stop:
                return c[i] / ein - 1 - kosten, i
        else:
            if l[i] <= stop:
                fill = o[i] if o[i] < stop else stop
                return fill / ein - 1 - kosten, i
        # nachziehen (nach Schluss des Tages)
        hoch = max(h[max(0, i - N_HOCH + 1):i + 1])
        neu = hoch - k * atr[i]
        if neu > stop:
            stop = neu
    return c[ende] / ein - 1, None


def lauf(reihen, kosten, nur_schluss):
    """reihen: name -> (o,h,l,c). Ergebnis je k je Haelfte."""
    faelle = []  # (tagindex_global_rel, name, start)
    for name, (o, h, l, c) in reihen.items():
        atr = wilder_atr(h, l, c)
        for s in range(max(N_ATR + 1, N_HOCH), len(c) - H, SCHRITT):
            if np.isnan(atr[s]):
                continue
            faelle.append((s / (len(c) - H), name, s, atr))
    faelle.sort(key=lambda x: x[0])
    ergebnis = {}
    for k in [None] + KS:
        haelften = {"IS": [], "OOS": []}
        for rel, name, s, atr in faelle:
            o, h, l, c = reihen[name]
            r, aus = simuliere(o, h, l, c, atr, s, k, kosten, nur_schluss)
            frueh = False
            if aus is not None and aus + 20 < len(c):
                exit_kurs = c[aus]
                frueh = c[aus + 20] > exit_kurs * 1.10
            haelften["IS" if rel < 0.5 else "OOS"].append((r, aus is not None, frueh))
        ergebnis["kein" if k is None else k] = {
            hf: {
                "n": len(v),
                "mittel": float(np.mean([x[0] for x in v]) * 100),
                "median": float(np.median([x[0] for x in v]) * 100),
                "q05": float(np.quantile([x[0] for x in v], 0.05) * 100),
                "ausgestoppt": float(np.mean([x[1] for x in v]) * 100),
                "zu_frueh": float(np.mean([x[2] for x in v]) * 100),
            }
            for hf, v in haelften.items()
        }
    return ergebnis


def coins():
    reihen = {}
    for p in sorted(glob.glob(BT + "/npz/*_1d.npz")):
        d = np.load(p)
        reihen[os.path.basename(p).split("_")[0]] = (d["o"], d["h"], d["l"], d["c"])
    return reihen


def aktien():
    d = json.load(open(BT + "/aktien_d1.json"))
    reihen = {}
    for name, liste in d.items():
        c = np.array([x[1] for x in liste], dtype=float)
        if len(c) < 200:
            continue
        reihen[name] = (c, c, c, c)
    return reihen


def entscheide(erg):
    kein = erg["kein"]
    zul = []
    for k in KS:
        e = erg[k]
        if all(e[hf]["mittel"] >= kein[hf]["mittel"] - 1.0 for hf in ("IS", "OOS")):
            zul.append(k)
    if not zul:
        return max(KS), "kein k zulaessig"
    q = {k: (erg[k]["IS"]["q05"] + erg[k]["OOS"]["q05"]) / 2 for k in zul}
    best = max(q.values())
    kand = [k for k in zul if q[k] >= best - 0.5]
    return max(kand), f"zulaessig {zul}, q05 {({k: round(v, 1) for k, v in q.items()})}"


if __name__ == "__main__":
    aus = {}
    for name, reihen, kosten, ns in (("coins", coins(), 0.002, False), ("aktien", aktien(), 0.001, True)):
        erg = lauf(reihen, kosten, ns)
        aus[name] = {str(k): v for k, v in erg.items()}
        print(f"\n=== {name} ({len(reihen)} Werte) ===")
        print(f"{'Regel':>7} | {'IS mittel':>9} {'q05':>7} {'stop%':>6} {'frueh%':>6} | {'OOS mittel':>10} {'q05':>7} {'stop%':>6} {'frueh%':>6}")
        for k, v in erg.items():
            a, b = v["IS"], v["OOS"]
            print(f"{str(k):>7} | {a['mittel']:9.1f} {a['q05']:7.1f} {a['ausgestoppt']:6.0f} {a['zu_frueh']:6.0f} | {b['mittel']:10.1f} {b['q05']:7.1f} {b['ausgestoppt']:6.0f} {b['zu_frueh']:6.0f}   (n {a['n']}/{b['n']})")
        k, grund = entscheide(erg)
        aus[name]["entscheidung"] = {"k": k, "grund": grund}
        print("Entscheidung:", k, grund)
    json.dump(aus, open(BT + "/halte_stop_ergebnis.json", "w"), indent=1)
