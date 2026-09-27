# Signal-Studie 2026-09 — was macht die Kauf- und Verkaufssignale besser?

**Vorab festgelegt am 27.09.2026, bevor der erste Lauf ausgewertet wurde.** Was hier steht,
wird nach dem Lauf nicht mehr geändert. Ergänzungen kommen unten in einen eigenen Abschnitt
„Ergebnis" — mit Datum.

## Anlass

Ozan, 27.09.: „brauchen mehr Gewinne, bis jetzt waren die nur ok, brauchen bessere Buy und
Sell Signale." Die eigene Bilanz (60 abgeschlossene Signale, 13.–26.09.) liegt bei −2,5 R.
57 % der Trades kamen nie auch nur 0,3 R ins Plus. Das ist ein Einstiegsproblem.

Die Bilanz reicht aber nicht, um Regeln zu ändern: sie stammt aus zwei Wochen und einem
einzigen Marktabschnitt. Deshalb wird der **echte Scanner** (`bewerte_chart` + Wachliste)
über 19 Monate Historie abgespielt, und jede Änderung muss sich dort bewähren — innerhalb
UND außerhalb der Stichprobe, auf der sie gefunden wurde.

## Aufbau

- **Werte:** 24 liquide Coins, die bei Kraken handelbar sind und bei Binance lange Historie
  haben: BTC ETH SOL XRP ADA DOGE LINK AVAX DOT LTC BCH ATOM NEAR UNI AAVE INJ SEI ARB OP
  SUI FET HBAR XLM TAO. Kerzen von data.binance.vision (Kurse weichen von Kraken um
  Promille ab — für die Frage „welche Regel ist besser" unerheblich).
- **Zeitraum:** 01.03.2025 – 20.09.2026. **In-Sample (IS):** 01.03.2025 – 31.12.2025.
  **Out-of-Sample (OOS):** 01.01.2026 – 20.09.2026.
- **Scanner:** unverändert, Profil `aggressiv`, dieselben Kerzenfenster wie live (M5/M15
  je 720 Kerzen wie bei Kraken, H1 20 Tage, H4 45 Tage, D1 400 Tage). Bewertet alle 8
  Stunden (00, 08, 16 UTC) — live alle 10 Minuten; die Studie sieht also weniger Setups,
  aber dieselbe Art.
- **Wachliste:** die echte Klasse `Wachliste` inklusive Einstiegsbestätigung, Stop-zuerst,
  Zielen, Schutz-Stop nach Ziel 1/2, Ablauf nach 10 Tagen und Ungültigkeit bei gedrehter
  Analyse. Geprüft wird stündlich gegen M15-Hoch/Tief.
- **Ergebnis je Trade in R nach Plan:** je Ziel ein Drittel zum tatsächlichen Zielkurs,
  Rest am Schutz-Stop, am Stop oder beim Ausstieg (gedrehte Analyse) zum Kurs. Kosten:
  0,5 % Hin- und Rückweg vom Einsatz, umgerechnet in R. Noch offene Trades am Ende zählen
  nicht.

## Was verglichen wird (fest)

Filter, jeweils auf dieselben Trades angewandt:

| Kürzel | Regel |
|---|---|
| F0 | alle Trades der Wachliste (so lief es bis 26.09.) |
| F1 | Alarm-Tor fest: benanntes Setup, Note ≥ A−, CRV ≥ 2, Ziel 1 ≥ 1,5 % |
| F2 | F1 + Tor-Bilanz der Setup-Art, zeitlich korrekt (nur Trades, die VOR dem Einstieg entschieden waren) |
| F3 | F1 + Bitcoin-Lage: Long nur, wenn BTC über seinem 50-Tage-Durchschnitt schließt; Short nur darunter |
| F4 | F1 + eigener Trend: Long nur, wenn der Coin über seinem 50-Tage-Durchschnitt schließt und der 20er über dem 50er liegt; Short spiegelbildlich |
| F5 | F1 + relative Stärke: Long nur ab RS 50, Short nur bis RS 50 |
| F6 | F1 + nur Long |

Ausstiege, jeweils auf dieselben Einstiege:

| Kürzel | Regel |
|---|---|
| X0 | Plan wie live: Drittel an jedem Ziel, nach Ziel 1 Stop auf Einstand, nach Ziel 2 auf Ziel 1 |
| X1 | alles oder nichts: ganze Position bis Ziel 3 oder Stop, kein Nachziehen |

## Entscheidungsregel (fest)

Eine Regel wird in den Live-Betrieb übernommen, wenn **alle** Punkte erfüllt sind:

1. Erwartungswert je Trade besser als die Vergleichsregel — **in IS und in OOS**.
2. Mindestens 30 Trades in OOS.
3. Erwartungswert in OOS über null.
4. Bootstrap (5 000 Ziehungen) auf OOS: die 90-%-Spanne des Erwartungswerts liegt nicht
   komplett unter null.

Hält eine Regel nur in IS, wird sie **nicht** übernommen — auch wenn sie dort glänzt. Wird
keine Regel übernommen, bleibt es beim Alarm-Tor vom 26.09.; das ist dann das Ergebnis.
