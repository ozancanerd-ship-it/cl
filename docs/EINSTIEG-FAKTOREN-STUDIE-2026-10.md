# Einstiegs-Faktoren, Notenskala und BTC-Filter — Studie vom 07.10.2026

**Fragen** (Roadmap 2, 3, 5): Welche Merkmale beim Einstieg trennen gute von schlechten Trades?
Ist A wirklich besser als B (Notenskala)? Verbessert ein „BTC im Aufwärtstrend"-Filter die Alt-Trades?

**Daten:** Replay aus Studie 2 (24 Coins, 01.03.2025–03.10.2026), 647 eingegangene Fälle
(IS bis 12.2025: 339, OOS ab 01.2026: 308), Ergebnis = R nach Plan (⅓–⅓–⅓) nach 0,5 % Kosten.
**Vorab-Regel** steht im Kopf von `scripts/einstieg_faktoren_studie.py` (Merkmal trennt nur, wenn die
bessere Gruppe in IS und OOS besser ist, je Gruppe OOS n ≥ 40, Monats-Bootstrap 5 %-Quantil > 0).

## Ergebnis (Ø R gute / schlechte Gruppe)

| Merkmal | IS | OOS | OOS-Bootstrap 5 % | trennt? |
|---|---|---|---:|:-:|
| Score oben vs. unten (Terzile) | −0,10 / +0,02 | −0,04 / +0,10 | −0,44 | nein, umgekehrt |
| CRV oben vs. unten | +0,11 / −0,18 | −0,06 / +0,06 | −0,34 | nein |
| Relative Stärke oben vs. unten | −0,02 / −0,27 | +0,08 / −0,07 | −0,07 | nein |
| Umsatz oben vs. unten | +0,12 / −0,23 | −0,14 / 0,00 | −0,32 | nein |
| BTC über EMA50 ja/nein | −0,03 / −0,07 | +0,02 / −0,08 | −0,16 | nein |
| BTC-EMA50 steigt ja/nein | −0,03 / −0,07 | +0,04 / −0,07 | −0,07 | nein |
| Eigen über EMA50 ja/nein | +0,01 / −0,09 | −0,07 / −0,02 | −0,26 | nein |
| Eigen EMA20 > 50 ja/nein | −0,09 / −0,02 | +0,04 / −0,06 | −0,25 | nein |
| Note A−… vs. B+/B | 0,00 / −0,06 | −0,22 / −0,01 | −0,63 | nein, umgekehrt |
| Long vs. Short | −0,02 / −0,09 | +0,06 / −0,10 | −0,09 | nein |
| Einstiegsart „sofort" vs. andere | −0,08 / −0,03 | +0,08 / −0,16 | −0,03 | nein |

Gesamt: Plan Ø −0,04 R je Fall im Replay (live schlechter: −0,36 R — Ausführung, verpasste Einstiege).

## Entscheidungen
1. **Kein Einstiegsmerkmal trennt** nach der Vorab-Regel. Es gibt keinen belegten Filter, der
   Alarme besser macht. Nichts wird am Alarm-Tor oder an Bewertungen geändert.
2. **Notenskala/Score:** Hoher Score ist im OOS *schlechter* als niedriger, A−… schlechter als B —
   der Score **rankt nicht**. Konsequenz: Rang und Note werden nirgends mehr als Qualitätsbeleg
   verkauft; entschieden wird weiter über das Alarm-Tor (belegte Setup-Arten) und Plan/Stop.
   Eine „Neueichung" ohne Signal in den Daten wäre Anpassung an Rauschen und unterbleibt.
3. **BTC-Filter (Roadmap 3):** Richtung stimmt in beiden Zeiträumen für „BTC-EMA50 steigt" /
   „BTC über EMA50" (ja besser als nein), aber der Abstand ist klein und nicht abgesichert
   (Bootstrap −0,07 bis −0,16). **Als Anzeige bleibt er (BTC-Beta, Lage), als Filter nicht.**
   Neue Hypothese für eine eigene Studie: „BTC-EMA50 steigt" + Long, mit mehr Daten.
4. Wo der Verlust sitzt: Einstieg trifft ≈ 0 R, Kosten und Fehlausführung fressen den Rest — Hebel
   sind Kostenkontrolle (Limit-Orders, weniger Trades) und die Setup-Art-Sperre, nicht Scoring.

Grenzen: ein Replay über 19 Monate in einer überwiegend steigenden Altcoin-Phase; elf Merkmale
= Mehrfachtest, deshalb zählt nur, was die strenge Regel besteht.
