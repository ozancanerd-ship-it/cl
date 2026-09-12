"""Ein Coin, in dem Geld liegt, darf nicht aus dem Universum fallen.

WARUM ES DIESEN TEST GIBT

Das Krypto-Universum wird nach 24-Stunden-Umsatz sortiert und unten gekappt. Rutscht ein
Coin an einem ruhigen Tag unter die Schwelle, verschwindet er komplett aus dem Scan — und
mit ihm jede Bewertung fuer eine Position, die man darin haelt. Auf der Depotseite steht
dann einfach nichts mehr: kein Kurs, kein Urteil, kein Ausstiegsplan. Lautlos.

Genau diese Stille ist das Problem. Fuer einen Wert, in dem Geld liegt, ist „heute wenig
Umsatz" kein Grund, weniger hinzusehen.
"""

from __future__ import annotations

import ast
from pathlib import Path

QUELLE = Path(__file__).resolve().parents[2] / "scripts" / "build_scan_data.py"

#: Ohne diese geht es nicht — die groessten Werte der Anlageklasse.
UNVERZICHTBAR = ("BTC", "ETH", "SOL", "XRP", "DOGE", "LINK", "AVAX", "ADA")


def _kern() -> tuple[str, ...]:
    baum = ast.parse(QUELLE.read_text(encoding="utf-8"))
    for knoten in baum.body:
        ziele = getattr(knoten, "targets", None) or (
            [knoten.target] if isinstance(knoten, ast.AnnAssign) else []
        )
        for ziel in ziele:
            if isinstance(ziel, ast.Name) and ziel.id == "KERN_COINS":
                wert = ast.literal_eval(knoten.value)  # type: ignore[arg-type]
                return tuple(wert)
    raise AssertionError("KERN_COINS steht nicht mehr in build_scan_data.py")


def test_die_grossen_sind_immer_dabei() -> None:
    kern = _kern()
    fehlend = [c for c in UNVERZICHTBAR if c not in kern]
    assert not fehlend, f"aus dem Pflichtteil des Universums gefallen: {fehlend}"


def test_keine_doppelten_eintraege() -> None:
    kern = _kern()
    assert len(kern) == len(set(kern)), "doppelte Coins in KERN_COINS"


def test_kern_wird_auch_benutzt() -> None:
    """Eine Liste, die niemand liest, schuetzt vor gar nichts."""
    text = QUELLE.read_text(encoding="utf-8")
    assert "immer_dabei=tuple(f\"{c}{quote}\" for c in KERN_COINS)" in text, (
        "KERN_COINS wird nicht mehr als immer_dabei an den Universumsfilter gereicht"
    )


def test_kern_verdraengt_nicht_das_halbe_universum() -> None:
    """Pflichtwerte gehen vor — zu viele davon wuerden die Suche nach Neuem ersticken."""
    from trading_agent.scanner.universe import UniversumFilter

    kern = _kern()
    obergrenze = UniversumFilter().max_symbole
    assert len(kern) <= obergrenze // 3, (
        f"{len(kern)} Pflichtcoins bei maximal {obergrenze} Symbolen — dann bleibt fuer "
        "die eigentliche Marktsuche kaum noch Platz."
    )
