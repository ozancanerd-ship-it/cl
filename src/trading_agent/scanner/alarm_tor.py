"""Das Alarm-Tor — was aufs Handy darf und was nicht.

Ozan, 26.09.: „Die Alarme sind irgendwie schlecht geworden … bessere Alarme, da wo es
sich wirklich lohnt."

WAS SCHIEFLIEF — GEMESSEN, NICHT VERMUTET

Die App sagte seit dem 16.09. „Neue Setups nur ab A−. B und B+ klingeln nicht." Fuer die
Browser-Meldung stimmte das. Der Waechter in der CI (``watch_levels.py``) schickte aber
JEDEN bestaetigten Einstieg aufs Telefon, egal welche Note — dazu Ziel und Stop fuer
jeden dieser Trades, auch fuer die, von denen Ozan nie etwas gekauft hat. Auf der
Wachliste standen am 26.09. 88 Wachen, 60 davon abgeschlossen; gemeldet wurde fast alles.

Die eigene Bilanz aus genau diesen 60 Signalen sagt dazu Folgendes:

* Die **Note** trennt nicht: A− lag bei −0,25 R je Trade, B+ bei ±0.
* Die **Setup-Art** trennt sehr wohl. „Ausbruch aus der Basis" +7,5 R aus 16 Signalen,
  drei davon bis zum letzten Ziel. „Rueckeroberung nach Liquiditaetsgriff" −5 R aus 18
  Signalen, kein einziges bis zum ersten Ziel. Signale ohne benanntes Setup: negativ.
* Doppelte Meldungen: LINKUSD und LINKUSDT, ETHUSD und ETHUSDT … derselbe Coin zweimal.

WAS DAS TOR PRUEFT

Ein bestaetigter Einstieg geht nur dann aufs Telefon, wenn ALLES davon stimmt:

1. **Benanntes Setup.** Ohne Handelsidee kein Alarm — das stand schon in der Wachliste,
   geprueft wurde es nie.
2. **Note.** A−, A oder A+. B+ nur, wenn die Setup-Art sich in der eigenen Bilanz
   **bewaehrt** hat (siehe unten) — dann ist die Setup-Art die bessere Auskunft als die
   Note, und genau das zeigen die Zahlen.
3. **Bilanz der Setup-Art.** Hat eine Art (oder dieselbe Art in derselben Anlageklasse,
   oder dieselbe Richtung in derselben Klasse) nach mindestens :data:`MIN_FAELLE`
   tatsaechlich eingegangenen Trades unter dem Strich VERLOREN, klingelt sie nicht mehr.
   Sie wird weiter beobachtet und weiter gezaehlt — dreht die Bilanz, ist sie
   automatisch wieder frei. Gerechnet wird mit der Drittel-Regel, weil das der Plan ist,
   den Ozan bekommt (Teilverkauf an Ziel 1, Stop auf Einstand).
4. **Chance-Risiko** mindestens 1 : :data:`MIN_CRV`.
5. **Raum nach Kosten.** Ziel 1 muss mindestens :data:`MIN_ZIEL1_PCT` entfernt sein.
   Ein Ziel 0,4 % weg ist nach Gebuehren und Spread kein Gewinn, sondern Arbeit.

Und zur Laufzeit, im Waechter:

6. **Kein Doppel.** Derselbe Basiswert (LINK, egal ob USD oder USDT) hoechstens einmal
   in :data:`SPERRE_JE_WERT`.
7. **Kein Tagesdeckel (seit 03.10.).** Bis 02.10. gingen hoechstens drei Einstiege in
   24 Stunden aufs Handy; am 02.10. kam Bitcoin Cash deshalb nur in die App. Ozan:
   „soll mir nicht die Kaufanzahl begrenzen — wenn es sehr gute Alarme gibt, dann so
   viele es gibt, aber nur, wo es sich wirklich lohnt." Die Auswahl macht jetzt allein
   die Qualitaet (Pruefungen 1–5 und 6). :data:`MAX_JE_TAG` ist ``None``; wer wieder
   einen Deckel will, setzt dort eine Zahl.

Folgealarme (Ziel, Stop, Stop nachziehen, Ausstieg) klingeln nur fuer Trades, deren
Einstieg auch gemeldet wurde. Fuer alles andere hat Ozan kein Geld im Markt.

WAS DAS TOR NICHT IST

Es ist keine neue Strategie und keine Optimierung auf die Vergangenheit. Der Scanner
bewertet und beobachtet unveraendert alles; das Tor entscheidet nur, was klingelt. Jede
Sperre steht mit ihrem Grund in der App, und sie loest sich von selbst, wenn die
Zahlen es tun. Ein Vorlauf aus wenigen Wochen belegt keinen Edge — er reicht aber, um
nicht mehr jede Stunde eine Setup-Art aufs Telefon zu schicken, die bisher nur Geld
gekostet hat.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

#: Ab so vielen ENTSCHIEDENEN Trades gilt eine Bilanz als Aussage (siehe :func:`bilanz`).
#: Darunter bleibt die Setup-Art „offen" und wird weder gesperrt noch bevorzugt.
#:
#: Sechs ist wenig, und das ist Absicht: das Tor entscheidet nur, was KLINGELT, nicht was
#: beobachtet wird. Eine zu frueh gesperrte Art kostet ein paar Alarme und loest sich von
#: selbst, sobald sie wieder Gewinne zaehlt. Eine zu spaet gesperrte Art kostet Geld.
MIN_FAELLE = 6

#: Mindest-Chance-Risiko laut Plan (Ziel 3 gegen Stop).
MIN_CRV = 2.0

#: Mindestabstand bis Ziel 1 in Prozent, je Anlageklasse. Krypto hoeher, weil die
#: Gebuehren hoeher sind (Kraken 0,4 % je Seite mit Limit-Order).
MIN_ZIEL1_PCT: Mapping[str, float] = {"krypto": 1.5, "gold": 1.0, "aktien": 1.0}

#: Hoechstens so viele Einstiegs-Alarme je 24 Stunden — ``None`` = kein Deckel (seit 03.10.).
MAX_JE_TAG: int | None = None

#: Derselbe Basiswert klingelt hoechstens einmal in diesem Zeitraum.
SPERRE_JE_WERT = timedelta(hours=48)

#: Ab dieser Bilanz gilt eine Setup-Art als bewaehrt (Profitfaktor mit Drittel-Regel).
BEWAEHRT_PF = 1.5

#: Gewinner-Tor (01.10.). Ozan: „nicht einfach bei jedem Setup einen Alarm, sondern erst,
#: wenn es wirklich ein Gewinner ist." Bis dahin klingelte jede Setup-Art, die noch nicht
#: GESPERRT war — also auch jede, ueber die die eigene Bilanz noch gar nichts wusste. Am
#: 30.09. waren das drei Aktien-Shorts „Ruecksetzer im Trend": eine Art mit −1,3 R aus 4
#: Trades in der eigenen Bilanz und der schwaechsten Bilanz beider Haelften im 19-Monats-
#: Nachspiel (docs/SIGNAL-STUDIE-2026-09.md).
#:
#: Jetzt klingelt nur noch eine Setup-Art, die sich in der eigenen Bilanz BEWAEHRT hat
#: (mindestens MIN_FAELLE entschiedene Trades, im Plus, Profitfaktor ab BEWAEHRT_PF), und
#: nur mit Note ab A−. B+ klingelt nicht mehr. Beobachtet und gezaehlt wird weiter alles —
#: eine Art, die sich bewaehrt, wird von selbst frei.
#:
#: Ehrlich dazu: das ist eine strengere Auswahl, kein Beweis. Die eigene Bilanz ist klein;
#: das 19-Monats-Nachspiel hat fuer keinen Filter einen belastbaren Vorteil gezeigt. Was das
#: Tor sicher leistet: weniger Alarme, und keiner mehr fuer eine Art ohne Erfolgsnachweis.
NUR_BEWAEHRT = True

#: B+ klingelt wieder — aber NUR bei einer bewaehrten Setup-Art (01.10. abends).
#:
#: Das Gewinner-Tor hatte B+ mit gesperrt („strengere Auswahl, kein Beweis"). Nachgemessen
#: (docs/STOPS-OHNE-CHANCE-2026-10.md) traegt das nicht:
#:
#: * Eigene Bilanz, „Ausbruch aus der Basis" — die einzige freie Art: B+ 11 Trades
#:   +3,8 R, A− 2 Trades −0,7 R. Die Art ist ueberhaupt nur wegen ihrer B+-Trades
#:   bewaehrt; geklingelt haetten aber nur die A−-Trades.
#: * 19-Monats-Nachspiel, dieselbe Art: B+ In-Sample Ø +0,02 R (n 60), Out-of-Sample
#:   Ø +0,10 R (n 41); A− −0,05 R (n 24) und −0,49 R (n 11). B+ in BEIDEN Haelften nicht
#:   schlechter — das ist die Bedingung, unter der hier eine Regel geaendert wird.
#:
#: Was bleibt: die Setup-Art muss sich in der eigenen Bilanz bewaehrt haben, B klingelt nie,
#: hoechstens drei Alarme am Tag. Zuruecknehmen, wenn die B+-Alarme nach zehn entschiedenen
#: Trades unter Profitfaktor 1 liegen.
B_PLUS_BEI_BEWAEHRT = True

#: Eine Setup-Art wird in IHRER Klasse beurteilt (01.10. nachts).
#:
#: Bis dahin zaehlte fuer „bewaehrt" die Bilanz der Setup-Art ueber alle Klassen. Dann
#: entschieden Aktien-Trades darueber, ob ein Coin-Alarm klingelt — und zwar Aktien-KAEUFE,
#: die selbst nie klingeln (Aktien Long ist gesperrt). Der Fall: „Rueckeroberung nach
#: Liquiditaetsgriff" bei Coins 6 Trades, +5,2 R, Profitfaktor 6,2 — bei Aktien 4 Kaeufe,
#: −4,0 R. Zusammen PF 1,2, also „nicht bewaehrt", und Decred (A−) stieg am 01.10. ohne
#: Alarm ein. Ein Aktienverlust sagt nichts darueber, wie ein Coin-Setup laeuft.
#:
#: Jetzt: bewaehrt oder gesperrt wird je Klasse entschieden (``klasse_setup``), dazu
#: weiter die Sperre je Klasse und Richtung (``Aktien Long``). Zu wenige Faelle in der
#: eigenen Klasse heisst „offen" — die anderen Klassen springen nicht ein.
BILANZ_JE_KLASSE = True

_KLASSE_NAME = {"krypto": "Coins", "aktien": "Aktien", "gold": "Gold"}

NOTEN_A = frozenset({"A+", "A", "A−", "A-", "A_PLUS", "A_MINUS"})
NOTEN_B_PLUS = frozenset({"B+", "B_PLUS"})
_NOTE_PUNKTE = {"A+": 3, "A_PLUS": 3, "A": 2, "A−": 1, "A-": 1, "A_MINUS": 1, "B+": 0, "B_PLUS": 0}

#: Endungen der Handelspaare, in der Reihenfolge, in der sie abgeschnitten werden.
_QUOTES = ("USDT", "USDC", "USD", "EUR")

#: Zustaende, ab denen ein Trade zu Ende ist.
_ABGESCHLOSSEN = frozenset({"stop", "ziel_erreicht", "invalidiert", "abgelaufen"})

#: Wie viel R ein Ziel mit der Drittel-Regel einbringt (wie ``performance.ZIEL_R``).
_ZIEL_R = {"TP1": 1.0, "TP2": 2.0, "TP3": 3.5}


def basis(instrument: str) -> str:
    """``LINKUSDT`` → ``LINK``, ``BTCUSD`` → ``BTC``, ``NVDA`` → ``NVDA``."""
    n = str(instrument or "").upper()
    for q in _QUOTES:
        if n.endswith(q) and len(n) > len(q):
            return n[: -len(q)]
    return n


def note_kurz(note: str) -> str:
    return {"A_PLUS": "A+", "A_MINUS": "A−", "A-": "A−", "B_PLUS": "B+"}.get(note, note)


def _r_drittel(
    zustand: str, erreicht: Iterable[str], raus_erreicht: Iterable[str] | None = None
) -> float:
    """R nach Plan. ``raus_erreicht``: die Ziele, die erreicht waren, als der Schutz-Stop
    die Position beendet hat — was die Wache danach noch sieht, hat der Plan nicht mehr."""
    if raus_erreicht is not None:
        getroffen = [t for t in ("TP1", "TP2", "TP3") if t in set(raus_erreicht)]
        if not getroffen:
            return 0.0
        # Rest am Schutz-Stop: nach Ziel 2 liegt er auf Ziel 1, sonst auf Einstand.
        rest_r = _ZIEL_R["TP1"] if "TP2" in getroffen else 0.0
        return (sum(_ZIEL_R[t] for t in getroffen) + (3 - len(getroffen)) * rest_r) / 3.0
    getroffen = [t for t in ("TP1", "TP2", "TP3") if t in set(erreicht)]
    if not getroffen:
        return -1.0 if zustand == "stop" else 0.0
    return sum(_ZIEL_R[t] for t in getroffen) / 3.0


@dataclass(frozen=True, slots=True)
class Stand:
    """Die Bilanz einer Gruppe von Trades — Setup-Art, Klasse oder Richtung."""

    schluessel: str
    anzahl: int = 0
    summe_r: float = 0.0
    gewinn_r: float = 0.0
    verlust_r: float = 0.0
    ziel1: int = 0

    @property
    def schnitt(self) -> float | None:
        return self.summe_r / self.anzahl if self.anzahl else None

    @property
    def profitfaktor(self) -> float | None:
        return self.gewinn_r / abs(self.verlust_r) if self.verlust_r < 0 else None

    @property
    def urteil(self) -> str:
        """``bewaehrt`` | ``gesperrt`` | ``offen``."""
        if self.anzahl < MIN_FAELLE:
            return "offen"
        if self.summe_r < 0:
            return "gesperrt"
        pf = self.profitfaktor
        if self.summe_r > 0 and (pf is None or pf >= BEWAEHRT_PF):
            return "bewaehrt"
        return "offen"

    def satz(self, name: str) -> str:
        s = self.schnitt
        teile = f"{self.anzahl} Trades, {self.ziel1}× Ziel 1"
        if s is not None:
            teile += f", zusammen {self.summe_r:+.1f} R (Ø {s:+.2f} R)"
        teile = teile.replace(".", ",")
        if self.urteil == "gesperrt":
            return f"{name}: {teile} — bisher Verlust, klingelt nicht, bis die Bilanz dreht"
        if self.urteil == "bewaehrt":
            return f"{name}: {teile} — bewaehrt"
        return f"{name}: {teile} — noch zu wenig Faelle fuer ein Urteil"

    def as_dict(self) -> dict[str, Any]:
        return {
            "anzahl": self.anzahl,
            "summe_r": round(self.summe_r, 2),
            "schnitt_r": round(self.schnitt, 3) if self.schnitt is not None else None,
            "profitfaktor": (
                round(self.profitfaktor, 2) if self.profitfaktor is not None else None
            ),
            "ziel1": self.ziel1,
            "urteil": self.urteil,
        }


def _schluessel(setup: str, klasse: str, richtung: str) -> list[str]:
    aus = []
    if setup:
        aus.append(f"setup:{setup}")
        if klasse:
            aus.append(f"klasse_setup:{klasse}|{setup}")
    if klasse and richtung:
        aus.append(f"klasse_richtung:{klasse}|{richtung}")
    return aus


def bilanz(wachen: Iterable[Mapping[str, Any]]) -> dict[str, Stand]:
    """Die Bilanz je Setup-Art, je Klasse+Setup-Art und je Klasse+Richtung.

    Gezaehlt werden nur **entschiedene** Trades: eingegangen (es gibt einen
    Einstiegskurs) und mit bekanntem Ausgang — Stop, alle Ziele, oder wenigstens Ziel 1
    (ab dort steht der Stop laut Plan auf Einstand, der Trade kann nicht mehr verlieren).

    Nicht gezaehlt wird, was ohne Ergebnis endete: ein Setup, das seinen Einstieg nie
    erreicht hat, und ein Trade, der ungueltig wurde oder ablief, bevor er ein Ziel oder
    den Stop sah. Die alte Zaehlung buchte solche Faelle mit 0 R — bei Aktien waren das
    bis zum 26.09. fast alle, weil der Waechter sie jedes Wochenende als „ohne Kurs"
    geschlossen hat. Das hat die Bilanz verduennt, ohne etwas ueber die Setup-Art zu sagen.
    """
    roh: dict[str, list[tuple[float, bool]]] = {}
    for w in wachen:
        if not isinstance(w, Mapping):
            continue
        roh_raus = w.get("raus_erreicht")
        # Laut Plan draussen (Schutz-Stop) ist entschieden, auch wenn die Wache fuer die
        # Statistik weiterlaeuft — das Ergebnis steht fest.
        raus_e = (
            [str(x) for x in roh_raus] if w.get("raus") and isinstance(roh_raus, list) else None
        )
        if str(w.get("zustand") or "") not in _ABGESCHLOSSEN and raus_e is None:
            continue
        if w.get("einstiegskurs") is None:
            continue
        erreicht = [str(x) for x in (w.get("erreicht") or [])]
        zustand = str(w.get("zustand"))
        if raus_e is None and zustand not in ("stop", "ziel_erreicht") and "TP1" not in erreicht:
            continue
        r = _r_drittel(zustand, erreicht, raus_e)
        for k in _schluessel(
            str(w.get("setup") or ""), str(w.get("klasse") or ""), str(w.get("richtung") or "")
        ):
            roh.setdefault(k, []).append((r, "TP1" in erreicht))
    aus: dict[str, Stand] = {}
    for k, werte in roh.items():
        aus[k] = Stand(
            schluessel=k,
            anzahl=len(werte),
            summe_r=sum(r for r, _ in werte),
            gewinn_r=sum(r for r, _ in werte if r > 0),
            verlust_r=sum(r for r, _ in werte if r < 0),
            ziel1=sum(1 for _, z in werte if z),
        )
    return aus


def _anzeige(schluessel: str) -> str:
    art, _, rest = schluessel.partition(":")
    if art == "setup":
        return f"„{rest}“"
    klasse, _, zweites = rest.partition("|")
    kl = _KLASSE_NAME.get(klasse, klasse)
    if art == "klasse_setup":
        return f"„{zweites}“ bei {kl}"
    return f"{kl} {'Long' if zweites == 'long' else 'Short'}"


@dataclass(frozen=True, slots=True)
class Punkt:
    """Eine Pruefung des Tors mit ihrem Ergebnis in einem Satz."""

    name: str
    ok: bool
    satz: str

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "ok": self.ok, "satz": self.satz}


@dataclass(frozen=True, slots=True)
class Tor:
    """Das Ergebnis: darf es klingeln, und warum (nicht)."""

    ja: bool
    punkte: tuple[Punkt, ...] = field(default_factory=tuple)
    #: Rangfolge, wenn mehr Kandidaten da sind als der Tagesdeckel erlaubt.
    guete: float = 0.0
    #: Die Bilanz der Setup-Art in einem Satz — gehoert in den Alarmtext.
    bilanz_satz: str = ""

    @property
    def grund(self) -> str:
        """Der erste Grund, warum es NICHT klingelt — leer, wenn es klingelt."""
        for p in self.punkte:
            if not p.ok:
                return p.satz
        return ""

    def mit(self, punkt: Punkt) -> Tor:
        return Tor(
            ja=self.ja and punkt.ok,
            punkte=(*self.punkte, punkt),
            guete=self.guete,
            bilanz_satz=self.bilanz_satz,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "ja": self.ja,
            "grund": self.grund,
            "guete": round(self.guete, 2),
            "bilanz": self.bilanz_satz,
            "punkte": [p.as_dict() for p in self.punkte],
        }


def _pct(v: float) -> str:
    return f"{v:.1f} %".replace(".", ",")


def pruefe(
    *,
    note: str,
    setup: str,
    klasse: str,
    richtung: str,
    crv: float | None,
    ziel1_pct: float | None,
    stand: Mapping[str, Stand] | None,
) -> Tor:
    """Die festen Pruefungen 1–5. Rein, ohne Zeit und ohne Verlauf.

    Dieselbe Funktion laeuft im Scan (fuer die App) und im Waechter (fuers Telefon) —
    damit „Jetzt einsteigen" in der App und der Alarm auf dem Handy nie etwas anderes
    sagen. Genau dieser Widerspruch hat Ozan schon einmal einen Verkauf gekostet.
    """
    stand = stand or {}
    note = str(note or "")
    setup = str(setup or "")
    klasse = str(klasse or "")
    richtung = str(richtung or "")
    punkte: list[Punkt] = []

    # 1. Benanntes Setup
    punkte.append(
        Punkt(
            "setup",
            bool(setup),
            f"Setup „{setup}“" if setup else "kein benanntes Setup — ohne Handelsidee kein Alarm",
        )
    )

    # 3. Bilanz — vor der Note, weil die Note von ihr abhaengt.
    je_klasse = BILANZ_JE_KLASSE and bool(klasse) and bool(setup)
    schluessel = _schluessel(setup, klasse, richtung)
    if je_klasse:
        schluessel = [k for k in schluessel if not k.startswith("setup:")]
        art_schluessel: str | None = f"klasse_setup:{klasse}|{setup}"
    else:
        art_schluessel = f"setup:{setup}" if setup else None
    gruppen = [stand.get(k) for k in schluessel]
    gesperrt = [g for g in gruppen if g is not None and g.urteil == "gesperrt"]
    art = stand.get(art_schluessel) if art_schluessel else None
    bewaehrt = art is not None and art.urteil == "bewaehrt"
    if je_klasse and bewaehrt:
        # Die eigene Bilanz der Setup-Art schlaegt die grobe Sperre „Klasse + Richtung".
        # Seit das Archiv alle Trades seit 05.09. kennt (02.10.), steht „Coins Long" bei
        # 168 Trades und −49 R — fast alles aus der ersten Woche, ohne Setup-Namen, mit
        # bis zu 40 offenen Wachen. Die benannten Coin-Setups liegen im Plus (Ausbruch
        # +5,7 R aus 18, Rueckeroberung +9,5 R aus 24). Die grobe Sperre wuerde genau die
        # Setups stummschalten, die sich bewaehrt haben, wegen derer, die nie klingeln.
        gesperrt = [g for g in gesperrt if not g.schluessel.startswith("klasse_richtung:")]
    if gesperrt:
        g = gesperrt[0]
        punkte.append(Punkt("bilanz", False, g.satz(_anzeige(g.schluessel))))
    elif bewaehrt:
        punkte.append(Punkt("bilanz", True, art.satz(_anzeige(art.schluessel))))
    elif NUR_BEWAEHRT:
        bisher = (
            f"bisher {art.anzahl} Trades, zusammen {art.summe_r:+.1f} R".replace(".", ",")
            if art is not None and art.anzahl
            else "noch ohne entschiedene Trades"
        )
        punkte.append(
            Punkt(
                "bilanz",
                False,
                f"{_anzeige(art_schluessel) if art_schluessel else '„—“'} hat sich noch nicht "
                f"bewaehrt ({bisher}) — Alarm erst ab "
                f"{MIN_FAELLE} Trades im Plus mit Profitfaktor ab "
                f"{BEWAEHRT_PF:.1f}".replace(".", ","),
            )
        )
    elif art is not None:
        punkte.append(Punkt("bilanz", True, art.satz(_anzeige(art.schluessel))))
    else:
        punkte.append(
            Punkt("bilanz", True, "Setup-Art noch ohne abgeschlossene Trades — kein Urteil")
        )
    bilanz_satz = art.satz(_anzeige(art.schluessel)) if art is not None else ""

    # 2. Note
    nk = note_kurz(note)
    if note in NOTEN_A:
        punkte.append(Punkt("note", True, f"Note {nk}"))
    elif note in NOTEN_B_PLUS and bewaehrt and (B_PLUS_BEI_BEWAEHRT or not NUR_BEWAEHRT):
        punkte.append(
            Punkt("note", True, f"Note {nk} — reicht, weil sich die Setup-Art bewaehrt hat")
        )
    else:
        punkte.append(
            Punkt(
                "note",
                False,
                f"Note {nk or '—'} — Alarm erst ab A−"
                + (
                    " (B+ nur bei bewaehrter Setup-Art)"
                    if note in NOTEN_B_PLUS and (B_PLUS_BEI_BEWAEHRT or not NUR_BEWAEHRT)
                    else ""
                ),
            )
        )

    # 4. Chance-Risiko
    if crv is None:
        punkte.append(Punkt("crv", False, "kein Chance-Risiko-Verhaeltnis berechenbar"))
    else:
        ok = float(crv) >= MIN_CRV
        punkte.append(
            Punkt(
                "crv",
                ok,
                f"Chance-Risiko 1:{float(crv):.1f}".replace(".", ",")
                + ("" if ok else f" — unter 1:{MIN_CRV:.0f}"),
            )
        )

    # 5. Raum nach Kosten
    grenze = MIN_ZIEL1_PCT.get(klasse, 1.0)
    if ziel1_pct is None:
        punkte.append(Punkt("raum", False, "kein erstes Ziel — nichts zu verdienen"))
    else:
        ok = float(ziel1_pct) >= grenze
        punkte.append(
            Punkt(
                "raum",
                ok,
                f"Ziel 1 ist {_pct(float(ziel1_pct))} entfernt"
                + ("" if ok else f" — unter {_pct(grenze)}, nach Gebuehren bleibt kaum etwas"),
            )
        )

    ja = all(p.ok for p in punkte)
    schnitt = art.schnitt if (art is not None and art.anzahl >= MIN_FAELLE) else 0.0
    guete = (
        10.0 * _NOTE_PUNKTE.get(note, -1)
        + 5.0 * max(-1.0, min(2.0, schnitt or 0.0))
        + min(float(crv or 0.0), 5.0)
    )
    return Tor(ja=ja, punkte=tuple(punkte), guete=guete, bilanz_satz=bilanz_satz)


def ziel1_prozent(einstieg: float | None, tp1: float | None) -> float | None:
    if not einstieg or tp1 is None or einstieg <= 0:
        return None
    return abs(float(tp1) - float(einstieg)) / float(einstieg) * 100.0


def termin_punkt(sperre: str | None) -> Punkt | None:
    """Pruefung 8: kein Einstiegs-Alarm kurz vor Quartalszahlen (Masterplan §8).

    An so einem Tag springt der Kurs oft ueber jeden Stop hinweg — das R, mit dem der
    Plan rechnet, gilt dann nicht mehr. Die Sperre setzt der Scan (``termin_sperre``)."""
    if not sperre:
        return None
    return Punkt("termin", False, str(sperre))


def pruefe_zeile(z: Mapping[str, Any], stand: Mapping[str, Stand] | None) -> Tor:
    """:func:`pruefe` fuer eine Scan-Zeile."""
    pl = z.get("plan") if isinstance(z.get("plan"), Mapping) else {}
    st = z.get("setup") if isinstance(z.get("setup"), Mapping) else {}
    einstieg = (pl or {}).get("einstieg") or z.get("einstieg")
    tp1 = (pl or {}).get("tp1") or z.get("ziel")
    tor = pruefe(
        note=str(z.get("note") or ""),
        setup=str((st or {}).get("name") or ""),
        klasse=str(z.get("klasse") or ""),
        richtung=str(z.get("richtung") or ""),
        crv=(pl or {}).get("crv") if (pl or {}).get("crv") is not None else z.get("rr"),
        ziel1_pct=ziel1_prozent(einstieg, tp1),
        stand=stand,
    )
    punkt = termin_punkt(z.get("termin_sperre"))
    return tor.mit(punkt) if punkt is not None else tor


def pruefe_wache(w: Any, stand: Mapping[str, Stand] | None) -> Tor:
    """:func:`pruefe` fuer eine Wache der Wachliste."""
    return pruefe(
        note=str(getattr(w, "note", "") or ""),
        setup=str(getattr(w, "setup", "") or ""),
        klasse=str(getattr(w, "klasse", "") or ""),
        richtung=str(getattr(w, "richtung", "") or ""),
        crv=getattr(w, "rr", None),
        ziel1_pct=ziel1_prozent(getattr(w, "einstieg", None), getattr(w, "tp1", None)),
        stand=stand,
    )


def deckel(
    tor: Tor,
    *,
    instrument: str,
    verlauf: Iterable[tuple[datetime, str]],
    jetzt: datetime,
) -> Tor:
    """Pruefungen 6 und 7: kein Doppel, Tagesdeckel. ``verlauf`` = (Zeit, Instrument)
    der bereits gemeldeten Einstiege."""
    if not tor.ja:
        return tor
    b = basis(instrument)
    frisch = [(t, n) for t, n in verlauf if jetzt - t < timedelta(hours=24)]
    doppelt = [t for t, n in verlauf if basis(n) == b and jetzt - t < SPERRE_JE_WERT]
    if doppelt:
        return tor.mit(
            Punkt(
                "doppel",
                False,
                f"{b} hat vor {int((jetzt - max(doppelt)).total_seconds() // 3600)} Std. "
                "schon einen Einstiegs-Alarm bekommen",
            )
        )
    if MAX_JE_TAG is not None and len(frisch) >= MAX_JE_TAG:
        return tor.mit(
            Punkt(
                "deckel",
                False,
                f"schon {len(frisch)} Einstiegs-Alarme in 24 Std. — mehr gehen nicht aufs Handy",
            )
        )
    return tor.mit(Punkt("deckel", True, f"{len(frisch) + 1}. Einstiegs-Alarm in 24 Std."))


def fuers_telefon(
    ereignisse: list[Any],
    wachen: Mapping[str, Any],
    *,
    raus_vorher: Mapping[str, bool],
    jetzt: datetime,
    erlaubt: Iterable[str] = ("EINSTIEG", "TP", "STOP", "SCHUTZ", "AUSSTIEG"),
    alle: bool = False,
    sperren: Mapping[str, str] | None = None,
    archiv: Iterable[Mapping[str, Any]] = (),
) -> tuple[list[Any], list[str]]:
    """Welche Ereignisse aufs Telefon gehen — in ihrer Reihenfolge, Einstiege ergaenzt.

    Veraendert die Wachen: ``gemeldet`` bei freigegebenen Einstiegen, ``tor_grund`` bei
    abgewiesenen. Gibt die zu sendenden Ereignisse zurueck und je abgewiesenem Einstieg
    einen Satz fuers Protokoll.

    * ``EINSTIEG`` nur durch das Tor, die besten zuerst (Tagesdeckel nur, wenn gesetzt).
    * Folgealarme (``TP``, ``STOP``, ``SCHUTZ``, ``AUSSTIEG``) nur fuer gemeldete Trades,
      die vor diesem Lauf nicht schon draussen waren. Stop und Schutz-Stop im selben
      Fenster: nur der Schutz-Stop — wer dem Plan folgt, ist dort raus.
    * ``alle`` schaltet das Tor ab (fuer Tests von Hand).
    """
    from dataclasses import replace

    erlaubt = set(erlaubt)
    folgen = erlaubt - {"EINSTIEG"}
    # Dazu das Archiv: abgeschlossene Trades, die nicht mehr auf der Liste stehen (02.10.).
    aktuell = [w.as_dict() for w in wachen.values()]
    da = {f"{d.get('instrument')}|{d.get('aufgenommen')}" for d in aktuell}
    alt = [
        a
        for a in archiv
        if isinstance(a, Mapping) and f"{a.get('instrument')}|{a.get('aufgenommen')}" not in da
    ]
    stand = bilanz([*aktuell, *alt])
    verlauf: list[tuple[datetime, str]] = []
    for w in wachen.values():
        if getattr(w, "gemeldet", ""):
            try:
                verlauf.append((datetime.fromisoformat(w.gemeldet), w.instrument))
            except ValueError:
                continue

    freigegeben: set[int] = set()
    ersetzt: dict[int, Any] = {}
    notizen: list[str] = []
    einstiege = []
    for i, e in enumerate(ereignisse):
        w = wachen.get(e.instrument)
        if e.art == "EINSTIEG" and "EINSTIEG" in erlaubt and w is not None:
            einstiege.append((pruefe_wache(w, stand), i, e, w))
    einstiege.sort(key=lambda t: -t[0].guete)
    for tor, i, e, w in einstiege:
        punkt = termin_punkt((sperren or {}).get(w.instrument))
        if punkt is not None:
            tor = tor.mit(punkt)
        tor = deckel(tor, instrument=w.instrument, verlauf=verlauf, jetzt=jetzt)
        if tor.ja or alle:
            w.gemeldet = jetzt.isoformat()
            w.tor_grund = ""
            verlauf.append((jetzt, w.instrument))
            freigegeben.add(i)
            warum = "; ".join(p.satz for p in tor.punkte if p.ok and p.name != "bilanz")
            warum = warum.rstrip(".")
            ersetzt[i] = replace(
                e,
                text=e.text
                + (f"\n\nWarum dieser Alarm: {warum}." if warum else "")
                + (
                    f"\nBisher: {tor.bilanz_satz}.\nDas ist gezaehlte Vergangenheit, "
                    "kein Versprechen."
                    if tor.bilanz_satz
                    else ""
                ),
            )
        else:
            w.tor_grund = tor.grund
            notizen.append(f"{w.instrument}: Einstieg nur in der App — {tor.grund}")

    raus_jetzt = {e.instrument for e in ereignisse if e.art in ("SCHUTZ", "AUSSTIEG")}
    for i, e in enumerate(ereignisse):
        if e.art not in folgen:
            continue
        if alle:
            freigegeben.add(i)
            continue
        w = wachen.get(e.instrument)
        if w is None or not getattr(w, "gemeldet", "") or raus_vorher.get(e.instrument, False):
            continue
        if e.art == "STOP" and e.instrument in raus_jetzt:
            continue
        freigegeben.add(i)

    return [ersetzt.get(i, e) for i, e in enumerate(ereignisse) if i in freigegeben], notizen


def regeln_uebersicht(stand: Mapping[str, Stand]) -> dict[str, Any]:
    """Fuer die App: die Bilanz je Setup-Art mit Urteil, und die festen Schwellen."""
    if BILANZ_JE_KLASSE:
        # „Ausbruch aus der Basis · Coins" — so, wie das Tor tatsaechlich entscheidet.
        je_klasse: dict[str, Any] = {}
        for k, v in sorted(stand.items()):
            if not k.startswith("klasse_setup:"):
                continue
            klasse, _, setup = k.removeprefix("klasse_setup:").partition("|")
            je_klasse[f"{setup} · {_KLASSE_NAME.get(klasse, klasse)}"] = v.as_dict()
        # Eine Sperre je Klasse + Richtung gilt nicht fuer Setup-Arten, die sich in der
        # Klasse bewaehrt haben (siehe ``pruefe``) — das steht dabei, sonst liest man
        # „Coins Long gesperrt" neben „frei: Ausbruch · Coins".
        gesperrt_k = sorted(
            _anzeige(k)
            + (" (außer bewährte Setup-Arten)" if k.startswith("klasse_richtung:") else "")
            for k, v in stand.items()
            if v.urteil == "gesperrt" and not k.startswith("setup:")
        )
        return {
            "min_faelle": MIN_FAELLE,
            "min_crv": MIN_CRV,
            "min_ziel1_pct": dict(MIN_ZIEL1_PCT),
            "max_je_tag": MAX_JE_TAG,
            "sperre_je_wert_h": int(SPERRE_JE_WERT.total_seconds() // 3600),
            "setup_arten": je_klasse,
            "gesperrt": gesperrt_k,
            "nur_bewaehrt": NUR_BEWAEHRT,
            "b_plus_bei_bewaehrt": B_PLUS_BEI_BEWAEHRT,
            "bilanz_je_klasse": True,
            "bewaehrt_pf": BEWAEHRT_PF,
            "frei": sorted(k for k, v in je_klasse.items() if v.get("urteil") == "bewaehrt"),
        }
    arten = {
        k.removeprefix("setup:"): v.as_dict()
        for k, v in sorted(stand.items())
        if k.startswith("setup:")
    }
    # Ist eine Setup-Art insgesamt gesperrt, steht sie einmal da — nicht zusaetzlich
    # noch einmal je Anlageklasse.
    arten_gesperrt = {
        k.removeprefix("setup:")
        for k, v in stand.items()
        if k.startswith("setup:") and v.urteil == "gesperrt"
    }
    gesperrt = sorted(
        _anzeige(k)
        for k, v in stand.items()
        if v.urteil == "gesperrt"
        and not (k.startswith("klasse_setup:") and k.rpartition("|")[2] in arten_gesperrt)
    )
    return {
        "min_faelle": MIN_FAELLE,
        "min_crv": MIN_CRV,
        "min_ziel1_pct": dict(MIN_ZIEL1_PCT),
        "max_je_tag": MAX_JE_TAG,
        "sperre_je_wert_h": int(SPERRE_JE_WERT.total_seconds() // 3600),
        "setup_arten": arten,
        "gesperrt": gesperrt,
        "nur_bewaehrt": NUR_BEWAEHRT,
        "b_plus_bei_bewaehrt": B_PLUS_BEI_BEWAEHRT,
        "bewaehrt_pf": BEWAEHRT_PF,
        "frei": sorted(k for k, v in arten.items() if v.get("urteil") == "bewaehrt"),
    }


__all__ = [
    "BILANZ_JE_KLASSE",
    "B_PLUS_BEI_BEWAEHRT",
    "MAX_JE_TAG",
    "MIN_CRV",
    "MIN_FAELLE",
    "MIN_ZIEL1_PCT",
    "NUR_BEWAEHRT",
    "SPERRE_JE_WERT",
    "Punkt",
    "Stand",
    "Tor",
    "basis",
    "bilanz",
    "deckel",
    "fuers_telefon",
    "note_kurz",
    "pruefe",
    "pruefe_wache",
    "pruefe_zeile",
    "regeln_uebersicht",
    "ziel1_prozent",
]
