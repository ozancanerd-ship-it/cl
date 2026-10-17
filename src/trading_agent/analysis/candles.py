"""analysis/candles — Kerzenlesen auf abgeschlossenen Kerzen.

Warum es das gibt: der Chart-Score kannte Struktur, Zonen und Momentum, aber nicht die
einzelne Kerze. Ein Trader schaut aber zuerst darauf, WIE der Kurs an einer Marke
reagiert hat: Docht und Ablehnung, Koerper und Wucht, Verschlucken der Vorkerze.

Regeln, damit daraus keine Zahlenspielerei wird:

* Nur abgeschlossene Kerzen (``OHLCV`` ist per Definition abgeschlossen).
* Ein Muster zaehlt nur, wenn es GROSS genug ist (ATR-Bezug) — sonst ist es Rauschen.
* Ein Muster ohne Ort ist schwach: dasselbe Pin-Bar mitten im Nichts bekommt
  einen Bruchteil der Staerke eines Pin-Bars direkt am Swing-Tief / unter einer Marke.
* Es wird nichts erfunden: fehlt ATR oder sind zu wenige Kerzen da, kommt ``[]``.
* Beobachtung (Muster) und Deutung (``richtung``) sind getrennt: Doji und Inside Bar
  haben keine Richtung — sie sind Unentschlossenheit, kein Signal.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class Kerzenmuster:
    art: str  # impuls | verschlingen | pin | sweep | inside | doji
    richtung: str | None  # "long" | "short" | None (neutral)
    staerke: float  # 0..1, inkl. Ortsbonus
    index: int  # Position in der uebergebenen Reihe
    kerzen_zurueck: int  # 0 = letzte abgeschlossene Kerze
    ort: str  # "am_tief" | "am_hoch" | "frei"
    text: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "art": self.art,
            "richtung": self.richtung,
            "staerke": round(self.staerke, 2),
            "index": self.index,
            "zurueck": self.kerzen_zurueck,
            "ort": self.ort,
            "text": self.text,
        }


def _f(b: Any, name: str) -> float:
    return float(getattr(b, name))


def _ort(b: Any, atr: float, tiefs: Sequence[float], hochs: Sequence[float]) -> str:
    """Liegt die Kerze an einer bekannten Marke (Swing/Liquiditaet)?"""
    tol = 0.5 * atr
    lo, hi = _f(b, "low"), _f(b, "high")
    if any(abs(lo - p) <= tol or (lo < p <= _f(b, "close")) for p in tiefs):
        return "am_tief"
    if any(abs(hi - p) <= tol or (hi > p >= _f(b, "close")) for p in hochs):
        return "am_hoch"
    return "frei"


def erkenne_kerzen(
    bars: Sequence[Any],
    atr: float,
    *,
    tiefs: Sequence[float] = (),
    hochs: Sequence[float] = (),
    fenster: int = 4,
) -> list[Kerzenmuster]:
    """Muster der letzten ``fenster`` Kerzen. ``tiefs``/``hochs`` = bekannte Marken."""
    n = len(bars)
    if n < 3 or not atr or atr <= 0:
        return []
    out: list[Kerzenmuster] = []
    for i in range(max(1, n - fenster), n):
        b, p = bars[i], bars[i - 1]
        o, h, lo, c = _f(b, "open"), _f(b, "high"), _f(b, "low"), _f(b, "close")
        rng = h - lo
        if rng <= 0:
            continue
        body = abs(c - o)
        ober = h - max(o, c)
        unter = min(o, c) - lo
        zur = n - 1 - i
        ort = _ort(b, atr, tiefs, hochs)
        bonus = 0.0 if ort == "frei" else 0.25

        def add(
            art: str, d: str | None, st: float, text: str, *, i=i, zur=zur, ort=ort, bonus=bonus
        ) -> None:
            out.append(Kerzenmuster(art, d, max(0.0, min(1.0, st + bonus)), i, zur, ort, text))

        # Impulskerze: grosser Koerper, Schluss nahe dem Extrem.
        if rng >= 1.3 * atr and body / rng >= 0.6:
            d = "long" if c > o else "short"
            add(
                "impuls",
                d,
                min(0.75, 0.4 + 0.15 * (rng / atr - 1.3)),
                f"Impulskerze {rng / atr:.1f} ATR, Koerper {body / rng:.0%} der Spanne",
            )

        # Verschlingen: Koerper umschliesst den Vorkoerper, Gegenfarbe, spuerbar gross.
        po, pc = _f(p, "open"), _f(p, "close")
        pbody = abs(pc - po)
        if (
            pbody > 0.1 * atr
            and body >= 0.8 * atr
            and body > pbody
            and (c > o) != (pc > po)
            and max(o, c) >= max(po, pc)
            and min(o, c) <= min(po, pc)
        ):
            d = "long" if c > o else "short"
            add(
                "verschlingen",
                d,
                min(0.7, 0.35 + 0.2 * (body / pbody - 1)),
                f"Verschlingende Kerze ({'bullisch' if d == 'long' else 'baerisch'}), "
                f"{body / pbody:.1f}x der Vorkerze",
            )

        # Pin / Ablehnung: langer Docht, kleiner Koerper, Schluss weg vom Docht.
        if rng >= 0.8 * atr and body <= 0.35 * rng:
            if unter >= 0.55 * rng and unter >= 2 * max(body, 1e-12) and (c - lo) / rng >= 0.6:
                add(
                    "pin",
                    "long",
                    0.35 + 0.3 * (unter / rng - 0.55) / 0.45,
                    f"Ablehnung nach unten: Docht {unter / rng:.0%} der Spanne",
                )
            elif ober >= 0.55 * rng and ober >= 2 * max(body, 1e-12) and (h - c) / rng >= 0.6:
                add(
                    "pin",
                    "short",
                    0.35 + 0.3 * (ober / rng - 0.55) / 0.45,
                    f"Ablehnung nach oben: Docht {ober / rng:.0%} der Spanne",
                )

        # Sweep mit Rueckschluss: Marke per Docht genommen, Schluss wieder davor.
        for t in tiefs:
            if lo < t <= c and rng >= 0.6 * atr:
                add(
                    "sweep",
                    "long",
                    0.5,
                    f"Tief bei {t:g} per Docht genommen, Schluss zurueck darueber",
                )
                break
        for t in hochs:
            if h > t >= c and rng >= 0.6 * atr:
                add(
                    "sweep",
                    "short",
                    0.5,
                    f"Hoch bei {t:g} per Docht genommen, Schluss zurueck darunter",
                )
                break

        # Unentschlossenheit — beobachtet, aber ohne Richtung.
        if h < _f(p, "high") and lo > _f(p, "low"):
            add("inside", None, 0.15, "Inside Bar — Kompression, noch keine Entscheidung")
        elif body <= 0.08 * rng and rng >= 0.7 * atr:
            add("doji", None, 0.1, "Doji — Unentschlossenheit")
    return out


def kerzen_urteil(
    muster: Sequence[Kerzenmuster], richtung: str | None
) -> tuple[float, str, list[str]]:
    """(Anteil 0..1, Detailtext, Warnungen) fuer eine Handelsrichtung.

    Dafuer zaehlt das beste Muster in Richtung; ein gleich starkes Gegenmuster zieht
    ab und wird als Warnung gezeigt. Aeltere Kerzen zaehlen weniger.
    """
    if richtung not in ("long", "short"):
        return 0.0, "keine Richtung", []
    gegen_r = "short" if richtung == "long" else "long"

    def wert(m: Kerzenmuster) -> float:
        return m.staerke * (1.0 - 0.2 * m.kerzen_zurueck)

    dafuer = [m for m in muster if m.richtung == richtung]
    dagegen = [m for m in muster if m.richtung == gegen_r]
    best = max(dafuer, key=wert, default=None)
    geg = max(dagegen, key=wert, default=None)
    warn: list[str] = []
    if geg is not None and wert(geg) >= 0.35:
        warn.append(f"Kerze gegen die Idee: {geg.text}")
    if best is None:
        return 0.0, "kein Kerzenmuster in Richtung", warn
    anteil = wert(best) - (0.5 * wert(geg) if geg is not None else 0.0)
    anteil = max(0.0, min(1.0, anteil))
    ort = {"am_tief": " am Tief", "am_hoch": " am Hoch", "frei": " ohne Marke"}[best.ort]
    return anteil, f"{best.text}{ort} (vor {best.kerzen_zurueck} Kerzen)", warn
