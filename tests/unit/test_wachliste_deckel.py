"""Die Beobachtungsliste ist kein Depot — und darf nicht wie eines gedeckelt werden.

Der Fehler, den dieser Test festhaelt, hat Ozan echtes Geld an entgangenen Signalen
gekostet und war von aussen unsichtbar: die Wachliste benutzte den Deckel fuer ein
ECHTES Depot (hoechstens acht offene Positionen). Am 17.09. standen sechs Wachen seit
Tagen auf "aktiv", ohne Stop und ohne Ziel zu treffen. Damit waren sechs von acht
Plaetzen belegt, bei Long fuenf von sechs — der Scanner fand weiter Setups, aufgenommen
wurde fast keines mehr, und ohne Aufnahme gibt es nie einen Einstiegsalarm.

Ozan: "Ich kriege keine Beisignale. Kaufsignale oder Verkaufssignale." Genau das.
"""

from __future__ import annotations

from datetime import UTC, datetime

from trading_agent.scanner.watchlist import Wachliste


def _zeile(name: str, *, richtung: str = "long", klasse: str = "krypto") -> dict:
    return {
        "instrument": name,
        "klasse": klasse,
        "richtung": richtung,
        "handelbar": True,
        "note": "A",
        "einstieg": 100.0,
        "invalidierung": 95.0,
        "score": 70.0,
        "plan": {"tp1": 110.0, "tp2": 120.0, "crv": 2.0},
        "setup": {"name": "Test", "these": "", "trigger": ""},
    }


def test_viele_gute_setups_kommen_auch_wirklich_auf_die_liste() -> None:
    """Zwoelf handelbare Setups duerfen nicht nach acht abgeschnitten werden."""
    liste = Wachliste()
    jetzt = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)
    zeilen = [_zeile(f"C{i}USD") for i in range(12)]
    liste.aufnehmen(zeilen, jetzt=jetzt)
    assert len(liste.wachen) == 12, (
        f"nur {len(liste.wachen)} von 12 aufgenommen — der Deckel schneidet wieder ab"
    )


def test_laufende_wachen_blockieren_neue_nicht_schon_bei_acht() -> None:
    """Der Fall vom 17.09.: sechs Wachen stehen auf 'aktiv' und haengen dort fest."""
    liste = Wachliste()
    jetzt = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)
    liste.aufnehmen([_zeile(f"ALT{i}USD") for i in range(6)], jetzt=jetzt)
    for w in liste.wachen.values():
        w.zustand = "aktiv"

    liste.aufnehmen([_zeile(f"NEU{i}USD") for i in range(6)], jetzt=jetzt)
    frisch = [k for k in liste.wachen if k.startswith("NEU")]
    assert len(frisch) >= 5, (
        f"nur {len(frisch)} von 6 neuen Setups aufgenommen, obwohl sechs alte nur "
        "beobachtet werden — beobachten kostet nichts"
    )


def test_eine_klasse_darf_die_liste_trotzdem_nicht_ganz_fuellen() -> None:
    """Weit heisst nicht unbegrenzt: zwanzig Kryptolongs bleiben eine einzige Wette."""
    liste = Wachliste()
    jetzt = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)
    liste.aufnehmen([_zeile(f"K{i}USD", klasse="krypto") for i in range(30)], jetzt=jetzt)
    assert len(liste.wachen) <= 24, "ohne jede Grenze wird die Liste zur Flut"
