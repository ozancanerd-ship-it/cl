"""Einstiegs-Bestätigung — der Preis allein reicht nicht.

DER BEFUND, DER DAZU GEFÜHRT HAT

Die Auswertung der echten Wachliste am 11. September 2026 (``scanner/performance.py``)
ergab über 58 abgeschlossene Signale −23,5 R. Die aufschlussreiche Zahl war aber nicht
die Summe, sondern diese:

    29 von 58 Trades — die Hälfte — kamen nie auch nur 0,3 R ins Plus.

Ein Trade, der nie im Plus war, hat kein Ziel-Problem und kein Stop-Problem. Er hat ein
**Einstiegs-Problem**: er lief ab der ersten Minute gegen uns. Und das ist genau das,
was passiert, wenn ein System einsteigt, sobald ein Kurs eine Marke *berührt*.

Denn eine berührte Marke sagt nur, dass der Kurs dort vorbeikam. Ob er dort gedreht hat
oder einfach durchgefallen ist, sagt sie nicht — und zwischen diesen beiden Fällen liegt
der ganze Unterschied. Wer bei Berührung kauft, kauft in jedem Wasserfall mit, weil ein
Wasserfall zwangsläufig durch jede Marke darunter hindurchgeht.

WAS DIESES MODUL VERLANGT

Drei Dinge, alle auf **geschlossenen** Kerzen. Ein laufender Kerzenkörper ist eine
Momentaufnahme und kann sich bis zum Schluss noch umdrehen.

1. **Die Marke muss erreicht worden sein.** Notwendig, aber eben nicht hinreichend.
2. **Die letzte geschlossene Kerze muss in Handelsrichtung schließen** — und zwar auf
   der richtigen Seite der Marke und im starken Teil ihrer eigenen Spanne. Eine Kerze,
   die die Marke antippt und am Tief schließt, ist keine Umkehr, sondern ein Durchfall.
3. **Der Kurs darf nicht zu weit unter die Marke gefallen sein.** Wer 1,5 ATR unter
   seinem Einstieg „bestätigt" kauft, kauft nicht den Rücksetzer, sondern den Bruch.

Erfüllt eine Kerze zusätzlich die strenge Bedingung — Schluss über dem Hoch der
Vorkerze —, gilt die Bestätigung als *stark*. Der Unterschied wird nicht in eine
Ja/Nein-Entscheidung eingerechnet, sondern mitgeführt: er gehört in die Statistik,
damit sich später zeigen lässt, ob die strenge Variante tatsächlich besser läuft.

WAS DAS KOSTET

Bestätigung kostet Einstiegskurs. Wer wartet, bis die Kerze schließt, steigt schlechter
ein als jemand, der die Marke abgreift — und verpasst die Trades, die ohne Rücksicht
davonlaufen. Das ist der bewusst gezahlte Preis dafür, in der Hälfte der Fälle *nicht*
dabei zu sein, in denen der Kurs einfach weiterfällt.

**Kein Beleg.** Dass Bestätigung hilft, ist plausibel, steht so in der Praxisliteratur
und passt zum Befund oben. Bewiesen ist es damit nicht — das zeigt erst der nächste
Vorlauf, und ``performance.py`` wird es zeigen, egal wie es ausgeht.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: Der Schluss muss mindestens in diesem Anteil der Kerzenspanne liegen — von der
#: Handelsrichtung aus gesehen. Etwas über der Hälfte, damit eine Kerze, die genau in
#: der Mitte schließt, noch nicht als Umkehr durchgeht: die sagt nichts aus.
MIN_SCHLUSSKRAFT = 0.55

#: So weit darf der Kurs höchstens unter die Marke gefallen sein (in ATR), damit es
#: noch ein Rücksetzer heißt und nicht ein Bruch.
MAX_UNTERSCHREITUNG_ATR = 1.2

#: So viele geschlossene Kerzen nach der Berührung wird auf Bestätigung gewartet.
#: Danach ist der Zug abgefahren: der Plan wurde für eine Lage gemacht, die es nicht
#: mehr gibt.
VERFALL_KERZEN = 8

#: Wie viele Kerzen zurück überhaupt gesehen wird.
FENSTER = 12


@dataclass(frozen=True, slots=True)
class Bestaetigung:
    """Das Urteil über einen Einstieg, mit dem Grund im Klartext."""

    #: Darf eingestiegen werden?
    ja: bool
    #: Wurde die Marke überhaupt erreicht?
    beruehrt: bool
    #: Schluss über dem Hoch der Vorkerze — die strenge Variante.
    stark: bool
    #: Wenn gesetzt: die Idee ist nicht mehr gültig, nicht nur unbestätigt.
    hinfaellig: str | None
    grund: str
    #: Kurs, zu dem eingestiegen würde (Schluss der bestätigenden Kerze).
    kurs: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "ja": self.ja,
            "beruehrt": self.beruehrt,
            "stark": self.stark,
            "hinfaellig": self.hinfaellig,
            "grund": self.grund,
            "kurs": self.kurs,
        }


def _schlusskraft(bar: Any, lang: bool) -> float | None:
    hi, lo, cl = float(bar.high), float(bar.low), float(bar.close)
    if hi <= lo:
        return None
    lage = (cl - lo) / (hi - lo)
    return lage if lang else 1.0 - lage


def pruefe_einstieg(
    kerzen: list[Any],
    *,
    einstieg: float,
    lang: bool,
    atr: float = 0.0,
) -> Bestaetigung:
    """Darf zum geplanten Einstieg gekauft (bzw. verkauft) werden?

    ``kerzen`` sind **geschlossene** Kerzen, älteste zuerst — üblicherweise die
    Ausführungsebene des Setups (M15 oder H1). Die letzte davon ist die, auf die es
    ankommt.

    Ohne ausreichend Kerzen gibt es kein Urteil und damit kein „ja". Das ist Absicht:
    im Zweifel nicht einsteigen ist die Variante, die nichts kostet.
    """
    if einstieg <= 0 or len(kerzen) < 2:
        return Bestaetigung(
            ja=False,
            beruehrt=False,
            stark=False,
            hinfaellig=None,
            grund="zu wenige geschlossene Kerzen, um etwas zu bestaetigen",
        )

    fenster = kerzen[-FENSTER:]
    letzte = fenster[-1]
    vorige = fenster[-2]

    # 1 — Wurde die Marke erreicht?
    extrem = min(float(b.low) for b in fenster) if lang else max(float(b.high) for b in fenster)
    beruehrt = extrem <= einstieg if lang else extrem >= einstieg
    if not beruehrt:
        abstand = abs(extrem - einstieg) / einstieg * 100.0
        return Bestaetigung(
            ja=False,
            beruehrt=False,
            stark=False,
            hinfaellig=None,
            grund=f"Marke noch nicht erreicht — es fehlen {abstand:.2f} %",
        )

    # 2 — Ist der Kurs durch die Marke durchgefallen statt an ihr gedreht?
    if atr > 0:
        durchfall = (einstieg - extrem) if lang else (extrem - einstieg)
        if durchfall > MAX_UNTERSCHREITUNG_ATR * atr:
            wohin = "unter" if lang else "ueber"
            return Bestaetigung(
                ja=False,
                beruehrt=True,
                stark=False,
                hinfaellig=(
                    f"Der Kurs ist {durchfall / atr:.1f} ATR {wohin} die Marke gelaufen. "
                    "Das ist kein Ruecksetzer mehr, sondern ein Bruch — der Plan gilt nicht."
                ),
                grund="durch die Marke gefallen",
            )

    # 3 — Schließt die letzte Kerze auf der richtigen Seite, und zwar mit Nachdruck?
    #
    # Bewusst NICHT gefordert: dass die Kerze grün ist. Die klassische Umkehrkerze am
    # Tief — langer Docht nach unten, kleiner Körper — ist oft rot und trotzdem genau
    # das Signal, um das es geht. Was zählt, ist, wo sie schließt: über der Marke und
    # im oberen Teil ihrer eigenen Spanne. Eine Farbabfrage würde den halben Sinn des
    # Moduls wegwerfen.
    schluss = float(letzte.close)
    auf_seite = schluss > einstieg if lang else schluss < einstieg
    kraft = _schlusskraft(letzte, lang)
    stark = schluss > float(vorige.high) if lang else schluss < float(vorige.low)

    # 4 — Ist der Zug abgefahren? Zählt ab der Kerze, die die Marke zuletzt berührt hat.
    seit = 0
    for i, b in enumerate(reversed(fenster)):
        traf = float(b.low) <= einstieg if lang else float(b.high) >= einstieg
        if traf:
            seit = i
            break
    if seit >= VERFALL_KERZEN:
        return Bestaetigung(
            ja=False,
            beruehrt=True,
            stark=False,
            hinfaellig=(
                f"Die Marke wurde vor {seit} Kerzen beruehrt und nie bestaetigt. "
                "Der Plan wurde fuer eine Lage gemacht, die es so nicht mehr gibt."
            ),
            grund="Bestaetigung ausgeblieben",
        )

    if not auf_seite:
        wo = "unter" if lang else "ueber"
        return Bestaetigung(
            ja=False,
            beruehrt=True,
            stark=False,
            hinfaellig=None,
            grund=f"Marke erreicht, aber die Kerze schliesst noch {wo} der Marke",
        )
    if kraft is None or kraft < MIN_SCHLUSSKRAFT:
        return Bestaetigung(
            ja=False,
            beruehrt=True,
            stark=stark,
            hinfaellig=None,
            grund=(
                "Marke erreicht, aber der Schluss liegt im schwachen Teil der Kerze — "
                "das ist ein Docht, keine Umkehr"
            ),
        )

    art = (
        "Schluss ueber dem Hoch der Vorkerze"
        if stark
        else f"Schluss auf der richtigen Seite, im starken Teil der Kerze ({kraft:.0%})"
    )
    return Bestaetigung(
        ja=True,
        beruehrt=True,
        stark=stark,
        hinfaellig=None,
        grund=f"bestaetigt: {art}",
        kurs=schluss,
    )


__all__ = [
    "FENSTER",
    "MAX_UNTERSCHREITUNG_ATR",
    "MIN_SCHLUSSKRAFT",
    "VERFALL_KERZEN",
    "Bestaetigung",
    "pruefe_einstieg",
]
