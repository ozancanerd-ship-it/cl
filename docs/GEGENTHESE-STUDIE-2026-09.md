# „Gegen die These — Hälfte verkaufen": hält die Regel? — Vorab-Registrierung (28.09.2026)

**Vorab festgelegt, bevor gerechnet wurde.** Die Abschnitte bis „Entscheidungsregel"
werden danach nicht geändert; das Ergebnis kommt unten dazu.

## Anlass

Ozan, 28.09.: „In meinem Portfolio sagt er bei mehreren Sachen, ich soll schon Teil
verkaufen, obwohl du selber meintest, das wäre nicht so sinnvoll."

Die Schlusslicht-Regel („ein Drittel verkaufen") ist am 27.09. gefallen, weil sie den
eigenen Daten nicht standhielt. Eine zweite Teilverkaufs-Regel steht noch in der App und
wurde nie gemessen: **hält man einen Wert long, zeigt die Analyse ein handelbares
Short-Setup, und liegt die relative Stärke unter 45, rät die App, die Hälfte zu verkaufen.**

## Frage

Laufen Coins in genau dieser Lage danach schlechter als ihre Klasse — so viel schlechter,
dass sich ein Teilverkauf (Gebühr, Spread, Steuer) lohnt?

## Daten

* Die Bewertungen der Signal-Studie (echter Scanner, alle 8 Stunden, 24 Coins,
  03/2025 – 09/2026), IS bis 31.12.2025, OOS ab 01.01.2026 — dieselbe Teilung.
* Relative Stärke wie in der App: Perzentil aus 0,65 × Rendite 21 Tage + 0,35 × Rendite
  63 Tage innerhalb der Coins zum selben Zeitpunkt.
* Lage „gegen die These": Richtung short, handelbar, relative Stärke unter 45.
* Ergebnis: Rendite der nächsten 7 und 14 Tage relativ zum Mittel aller Coins am selben
  Zeitpunkt. Je Coin höchstens ein Fall pro 7 Tage (sonst zählt dieselbe Lage dreimal am Tag).

## Entscheidungsregel

Die Regel bleibt nur, wenn die Lage „gegen die These" nach 7 Tagen **in IS und in OOS**
mindestens **1,0 Prozentpunkte** schlechter lief als die Klasse, bei mindestens 20 Fällen
je Hälfte. Sonst fällt der automatische Teilverkaufs-Rat weg; der Hinweis auf das
Gegen-Setup bleibt als Beobachtung stehen.

---

## Ergebnis (30.09.2026)

`python3 scripts/gegenthese_studie.py` auf den vollständigen Scan-Zeilen der Signal-Studie.

| Hälfte | Fälle | nach 7 Tagen, relativ zur Klasse (Mittel) | Median | Anteil schlechter |
|---|---:|---:|---:|---:|
| IS | 108 | **+1,38 Pp** (besser, nicht schlechter) | −0,29 Pp | 55 % |
| OOS | 123 | **−0,32 Pp** | −0,65 Pp | 53 % |

Nach 14 Tagen: IS +1,81 Pp, OOS −0,37 Pp.

**Urteil nach der Regel: die Regel fällt.** Verlangt waren mindestens 1,0 Prozentpunkte
schlechter in beiden Hälften; in IS lief die Lage sogar besser als die Klasse. Der Rat
„Gegen die These — Hälfte verkaufen" ist aus dem Depot entfernt. Der Hinweis auf das
Gegen-Setup bleibt als Satz in der Halte-Empfehlung stehen, ohne Stückzahl und ohne Alarm.
