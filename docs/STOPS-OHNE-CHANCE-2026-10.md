# Stops ohne Chance — Nachmessung 01.10.2026

## Frage

Welche Trades laufen in den Stop, ohne vorher auch nur +0,5 R im Plus gewesen zu sein
(Fehlstarts)? Gibt es ein Muster — Klasse, Setup-Art, Einstiegsart („sofort" gegen Limit
im Rücklauf), Note —, das sich als Regel fürs Alarm-Tor lohnt?

Regel des Projekts: eine neue Regel nur, wenn sie in der eigenen Bilanz **und** im
19-Monats-Nachspiel (`docs/SIGNAL-STUDIE-2026-09.md`, In-Sample bis 31.12.2025,
Out-of-Sample ab 01.01.2026) in dieselbe Richtung zeigt.

## 1. Eigene Bilanz (38 gezählte Trades, Stand 01.10. 15:30 UTC)

17 Stops, davon **10 ohne je +0,5 R** gesehen zu haben:

| Wert | Klasse | Setup | Einstieg | höchstens |
|---|---|---|---|---:|
| LTC | Krypto Long A− | Rücksetzer im Trend | Limit (FVG M15) | +0,08 R |
| SCHW | Aktie Long B | Rückeroberung | sofort | +0,09 R |
| ADBE | Aktie Short B+ | — | Limit (FVG H1) | +0,15 R |
| DDOG | Aktie Short B+ | Rücksetzer im Trend | Limit (FVG H4) | +0,26 R |
| PAXG | Gold Short B+ | Ausbruch | Limit (FVG M15) | +0,27 R |
| UNH | Aktie Long B+ | Rückeroberung | Limit (FVG M15) | +0,29 R |
| RENDER | Krypto Long B+ | Ausbruch | sofort | +0,36 R |
| PEPE | Krypto Short B | Ausbruch | sofort | +0,40 R |
| ABNB | Aktie Long B+ | — | Limit (FVG M15) | +0,42 R |
| DIS | Aktie Long B+ | Rückeroberung | Limit (FVG H4) | +0,49 R |

* **Aufs Handy ging davon genau einer** (RENDER). Die übrigen neun gingen nicht aufs
  Telefon — je nach Datum wegen des Alarm-Tors (Aktien Long gesperrt, Setup-Art nicht
  bewährt, kein Name) oder der Notenschwelle davor.
* Zusammen: aufs Handy 3 Trades, +1,5 R; nur in der App 35 Trades, −3,5 R.
* 6 der 10 Fehlstarts sind Aktien, 7 der 10 hatten einen Limit-Einstieg im Rücklauf.

## 2. Gegenprobe im Nachspiel (633 Trades, 24 Coins, 19 Monate)

**Einstiegsart:**

| | In-Sample | Out-of-Sample |
|---|---|---|
| sofort | n = 142, Ø −0,079 R, PF 0,85 | n = 148, Ø **+0,066 R**, PF 1,14 |
| Limit im Rücklauf | n = 196, Ø **−0,025 R**, PF 0,95 | n = 147, Ø −0,126 R, PF 0,78 |
| Stop ohne +0,5 R | sofort 23 %, Limit 25 % | sofort 16 %, Limit 22 % |

Die Rangfolge **kippt** zwischen den Hälften (In-Sample ist Limit besser, Out-of-Sample
sofort). Das ist kein Muster, sondern Rauschen → **keine Regel**.

**Note** (nur die beiden freien Setup-Arten „Ausbruch aus der Basis" und „Rückeroberung"):

| Note | In-Sample | Out-of-Sample |
|---|---|---|
| A− | n = 45, Ø −0,063 R | n = 26, Ø −0,463 R |
| B+ | n = 109, Ø +0,015 R | n = 80, Ø +0,036 R |
| B | n = 77, Ø −0,105 R | n = 90, Ø +0,018 R |

A− war in **beiden** Hälften nicht besser als B+ (zusammen −0,23 R je Trade schlechter,
Permutationstest p = 0,15 → nicht belastbar). B lag in beiden Hälften etwas unter B+ —
das passt zur bestehenden Regel „B+ nur bei bewährter Setup-Art, B nie".

## Entscheidung

1. **Keine neue Regel.** Die Fehlstarts der eigenen Bilanz sind schon weitgehend nicht
   aufs Handy gegangen (9 von 10). Die Einstiegsart zeigt im Nachspiel
   kein stabiles Muster.
2. **Beobachtung für später (vorab festgehalten, nicht umgesetzt):** Die Note sagt im
   Nachspiel nichts über das Ergebnis — A− ist nicht besser als B+. Sollten nach
   mindestens 30 weiteren Alarm-Trades in der eigenen Bilanz A−/A ebenfalls nicht
   besser abschneiden als B+, wird die Rangfolge im Tor („die besten zuerst", wenn mehr
   als drei Alarme am Tag anstehen) nicht mehr nach Note, sondern nach Bilanz der
   Setup-Art sortiert. Vorher nicht.
3. Aktien bleiben, wie sie sind: Aktien Long gesperrt (eigene Bilanz −3,3 R aus 6),
   Aktien-Shorts nur mit bewährter Setup-Art.
