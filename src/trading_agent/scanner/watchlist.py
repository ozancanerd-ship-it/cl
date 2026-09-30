"""Die Wachliste — was aus einem Signal wird, nachdem es einmal da war.

Ozans Satz, der dieses Modul ausgeloest hat: „und er gibt mir dann das buy signal
oder wenn der wert getroffen ist, will nicht selber alarme erstellen."

Bis hierher meldete das System nur, dass ein Setup **entstanden** ist. Was danach
passiert — Kurs erreicht den Einstieg, laeuft ins erste Ziel, dreht in den Stop — war
seine Sache. Genau das soll es nicht sein.

WIE ES ARBEITET

Jedes handelbare Setup wandert automatisch auf die Wachliste. Bei jeder Pruefung
bekommt die Liste das **Hoch und Tief seit der letzten Pruefung** (nicht nur den
Schlusskurs) und leitet daraus die Zustandsuebergaenge ab:

    WARTET_AUF_EINSTIEG → EINSTIEG_ERREICHT → AKTIV → TP1 → TP2 → TP3 → ZIEL_ERREICHT
                       ↘ ABGELAUFEN                 ↘ STOP
                                                    ↘ INVALIDIERT

Jeder Uebergang wird **genau einmal** gemeldet. Der Zustand liegt in einer Datei und
wird zwischen den Laeufen mitgeschleppt; ohne ihn wuerde jeder Lauf alles neu melden.

ZWEI EHRLICHE EINSCHRAENKUNGEN

1. **Reihenfolge innerhalb eines Fensters ist nicht bekannt.** Wenn zwischen zwei
   Pruefungen sowohl das Ziel als auch der Stop beruehrt wurden, sagt ein Hoch/Tief
   nicht, was zuerst kam. Die Wachliste nimmt dann den **Stop** an. Das ist die
   pessimistische Annahme, und sie ist die richtige: eine Statistik, die sich im
   Zweifel den Gewinn gutschreibt, waere geschoent.
2. **Die Pruefung laeuft im Takt, nicht in Echtzeit.** Ein Treffer um 14:23 erreicht
   dich beim naechsten Lauf. Fuer Swing-Trades ueber Tage ist das kein Unterschied;
   fuer Scalping waere es einer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

#: Nach so vielen Tagen ohne Einstieg wird ein Setup verworfen. Ein Chartbild von
#: vor zwei Wochen beschreibt den Markt nicht mehr.
HALTBARKEIT = timedelta(days=10)

#: Ein Setup, dessen Stop naeher als das am Einstieg liegt, kommt gar nicht erst auf
#: die Liste. Der Grund sind die Kosten: rund 0,2 % Gebuehren hin und zurueck plus
#: Spread. Bei UUSDT lag der Stop 0,06 % entfernt — auf dem Papier ein
#: Chance-Risiko-Verhaeltnis von 1:25, in Wirklichkeit ein sicherer Verlust, und in
#: der App kam als Positionsgroesse "9.992 EUR Einsatz" bei 1.200 EUR Kapital heraus.
MIN_STOP_ANTEIL = 0.003  # 0,3 %

#: Ab dieser Abweichung gilt der Einstiegsplan als ueberholt und wird durch den
#: frischen ersetzt (in Prozent des Einstiegs).
#:
#: Ozans Einwand, woertlich: „wenn da einfach nur ein Wert liegt, der dann so krass
#: gesunken ist, dass der Wert auf einmal gar keinen Sinn ergibt". Genau so war es
#: gebaut — eine aufgenommene Wache behielt ihre Zahlen fuer immer. Vor dem Einstieg
#: ist der Plan aber ein Vorschlag und gehoert aktualisiert; erst ab dem Einstieg ist
#: er ein Vertrag und muss fest bleiben, sonst ist kein Ergebnis mehr messbar.
PLAN_VERALTET_PCT = 1.5


class Zustand(StrEnum):
    WARTET = "wartet_auf_einstieg"
    AKTIV = "aktiv"
    ZIEL_ERREICHT = "ziel_erreicht"
    STOP = "stop"
    INVALIDIERT = "invalidiert"
    ABGELAUFEN = "abgelaufen"


#: Zustaende, aus denen es kein Zurueck gibt.
ENDZUSTAENDE = frozenset(
    {Zustand.ZIEL_ERREICHT, Zustand.STOP, Zustand.INVALIDIERT, Zustand.ABGELAUFEN}
)


@dataclass(slots=True)
class Wache:
    """Ein beobachtetes Setup mit seinen Marken."""

    instrument: str
    klasse: str
    richtung: str  # "long" | "short"
    note: str
    einstieg: float
    einstieg_art: str
    stop: float
    tp1: float | None
    tp2: float | None
    tp3: float | None
    score: float
    rr: float | None
    erwartet_pct: float | None
    zustand: str = Zustand.WARTET.value
    erreicht: list[str] = field(default_factory=list)
    aufgenommen: str = ""
    zuletzt: str = ""
    einstiegskurs: float | None = None
    bestes_r: float = 0.0
    schlechtestes_r: float = 0.0
    #: Name des Setups ("Ruecksetzer im Trend"). Ohne Namen kein Alarm — genau daran
    #: haben die ersten Meldungen gekrankt: Zahlen ohne erkennbare Handelsidee.
    setup: str = ""
    #: Ein Satz, warum dieser Trade ueberhaupt Sinn ergibt.
    these: str = ""
    #: Was passieren muss, bevor eingestiegen wird.
    trigger: str = ""
    #: Relative Staerke innerhalb der eigenen Klasse, 0..100.
    rs: float | None = None
    #: Ausgeschriebener Name und Handelsort aus dem Scan. Ozans Vorgabe: jeder Wert mit
    #: vollem Namen und der App, in der er ihn findet — ein Alarm „LINKUSD" zwingt ihn,
    #: erst nachzuschlagen, was das ist und wo er es kauft.
    name: str = ""
    broker: str = ""
    #: Euro je Dollar zum Zeitpunkt der Aufnahme — fuer den Euro-Gegenwert im Alarm.
    eurusd: float | None = None
    #: Wann der Einstieg AUFS TELEFON ging (ISO-Zeit), leer = nie. Nur fuer diese Trades
    #: klingeln danach Ziel, Stop und Ausstieg — fuer alle anderen hat Ozan nichts im Markt.
    gemeldet: str = ""
    #: Warum der Einstieg NICHT aufs Telefon ging — steht in der App, damit ein
    #: ausbleibender Alarm erklaerbar ist.
    tor_grund: str = ""
    #: Der Schutz-Stop laut Plan: nach Ziel 1 der Einstieg, nach Ziel 2 das Ziel 1.
    #: Der urspruengliche ``stop`` bleibt unveraendert — an ihm haengt das R, und eine
    #: Statistik, deren Massstab wandert, misst nichts.
    schutz: float | None = None
    #: Die Position ist laut Plan draussen (Schutz-Stop oder Ausstieg). Ab dann klingelt
    #: zu diesem Trade nichts mehr; beobachtet und gezaehlt wird trotzdem weiter.
    raus: bool = False
    #: Kurs beim vorzeitigen Ausstieg (Analyse gedreht).
    ausstiegskurs: float | None = None
    #: Welche Ziele erreicht waren, als der Schutz-Stop die Position laut Plan beendet
    #: hat, und wo dieser Schutz-Stop lag. Die Wache laeuft danach weiter — ``erreicht``
    #: waechst also womoeglich noch. Bis zum 27.09. zaehlte die Bilanz auch Ziele, die
    #: ERST NACH dem Ausstieg kamen: Ziel 1, zurueck auf Einstand (raus), spaeter Ziel 3
    #: ging als +2,17 R in die Statistik, obwohl der Plan nur +0,33 R gebracht hat.
    raus_erreicht: list[str] | None = None
    raus_kurs: float | None = None

    @property
    def long(self) -> bool:
        return self.richtung == "long"

    @property
    def risiko(self) -> float:
        return abs(self.einstieg - self.stop)

    def r_bei(self, kurs: float) -> float | None:
        """Wie viele R der Trade gerade steht — die einzige Groesse, die Trades vergleichbar macht."""
        basis = self.einstiegskurs if self.einstiegskurs is not None else self.einstieg
        if self.risiko <= 0:
            return None
        weg = (kurs - basis) if self.long else (basis - kurs)
        return weg / self.risiko

    def as_dict(self) -> dict[str, Any]:
        return {
            "instrument": self.instrument,
            "klasse": self.klasse,
            "richtung": self.richtung,
            "note": self.note,
            "einstieg": self.einstieg,
            "einstieg_art": self.einstieg_art,
            "stop": self.stop,
            "tp1": self.tp1,
            "tp2": self.tp2,
            "tp3": self.tp3,
            "score": self.score,
            "rr": self.rr,
            "erwartet_pct": self.erwartet_pct,
            "zustand": self.zustand,
            "erreicht": list(self.erreicht),
            "aufgenommen": self.aufgenommen,
            "zuletzt": self.zuletzt,
            "einstiegskurs": self.einstiegskurs,
            "bestes_r": round(self.bestes_r, 2),
            "schlechtestes_r": round(self.schlechtestes_r, 2),
            "setup": self.setup,
            "these": self.these,
            "trigger": self.trigger,
            "rs": self.rs,
            "name": self.name,
            "broker": self.broker,
            "eurusd": self.eurusd,
            "gemeldet": self.gemeldet,
            "tor_grund": self.tor_grund,
            "schutz": self.schutz,
            "raus": self.raus,
            "ausstiegskurs": self.ausstiegskurs,
            "raus_erreicht": (list(self.raus_erreicht) if self.raus_erreicht is not None else None),
            "raus_kurs": self.raus_kurs,
        }

    @property
    def wer(self) -> str:
        """„Chainlink (LINK)" — voller Name, Kuerzel in Klammern. Ohne Namen das Paar."""
        from trading_agent.scanner.alarm_tor import basis

        kurz = basis(self.instrument)
        if self.name and self.name.upper() != kurz:
            return f"{self.name} ({kurz})"
        return self.instrument

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Wache:
        bekannt = set(cls.__slots__)
        return cls(**{k: v for k, v in d.items() if k in bekannt})


@dataclass(frozen=True, slots=True)
class Ereignis:
    """Ein Zustandsuebergang, der eine Meldung wert ist."""

    #: NEUES_SETUP | PLAN_AKTUALISIERT | EINSTIEG | TP | STOP | SCHUTZ | AUSSTIEG |
    #: INVALIDIERT | ABGELAUFEN
    art: str
    instrument: str
    dringend: bool
    titel: str
    text: str
    dedup_key: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "art": self.art,
            "instrument": self.instrument,
            "dringend": self.dringend,
            "titel": self.titel,
            "text": self.text,
        }


def _fmt(v: float | None) -> str:
    if v is None:
        return "—"
    a = abs(v)
    n = 2 if a >= 10 else 4 if a >= 1 else 6
    return f"{v:,.{n}f}".replace(",", " ")


def _abstand(w: Wache, v: float | None) -> str:
    """„(+6,2 %)" vom Einstieg aus gesehen — so liest man einen Plan auf dem Handy."""
    if v is None or not w.einstieg:
        return ""
    return f"({(v - w.einstieg) / w.einstieg * 100:+.1f} %)".replace(".", ",")


def _euro(w: Wache, v: float | None) -> str:
    """Der Euro-Gegenwert zur Orientierung. Ohne Wechselkurs lieber nichts als geschaetzt."""
    if v is None or not w.eurusd or w.eurusd <= 0:
        return ""
    if w.instrument.upper().endswith("EUR"):
        return ""
    e = v / w.eurusd
    n = 2 if abs(e) >= 1 else 4 if abs(e) >= 0.01 else 8
    return f"≈ {e:,.{n}f} €".replace(",", "X").replace(".", ",").replace("X", ".")


def short_hinweis(w: Wache) -> str:
    """Wie ein Short bei Ozans Handelsplaetzen ueberhaupt geht — steht im Alarm, nicht im Kopf.

    30.09.: die drei Alarme, die gerade klingelten, waren alle Aktien-Shorts mit „Short
    bei: Trade Republic". Trade Republic verkauft aber keine Aktie leer; ein Short geht
    dort nur ueber einen Short-Schein (Knock-out/Turbo). Und bei Coins geht ein Short
    nicht im Spotmarkt, sondern nur ueber einen Terminkontrakt. Ohne diesen Satz ist der
    Alarm eine Anweisung, die man so gar nicht ausfuehren kann.
    """
    if w.long:
        return ""
    if w.klasse == "aktien":
        return (
            "So geht der Short: bei Trade Republic nur ueber einen Short-Schein "
            f"(Knock-out/Turbo). Knock-out-Schwelle UEBER dem Stop ({_fmt(w.stop)}) waehlen, "
            "sonst ist der Schein weg, bevor der Plan greift. Der Hebel steckt im Schein — "
            "Einsatz entsprechend kleiner."
        )
    if w.klasse == "krypto":
        return (
            "So geht der Short: nicht im Spotmarkt, nur ueber einen Terminkontrakt "
            "(Bybit Perpetual, USDT). Ohne Hebel bzw. mit 1x handeln, dann entspricht das "
            "Risiko dem Plan."
        )
    return ""


def einstieg_text(
    w: Wache, *, kurs: float | None = None, bestaetigt: str = "", warum: str = "", bilanz: str = ""
) -> str:
    """Der Kaufalarm — vollstaendig, weil er allein auf dem Handy steht.

    Die Aufnahme auf die Wachliste klingelt nicht mehr (Ozan: nur Alarme, bei denen er
    einsteigen kann). Also muss der Einstiegsalarm selbst alles enthalten: was, wo,
    zu welchem Preis, wo raus, was es bringen kann — und warum gerade dieser Alarm
    durchs Tor kam.
    """
    aktie = w.klasse == "aktien"
    z = [
        f"{w.wer} · {'LONG' if w.long else 'SHORT'} · {w.note}",
        w.setup or ("Long-Setup" if w.long else "Short-Setup"),
        "",
    ]
    if w.broker:
        z.append(f"{'Kaufen' if w.long else 'Short'} bei: {w.broker}")
    hinweis = short_hinweis(w)
    if hinweis:
        z.append(hinweis)
    z.append(
        f"Einstieg  {_fmt(w.einstieg)} {_euro(w, w.einstieg)}".rstrip()
        + (f"   · Kurs jetzt {_fmt(kurs)}" if kurs else "")
    )

    # Bei Aktien ist der Euro-Wert der Ausfuehrungspreis (Trade Republic), also steht er
    # an jeder Marke. Bei Coins reicht er am Einstieg — gekauft wird im Dollarmarkt.
    def eur(v: float | None) -> str:
        return f" {_euro(w, v)}" if aktie and _euro(w, v) else ""

    z.append(f"Stop      {_fmt(w.stop)}{eur(w.stop)} {_abstand(w, w.stop)}")
    for name, v, rat in (
        ("Ziel 1", w.tp1, "  → ein Drittel verkaufen, Stop auf Einstieg"),
        ("Ziel 2", w.tp2, "  → zweites Drittel"),
        ("Ziel 3", w.tp3, ""),
    ):
        if v is not None:
            z.append(f"{name}    {_fmt(v)}{eur(v)} {_abstand(w, v)}{rat}")
    if w.rr:
        z.append(f"Chance-Risiko 1:{w.rr:.1f}".replace(".", ","))
    if aktie and w.eurusd:
        z.append("Trade Republic handelt in Euro — die Euro-Werte sind umgerechnet.")
    if bestaetigt:
        z += ["", f"Bestaetigt: {bestaetigt}"]
    if warum:
        z += ["", f"Warum dieser Alarm: {warum}"]
    if bilanz:
        z += [f"Bisher: {bilanz}", "Das ist gezaehlte Vergangenheit, kein Versprechen."]
    return "\n".join(z)


def _plan_text(w: Wache) -> str:
    """Der Alarmtext. Er fuehrt mit dem Setup, nicht mit dem Score.

    Ein Alarm, der mit "Score 63" anfaengt, zwingt den Leser, sich die Handelsidee
    selbst zusammenzureimen. Der Name des Setups und ein Satz zur These beantworten
    dagegen sofort die einzige Frage, die zaehlt: was ist das hier, und warum jetzt.
    """
    kopf = w.setup or ("Long-Setup" if w.long else "Short-Setup")
    zeilen = [
        f"{w.wer}  {'LONG' if w.long else 'SHORT'}  [{w.note}]",
        kopf,
        "",
        f"Einstieg  {_fmt(w.einstieg)}  ({w.einstieg_art})",
        f"Stop      {_fmt(w.stop)}",
    ]
    for name, v in (("Ziel 1", w.tp1), ("Ziel 2", w.tp2), ("Ziel 3", w.tp3)):
        if v is not None:
            r = w.r_bei(v)
            zeilen.append(f"{name}    {_fmt(v)}" + (f"   ({r:+.1f}R)" if r else ""))
    if w.rr:
        zeilen.append(f"CRV       1:{w.rr:.2f}")
    if w.einstieg > 0:
        zeilen.append(f"Risiko    {w.risiko / w.einstieg * 100:.1f} % vom Einstieg")
    if w.rs is not None:
        zeilen.append(f"Rel. St.  {w.rs:.0f}/100 in der eigenen Klasse")
    if w.these:
        zeilen += ["", w.these]
    if w.trigger:
        zeilen += ["", f"Ausloeser: {w.trigger}"]
    return "\n".join(zeilen)


class Wachliste:
    """Haelt die beobachteten Setups und leitet aus Kursbewegungen Ereignisse ab."""

    def __init__(self, wachen: dict[str, Wache] | None = None) -> None:
        self.wachen: dict[str, Wache] = dict(wachen or {})

    # ------------------------------------------------------------------ laden/speichern
    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> Wachliste:
        roh = (d or {}).get("wachen") or {}
        return cls({k: Wache.from_dict(v) for k, v in roh.items() if isinstance(v, dict)})

    def as_dict(self) -> dict[str, Any]:
        return {
            "stand": datetime.now(UTC).isoformat(),
            "wachen": {k: w.as_dict() for k, w in self.wachen.items()},
        }

    @property
    def offen(self) -> list[Wache]:
        return [w for w in self.wachen.values() if w.zustand not in ENDZUSTAENDE]

    # ------------------------------------------------------------------ aufnehmen
    def aufnehmen(self, zeilen: list[dict[str, Any]], *, jetzt: datetime) -> list[Ereignis]:
        """Neue handelbare Setups aus dem Scan uebernehmen. Bestehende nicht ueberschreiben.

        Bewusst kein Update laufender Wachen: ein Setup, das schon beobachtet wird, behaelt
        seine Marken. Sonst wandert der Stop mit jedem Scan, und hinterher weiss niemand,
        gegen welchen Plan das Ergebnis gemessen wurde.
        """
        from trading_agent.scanner.exposure import Grenzen, pruefe

        neu: list[Ereignis] = []
        """Wie viel darf ueberhaupt noch dazukommen?

        Am 11. September standen 40 Positionen gleichzeitig offen, 22 davon dieselbe
        Krypto-Wette; am 9. wurden elf an einem Tag ausgestoppt. Der Deckel greift
        deshalb VOR der Aufnahme, nicht hinterher.

        ABER: hier stand bis zum 17.09. ``Grenzen()`` — also der Deckel fuer ein ECHTES
        Depot: hoechstens acht offene Positionen. Die Wachliste ist aber kein Depot,
        sondern die Beobachtungsliste, aus der die Kaufsignale entstehen. Der Effekt war
        verheerend und hat lange niemand gesehen:

        Am 17.09. standen sechs Wachen auf "aktiv" — seit dem 13. und 15. September, ohne
        Stop und ohne Ziel zu treffen. Damit waren sechs der acht Plaetze belegt, bei
        Long sogar fuenf von sechs. Der Scanner fand weiter Setups, nur aufgenommen
        wurde fast keines mehr, und was nicht aufgenommen wird, kann auch nie einen
        Einstiegsalarm ausloesen. Ozans Beobachtung "ich kriege keine Kaufsignale" war
        also voellig richtig, und die Ursache war dieser Deckel — nicht die Zustellung.

        Beobachten kostet nichts. Was Geld kostet, ist KAUFEN, und darueber entscheidet
        Ozan; das Depot hat seine eigenen Grenzen im Portfolio-Reiter (Klumpen,
        Buendel, Hebel). Die Beobachtungsliste bekommt deshalb eigene, weite Grenzen.
        Gegen Flut schuetzt nicht der Deckel, sondern die Notenschwelle: gemeldet wird
        erst ab A-, und das bleibt so.
        """
        grenzen = Grenzen(
            max_offen=24,
            max_je_klasse=14,
            max_je_richtung=18,
            # Zwoelf gleichgerichtete Setups derselben Klasse duerfen beobachtet werden.
            # Dass zwoelf Kryptolongs im Kern EINE Wette sind, ist wahr — aber das ist
            # eine Aussage ueber das Depot, und dort steht sie auch: der Portfolio-Reiter
            # misst den Gleichlauf und raet vom Nachlegen ins selbe Buendel ab. Die
            # Beobachtungsliste deshalb blind zu machen, hilft niemandem.
            max_je_buendel=12,
            max_risiko_pct=12.0,
            # Die Regel, die tatsaechlich zugeschlagen hat. Mit den Depotwerten
            # (25 % je Position, 100 % gesamt) ist die Liste schon bei VIER Wachen
            # "voll investiert" — ab der fuenften lautete die Antwort woertlich:
            # "Das Kapital ist ausgelastet. Mehr ginge nur auf Kredit." Fuer eine
            # Beobachtungsliste ist das sinnlos: Beobachten bindet kein Kapital.
            # 4 % je Wache mal 24 Plaetze ergibt 96 % — die Regel bindet damit nie,
            # bevor einer der echten Deckel oben greift.
            max_anteil_je_position=0.04,
            max_anteil_gesamt=1.0,
        )
        laufend: list[dict[str, Any]] = [
            {"klasse": v.klasse, "richtung": v.richtung}
            for v in self.wachen.values()
            if v.zustand == Zustand.AKTIV.value
        ]
        alt_einstieg = {
            k: v.einstieg for k, v in self.wachen.items() if v.zustand == Zustand.WARTET.value
        }
        from trading_agent.scanner.alarm_tor import basis as _basis

        for z in zeilen:
            name = str(z.get("instrument") or "")
            if not name or not z.get("handelbar"):
                continue
            # Derselbe Coin nur EINMAL. Am 20. und 21.09. standen LINKUSD und LINKUSDT,
            # ETCUSD und ETCUSDT, ETHUSD und ETHUSDT, KASUSD und KASUSDT gleichzeitig auf
            # der Liste — dieselbe Wette zweimal, mit zwei Alarmen. Wer zuerst da war,
            # bleibt; das zweite Paar wird nicht aufgenommen.
            b = _basis(name)
            if any(
                k != name and _basis(k) == b and v.zustand not in ENDZUSTAENDE
                for k, v in self.wachen.items()
            ):
                continue
            vorhanden = self.wachen.get(name)
            if vorhanden is not None and vorhanden.zustand not in ENDZUSTAENDE:
                # Laufender Trade: Plan bleibt, wie er war.
                if vorhanden.zustand != Zustand.WARTET.value:
                    continue
                neuer_einstieg = z.get("einstieg")
                if neuer_einstieg is None or vorhanden.einstieg <= 0:
                    continue
                abweichung = abs(float(neuer_einstieg) - vorhanden.einstieg) / vorhanden.einstieg
                if abweichung * 100.0 < PLAN_VERALTET_PCT:
                    continue
                # Der Plan ist ueberholt — er wird unten neu gebaut.
                del self.wachen[name]
                vorhanden = None
            einstieg = z.get("einstieg")
            stop = z.get("invalidierung")
            if einstieg is None or stop is None:
                continue
            # Ein Setup, fuer das kein Platz mehr ist, wird gar nicht erst aufgenommen.
            # Es bleibt in der Rangliste sichtbar — es klingelt nur nicht.
            if not pruefe(
                {"klasse": z.get("klasse"), "richtung": z.get("richtung")}, laufend, grenzen
            ).ja:
                continue
            setup = z.get("setup") or {}
            # Die Ziele kommen aus dem Handelsplan, nicht roh aus der Liquiditaetsliste.
            # Der Plan sorgt dafuer, dass Ziel 1 nie naeher liegt als der Stop weg ist —
            # sonst steht im Alarm ein Chance-Risiko, das es so nie gab.
            pl = z.get("plan") or {}
            w = Wache(
                instrument=name,
                klasse=str(z.get("klasse") or ""),
                richtung=str(z.get("richtung") or ""),
                note=str(z.get("note") or z.get("urteil") or ""),
                einstieg=float(einstieg),
                einstieg_art=str(z.get("einstieg_art") or "sofort"),
                stop=float(stop),
                tp1=pl.get("tp1", z.get("ziel")),
                tp2=pl.get("tp2", z.get("tp2")),
                tp3=pl.get("tp3", z.get("tp3")),
                score=float(z.get("score") or 0.0),
                rr=pl.get("crv", z.get("rr")),
                erwartet_pct=z.get("erwartete_bewegung_pct"),
                aufgenommen=jetzt.isoformat(),
                zuletzt=jetzt.isoformat(),
                setup=str(setup.get("name") or ""),
                these=str(setup.get("these") or ""),
                trigger=str(setup.get("trigger") or ""),
                rs=z.get("rs"),
                name=str(z.get("name") or ""),
                broker=str(z.get("broker") or ""),
                eurusd=(float(z["eurusd"]) if z.get("eurusd") else None),
            )
            if w.risiko <= 0 or w.richtung not in ("long", "short"):
                continue
            if w.einstieg > 0 and w.risiko / w.einstieg < MIN_STOP_ANTEIL:
                continue
            ersetzt = alt_einstieg.get(name)
            self.wachen[name] = w
            # Was gerade aufgenommen wurde, zaehlt ab sofort mit. Vorher wurde
            # ``laufend`` einmal VOR der Schleife gebaut und nie fortgeschrieben — der
            # Deckel sah also nur die bereits aktiven Wachen und konnte einen einzelnen
            # Scan-Durchgang gar nicht begrenzen. Zusammen mit der Kapitalregel ergab
            # das die schlechteste aller Kombinationen: entweder alles oder nichts.
            laufend.append({"klasse": w.klasse, "richtung": w.richtung})
            sofort = w.einstieg_art == "sofort"
            neu.append(
                Ereignis(
                    art="PLAN_AKTUALISIERT" if ersetzt is not None else "NEUES_SETUP",
                    instrument=name,
                    dringend=w.note in ("A+", "A", "A_PLUS", "A_MINUS", "A−"),
                    titel=(
                        f"Plan angepasst  {name}"
                        if ersetzt is not None
                        else f"{w.note} {'BUY' if w.long else 'SELL'}  {name}"
                    ),
                    text=(
                        (
                            f"Der Einstieg wandert von {_fmt(ersetzt)} auf "
                            f"{_fmt(w.einstieg)} — der Markt ist weitergelaufen, der alte "
                            "Wert passt nicht mehr.\n\n"
                            if ersetzt is not None
                            else ""
                        )
                        + _plan_text(w)
                        + (
                            "\n\nEinstieg liegt beim aktuellen Kurs."
                            if sofort
                            else f"\n\nNoch nicht einsteigen — warten, bis {_fmt(w.einstieg)} erreicht ist."
                        )
                    ),
                    dedup_key=(
                        f"plan:{name}:{w.einstieg:.8g}"
                        if ersetzt is not None
                        else f"setup:{name}:{w.note}:{w.richtung}"
                    ),
                )
            )
        return neu

    # ------------------------------------------------------------------ pruefen
    def pruefen(
        self,
        kurse: dict[str, dict[str, float]],
        *,
        jetzt: datetime,
        kerzen: dict[str, list[Any]] | None = None,
    ) -> list[Ereignis]:
        """``kurse`` je Instrument: ``{"hoch":…, "tief":…, "letzter":…}`` seit der letzten Pruefung.

        ``kerzen`` sind die geschlossenen Kerzen desselben Fensters. Sind sie da, entscheidet
        ``scanner.trigger`` ueber den Einstieg — eine beruehrte Marke allein reicht dann
        nicht mehr. Fehlen sie, bleibt es beim alten Verhalten; ein fehlender Kurs-Feed
        darf nicht dazu fuehren, dass gar nichts mehr passiert.
        """
        from trading_agent.scanner.trigger import pruefe_einstieg

        kerzen = kerzen or {}
        ereignisse: list[Ereignis] = []
        for name, w in list(self.wachen.items()):
            if w.zustand in ENDZUSTAENDE:
                continue
            k = kurse.get(name)
            if k is None:
                # Keine Kurse — nur die Haltbarkeit pruefen, sonst nichts behaupten.
                ereignisse += self._haltbarkeit(w, jetzt)
                continue
            hoch, tief = float(k.get("hoch", 0.0)), float(k.get("tief", 0.0))
            letzter = float(k.get("letzter", 0.0))
            if hoch <= 0 or tief <= 0:
                continue
            w.zuletzt = jetzt.isoformat()

            if w.zustand == Zustand.WARTET.value:
                getroffen = tief <= w.einstieg if w.long else hoch >= w.einstieg

                # Bestaetigung, wo Kerzen vorliegen. Der Befund dahinter: 29 von 58
                # abgeschlossenen Trades kamen nie auch nur 0,3 R ins Plus — sie liefen
                # ab der ersten Minute gegen uns, weil bei blosser Beruehrung gekauft
                # wurde. Ein Wasserfall geht durch jede Marke unter sich hindurch.
                b = None
                reihe = kerzen.get(name) or []
                if getroffen and len(reihe) >= 2:
                    b = pruefe_einstieg(
                        reihe,
                        einstieg=w.einstieg,
                        lang=w.long,
                        atr=abs(w.einstieg - w.stop) / 1.5,
                    )
                    if b.hinfaellig:
                        w.zustand = Zustand.INVALIDIERT.value
                        ereignisse.append(
                            Ereignis(
                                art="INVALIDIERT",
                                instrument=name,
                                dringend=False,
                                titel=f"Setup gebrochen  {name}",
                                text=b.hinfaellig,
                                dedup_key=f"gebrochen:{name}:{w.aufgenommen}",
                            )
                        )
                        continue
                    if not b.ja:
                        # Marke erreicht, aber nicht bestaetigt: weiter warten statt
                        # blind einzusteigen. Das kostet Einstiegskurs und verhindert
                        # genau die Trades, die nie funktioniert haben.
                        ereignisse += self._haltbarkeit(w, jetzt)
                        continue

                if getroffen:
                    w.zustand = Zustand.AKTIV.value
                    w.einstiegskurs = b.kurs if (b is not None and b.kurs) else w.einstieg
                    ereignisse.append(
                        Ereignis(
                            art="EINSTIEG",
                            instrument=name,
                            dringend=True,
                            titel=(f"{'KAUFEN' if w.long else 'SHORT'}  {w.wer}  {w.note}"),
                            text=einstieg_text(
                                w,
                                kurs=letzter,
                                bestaetigt=(b.grund.capitalize() + ".") if b is not None else "",
                            ),
                            dedup_key=f"einstieg:{name}:{w.aufgenommen}",
                        )
                    )
                else:
                    ereignisse += self._haltbarkeit(w, jetzt)
                    continue

            if w.zustand != Zustand.AKTIV.value:
                continue

            r = w.r_bei(hoch if w.long else tief)
            if r is not None:
                w.bestes_r = max(w.bestes_r, r)
            r_schlecht = w.r_bei(tief if w.long else hoch)
            if r_schlecht is not None:
                w.schlechtestes_r = min(w.schlechtestes_r, r_schlecht)

            # Der Schutz-Stop laut Plan (nach Ziel 1 der Einstieg, nach Ziel 2 das Ziel 1).
            # Er wird VOR dem urspruenglichen Stop geprueft: wer dem Plan folgt, ist dort
            # schon raus. Bis zum 26.09. meldete die Wachliste nach Ziel 1 nur noch den
            # alten Stop — also einen Verlust von −1 R fuer eine Position, die laut
            # eigenem Rat laengst bei ±0 geschlossen war (HBAR am 23.09.).
            #
            # Die Wache laeuft danach fuer die Statistik weiter (der Vergleich „ganz"
            # gegen „drittel" in der Bilanz braucht den echten Verlauf); nur klingeln
            # tut zu diesem Trade nichts mehr.
            if w.schutz is not None and not w.raus:
                schutz_beruehrt = tief <= w.schutz if w.long else hoch >= w.schutz
                if schutz_beruehrt:
                    w.raus = True
                    w.raus_erreicht = list(w.erreicht)
                    w.raus_kurs = w.schutz
                    nach_tp2 = "TP2" in w.erreicht
                    ergebnis = (1.0 + 2.0 + 1.0) / 3.0 if nach_tp2 else 1.0 / 3.0
                    ereignisse.append(
                        Ereignis(
                            art="SCHUTZ",
                            instrument=name,
                            dringend=True,
                            titel=f"RAUS  {w.wer} — nachgezogener Stop",
                            text=(
                                f"{w.wer} ist zurueck bei {_fmt(w.schutz)} — dort liegt laut "
                                f"Plan dein Stop seit {'Ziel 2' if nach_tp2 else 'Ziel 1'}.\n"
                                f"Der Rest ist damit raus, "
                                + ("bei Ziel 1." if nach_tp2 else "ohne Verlust.")
                                + "\nErgebnis des ganzen Trades: rund "
                                + f"{ergebnis:+.2f} R".replace(".", ",")
                                + " (Teilverkaeufe eingerechnet)."
                            ),
                            dedup_key=f"schutz:{name}:{w.aufgenommen}",
                        )
                    )

            # Stop zuerst pruefen. Wenn im selben Fenster Ziel UND Stop beruehrt wurden,
            # ist nicht bekannt, was zuerst kam — und dann ist die pessimistische Annahme
            # die einzige, die eine Statistik nicht schoenrechnet.
            stop_beruehrt = tief <= w.stop if w.long else hoch >= w.stop
            if stop_beruehrt:
                w.zustand = Zustand.STOP.value
                ereignisse.append(
                    Ereignis(
                        art="STOP",
                        instrument=name,
                        dringend=True,
                        titel=f"STOP  {w.wer} — raus",
                        text=(
                            f"{w.wer} hat den Stop bei {_fmt(w.stop)} beruehrt.\n"
                            f"Ergebnis {w.schlechtestes_r:.2f}R, bestes zwischendurch "
                            f"{w.bestes_r:+.2f}R.\nDie These ist damit beendet."
                        ),
                        dedup_key=f"stop:{name}:{w.aufgenommen}",
                    )
                )
                continue

            for marke, ziel in (("TP1", w.tp1), ("TP2", w.tp2), ("TP3", w.tp3)):
                if ziel is None or marke in w.erreicht:
                    continue
                getroffen = hoch >= ziel if w.long else tief <= ziel
                if not getroffen:
                    continue
                w.erreicht.append(marke)
                letztes = marke == "TP3" or (marke == "TP2" and w.tp3 is None)
                basis_kurs = w.einstiegskurs if w.einstiegskurs is not None else w.einstieg
                if marke == "TP1":
                    w.schutz = basis_kurs
                elif marke == "TP2" and w.tp1 is not None:
                    w.schutz = w.tp1
                rat = {
                    "TP1": "Ein Drittel verkaufen und den Stop auf den Einstieg "
                    f"({_fmt(basis_kurs)}) ziehen — ab hier kann der Trade nicht mehr "
                    "verlieren.",
                    "TP2": "Zweites Drittel verkaufen. Rest laufen lassen, Stop auf Ziel 1 "
                    f"({_fmt(w.tp1)}) nachziehen.",
                    "TP3": "Letztes Ziel erreicht — Rest verkaufen. Das war der Plan.",
                }[marke]
                nummer = marke[-1]
                ereignisse.append(
                    Ereignis(
                        art="TP",
                        instrument=name,
                        dringend=marke != "TP3",
                        titel=f"ZIEL {nummer}  {w.wer} — "
                        + ("Rest verkaufen" if marke == "TP3" else "Teil verkaufen"),
                        text=(
                            f"{w.wer} hat Ziel {nummer} bei {_fmt(ziel)} erreicht "
                            f"({w.r_bei(ziel):+.2f}R).\n{rat}"
                        ),
                        dedup_key=f"{marke.lower()}:{name}:{w.aufgenommen}",
                    )
                )
                if letztes:
                    w.zustand = Zustand.ZIEL_ERREICHT.value
        return ereignisse

    def _haltbarkeit(self, w: Wache, jetzt: datetime) -> list[Ereignis]:
        # Nur ein Setup, das noch auf seinen Einstieg WARTET, kann ablaufen. Bis 30.09. lief
        # diese Pruefung auch fuer laufende Trades, sobald ein Lauf keinen Kurs hatte — eine
        # Aktie, die zehn Tage nach der Aufnahme am Wochenende ohne Kurs dastand, wurde
        # mitten im Trade mit 0 R beendet, samt der Meldung, der Einstieg sei nie erreicht.
        if w.zustand != Zustand.WARTET.value:
            return []
        try:
            seit = datetime.fromisoformat(w.aufgenommen)
        except ValueError:
            return []
        if jetzt - seit < HALTBARKEIT:
            return []
        w.zustand = Zustand.ABGELAUFEN.value
        return [
            Ereignis(
                art="ABGELAUFEN",
                instrument=w.instrument,
                dringend=False,
                titel=f"Setup abgelaufen  {w.instrument}",
                text=(
                    f"{w.instrument} hat den Einstieg bei {_fmt(w.einstieg)} in "
                    f"{HALTBARKEIT.days} Tagen nicht erreicht. Das Chartbild von damals "
                    "beschreibt den Markt nicht mehr — die Wache endet."
                ),
                dedup_key=f"abgelaufen:{w.instrument}:{w.aufgenommen}",
            )
        ]

    # ------------------------------------------------------------------ Invalidierung
    def gegen_scan(self, zeilen: list[dict[str, Any]], *, jetzt: datetime) -> list[Ereignis]:
        """Setups verwerfen, deren Grundlage der neue Scan nicht mehr hergibt."""
        nach_name = {str(z.get("instrument")): z for z in zeilen}
        ereignisse: list[Ereignis] = []
        for name, w in self.wachen.items():
            if w.zustand in ENDZUSTAENDE:
                continue
            z = nach_name.get(name)
            if z is None:
                continue
            richtung_neu = str(z.get("richtung") or "")
            if richtung_neu and richtung_neu != w.richtung:
                # Vorher stand hier ``dringend=w.zustand == AKTIV`` — ausgewertet NACHDEM
                # der Zustand schon auf „invalidiert" gesetzt war. Ein laufender Trade,
                # dessen Analyse gedreht hat, war damit nie dringend und ging nie aufs
                # Telefon: genau der Moment, in dem man raus sollte, blieb stumm.
                lief = w.zustand == Zustand.AKTIV.value
                w.zustand = Zustand.INVALIDIERT.value
                if lief:
                    kurs = z.get("kurs")
                    w.ausstiegskurs = float(kurs) if kurs else None
                    r = w.r_bei(float(kurs)) if kurs else None
                    ereignisse.append(
                        Ereignis(
                            art="AUSSTIEG",
                            instrument=name,
                            dringend=True,
                            titel=f"AUSSTEIGEN  {w.wer} — Analyse gedreht",
                            text=(
                                f"Die Analyse zu {w.wer} dreht auf {richtung_neu.upper()}, "
                                f"der Trade laeuft {w.richtung.upper()}.\n"
                                "Laut Plan heisst ein Strukturbruch gegen die Richtung: raus, "
                                "auch wenn der Stop noch nicht erreicht ist."
                                + (f"\nKurs {_fmt(float(kurs))}" if kurs else "")
                                + (f", Stand {r:+.2f} R".replace(".", ",") if r is not None else "")
                                + (
                                    ""
                                    if not w.erreicht
                                    else f" (erreicht: {', '.join(w.erreicht)})"
                                )
                                + "."
                            ),
                            dedup_key=f"ausstieg:{name}:{w.aufgenommen}",
                        )
                    )
                    continue
                ereignisse.append(
                    Ereignis(
                        art="INVALIDIERT",
                        instrument=name,
                        dringend=False,
                        titel=f"Setup ungueltig  {w.wer}",
                        text=(
                            f"Die Analyse dreht auf {richtung_neu.upper()}, die Wache lief auf "
                            f"{w.richtung.upper()}. Kein Einstieg mehr auf dieser Grundlage."
                        ),
                        dedup_key=f"invalid:{name}:{w.aufgenommen}",
                    )
                )
        return ereignisse

    def aufraeumen(self, *, behalten: int = 60) -> int:
        """Abgeschlossene Wachen begrenzen, damit die Datei nicht endlos waechst."""
        fertig = [(w.zuletzt, k) for k, w in self.wachen.items() if w.zustand in ENDZUSTAENDE]
        fertig.sort(reverse=True)
        weg = [k for _, k in fertig[behalten:]]
        for k in weg:
            del self.wachen[k]
        return len(weg)


__all__ = [
    "ENDZUSTAENDE",
    "HALTBARKEIT",
    "MIN_STOP_ANTEIL",
    "PLAN_VERALTET_PCT",
    "Ereignis",
    "Wache",
    "Wachliste",
    "Zustand",
    "einstieg_text",
]
