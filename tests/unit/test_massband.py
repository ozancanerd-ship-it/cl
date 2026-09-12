"""Das Maßband: zu weiter Stop, zu weites Ziel — beides kein Trade.

Die Grenzen stammen nicht aus einem Lehrbuch, sondern aus den 60 abgeschlossenen
Signalen des eigenen Vorlaufs (Auswertung vom 12. September 2026):

    Stopabstand  ≥ 4 %      n=46   Ø −0,38 R   TP1 15 %
    Stopabstand  2–4 %      n= 9   Ø −0,11 R   TP1 33 %
    erwarteter Weg ≥ 10 %   n=36   Ø −0,46 R   TP1 11 %
    erwarteter Weg 6–10 %   n=18   Ø −0,06 R   TP1 33 %

Dieselbe Aussage aus zwei Richtungen: je weiter Stop und Ziel, desto schlechter das
Ergebnis. Das System hat das vorher belohnt — der Faktor ``bewegungsraum`` stieg
linear mit dem Abstand. Deshalb war Note A− die schlechteste Gruppe im Buch.

Die Tests halten drei Dinge fest, die leicht wieder verlorengehen:

1. Der Bewegungsraum ist ein **Band**, keine Rampe: sehr weit weg gibt WENIGER Punkte
   als mittelweit, nicht mehr.
2. Die Deckel greifen und nennen den Grund im Klartext.
3. Ein normaler Trade in der Mitte des Bandes wird von den Deckeln NICHT angefasst —
   ein Filter, der alles wegwirft, ist kein Filter, sondern ein Aus-Schalter.
"""

from __future__ import annotations

from trading_agent.scanner.chart_score import (
    MAX_ERWARTET_PCT,
    MAX_PUNKTE,
    MAX_STOP_PCT,
    RAUM_AUS,
    RAUM_BAND,
)


def _raum_punkte(in_atr: float) -> float:
    """Dieselbe Kurve wie im Scorer — hier nachgebaut, um sie allein zu prüfen."""
    unten, oben = RAUM_BAND
    if in_atr <= unten:
        anteil = max(0.0, in_atr / unten) ** 2
    elif in_atr <= oben:
        anteil = 1.0
    else:
        anteil = max(0.0, (RAUM_AUS - in_atr) / (RAUM_AUS - oben))
    return MAX_PUNKTE["bewegungsraum"] * anteil


def test_bewegungsraum_ist_ein_band_keine_rampe() -> None:
    """Weiter weg darf nicht automatisch besser sein — genau das war der Fehler."""
    mitte = _raum_punkte(3.0)
    sehr_weit = _raum_punkte(7.0)
    assert mitte > sehr_weit, "ein Ziel 7 ATR entfernt darf nicht besser bewertet sein als eines bei 3"
    assert _raum_punkte(2.0) == MAX_PUNKTE["bewegungsraum"]
    assert _raum_punkte(9.0) == 0.0


def test_zu_nah_zaehlt_auch_nicht() -> None:
    """Unter dem Band faellt es ab — ein Ziel 0,5 ATR entfernt ist kein Trade."""
    assert _raum_punkte(0.5) < _raum_punkte(1.2) < _raum_punkte(2.0)


def test_grenzen_liegen_weiter_als_das_optimum() -> None:
    """Die beste Kombination in den Daten war Stop 2–6 % und Weg 4–10 %, bei n=12.

    Genau darauf zu schneiden waere Kurvenanpassung an zwoelf Faelle. Die Deckel
    liegen deshalb bewusst darueber: sie schneiden den Unsinn ab, nicht das Optimum.
    """
    assert MAX_STOP_PCT >= 6.0
    assert MAX_ERWARTET_PCT >= 10.0
