"""Die Notenskala — A+ bis NO_TRADE, und warum sie nicht nur aus dem Score kommt.

Ozans Korrektur woertlich: „Du bist aktuell wieder zu streng mit A oder A+ = Trade,
alles andere = NO TRADE. Das passt nicht zu unserem gewuenschten aggressiven
Swing-Trading-Profil." Und: „Optimiere das System nicht darauf, moeglichst wenige
Trades zu finden."

Die alte Logik hatte genau diesen Fehler. Sie kannte drei Stufen und liess alles
zwischen „sehr gut" und „nichts" verschwinden. Ein Chart mit Score 58 und einem
Chance-Risiko-Verhaeltnis von 1:2,6 landete auf WATCH und war damit unsichtbar,
obwohl er handelbar ist.

DREI GROESSEN, NICHT EINE

* **Score** — wie viel am Chart zusammenpasst (0–100 aus ``chart_score``).
* **Chance-Risiko-Verhaeltnis** — Weg zum Ziel gegen Weg zur Invalidierung.
* **Erwartete Bewegung** — wie gross der Move ueberhaupt sein kann, in Prozent.

Die dritte ist die, die vorher fehlte. Ein sauberer Chart mit 2 % Luft bis zum Ziel ist
fuer aggressives Swing-Trading uninteressant; ein etwas unruhigerer Chart mit 18 % Luft
und 1:3 ist es sehr wohl. Genau das steht in Ozans Vorgabe: „Ein Setup mit moeglichem
Move von +2 % ist nicht automatisch interessant."

DREI PROFILE

``KONSERVATIV`` verlangt mehr von allem. ``AGGRESSIV`` laesst mehr durch, ohne die
Risikoseite anzufassen — das ist der springende Punkt: **mehr Risikobereitschaft heisst
nicht, den Stop wegzulassen.** Die Invalidierung bleibt in jeder Stufe Pflicht; ohne sie
gibt es keine Note ausser NO_TRADE.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

#: Die Leiter, von oben nach unten. Reihenfolge ist bedeutsam (Vergleich per Index).
NOTEN: tuple[str, ...] = ("A_PLUS", "A", "A_MINUS", "B_PLUS", "B", "WATCH", "NO_TRADE")

#: Kurzform fuer Anzeige.
NOTE_KURZ: dict[str, str] = {
    "A_PLUS": "A+",
    "A": "A",
    "A_MINUS": "A−",
    "B_PLUS": "B+",
    "B": "B",
    "WATCH": "WATCH",
    "NO_TRADE": "—",
}

#: Ab welcher Note wir von einer handelbaren Gelegenheit sprechen.
HANDELBAR: frozenset[str] = frozenset({"A_PLUS", "A", "A_MINUS", "B_PLUS", "B"})


class Profil(StrEnum):
    KONSERVATIV = "konservativ"
    AUSGEWOGEN = "ausgewogen"
    AGGRESSIV = "aggressiv"


@dataclass(frozen=True, slots=True)
class Stufe:
    """Eine Notenstufe: alle drei Bedingungen muessen erfuellt sein."""

    note: str
    min_score: float
    min_rr: float
    min_move_pct: float


@dataclass(frozen=True, slots=True)
class Notenschema:
    """Die Leiter eines Profils, plus die Regeln, die eine Note anheben oder senken."""

    profil: Profil
    stufen: tuple[Stufe, ...]
    #: Ab dieser erwarteten Bewegung (%) mit solidem CRV wird mindestens `grosser_move_note`
    #: vergeben — ein sehr grosser Move ist ein eigenes Argument.
    grosser_move_pct: float
    grosser_move_min_rr: float
    grosser_move_note: str
    #: Unterhalb dieser Bewegung ist die Sache fuer Swing-Trading uninteressant, egal wie
    #: sauber der Chart aussieht. Deckel, keine Ablehnung: es bleibt WATCH.
    winziger_move_pct: float


def _leiter(
    score: tuple[float, ...], rr: tuple[float, ...], move: tuple[float, ...]
) -> tuple[Stufe, ...]:
    return tuple(
        Stufe(note=n, min_score=s, min_rr=r, min_move_pct=m)
        for n, s, r, m in zip(NOTEN[:5], score, rr, move, strict=True)
    )


SCHEMA: dict[Profil, Notenschema] = {
    # Verlangt Ausrichtung UND Weg UND Puffer. Wenige Treffer, dafuer sehr saubere.
    Profil.KONSERVATIV: Notenschema(
        profil=Profil.KONSERVATIV,
        stufen=_leiter(
            score=(74.0, 66.0, 60.0, 54.0, 48.0),
            rr=(3.0, 2.5, 2.2, 1.9, 1.6),
            move=(8.0, 6.0, 5.0, 4.0, 3.0),
        ),
        grosser_move_pct=25.0,
        grosser_move_min_rr=3.0,
        grosser_move_note="A_MINUS",
        winziger_move_pct=2.5,
    ),
    Profil.AUSGEWOGEN: Notenschema(
        profil=Profil.AUSGEWOGEN,
        stufen=_leiter(
            score=(70.0, 61.0, 55.0, 48.0, 41.0),
            rr=(2.6, 2.1, 1.8, 1.5, 1.3),
            move=(6.0, 4.5, 3.5, 2.5, 2.0),
        ),
        grosser_move_pct=18.0,
        grosser_move_min_rr=2.2,
        grosser_move_note="A_MINUS",
        winziger_move_pct=1.5,
    ),
    # Ozans Standard. Laesst mehr durch, verlangt aber weiterhin, dass das Ziel weiter
    # weg ist als der Stop — sonst waere es kein aggressiver Trade, sondern ein schlechter.
    Profil.AGGRESSIV: Notenschema(
        profil=Profil.AGGRESSIV,
        stufen=_leiter(
            score=(66.0, 57.0, 50.0, 43.0, 36.0),
            rr=(2.2, 1.8, 1.5, 1.3, 1.15),
            move=(5.0, 3.5, 2.5, 1.8, 1.2),
        ),
        grosser_move_pct=12.0,
        grosser_move_min_rr=2.0,
        grosser_move_note="A_MINUS",
        winziger_move_pct=1.0,
    ),
}

#: Standardprofil des Systems. Ozans ausdrueckliche Vorgabe.
STANDARD = Profil.AGGRESSIV


@dataclass(frozen=True, slots=True)
class Bewertung:
    note: str
    profil: Profil
    begruendung: str
    #: Was die Note verhindert hat, falls sie nicht die hoechste ist — die konkrete Groesse.
    bremse: str | None


def _hoehere(a: str, b: str) -> str:
    return a if NOTEN.index(a) <= NOTEN.index(b) else b


def benote(
    *,
    score: float,
    rr: float | None,
    move_pct: float | None,
    hat_invalidierung: bool,
    profil: Profil | str = STANDARD,
) -> Bewertung:
    """Note aus Score, Chance-Risiko-Verhaeltnis und erwarteter Bewegung.

    Ohne Invalidierung gibt es nie eine handelbare Note. Das ist der eine Punkt, den
    auch das aggressivste Profil nicht aufweicht: mehr Risiko heisst groessere Position
    oder weiterer Stop — nicht kein Stop.
    """
    p = Profil(profil)
    schema = SCHEMA[p]

    if not hat_invalidierung or rr is None or rr <= 0:
        return Bewertung(
            note="NO_TRADE",
            profil=p,
            begruendung="keine belastbare Invalidierung — ohne Stop keine Position",
            bremse="Invalidierung",
        )

    m = move_pct if move_pct is not None else 0.0

    note = "NO_TRADE"
    bremse: str | None = None
    for stufe in schema.stufen:
        if score >= stufe.min_score and rr >= stufe.min_rr and m >= stufe.min_move_pct:
            note = stufe.note
            break
        # Merken, woran die erste (beste) verpasste Stufe scheiterte.
        if bremse is None:
            fehlt = []
            if score < stufe.min_score:
                fehlt.append(f"Score {score:.0f} < {stufe.min_score:.0f}")
            if rr < stufe.min_rr:
                fehlt.append(f"CRV 1:{rr:.2f} < 1:{stufe.min_rr:.2f}")
            if m < stufe.min_move_pct:
                fehlt.append(f"Bewegung {m:.1f} % < {stufe.min_move_pct:.1f} %")
            bremse = ", ".join(fehlt)

    # Ein sehr grosser Move mit solidem CRV ist ein Argument fuer sich.
    if m >= schema.grosser_move_pct and rr >= schema.grosser_move_min_rr:
        note = _hoehere(note, schema.grosser_move_note)

    # Zu wenig Luft fuer Swing-Trading: nie besser als WATCH, egal wie sauber.
    if m < schema.winziger_move_pct and NOTEN.index(note) < NOTEN.index("WATCH"):
        note = "WATCH"
        bremse = f"Bewegung nur {m:.1f} % — zu wenig fuer einen Swing"

    # Nicht handelbar, aber beobachtenswert: Score da, CRV noch nicht.
    if note == "NO_TRADE" and (score >= 35.0 or rr >= 1.5):
        note = "WATCH"

    if note in HANDELBAR:
        begruendung = (
            f"Score {score:.0f}, CRV 1:{rr:.2f}, erwartete Bewegung {m:.1f} % "
            f"— reicht fuer {NOTE_KURZ[note]} im Profil {p.value}"
        )
        if note != "A_PLUS":
            begruendung += f". Fuer mehr fehlt: {bremse}" if bremse else ""
    elif note == "WATCH":
        begruendung = f"beobachten — {bremse or 'noch keine handelbare Kombination'}"
    else:
        begruendung = bremse or "nichts, was zusammenpasst"

    return Bewertung(note=note, profil=p, begruendung=begruendung, bremse=bremse)


def confidence(
    *,
    einigkeit: float,
    score: float,
    faktoren_erfuellt: float,
    daten_ok: bool,
    warnungen: int = 0,
) -> float:
    """Wie einheitlich das Bild ist — 0..1.

    **Nicht die Trefferwahrscheinlichkeit.** Das hier misst, wie sehr die Bausteine in
    dieselbe Richtung zeigen, nicht wie oft so ein Bild historisch aufgegangen ist. Die
    zweite Frage ist offen und wird getrennt geprueft — sie mit dieser Zahl zu
    beantworten waere genau die Art Selbsttaeuschung, die wir vermeiden wollen.

    Drei Anteile: die Einigkeit der Zeitebenen (die wichtigste Groesse), die Hoehe des
    Scores und wie viele Einzelfaktoren wirklich erfuellt sind. Warnungen ziehen ab,
    schlechte Datenqualitaet deckelt hart.
    """
    e = max(0.0, min(1.0, einigkeit))
    sc = max(0.0, min(1.0, score / 100.0))
    fe = max(0.0, min(1.0, faktoren_erfuellt))
    basis = 0.45 * e + 0.35 * sc + 0.20 * fe
    if not daten_ok:
        # Gedaempft, nicht halbiert. Bei Aktien ist die Reihe ausserhalb der Boersenzeit
        # regelmaessig "veraltet" — das ist Wochenende, kein Datenproblem. Der Grund steht
        # als Warnung daneben, statt die Zahl heimlich zu zerstoeren.
        basis *= 0.8
    basis -= 0.05 * max(0, warnungen)
    return round(max(0.05, min(0.97, basis)), 3)


__all__ = [
    "HANDELBAR",
    "NOTEN",
    "NOTE_KURZ",
    "SCHEMA",
    "STANDARD",
    "Bewertung",
    "Notenschema",
    "Profil",
    "Stufe",
    "benote",
    "confidence",
]
