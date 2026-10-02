"""Ein Stop, der gleich fällt, heißt nicht „Laufen lassen".

WARUM ES DIESEN TEST GIBT

02.10., Depot-Check mit Test-Positionen: Mondelez bekam einen frischen Halte-Stop bei
50,97 € — der Kurs stand bei 50,99 €. Auf der Karte: „Halten — Stop steht … 0,0 %
entfernt. Laufen lassen", und oben „Nichts zu tun … Die Stops stehen". Die Regel war
richtig (verkauft wird, wenn der Stop fällt), der Text nicht: er klang nach Ruhe, wo der
Ausstieg eine Kursbewegung entfernt war.

Festgehalten wird:

1. Unter ``STOP_NAH_PCT`` sagt der Schritt, dass der Stop nah ist — nicht „Laufen lassen".
2. Der Stop selbst bleibt gültig; es ist weiter ein Halten, kein Verkauf.
3. Bei einem Short heißt es „schliessen", nicht „verkaufen".
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


def _schritt(pos: dict, kurs: float) -> dict:
    node = shutil.which("node")
    if not node:  # pragma: no cover - nur auf Rechnern ohne Node
        pytest.skip("node nicht vorhanden")
    roh = VORLAGE.read_text(encoding="utf-8")
    nah = re.search(r"const STOP_NAH_PCT[^\n]*\n", roh)
    assert nah
    skript = (
        nah.group(0)
        + "".join(
            _funktion(roh, f) + "\n"
            for f in ("digits", "num", "waehrungsZeichen", "zieleGelten", "naechsterSchritt")
        )
        + "const a = JSON.parse(process.argv[1]);\n"
        + "console.log(JSON.stringify(naechsterSchritt(a.pos, a.kurs, 'EUR')));"
    )
    aus = subprocess.run(
        [node, "-e", skript, json.dumps({"pos": pos, "kurs": kurs})],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    return json.loads(aus.stdout)


def _pos(stop: float, richtung: str = "long") -> dict:
    return {
        "sym": "MDLZ",
        "erledigt": [],
        "plan": {"stop": stop, "richtung": richtung, "art": "halte", "quelle": "app"},
    }


def test_stop_direkt_unter_dem_kurs_ist_nah_und_nicht_laufen_lassen() -> None:
    s = _schritt(_pos(50.97), 50.99)
    assert s["stufe"] == "halten"
    assert s.get("nah") is True
    assert "nah" in s["titel"]
    assert "Laufen lassen" not in s["text"]
    assert "Nur noch 0,0 % bis zum Stop" in s["text"]


def test_weiter_stop_bleibt_laufen_lassen() -> None:
    s = _schritt(_pos(40.0), 50.99)
    assert s["titel"] == "Halten — Stop steht"
    assert "Laufen lassen" in s["text"]
    assert not s.get("nah")


def test_short_heisst_schliessen() -> None:
    nah = _schritt(_pos(51.5, "short"), 51.0)
    assert "wird geschlossen" in nah["text"] and "verkauft" not in nah["text"]
    weit = _schritt(_pos(60.0, "short"), 51.0)
    assert "Geschlossen wird" in weit["text"]


def test_gerissener_stop_bleibt_verkaufen() -> None:
    s = _schritt(_pos(50.97), 50.90)
    assert s["stufe"] == "stop"
