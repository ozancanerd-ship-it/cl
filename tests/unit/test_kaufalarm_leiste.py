"""Kaufalarm-Leiste: heutige Alarme oben, Tagesdeckel sichtbar, „Noch einsteigen bis".

WARUM ES DIESEN TEST GIBT

Ozan, 02.10., 20:00: „Unsere App hat nicht einen einzigen Buy-Signal heute angezeigt,
das geht schon fünf Tage so." Am selben Tag waren drei Kaufalarme rausgegangen (Zcash,
Gala, Dash) — die App zeigte sie aber erst weit unten; oben stand „Gerade kein Einstieg
erreicht". Dieser Test hält fest, dass die Leiste auf der Startseite UND unter Alarme
steht und dass die Grenze für „Noch einsteigen" richtig gerechnet wird.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

TEMPLATE = Path(__file__).resolve().parents[2] / "site" / "template.html"


def _quelle() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


def test_leiste_steht_auf_startseite_und_unter_alarme() -> None:
    s = _quelle()
    assert "${sicherText(kaufAlarmLeiste)}\n    ${sicherText(meineCoinsKasten)}" in s
    assert "sicherText(kaufAlarmLeiste) + pushBlock()" in s


def test_tagesdeckel_wird_angezeigt() -> None:
    s = _quelle()
    assert "Tagesdeckel voll:" in s
    assert "Einstiegs-Alarme in 24" in s  # derselbe Satz wie in alarm_tor.deckel


@pytest.mark.skipif(shutil.which("node") is None, reason="node fehlt")
def test_noch_einstieg_grenze() -> None:
    s = _quelle()
    m = re.search(r"function nochEinstiegGrenze\(w, minCrv\)\{.*?\n\}", s, re.S)
    assert m, "nochEinstiegGrenze nicht gefunden"
    js = m.group(0) + """
const a = nochEinstiegGrenze({tp1: 61.9, tp2: 64, tp3: 66, stop: 57.02}, 2);
const b = nochEinstiegGrenze({tp1: 57.78, tp2: 56, tp3: 55, stop: 60.68}, 2);
const c = nochEinstiegGrenze({tp1: null, tp2: null, tp3: null, stop: 1}, 2);
console.log(JSON.stringify([a, b, c]));
"""
    out = subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True).stdout
    a, b, c = __import__("json").loads(out)
    # Long: (66 - p) = 2 (p - 57.02)  ->  p = (66 + 114.04) / 3
    assert a == pytest.approx((66 + 2 * 57.02) / 3)
    # Short spiegelbildlich: (p - 55) = 2 (60.68 - p)
    assert b == pytest.approx((55 + 2 * 60.68) / 3)
    assert c is None
