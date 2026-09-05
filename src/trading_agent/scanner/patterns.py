"""Mustererkennung auf dem Chart — Flaggen, Dreiecke, Doppeltops, Kompression.

Das ist die Faehigkeit, nicht die Strategie. Ozans Unterscheidung woertlich: „Wenn eine
bestimmte SMC-Strategie im OOS-Test nicht funktioniert, dann verwerfen wir diese
Strategie. Aber wir wollen deshalb NICHT die Faehigkeit aus unserem System entfernen,
SMC-Strukturen zu erkennen."

Dasselbe gilt hier. Ein erkanntes Dreieck ist **kein Handelssignal**. Es ist eine
Beschreibung dessen, was auf dem Chart zu sehen ist, und geht als Kontext in die
Bewertung ein — mehr nicht. Ob ein Muster historisch traegt, ist eine eigene Frage.

WIE ERKANNT WIRD

Ueber die bereits berechneten Swing-Punkte, nicht ueber rohe Kerzen. Ein Muster besteht
aus dem Verhaeltnis seiner Hoch- und Tiefpunkte zueinander; wer stattdessen jede Kerze
anschaut, findet in jedem Rauschen ein Dreieck. Toleranzen sind in ATR ausgedrueckt,
nicht in Prozent — sonst ist dieselbe Regel bei BTC zu eng und bei einem 3-Cent-Altcoin
zu weit.

JEDES MUSTER TRAEGT SEINE EIGENE UNSICHERHEIT

``guete`` sagt, wie sauber die Form ist (0..1). Ein Doppeltop mit 0.3 % Abstand zwischen
den Hochs ist etwas anderes als eines mit 4 %. Beide werden gemeldet, aber unterschieden.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field
from itertools import pairwise
from typing import Any

from trading_agent.core.enums import Direction

#: Wie viele Swing-Punkte hoechstens zurueckgeschaut wird. Ein Muster, das zwanzig
#: Swings zurueckliegt, beschreibt nicht mehr die aktuelle Lage.
FENSTER_SWINGS = 12

#: Toleranz fuer "gleich hoch" — in ATR der Zeitebene.
GLEICH_ATR = 0.55


@dataclass(frozen=True, slots=True)
class Muster:
    """Ein erkanntes Chartmuster."""

    name: str
    richtung: Direction | None  # wohin es zeigen WUERDE, falls es aufgeht
    guete: float  # 0..1 — wie sauber die Form ist
    beschreibung: str
    #: Preislinien, die zum Muster gehoeren (Nackenlinie, Ausbruchskante, Begrenzungen).
    linien: tuple[tuple[str, float], ...] = ()
    #: Ab wo das Muster als bestaetigt gilt (Ausbruchspunkt), falls definierbar.
    ausloeser: float | None = None
    #: Ab wo es als gescheitert gilt.
    hinfaellig: float | None = None
    zeitebene: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "richtung": self.richtung.value if self.richtung else None,
            "guete": round(self.guete, 2),
            "beschreibung": self.beschreibung,
            "linien": [{"name": n, "preis": p} for n, p in self.linien],
            "ausloeser": self.ausloeser,
            "hinfaellig": self.hinfaellig,
            "zeitebene": self.zeitebene,
        }


@dataclass(slots=True)
class _Punkte:
    """Swings, getrennt nach Hoch und Tief, jeweils (index, preis)."""

    hochs: list[tuple[int, float]] = field(default_factory=list)
    tiefs: list[tuple[int, float]] = field(default_factory=list)
    alle: list[tuple[int, float, bool]] = field(default_factory=list)  # (idx, preis, ist_hoch)


def _punkte(swings: Sequence[Any], fenster: int = FENSTER_SWINGS) -> _Punkte:
    p = _Punkte()
    for s in list(swings)[-fenster:]:
        ist_hoch = bool(getattr(s, "is_high", False))
        idx = int(getattr(s, "bar_index", 0))
        preis = float(getattr(s, "price", 0.0))
        p.alle.append((idx, preis, ist_hoch))
        (p.hochs if ist_hoch else p.tiefs).append((idx, preis))
    p.alle.sort()
    return p


def _steigung(punkte: Sequence[tuple[int, float]]) -> float:
    """Einfache Regressionssteigung (Preis je Bar). Ohne numpy — es sind selten mehr als acht."""
    if len(punkte) < 2:
        return 0.0
    xs = [float(i) for i, _ in punkte]
    ys = [p for _, p in punkte]
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    nenner = sum((x - mx) ** 2 for x in xs)
    if nenner <= 0:
        return 0.0
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / nenner


def _spanne(punkte: Sequence[tuple[int, float]]) -> float:
    if not punkte:
        return 0.0
    ys = [p for _, p in punkte]
    return max(ys) - min(ys)


def _gleich(a: float, b: float, atr: float) -> bool:
    return atr > 0 and abs(a - b) <= GLEICH_ATR * atr


def _guete_gleich(a: float, b: float, atr: float) -> float:
    """1.0 wenn identisch, 0.0 an der Toleranzgrenze."""
    if atr <= 0:
        return 0.0
    d = abs(a - b) / (GLEICH_ATR * atr)
    return max(0.0, min(1.0, 1.0 - d))


# ------------------------------------------------------------------ einzelne Muster


def _doppel(p: _Punkte, atr: float, tf: str) -> list[Muster]:
    out: list[Muster] = []
    if len(p.hochs) >= 2 and len(p.tiefs) >= 1:
        (i1, h1), (i2, h2) = p.hochs[-2], p.hochs[-1]
        zwischen = [t for i, t in p.tiefs if i1 < i < i2]
        if zwischen and _gleich(h1, h2, atr):
            nacken = min(zwischen)
            out.append(
                Muster(
                    name="Doppeltop",
                    richtung=Direction.SHORT,
                    guete=_guete_gleich(h1, h2, atr),
                    beschreibung=(
                        f"Zwei Hochs auf gleicher Hoehe ({h1:g} / {h2:g}), Nackenlinie {nacken:g}. "
                        "Faellt der Kurs darunter, ist die obere Liquiditaet abgearbeitet."
                    ),
                    linien=(("Hoch 1", h1), ("Hoch 2", h2), ("Nackenlinie", nacken)),
                    ausloeser=nacken,
                    hinfaellig=max(h1, h2),
                    zeitebene=tf,
                )
            )
    if len(p.tiefs) >= 2 and len(p.hochs) >= 1:
        (i1, t1), (i2, t2) = p.tiefs[-2], p.tiefs[-1]
        zwischen = [h for i, h in p.hochs if i1 < i < i2]
        if zwischen and _gleich(t1, t2, atr):
            nacken = max(zwischen)
            out.append(
                Muster(
                    name="Doppelboden",
                    richtung=Direction.LONG,
                    guete=_guete_gleich(t1, t2, atr),
                    beschreibung=(
                        f"Zwei Tiefs auf gleicher Hoehe ({t1:g} / {t2:g}), Nackenlinie {nacken:g}. "
                        "Ueber der Nackenlinie ist die untere Liquiditaet abgearbeitet."
                    ),
                    linien=(("Tief 1", t1), ("Tief 2", t2), ("Nackenlinie", nacken)),
                    ausloeser=nacken,
                    hinfaellig=min(t1, t2),
                    zeitebene=tf,
                )
            )
    return out


def _kopf_schulter(p: _Punkte, atr: float, tf: str) -> list[Muster]:
    out: list[Muster] = []
    if len(p.hochs) >= 3 and len(p.tiefs) >= 2:
        (_, ls), (_, k), (_, r) = p.hochs[-3:]
        if k > ls and k > r and _gleich(ls, r, atr) and (k - max(ls, r)) > 0.6 * atr:
            nacken = statistics.fmean([t for _, t in p.tiefs[-2:]])
            out.append(
                Muster(
                    name="Kopf-Schulter",
                    richtung=Direction.SHORT,
                    guete=_guete_gleich(ls, r, atr),
                    beschreibung=(
                        f"Mittleres Hoch {k:g} ueberragt beide Schultern ({ls:g} / {r:g}); "
                        f"Nackenlinie bei {nacken:g}."
                    ),
                    linien=(
                        ("Linke Schulter", ls),
                        ("Kopf", k),
                        ("Rechte Schulter", r),
                        ("Nackenlinie", nacken),
                    ),
                    ausloeser=nacken,
                    hinfaellig=k,
                    zeitebene=tf,
                )
            )
    if len(p.tiefs) >= 3 and len(p.hochs) >= 2:
        (_, ls), (_, k), (_, r) = p.tiefs[-3:]
        if k < ls and k < r and _gleich(ls, r, atr) and (min(ls, r) - k) > 0.6 * atr:
            nacken = statistics.fmean([h for _, h in p.hochs[-2:]])
            out.append(
                Muster(
                    name="Inverse Kopf-Schulter",
                    richtung=Direction.LONG,
                    guete=_guete_gleich(ls, r, atr),
                    beschreibung=(
                        f"Mittleres Tief {k:g} liegt unter beiden Schultern ({ls:g} / {r:g}); "
                        f"Nackenlinie bei {nacken:g}."
                    ),
                    linien=(
                        ("Linke Schulter", ls),
                        ("Kopf", k),
                        ("Rechte Schulter", r),
                        ("Nackenlinie", nacken),
                    ),
                    ausloeser=nacken,
                    hinfaellig=k,
                    zeitebene=tf,
                )
            )
    return out


def _dreieck_keil_range(p: _Punkte, atr: float, tf: str) -> list[Muster]:
    if len(p.hochs) < 3 or len(p.tiefs) < 3 or atr <= 0:
        return []
    sh, st = _steigung(p.hochs[-4:]), _steigung(p.tiefs[-4:])
    spanne_h, spanne_t = _spanne(p.hochs[-3:]), _spanne(p.tiefs[-3:])
    flach = 0.35 * atr
    obergrenze = max(h for _, h in p.hochs[-3:])
    untergrenze = min(t for _, t in p.tiefs[-3:])
    hoehe = obergrenze - untergrenze
    if hoehe <= 0:
        return []
    guete = max(0.0, min(1.0, 1.0 - (spanne_h + spanne_t) / (2.0 * max(hoehe, atr))))

    fallend_h, steigend_h = sh < -0.02 * atr, sh > 0.02 * atr
    fallend_t, steigend_t = st < -0.02 * atr, st > 0.02 * atr
    flach_h, flach_t = spanne_h <= flach, spanne_t <= flach

    def m(name: str, richtung: Direction | None, text: str) -> Muster:
        return Muster(
            name=name,
            richtung=richtung,
            guete=guete,
            beschreibung=text,
            linien=(("Obergrenze", obergrenze), ("Untergrenze", untergrenze)),
            ausloeser=obergrenze if richtung is Direction.LONG else untergrenze,
            hinfaellig=untergrenze if richtung is Direction.LONG else obergrenze,
            zeitebene=tf,
        )

    if flach_h and steigend_t:
        return [
            m(
                "Aufsteigendes Dreieck",
                Direction.LONG,
                f"Hochs bei {obergrenze:g} gedeckelt, Tiefs steigen — Druck von unten.",
            )
        ]
    if flach_t and fallend_h:
        return [
            m(
                "Absteigendes Dreieck",
                Direction.SHORT,
                f"Tiefs bei {untergrenze:g} gehalten, Hochs fallen — Druck von oben.",
            )
        ]
    if fallend_h and steigend_t:
        return [
            m(
                "Symmetrisches Dreieck",
                None,
                "Hochs fallen, Tiefs steigen — die Spanne zieht sich zusammen. "
                "Richtung entscheidet der Ausbruch, nicht die Form.",
            )
        ]
    if fallend_h and fallend_t and abs(sh) > abs(st):
        return [
            m(
                "Fallender Keil",
                Direction.LONG,
                "Beide Kanten fallen, die obere schneller — typischerweise eine Erschoepfung nach unten.",
            )
        ]
    if steigend_h and steigend_t and abs(st) > abs(sh):
        return [
            m(
                "Steigender Keil",
                Direction.SHORT,
                "Beide Kanten steigen, die untere schneller — typischerweise eine Erschoepfung nach oben.",
            )
        ]
    if flach_h and flach_t:
        return [
            m(
                "Seitwaertsspanne",
                None,
                f"Kurs pendelt zwischen {untergrenze:g} und {obergrenze:g}. "
                "Innerhalb der Spanne gibt es keinen Trend zu handeln.",
            )
        ]
    return []


def _flagge(p: _Punkte, atr: float, tf: str, bars: Sequence[Any]) -> list[Muster]:
    """Impuls, dann enge Gegenbewegung. Der Impuls ist das Signal, die Flagge die Pause."""
    if len(p.alle) < 4 or atr <= 0 or len(bars) < 20:
        return []
    letzte = p.alle[-4:]
    beine = [(b[1] - a[1], a[0], b[0]) for a, b in pairwise(letzte)]
    if not beine:
        return []
    impuls = max(beine, key=lambda x: abs(x[0]))
    if abs(impuls[0]) < 2.5 * atr:
        return []
    danach = [b for b in beine if b[1] >= impuls[2]]
    if not danach:
        return []
    rueck = sum(abs(b[0]) for b in danach)
    if rueck > 0.55 * abs(impuls[0]):
        return []
    hoch = max(h for _, h in p.hochs[-2:]) if p.hochs else 0.0
    tief = min(t for _, t in p.tiefs[-2:]) if p.tiefs else 0.0
    lang = impuls[0] > 0
    guete = max(0.0, min(1.0, 1.0 - rueck / max(1e-9, 0.55 * abs(impuls[0]))))
    return [
        Muster(
            name="Bullenflagge" if lang else "Baerenflagge",
            richtung=Direction.LONG if lang else Direction.SHORT,
            guete=guete,
            beschreibung=(
                f"Impuls ueber {abs(impuls[0]) / atr:.1f} ATR, danach nur "
                f"{rueck / max(1e-9, abs(impuls[0])) * 100:.0f} % Rueckgabe — "
                "die Bewegung wird gehalten, nicht abverkauft."
            ),
            linien=(("Flaggenhoch", hoch), ("Flaggentief", tief)),
            ausloeser=hoch if lang else tief,
            hinfaellig=tief if lang else hoch,
            zeitebene=tf,
        )
    ]


def _kompression(bars: Sequence[Any], atr: float, tf: str, n: int = 20) -> list[Muster]:
    """Zieht sich die Schwankung zusammen oder weitet sie sich? Beides ist eine Aussage."""
    if len(bars) < 2 * n or atr <= 0:
        return []

    def spanne(teil: Sequence[Any]) -> float:
        return statistics.fmean([float(b.high) - float(b.low) for b in teil])

    jetzt, davor = spanne(bars[-n:]), spanne(bars[-2 * n : -n])
    if davor <= 0:
        return []
    q = jetzt / davor
    hoch = max(float(b.high) for b in bars[-n:])
    tief = min(float(b.low) for b in bars[-n:])
    if q <= 0.65:
        return [
            Muster(
                name="Kompression",
                richtung=None,
                guete=max(0.0, min(1.0, (0.65 - q) / 0.4)),
                beschreibung=(
                    f"Die Schwankung der letzten {n} Kerzen betraegt nur {q * 100:.0f} % der "
                    f"vorherigen. Enge Phasen loesen sich, aber die Form sagt nicht wohin."
                ),
                linien=(("Obergrenze", hoch), ("Untergrenze", tief)),
                ausloeser=hoch,
                hinfaellig=tief,
                zeitebene=tf,
            )
        ]
    if q >= 1.6:
        return [
            Muster(
                name="Expansion",
                richtung=None,
                guete=max(0.0, min(1.0, (q - 1.6) / 1.4)),
                beschreibung=(
                    f"Die Schwankung hat sich auf {q * 100:.0f} % ausgeweitet. Stops brauchen "
                    "hier mehr Abstand, sonst wird man vom Rauschen ausgestoppt."
                ),
                zeitebene=tf,
            )
        ]
    return []


def _ausbruch(bars: Sequence[Any], p: _Punkte, atr: float, tf: str) -> list[Muster]:
    """Ausbruch oder Fehlausbruch aus der juengsten Spanne."""
    if len(bars) < 25 or not p.hochs or not p.tiefs or atr <= 0:
        return []
    basis = bars[-25:-3]
    if not basis:
        return []
    obergrenze = max(float(b.high) for b in basis)
    untergrenze = min(float(b.low) for b in basis)
    letzte = bars[-3:]
    schluss = float(letzte[-1].close)
    hoch3 = max(float(b.high) for b in letzte)
    tief3 = min(float(b.low) for b in letzte)

    if schluss > obergrenze + 0.15 * atr:
        return [
            Muster(
                "Ausbruch nach oben",
                Direction.LONG,
                min(1.0, (schluss - obergrenze) / atr),
                f"Schluss {schluss:g} ueber der Kante {obergrenze:g} der letzten Spanne.",
                (("Ausbruchskante", obergrenze),),
                obergrenze,
                untergrenze,
                tf,
            )
        ]
    if schluss < untergrenze - 0.15 * atr:
        return [
            Muster(
                "Ausbruch nach unten",
                Direction.SHORT,
                min(1.0, (untergrenze - schluss) / atr),
                f"Schluss {schluss:g} unter der Kante {untergrenze:g} der letzten Spanne.",
                (("Ausbruchskante", untergrenze),),
                untergrenze,
                obergrenze,
                tf,
            )
        ]
    if hoch3 > obergrenze + 0.2 * atr and schluss < obergrenze:
        return [
            Muster(
                "Fehlausbruch oben",
                Direction.SHORT,
                min(1.0, (hoch3 - obergrenze) / atr),
                f"Ueber {obergrenze:g} hinaus gelaufen und wieder darunter geschlossen — "
                "die Kaeufer darueber sind eingefangen.",
                (("Ausbruchskante", obergrenze),),
                untergrenze,
                hoch3,
                tf,
            )
        ]
    if tief3 < untergrenze - 0.2 * atr and schluss > untergrenze:
        return [
            Muster(
                "Fehlausbruch unten",
                Direction.LONG,
                min(1.0, (untergrenze - tief3) / atr),
                f"Unter {untergrenze:g} gefallen und wieder darueber geschlossen — "
                "die Verkaeufer darunter sind eingefangen.",
                (("Ausbruchskante", untergrenze),),
                obergrenze,
                tief3,
                tf,
            )
        ]
    return []


# ------------------------------------------------------------------ Einstieg


def erkenne_muster(tfc: Any, *, zeitebene: str = "") -> list[Muster]:
    """Alle Muster einer Zeitebene. Reihenfolge: die aussagekraeftigsten zuerst."""
    bars = list(getattr(tfc, "bars", ()) or ())
    swings = list(getattr(tfc, "swings", ()) or ())
    atr = float(getattr(tfc, "atr", 0.0) or 0.0)
    tf = zeitebene or getattr(getattr(tfc, "timeframe", None), "value", "")
    if not bars or atr <= 0:
        return []
    p = _punkte(swings)
    gefunden: list[Muster] = []
    gefunden += _ausbruch(bars, p, atr, tf)
    gefunden += _kopf_schulter(p, atr, tf)
    gefunden += _doppel(p, atr, tf)
    gefunden += _flagge(p, atr, tf, bars)
    gefunden += _dreieck_keil_range(p, atr, tf)
    gefunden += _kompression(bars, atr, tf)
    gefunden.sort(key=lambda m: -m.guete)
    return gefunden


def muster_ueber_zeitebenen(per_tf: dict[Any, Any], zeitebenen: Sequence[Any]) -> list[Muster]:
    out: list[Muster] = []
    for tf in zeitebenen:
        tfc = per_tf.get(tf)
        if tfc is None:
            continue
        out += erkenne_muster(tfc, zeitebene=getattr(tf, "value", str(tf)))
    return out


__all__ = [
    "FENSTER_SWINGS",
    "GLEICH_ATR",
    "Muster",
    "erkenne_muster",
    "muster_ueber_zeitebenen",
]
