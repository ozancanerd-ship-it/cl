# Finanzierung am Terminmarkt als Signal? — Vorab-Registrierung (27.09.2026)

**Vorab festgelegt, bevor ein einziges Ergebnis gerechnet wurde.** Die Abschnitte bis
„Entscheidungsregel" werden danach nicht mehr geändert; das Ergebnis kommt unten dazu.

## Anlass

Seit dem 27.09. zeigt die App je Coin die Finanzierung am Terminmarkt (Kraken Futures) und
warnt ab ±50 %/−30 % aufs Jahr. Das ist Kontext. Ob die Zahl darüber hinaus die Signale
besser macht — etwa „kein Long-Alarm, wenn die Longs schon teuer zahlen" —, ist eine
Behauptung, die man prüfen muss, bevor sie in den Score oder ins Alarm-Tor darf.

## Hypothese

Coins, deren Longs gerade viel Finanzierung zahlen (überfüllte Long-Seite), laufen in den
folgenden Tagen **schlechter** als der Rest ihrer Klasse; Coins mit negativer Finanzierung
(die Shorts zahlen) laufen **besser**.

## Daten

* Finanzierung: Kraken Futures, `historicalfundingrates` je Perpetual, stündlich
  (`relativeFundingRate`), so weit die Schnittstelle zurückreicht (rund ein Jahr).
* Kurse: Tagesschlusskurse der 24 Coins der Signal-Studie (Binance).
* Signal am Tag t: mittlere Finanzierung der letzten 7 Tage, aufs Jahr gerechnet — nur
  Daten, die am Ende von Tag t bekannt waren.
* Ergebnis: Rendite der nächsten 7 und 14 Tage **relativ zum Mittel aller Coins** am
  selben Tag (damit misst die Studie die Auswahl, nicht den Markt).
* Stichprobe: jeder 7. Tag (nicht überlappend für 7 Tage).
* Zeitlich geteilt: erste Hälfte der Tage (IS), zweite Hälfte (OOS).

## Was verglichen wird

1. Quintile je Tag nach Finanzierung: oberstes Fünftel (teuerste Longs) gegen unterstes
   Fünftel. Kennzahl: mittlere relative Rendite je Quintil, und die Spanne oben minus unten.
2. Absolute Schwelle wie in der App: Finanzierung ≥ 50 %/Jahr gegen den Rest.

## Entscheidungsregel

Die Finanzierung darf als Filter ins Alarm-Tor (kein Long-Alarm im obersten Fünftel), wenn
**alle** Punkte erfüllt sind:

1. Spanne oberstes minus unterstes Fünftel (7 Tage) ≤ −1,0 Prozentpunkte — **in IS und
   in OOS**.
2. Das oberste Fünftel liegt in beiden Hälften unter dem Klassenmittel.
3. Mindestens 30 Stichtage je Hälfte.

Sonst bleibt es beim Kontext in der App, und diese Datei sagt das so.

## Ergebnis (gerechnet am 27.09.2026)

367 Tage mit Finanzierung und Kurs für alle 24 Coins (24.09.2025 – 25.09.2026), jeder
7. Tag als Stichtag: **25 Stichtage je Hälfte** — schon damit ist Punkt 3 (mindestens 30)
nicht erfüllt. Relative Rendite je Fünftel in Prozentpunkten (unterstes → oberstes
Fünftel der Finanzierung):

| | 7 Tage | Spanne oben − unten | 14 Tage | Spanne |
|---|---|---|---|---|
| IS | +1,44 · −0,71 · −0,32 · −0,46 · **+0,05** | −1,39 | +2,34 · −0,67 · −0,37 · −1,25 · −0,08 | −2,42 |
| OOS | +0,55 · −0,08 · −1,09 · +0,30 · **+0,40** | **−0,15** | +0,17 · −0,58 · −0,84 · −0,10 · +1,70 | **+1,53** |

Die Schwelle der App (≥ 50 % aufs Jahr): in IS 9 Fälle, die danach **besser** liefen als
die Klasse (+7,9 Punkte in 7 Tagen), in OOS nur ein Fall.

**Entscheidung: nicht übernehmen.** Das oberste Fünftel lag in keiner Hälfte unter dem
Klassenmittel, die Spanne hielt in OOS nicht, und die Stichprobe ist zu klein. Die
Finanzierung bleibt Kontext in der App — und steht dort **nicht mehr unter „Was dagegen
spricht"**, weil die eigenen Daten das nicht hergeben. In einem Jahr mit doppelt so vielen
Stichtagen kann die Frage neu gestellt werden.
