"""Wie stark sich eine Aktie rund um ihre Quartalszahlen bewegt hat — gemessen, nicht geschaetzt.

Grundlage: 1 029 Termine bei 103 Aktien, 01/2024 – 09/2026 (``zahlen_bewegung.json``,
erzeugt mit ``scripts/zahlen_studie.py``). Gemessen wird die Bewegung vom Schluss vor dem
Termin bis zum Schluss danach — zwei Handelstage, damit Termine vor Boersenbeginn und nach
Boersenschluss gleich behandelt werden.

Im Mittel aller Termine: 4,5 %, das ist das 2,7-Fache eines normalen Zwei-Tage-Fensters.
Jeder zehnte Termin brachte mehr als 13 %. Fuer eine Aktie mit Stop 2 % unter dem Kurs
heisst das: an diesem Tag ist der Stop keine Grenze, sondern ein Vorschlag. Fuer einen
Turbo mit 11 % Puffer heisst es: eine einzige Nacht kann den Knock-out bringen.

Beschreibend, keine Vorhersage: wie weit es DIESMAL geht, weiss niemand.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_DATEI = Path(__file__).with_name("zahlen_bewegung.json")


@lru_cache(maxsize=1)
def _daten() -> dict[str, Any]:
    try:
        d = json.loads(_DATEI.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return d if isinstance(d, dict) else {}


def gesamt() -> dict[str, Any]:
    return dict(_daten().get("gesamt") or {})


def je_aktie(symbol: str) -> dict[str, Any] | None:
    """Median und Maximum der Zahlen-Bewegung dieser Aktie — nur ab vier Terminen."""
    e = (_daten().get("je_aktie") or {}).get(str(symbol or "").upper())
    if not isinstance(e, dict) or int(e.get("n") or 0) < 4:
        return None
    return {"median": e.get("median"), "max": e.get("max"), "n": e.get("n")}


def typisch_grosse_luecke(symbol: str) -> float:
    """Ab welcher Bewegung es fuer diese Aktie „knapp" wird: das Maximum ihrer eigenen
    Termine, mindestens aber jeder zehnte Termin aller Aktien (90. Perzentil)."""
    p90 = float(gesamt().get("p90") or 13.0)
    e = je_aktie(symbol)
    return max(p90, float(e["max"])) if e and e.get("max") is not None else p90


__all__ = ["gesamt", "je_aktie", "typisch_grosse_luecke"]
