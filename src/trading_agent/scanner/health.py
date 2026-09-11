"""Portfolio-Gesundheit — eine Zahl, die das Depot als Ganzes beurteilt.

WARUM EINE EINZELNE ZAHL, WENN SIE DOCH IMMER ZU GROB IST

Weil niemand fünf Kennzahlen gleichzeitig im Blick behält. Der Masterplan verlangt an
dieser Stelle einen Wert von 0 bis 100 mit einer Ampel, und das ist richtig gedacht: die
Zahl ist kein Urteil, sondern ein **Aufmerksamkeitssignal**. Sie soll bewirken, dass man
hinsieht, wenn etwas nicht stimmt — und danach sind die Einzelteile dran, nicht die Zahl.

Deshalb liefert dieses Modul die Gesamtnote nie allein. Jeder Teilbereich kommt mit
seiner eigenen Note und einem Satz, der sagt, was ihn drückt. Eine Ampel ohne Begründung
ist eine Aufforderung, ihr zu glauben.

DIE FÜNF TEILE

* **Klumpen** — wie stark alles dasselbe ist. Aus ``scanner/exposure.py``. Der Teil, der
  am 9. September elf Positionen gleichzeitig gekostet hat.
* **Auslastung** — wie viel des Kapitals gebunden ist. Ein volles Depot kann keine
  Gelegenheit mehr ergreifen; das ist ein Risiko, auch wenn es sich nicht so anfühlt.
* **Qualität** — wie gut die Positionen im letzten Scan noch bewertet wurden. Eine
  Position, die man heute nicht mehr eröffnen würde, ist eine Entscheidung, die man
  nicht trifft, weil man sie schon getroffen hat.
* **Verlauf** — wie die bisherigen Trades gelaufen sind. Aus ``scanner/performance.py``.
* **Stand** — wie weit das Depot gerade unter Wasser steht.

WAS DIE ZAHL NICHT IST

Keine Prognose. Ein Depot mit 85 kann morgen zwanzig Prozent verlieren, und ein Depot
mit 40 kann steigen. Die Note misst, wie **robust** die Aufstellung ist, nicht wie
erfolgreich sie sein wird. Das ist ein Unterschied, den eine Ampel von sich aus nicht
mitliefert — deshalb steht er hier.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from trading_agent.scanner.exposure import Belegung, Grenzen, belegung

#: Gewichte der Teilbereiche. Gesetzt, nicht optimiert.
#:
#: Klumpen wiegt am schwersten, weil er der einzige Teil ist, der ein Depot an einem
#: einzigen Tag zerlegen kann. Der Verlauf wiegt wenig: er beschreibt die Vergangenheit,
#: und ein Depot ist nicht deshalb schlecht aufgestellt, weil die letzten Trades schlecht
#: liefen — es ist dann schlecht *gelaufen*, was etwas anderes ist.
GEWICHTE: dict[str, float] = {
    "klumpen": 0.30,
    "auslastung": 0.20,
    "qualitaet": 0.25,
    "verlauf": 0.10,
    "stand": 0.15,
}

#: Ab diesen Werten wechselt die Ampel.
GRUEN_AB = 70.0
GELB_AB = 45.0


@dataclass(frozen=True, slots=True)
class Teil:
    name: str
    note: float | None  # 0..100
    satz: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "note": round(self.note, 1) if self.note is not None else None,
            "satz": self.satz,
        }


@dataclass(frozen=True, slots=True)
class Gesundheit:
    note: float | None
    ampel: str  # gruen | gelb | rot | unbekannt
    teile: tuple[Teil, ...]
    #: Was zuerst angeschaut gehört.
    schwaechster: str | None
    saetze: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "note": round(self.note, 1) if self.note is not None else None,
            "ampel": self.ampel,
            "teile": [t.as_dict() for t in self.teile],
            "schwaechster": self.schwaechster,
            "saetze": list(self.saetze),
        }


def _ampel(note: float | None) -> str:
    if note is None:
        return "unbekannt"
    if note >= GRUEN_AB:
        return "gruen"
    if note >= GELB_AB:
        return "gelb"
    return "rot"


def _klumpen(b: Belegung) -> Teil:
    """Wie viel des Depots ist dieselbe Wette?

    Gemessen am größten Bündel im Verhältnis zu allem Offenen. Ein Depot, in dem zwei
    Drittel der Positionen krypto-long sind, ist zu zwei Dritteln eine einzige Position
    — unabhängig davon, wie viele Zeilen die Liste hat.
    """
    if b.offen == 0:
        return Teil("klumpen", 100.0, "Nichts offen — nichts, was gleichzeitig fallen kann.")
    groesstes = max(b.je_buendel.items(), key=lambda x: x[1], default=("", 0))
    anteil = groesstes[1] / b.offen
    # 1/n wäre perfekte Streuung; ab zwei Dritteln in einem Bündel ist die Note bei null.
    note = max(0.0, min(100.0, (1.0 - (anteil - 1.0 / max(2, b.offen)) / 0.66) * 100.0))
    klasse, _, richtung = groesstes[0].partition("-")
    if anteil >= 0.5:
        satz = (
            f"{groesstes[1]} von {b.offen} Positionen sind {klasse} {richtung} — "
            f"{anteil:.0%} des Depots hängen an derselben Bewegung."
        )
    else:
        satz = f"Größter Klumpen: {groesstes[1]} von {b.offen} in {klasse} {richtung}."
    return Teil("klumpen", note, satz)


def _auslastung(b: Belegung) -> Teil:
    """Wie viel Platz ist noch?

    Bewusst nicht „voll = schlecht". Ein leeres Depot verdient keine Bestnote: wer nie
    investiert ist, hat kein Risiko und auch keine Chance. Die beste Note liegt bei
    etwa der Hälfte der erlaubten Positionen.
    """
    moeglich = max(1, b.grenzen.tatsaechlich_moeglich)
    quote = b.offen / moeglich
    # Ein Bogen mit Maximum bei 0,5: halb belegt ist der beste Zustand.
    note = max(0.0, 100.0 * (1.0 - abs(quote - 0.5) / 0.5))
    if quote >= 1.0:
        satz = (
            f"{b.offen} von {moeglich} Plätzen belegt. Das Depot kann keine neue "
            "Gelegenheit mehr aufnehmen, egal wie gut sie wäre."
        )
    elif b.offen == 0:
        satz = "Nichts offen. Kein Risiko — aber auch nichts, was laufen kann."
    else:
        satz = f"{b.offen} von {moeglich} Plätzen belegt, {moeglich - b.offen} frei."
    return Teil("auslastung", note, satz)


def _qualitaet(positionen: list[dict[str, Any]]) -> Teil:
    """Würde man diese Positionen heute noch eröffnen?

    Gemessen am Anteil, der im letzten Scan noch handelbar bewertet wurde. Eine Position
    ohne aktuelle Bewertung zählt nicht mit — weder positiv noch negativ; sie ist
    schlicht unbekannt.
    """
    bewertet = [p for p in positionen if p.get("handelbar") is not None]
    if not bewertet:
        return Teil(
            "qualitaet",
            None,
            "Keine der offenen Positionen war im letzten Scan — keine Aussage möglich.",
        )
    gut = sum(1 for p in bewertet if p.get("handelbar"))
    note = gut / len(bewertet) * 100.0
    schwach = len(bewertet) - gut
    if schwach:
        satz = (
            f"{schwach} von {len(bewertet)} offenen Positionen würde man heute nicht mehr "
            "eröffnen. Das ist kein Verkaufsbefehl, aber die Stelle, an der man hinsieht."
        )
    else:
        satz = f"Alle {gut} bewerteten Positionen sind auch heute noch handelbar eingestuft."
    return Teil("qualitaet", note, satz)


def _verlauf(performance: dict[str, Any] | None) -> Teil:
    """Was die abgeschlossenen Trades gebracht haben — in eine Note übersetzt.

    Der Erwartungswert je Trade in R ist die Größe, die zählt. Null R ist die Mitte:
    ein System, das im Schnitt nichts verliert und nichts gewinnt, ist weder gesund noch
    krank, es ist ergebnislos.
    """
    if not performance:
        return Teil("verlauf", None, "Noch keine ausgewertete Bilanz.")
    ganz = (performance.get("je_regel") or {}).get("ganz") or {}
    n = int(ganz.get("anzahl") or 0)
    ew = ganz.get("erwartungswert")
    if not n or ew is None:
        return Teil("verlauf", None, "Noch kein abgeschlossener Trade.")
    # −0,5 R je Trade → 0, 0 R → 50, +0,5 R → 100.
    note = max(0.0, min(100.0, 50.0 + float(ew) * 100.0))
    belastbar = bool(ganz.get("belastbar"))
    satz = f"{n} abgeschlossene Trades, im Schnitt {float(ew):+.2f} R je Trade" + (
        "." if belastbar else " — noch zu wenige für eine belastbare Aussage."
    )
    return Teil("verlauf", note, satz)


def _stand(positionen: list[dict[str, Any]]) -> Teil:
    """Wie weit stehen die offenen Positionen gerade im Minus?

    Gerechnet als **Durchschnitt** in R, nicht als Summe. Die Summe wäre die größere
    Zahl und die schlechtere Kennzahl: ein Depot mit vierzig Positionen käme allein
    durch seine Größe auf einen Wert, der jede Skala sprengt, und die Note wäre dann
    ein Maß für die Anzahl der Zeilen statt für den Zustand. Der Durchschnitt ist
    unabhängig davon, wie viele Positionen offen sind.
    """
    werte = [float(p["r"]) for p in positionen if p.get("r") is not None]
    if not werte:
        return Teil("stand", None, "Kein Stand für die offenen Positionen bekannt.")
    schnitt = sum(werte) / len(werte)
    summe = sum(werte)
    # −1 R im Schnitt → 0, 0 R → 70, +1 R → 100.
    note = max(0.0, min(100.0, 70.0 + schnitt * 30.0))
    if schnitt < -0.25:
        satz = (
            f"Die offenen Positionen stehen im Schnitt bei {schnitt:+.2f} R "
            f"(zusammen {summe:+.1f} R). Das ist der Teil, der noch nicht entschieden ist."
        )
    else:
        satz = (
            f"Die offenen Positionen stehen im Schnitt bei {schnitt:+.2f} R "
            f"(zusammen {summe:+.1f} R)."
        )
    return Teil("stand", note, satz)


def bewerte(
    offen: list[dict[str, Any]],
    *,
    performance: dict[str, Any] | None = None,
    grenzen: Grenzen | None = None,
) -> Gesundheit:
    """Die Gesamtnote und ihre fünf Teile.

    ``offen`` sind die laufenden Positionen. Erwartet werden — soweit vorhanden — die
    Schlüssel ``klasse``, ``richtung``, ``handelbar`` (aus dem letzten Scan) und ``r``
    (aktueller Stand in Vielfachen des Risikos). Fehlende Angaben führen dazu, dass ein
    Teil **keine** Note bekommt, nicht dazu, dass er geraten wird.
    """
    b = belegung(offen, grenzen)
    teile = (
        _klumpen(b),
        _auslastung(b),
        _qualitaet(offen),
        _verlauf(performance),
        _stand(offen),
    )

    vorhanden = [t for t in teile if t.note is not None]
    if not vorhanden:
        return Gesundheit(
            note=None,
            ampel="unbekannt",
            teile=teile,
            schwaechster=None,
            saetze=("Zu wenig bekannt, um das Depot zu beurteilen.",),
        )
    gewicht = sum(GEWICHTE[t.name] for t in vorhanden)
    note = sum((t.note or 0.0) * GEWICHTE[t.name] for t in vorhanden) / gewicht

    schwach = min(vorhanden, key=lambda t: t.note or 0.0)
    saetze = [
        f"Gesamtnote {note:.0f} von 100 ({_ampel(note)}).",
        f"Am schwächsten: {schwach.name}. {schwach.satz}",
    ]
    if len(vorhanden) < len(teile):
        fehlt = [t.name for t in teile if t.note is None]
        saetze.append(f"Ohne Bewertung und deshalb nicht eingerechnet: {', '.join(fehlt)}.")
    saetze.append(
        "Die Note misst, wie robust das Depot aufgestellt ist — nicht, wie es laufen "
        "wird. Ein Depot mit 85 kann morgen verlieren, eines mit 40 kann steigen."
    )

    return Gesundheit(
        note=note,
        ampel=_ampel(note),
        teile=teile,
        schwaechster=schwach.name,
        saetze=tuple(saetze),
    )


__all__ = [
    "GELB_AB",
    "GEWICHTE",
    "GRUEN_AB",
    "Gesundheit",
    "Teil",
    "bewerte",
]
