#!/usr/bin/env python3
"""Zuverlaessigkeitstabelle: sagt Score/Note/Vertrauen den Ausgang eines Trades voraus?

Nur Lesen. Gleiche Trades wie die Bilanz, Messartefakte (Sofort-Stops) getrennt. Je Klasse:
n, Treffer, Mittel-R, 95-%-Intervall. n < 30 traegt keine Aussage. Zusaetzlich Rangkorrelation
(Spearman) Score->R: liegt sie nahe 0, ist der Score kein Auswahlkriterium.

Aufruf: python scripts/zuverlaessigkeit.py [--json out.json]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))
import verlustquellen as vq  # noqa: E402


def _rang(v: list[float]) -> list[float]:
    idx = sorted(range(len(v)), key=lambda i: v[i])
    r = [0.0] * len(v)
    i = 0
    while i < len(idx):
        j = i
        while j + 1 < len(idx) and v[idx[j + 1]] == v[idx[i]]:
            j += 1
        for k in range(i, j + 1):
            r[idx[k]] = (i + j) / 2 + 1
        i = j + 1
    return r


def spearman(x: list[float], y: list[float]) -> float | None:
    if len(x) < 3:
        return None
    a, b = _rang(x), _rang(y)
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    num = sum((p - ma) * (q - mb) for p, q in zip(a, b))
    da = sum((p - ma) ** 2 for p in a) ** 0.5
    db = sum((q - mb) ** 2 for q in b) ** 0.5
    return round(num / (da * db), 3) if da and db else None


def _klasse(wert, grenzen: list[float]) -> str:
    if wert is None:
        return "ohne"
    for g in grenzen:
        if wert < g:
            return f"<{g:g}"
    return f">={grenzen[-1]:g}"


def analyse(zeilen: list[dict]) -> dict:
    echt = [z for z in zeilen if not vq.ist_sofort(z)]
    out: dict = {"alle": vq.kz(zeilen), "ohne_sofortstops": vq.kz(echt), "tabellen": {}, "spearman": {}}
    out["tabellen"]["score"] = vq.gruppiere(echt, lambda z: _klasse(z.get("score"), [40, 50, 60, 70]))
    out["tabellen"]["note"] = vq.gruppiere(echt, lambda z: str(z.get("note")))
    paare = [(z["score"], z["r"]) for z in echt if z.get("score") is not None and z.get("r") is not None]
    out["spearman"]["score"] = spearman([p[0] for p in paare], [p[1] for p in paare])
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json")
    a = ap.parse_args()
    res = analyse(vq.ta.laden())
    print("ALLE:", res["alle"])
    print("OHNE SOFORT-STOPS:", res["ohne_sofortstops"])
    for k, t in res["tabellen"].items():
        print(f"\n{k} (Spearman {res['spearman'].get(k)}):")
        for g, v in sorted(t.items()):
            print(" ", g, v)
    if a.json:
        Path(a.json).write_text(json.dumps(res, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
