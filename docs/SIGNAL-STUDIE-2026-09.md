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

---

## Ergebnis (30.09.2026)

Phase A (24 Coins × 1 705 Bewertungszeitpunkte), Phase B (1 009 Wachen) und Phase C
gerechnet, Regeln unverändert wie oben. Erwartungswert je Trade in R nach Kosten:

| Filter | Ausstieg | IS n | IS E | OOS n | OOS E | OOS 90 % |
|---|---|---:|---:|---:|---:|---|
| F0 alle | X0 Plan | 338 | −0,047 | 295 | −0,030 | −0,14 … +0,08 |
| F0 alle | X1 ganz | 328 | −0,062 | 287 | −0,001 | −0,16 … +0,16 |
| F1 Tor fest | X0 Plan | 76 | +0,002 | 41 | **−0,195** | −0,51 … +0,14 |
| F1 Tor fest | X1 ganz | 74 | +0,004 | 41 | −0,085 | −0,52 … +0,38 |
| F2 Tor + Bilanz | X0 Plan | 45 | −0,121 | 30 | −0,321 | −0,68 … +0,08 |
| F3 Tor + BTC-Lage | X0 Plan | 63 | −0,021 | 33 | −0,319 | −0,67 … +0,04 |
| F4 Tor + eigener Trend | X0 Plan | 71 | +0,011 | 39 | −0,277 | −0,59 … +0,06 |
| F5 Tor + RS | X0 Plan | 57 | +0,032 | 35 | −0,155 | −0,52 … +0,22 |
| F6 Tor + nur Long | X0 Plan | 47 | −0,120 | 5 | −0,163 | — |

**Urteil nach der Regel: keine Regel wird übernommen.** Keine Variante hat in OOS einen
Erwartungswert über null; X1 ist in IS schlechter als X0. Es bleibt beim Alarm-Tor vom 26.09.

### Was das heißt — ohne Beschönigung

Der Struktur-Scanner, genau so wie er live läuft, liegt über 19 Monate und 24 Coins bei
**etwa null R je Trade nach Kosten**. Das Alarm-Tor (F1) ist in OOS nicht besser als alle
Trades zusammen (F0), sondern schlechter — die Spanne ist allerdings so breit, dass auch
das Rauschen sein kann. Keiner der vorab festgelegten Zusatzfilter dreht das Ergebnis.

Die gute Live-Bilanz der Krypto-Signale seit 13.09. (30 Trades, +12 R) ist damit ein
einzelner Marktabschnitt, kein Beleg. Die Replay-Zahlen sind der größere und ältere Test.

### Nur zur Einordnung (nachträglich angeschaut, entscheidet nichts)

- Shorts: IS −0,087 R, OOS −0,087 R (n = 147 / 180). Longs: IS −0,017, OOS +0,060.
- „Rücksetzer im Trend": IS −0,286, OOS −0,106 (n = 41 / 31) — in beiden Hälften die
  schwächste Setup-Art. „Ausbruch aus der Basis": ≈ 0 in beiden Hälften.
- Diese Zahlen sind aus denselben Daten ausgewählt, auf denen sie gut oder schlecht
  aussehen. Wer daraus eine Regel macht, muss sie vorab registrieren und vorwärts prüfen.
