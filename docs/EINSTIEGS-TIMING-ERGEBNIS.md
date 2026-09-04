# Bringt Warten auf einen besseren Einstieg etwas?

**Ergebnis: Nein. Alle sechs Warte-Varianten schneiden schlechter ab als sofort kaufen.**

Stand 2026-09-04 · 400 Einstiegssignale · 13 Instrumente · 4 Anlageklassen
Rohdaten: `docs/EINSTIEGS-TIMING-ERGEBNIS.json` · Code: `scripts/entry_timing_research.py`

## Die Frage

Ozan: *„man kann nicht einfach sofort rein, man braucht den richtigen Zeitpunkt."*

Das ist eine überprüfbare Behauptung. Also geprüft, statt beantwortet.

## Der Aufbau

Jedes Mal, wenn die eingefrorene TSMOM-Regel ein Instrument von „kein Gewicht" auf
„Gewicht > 0" setzt, ist das ein Einstiegssignal. Der **Ausstieg ist bei allen
Varianten identisch** (Signal dreht). Verglichen wird also ausschließlich, *wann*
gekauft wird — sonst nichts.

Die sechs Varianten wurden **vor** dem ersten Lauf festgelegt:

| Variante | Regel |
|---|---|
| `sofort` | am nächsten Schlusskurs (Grundlinie) |
| `limit_2` | Kauflimit 2 % unter Signalkurs, 10 Tage gültig, sonst Markt |
| `limit_5` | dasselbe mit 5 % |
| `tranchen_3` | drei gleiche Teile an Tag 1, 6, 11 |
| `dip_5d` | warten bis ein Schluss unter dem 5-Tage-Mittel liegt, max. 10 Tage |
| `warte_5t` | einfach 5 Tage warten — **Kontrolle** |

Die Kontrollvariante ist der wichtigste Teil des Aufbaus: Wenn stumpfes Warten
genauso abschneidet wie die klugen Regeln, misst man keinen Einstiegsvorteil.

## Das Ergebnis

| Variante | n | Ø Rendite | Median | Trefferquote | Ø Verzug |
|---|---:|---:|---:|---:|---:|
| **sofort** | 400 | **+12,66 %** | 0,00 % | **21 %** | 1,0 d |
| limit_2 | 400 | +11,38 % | −0,53 % | 41 % | 5,9 d |
| limit_5 | 400 | +11,45 % | −0,14 % | 47 % | 7,9 d |
| tranchen_3 | 400 | +11,89 % | −0,44 % | 42 % | 10,9 d |
| dip_5d | 400 | +11,86 % | 0,00 % | 28 % | 3,8 d |
| warte_5t | 400 | +11,66 % | −0,37 % | 40 % | 5,0 d |

Gepaarter Vergleich gegen die Grundlinie (dieselben Trades, anderer Einstieg):

| Variante | Δ je Trade | t | besser auf … Symbolen |
|---|---:|---:|---|
| limit_2 | −1,28 pp | −2,19 | 3 von 13 |
| limit_5 | −1,21 pp | −1,93 | 4 von 13 |
| tranchen_3 | −0,76 pp | −1,47 | 4 von 13 |
| dip_5d | −0,80 pp | −1,38 | 5 von 13 |
| warte_5t | −0,99 pp | −1,10 | 5 von 13 |

**Alle sechs negativ. Auf 8 bis 10 von 13 Instrumenten schlechter.**

## Warum — die Trefferquote verrät den Mechanismus

Das Auffälligste steht in der Trefferquoten-Spalte, und es sieht zuerst nach dem
Gegenteil aus:

- `sofort`: **21 %** Treffer, aber **+12,66 %** im Schnitt
- `limit_5`: **47 %** Treffer, aber nur **+11,45 %**

Warten **verdoppelt die Trefferquote** und **senkt trotzdem die Rendite.** Das ist kein
Widerspruch, sondern der ganze Punkt:

1. **Ein Limit wird gefüllt, wenn der Kurs fällt.** Und ein Kurs, der nach einem
   Trendsignal erst einmal fällt, gehört überdurchschnittlich oft zu einem Trend, der
   gar nicht zustande kommt. Man wird also bevorzugt in die schlechten Trades
   hineingelassen. (Von 400 Signalen wurde `limit_5` in **271 Fällen nie gefüllt** —
   und das sind genau die davonlaufenden.)
2. **Die guten Trades laufen sofort weg.** Wer auf einen Rücksetzer wartet, verpasst
   sie ganz oder steigt teurer ein.
3. Das passt exakt zum bereits bekannten Befund: 59 von 398 Positionen tragen
   praktisch den gesamten Gewinn. Jede Regel, die die Wahrscheinlichkeit senkt, in
   diesen 59 dabei zu sein, kostet Geld — auch wenn sie die Statistik „schöner" macht.

Deshalb die höhere Trefferquote: man sammelt mehr kleine Gewinne ein und verliert die
wenigen großen. Genau der Tausch, den man bei einer Trendfolge nicht machen darf.

## Was das NICHT heißt

- **Nicht**, dass Timing generell unmöglich ist. Geprüft wurden sechs Regeln auf
  Tagesdaten für dieses eine Setup. Andere Ansätze (Intraday, Orderbuch,
  Volatilitätsregime) sind hier nicht geprüft worden.
- **Nicht**, dass der Unterschied statistisch gesichert ist. Der stärkste Effekt
  (`limit_2`, t = −2,19) ist nominal signifikant, aber **nicht** nach Korrektur für
  sechs Versuche (Bonferroni bräuchte |t| > 2,64). Belastbar ist die *Richtung*: alle
  sechs Varianten negativ, auf der Mehrzahl der Symbole schlechter, und die
  Kontrollvariante schneidet so ab wie die klugen Regeln.

## Konsequenz für das System

Es wird **nicht** auf einen Einstiegszeitpunkt gewartet und **kein** Datum genannt, an
dem ein Einstieg „perfekt" wäre. Das wäre nach dieser Prüfung nicht nur unbelegt,
sondern messbar teurer.

Was stattdessen im System bleibt:

- **Die Position ist klein genug, dass der Einstiegskurs nicht entscheidet.** Die
  Größe kommt aus der Volatilität, nicht aus einer Kursmeinung.
- **Die App zeigt, ab welchem Kurs die Regel kippt.** Das ist die einzige
  Kursaussage, die überprüfbar ist — und sie sagt, wann man *raus* muss, nicht wann
  man *rein* sollte.
- **Der Ausstieg trägt das Ergebnis, nicht der Einstieg.** Ein Prozentpunkt besserer
  Einstieg verschwindet neben einem Gewinner, der 300 % läuft.

## Registriert

Sechs Konfigurationen, aufgenommen in `config/hypothesis_registry.json` als
`HYP-ENTRY-TIMING-01`. Sie zählen zur Multiple-Testing-Last für alle künftigen Tests.
