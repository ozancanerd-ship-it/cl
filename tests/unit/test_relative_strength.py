"""scanner/relative_strength — wer laeuft besser als der Rest seiner Klasse.

Warum getestet: die relative Staerke greift NACH dem Scan in die Noten ein. Ein Fehler
hier zeigt sich nicht als Absturz, sondern als stilles Verschieben der Rangliste — die
schlimmste Sorte, weil sie wie ein Marktbefund aussieht.
"""

from __future__ import annotations

from typing import Any

from trading_agent.scanner.relative_strength import (
    FUEHRER_AB,
    MIN_KLASSENGROESSE,
    anwenden,
    bewerte_klasse,
    renditen,
    urteil_anpassen,
)


class _Bar:
    def __init__(self, close: float) -> None:
        self.close = close


def _zeile(name: str, r21: float, *, richtung: str = "long", urteil: str = "A") -> dict[str, Any]:
    return {
        "instrument": name,
        "richtung": richtung,
        "urteil": urteil,
        "note": urteil,
        "warnungen": [],
        "zusatz": {"renditen": {"r21": r21, "r63": r21}},
    }


def test_renditen_brauchen_historie() -> None:
    assert renditen([_Bar(100.0)] * 10) == {}
    r = renditen([_Bar(100.0 + i) for i in range(80)])
    assert "r21" in r and "r63" in r
    assert r["r21"] > 0


def test_perzentil_ordnet_die_klasse() -> None:
    zeilen = [_zeile(f"X{i}", float(i)) for i in range(10)]
    bewerte_klasse(zeilen)
    werte = [z["rs"] for z in zeilen]
    assert werte[0] is not None
    assert werte[-1] > werte[0]
    assert max(werte) >= 90 and min(werte) <= 10


def test_zu_kleine_klasse_bekommt_keine_bewertung() -> None:
    """Ein Perzentil aus drei Werten waere eine Zahl ohne Aussage."""
    zeilen = [_zeile(f"X{i}", float(i)) for i in range(MIN_KLASSENGROESSE - 1)]
    bewerte_klasse(zeilen)
    assert all(z["rs"] is None for z in zeilen)


def test_long_im_nachzuegler_wird_gedeckelt() -> None:
    z = _zeile("SCHWACH", -30.0, urteil="A_PLUS")
    z["rs"] = 5.0
    urteil_anpassen(z)
    assert z["urteil"] == "A_MINUS"
    assert z["deckel"]
    assert any("Nachzuegler" in w for w in z["warnungen"])


def test_short_im_marktfuehrer_wird_gedeckelt() -> None:
    z = _zeile("STARK", 90.0, richtung="short", urteil="A")
    z["rs"] = 95.0
    urteil_anpassen(z)
    assert z["urteil"] == "A_MINUS"


def test_staerke_hebt_die_note_nicht_an() -> None:
    """Relative Staerke ist ein Filter, keine Punktequelle."""
    z = _zeile("STARK", 90.0, urteil="B")
    z["rs"] = FUEHRER_AB + 5
    urteil_anpassen(z)
    assert z["urteil"] == "B"
    assert "staerksten" in z["begruendung"]


def test_ohne_rs_bleibt_alles_wie_es_war() -> None:
    z = _zeile("UNBEKANNT", 0.0, urteil="A_PLUS")
    z["rs"] = None
    urteil_anpassen(z)
    assert z["urteil"] == "A_PLUS"
    assert not z["warnungen"]


def test_anwenden_geht_klassenweise_vor() -> None:
    """Eine Aktie wird gegen Aktien gemessen, nicht gegen Kryptowerte."""
    krypto = [_zeile(f"K{i}", float(i) * 10) for i in range(10)]
    aktien = [_zeile(f"A{i}", float(i) * 0.1) for i in range(10)]
    anwenden({"krypto": krypto, "aktien": aktien})
    # Der schwaechste Kryptowert und die schwaechste Aktie liegen beide unten,
    # obwohl ihre Rohrenditen um Groessenordnungen auseinanderliegen.
    assert krypto[0]["rs"] == aktien[0]["rs"]
    assert krypto[-1]["rs"] == aktien[-1]["rs"]
