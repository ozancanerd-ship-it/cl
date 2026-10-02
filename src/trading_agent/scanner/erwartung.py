"""Was ein Signal bringen kann — und wie oft das bisher aufging.

DIE FRAGE, DIE DAHINTERSTECKT

Ozan hat sie in einem Satz gestellt: *„es fehlt noch bei den Angaben, wie viel Plus die
ungefähr machen wird, wie wahrscheinlich es ist."* Beides gehört auf jede Signalkarte —
aber es sind zwei völlig verschiedene Arten von Zahl, und sie hier zu vermischen wäre
der bequemste Weg, jemanden in einen Trade zu reden.

**Die erste Zahl ist Rechnen.** Vom Einstieg zum ersten Ziel liegen soundso viel
Prozent. Das steht im Plan, das ist nachprüfbar, da gibt es nichts zu schätzen.

**Die zweite Zahl ist Erinnerung, keine Vorhersage.** Niemand — kein Modell, kein
Händler, kein Scanner — kennt die Wahrscheinlichkeit, dass *dieser* Trade aufgeht. Was
sich sagen lässt: von den Signalen derselben Note und desselben Setups, die dieses
System bisher ausgegeben hat, haben soundso viele das erste Ziel erreicht. Das ist eine
Häufigkeit aus der eigenen Vergangenheit, mit allem, was daran hängt: kleine Stichprobe,
ein einziger Marktabschnitt, überwiegend Krypto, wenige Wochen.

Deshalb steht neben jeder Häufigkeit die Zahl der Fälle, aus denen sie stammt, und
deshalb sagt dieses Modul **gar nichts**, wenn es zu wenige davon gibt. Eine Quote aus
vier Trades ist keine Quote, sondern eine Anekdote mit Prozentzeichen — und eine
Anekdote mit Prozentzeichen ist gefährlicher als gar keine Zahl, weil sie sich anfühlt
wie Wissen.

WELCHE GRUPPE GEZÄHLT WIRD

Von fein nach grob, und es gewinnt die feinste Gruppe, die genug Fälle hat:

0. Setup-Art **in ihrer Klasse** (z. B. „Rückeroberung bei Coins") — gezählt wie im
   Alarm-Tor: nur entschiedene Trades (Stop, Ziel 1 oder alle Ziele)
1. Setup **und** Note (z. B. „Ausbruch, Note A")
2. nur das Setup
3. nur die Note
4. alle abgeschlossenen Signale

Stufe 0 gibt es seit 02.10. Davor stand auf der Karte eines Dash-Alarms „Von 102
Signalen der Note A− erreichten 13 % das erste Ziel … die Vergangenheit spricht gegen
den Trade" — während derselbe Alarm klingelte, weil „Rückeroberung nach
Liquiditätsgriff" bei Coins mit 24 Trades und +9,5 R im Plus lag. Die Note-Zahl kam fast
ganz aus 91 Signalen ohne benanntes Setup aus der ersten Septemberhälfte. Zwei Zahlen,
zwei Urteile auf einer Karte. Dazu kam ein stiller Fehler: der Scan fragte nach dem
Kürzel der Setup-Art („RUECKEROBERUNG"), die Trades tragen den Namen — Stufe 1 und 2
fanden deshalb nie etwas.

Die Reihenfolge ist absichtlich so und nicht umgekehrt: je ähnlicher die Vergangenheit
dem vorliegenden Fall ist, desto mehr sagt sie. Nur reicht die Ähnlichkeit eben oft
nicht für eine belastbare Zahl — dann lieber eine gröbere Gruppe mit mehr Fällen als
eine passgenaue mit dreien.

WAS BEWUSST NICHT PASSIERT

Die Häufigkeit wird **nicht** in den Score eingerechnet. Täte sie das, würde sich das
System an seiner eigenen kurzen Vergangenheit festbeißen: Setups, die in sechs Wochen
zufällig schlecht liefen, kämen nie wieder hoch, und die Stichprobe, aus der gelernt
wird, bliebe für immer klein. Die Zahl steht daneben, damit ein Mensch sie sieht.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: Unter so vielen abgeschlossenen Fällen wird keine Häufigkeit ausgewiesen. Bewusst
#: nicht bei 5 oder 6: bei zehn Trades verschiebt ein einziger die Quote um zehn
#: Punkte, und eine Zahl, die ein Einzelfall um zehn Punkte dreht, ist keine.
MIN_FAELLE = 12

#: Ab hier heißt die Zahl belastbar — und selbst das ist großzügig. Entspricht
#: ``performance.GENUG``, hier eigenständig gehalten.
BELASTBAR_AB = 30

ZIELE = ("TP1", "TP2", "TP3")


@dataclass(frozen=True, slots=True)
class Quote:
    """Wie eine Gruppe vergangener Signale ausgegangen ist."""

    #: Woher die Zahl stammt, im Klartext für die Karte.
    basis: str
    n: int
    #: Anteil, der das jeweilige Ziel erreicht hat.
    tp1: float
    tp2: float
    tp3: float
    #: Anteil, der ausgestoppt wurde.
    stop: float
    #: Durchschnittliches Ergebnis in R nach der Drittel-Regel.
    schnitt_r: float
    belastbar: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "basis": self.basis,
            "n": self.n,
            "tp1": round(self.tp1, 3),
            "tp2": round(self.tp2, 3),
            "tp3": round(self.tp3, 3),
            "stop": round(self.stop, 3),
            "schnitt_r": round(self.schnitt_r, 2),
            "belastbar": self.belastbar,
        }


def _quote(basis: str, gruppe: list[dict[str, Any]]) -> Quote:
    n = len(gruppe)
    getroffen = {z: sum(1 for t in gruppe if z in (t.get("erreicht") or ())) / n for z in ZIELE}
    return Quote(
        basis=basis,
        n=n,
        tp1=getroffen["TP1"],
        tp2=getroffen["TP2"],
        tp3=getroffen["TP3"],
        stop=sum(1 for t in gruppe if t.get("zustand") == "stop") / n,
        schnitt_r=sum(float(t.get("r_drittel") or 0.0) for t in gruppe) / n,
        belastbar=n >= BELASTBAR_AB,
    )


#: Wie die App die Klassen nennt.
KLASSE_NAME = {"krypto": "Coins", "aktien": "Aktien", "gold": "Gold"}


def _entschieden(t: dict[str, Any]) -> bool:
    """Wie im Alarm-Tor (``alarm_tor.bilanz``): Stop, alle Ziele oder wenigstens Ziel 1."""
    zustand = str(t.get("zustand") or "")
    return zustand in ("stop", "ziel_erreicht") or "TP1" in (t.get("erreicht") or ())


def quoten(trades: list[dict[str, Any]] | None) -> dict[str, Quote]:
    """Die Häufigkeitstabelle über alle abgeschlossenen Trades.

    Schlüssel: ``"klasse:krypto|setup:<Name>"`` (nur entschiedene Trades, wie im
    Alarm-Tor), ``"setup:<Name>|note:A"``, ``"setup:<Name>"``, ``"note:A"``, ``"alle"``.
    """
    if not trades:
        return {}
    eimer: dict[str, list[dict[str, Any]]] = {"alle": list(trades)}
    for t in trades:
        note = str(t.get("note") or "").strip()
        setup = str(t.get("setup") or "").strip()
        klasse = str(t.get("klasse") or "").strip()
        if klasse and setup and _entschieden(t):
            eimer.setdefault(f"klasse:{klasse}|setup:{setup}", []).append(t)
        if note:
            eimer.setdefault(f"note:{note}", []).append(t)
        if setup:
            eimer.setdefault(f"setup:{setup}", []).append(t)
        if note and setup:
            eimer.setdefault(f"setup:{setup}|note:{note}", []).append(t)
    namen = {
        "alle": "Signalen insgesamt",
    }
    aus: dict[str, Quote] = {}
    for k, g in eimer.items():
        if k in namen:
            name = namen[k]
        elif k.startswith("klasse:"):
            kl, _, s = k[len("klasse:") :].partition("|setup:")
            name = f"Signalen „{s}“ bei {KLASSE_NAME.get(kl, kl)}"
        elif k.startswith("setup:") and "|note:" in k:
            s, nt = k[len("setup:") :].split("|note:")
            name = f"Signalen „{s}“ mit Note {nt}"
        elif k.startswith("setup:"):
            name = f"Signalen „{k[len('setup:') :]}“"
        else:
            name = f"Signalen der Note {k[len('note:') :]}"
        aus[k] = _quote(name, g)
    return aus


def passende(
    tabelle: dict[str, Quote],
    *,
    note: str | None,
    setup: str | None,
    klasse: str | None = None,
) -> Quote | None:
    """Die feinste Gruppe mit genug Fällen — oder nichts."""
    kandidaten = []
    if setup and klasse:
        kandidaten.append(f"klasse:{klasse}|setup:{setup}")
    if setup and note:
        kandidaten.append(f"setup:{setup}|note:{note}")
    if setup:
        kandidaten.append(f"setup:{setup}")
    if note:
        kandidaten.append(f"note:{note}")
    kandidaten.append("alle")
    for k in kandidaten:
        q = tabelle.get(k)
        if q is not None and q.n >= MIN_FAELLE:
            return q
    return None


@dataclass(frozen=True, slots=True)
class Erwartung:
    """Was die Karte anzeigt: die gerechnete Strecke und die erinnerte Häufigkeit."""

    #: Weg vom Einstieg zum jeweiligen Ziel, in Prozent des Einstiegs.
    bis_tp1_pct: float | None
    bis_tp3_pct: float | None
    #: Weg zum Stop, in Prozent — damit die Strecke nicht ohne ihren Preis dasteht.
    risiko_pct: float | None
    quote: Quote | None
    saetze: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "bis_tp1_pct": round(self.bis_tp1_pct, 2) if self.bis_tp1_pct is not None else None,
            "bis_tp3_pct": round(self.bis_tp3_pct, 2) if self.bis_tp3_pct is not None else None,
            "risiko_pct": round(self.risiko_pct, 2) if self.risiko_pct is not None else None,
            "quote": self.quote.as_dict() if self.quote else None,
            "saetze": list(self.saetze),
        }


def _de(x: float, nk: int = 1, *, vorzeichen: bool = False) -> str:
    """Deutsche Schreibweise — Komma statt Punkt, echtes Minuszeichen.

    Die Sätze hier gehen unverändert auf die Signalkarte. Wer „-0.30 R" liest, liest
    zweimal hin; das ist bei einer Zahl, die von einem Trade abraten soll, genau der
    Moment, in dem man sie überliest.
    """
    roh = f"{x:+.{nk}f}" if vorzeichen else f"{x:.{nk}f}"
    return roh.replace("-", "\u2212").replace(".", ",")


def _strecke(von: float, nach: float, lang: bool) -> float | None:
    if von <= 0 or nach <= 0:
        return None
    roh = (nach - von) / von * 100.0
    return roh if lang else -roh


def rechne(
    *,
    einstieg: float | None,
    stop: float | None,
    tp1: float | None,
    tp3: float | None,
    lang: bool,
    note: str | None,
    setup: str | None,
    tabelle: dict[str, Quote],
    klasse: str | None = None,
) -> Erwartung:
    """Die Erwartung zu einem einzelnen Signal, fertig zum Anzeigen."""
    e = float(einstieg or 0.0)
    bis1 = _strecke(e, float(tp1), lang) if e and tp1 else None
    bis3 = _strecke(e, float(tp3), lang) if e and tp3 else None
    risiko = abs(_strecke(e, float(stop), lang) or 0.0) if e and stop else None

    q = passende(tabelle, note=note, setup=setup, klasse=klasse)
    saetze: list[str] = []

    if bis1 is not None:
        teil = f"Wenn es aufgeht: {_de(bis1, vorzeichen=True)} % bis zum ersten Ziel"
        if bis3 is not None:
            teil += f", {_de(bis3, vorzeichen=True)} % wenn es bis zum letzten läuft"
        if risiko:
            teil += f". Wenn nicht: \u2212{_de(risiko)} % bis zum Stop"
        saetze.append(teil + ".")

    if q is None:
        saetze.append(
            "Wie oft so etwas bisher aufging, steht noch nicht fest — dafür sind zu "
            "wenige Signale dieser Art abgeschlossen."
        )
    else:
        anteil = round(q.tp1 * q.n)
        verb = "erreichte" if anteil == 1 else "erreichten"
        saetze.append(
            f"Von {q.n} abgeschlossenen {q.basis} {verb} {anteil} das erste Ziel "
            f"({q.tp1 * 100:.0f} %), {q.stop * 100:.0f} % liefen in den Stop."
        )
        if q.schnitt_r < 0:
            saetze.append(
                f"Im Schnitt kostete ein solches Signal bisher {_de(q.schnitt_r, 2)} R \u2014 "
                "die Vergangenheit spricht hier also gegen den Trade."
            )
        elif q.schnitt_r > 0:
            saetze.append(
                f"Im Schnitt brachte ein solches Signal bisher "
                f"{_de(q.schnitt_r, 2, vorzeichen=True)} R."
            )
        if not q.belastbar:
            saetze.append(
                f"Achtung: {q.n} Fälle sind wenig. Die Quote kann sich mit ein paar "
                "weiteren Trades deutlich verschieben."
            )
        saetze.append("Das ist gezählte Vergangenheit, keine Vorhersage für diesen Trade.")

    return Erwartung(
        bis_tp1_pct=bis1,
        bis_tp3_pct=bis3,
        risiko_pct=risiko,
        quote=q,
        saetze=tuple(saetze),
    )


__all__ = [
    "BELASTBAR_AB",
    "MIN_FAELLE",
    "ZIELE",
    "Erwartung",
    "Quote",
    "passende",
    "quoten",
    "rechne",
]
