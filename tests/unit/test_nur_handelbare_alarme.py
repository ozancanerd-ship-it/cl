"""Aufs Telefon gehen nur Momente, in denen etwas zu tun ist.

Ozans Einwand am 12. September, wörtlich: *„mein Handy kriegt die ganze Zeit
Nachrichten, wenn er sich neu aktualisiert hat. Ich will nur Alarme bekommen, wo ich
reingehen kann."*

Er hatte recht, und es war keine Kleinigkeit. Vorher ging fast jede Zustandsänderung
raus: jede neue Wache, jeder nachgezogene Plan, jede abgelaufene Idee. Das sind
Meldungen eines Programms über sich selbst — und ein Telefon, das bei jeder davon
summt, wird stummgeschaltet. Danach kommt auch das Wichtige nicht mehr an. Ein
Alarmsystem, das zu oft meldet, ist dasselbe wie eins, das gar nicht meldet, nur mit
mehr Aufwand.

Dieser Test hält die Liste fest, damit sie nicht beim nächsten neuen Ereignistyp
stillschweigend wieder wächst.
"""

from __future__ import annotations

import re
from pathlib import Path

SKRIPT = Path(__file__).resolve().parents[2] / "scripts" / "watch_levels.py"

#: Nur diese drei. Alles andere steht in der App.
ERLAUBT = {"EINSTIEG", "TP", "STOP"}


def _liste() -> set[str]:
    txt = SKRIPT.read_text(encoding="utf-8")
    treffer = re.search(r"AUFS_TELEFON\s*=\s*\{([^}]*)\}", txt)
    assert treffer, "AUFS_TELEFON wurde in watch_levels.py nicht gefunden"
    return set(re.findall(r'"([A-Z_]+)"', treffer.group(1)))


def test_nur_handlungsrelevante_ereignisse() -> None:
    assert _liste() == ERLAUBT


def test_kein_neues_setup_und_kein_plan_update() -> None:
    """Die beiden lautesten Störer namentlich — sie waren die Ursache."""
    laut = _liste() & {"NEUES_SETUP", "PLAN_AKTUALISIERT", "ABGELAUFEN", "INVALIDIERT"}
    assert not laut, f"Diese Ereignisse gehoeren nicht aufs Telefon: {sorted(laut)}"
