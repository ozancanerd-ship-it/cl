"""Relative Staerke — wer laeuft besser als der Rest seiner Klasse?

WARUM ES DAS BRAUCHT

Bis hierher wird jedes Instrument fuer sich betrachtet. Ein Trader tut das nie. Er fragt
zuerst: laeuft dieser Wert besser oder schlechter als der Markt, in dem er sich bewegt?
Diese Frage ist in der Praxis eine der wenigen mit belastbarem empirischem Rueckhalt —
Staerke bleibt ueber Wochen bis Monate eher bestehen als sie dreht.

Praktisch heisst das:

* **Long in einem Nachzuegler ist ein schlechter Trade**, auch wenn der Chart huebsch
  aussieht. Wenn der ganze Sektor laeuft und dieser eine Wert nicht, gibt es einen Grund.
* **Short in einem Marktfuehrer ist ein schlechter Trade**, aus demselben Grund
  spiegelverkehrt.
* Bei sonst gleichem Bild gewinnt der staerkere Wert den Platz im Ranking.

WIE GERECHNET WIRD

Nicht gegen einen externen Index — das braeuchte zusaetzliche Daten und waere fuer
Krypto ohnehin fragwuerdig. Stattdessen gegen die **eigene Klasse**: jeder Kryptowert
gegen alle anderen gescannten Kryptowerte, jede Aktie gegen alle anderen Aktien. Das
ist genau das, was ein „RS Rating" ausdrueckt: der Perzentilrang der Rendite.

Zwei Zeitfenster, wie in der Praxis ueblich: rund einen Monat und rund ein Quartal.
Das kurze Fenster zaehlt doppelt — fuer einen Swing-Trade ueber Tage bis Wochen ist
die juengere Staerke die relevantere.

**Kein Signal.** Ein RS von 95 ist kein Kaufgrund. Es ist ein Filter: es sagt, dass ein
Long hier nicht gegen die Herde laeuft.
"""

from __future__ import annotations

from typing import Any

#: Fenster in Tageskerzen und ihr Gewicht.
FENSTER: tuple[tuple[int, float], ...] = ((21, 0.65), (63, 0.35))
#: Ab hier gilt ein Wert als Fuehrer seiner Klasse.
FUEHRER_AB = 80.0
#: Darunter gilt er als Nachzuegler.
NACHZUEGLER_BIS = 30.0
#: Unter so vielen Werten in einer Klasse ist ein Perzentil bedeutungslos.
MIN_KLASSENGROESSE = 8


def renditen(bars: list[Any]) -> dict[str, float]:
    """Rohrenditen ueber die Fenster, in Prozent. Aus Tageskerzen.

    Rueckgabe leer, wenn die Historie nicht reicht — lieber keine Zahl als eine, die
    aus drei Kerzen stammt.
    """
    if len(bars) < 25:
        return {}
    schluss = [float(b.close) for b in bars]
    aus: dict[str, float] = {}
    for n, _ in FENSTER:
        if len(schluss) <= n:
            continue
        alt = schluss[-(n + 1)]
        if alt <= 0:
            continue
        aus[f"r{n}"] = (schluss[-1] / alt - 1.0) * 100.0
    return aus


def _perzentil(wert: float, alle: list[float]) -> float:
    """Anteil der Werte, die kleiner sind — in Prozent. 100 = bester."""
    if not alle:
        return 50.0
    kleiner = sum(1 for x in alle if x < wert)
    gleich = sum(1 for x in alle if x == wert)
    return (kleiner + 0.5 * gleich) / len(alle) * 100.0


def bewerte_klasse(zeilen: list[dict[str, Any]]) -> None:
    """Setzt ``rs`` (0..100) auf jeder Zeile einer Klasse. Aendert die Zeilen in place.

    Zeilen ohne ausreichende Historie bekommen ``rs = None`` — und werden dadurch weder
    belohnt noch bestraft.
    """
    if len(zeilen) < MIN_KLASSENGROESSE:
        for z in zeilen:
            z["rs"] = None
        return

    gesammelt: dict[int, list[float]] = {n: [] for n, _ in FENSTER}
    for z in zeilen:
        roh = (z.get("zusatz") or {}).get("renditen") or {}
        for n, _ in FENSTER:
            v = roh.get(f"r{n}")
            if v is not None:
                gesammelt[n].append(float(v))

    for z in zeilen:
        roh = (z.get("zusatz") or {}).get("renditen") or {}
        teile: list[tuple[float, float]] = []
        for n, gew in FENSTER:
            v = roh.get(f"r{n}")
            if v is None or len(gesammelt[n]) < MIN_KLASSENGROESSE:
                continue
            teile.append((_perzentil(float(v), gesammelt[n]), gew))
        if not teile:
            z["rs"] = None
            continue
        summe_gew = sum(g for _, g in teile)
        z["rs"] = round(sum(p * g for p, g in teile) / summe_gew, 1)


def urteil_anpassen(zeile: dict[str, Any]) -> None:
    """Wendet die relative Staerke auf Note, Warnungen und Begruendung an.

    Der Eingriff ist bewusst ein **Deckel**, keine Punktevergabe: relative Schwaeche
    verhindert die Spitzennoten, hebt aber nie eine Note an. Staerke wird nur genannt,
    nicht belohnt — sonst waere es am Ende doch wieder eine Rangliste der Gewinner der
    letzten Wochen.
    """
    rs = zeile.get("rs")
    if rs is None:
        return
    richtung = zeile.get("richtung")
    if richtung not in ("long", "short"):
        return
    lang = richtung == "long"
    warnungen = list(zeile.get("warnungen") or [])

    schwach = (lang and rs < NACHZUEGLER_BIS) or (not lang and rs > 100.0 - NACHZUEGLER_BIS)
    stark = (lang and rs >= FUEHRER_AB) or (not lang and rs <= 100.0 - FUEHRER_AB)

    if schwach:
        satz = (
            f"relative Staerke {rs:.0f} von 100 — dieser Wert laeuft schlechter als "
            f"{100 - rs:.0f} % seiner Klasse. Ein Long in einem Nachzuegler kaempft "
            "gegen die Herde."
            if lang
            else (
                f"relative Staerke {rs:.0f} von 100 — dieser Wert laeuft besser als "
                "der Rest seiner Klasse. Ein Short im Marktfuehrer ist der schwerere Weg."
            )
        )
        if satz not in warnungen:
            warnungen.insert(0, satz)
        zeile["warnungen"] = warnungen
        # Deckel: hoechstens A_MINUS.
        rang = ("A_PLUS", "A")
        if zeile.get("urteil") in rang:
            zeile["urteil"] = "A_MINUS"
            zeile["note"] = "A−"
            zeile["deckel"] = "relative Schwaeche gegenueber der eigenen Klasse"
    elif stark:
        zusatz = (
            f"gehoert zu den staerksten {100 - rs:.0f} % seiner Klasse"
            if lang
            else f"gehoert zu den schwaechsten {rs:.0f} % seiner Klasse"
        )
        b = zeile.get("begruendung") or ""
        if zusatz not in b:
            zeile["begruendung"] = (b + " · " if b else "") + zusatz


def anwenden(nach_klasse: dict[str, list[dict[str, Any]]]) -> None:
    """Komplettdurchlauf: je Klasse bewerten, dann die Urteile anpassen."""
    for zeilen in nach_klasse.values():
        bewerte_klasse(zeilen)
        for z in zeilen:
            urteil_anpassen(z)


__all__ = [
    "FENSTER",
    "FUEHRER_AB",
    "MIN_KLASSENGROESSE",
    "NACHZUEGLER_BIS",
    "anwenden",
    "bewerte_klasse",
    "renditen",
    "urteil_anpassen",
]
