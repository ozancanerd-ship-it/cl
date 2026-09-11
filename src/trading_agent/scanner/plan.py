"""Handelsplan — was nach dem Einstieg passiert. Der Teil, an dem Trades sterben.

WARUM ES DAS BRAUCHT

Bis hierher beantwortet das System die Frage „was kaufen und wo einsteigen". Das ist der
kleinere Teil des Handwerks. Der groessere ist: wann nehme ich Gewinn mit, wann ziehe ich
den Stop nach, wann steige ich aus, obwohl das Ziel nicht erreicht ist. Ohne diese Regeln
bekommt man ein gutes Signal und trotzdem ein schlechtes Ergebnis.

Die Regeln hier sind das, was in der Praxisliteratur uebereinstimmend beschrieben wird:

* Position in Drittel teilen. Erstes Drittel am ersten Ziel, zweites am zweiten,
  letztes Drittel laeuft mit nachgezogenem Stop.
* Nach dem ersten Ziel Stop auf Einstand. Ab da kann der Trade nichts mehr kosten.
* Nachziehen entweder unter jedes neue hoehere Tief oder 2 ATR unter das Hoch —
  nicht beides gleichzeitig, sonst wird man von der engeren Regel ausgestoppt.
* Zeit-Stop: passiert nach ein bis zwei Wochen nichts, ist das Kapital besser woanders.
* Risiko ueber rund 10 % vom Einstiegskurs ist kein Swing-Trade mehr, sondern eine Wette.

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
#: Nach so vielen Tagen ohne Fortschritt ist das Kapital woanders besser aufgehoben.
ZEIT_STOP_TAGE = 12
#: Abstand des nachgezogenen Stops zum Hoch, in ATR.
TRAIL_ATR = 2.0
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


def _fmt(x: float) -> str:
    if x >= 1000:
        return f"{x:,.2f}".replace(",", " ")
    if x >= 1:
        return f"{x:.4g}"
    return f"{x:.6g}"


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

    trail = (
        f"2 ATR ({_fmt(TRAIL_ATR * atr)}) unter dem hoechsten erreichten Kurs"
        if atr > 0 and lang
        else (
            f"2 ATR ({_fmt(TRAIL_ATR * atr)}) ueber dem tiefsten erreichten Kurs"
            if atr > 0
            else "unter jedem neuen hoeheren Tief"
        )
    )

    schritte = (
        f"Einstieg bei {_fmt(einstieg)} — ein Drittel, ein Drittel, ein Drittel geplant.",
        f"Stop bei {_fmt(stop)}. Das ist 1 R = {_fmt(r)} ({risiko_pct:.1f} % vom Einstieg). "
        "Alles danach wird in R gerechnet, nicht in Euro.",
        f"Erstes Drittel raus bei {_fmt(tp1)} ({r1:.1f} R). Danach Stop sofort auf Einstand "
        f"{_fmt(einstieg)} — ab hier kann der Trade nichts mehr kosten.",
        f"Zweites Drittel raus bei {_fmt(tp2)} ({crv:.1f} R). Stop auf {_fmt(tp1)} nachziehen.",
        f"Letztes Drittel laeuft. Stop nachziehen: {trail}. Ziel {_fmt(tp3)} ({r3:.1f} R), "
        "aber der Trailing-Stop entscheidet, nicht die Zahl.",
    )

    ausstiege = (
        f"Stop bei {_fmt(stop)} wird ausgeloest — Trade beendet, keine Diskussion.",
        f"Nach {ZEIT_STOP_TAGE} Tagen ohne Fortschritt raus. Kapital, das steht, "
        "kostet die naechste Gelegenheit.",
        "Gegenteilige Kerze auf der 4-Stunden-Ebene mit Volumen (bei Long: "
        "Umkehrkerze am Hoch) — Rest reduzieren, ohne auf den Stop zu warten.",
        "Bricht die Struktur in die Gegenrichtung, ist die These falsch. Raus, auch "
        "wenn der Stop noch nicht erreicht ist.",
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


__all__ = [
    "MAX_RISIKO_PCT",
    "R_ZIELE",
    "TRAIL_ATR",
    "ZEIT_STOP_TAGE",
    "ZIEL_SPIELRAUM_R",
    "Handelsplan",
    "baue_plan",
]
