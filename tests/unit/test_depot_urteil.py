"""Das Depot-Urteil misst relative Staerke — nicht die Einstiegsqualitaet.

WARUM ES DIESEN TEST GIBT

Das Depot benutzte den Score aus der Rangliste als Urteil ueber bestehende Positionen.
Der Score beantwortet aber eine andere Frage: „gibt es hier gerade jetzt einen guten
Einstieg?" In einem Wert, in dem man laengst drin ist, steht dort fast immer eine kleine
Zahl — Solana 5 von 100, Fetch 3, Render 2 — nicht weil die Position schlecht waere,
sondern weil der Einstieg vorbei ist. Das Depot sagte deshalb bei praktisch jeder
Position „REDUZIEREN". Ein Rat, der immer gleich lautet, ist kein Rat.

Entschieden wird jetzt ueber die relative Staerke innerhalb der eigenen Klasse. Dieser
Test haelt zwei Dinge fest:

1. Zwei Positionen mit gleichem (niedrigem) Score, aber gegensaetzlicher relativer
   Staerke muessen unterschiedliche Urteile bekommen. Sonst ist die Unterscheidung
   wieder verloren, egal wie der Code aussieht.
2. Die Reihenfolge stimmt: mehr Staerke darf nie zu einem schlechteren Urteil fuehren.

Geprueft wird die echte Funktion aus ``site/template.html``, ausgefuehrt in Node — nicht
eine Nachbildung. Eine Nachbildung wuerde genau dann weiter gruen leuchten, wenn die
Vorlage kaputtgeht.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

VORLAGE = Path(__file__).resolve().parents[2] / "site" / "template.html"

#: Reihenfolge von gut nach schlecht. Der Test kennt nur diese Ordnung, nicht die
#: Schwellen — die duerfen sich aendern, ohne dass der Test umgeschrieben werden muss.
RANG = ["STARK HALTEN", "HALTEN", "BEOBACHTEN", "REDUZIEREN"]


def _funktion(name: str) -> str:
    """Den Quelltext genau einer Funktion aus der Vorlage schneiden."""
    roh = VORLAGE.read_text(encoding="utf-8")
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


def _konstanten() -> str:
    roh = VORLAGE.read_text(encoding="utf-8")
    m = re.search(r"const RS_FUEHRER[^\n]*\n", roh)
    assert m, "RS-Schwellen nicht gefunden"
    return m.group(0)


def _laufe(zeilen: list[dict]) -> list[str | None]:
    node = shutil.which("node")
    if not node:  # pragma: no cover - nur auf Rechnern ohne Node
        pytest.skip("node nicht vorhanden")
    skript = (
        _konstanten()
        + _funktion("haltUrteil")
        + "\nconst ein = JSON.parse(process.argv[1]);\n"
        + "console.log(JSON.stringify(ein.map(r => { const h = haltUrteil(r);"
        + " return h ? h.urteil : null; })));"
    )
    aus = subprocess.run(
        [node, "-e", skript, json.dumps(zeilen)],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    return list(json.loads(aus.stdout))


def _zeile(rs: float | None, score: float = 4.0, **rest: object) -> dict:
    return {"rs": rs, "score": score, "zusatz": {"renditen": {"r21": 1.0, "r63": 2.0}}, **rest}


def test_gleicher_score_verschiedene_staerke_verschiedenes_urteil() -> None:
    fuehrer, schlusslicht = _laufe([_zeile(88.0), _zeile(9.0)])
    assert fuehrer != schlusslicht, (
        "Ein Klassenfuehrer und ein Schlusslicht mit demselben Score bekommen dasselbe "
        "Urteil — dann haengt das Depot wieder am Einstiegs-Score."
    )
    assert RANG.index(fuehrer) < RANG.index(schlusslicht)


def test_urteil_wird_mit_staerke_nie_schlechter() -> None:
    werte = [5.0, 20.0, 30.0, 50.0, 68.0, 75.0, 95.0]
    urteile = _laufe([_zeile(rs) for rs in werte])
    raenge = [RANG.index(u) for u in urteile]
    assert raenge == sorted(raenge, reverse=True), (
        f"Mehr relative Staerke fuehrt zu einem schlechteren Urteil: {list(zip(werte, urteile, strict=True))}"
    )


def test_hoher_score_rettet_ein_schlusslicht_nicht() -> None:
    """Gegenprobe: der Score darf das Urteil nicht mehr anheben."""
    (mit_score,) = _laufe([_zeile(9.0, score=95.0)])
    (ohne_score,) = _laufe([_zeile(9.0, score=1.0)])
    assert mit_score == ohne_score == "REDUZIEREN"


def test_laufendes_short_setup_deckelt_das_halten() -> None:
    (normal,) = _laufe([_zeile(88.0)])
    (dagegen,) = _laufe([_zeile(88.0, richtung="short")])
    assert normal == "STARK HALTEN"
    assert dagegen == "BEOBACHTEN", (
        "Ein aktuelles Short-Setup im eigenen Wert muss das Urteil deckeln."
    )


def test_ohne_klassenvergleich_entscheidet_die_eigene_rendite() -> None:
    zeilen = [
        {"rs": None, "score": 4.0, "zusatz": {"renditen": {"r21": 6.0}}},
        {"rs": None, "score": 4.0, "zusatz": {"renditen": {"r21": -6.0}}},
    ]
    plus, minus = _laufe(zeilen)
    assert RANG.index(plus) < RANG.index(minus)
