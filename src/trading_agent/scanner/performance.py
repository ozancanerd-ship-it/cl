"""Performance — was die Signale tatsächlich gebracht haben, nicht was sie versprachen.

WARUM ES DAS BRAUCHT

Die Wachliste hält jedes Setup von der Aufnahme bis zum Ende fest. Damit liegt der
einzige ehrliche Prüfstein des Systems auf der Platte — und wurde bisher nirgends
ausgewertet. Ein Scanner, der jeden Tag neue Chancen ausruft und nie nachrechnet, ob
die alten aufgegangen sind, ist eine Meinungsmaschine.

Der erste Durchlauf über die echten Daten vom 11. September 2026 (58 abgeschlossene
Wachen) ergab **−24,5 R**. Das ist kein Randbefund, das ist das Ergebnis. Genau
deshalb steht diese Auswertung jetzt fest im System und nicht in einem einmaligen
Skript: eine Zahl, die man nur ansieht, wenn man sie sehen will, sieht sich niemand an.

WAS HIER GERECHNET WIRD — UND WAS NICHT

Alle Ergebnisse stehen in **R**, also in Vielfachen des eingegangenen Risikos. Das ist
die einzige Einheit, in der Trades über verschiedene Instrumente, Kurshöhen und
Positionsgrößen hinweg vergleichbar sind. Euro wären hier irreführend: ein Gewinn von
200 € sagt nichts, solange nicht dabeisteht, wie viel dafür riskiert wurde.

Zwei Ausstiegsregeln werden nebeneinander gerechnet:

* ``ganz`` — alles oder nichts, wie das System es bis zum 10. September gehandhabt hat.
* ``drittel`` — Drittel am ersten Ziel raus, danach Stop auf Einstand, wie
  ``scanner/plan.py`` es vorsieht.

Das ist **keine** Optimierung im Nachhinein. Die Drittel-Regel steht in der
Praxisliteratur, sie war vorher da, und sie wird hier nur auf dieselben Daten
angewandt, damit sichtbar wird, was der Unterschied ausmacht.

**Kein Beleg für einen Edge.** Das ist ein Vorlauf über wenige Wochen mit wenigen
Dutzend Trades, aus einem einzigen Marktabschnitt, überwiegend Krypto. Positive Zahlen
wären hier genauso wenig ein Beweis wie negative ein Todesurteil. Was es leistet: es
macht das Ergebnis unübersehbar, statt es dem Gefühl zu überlassen.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

#: Ausstiegsregeln, die nebeneinander gerechnet werden.
REGELN = ("ganz", "drittel")

#: Wie viel R ein Ziel einbringt. Entspricht ``plan.R_ZIELE``; hier noch einmal
#: eigenständig, damit die Auswertung nicht kippt, wenn am Plan geschraubt wird —
#: eine Statistik, deren Maßstab sich mitbewegt, misst nichts.
ZIEL_R = {"TP1": 1.0, "TP2": 2.0, "TP3": 3.5}

#: Zustände, ab denen ein Trade zu Ende ist.
ABGESCHLOSSEN = frozenset({"stop", "ziel_erreicht", "invalidiert", "abgelaufen"})

#: Unter so vielen abgeschlossenen Trades ist jede Kennzahl Rauschen. Die Zahlen
#: werden trotzdem ausgegeben — aber mit dieser Marke daneben.
GENUG = 30


@dataclass(frozen=True, slots=True)
class Ergebnis:
    """Ein abgeschlossener Trade, auf das Wesentliche reduziert."""

    instrument: str
    klasse: str
    note: str
    setup: str
    zustand: str
    erreicht: tuple[str, ...]
    #: Bestes und schlechtestes erreichtes Vielfaches des Risikos im Trade-Verlauf.
    mfe: float
    mae: float
    beendet: str
    r_ganz: float
    r_drittel: float


def _r_ganz(zustand: str, erreicht: tuple[str, ...], mfe: float) -> float:
    """Alles oder nichts: Ziel erreicht → der Gewinn, Stop → −1 R, sonst flat.

    ``invalidiert`` wird mit 0 bewertet, nicht mit dem tatsächlichen Stand beim
    Ausstieg. Das ist **zugunsten** des Systems gerechnet: in Wirklichkeit steht man
    beim Ausstieg auf Strukturbruch meistens leicht im Minus. Wer schönrechnet, soll
    es wenigstens in die Richtung tun, die das eigene Ergebnis schlechter aussehen
    lässt, nicht besser.
    """
    if zustand == "ziel_erreicht":
        hoechstes = max((ZIEL_R[t] for t in erreicht if t in ZIEL_R), default=mfe)
        return float(hoechstes)
    if zustand == "stop":
        return -1.0
    return 0.0


def _r_drittel(zustand: str, erreicht: tuple[str, ...]) -> float:
    """Drittel-Regel: je Ziel ein Drittel raus, nach dem ersten Ziel Stop auf Einstand.

    Der entscheidende Unterschied liegt nicht in den Teilgewinnen, sondern im Stop auf
    Einstand: ein Trade, der TP1 berührt und danach dreht, ist damit ein kleiner Gewinn
    statt eines vollen Verlusts. Genau diese Fälle machen in den echten Daten die
    Trefferquote aus.
    """
    teil = 1.0 / 3.0
    getroffen = [t for t in ("TP1", "TP2", "TP3") if t in erreicht]
    if not getroffen:
        return -1.0 if zustand == "stop" else 0.0
    gewinn = sum(teil * ZIEL_R[t] for t in getroffen)
    # Der Rest geht am Einstand raus — nach TP1 steht der Stop dort.
    return gewinn


def aus_wachliste(daten: dict[str, Any] | None) -> list[Ergebnis]:
    """Abgeschlossene Trades aus dem gespeicherten Wachlisten-Zustand."""
    if not daten:
        return []
    roh = daten.get("wachen") if isinstance(daten.get("wachen"), dict) else daten
    if not isinstance(roh, dict):
        return []
    aus: list[Ergebnis] = []
    for w in roh.values():
        if not isinstance(w, dict):
            continue
        zustand = str(w.get("zustand") or "")
        if zustand not in ABGESCHLOSSEN:
            continue
        erreicht = tuple(str(x) for x in (w.get("erreicht") or []))
        mfe = float(w.get("bestes_r") or 0.0)
        aus.append(
            Ergebnis(
                instrument=str(w.get("instrument") or "?"),
                klasse=str(w.get("klasse") or "?"),
                note=str(w.get("note") or "?"),
                setup=str(w.get("setup") or ""),
                zustand=zustand,
                erreicht=erreicht,
                mfe=mfe,
                mae=float(w.get("schlechtestes_r") or 0.0),
                beendet=str(w.get("zuletzt") or ""),
                r_ganz=_r_ganz(zustand, erreicht, mfe),
                r_drittel=_r_drittel(zustand, erreicht),
            )
        )
    aus.sort(key=lambda e: e.beendet)
    return aus


@dataclass(frozen=True, slots=True)
class Kennzahlen:
    anzahl: int
    treffer: int
    trefferquote: float | None
    summe_r: float
    erwartungswert: float | None
    profitfaktor: float | None
    groesster_gewinn: float
    groesster_verlust: float
    #: Tiefster Punkt der aufsummierten R-Kurve gegenüber ihrem bisherigen Hoch.
    max_rueckgang_r: float
    #: Längste Serie von Verlusten hintereinander.
    verlustserie: int
    belastbar: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "anzahl": self.anzahl,
            "treffer": self.treffer,
            "trefferquote": round(self.trefferquote, 4) if self.trefferquote is not None else None,
            "summe_r": round(self.summe_r, 2),
            "erwartungswert": (
                round(self.erwartungswert, 3) if self.erwartungswert is not None else None
            ),
            "profitfaktor": (
                round(self.profitfaktor, 2) if self.profitfaktor is not None else None
            ),
            "groesster_gewinn": round(self.groesster_gewinn, 2),
            "groesster_verlust": round(self.groesster_verlust, 2),
            "max_rueckgang_r": round(self.max_rueckgang_r, 2),
            "verlustserie": self.verlustserie,
            "belastbar": self.belastbar,
        }


def rechne(werte: list[float]) -> Kennzahlen:
    """Die Standardkennzahlen über eine Folge von R-Ergebnissen, in ihrer Reihenfolge."""
    n = len(werte)
    gewinne = [v for v in werte if v > 0]
    verluste = [v for v in werte if v < 0]
    summe = sum(werte)

    # Rückgang auf der aufsummierten Kurve — die Größe, an der ein Konto stirbt,
    # lange bevor der Erwartungswert sie einholt.
    spitze = 0.0
    stand = 0.0
    tiefster = 0.0
    serie = laufend = 0
    for v in werte:
        stand += v
        spitze = max(spitze, stand)
        tiefster = min(tiefster, stand - spitze)
        if v < 0:
            laufend += 1
            serie = max(serie, laufend)
        else:
            laufend = 0

    return Kennzahlen(
        anzahl=n,
        treffer=len(gewinne),
        trefferquote=(len(gewinne) / n) if n else None,
        summe_r=summe,
        erwartungswert=(summe / n) if n else None,
        profitfaktor=(sum(gewinne) / abs(sum(verluste))) if verluste else None,
        groesster_gewinn=max(gewinne, default=0.0),
        groesster_verlust=min(verluste, default=0.0),
        max_rueckgang_r=tiefster,
        verlustserie=serie,
        belastbar=n >= GENUG,
    )


@dataclass(frozen=True, slots=True)
class Bericht:
    erzeugt: str
    abgeschlossen: int
    offen: int
    je_regel: dict[str, Kennzahlen]
    je_note: dict[str, Kennzahlen]
    je_klasse: dict[str, Kennzahlen]
    je_setup: dict[str, Kennzahlen]
    mfe_schnitt: float | None
    mae_schnitt: float | None
    #: Anteil der Trades, die nie nennenswert ins Plus kamen. Die aussagekräftigste
    #: Einzelzahl: sie trennt ein Ziel-Problem von einem Einstiegs-Problem.
    nie_im_plus: float | None
    #: Jeder abgeschlossene Trade einzeln, jüngster zuerst. Ozan hat ausdrücklich danach
    #: gefragt: er will nicht nur die Summe sehen, sondern nachlesen können, wie die
    #: Signale ausgegangen sind, die ihm angezeigt wurden. Eine Kennzahl, die man nicht
    #: auf einzelne Fälle zurückführen kann, glaubt man oder nicht — mehr nicht.
    trades: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    saetze: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "erzeugt": self.erzeugt,
            "abgeschlossen": self.abgeschlossen,
            "offen": self.offen,
            "je_regel": {k: v.as_dict() for k, v in self.je_regel.items()},
            "je_note": {k: v.as_dict() for k, v in self.je_note.items()},
            "je_klasse": {k: v.as_dict() for k, v in self.je_klasse.items()},
            "je_setup": {k: v.as_dict() for k, v in self.je_setup.items()},
            "mfe_schnitt": round(self.mfe_schnitt, 2) if self.mfe_schnitt is not None else None,
            "mae_schnitt": round(self.mae_schnitt, 2) if self.mae_schnitt is not None else None,
            "nie_im_plus": round(self.nie_im_plus, 3) if self.nie_im_plus is not None else None,
            "trades": [dict(t) for t in self.trades],
            "saetze": list(self.saetze),
        }


#: Ab hier gilt ein Trade als „war mal im Plus". Bewusst niedrig: es geht nicht um
#: Gewinn, sondern um die Frage, ob der Einstieg überhaupt je funktioniert hat.
IM_PLUS_AB = 0.3


def _gruppiere(
    ergebnisse: list[Ergebnis], schluessel: Any, regel: str = "ganz"
) -> dict[str, Kennzahlen]:
    eimer: dict[str, list[float]] = {}
    for e in ergebnisse:
        k = str(schluessel(e) or "?")
        if not k or k == "?":
            continue
        eimer.setdefault(k, []).append(e.r_ganz if regel == "ganz" else e.r_drittel)
    return {k: rechne(v) for k, v in sorted(eimer.items())}


def _saetze(ergebnisse: list[Ergebnis], je_regel: dict[str, Kennzahlen]) -> list[str]:
    """Die Auswertung in Klartext — das, was man jemandem sagen würde.

    Bewusst ohne Beschönigung. Wenn das Ergebnis schlecht ist, steht das hier, und es
    steht zuerst.
    """
    aus: list[str] = []
    ganz = je_regel.get("ganz")
    drittel = je_regel.get("drittel")
    if ganz is None or ganz.anzahl == 0:
        return ["Noch kein abgeschlossener Trade — es gibt nichts auszuwerten."]

    richtung = "verloren" if ganz.summe_r < 0 else "gewonnen"
    aus.append(
        f"Über {ganz.anzahl} abgeschlossene Signale hat das System {abs(ganz.summe_r):.1f} R "
        f"{richtung} — im Schnitt {ganz.erwartungswert:+.2f} R je Trade."
    )
    if not ganz.belastbar:
        aus.append(
            f"Das sind weniger als {GENUG} Trades. Die Zahlen zeigen, was passiert ist, "
            "aber sie tragen noch keine Aussage über den kommenden Monat."
        )

    if drittel is not None and abs(drittel.summe_r - ganz.summe_r) > 0.5:
        besser = "besser" if drittel.summe_r > ganz.summe_r else "schlechter"
        aus.append(
            f"Mit Teilverkauf am ersten Ziel und Stop auf Einstand wären es "
            f"{drittel.summe_r:+.1f} R gewesen, also {besser}. Der Unterschied kommt fast "
            "vollständig aus Trades, die kurz im Plus waren und danach voll zurückliefen."
        )

    mfe = [e.mfe for e in ergebnisse]
    nie = [e for e in ergebnisse if e.mfe < IM_PLUS_AB]
    if mfe:
        anteil = len(nie) / len(mfe) * 100
        aus.append(
            f"{len(nie)} von {len(mfe)} Trades ({anteil:.0f} %) kamen nie auch nur "
            f"{IM_PLUS_AB:.1f} R ins Plus."
        )
        if anteil >= 45:
            aus.append(
                "Das ist die wichtigste Zahl hier. Wenn die Hälfte der Einstiege nie "
                "funktioniert, liegt das Problem nicht bei den Zielen und nicht beim "
                "Stop, sondern beim Einstieg selbst."
            )

    if ganz.max_rueckgang_r < -5:
        aus.append(
            f"Der tiefste Rückgang in der Reihenfolge lag bei {ganz.max_rueckgang_r:.1f} R, "
            f"die längste Verlustserie bei {ganz.verlustserie} Trades hintereinander. "
            "Bei 0,5 % Risiko je Trade wäre das ein Konto-Rückgang, den man aushält — "
            "bei 2 % nicht mehr."
        )

    # Der Schluss-Satz steht immer da, auch und gerade wenn die Zahlen gut aussehen.
    # Eine Auswertung, die ihre eigene Reichweite verschweigt, lädt dazu ein, mehr aus
    # ihr zu lesen, als drinsteht.
    klassen = {e.klasse for e in ergebnisse}
    schwerpunkt = max(
        klassen, key=lambda k: sum(1 for e in ergebnisse if e.klasse == k), default=""
    )
    anteil_schwer = (
        sum(1 for e in ergebnisse if e.klasse == schwerpunkt) / len(ergebnisse) if ergebnisse else 0
    )
    aus.append(
        "Das ist ein Vorlauf aus einem einzigen Marktabschnitt"
        + (
            f", zu {anteil_schwer * 100:.0f} % aus {schwerpunkt}"
            if schwerpunkt and anteil_schwer >= 0.6
            else ""
        )
        + ". Er sagt, was passiert ist — er belegt keinen Edge und widerlegt auch keinen. "
        "Dafür braucht es mehrere Marktphasen."
    )
    return aus


def bericht(wachliste: dict[str, Any] | None, *, jetzt: datetime | None = None) -> Bericht:
    """Die vollständige Auswertung aus dem gespeicherten Wachlisten-Zustand."""
    from datetime import UTC

    ergebnisse = aus_wachliste(wachliste)
    roh = (wachliste or {}).get("wachen") or {}
    offen = sum(
        1
        for w in (roh.values() if isinstance(roh, dict) else [])
        if isinstance(w, dict) and str(w.get("zustand")) not in ABGESCHLOSSEN
    )

    je_regel = {
        "ganz": rechne([e.r_ganz for e in ergebnisse]),
        "drittel": rechne([e.r_drittel for e in ergebnisse]),
    }
    mfe = [e.mfe for e in ergebnisse]
    mae = [e.mae for e in ergebnisse]

    return Bericht(
        erzeugt=(jetzt or datetime.now(UTC)).isoformat(),
        abgeschlossen=len(ergebnisse),
        offen=offen,
        je_regel=je_regel,
        je_note=_gruppiere(ergebnisse, lambda e: e.note),
        je_klasse=_gruppiere(ergebnisse, lambda e: e.klasse),
        je_setup=_gruppiere(ergebnisse, lambda e: e.setup),
        mfe_schnitt=(sum(mfe) / len(mfe)) if mfe else None,
        mae_schnitt=(sum(mae) / len(mae)) if mae else None,
        nie_im_plus=(sum(1 for m in mfe if m < IM_PLUS_AB) / len(mfe) if mfe else None),
        trades=tuple(
            {
                "instrument": e.instrument,
                "klasse": e.klasse,
                "note": e.note,
                "setup": e.setup,
                "zustand": e.zustand,
                "erreicht": list(e.erreicht),
                "r_ganz": round(e.r_ganz, 2),
                "r_drittel": round(e.r_drittel, 2),
                "mfe": round(e.mfe, 2),
                "mae": round(e.mae, 2),
                "beendet": e.beendet,
            }
            for e in reversed(ergebnisse)
        ),
        saetze=tuple(_saetze(ergebnisse, je_regel)),
    )


__all__ = [
    "ABGESCHLOSSEN",
    "GENUG",
    "IM_PLUS_AB",
    "REGELN",
    "ZIEL_R",
    "Bericht",
    "Ergebnis",
    "Kennzahlen",
    "aus_wachliste",
    "bericht",
    "rechne",
]
