"""Die Makrolage muss auch im Zehn-Minuten-Lauf da sein.

Bis zum 27.09. holte nur der volle Lauf (:25/:55) die Makrodaten. Jeder Krypto-Lauf
dazwischen bewertete ohne Makro und schrieb ``makro: null`` in die App — dieselbe Muenze
bekam je nach Uhrzeit zwei verschiedene Noten, und die Makro-Anzeige war meist leer.
Seitdem wird sie aus dem Vorlauf uebernommen, aber nur mit Zeitstempel und frisch.
"""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


def _modul() -> Any:
    pfad = Path(__file__).resolve().parents[2] / "scripts" / "build_scan_data.py"
    spec = importlib.util.spec_from_file_location("build_scan_data", pfad)
    assert spec and spec.loader
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


def test_frische_makrolage_wird_uebernommen() -> None:
    m = _modul()
    vor_einer_stunde = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    assert m._makro_frisch(vor_einer_stunde)


def test_alte_oder_undatierte_makrolage_nicht() -> None:
    m = _modul()
    alt = (datetime.now(UTC) - timedelta(hours=m.MAKRO_MAX_STUNDEN + 1)).isoformat()
    assert not m._makro_frisch(alt)
    # Ohne Zeitstempel wuerde ``as_dict`` beim Schreiben „jetzt" eintragen — die Lage
    # wuerde sich damit von Lauf zu Lauf selbst verjuengen. Deshalb: gar nicht.
    assert not m._makro_frisch("")
    assert not m._makro_frisch("2026-09-27T12:00:00")  # ohne Zeitzone
