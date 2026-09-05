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
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Wache:
        bekannt = set(cls.__slots__)
        return cls(**{k: v for k, v in d.items() if k in bekannt})


@dataclass(frozen=True, slots=True)
class Ereignis:
    """Ein Zustandsuebergang, der eine Meldung wert ist."""

    art: str  # NEUES_SETUP | EINSTIEG | TP | STOP | INVALIDIERT | ABGELAUFEN
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


def _plan_text(w: Wache) -> str:
    zeilen = [
        f"{w.instrument}  {'LONG' if w.long else 'SHORT'}  [{w.note}]  Score {w.score:.0f}",
        f"Einstieg  {_fmt(w.einstieg)}  ({w.einstieg_art})",
        f"Stop      {_fmt(w.stop)}",
    ]
    for name, v in (("Ziel 1", w.tp1), ("Ziel 2", w.tp2), ("Ziel 3", w.tp3)):
        if v is not None:
            zeilen.append(f"{name}    {_fmt(v)}")
    if w.rr:
        zeilen.append(f"CRV       1:{w.rr:.2f}")
    if w.erwartet_pct:
        zeilen.append(f"Erwartet  {w.erwartet_pct:+.1f} %")
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
        neu: list[Ereignis] = []
        for z in zeilen:
            name = str(z.get("instrument") or "")
            if not name or not z.get("handelbar"):
                continue
            vorhanden = self.wachen.get(name)
            if vorhanden is not None and vorhanden.zustand not in ENDZUSTAENDE:
                continue
            einstieg = z.get("einstieg")
            stop = z.get("invalidierung")
            if einstieg is None or stop is None:
                continue
            w = Wache(
                instrument=name,
                klasse=str(z.get("klasse") or ""),
                richtung=str(z.get("richtung") or ""),
                note=str(z.get("note") or z.get("urteil") or ""),
                einstieg=float(einstieg),
                einstieg_art=str(z.get("einstieg_art") or "sofort"),
                stop=float(stop),
                tp1=z.get("ziel"),
                tp2=z.get("tp2"),
                tp3=z.get("tp3"),
                score=float(z.get("score") or 0.0),
                rr=z.get("rr"),
                erwartet_pct=z.get("erwartete_bewegung_pct"),
                aufgenommen=jetzt.isoformat(),
                zuletzt=jetzt.isoformat(),
            )
            if w.risiko <= 0 or w.richtung not in ("long", "short"):
                continue
            self.wachen[name] = w
            sofort = w.einstieg_art == "sofort"
            neu.append(
                Ereignis(
                    art="NEUES_SETUP",
                    instrument=name,
                    dringend=w.note in ("A+", "A", "A_PLUS", "A_MINUS", "A−"),
                    titel=f"{w.note} {'BUY' if w.long else 'SELL'}  {name}",
                    text=_plan_text(w)
                    + (
                        "\n\nEinstieg liegt beim aktuellen Kurs."
                        if sofort
                        else f"\n\nNoch nicht einsteigen — warten, bis {_fmt(w.einstieg)} erreicht ist."
                    ),
                    dedup_key=f"setup:{name}:{w.note}:{w.richtung}",
                )
            )
        return neu

    # ------------------------------------------------------------------ pruefen
    def pruefen(self, kurse: dict[str, dict[str, float]], *, jetzt: datetime) -> list[Ereignis]:
        """``kurse`` je Instrument: ``{"hoch":…, "tief":…, "letzter":…}`` seit der letzten Pruefung."""
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
                if getroffen:
                    w.zustand = Zustand.AKTIV.value
                    w.einstiegskurs = w.einstieg
                    ereignisse.append(
                        Ereignis(
                            art="EINSTIEG",
                            instrument=name,
                            dringend=True,
                            titel=f"EINSTIEG ERREICHT  {name}  {'LONG' if w.long else 'SHORT'}",
                            text=(
                                f"{name} hat {_fmt(w.einstieg)} erreicht (Kurs jetzt {_fmt(letzter)}).\n"
                                f"Stop {_fmt(w.stop)} · Ziel 1 {_fmt(w.tp1)}"
                                + (f" · Ziel 2 {_fmt(w.tp2)}" if w.tp2 else "")
                                + (f"\nCRV 1:{w.rr:.2f}" if w.rr else "")
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
                        titel=f"STOP  {name}",
                        text=(
                            f"{name} hat den Stop bei {_fmt(w.stop)} beruehrt.\n"
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
                rat = {
                    "TP1": "Teilgewinn nehmen und den Stop auf den Einstieg ziehen — ab hier "
                    "kann der Trade nicht mehr verlieren.",
                    "TP2": "Zweiter Teilgewinn. Rest laufen lassen, Stop unter das letzte "
                    "hoehere Tief nachziehen.",
                    "TP3": "Ziel erreicht. Das war der Plan.",
                }[marke]
                ereignisse.append(
                    Ereignis(
                        art="TP",
                        instrument=name,
                        dringend=marke != "TP3",
                        titel=f"{marke} ERREICHT  {name}",
                        text=(f"{name} hat {_fmt(ziel)} erreicht ({w.r_bei(ziel):+.2f}R).\n{rat}"),
                        dedup_key=f"{marke.lower()}:{name}:{w.aufgenommen}",
                    )
                )
                if letztes:
                    w.zustand = Zustand.ZIEL_ERREICHT.value
        return ereignisse

    def _haltbarkeit(self, w: Wache, jetzt: datetime) -> list[Ereignis]:
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
                w.zustand = Zustand.INVALIDIERT.value
                ereignisse.append(
                    Ereignis(
                        art="INVALIDIERT",
                        instrument=name,
                        dringend=w.zustand == Zustand.AKTIV.value,
                        titel=f"Setup ungueltig  {name}",
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
    "Ereignis",
    "Wache",
    "Wachliste",
    "Zustand",
]
