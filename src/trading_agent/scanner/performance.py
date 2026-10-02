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
    #: Wann das Signal auf die Wachliste kam, und wie viele Stunden bis zum Ausgang
    #: vergangen sind. ``None``, wenn eine der beiden Zeiten fehlt.
    begonnen: str = ""
    dauer_h: float | None = None
    #: Wurde der Einstieg ueberhaupt ausgeloest? Ein Setup, das seinen Einstieg nie
    #: erreicht hat, ist kein Trade — es hat kein Geld gekostet und keines gebracht.
    eingestiegen: bool = True
    #: Ging der Einstieg aufs Telefon (Alarm-Tor offen)? Nur diese Trades hat Ozan
    #: tatsaechlich gesehen.
    gemeldet: bool = False
    #: Eingestiegen, aber ohne bekannten Ausgang beendet — die Wache wurde geschlossen,
    #: weil es keinen Kurs mehr gab (bis 27.09. jedes Wochenende bei Aktien). Das Ergebnis
    #: ist unbekannt; es als 0 R zu buchen, waere eine erfundene Zahl.
    ohne_ergebnis: bool = False

    @property
    def gezaehlt(self) -> bool:
        return self.eingestiegen and not self.ohne_ergebnis


def _r_bei(w: dict[str, Any], kurs: float) -> float | None:
    """R beim Kurs ``kurs`` — ab dem echten Einstiegskurs, gemessen am urspruenglichen Stop."""
    try:
        einstieg = float(w["einstieg"])
        stop = float(w["stop"])
    except (KeyError, TypeError, ValueError):
        return None
    risiko = abs(einstieg - stop)
    if risiko <= 0:
        return None
    basis = float(w.get("einstiegskurs") or einstieg)
    weg = (kurs - basis) if str(w.get("richtung") or "long") == "long" else (basis - kurs)
    return weg / risiko


def _ist_eingestiegen(w: dict[str, Any], zustand: str, erreicht: tuple[str, ...]) -> bool:
    """Aeltere Eintraege haben keinen Einstiegskurs — Stop und Ziele gibt es aber nur
    nach einem Einstieg, also zaehlen sie ebenfalls."""
    return (
        w.get("einstiegskurs") is not None or zustand in ("stop", "ziel_erreicht") or bool(erreicht)
    )


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


def _r_drittel(
    zustand: str, erreicht: tuple[str, ...], raus_erreicht: tuple[str, ...] | None = None
) -> float:
    """Drittel-Regel: je Ziel ein Drittel raus, nach dem ersten Ziel Stop auf Einstand.

    Der entscheidende Unterschied liegt nicht in den Teilgewinnen, sondern im Stop auf
    Einstand: ein Trade, der TP1 berührt und danach dreht, ist damit ein kleiner Gewinn
    statt eines vollen Verlusts. Genau diese Fälle machen in den echten Daten die
    Trefferquote aus.
    """
    teil = 1.0 / 3.0
    if raus_erreicht is not None:
        # Der Schutz-Stop hat die Position beendet; spaetere Ziele zaehlen nicht mehr.
        # Rest am Schutz-Stop: nach Ziel 2 auf Ziel 1, sonst auf Einstand.
        vorher = [t for t in ("TP1", "TP2", "TP3") if t in raus_erreicht]
        if not vorher:
            return 0.0
        rest_r = ZIEL_R["TP1"] if "TP2" in vorher else 0.0
        return sum(teil * ZIEL_R[t] for t in vorher) + (3 - len(vorher)) * teil * rest_r
    getroffen = [t for t in ("TP1", "TP2", "TP3") if t in erreicht]
    if not getroffen:
        return -1.0 if zustand == "stop" else 0.0
    gewinn = sum(teil * ZIEL_R[t] for t in getroffen)
    # Der Rest geht am Einstand raus — nach TP1 steht der Stop dort.
    return gewinn


def _stunden(von: str, bis: str) -> float | None:
    """Abstand zweier ISO-Zeitstempel in Stunden. ``None``, wenn etwas fehlt oder krumm ist.

    Gemessen wird von der AUFNAHME auf die Wachliste bis zum Ausgang — nicht vom
    Einstieg, denn wann der Kurs die Marke berührt hat, wird nirgends festgehalten. Das
    ist die ehrlichere Zahl für Ozans Frage („wie lange läuft so ein Trade?"): sie sagt,
    wie lange es vom angezeigten Signal bis zum Ergebnis gedauert hat, Wartezeit auf den
    Einstieg eingeschlossen. Und genau diese Wartezeit gehört dazu — sie ist Zeit, in der
    Geld gebunden oder Aufmerksamkeit verbraucht wird.
    """
    if not von or not bis:
        return None
    try:
        a = datetime.fromisoformat(von)
        b = datetime.fromisoformat(bis)
    except ValueError:
        return None
    stunden = (b - a).total_seconds() / 3600.0
    return stunden if stunden >= 0 else None


def median(werte: list[float]) -> float | None:
    """Median. Bei Haltedauern der richtige Mittelwert: ein einziger Wert, der drei
    Wochen offen stand, zieht den Durchschnitt sonst weit über alles Typische."""
    if not werte:
        return None
    s = sorted(werte)
    m = len(s) // 2
    return float(s[m]) if len(s) % 2 else float((s[m - 1] + s[m]) / 2.0)


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
        roh_raus = w.get("raus_erreicht")
        raus_e = (
            tuple(str(x) for x in roh_raus)
            if w.get("raus") and isinstance(roh_raus, list)
            else None
        )
        mfe = float(w.get("bestes_r") or 0.0)
        begonnen = str(w.get("aufgenommen") or "")
        beendet = str(w.get("zuletzt") or "")
        r_ganz = _r_ganz(zustand, erreicht, mfe)
        r_drittel = _r_drittel(zustand, erreicht, raus_e)
        # Ausstieg auf gedrehte Analyse: der Kurs beim Ausstieg steht in der Wache. Bis
        # 30.09. wurde er ignoriert und der Trade pauschal mit 0 R gebucht — mal zu gut
        # (TAO -0,59 R), mal zu schlecht (DASH +1,13 R). Jetzt der echte Kurs, wo es ihn gibt.
        if zustand == "invalidiert" and w.get("ausstiegskurs") is not None:
            r_aus = _r_bei(w, float(w["ausstiegskurs"]))
            if r_aus is not None:
                r_ganz = r_aus
                if raus_e is None:
                    teil = 1.0 / 3.0
                    getroffen = [t for t in ("TP1", "TP2", "TP3") if t in erreicht]
                    # Nach Ziel 1 steht der Schutz-Stop auf Einstand, nach Ziel 2 auf Ziel 1 —
                    # tiefer kann der Rest laut Plan nicht rausgehen.
                    boden = ZIEL_R["TP1"] if "TP2" in getroffen else 0.0
                    rest_r = max(r_aus, boden) if getroffen else r_aus
                    r_drittel = (
                        sum(teil * ZIEL_R[t] for t in getroffen)
                        + (1.0 - teil * len(getroffen)) * rest_r
                    )
        eingestiegen = _ist_eingestiegen(w, zustand, erreicht)
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
                beendet=beendet,
                r_ganz=r_ganz,
                r_drittel=r_drittel,
                begonnen=begonnen,
                dauer_h=_stunden(begonnen, beendet),
                eingestiegen=eingestiegen,
                gemeldet=bool(w.get("gemeldet")),
                ohne_ergebnis=eingestiegen and zustand == "abgelaufen" and raus_e is None,
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
    #: Wie lange die Signale tatsächlich gelaufen sind, in Stunden — Median, und
    #: getrennt danach, wie sie ausgegangen sind. Ozan fragt danach ausdrücklich: er
    #: will wissen, ob ein Signal Tage oder Wochen bindet, bevor er einsteigt. Gemessen,
    #: nicht geschätzt; ``None``, solange es zu wenige Fälle sind.
    dauer: dict[str, Any] = field(default_factory=dict)
    #: Jeder abgeschlossene Trade einzeln, jüngster zuerst. Ozan hat ausdrücklich danach
    #: gefragt: er will nicht nur die Summe sehen, sondern nachlesen können, wie die
    #: Signale ausgegangen sind, die ihm angezeigt wurden. Eine Kennzahl, die man nicht
    #: auf einzelne Fälle zurückführen kann, glaubt man oder nicht — mehr nicht.
    trades: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    saetze: tuple[str, ...] = field(default_factory=tuple)
    #: Getrennt nach dem, was Ozan wirklich gesehen hat: Einstieg aufs Telefon oder nur in
    #: der App. Drittel-Regel, weil das der Plan im Alarm ist.
    je_meldung: dict[str, Kennzahlen] = field(default_factory=dict)
    #: Setups, die ihren Einstieg nie erreicht haben — kein Trade, nicht mitgezaehlt.
    nicht_ausgeloest: int = 0
    #: Eingestiegen, aber ohne bekannten Ausgang beendet (Kursausfall) — nicht mitgezaehlt.
    ohne_ergebnis: int = 0
    #: Je Klasse nach PLAN (Drittel-Regel) — dieselbe Regel wie „Deine Alarme" und die
    #: Summe oben, damit sich die Zahlen auf der Karte zusammenzaehlen lassen.
    je_klasse_plan: dict[str, Kennzahlen] = field(default_factory=dict)
    #: Je Kalenderwoche der Aufnahme, nach Plan — „KW37" usw. Damit eine schlechte erste
    #: Woche nicht als Dauerzustand gelesen wird, und eine gute nicht als Beweis.
    je_woche_plan: dict[str, Kennzahlen] = field(default_factory=dict)

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
            "dauer": dict(self.dauer),
            "trades": [dict(t) for t in self.trades],
            "saetze": list(self.saetze),
            "je_meldung": {k: v.as_dict() for k, v in self.je_meldung.items()},
            "nicht_ausgeloest": self.nicht_ausgeloest,
            "ohne_ergebnis": self.ohne_ergebnis,
            "je_klasse_plan": {k: v.as_dict() for k, v in self.je_klasse_plan.items()},
            "je_woche_plan": {k: v.as_dict() for k, v in self.je_woche_plan.items()},
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


#: So heissen die Klassen in der App.
KLASSE_NAME = {"krypto": "Coins", "aktien": "Aktien", "gold": "Gold"}


def _z(x: float, stellen: int = 1, vorzeichen: bool = True) -> str:
    """Zahl mit deutschem Komma — „+2,8", „-0,07".

    Erst auf zwei Stellen gerundet wie in ``performance.json`` (``summe_r``) — sonst stand
    im Satz „KW38 +10,4 R" und auf dem Chip derselben Karte „+10,3 R" (10,3500… gegen 10,35).
    """
    x = round(float(x), max(2, stellen))
    roh = f"{x:+.{stellen}f}" if vorzeichen else f"{x:.{stellen}f}"
    return roh.replace(".", ",")


def _woche(e: Ergebnis) -> str:
    """„KW37" — Kalenderwoche der Aufnahme, ``""`` ohne Datum."""
    try:
        d = datetime.fromisoformat(e.begonnen)
    except (TypeError, ValueError):
        return ""
    return f"KW{d.isocalendar()[1]:02d}"


def _wochen_satz(ergebnisse: list[Ergebnis]) -> list[str]:
    je: dict[str, list[float]] = {}
    for e in ergebnisse:
        k = _woche(e)
        if k:
            je.setdefault(k, []).append(e.r_drittel)
    if len(je) < 2:
        return []
    teile = sorted(je.items())
    aus = [
        "Je Woche (nach Aufnahme): "
        + " · ".join(f"{k} {_z(sum(v))} R aus {len(v)}" for k, v in teile)
        + "."
    ]
    gesamt = sum(sum(v) for _, v in teile)
    schlimm_k, schlimm_v = min(teile, key=lambda kv: sum(kv[1]))
    if gesamt < 0 and sum(schlimm_v) < 0.7 * gesamt:
        rest = [r for k, v in teile if k > schlimm_k for r in v]
        if rest:
            aus.append(
                f"Der größte Teil des Verlusts stammt aus {schlimm_k} "
                f"({_z(sum(schlimm_v))} R aus {len(schlimm_v)} Trades). Seitdem: "
                f"{_anzahl(len(rest), 'Trade', 'Trades')}, {_z(sum(rest))} R."
            )
    return aus


def _anzahl(n: int, einzahl: str, mehrzahl: str) -> str:
    return f"{n} {einzahl if n == 1 else mehrzahl}"


def _saetze(ergebnisse: list[Ergebnis], je_regel: dict[str, Kennzahlen]) -> list[str]:
    """Die Auswertung in Klartext — das, was man jemandem sagen würde.

    Bewusst ohne Beschönigung: ein Verlust steht als Verlust da. Seit 02.10. steht er aber
    mit seiner Herkunft da. Vorher hiess der erste Satz nur „Über 39 Signale hat das
    System 2,8 R verloren" — ohne dass man sah, dass die Coins im Plus lagen und der ganze
    Verlust aus Aktien-Kaeufen kam, die nie aufs Handy gingen. Ozan las daraus, das System
    verliere Geld; das, was er bekommen hat, lag im Plus.
    """
    aus: list[str] = []
    ganz = je_regel.get("ganz")
    drittel = je_regel.get("drittel")
    if ganz is None or ganz.anzahl == 0:
        return ["Noch kein abgeschlossener Trade — es gibt nichts auszuwerten."]

    # Hauptregel ist der PLAN aus den Alarmen (Drittel an jedem Ziel). Bis 02.10. stand
    # hier „alles oder nichts" — eine Regel, nach der kein Alarm handelt; die Karte zeigte
    # oben −2,8 R, die Chips darunter (nach Plan) zusammen −4,3 R.
    plan = drittel if drittel is not None and drittel.anzahl else ganz
    richtung = "verloren" if plan.summe_r < 0 else "gewonnen"
    aus.append(
        f"{'Alle ' + str(plan.anzahl) + ' beobachteten Signale' if plan.anzahl != 1 else 'Das eine beobachtete Signal'}"
        " zusammen — auch die, die nie aufs Handy "
        f"gingen: nach Plan {_z(abs(plan.summe_r), vorzeichen=False)} R {richtung}, im "
        f"Schnitt {_z(plan.erwartungswert or 0.0, 2)} R je Trade."
    )

    # Woher das Ergebnis kommt — je Klasse, nach derselben Regel wie die Summe oben.
    je: dict[str, list[float]] = {}
    for e in ergebnisse:
        je.setdefault(e.klasse or "?", []).append(e.r_drittel)
    if len(je) > 1:
        teile = sorted(je.items(), key=lambda kv: -sum(kv[1]))

        def wie(k: str, v: list[float]) -> str:
            return f"{KLASSE_NAME.get(k, k)} ({_z(sum(v))} R aus {len(v)})"

        plus = [(k, v) for k, v in teile if sum(v) > 0]
        minus = [(k, v) for k, v in teile if sum(v) < 0]
        if plus and minus and plan.summe_r < 0:
            einzahl = len(plus) == 1 and plus[0][0] == "gold"
            aus.append(
                f"Der Verlust kommt aus {' und '.join(wie(k, v) for k, v in reversed(minus))}; "
                f"{' und '.join(wie(k, v) for k, v in plus)} "
                f"{'liegt' if einzahl else 'liegen'} im Plus. Fürs Alarm-Tor zählt deshalb "
                "jede Setup-Art nur in ihrer eigenen Klasse: ein Verlust bei Aktien bremst "
                "keinen Coin-Alarm, und eine Klasse, die verliert, klingelt nicht."
            )
        else:
            aus.append("Je Klasse: " + " · ".join(wie(k, v) for k, v in teile) + ".")

    aus.extend(_wochen_satz(ergebnisse))

    if not plan.belastbar:
        aus.append(
            f"Das sind weniger als {GENUG} Trades. Die Zahlen zeigen, was passiert ist, "
            "aber sie tragen noch keine Aussage über den kommenden Monat."
        )

    if drittel is not None and abs(drittel.summe_r - ganz.summe_r) > 0.5:
        vergleich = f"{_z(ganz.summe_r)} R statt {_z(drittel.summe_r)} R"
        if drittel.summe_r > ganz.summe_r:
            aus.append(
                f"Alles auf einmal (ganze Position bis Ziel 3 oder Stop) wären es {vergleich} "
                "gewesen — der Plan war besser. Der Unterschied kommt aus Trades, die kurz "
                "im Plus waren und danach voll zurückliefen."
            )
        else:
            aus.append(
                f"Alles auf einmal (ganze Position bis Ziel 3 oder Stop) wären es {vergleich} "
                "gewesen. In diesen Wochen liefen viele Trades nach Ziel 1 bis ans letzte "
                "Ziel — dann kostet der Teilverkauf Gewinn. Über 19 Monate Nachspiel lagen "
                "beide Regeln gleichauf; der Plan bleibt, weil nach Ziel 1 kein Verlust mehr "
                "möglich ist."
            )

    mfe = [e.mfe for e in ergebnisse]
    nie = [e for e in ergebnisse if e.mfe < IM_PLUS_AB]
    if mfe:
        anteil = len(nie) / len(mfe) * 100
        aus.append(
            f"{len(nie)} von {_anzahl(len(mfe), 'Trade', 'Trades')} ({anteil:.0f} %) "
            f"{'kam' if len(nie) == 1 else 'kamen'} nie auch nur "
            f"{_z(IM_PLUS_AB, vorzeichen=False)} R ins Plus."
        )
        if anteil >= 45:
            aus.append(
                "Das ist die wichtigste Zahl hier. Wenn die Hälfte der Einstiege nie "
                "funktioniert, liegt das Problem nicht bei den Zielen und nicht beim "
                "Stop, sondern beim Einstieg selbst."
            )

    if plan.max_rueckgang_r < -5:
        rg = plan.max_rueckgang_r
        halb, zwei = rg * 0.5, rg * 2.0

        def konto(pct: float) -> str:
            if pct <= -100:
                return "mehr als das ganze Konto"
            return f"rund {_z(pct, 0)} % aufs Konto" + (" — tragbar" if pct > -15 else "")

        aus.append(
            f"Der tiefste Rückgang aller Signale hintereinander lag bei {_z(rg)} R, die "
            f"längste Verlustserie bei {plan.verlustserie} Trades. Bei 0,5 % Risiko je "
            f"Trade wären das {konto(halb)}; bei 2 % {konto(zwei)}."
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
            f", zu {anteil_schwer * 100:.0f} % aus {KLASSE_NAME.get(schwerpunkt, schwerpunkt)}"
            if schwerpunkt and anteil_schwer >= 0.6
            else ""
        )
        + ". Er sagt, was passiert ist — er belegt keinen Edge und widerlegt auch keinen. "
        "Dafür braucht es mehrere Marktphasen."
    )
    return aus


def _satz_handy(alle: list[Ergebnis]) -> list[str]:
    """Was Ozan tatsaechlich bekommen hat — steht vor allem anderen."""
    handy = [e for e in alle if e.gezaehlt and e.gemeldet]
    if not handy:
        return []
    werte = [e.r_drittel for e in handy]
    s = sum(werte)
    plus = sum(1 for v in werte if v > 0)
    stops = sum(1 for e in handy if e.zustand == "stop")
    satz = (
        f"Deine Alarme (aufs Handy): {_anzahl(len(handy), 'Trade', 'Trades')}, zusammen "
        f"{_z(s)} R nach Plan ({_z(s / len(handy), 2)} R je Trade) — {plus} im Plus, "
        f"{_anzahl(stops, 'Stop', 'Stops')}."
    )
    if len(handy) < 10:
        satz += " Noch zu wenige für ein Urteil; jeder weitere Alarm zählt hier mit."
    return [satz]


#: Unter so vielen gemessenen Fällen wird eine Haltedauer nicht ausgewiesen. Ein Median
#: aus drei Trades ist keine Erfahrung, sondern eine Anekdote mit Nachkommastelle.
DAUER_MIN_FAELLE = 8
#: Bis hierher gilt ein Trade als kurz. Zwei Tage: was über ein Wochenende hinausläuft,
#: ist für Ozans Frage („ein, zwei kurze Trades") keiner mehr.
KURZ_BIS_H = 48.0


def _dauern(ergebnisse: list[Ergebnis]) -> dict[str, Any]:
    """Gemessene Haltedauern — insgesamt, nach Ausgang, nach Klasse und nach Note.

    Die Frage dahinter ist praktisch, nicht akademisch: bindet dieses Signal einen
    Nachmittag oder drei Wochen? Beantwortet wird sie aus dem, was tatsächlich passiert
    ist, nicht aus dem Abstand zum Ziel geteilt durch irgendeine Durchschnittsbewegung.
    """
    mit = [e for e in ergebnisse if e.dauer_h is not None]
    if len(mit) < DAUER_MIN_FAELLE:
        return {"faelle": len(mit), "belastbar": False}

    alle = [float(e.dauer_h or 0.0) for e in mit]
    gewonnen = [float(e.dauer_h or 0.0) for e in mit if e.zustand == "ziel_erreicht"]
    verloren = [float(e.dauer_h or 0.0) for e in mit if e.zustand == "stop"]

    def gruppe(schluessel: Any) -> dict[str, Any]:
        eimer: dict[str, list[float]] = {}
        for e in mit:
            k = str(schluessel(e) or "")
            if not k or k == "?":
                continue
            eimer.setdefault(k, []).append(float(e.dauer_h or 0.0))
        return {
            k: {"median_h": round(median(v) or 0.0, 1), "faelle": len(v)}
            for k, v in sorted(eimer.items())
            if len(v) >= 3
        }

    kurz = sum(1 for d in alle if d <= KURZ_BIS_H)
    return {
        "faelle": len(mit),
        "belastbar": len(mit) >= GENUG,
        "median_h": round(median(alle) or 0.0, 1),
        "median_gewinn_h": round(median(gewonnen), 1) if gewonnen else None,
        "median_verlust_h": round(median(verloren), 1) if verloren else None,
        "kurz_anteil": round(kurz / len(alle), 3),
        "kurz_bis_h": KURZ_BIS_H,
        "je_klasse": gruppe(lambda e: e.klasse),
        "je_note": gruppe(lambda e: e.note),
    }


def bericht(wachliste: dict[str, Any] | None, *, jetzt: datetime | None = None) -> Bericht:
    """Die vollständige Auswertung aus dem gespeicherten Wachlisten-Zustand."""
    from datetime import UTC

    alle = aus_wachliste(wachliste)
    # Gezaehlt wird, was ein Trade war: eingestiegen und mit bekanntem Ausgang. Bis 30.09.
    # standen hier auch Setups, die ihren Einstieg nie erreicht haben, und Aktien-Trades,
    # die der Waechter am Wochenende mangels Kurs geschlossen hat — alle mit 0 R. Das hat
    # Trefferquote und Erwartungswert verduennt, bei Aktien bis auf „0 % Treffer".
    ergebnisse = [e for e in alle if e.gezaehlt]
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
    dauer = _dauern(ergebnisse)

    return Bericht(
        erzeugt=(jetzt or datetime.now(UTC)).isoformat(),
        abgeschlossen=len(ergebnisse),
        offen=offen,
        je_regel=je_regel,
        je_note=_gruppiere(ergebnisse, lambda e: e.note),
        je_klasse=_gruppiere(ergebnisse, lambda e: e.klasse),
        je_klasse_plan=_gruppiere(ergebnisse, lambda e: e.klasse, "drittel"),
        je_woche_plan=_gruppiere(ergebnisse, _woche, "drittel"),
        je_setup=_gruppiere(ergebnisse, lambda e: e.setup),
        je_meldung=_gruppiere(
            ergebnisse, lambda e: "aufs_handy" if e.gemeldet else "nur_app", "drittel"
        ),
        nicht_ausgeloest=sum(1 for e in alle if not e.eingestiegen),
        ohne_ergebnis=sum(1 for e in alle if e.eingestiegen and e.ohne_ergebnis),
        mfe_schnitt=(sum(mfe) / len(mfe)) if mfe else None,
        mae_schnitt=(sum(mae) / len(mae)) if mae else None,
        nie_im_plus=(sum(1 for m in mfe if m < IM_PLUS_AB) / len(mfe) if mfe else None),
        dauer=dauer,
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
                "begonnen": e.begonnen,
                "dauer_h": round(e.dauer_h, 1) if e.dauer_h is not None else None,
                "eingestiegen": e.eingestiegen,
                "gemeldet": e.gemeldet,
                "gezaehlt": e.gezaehlt,
            }
            for e in reversed(alle)
        ),
        saetze=tuple(_satz_handy(alle) + _saetze(ergebnisse, je_regel) + _saetze_zaehlung(alle)),
    )


def _saetze_zaehlung(alle: list[Ergebnis]) -> list[str]:
    """Was NICHT mitgezaehlt wurde."""
    aus: list[str] = []
    nie = sum(1 for e in alle if not e.eingestiegen)
    ausfall = [e for e in alle if e.eingestiegen and e.ohne_ergebnis]
    if nie:
        aus.append(
            f"{_anzahl(nie, 'Setup hat seinen', 'Setups haben ihren')} Einstieg nie erreicht — "
            "kein Trade, kein Geld im Markt, deshalb nicht mitgezählt."
        )
    if ausfall:
        namen = ", ".join(sorted({e.instrument for e in ausfall})[:8])
        if len(ausfall) == 1:
            aus.append(
                f"1 Trade lief noch, als der Wächter ihn mangels Kurs schloss ({namen}). "
                "Sein Ausgang ist unbekannt — er steht in der Liste, zählt aber nicht."
            )
        else:
            aus.append(
                f"{len(ausfall)} Trades liefen noch, als der Wächter sie mangels Kurs schloss "
                f"({namen}). Ihr Ausgang ist unbekannt — sie stehen in der Liste, zählen "
                "aber nicht."
            )
    return aus


__all__ = [
    "ABGESCHLOSSEN",
    "GENUG",
    "IM_PLUS_AB",
    "KLASSE_NAME",
    "REGELN",
    "ZIEL_R",
    "Bericht",
    "Ergebnis",
    "Kennzahlen",
    "aus_wachliste",
    "bericht",
    "rechne",
]
