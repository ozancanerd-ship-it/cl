"""Gleichlauf — wie stark sich zwei Werte tatsaechlich zusammen bewegen.

WARUM DAS HIER STEHT

Ein Depot aus achtzehn Positionen sieht breit aus. Ozans Depot besteht aus zehn
Altcoins, zwei US-Tech-Aktien und drei Turbos auf US-Tech. Das sind nicht achtzehn
Wetten, das sind zwei — und die eine davon laeuft gehebelt. Solange die App nur
"Konzentration: groesste Position 14 %" anzeigt, sieht ein Depot gesund aus, das in
Wahrheit an einem einzigen Faktor haengt.

Der ehrliche Weg, das zu zeigen, ist nicht "Krypto laeuft halt zusammen" (eine
Behauptung), sondern die gemessene Korrelation der Tagesrenditen. Die rechnet dieses
Modul im Scan aus und schickt sie so klein in die App, dass der Browser jede Paarung
selbst nachrechnen kann.

WIE DIE ZAHLEN IN DIE APP KOMMEN

Die volle Matrix waere 206x206 — zu gross und zu starr, denn die App soll auch die
Korrelation einer NEUEN Chance gegen das bestehende Depot rechnen koennen. Deshalb
wandert nicht die Matrix in die Datei, sondern die standardisierte Renditereihe je
Instrument: 90 Tage, z-standardisiert, auf ein Byte je Tag quantisiert, Base64.
Rund 120 Zeichen je Instrument, keine 25 KB fuer das ganze Universum. Die Korrelation
ist dann im Browser ein Skalarprodukt.

Die Quantisierung kostet Genauigkeit: bei Skala 32 liegt ein Schritt bei 1/32
Standardabweichung, der Fehler in der Korrelation bleibt unter 0,01. Das ist deutlich
genauer, als die Frage je sein wird ("laeuft das zusammen?").

WORAUF GEACHTET WIRD

* Handelstage statt Kalendertage. Krypto handelt sieben Tage, Aktien fuenf. Wer die
  Reihen einfach nebeneinander legt, korreliert Montag gegen Samstag. Deshalb wird auf
  ein gemeinsames Datumsraster gerechnet — die Tage, die bei der Mehrheit vorkommen.
* Lueckenhafte Reihen fliegen raus, statt mit Nullen aufgefuellt zu werden. Eine
  aufgefuellte Luecke ist eine erfundene Rendite von 0 % und zieht jede Korrelation
  Richtung null.
* Ein Wert ohne Bewegung (Stablecoin, ausgesetzter Handel) hat keine Standard-
  abweichung und damit keine Korrelation. Der faellt raus, statt durch null zu teilen.
"""

from __future__ import annotations

import base64
import math
from collections import Counter
from datetime import date
from typing import Any

# 90 Handelstage — gut ein Quartal. Kuerzer wird die Korrelation vom Rauschen bestimmt,
# laenger mittelt sie ueber Regimewechsel hinweg und beschreibt dann keinen der beiden.
TAGE = 90
# Ein Schritt = 1/32 Standardabweichung, Bereich +/- 3,97. Passt in ein Byte.
SKALA = 32
# Weniger als 60 gemeinsame Tage: die Korrelation waere eine Zufallszahl mit Nachkommastellen.
MIN_TAGE = 60
# Ein Instrument muss auf mindestens 90 % des Rasters einen Kurs haben.
MIN_ABDECKUNG = 0.9


def sammle(bars: Any) -> dict[date, float]:
    """Tagesschlusskurse aus D1-Kerzen. Der Tag ist ``open_time``, nicht ``close_time``:
    die Kerze vom 16.09. oeffnet am 16.09. und schliesst am 17.09. um 00:00."""
    reihe: dict[date, float] = {}
    for b in bars or ():
        t = getattr(b, "open_time", None)
        c = getattr(b, "close", None)
        if t is None or c is None:
            continue
        try:
            wert = float(c)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(wert) or wert <= 0:
            continue
        reihe[t.date()] = wert
    return reihe


def _raster(reihen: dict[str, dict[date, float]]) -> list[date]:
    """Das gemeinsame Datumsraster: Tage, an denen fast alle einen Kurs haben.

    Die Schwelle ist der Punkt, an dem dieses Modul beim ersten Testlauf falsch lag.
    Zuerst stand hier "die Haelfte". Im echten Universum sind 104 von 205 Werten Krypto
    — also kommen Samstag und Sonntag bei der Mehrheit vor und landen im Raster. Danach
    fehlt jedem Aktienwert ein Drittel der Tage, er faellt wegen Luecken raus, und die
    Gleichlauf-Rechnung enthaelt am Ende nur noch Krypto. Genau die Analyse, die zeigen
    soll, dass Ozans Depot aus zwei Wetten besteht, haette dann eine davon nicht gekannt.

    Mit "fast alle" regelt sich das von selbst: ein Samstag steht nur bei den rund 50 %
    Krypto in den Daten und faellt raus; ein US-Feiertag fehlt allen Aktien und faellt
    ebenfalls raus; ein normaler Handelstag ist ueberall da. Laeuft ein Scan nur ueber
    Krypto, sind Samstage wieder bei 100 % — dann ist das Raster eben siebentaegig, was
    fuer ein reines Kryptofeld auch richtig ist.
    """
    if not reihen:
        return []
    zaehler: Counter[date] = Counter()
    for r in reihen.values():
        zaehler.update(r.keys())
    for anteil in (0.9, 0.75, 0.6, 0.5):
        schwelle = max(2, math.ceil(len(reihen) * anteil))
        tage = sorted(d for d, n in zaehler.items() if n >= schwelle)
        if len(tage) >= TAGE + 1:
            return tage[-(TAGE + 1) :]
    # Nichts reicht fuer das volle Fenster — dann das breiteste Raster, das es gibt,
    # und ``baue`` entscheidet anhand von MIN_TAGE, ob das noch traegt.
    schwelle = max(2, math.ceil(len(reihen) * 0.5))
    tage = sorted(d for d, n in zaehler.items() if n >= schwelle)
    return tage[-(TAGE + 1) :]


def baue(reihen: dict[str, dict[date, float]]) -> dict[str, Any] | None:
    """Der kompakte Block fuer scan.json. ``None``, wenn die Datenlage nicht traegt."""
    tage = _raster(reihen)
    if len(tage) < MIN_TAGE + 1:
        return None

    z_je: dict[str, str] = {}
    sd_je: dict[str, float] = {}
    for name, reihe in reihen.items():
        kurse = [reihe.get(t) for t in tage]
        vorhanden = sum(1 for k in kurse if k is not None)
        if vorhanden < len(tage) * MIN_ABDECKUNG:
            continue
        # Einzelne Luecken werden mit dem letzten bekannten Kurs geschlossen — das ist
        # eine Rendite von 0 % fuer genau diesen Tag und bei unter 10 % Luecken ehrlich.
        # Fuehrende Luecken lassen sich nicht schliessen; dann faellt der Wert raus.
        if kurse[0] is None:
            continue
        gefuellt: list[float] = []
        letzter = kurse[0]
        for k in kurse:
            if k is not None:
                letzter = k
            gefuellt.append(float(letzter))

        rend = [math.log(gefuellt[i] / gefuellt[i - 1]) for i in range(1, len(gefuellt))]
        n = len(rend)
        mittel = sum(rend) / n
        var = sum((x - mittel) ** 2 for x in rend) / (n - 1)
        sd = math.sqrt(var)
        if sd <= 1e-9:
            continue  # bewegt sich nicht — Stablecoin oder ausgesetzt

        roh = bytes(max(0, min(255, round((x - mittel) / sd * SKALA) + 128)) for x in rend)
        z_je[name] = base64.b64encode(roh).decode("ascii")
        # Tagesschwankung in Prozent — die Groesse, mit der die App aus Gewichten und
        # Korrelationen das Tagesrisiko des Depots in Euro rechnet.
        sd_je[name] = round(sd * 100, 4)

    if len(z_je) < 5:
        return None
    return {
        "tage": len(tage) - 1,
        "bis": tage[-1].isoformat(),
        "skala": SKALA,
        "mitte": 128,
        "z": z_je,
        "sd": sd_je,
    }
