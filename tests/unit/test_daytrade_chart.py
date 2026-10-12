"""Day-Trading-Reiter: Chart + Analyse (12.10.2026).

Ozan, 12.10.: "ich kann bei den daytrades vor allem gold nicht den chart und die analyse
sehen". Der Reiter zeichnet jetzt einen Kerzenchart (Kraken live) mit den Marken der Regel
und erklaert darunter, was fuer einen Einstiegs-Alarm fehlt. Festgehalten wird:

1. Die Analyse im Browser rechnet GENAU wie der Alarm in Python (``daytrade_ausbruch.analyse``)
   — sonst zeigt die Seite "Ausbruch", und der Alarm kommt nicht (oder umgekehrt).
2. Gold steht als erster Wert in der Auswahl, mit Pax Gold (PAXG/USD) von Kraken.
3. Die Alarm-Schwelle im Chart ist dieselbe wie im Lohnt-sich-Tor (0,25 ATR).
4. Der Chart hat einen eigenen Container — der 5-Minuten-Neuaufbau zerreisst ihn nicht.
"""

from __future__ import annotations

import json
import math
import random
import re
import shutil
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from trading_agent.strategy.setups import daytrade_ausbruch as m

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


def _konst(roh: str, name: str) -> str:
    mm = re.search(rf"const {name}\s*=", roh)
    assert mm, name
    return roh[mm.start() : roh.index(";", mm.start()) + 1] + "\n"


def _reihe(seed: int, n: int = 400, ausbruch: float | None = None) -> list[tuple]:
    rnd = random.Random(seed)
    t0 = datetime(2026, 9, 1, tzinfo=UTC)
    k, c = [], 100.0
    for i in range(n):
        o = c
        c = o * (1 + rnd.gauss(0.0004, 0.006)) + 0.3 * math.sin(i / 7)
        h = max(o, c) * (1 + abs(rnd.gauss(0, 0.002)))
        lo = min(o, c) * (1 - abs(rnd.gauss(0, 0.002)))
        k.append((t0 + timedelta(hours=i), o, h, lo, c))
    if ausbruch is not None:
        hoch = max(x[2] for x in k[-48:])
        t, o, h, lo, _ = k[-1]
        c = hoch * (1 + ausbruch)
        k.append(
            (
                t + timedelta(hours=1),
                k[-1][4],
                max(c, k[-1][4]) * 1.0005,
                min(c, k[-1][4]) * 0.999,
                c,
            )
        )
    return k


def _js(reihen: list[tuple[list[tuple], bool | None]]) -> list[dict | None]:
    node = shutil.which("node")
    if not node:
        pytest.skip("node nicht vorhanden")
    roh = VORLAGE.read_text(encoding="utf-8")
    eingabe = [
        [[[x[0].timestamp() * 1000, x[1], x[2], x[3], x[4]] for x in k], markt]
        for k, markt in reihen
    ]
    skript = (
        _konst(roh, "DT_LOOKBACK")
        + _funktion(roh, "dtEma")
        + _funktion(roh, "dtAtr")
        + _funktion(roh, "dtAnalyse")
        + f"\nconst E = {json.dumps(eingabe)};\n"
        + "console.log(JSON.stringify(E.map(([k, mo]) => { const a = dtAnalyse(k, mo);"
        " if (a) delete a.emaReihe; return a; })));"
    )
    out = subprocess.run([node, "-e", skript], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


@pytest.mark.parametrize("ausbruch", [None, 0.0002, 0.01])
@pytest.mark.parametrize("markt", [True, False, None])
def test_browser_rechnet_wie_der_alarm(ausbruch, markt) -> None:
    reihen = [(_reihe(s, ausbruch=ausbruch), markt) for s in (1, 2, 3)]
    js = _js(reihen)
    for (k, mo), b in zip(reihen, js, strict=True):
        p = m.analyse(k, markt_ok=mo)
        assert b is not None and p is not None
        for f in (
            "kurs",
            "ema200",
            "marke48",
            "tief24",
            "atr24",
            "stop_bei_signal",
            "abstand_pct",
            "ueberhang_atr",
        ):
            assert b[f] == pytest.approx(p[f], rel=1e-9, abs=1e-9), f
        assert b["signal"] == p["signal"]
        assert b["status"] == p["status"]


def test_zu_kurze_reihe_gibt_keine_analyse() -> None:
    assert _js([(_reihe(1, n=150), True)]) == [None]
    assert m.analyse(_reihe(1, n=150)) is None


def test_gold_zuerst_und_von_kraken() -> None:
    roh = VORLAGE.read_text(encoding="utf-8")
    assert "PAXG: {paar: 'PAXGUSD'" in roh
    assert "const out = ['PAXG'];" in _funktion(roh, "dtWahlListe")
    assert "if (!DTC.sym) DTC.sym = 'PAXG';" in roh
    assert "api.kraken.com/0/public/OHLC?pair=" in _funktion(roh, "dtLaden")


def test_alarm_schwelle_wie_im_tor() -> None:
    roh = VORLAGE.read_text(encoding="utf-8")
    assert f"DT_MIN_UEBERHANG = {m.MIN_UEBERHANG_ATR}" in roh
    assert "an.marke48 + DT_MIN_UEBERHANG * an.atr24" in _funktion(roh, "dtLinien")


def test_chart_hat_eigenen_container_und_wird_beim_tabwechsel_gemessen() -> None:
    roh = VORLAGE.read_text(encoding="utf-8")
    assert '<div id="dtchart"></div>\n    <div id="daytrading"></div>' in roh
    assert "try { dtChartAufbau(); } catch(e){ console.error(e); }" in roh
    assert "if (i===11) requestAnimationFrame(" in roh
