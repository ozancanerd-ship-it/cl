# Ausstiegs-Studie 2 (2026-10) — Gewinne laufen lassen statt bei Einstand rausfliegen?

**Vorab festgelegt am 04.10.2026, ca. 17:00 UTC, bevor ein Ergebnis gerechnet wurde.**
Was hier steht, wird nach dem Lauf nicht mehr geändert; das Ergebnis kommt unten in einen
eigenen Abschnitt.

## Anlass

Ozan, 04.10.: „Es kann nicht sein, dass fast jeder Buy-Alarm negativ ist — versuch alles,
damit wir mehr Gewinn erzielen."

Befund aus den 16 Handy-Alarmen seit 19.09.: mehrere liefen weit ins Plus und brachten nach
Plan trotzdem fast nichts —

| Wert | bestes zwischendurch | Ergebnis nach Plan |
|---|---:|---:|
| Decred | +2,3 R | +0,33 R |
| DeepBook | +2,9 R | +0,33 R |
| Gala | +3,0 R | +0,33 R (Ziel 1, dann Einstand) |
| Mantle | +2,5 R | +1,0 R |
| Aave | +2,6 R | +1,0 R |

Der Plan verkauft ein Drittel an Ziel 1 und zieht den Stop auf Einstand. Ziel 2 und 3
liegen an Strukturmarken, die oft weiter weg sind, als der Kurs läuft — dann kommt der
Kurs zurück und der Rest fliegt bei Einstand raus. Die Frage ist, ob ein **nachgezogener
Stop** (Trailing) mehr von solchen Bewegungen behält, ohne anderswo mehr zu kosten.

Die erste Ausstiegs-Studie (docs/AUSSTIEG-STUDIE-2026-10.md) hat nur „raus bei gedrehter
Analyse" gegen „Plan weiter" verglichen. Ein Trailing-Stop wurde nie gemessen.

## Daten (fest)

* Kerzen von data.binance.vision, 24 Coins wie in der Signal-Studie (BTC ETH SOL XRP ADA
  DOGE LINK AVAX DOT LTC BCH ATOM NEAR UNI AAVE INJ SEI ARB OP SUI FET HBAR XLM TAO),
  01/2025 – 03.10.2026, neu geladen am 04.10.
* Scanner und Wachliste **in der heutigen Fassung** (`scripts/signal_replay.py`, Phase A
  und B, Profil aggressiv, Bewertung alle 8 Std.), Zeitraum 01.03.2025 – 03.10.2026.
  **In-Sample (IS):** Aufnahme bis 31.12.2025. **Out-of-Sample (OOS):** ab 01.01.2026.
* Fälle: jede Wache mit Einstieg (Einstiegskurs und Einstiegszeit vorhanden).
* Kursweg: M15-Kerzen ab der ersten vollen Stunde nach dem Einstieg, höchstens 10 Tage
  (wie der Ablauf der Wachliste); danach Rest zum Schlusskurs.
* Berührt eine Kerze Stop und Ziel, zählt der Stop (pessimistisch). Öffnet eine Kerze
  jenseits des Stops, zum Eröffnungskurs.
* Kosten: 0,5 % Hin- und Rückweg vom Einsatz, in R umgerechnet, in allen Varianten gleich.
* Alle Varianten werden auf **denselben** Kursweg gerechnet — auch der Plan selbst (X0),
  damit kein Unterschied aus der Rechenweise kommt. Ausstieg bei gedrehter Analyse spielt
  keine Rolle (Studie 1: kein messbarer Unterschied).

## Varianten (fest, genau diese vier)

`R` = Abstand Einstieg–Stop beim Einstieg.

| Kürzel | Regel |
|---|---|
| **X0 Plan** | ⅓ an Ziel 1, ⅓ an Ziel 2, ⅓ an Ziel 3; nach Ziel 1 Stop auf Einstand, nach Ziel 2 auf Ziel 1 (so läuft es live) |
| **X2 Trail** | ⅓ an Ziel 1, Stop auf Einstand; der Rest (⅔) mit einem Stop, der dem besten Kurs seit Einstieg im Abstand von 1 R folgt (nie unter Einstand); keine weiteren festen Ziele |
| **X3 Trail weit** | wie X2, aber Abstand 1,5 R |
| **X4 Halb + Trail** | ½ an Ziel 1, Stop auf Einstand; die andere Hälfte mit Trailing 1 R wie X2 |

## Entscheidungsregel (fest)

Verglichen wird je Fall der Unterschied **Variante − X0** in R.

Eine Variante **ersetzt den Plan**, wenn
1. der mittlere Unterschied in **IS und OOS über null** liegt,
2. OOS mindestens 100 Fälle hat,
3. im Monats-Bootstrap auf OOS (ganze Monate ziehen, 5 000 Ziehungen, Seed 7) das
   5-%-Quantil des mittleren Unterschieds über null liegt, und
4. die Variante auch auf der Teilmenge **„Long mit benanntem Setup"** (das, was heute
   klingeln kann) in IS und OOS im Mittel nicht schlechter ist als X0.

Erfüllen mehrere Varianten das, gewinnt die mit dem größeren mittleren Unterschied in OOS.
Erfüllt keine es, **bleibt der Plan**, wie er ist.

Ausdrücklich keine weiteren Varianten nachträglich. Was nach dem Lauf auffällt, steht
unten unter „nur zur Einordnung" und entscheidet nichts.

---

## Ergebnis

(folgt nach dem Lauf)
