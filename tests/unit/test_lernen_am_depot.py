"""``lernKasten`` — Lernen an den eigenen Positionen statt in einer zweiten App.

WARUM ES DIESEN TEST GIBT

Ozan, 22.09.: "bau mir einen lern extra ding ... das ich nebenbei noch lernen kann";
07.10.: Analysen sollen erklaeren, "aus denen er lernen kann, Begriffe erklaert"; und er
will nur EINE App. Die Lektionen stehen deshalb im Portfolio-Reiter und rechnen mit
seinen echten Positionen.

Festgehalten wird:

1. Jede Lektion erscheint nur, wenn sie passt (kein Turbo -> keine Knock-out-Lektion).
2. Die Zahlen stimmen: −20 % brauchen +25 %; Knock-out-Puffer und Hebel aus dem Turbo;
   R aus Einstieg/Stop/Kurs; Klumpen-Anteil aus dem Depotwert.
3. Ohne Stop gibt es die Stop-Lektion statt der R-Lektion.
4. Der Begriff des Tages ist immer da; ein leeres Depot ergibt nichts.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

VORLAGE = Path(__file__).resolve().parents[2] / "site" / "template.html"


def _funktion(roh: str, name: str) -> str:
    start = roh.index(f"function {name}(")
    tiefe, i = 0, roh.index("{", start)
    anfang = i
    while True:
        if roh[i] == "{":
            tiefe += 1
        elif roh[i] == "}":
            tiefe -= 1
            if tiefe == 0:
                break
        i += 1
    return roh[start:anfang] + roh[anfang : i + 1]


def _block(roh: str, name: str) -> str:
    """``const NAME = [ ... ];`` mit verschachtelten Klammern."""
    m = re.search(rf"const {name}\s*=\s*\[", roh)
    assert m
    tiefe, i = 0, m.end() - 1
    while True:
        if roh[i] == "[":
            tiefe += 1
        elif roh[i] == "]":
            tiefe -= 1
            if tiefe == 0:
                break
        i += 1
    return roh[m.start() : i + 1] + ";\n"


def _lauf(bew: list[dict], gesamt: float) -> str:
    node = shutil.which("node")
    if not node:
        pytest.skip("node nicht vorhanden")
    roh = VORLAGE.read_text(encoding="utf-8")
    skript = (
        "const esc = s => String(s == null ? '' : s).replace(/[&<>\"']/g, c => "
        "({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',\"'\":'&#39;'}[c]));\n"
        + _funktion(roh, "digits")
        + _funktion(roh, "num")
        + _funktion(roh, "pct")
        + _funktion(roh, "waehrungsZeichen")
        + "function posName(b){ return b.pos.sym; }\n"
        + _block(roh, "BEGRIFFE")
        + _funktion(roh, "lernKasten")
        + f"const bew = {json.dumps(bew)};\n"
        + f"const stand = {{bew, gesamt: {gesamt}, inEuro: (b, w) => w}};\n"
        + "console.log(JSON.stringify({html: lernKasten(stand)}));\n"
    )
    aus = subprocess.run([node, "-e", skript], capture_output=True, text=True, timeout=20)
    if aus.returncode != 0:
        raise AssertionError(aus.stderr)
    return json.loads(aus.stdout)["html"]


def _pos(sym: str, **kw) -> dict:
    b = {
        "pos": {"sym": sym, "plan": None},
        "w": "USD",
        "wert": 100.0,
        "gvPct": 2.0,
        "r": None,
        "kurs": 10.0,
        "hebel": None,
    }
    b.update(kw)
    return b


def test_leeres_depot_ergibt_nichts():
    assert _lauf([], 0) == ""


def test_nur_passende_lektionen_und_begriff_des_tages():
    html = _lauf([_pos("SOLUSD"), _pos("ETHUSD")], 1000)
    assert "Begriff des Tages" in html
    assert "Knock-out —" not in html
    assert "Verluste schwerer" not in html
    assert "Gewinne sichern" not in html
    assert "Klumpenrisiko" not in html
    assert "Warum ein Stop" in html  # keine Position hat einen Stop


def test_verlust_mathematik_rechnet_richtig():
    html = _lauf([_pos("OPUSD", gvPct=-20.0), _pos("SEIUSD", gvPct=-12.0)], 1000)
    assert "OPUSD" in html.split("Warum Verluste")[1].split("</details>")[0]
    assert "+25,0 %" in html


def test_knockout_mit_puffer_und_hebel():
    turbo = _pos(
        "TURBO-MELI",
        hebel={
            "puffer_pct": 8.0,
            "hebel": 12.5,
            "basisKurs": 2000.0,
            "ko": 1840.0,
            "lang": True,
            "basisName": "Mercado Libre",
            "ausgeknockt": False,
        },
    )
    turbo["pos"]["ko"] = 1840.0
    html = _lauf([turbo], 1000)
    assert "Knock-out — an deinem Turbo auf Mercado Libre" in html
    assert "8,0 %" in html and "12,5×" in html
    assert "Fällt" in html


def test_r_lektion_bei_position_mit_stop():
    b = _pos("NVDA", r=1.5, kurs=130.0)
    b["pos"].update({"einstieg": 100.0, "plan": {"stop": 80.0}})
    html = _lauf([b], 1000)
    assert "In R denken — an NVDA" in html
    assert "+1,50 R" in html
    assert "Warum ein Stop" not in html


def test_klumpen_und_teilverkauf():
    html = _lauf([_pos("PLTR", wert=600.0, gvPct=40.0), _pos("SOLUSD", wert=400.0)], 1000)
    assert "Klumpenrisiko — an PLTR" in html
    assert "60 %" in html and "12,0 %" in html  # 60 % * 20 % Rueckgang
    assert "Gewinne sichern — an PLTR" in html
