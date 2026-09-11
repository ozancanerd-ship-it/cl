"""Wie viel darf gleichzeitig offen sein — und wie viel davon in dieselbe Richtung.

DER BEFUND

Am 11. September 2026 standen in der echten Wachliste **40 gleichzeitig aktive
Positionen**: 26 Krypto, 14 Aktien, 31 davon long. Bei 0,5 % Risiko je Trade wären das
20 % des Kontos gleichzeitig im Feuer.

Das klingt nach Streuung und ist das Gegenteil. Kryptowerte laufen fast alle mit
Bitcoin; 26 Krypto-Longs sind nicht 26 Wetten, sondern eine Wette, 26-mal abgerechnet.
Die Abschlüsse zeigen es unmittelbar: am 9. September wurden **elf Positionen an einem
Tag ausgestoppt**. Das war kein Pech in Serie — das war ein einziger Markttag, der
elfmal gezählt wurde. Genau daher kommt auch die „Verlustserie von 10" in der Statistik.

Ein Risikomodell, das jeden Trade für sich betrachtet, übersieht das strukturell. Es
rechnet 40-mal „nur ein halbes Prozent" und kommt nie auf die Frage, ob diese vierzig
halben Prozent dasselbe Prozent sind.

DIE ANTWORT

Deckel, die vor dem Einstieg greifen, nicht danach:

* **Wie viele Positionen überhaupt.** Über einer Handvoll kann niemand mehr ernsthaft
  einzelne Trades führen — und das System ist ausdrücklich kein Autopilot.
* **Wie viele je Klasse und je Richtung.** Die Größe, die den 9. September erklärt.
* **Wie viel Risiko in Summe.** Die einzige Zahl, die am Ende zählt.
* **Wie viele im selben Bündel.** Klasse mal Richtung als Näherung für Korrelation.

WARUM KLASSE MAL RICHTUNG UND NICHT GERECHNETE KORRELATION

Eine echte Korrelationsmatrix wäre genauer und wäre hier trotzdem die schlechtere Wahl:
sie bräuchte für jedes Paar eine ausreichend lange gemeinsame Historie, sie ist
instabil (Korrelationen springen genau dann auf eins, wenn es darauf ankommt), und sie
verführt dazu, Grenzen feiner einzustellen, als die Datenlage hergibt.

Klasse mal Richtung ist grob und in die sichere Richtung falsch: sie behandelt zwei
Kryptowerte als abhängig, auch wenn sie es gerade nicht sind. Das kostet gelegentlich
eine Position — und verhindert den Tag, an dem elf auf einmal sterben.

**Ein Deckel ist keine Prognose.** Er sagt nicht, dass der 41. Trade schlecht wäre. Er
sagt, dass er nichts hinzufügt, was nicht schon dasteht.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: Klassen, deren Werte untereinander erfahrungsgemäß stark mitlaufen. Für sie gilt der
#: Bündel-Deckel. Alles andere bekommt nur den Klassen-Deckel.
GEKOPPELT = frozenset({"krypto", "gold"})


@dataclass(frozen=True, slots=True)
class Grenzen:
    """Die Deckel. Gesetzt, nicht optimiert — und bewusst niedrig.

    Die Werte kommen aus der Frage „wie viele Trades kann ein Mensch gleichzeitig
    führen", nicht aus einer Rückrechnung. Wer sie aus historischen Ergebnissen
    ableitet, optimiert auf genau den Marktabschnitt, der vorbei ist.
    """

    #: Gleichzeitig offene Positionen insgesamt.
    max_offen: int = 8
    #: Gleichzeitig offen je Anlageklasse.
    max_je_klasse: int = 5
    #: Gleichzeitig offen je Richtung.
    max_je_richtung: int = 6
    #: Gleichzeitig offen im selben Bündel (Klasse × Richtung) bei gekoppelten Klassen.
    max_je_buendel: int = 4
    #: Summe des Risikos aller offenen Positionen, in Prozent des Kapitals.
    max_risiko_pct: float = 4.0
    #: Risiko je einzelnem Trade, in Prozent des Kapitals.
    risiko_je_trade_pct: float = 0.5
    #: Wie viel des Kapitals höchstens in EINER Position stecken darf. Nicht das
    #: Risiko — der Einsatz. Bei einem engen Stop erlaubt die Risikorechnung eine
    #: Stückzahl, die man gar nicht bezahlen kann.
    max_anteil_je_position: float = 0.25
    #: Wie viel des Kapitals insgesamt investiert sein darf. 1,0 heißt: voll, aber
    #: ohne Hebel. Wer darüber geht, leiht sich Geld — eine andere Entscheidung als
    #: „noch ein Trade".
    max_anteil_gesamt: float = 1.0

    @property
    def rechnerisch_moeglich(self) -> int:
        """Wie viele Positionen der Risiko-Deckel allein zulässt."""
        if self.risiko_je_trade_pct <= 0:
            return self.max_offen
        return int(self.max_risiko_pct / self.risiko_je_trade_pct)

    @property
    def bezahlbar(self) -> int:
        """Wie viele Positionen das Kapital hergibt — die Grenze, die zuerst greift.

        Bei kleinem Konto ist nicht das Risiko der Engpass, sondern das Geld. Mit einem
        Deckel von 25 % je Position passen vier Positionen ins Depot, nicht acht.
        Beides zugleich zu versprechen — „acht Positionen" und „je bis zu einem Viertel
        des Kapitals" — ergibt zweihundert Prozent und damit einen Kredit, von dem nie
        jemand gesprochen hat.
        """
        if self.max_anteil_je_position <= 0:
            return self.max_offen
        return int(self.max_anteil_gesamt / self.max_anteil_je_position)

    @property
    def tatsaechlich_moeglich(self) -> int:
        """Der kleinste der drei Deckel — die Zahl, die wirklich gilt."""
        return min(self.max_offen, self.rechnerisch_moeglich, self.bezahlbar)


@dataclass(frozen=True, slots=True)
class Entscheidung:
    ja: bool
    grund: str
    #: Welcher Deckel gegriffen hat — für die Statistik, nicht für die Anzeige.
    deckel: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"ja": self.ja, "grund": self.grund, "deckel": self.deckel}


def buendel(klasse: str, richtung: str) -> str:
    """Das Korrelationsbündel als Näherung: Klasse mal Richtung."""
    return f"{(klasse or '?').lower()}-{(richtung or '?').lower()}"


@dataclass(frozen=True, slots=True)
class Belegung:
    """Wie voll das Depot gerade ist — die Zahlen, die die App anzeigt."""

    offen: int
    je_klasse: dict[str, int]
    je_richtung: dict[str, int]
    je_buendel: dict[str, int]
    risiko_pct: float
    grenzen: Grenzen
    saetze: tuple[str, ...] = field(default_factory=tuple)

    @property
    def voll(self) -> bool:
        return self.offen >= self.grenzen.max_offen

    def as_dict(self) -> dict[str, Any]:
        return {
            "offen": self.offen,
            "je_klasse": dict(self.je_klasse),
            "je_richtung": dict(self.je_richtung),
            "je_buendel": dict(self.je_buendel),
            "risiko_pct": round(self.risiko_pct, 2),
            "voll": self.voll,
            "grenzen": {
                "max_offen": self.grenzen.max_offen,
                "max_je_klasse": self.grenzen.max_je_klasse,
                "max_je_richtung": self.grenzen.max_je_richtung,
                "max_je_buendel": self.grenzen.max_je_buendel,
                "max_risiko_pct": self.grenzen.max_risiko_pct,
            },
            "saetze": list(self.saetze),
        }


def _zaehle(offen: list[dict[str, Any]]) -> tuple[dict[str, int], dict[str, int], dict[str, int]]:
    klassen: dict[str, int] = {}
    richtungen: dict[str, int] = {}
    buendel_: dict[str, int] = {}
    for p in offen:
        k = str(p.get("klasse") or "?").lower()
        r = str(p.get("richtung") or "?").lower()
        klassen[k] = klassen.get(k, 0) + 1
        richtungen[r] = richtungen.get(r, 0) + 1
        b = buendel(k, r)
        buendel_[b] = buendel_.get(b, 0) + 1
    return klassen, richtungen, buendel_


def belegung(offen: list[dict[str, Any]], grenzen: Grenzen | None = None) -> Belegung:
    """Der Stand des Depots gegen die Deckel."""
    g = grenzen or Grenzen()
    klassen, richtungen, buendel_ = _zaehle(offen)
    risiko = len(offen) * g.risiko_je_trade_pct

    saetze: list[str] = []
    if len(offen) >= g.max_offen:
        saetze.append(
            f"{len(offen)} Positionen offen — das ist die Obergrenze. Neue Setups kommen "
            "auf die Warteliste, bis eine geschlossen ist."
        )
    groesstes = max(buendel_.items(), key=lambda x: x[1], default=("", 0))
    if groesstes[1] >= g.max_je_buendel and groesstes[0].split("-")[0] in GEKOPPELT:
        klasse, richtung = groesstes[0].split("-", 1)
        saetze.append(
            f"{groesstes[1]} Positionen sind {klasse} {richtung}. Diese Werte laufen "
            "weitgehend miteinander — das sind nicht mehrere Wetten, sondern eine, "
            "mehrfach abgerechnet."
        )
    if risiko >= g.max_risiko_pct:
        saetze.append(
            f"Rund {risiko:.1f} % des Kapitals stehen gleichzeitig im Risiko. "
            f"Der Deckel liegt bei {g.max_risiko_pct:.0f} %."
        )
    return Belegung(
        offen=len(offen),
        je_klasse=klassen,
        je_richtung=richtungen,
        je_buendel=buendel_,
        risiko_pct=risiko,
        grenzen=g,
        saetze=tuple(saetze),
    )


def pruefe(
    kandidat: dict[str, Any],
    offen: list[dict[str, Any]],
    grenzen: Grenzen | None = None,
) -> Entscheidung:
    """Darf diese Position zusätzlich eröffnet werden?

    Die Deckel werden in der Reihenfolge geprüft, in der ihre Verletzung am meisten
    aussagt — der Bündel-Deckel vor dem Klassen-Deckel, weil er das eigentliche Problem
    benennt und nicht nur ein Symptom.
    """
    g = grenzen or Grenzen()
    klassen, richtungen, buendel_ = _zaehle(offen)
    klasse = str(kandidat.get("klasse") or "?").lower()
    richtung = str(kandidat.get("richtung") or "?").lower()
    b = buendel(klasse, richtung)

    if len(offen) >= g.max_offen:
        return Entscheidung(
            False,
            f"Schon {len(offen)} Positionen offen (Grenze {g.max_offen}). Ein weiterer "
            "Trade macht das Depot nicht besser, nur unübersichtlicher.",
            "max_offen",
        )

    # Der Deckel, der bei kleinem Konto als erster greift: das Geld. Er kommt vor dem
    # Risiko-Deckel, weil eine Position, die man nicht bezahlen kann, gar nicht erst
    # zur Risikofrage wird.
    if len(offen) >= g.bezahlbar:
        return Entscheidung(
            False,
            f"Das Kapital ist ausgelastet: {len(offen)} Positionen zu je bis zu "
            f"{g.max_anteil_je_position:.0%} sind zusammen "
            f"{len(offen) * g.max_anteil_je_position:.0%} des Depots. Mehr ginge nur "
            "auf Kredit.",
            "kapital",
        )

    risiko = len(offen) * g.risiko_je_trade_pct
    if risiko + g.risiko_je_trade_pct > g.max_risiko_pct:
        return Entscheidung(
            False,
            f"Mit dieser Position stünden {risiko + g.risiko_je_trade_pct:.1f} % des "
            f"Kapitals gleichzeitig im Risiko, erlaubt sind {g.max_risiko_pct:.0f} %.",
            "max_risiko",
        )

    if klasse in GEKOPPELT and buendel_.get(b, 0) >= g.max_je_buendel:
        return Entscheidung(
            False,
            f"Es laufen bereits {buendel_[b]} Positionen {klasse} {richtung}. Diese Werte "
            "bewegen sich weitgehend gemeinsam — eine weitere erhöht das Risiko, ohne die "
            "Streuung zu erhöhen.",
            "max_buendel",
        )

    if klassen.get(klasse, 0) >= g.max_je_klasse:
        return Entscheidung(
            False,
            f"Schon {klassen[klasse]} Positionen in {klasse} (Grenze {g.max_je_klasse}).",
            "max_klasse",
        )

    if richtungen.get(richtung, 0) >= g.max_je_richtung:
        return Entscheidung(
            False,
            f"Schon {richtungen[richtung]} Positionen {richtung} (Grenze "
            f"{g.max_je_richtung}). Das ist eine Richtungswette auf den Gesamtmarkt.",
            "max_richtung",
        )

    frei = g.max_offen - len(offen)
    return Entscheidung(True, f"Platz vorhanden — {frei} Position(en) frei.", None)


def waehle(
    kandidaten: list[dict[str, Any]],
    offen: list[dict[str, Any]],
    grenzen: Grenzen | None = None,
) -> tuple[list[dict[str, Any]], list[tuple[dict[str, Any], Entscheidung]]]:
    """Aus einer nach Güte sortierten Liste so viele nehmen, wie die Deckel zulassen.

    Rückgabe: (genommen, zurückgestellt mit Grund). Die Reihenfolge der Kandidaten wird
    respektiert — es wird **nicht** umsortiert, um die Deckel besser auszunutzen. Wer
    das täte, würde ein schlechteres Setup vorziehen, nur weil es in eine freie Schublade
    passt, und genau das ist die Art von Optimierung, die Portfolios kaputtmacht.
    """
    g = grenzen or Grenzen()
    genommen: list[dict[str, Any]] = []
    zurueck: list[tuple[dict[str, Any], Entscheidung]] = []
    stand = list(offen)
    for k in kandidaten:
        e = pruefe(k, stand, g)
        if e.ja:
            genommen.append(k)
            stand.append(k)
        else:
            zurueck.append((k, e))
    return genommen, zurueck


__all__ = [
    "GEKOPPELT",
    "Belegung",
    "Entscheidung",
    "Grenzen",
    "belegung",
    "buendel",
    "pruefe",
    "waehle",
]
