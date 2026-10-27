"""Hintergrunddateien (Notenbanken, Aktien-Info) duerfen Zwischenlaeufe ueberleben.

Befund 27.10.2026: ``notenbanken.json`` und ``aktien_info.json`` liefern auf der Live-Seite 404,
weil nur der volle Lauf sie holt und jeder 10-Minuten-Lauf die Seite aus einem frischen
Checkout neu baut. Der Schritt „Hintergrunddateien uebernehmen" holt die zuletzt
veroeffentlichte Fassung zurueck.
"""

from __future__ import annotations

from pathlib import Path

import yaml

WF = Path(__file__).resolve().parents[2] / ".github/workflows/daily.yml"


def _schritte() -> list[dict]:
    d = yaml.safe_load(WF.read_text(encoding="utf-8"))
    return d["jobs"]["tagesplan"]["steps"]


def test_uebernahme_schritt_existiert_und_laeuft_vor_dem_scan_und_dem_bau() -> None:
    namen = [s.get("name", "") for s in _schritte()]
    assert "Hintergrunddateien uebernehmen" in namen
    i = namen.index("Hintergrunddateien uebernehmen")
    assert i < namen.index("Markt scannen")
    assert i < max(
        k for k, s in enumerate(_schritte()) if "upload-pages-artifact" in str(s.get("uses", ""))
    )


def test_uebernahme_gilt_fuer_alle_seitenbauenden_laeufe_und_beide_dateien() -> None:
    s = next(x for x in _schritte() if x.get("name") == "Hintergrunddateien uebernehmen")
    assert "waechter" in str(s["if"]) and "!=" in str(s["if"])
    assert s.get("continue-on-error") is True  # ein Ausfall darf den Lauf nie scheitern lassen
    for f in ("aktien_info.json", "notenbanken.json"):
        assert f in s["run"]
    # nur gueltiges JSON wird uebernommen, eine frische Datei dieses Laufs nie ueberschrieben
    assert "json.load" in s["run"] and '-s "web/$f"' in s["run"]
