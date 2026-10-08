"""Portfolio-Tab: "Gewinn fällt vom Hoch zurück" direkt in "Was jetzt zu tun ist".

WARUM ES DIESEN TEST GIBT

Ozan, 08.10. 18:50, sichtlich genervt: „wie oft soll ich dir noch sagen, du sollst es
an der App anpassen ... bis jetzt irgendwann Teilverkauf hat es aber bis jetzt nicht
einmal gemacht — und das nicht nur für das, sondern für alle, auch Aktien, alles."

Der Depot-Wächter auf GitHub (``depot_stops.py``, Art ``gewinn_rueckgang``) hatte genau
diese Regel schon — aber seine Meldung geht per Mail/Push/Telegram raus, und keiner
dieser Kanäle ist bei Ozan eingerichtet (SMTP/TELEGRAM/VAPID stehen in den Workflow-Logs
leer). Für ihn kam davon nichts an. Jetzt läuft dieselbe Regel zusätzlich direkt in
``positionsAktion`` — der Funktion hinter "Was jetzt zu tun ist" oben im Portfolio-Tab,
die JEDE Positionsart gleich behandelt (``b.gvPct`` ist für Krypto, Aktien und Turbos
dieselbe Rechnung), unabhängig von jedem Mail-/Push-Kanal.

Festgehalten wird:

1. Eine Position, die vom Spitzengewinn deutlich zurückgefallen ist (≥ 35 % des Hochs
   oder ≥ 8 Punkte), bekommt eine dringende Teilverkauf-Aktion — auch ohne gerissenen
   Stop und ohne gültiges Ausstiegs-Ziel.
2. Der Spitzenwert wird über ``localStorage`` je Position gemerkt (``posSchluessel``,
   sym+konto) und wächst nie rückwärts — ein zweiter, niedrigerer Besuch verändert ihn
   nicht.
3. Ist der Rückgang klein (unter der Schwelle), bleibt es beim normalen Urteil
   ("Halten").
4. Einmal über den Knopf abgehakt (``pos.erledigt`` bekommt die Rückgangs-Marke), feuert
   GENAU dieser Rückgangs-Bereich nicht noch einmal — fällt der Gewinn weiter, ist das
   ein neuer Bereich und ein neuer, berechtigter Hinweis (wie bei TP1/TP2/TP3).
5. Die Regel gilt unabhängig von der Anlageklasse (hier zusätzlich mit einer Aktie ohne
   ``row`` geprüft, analog zu einem Papier ohne aktuellen Scan-Eintrag).

Geprüft wird die echte Funktion ``positionsAktion`` aus ``site/template.html``,
ausgeführt in Node — mit einem In-Memory-``localStorage``, wie es der Browser böte.
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


def _konst(roh: str, name: str) -> str:
    m = re.search(rf"const {name}\s*=", roh)
    assert m, f"Konstante {name} nicht gefunden"
    ende = roh.index(";", m.start())
    return roh[m.start() : ende + 1] + "\n"


def _skript() -> str:
    roh = VORLAGE.read_text(encoding="utf-8")
    teile = [
        "function name(sym){ return sym; }\n",
        "globalThis.localStorage = (function(){ let s={}; return {\n"
        "  getItem:k=>(k in s?s[k]:null), setItem:(k,v)=>{s[k]=String(v);},\n"
        "  removeItem:k=>{delete s[k];}};})();\n",
        _konst(roh, "SCHEIN_MUSTER"),
        _konst(roh, "MIN_ORDER_EUR"),
        _konst(roh, "posSchluessel"),
        _funktion(roh, "istSchein"),
        _funktion(roh, "istKurzDirekt"),
        _funktion(roh, "digits"),
        _funktion(roh, "num"),
        _funktion(roh, "waehrungsZeichen"),
        _funktion(roh, "pct"),
        _funktion(roh, "naechsterSchritt"),
        _funktion(roh, "planKurs"),
        _funktion(roh, "posName"),
        _konst(roh, "GEWINN_HOCH_KEY"),
        _funktion(roh, "gewinnHochLaden"),
        _funktion(roh, "gewinnHochMerken"),
        _funktion(roh, "positionsAktion"),
    ]
    return "".join(teile)


def _besuche(pos: dict, *besuche: dict) -> list[dict]:
    """Mehrere "Besuche" derselben Position NACHEINANDER, in EINEM Node-Prozess — sonst
    wäre jeder Aufruf ein frischer, leerer ``localStorage`` und der Spitzenwert könnte
    nie über einen zweiten Besuch hinweg bestehen bleiben (genau das, was geprüft wird)."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node nicht vorhanden")
    basis = {"pos": pos, "kurs": pos.get("einstieg"), "w": "USD", "gv": None, "wert": None,
             "einstand": None}
    bs = [{**basis, **b} for b in besuche]
    skript = _skript() + (
        "\nconst besuche = JSON.parse(process.argv[1]);\n"
        "console.log(JSON.stringify(besuche.map(b => positionsAktion(b, null))));"
    )
    aus = subprocess.run([node, "-e", skript, json.dumps(bs)], capture_output=True, text=True, timeout=20)
    if aus.returncode != 0:
        raise AssertionError(aus.stderr)
    return json.loads(aus.stdout)


def _lauf(pos: dict, b_extra: dict | None = None) -> dict:
    return _besuche(pos, b_extra or {})[0]


def test_deutlicher_rueckgang_vom_hoch_loest_teilverkauf_aus():
    pos = {"sym": "METUSD", "konto": "Bybit", "menge": 10, "einstieg": 100.0}
    # Erster Besuch bei 160 (gvPct 60) merkt nur das Hoch, kein Grund zu handeln.
    # Rückfall auf 120 (gvPct 20): 40 Punkte vom Hoch weg, mehr als 35 % von 60.
    r1, r2 = _besuche(pos, {"kurs": 160.0, "gvPct": 60.0}, {"kurs": 120.0, "gvPct": 20.0})
    assert r1["art"] == "halten"
    assert r2["art"] == "teilverkauf"
    assert r2["dringend"] is True
    assert "60,0 %" in r2["titel"] and "20,0 %" in r2["titel"]
    assert r2["marke"] == "gewinn_rueckgang:40"


def test_kleiner_rueckgang_bleibt_beim_halten():
    pos = {"sym": "METUSD", "konto": "Bybit", "menge": 10, "einstieg": 100.0}
    _, r = _besuche(pos, {"kurs": 160.0, "gvPct": 60.0}, {"kurs": 155.0, "gvPct": 55.0})
    assert r["art"] == "halten"  # nur 5 Punkte vom Hoch


def test_abgehakter_rueckgang_feuert_nicht_noch_einmal():
    pos = {
        "sym": "METUSD", "konto": "Bybit", "menge": 10, "einstieg": 100.0,
        "erledigt": ["gewinn_rueckgang:40"],
    }
    _, r = _besuche(pos, {"kurs": 160.0, "gvPct": 60.0}, {"kurs": 120.0, "gvPct": 20.0})
    assert r["art"] == "halten"


def test_weiterer_rueckfall_in_einen_neuen_bereich_feuert_wieder():
    pos = {
        "sym": "METUSD", "konto": "Bybit", "menge": 10, "einstieg": 100.0,
        "erledigt": ["gewinn_rueckgang:40"],
    }
    # 56 Punkte vom Hoch (160 -> gvPct 4 bei 104) liegen in einem neuen Bereich (50).
    _, r = _besuche(pos, {"kurs": 160.0, "gvPct": 60.0}, {"kurs": 104.0, "gvPct": 4.0})
    assert r["art"] == "teilverkauf"
    assert r["marke"] == "gewinn_rueckgang:50"


def test_gilt_auch_fuer_eine_aktie_ohne_aktuelle_scan_zeile():
    """Ozan: "nicht nur für das, sondern für alle, auch Aktien, alles." — ``b.row`` fehlt
    hier bewusst (kein frischer Scan-Eintrag), die Regel hängt nur an ``gvPct``."""
    pos = {"sym": "NVDA", "konto": "Trade Republic", "menge": 3, "einstieg": 100.0}
    _, r = _besuche(
        pos, {"kurs": 180.0, "gvPct": 80.0, "w": "EUR"}, {"kurs": 130.0, "gvPct": 30.0, "w": "EUR"}
    )
    assert r["art"] == "teilverkauf"
    assert r["dringend"] is True


def test_kein_frueherer_besuch_ohne_hohen_gewinn_bleibt_still():
    pos = {"sym": "XRPUSD", "konto": "Kraken", "menge": 100, "einstieg": 100.0}
    r = _lauf(pos, {"kurs": 103.0, "gvPct": 3.0})
    assert r["art"] == "halten"
