"""Labor-Kurve und Aktivitaetsfeed (19.10.): rechnen aus echten Trades, nichts Geschoenes."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

TEMPLATE = (Path(__file__).resolve().parents[2] / "site" / "template.html").read_text(
    encoding="utf-8"
)


def _js(body: str) -> object:
    start = TEMPLATE.index("const R1 = v =>")
    end = TEMPLATE.index("function kurveSvg")
    code = TEMPLATE[start:end] + body
    out = subprocess.run(["node", "-e", code], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def test_equity_summe_drawdown_und_filter() -> None:
    trades = [
        {"beendet": "2026-10-02T10:00:00Z", "r_drittel": 2.0},
        {"beendet": "2026-10-01T10:00:00Z", "r_drittel": -1.0},  # kommt zuerst
        {"beendet": "2026-10-03T10:00:00Z", "r_drittel": -1.5},
        {"beendet": "2026-10-04T10:00:00Z", "r_drittel": 5.0, "gezaehlt": False},  # zaehlt nicht
        {
            "beendet": "2026-10-05T10:00:00Z",
            "r_drittel": 5.0,
            "eingestiegen": False,
        },  # nie ausgeloest
        {"beendet": None, "r_drittel": 9.0},  # noch offen
    ]
    r = _js(f"console.log(JSON.stringify(equityReihe({json.dumps(trades)})));")
    assert r["n"] == 3
    assert r["summe"] == -0.5
    assert [round(p["eq"], 2) for p in r["pts"]] == [-1.0, 1.0, -0.5]
    assert r["maxdd"] == -1.5  # von +1,0 auf -0,5
    assert [p["dd"] for p in r["pts"]] == [-1.0, 0, -1.5]


def test_r_format_und_leere_eingabe() -> None:
    assert _js("console.log(JSON.stringify([R1(1.25), R1(-0.04), R1(0)]));") == [
        "+1,3 R",
        "−0,0 R",
        "0,0 R",
    ]
    assert _js("console.log(JSON.stringify(equityReihe(null)));")["n"] == 0
