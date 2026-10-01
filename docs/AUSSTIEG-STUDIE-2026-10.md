# Ausstiegs-Studie 2026-10 — raus, wenn die Analyse dreht, oder halten?

**Vorab festgelegt am 01.10.2026, bevor gerechnet wurde.** Was hier steht, wird nach dem
Lauf nicht mehr geändert; das Ergebnis kommt unten in einen eigenen Abschnitt.

## Anlass

Ozan, 01.10.: die Verkaufssignale sollen besser werden. Neben Stop und Ziel gibt es einen
dritten Verkaufsalarm für laufende Trades, **„AUSSTEIGEN — Analyse gedreht"**: zeigt
der Scanner während des Trades in die Gegenrichtung, gilt das als Strukturbruch, und der
Plan sagt „raus, auch wenn der Stop noch nicht erreicht ist". Im 19-Monats-Nachspiel der
Signal-Studie endeten so 162 von knapp 700 eingegangenen Trades, solange der Plan noch voll
im Markt war — so viele wie keine andere Ausstiegsart außer dem Stop. Gemessen wurde diese
Regel nie.

## Frage

Ist der vorzeitige Ausstieg bei gedrehter Analyse besser als einfach weiter dem Plan zu
folgen (Stop, Ziele, Schutz-Stop nach Ziel 1 und 2)?

## Daten (fest)

* Die Wachen aus Phase B der Signal-Studie (`wachen.json`, 24 Coins, 03/2025–09/2026),
  dieselbe Teilung: IS Aufnahme bis 31.12.2025, OOS ab 01.01.2026.
* Fälle: eingegangen (Einstiegskurs vorhanden), beendet als `invalidiert` mit
  Ausstiegskurs, und **zum Zeitpunkt des Drehens noch nicht laut Plan draußen**
  (kein Schutz-Stop vorher).
* **Raus** (wie live): Ergebnis nach Plan wie in Phase C (`_r_trade`, Regel „plan"),
  Rest zum Ausstiegskurs.
* **Halten**: ab dem Zeitpunkt des Drehens läuft der Plan mit Stundenkerzen (H1) weiter —
  bereits erreichte Ziele bleiben, der Rest geht an den noch offenen Zielen in Dritteln
  raus; Stop bzw. Schutz-Stop (nach Ziel 1 Einstand, nach Ziel 2 Ziel 1). Berührt eine
  Kerze Stop und Ziel, zählt der Stop (pessimistisch). Öffnet eine Kerze schon jenseits
  des Stops: zum Eröffnungskurs. Spätestens 30 Tage nach dem Drehen: Rest zum Schlusskurs.
* Kosten wie in Phase C, in beiden Varianten gleich.

## Entscheidungsregel (fest)

Verglichen wird je Fall der Unterschied **Halten − Raus** in R.

* **Die Regel ändert sich** (kein Ausstiegs-Alarm mehr bei gedrehter Analyse, nur noch
  Stop, Ziele und Schutz-Stop), wenn der mittlere Unterschied in **IS und OOS über null**
  liegt, mindestens 30 Fälle in OOS, und im Monats-Bootstrap auf OOS (ganze Monate ziehen,
  5 000 Ziehungen, Seed 7) das 5-%-Quantil über null liegt.
* **Die Regel ist bestätigt**, wenn der mittlere Unterschied in IS und OOS unter null liegt
  und das 95-%-Quantil auf OOS unter null.
* Sonst **bleibt sie, wie sie ist** — dann ist der Unterschied nicht belegbar, und eine
  Änderung wäre Raten.

---

## Ergebnis (01.10.2026, erster und einziger Lauf)

`python3 scripts/ausstieg_studie.py --lauf … --npz …` — Regel unverändert wie oben.
162 Fälle (eingegangen, Analyse gedreht, Plan noch voll im Markt).

| Hälfte | Fälle | raus (wie live) | halten (Plan weiter) | Unterschied | halten besser in |
|---|---:|---:|---:|---:|---:|
| IS | 86 | −0,243 R | −0,206 R | +0,038 R | 33 % der Fälle |
| OOS | 76 | −0,214 R | −0,216 R | −0,001 R | 34 % der Fälle |

Monats-Bootstrap OOS, 90-%-Spanne des Unterschieds: −0,20 … +0,19 R.

**Urteil nach der Regel: bleibt, wie es ist.** Ob man bei gedrehter Analyse aussteigt oder
dem Plan weiter folgt, macht im Mittel keinen messbaren Unterschied — in beiden Hälften
liegt er bei praktisch null. Der Ausstiegs-Alarm bleibt; er kostet nichts und verkürzt die
Zeit, in der Geld in einem Trade steckt, dessen Begründung nicht mehr gilt.

Auffällig, aber nur zur Einordnung (nachträglich angeschaut, entscheidet nichts): bei
Longs wäre Halten im Mittel besser gewesen (+0,19 R, 65 Fälle), bei Shorts der Ausstieg
(−0,09 R, 97 Fälle). Wer daraus eine Regel machen will, muss sie vorab registrieren und
vorwärts prüfen.
