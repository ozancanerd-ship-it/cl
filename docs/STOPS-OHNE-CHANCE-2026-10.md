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
Permutationstest p = 0,15 → nicht belastbar). B lag in beiden Hälften etwas unter B+.
(Korrektur abends: das Gewinner-Tor vom 01.10. ließ B+ gar nicht mehr durch — siehe unten.)

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

## Nachtrag 01.10. abends — Tageszeit, Wartezeit, Weg zum Einstieg

Frage (Ozan: „bessere Kaufsignale"): laufen die beiden freien Setup-Arten besser, wenn
der Einstieg zu einer bestimmten Tageszeit, am Wochenende, nach kurzer oder langer
Wartezeit oder nach einem bestimmten Rücklauf kommt? Einstiegszeit aus den M15-Kerzen
(erste Kerze ab Aufnahme, die den Einstiegskurs enthält), Weg zum Einstieg in R aus dem
Kurs bei Aufnahme. Alle 633 Nachspiel-Trades zugeordnet.

| Aufteilung (freie Setups) | In-Sample | Out-of-Sample |
|---|---|---|
| Einstieg 1–8 h nach Aufnahme | Ø +0,331 R (n = 42) | Ø −0,038 R (n = 42) |
| Einstieg sofort (< 1 h) | Ø −0,132 R (n = 179) | Ø −0,004 R (n = 147) |
| Einstieg 16–24 UTC | Ø +0,137 R (n = 99) | Ø −0,130 R (n = 82) |
| Einstieg 08–16 UTC | Ø −0,167 R (n = 75) | Ø +0,067 R (n = 64) |
| Rücklauf 0,25–0,75 R bis zum Einstieg | Ø +0,167 R (n = 38) | Ø −0,107 R (n = 20) |
| Wochenende | Ø +0,096 R (n = 60) | Ø −0,043 R (n = 54) |

**Jede Aufteilung, die In-Sample gut aussieht, kippt Out-of-Sample.** Das ist genau das
Muster, vor dem die Studie schützen soll: eine Regel daraus hätte die Vergangenheit
schöner gemacht und die Zukunft nicht. → **Keine Regel**, nichts geändert.

Mitgefunden und behoben: der Stop-Alarm (Issue #188, Zcash) nannte als „Ergebnis" den
tiefsten Kurs der Prüfstunde (−1,42 R). Mit einer Stop-Order ist man am Stop draußen
(−1,11 R am tatsächlichen Einstieg; die Bilanz zählt einen Stop als −1 R). Jetzt: „Ergebnis laut
Plan …; zwischendurch lief der Kurs bis …".

## Nachtrag 01.10. abends — B+ bei bewährter Setup-Art wieder frei

Beim Live-Check fiel auf: die einzige freie Setup-Art „Ausbruch aus der Basis" ist **nur
wegen ihrer B+-Trades bewährt** — geklingelt hätten aber nur A−-Trades, denn das
Gewinner-Tor vom 01.10. hatte B+ mit gesperrt („strengere Auswahl, kein Beweis").

| „Ausbruch aus der Basis" | eigene Bilanz | Nachspiel In-Sample | Nachspiel Out-of-Sample |
|---|---|---|---|
| B+ | 11 Trades, **+3,8 R** | n = 60, Ø +0,020 R | n = 41, Ø **+0,102 R** |
| A− | 2 Trades, −0,7 R | n = 24, Ø −0,053 R | n = 11, Ø −0,493 R |

B+ ist in der eigenen Bilanz **und** in beiden Hälften des Nachspiels nicht schlechter
als A− — das ist die Bedingung, unter der hier eine Regel geändert wird.

**Geändert:** B+ klingelt wieder, aber nur bei einer in der eigenen Bilanz bewährten
Setup-Art (`alarm_tor.B_PLUS_BEI_BEWAEHRT`). Unverändert: B nie, höchstens drei Alarme am
Tag, derselbe Coin einmal in 48 Stunden, Aktien Long gesperrt.

**Zurücknehmen**, wenn die B+-Alarme nach zehn entschiedenen Trades unter Profitfaktor 1
liegen.

Nicht geändert: A− bleibt frei, obwohl es im Nachspiel bei dieser Art in beiden Hälften
negativ war — 35 Trades sind zu wenig, um eine bessere Note auszusperren.
