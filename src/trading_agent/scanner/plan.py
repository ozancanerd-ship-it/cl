"""Handelsplan — was nach dem Einstieg passiert. Der Teil, an dem Trades sterben.

WARUM ES DAS BRAUCHT

Bis hierher beantwortet das System die Frage „was kaufen und wo einsteigen". Das ist der
kleinere Teil des Handwerks. Der groessere ist: wann nehme ich Gewinn mit, wann ziehe ich
den Stop nach, wann steige ich aus, obwohl das Ziel nicht erreicht ist. Ohne diese Regeln
bekommt man ein gutes Signal und trotzdem ein schlechtes Ergebnis.

Die Schritte beschreiben GENAU die Regeln, nach denen die Wachliste klingelt und die
Bilanz zaehlt — nicht mehr und nicht weniger:

* Position in Drittel teilen. Erstes Drittel am ersten Ziel, zweites am zweiten,
  letztes am dritten.
* Nach dem ersten Ziel Stop auf Einstand, nach dem zweiten auf Ziel 1.
* Vorzeitig raus nur aus zwei Gruenden: Stop beruehrt, oder die Analyse dreht in die
  Gegenrichtung (Alarm „AUSSTEIGEN"). Die Ausstiegs-Studie (docs/AUSSTIEG-STUDIE-2026-10.md)
  hat diese zweite Regel gemessen: sie kostet nichts.
* Risiko ueber rund 10 % vom Einstiegskurs ist kein Swing-Trade mehr, sondern eine Wette.

Bis 01.10. stand hier mehr: ein Trailing-Stop von 2 ATR fuer das letzte Drittel, ein
Zeit-Stop nach 12 Tagen und „Rest reduzieren bei einer Gegenkerze". Keine dieser Regeln
hat je einen Alarm ausgeloest, keine wurde gemessen, und „reduzieren" ist genau der
Teilverkaufs-Rat, den die Gegenthese-Studie verworfen hat. Ein Plan, der etwas anderes
sagt als die Alarme, ist schlechter als keiner — man weiss nicht, welchem man folgt.

ZIELE AUS DER STRUKTUR ODER AUS DEM RISIKO

Die Liquiditaetsziele aus dem Chart sind das Erste, was zaehlt — dorthin laeuft der Kurs
tatsaechlich. Aber ein „Ziel", das ein Drittel des Stop-Abstands entfernt liegt, ist keins.
Deshalb: Strukturziel nehmen, wenn es weit genug ist, sonst das Vielfache des Risikos.
Damit steht TP1 nie naeher als 1 R, und die Rechnung im Plan stimmt mit dem ueberein, was
auf dem Chart passiert.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: Vielfache des Risikos, an denen die Teilziele mindestens liegen.
R_ZIELE = (1.0, 2.0, 3.5)
#: Ueber diesem Anteil des Einstiegskurses ist der Stop zu weit fuer einen Swing-Trade.
MAX_RISIKO_PCT = 10.0
#: Wie viel weiter als die Untergrenze ein Strukturziel hoechstens liegen darf, in R.
#: Alles dahinter ist zu weit weg, um noch ein Ziel dieses Trades zu sein.
ZIEL_SPIELRAUM_R = 1.5


@dataclass(frozen=True, slots=True)
class Handelsplan:
    einstieg: float
    stop: float
    tp1: float
    tp2: float
    tp3: float
    #: Risiko je Stueck, in Kurseinheiten.
    r: float
    #: Risiko in Prozent vom Einstieg — die Groesse, an der die Position haengt.
    risiko_pct: float
    #: Chance-Risiko bis TP2.
    crv: float
    #: Schritte im Klartext, in der Reihenfolge, in der sie eintreten.
    schritte: tuple[str, ...]
    #: Was den Trade beendet, bevor ein Ziel erreicht ist.
    ausstiege: tuple[str, ...]
    #: Wenn gesetzt: der Plan taugt nicht, und das ist der Grund.
    untauglich: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "einstieg": self.einstieg,
            "stop": self.stop,
            "tp1": self.tp1,
            "tp2": self.tp2,
            "tp3": self.tp3,
            "r": self.r,
            "risiko_pct": round(self.risiko_pct, 2),
            "crv": round(self.crv, 2),
            "schritte": list(self.schritte),
            "ausstiege": list(self.ausstiege),
            "untauglich": self.untauglich,
        }


def _fmt(x: float, bezug: float | None = None) -> str:
    """Preis mit deutschem Komma und so vielen Stellen, wie die App sie zeigt.

    Vorher ``:.4g`` — daraus wurde „59.56" und „0.805" mitten in einer Seite mit „59,56".
    ``bezug``: die Stellenzahl richtet sich nach diesem Preis (fuer 1 R = 0,81 bei einem
    Kurs von 58, nicht 0,80500).
    """
    a = abs(bezug if bezug is not None else x)
    n = 2 if a >= 10 else 4 if a >= 1 else 5 if a >= 0.01 else 8
    roh = f"{x:,.{n}f}"
    return roh.replace(",", "\u202f").replace(".", ",").replace("\u202f", ".")


def _zahl(x: float, n: int = 1) -> str:
    return f"{x:.{n}f}".replace(".", ",")


def baue_plan(
    *,
    einstieg: float,
    stop: float,
    lang: bool,
    strukturziele: list[float] | tuple[float, ...] = (),
    atr: float = 0.0,
) -> Handelsplan | None:
    """Aus Einstieg, Stop und den Strukturzielen einen ausfuehrbaren Plan machen.

    ``strukturziele`` sind die Liquiditaetsziele aus dem Chart, aufsteigend nach
    Entfernung. Sie werden genommen, wo sie weit genug liegen, sonst tritt das
    Vielfache des Risikos an ihre Stelle.
    """
    if einstieg <= 0 or stop <= 0:
        return None
    r = abs(einstieg - stop)
    if r <= 0:
        return None
    risiko_pct = r / einstieg * 100.0

    richtung = 1.0 if lang else -1.0
    passende = [z for z in strukturziele if z > 0 and (z > einstieg if lang else z < einstieg)]
    passende.sort(key=lambda z: abs(z - einstieg))

    # Untergrenze je Ziel: das Vielfache des Risikos UND ein Mindestabstand zum
    # vorigen Ziel. Ohne die zweite Bedingung koennen zwei Ziele zusammenfallen oder
    # sich ueberholen — beim ersten Lauf lag TP3 unter TP2, weil das Strukturziel
    # schon von TP2 verbraucht war und der Rueckfallwert dahinter lag.
    ziele: list[float] = []
    for mindest_r in R_ZIELE:
        boden = einstieg + richtung * mindest_r * r
        if ziele:
            abstand = ziele[-1] + richtung * 0.5 * r
            boden = max(boden, abstand) if lang else min(boden, abstand)
        # Ein Strukturziel zaehlt nur, wenn es in der Naehe der Untergrenze liegt.
        # Sonst passiert das hier: die naechste Liquiditaet liegt 100 % entfernt, und
        # TP2 wuerde dorthin gesetzt — ein Chance-Risiko von 1:25 auf dem Papier, das
        # in der Wirklichkeit nie eintritt. Liegt nichts Passendes in Reichweite, ist
        # das Vielfache des Risikos das ehrlichere Ziel.
        obergrenze = einstieg + richtung * (mindest_r + ZIEL_SPIELRAUM_R) * r
        kandidat = None
        for z in passende:
            weit_genug = z >= boden if lang else z <= boden
            nicht_zu_weit = z <= obergrenze if lang else z >= obergrenze
            if weit_genug and nicht_zu_weit:
                kandidat = z
                break
        ziele.append(kandidat if kandidat is not None else boden)

    tp1, tp2, tp3 = ziele
    r1 = abs(tp1 - einstieg) / r
    crv = abs(tp2 - einstieg) / r
    r3 = abs(tp3 - einstieg) / r

    untauglich = None
    if risiko_pct > MAX_RISIKO_PCT:
        untauglich = (
            f"Der Stop liegt {risiko_pct:.1f} % vom Einstieg entfernt. Ueber "
            f"{MAX_RISIKO_PCT:.0f} % ist das kein Swing-Trade mehr — die Position "
            "muesste so klein sein, dass sich der Aufwand nicht lohnt."
        )

    # Bei einem Short wird nicht „verkauft", sondern geschlossen: direkt gehalten heisst
    # das zurueckkaufen, ueber einen Short-Schein den Schein verkaufen (01.10., MDLZ).
    tun = "verkaufen" if lang else "schliessen"
    wie = (
        ""
        if lang
        else " (direkt gehalten: zurueckkaufen; ueber einen Short-Schein: den Schein-Anteil "
        "verkaufen)"
    )
    schritte = (
        f"Einstieg bei {_fmt(einstieg)} — die Position in drei gleiche Teile denken.",
        f"Stop bei {_fmt(stop)}. Das ist 1 R = {_fmt(r, einstieg)} ({_zahl(risiko_pct)} % vom "
        "Einstieg). Alles danach wird in R gerechnet, nicht in Euro.",
        f"Ziel 1 bei {_fmt(tp1)} ({_zahl(r1)} R): erstes Drittel {tun}{wie}, Stop auf den "
        f"Einstieg {_fmt(einstieg)} — ab hier kann der Trade nichts mehr kosten.",
        f"Ziel 2 bei {_fmt(tp2)} ({_zahl(crv)} R): zweites Drittel {tun}, Stop auf "
        f"Ziel 1 ({_fmt(tp1)}) nachziehen.",
        f"Ziel 3 bei {_fmt(tp3)} ({_zahl(r3)} R): den Rest {tun}. Der Trade ist fertig.",
    )

    ausstiege = (
        f"Der Kurs beruehrt den Stop bei {_fmt(stop)} (nach Ziel 1 den Einstand, nach "
        "Ziel 2 das Ziel 1) — raus, keine Diskussion.",
        "Die Analyse dreht in die Gegenrichtung (Strukturbruch) — der Alarm sagt "
        "AUSSTEIGEN, dann raus zum Marktkurs, auch wenn der Stop noch nicht erreicht ist.",
    )

    return Handelsplan(
        einstieg=einstieg,
        stop=stop,
        tp1=tp1,
        tp2=tp2,
        tp3=tp3,
        r=r,
        risiko_pct=risiko_pct,
        crv=crv,
        schritte=schritte,
        ausstiege=ausstiege,
        untauglich=untauglich,
    )


#: Oeffentlicher Name fuer andere Module (Analyse-Saetze): derselbe Preis-Formatierer.
preis_de = _fmt

__all__ = [
    "MAX_RISIKO_PCT",
    "R_ZIELE",
    "ZIEL_SPIELRAUM_R",
    "Handelsplan",
    "baue_plan",
    "preis_de",
]
