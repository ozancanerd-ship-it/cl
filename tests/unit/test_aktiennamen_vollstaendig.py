"""Jede gescannte Aktie muss einen ausgeschriebenen Namen haben.

Ozans Einwand war schlicht: „schreib auch die Aktien und Kryptos vollständig auf, nicht
die Abkürzungen. Ich muss jedes Mal suchen." Eine Signalkarte mit ``NEE`` darauf ist für
ihn eine Suchaufgabe, kein Signal.

Die Zuordnung ist von Hand gepflegt — sie muss es sein, weil es keine freie, verlässliche
Quelle für Firmennamen gibt, die nicht selbst wieder ausfallen kann. Von Hand gepflegte
Tabellen laufen aber der Liste hinterher, sobald jemand einen Wert ins Universum
aufnimmt. Genau das ist passiert: fünf der vierzig Aktien standen nur als Kürzel da.

Deshalb prüft dieser Test nicht die Tabelle, sondern die **Lücke zwischen Tabelle und
Universum**. Wer eine Aktie aufnimmt, wird beim Testlauf daran erinnert, ihr auch einen
Namen zu geben.
"""

from __future__ import annotations

import re
from pathlib import Path

from trading_agent.scanner.handelbarkeit import AKTIEN_NAMEN

SKRIPT = Path(__file__).resolve().parents[2] / "scripts" / "build_scan_data.py"


def _universum() -> list[str]:
    txt = SKRIPT.read_text(encoding="utf-8")
    treffer = re.search(r"^AKTIEN = \[(.*?)^\]", txt, re.S | re.M)
    assert treffer, "die Aktienliste in build_scan_data.py wurde nicht gefunden"
    return re.findall(r'"([A-Z.\-]+)"', treffer.group(1))


def test_universum_ist_nicht_leer() -> None:
    assert len(_universum()) >= 20


def test_jede_aktie_hat_einen_ausgeschriebenen_namen() -> None:
    ohne = [t for t in _universum() if t not in AKTIEN_NAMEN]
    assert not ohne, (
        "Diese Werte werden gescannt, haben aber keinen ausgeschriebenen Namen — auf der "
        f"Signalkarte stünde nur das Kürzel: {ohne}. Ergänze sie in "
        "scanner/handelbarkeit.py unter AKTIEN_NAMEN."
    )
