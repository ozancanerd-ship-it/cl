"""Eigener Lernen-Reiter (12.10.2026): Ozan fand das Lernmaterial nicht."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parents[2] / "site" / "template.html"


def _s() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


def test_reiter_dock_und_startkarte_vorhanden() -> None:
    s = _s()
    assert 'id="t12">🎓 Lernen</button>' in s
    assert 'id="p12"' in s and 'id="lernen"' in s
    assert 'data-dock="12"' in s
    assert "sicherText(lernKarteStart)" in s
    assert "'Day-Trading','Lernen']" in s
    assert "if (i===12)" in s


def test_lernpfad_quiz_gueltig() -> None:
    s = _s()
    a = s.index("const LERNPFAD = [")
    b = s.index("function lernStand")
    js = s[a:b] + "\nconsole.log(JSON.stringify(LERNPFAD));"
    out = subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True).stdout
    pfad = json.loads(out)
    assert len(pfad) == 4
    for stufe in pfad:
        assert stufe["quiz"] and stufe["merke"]
        for q in stufe["quiz"]:
            assert 0 <= q["r"] < len(q["o"]) and q["e"]


def test_glossar_fuehrt_beide_listen_zusammen() -> None:
    s = _s()
    assert "for (const [t, x] of BEGRIFFE)" in s
    assert "Object.values(DT_BEGRIFFE)" in s
    assert re.search(r"id=\"lern_suche\"", s)


def test_kurz_karte_nennt_richtung() -> None:
    s = _s()
    assert s.count("o.richtung==='short'?'Short über':'Kaufen bei'") >= 3


def test_einstiegs_rangliste_sortiert_beste_zuerst() -> None:
    s = _s()
    a = s.index("function einstiegsPunkte(x){")
    b = s.index("function einstiegsRangHtml")
    js = (
        "const NOTE_RANG={'A+':6,'A':5,'A−':4,'B+':3,'B':2,'WATCH':1,'—':0};"
        "const alarmReif=x=>x.ja; const crvVon=o=>o.crv;"
        + s[a:b]
        + "const hoch=einstiegsPunkte({w:{note:'A'},o:{crv:3,score:80,rs:80,warnungen:[]},ja:true,live:true}).punkte;"
        "const tief=einstiegsPunkte({w:{note:'B'},o:{crv:2,score:45,rs:27,warnungen:['a','b','c','d']},ja:false,live:false}).punkte;"
        "console.log(JSON.stringify([hoch,tief]));"
    )
    out = subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True).stdout
    hoch, tief = json.loads(out)
    assert hoch > tief and tief >= 0 and hoch <= 100
    assert "sicherText(()=>einstiegsRangHtml(amEinstieg))" in s
