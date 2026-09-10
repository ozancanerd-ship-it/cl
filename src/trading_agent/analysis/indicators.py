"""Klassische Indikatoren — RSI, Volumen, gleitende Durchschnitte.

Warum ueberhaupt, wenn es schon Struktur, Liquiditaet und Zonen gibt: das sind
Beschreibungen der FORM. Ein Trader liest zusaetzlich den ZUSTAND — ist die Bewegung
ueberdehnt, kommt Volumen dazu, laeuft der Kurs ueber oder unter seinen Durchschnitten.
Ozan hat es beim Durchsehen der App sofort vermisst: „der RSI fehlt bei allen Analysen".

Alles hier ist eine reine Funktion auf Kerzen. Kein Netz, kein Zustand, damit es
pruefbar bleibt. Und alles gibt ``None`` zurueck, wenn die Historie nicht reicht —
ein RSI aus fuenf Kerzen waere eine Zahl ohne Bedeutung.

**Kein Signal.** Ein RSI von 78 heisst nicht „verkaufen". In einem starken Trend bleibt
er wochenlang oben, und wer dort short geht, steht gegen die Bewegung. Er geht hier als
Kontext ein und erzeugt hoechstens eine Warnung.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

RSI_PERIODE = 14
EMA_KURZ = 50
EMA_LANG = 200
VOLUMEN_FENSTER = 20


def rsi(schluss: Sequence[float], periode: int = RSI_PERIODE) -> float | None:
    """Relative Strength Index nach Wilder (geglaettet, nicht der einfache Mittelwert).

    Der Unterschied ist nicht kosmetisch: die einfache Variante springt, sobald eine
    grosse Kerze aus dem Fenster faellt. Wilders Glaettung tut das nicht, und sie ist
    das, was jede Charting-Software zeigt — sonst stimmt unsere Zahl nicht mit dem
    ueberein, was Ozan auf dem Chart sieht.
    """
    if len(schluss) < periode + 1:
        return None
    gewinne = verluste = 0.0
    for i in range(1, periode + 1):
        diff = schluss[i] - schluss[i - 1]
        gewinne += max(diff, 0.0)
        verluste += max(-diff, 0.0)
    avg_g, avg_v = gewinne / periode, verluste / periode
    for i in range(periode + 1, len(schluss)):
        diff = schluss[i] - schluss[i - 1]
        avg_g = (avg_g * (periode - 1) + max(diff, 0.0)) / periode
        avg_v = (avg_v * (periode - 1) + max(-diff, 0.0)) / periode
    if avg_v <= 0:
        return 100.0 if avg_g > 0 else 50.0
    rs = avg_g / avg_v
    return 100.0 - 100.0 / (1.0 + rs)


def ema(werte: Sequence[float], periode: int) -> float | None:
    if len(werte) < periode:
        return None
    k = 2.0 / (periode + 1.0)
    schnitt = sum(werte[:periode]) / periode
    for v in werte[periode:]:
        schnitt = v * k + schnitt * (1.0 - k)
    return schnitt


def volumen_verhaeltnis(volumen: Sequence[float], fenster: int = VOLUMEN_FENSTER) -> float | None:
    """Letztes Volumen gegen den Durchschnitt davor. 1,0 = normal, 2,0 = doppelt."""
    if len(volumen) < fenster + 1:
        return None
    schnitt = sum(volumen[-fenster - 1 : -1]) / fenster
    if schnitt <= 0:
        return None
    return volumen[-1] / schnitt


def rsi_divergenz(
    schluss: Sequence[float], periode: int = RSI_PERIODE, fenster: int = 30
) -> str | None:
    """Laeuft der Kurs auf ein neues Extrem, der RSI aber nicht?

    Das ist die einzige RSI-Aussage, die mehr ist als „hoch" oder „tief": der Kurs macht
    ein neues Hoch, die Kraft dahinter nicht mit. Gemeldet wird nur der klare Fall.
    """
    if len(schluss) < periode + fenster + 2:
        return None
    reihe = [rsi(schluss[: i + 1], periode) for i in range(len(schluss) - fenster, len(schluss))]
    if any(r is None for r in reihe):
        return None
    werte: list[float] = [r for r in reihe if r is not None]
    kurse = list(schluss[-fenster:])
    mitte = fenster // 2
    hoch_neu, hoch_alt = max(kurse[mitte:]), max(kurse[:mitte])
    tief_neu, tief_alt = min(kurse[mitte:]), min(kurse[:mitte])
    r_hoch_neu, r_hoch_alt = max(werte[mitte:]), max(werte[:mitte])
    r_tief_neu, r_tief_alt = min(werte[mitte:]), min(werte[:mitte])
    if hoch_neu > hoch_alt and r_hoch_neu < r_hoch_alt - 3.0:
        return "baerisch"
    if tief_neu < tief_alt and r_tief_neu > r_tief_alt + 3.0:
        return "bullisch"
    return None


@dataclass(frozen=True, slots=True)
class Indikatoren:
    """Der Zustand einer Zeitebene in Zahlen."""

    rsi: float | None = None
    rsi_divergenz: str | None = None
    volumen_x: float | None = None
    ema_kurz: float | None = None
    ema_lang: float | None = None
    ueber_ema_kurz: bool | None = None
    ueber_ema_lang: bool | None = None
    abstand_ema_kurz_pct: float | None = None

    def as_dict(self) -> dict[str, Any]:
        def r(v: float | None, n: int = 2) -> float | None:
            return round(v, n) if v is not None else None

        return {
            "rsi": r(self.rsi, 1),
            "rsi_divergenz": self.rsi_divergenz,
            "volumen_x": r(self.volumen_x),
            "ema_kurz": r(self.ema_kurz, 6),
            "ema_lang": r(self.ema_lang, 6),
            "ueber_ema_kurz": self.ueber_ema_kurz,
            "ueber_ema_lang": self.ueber_ema_lang,
            "abstand_ema_kurz_pct": r(self.abstand_ema_kurz_pct),
            "satz": self.satz(),
        }

    def satz(self) -> str:
        """Was die Zahlen bedeuten — in einem Satz, ohne Handlungsempfehlung."""
        teile: list[str] = []
        if self.rsi is not None:
            lage = (
                "ueberkauft"
                if self.rsi >= 70
                else "ueberverkauft"
                if self.rsi <= 30
                else "neutral"
                if 45 <= self.rsi <= 55
                else "stark"
                if self.rsi > 55
                else "schwach"
            )
            teile.append(f"RSI {self.rsi:.0f} ({lage})")
        if self.rsi_divergenz:
            richtung = "nach unten" if self.rsi_divergenz == "baerisch" else "nach oben"
            teile.append(f"Divergenz {richtung} — der Kurs geht weiter als die Kraft dahinter")
        if self.volumen_x is not None:
            if self.volumen_x >= 1.8:
                teile.append(f"Volumen {self.volumen_x:.1f}× normal")
            elif self.volumen_x <= 0.5:
                teile.append(f"Volumen nur {self.volumen_x:.1f}× normal — duenn")
        if self.ueber_ema_kurz is not None and self.abstand_ema_kurz_pct is not None:
            wo = "ueber" if self.ueber_ema_kurz else "unter"
            teile.append(f"{abs(self.abstand_ema_kurz_pct):.1f} % {wo} dem EMA{EMA_KURZ}")
        return ", ".join(teile) if teile else "keine Indikatoren berechenbar"


def berechne(bars: Sequence[Any]) -> Indikatoren:
    """Alle Indikatoren aus einer Kerzenreihe. Fehlt Historie, bleiben Felder ``None``."""
    if not bars:
        return Indikatoren()
    schluss = [float(b.close) for b in bars]
    volumen = [float(getattr(b, "volume", 0.0) or 0.0) for b in bars]
    kurs = schluss[-1]
    ek, el = ema(schluss, EMA_KURZ), ema(schluss, EMA_LANG)
    return Indikatoren(
        rsi=rsi(schluss),
        rsi_divergenz=rsi_divergenz(schluss),
        volumen_x=volumen_verhaeltnis(volumen) if any(volumen) else None,
        ema_kurz=ek,
        ema_lang=el,
        ueber_ema_kurz=(kurs > ek) if ek else None,
        ueber_ema_lang=(kurs > el) if el else None,
        abstand_ema_kurz_pct=((kurs / ek - 1.0) * 100.0) if ek else None,
    )


def warnungen(ind: Indikatoren, richtung: str | None) -> list[str]:
    """Wo die Indikatoren gegen die Wette sprechen. Warnung, nie Verbot."""
    w: list[str] = []
    if ind.rsi is not None:
        if richtung == "long" and ind.rsi >= 78:
            w.append(
                f"RSI {ind.rsi:.0f} — die Bewegung ist ueberdehnt; ein Ruecksetzer kostet "
                "hier mehr als das Warten darauf"
            )
        elif richtung == "short" and ind.rsi <= 22:
            w.append(
                f"RSI {ind.rsi:.0f} — nach unten ueberdehnt, Gegenbewegungen sind wahrscheinlich"
            )
    if ind.rsi_divergenz == "baerisch" and richtung == "long":
        w.append("RSI-Divergenz nach unten — neue Hochs ohne neue Kraft")
    if ind.rsi_divergenz == "bullisch" and richtung == "short":
        w.append("RSI-Divergenz nach oben — neue Tiefs ohne neuen Druck")
    if ind.volumen_x is not None and ind.volumen_x <= 0.45:
        w.append(f"Volumen nur {ind.volumen_x:.1f}× normal — die Bewegung traegt niemand mit")
    if ind.ueber_ema_lang is not None:
        if richtung == "long" and not ind.ueber_ema_lang:
            w.append(f"Kurs unter dem EMA{EMA_LANG} — Long gegen den uebergeordneten Durchschnitt")
        elif richtung == "short" and ind.ueber_ema_lang:
            w.append(f"Kurs ueber dem EMA{EMA_LANG} — Short gegen den uebergeordneten Durchschnitt")
    return w


__all__ = [
    "EMA_KURZ",
    "EMA_LANG",
    "RSI_PERIODE",
    "VOLUMEN_FENSTER",
    "Indikatoren",
    "berechne",
    "ema",
    "rsi",
    "rsi_divergenz",
    "volumen_verhaeltnis",
    "warnungen",
]
