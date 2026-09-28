# Halte-Stop-Studie — Vorab-Registrierung (27.09.2026)

## Anlass

Ozan am 27.09.: „Die Stops auf meinem Portfolio 24/7 analysieren und immer neu setzen und
mir Bescheid sagen." Bis dahin bekam eine Position nur dann einen Stop, wenn der Scanner
für genau diesen Wert gerade ein Long-Setup mit Invalidierung hatte. Bei einer Position,
in der man längst drin ist, ist das selten — die meisten Depotwerte standen ohne Stop da.

Gebraucht wird ein **Halte-Stop**: eine Marke, die es für jeden Wert mit Tageskerzen gibt,
die nur nachgezogen wird und die unabhängig davon ist, ob heute ein Einstiegs-Setup vorliegt.

## Kandidaten (vor dem Rechnen festgelegt)

Chandelier-Stop (LeBeau): höchstes Hoch der letzten 22 Tageskerzen minus k × ATR(14,
Wilder). Nachgezogen wird nur nach oben. Ausstieg, wenn das Tagestief den Stop berührt —
zum Stop, bei einer Lücke darunter zum Eröffnungskurs.

    k ∈ {2,0 · 2,5 · 3,0 · 3,5 · 4,0 · 5,0}   und zum Vergleich: kein Stop

## Messung

* Coins: 24 liquide Coins, Tageskerzen (Binance) 11/2023 – 09/2026, Hoch/Tief/Schluss.
* Aktien: 103 Aktien, nur Tagesschlusskurse 2024 – 09/2026. Hier wird die ATR aus den
  Schlusskursen gebildet und der Ausstieg am Schlusskurs geprüft (gröber, aber ohne
  Hoch/Tief nicht anders möglich).
* Jeden 5. Tag gilt als „Position eröffnet"; gehalten wird bis zum Stop, höchstens
  90 Tage. Nach dem Ausstieg liegt das Geld bis Tag 90 in bar (0 %).
* Kosten beim Ausstieg über den Stop: 0,2 % (Coins) bzw. 0,1 % (Aktien).
* Zeitlich geteilt: erste Hälfte der Eröffnungstage (IS) und zweite Hälfte (OOS).

Kennzahlen je Regel: mittlere Rendite nach 90 Tagen, 5-%-Quantil (die schlechten Fälle),
Anteil ausgestoppt, und „zu früh raus" (ausgestoppt und 20 Tage später über 10 % höher).

## Entscheidungsregel (vorab)

1. Zulässig ist ein k, dessen mittlere Rendite in **beiden** Hälften höchstens
   1,0 Prozentpunkte unter „kein Stop" liegt.
2. Unter den zulässigen gewinnt das k mit dem besten 5-%-Quantil (im Mittel beider Hälften).
3. Liegen zwei innerhalb von 0,5 Prozentpunkten, gewinnt das größere k (weniger zu frühe
   Ausstiege).
4. Ist kein k zulässig, wird der Halte-Stop trotzdem angeboten — Ozan will ihn —, aber mit
   dem größten k und dem ausdrücklichen Hinweis, dass er Rendite kostet.

Für Coins und Aktien wird getrennt entschieden.

## Ergebnis (gerechnet am 27.09.2026, Skript `halte_stop.py` in der Studienumgebung)

Rendite nach 90 Tagen in Prozent, Mittel und 5-%-Quantil, dazu Anteil ausgestoppt:

**Coins** (24 Werte, je rund 2 200 Fälle pro Hälfte)

| Regel | IS Mittel | IS q05 | IS ausgestoppt | OOS Mittel | OOS q05 | OOS ausgestoppt |
|---|---|---|---|---|---|---|
| kein Stop | +19,7 | −54,1 | 0 % | −7,8 | −54,2 | 0 % |
| k 2,0 | −0,5 | −9,1 | 100 % | −0,6 | −7,1 | 100 % |
| k 3,0 | −0,6 | −15,4 | 100 % | −1,6 | −12,5 | 100 % |
| k 4,0 | +1,0 | −21,2 | 99 % | −3,3 | −17,9 | 100 % |
| k 5,0 | +3,0 | −28,1 | 95 % | −4,7 | −22,6 | 97 % |

**Aktien** (103 Werte, nur Schlusskurse, je rund 5 900 Fälle pro Hälfte)

| Regel | IS Mittel | IS q05 | OOS Mittel | OOS q05 |
|---|---|---|---|---|
| kein Stop | +5,4 | −23,2 | +10,1 | −24,5 |
| k 2,0 | −0,1 | −4,6 | +0,5 | −4,7 |
| k 5,0 | +0,1 | −8,1 | +1,8 | −8,3 |

**Entscheidung nach Regel 4:** kein k erfüllt die Hürde (in jeder Hälfte höchstens einen
Prozentpunkt schlechter als ohne Stop) — weder bei Coins noch bei Aktien. Der Halte-Stop
wird deshalb mit **k = 5,0** gesetzt, und die App sagt dazu ausdrücklich, dass er Rendite
kostet.

Was das heißt, ohne Beschönigung: Ein nachgezogener Stop ist eine **Versicherung gegen den
Absturz**. Er hat das schlechteste Zwanzigstel bei Coins von −54 % auf −28 %/−23 %
gedrückt und im Abwärtsjahr der Altcoins (OOS) sogar die mittlere Rendite verbessert
(−4,7 % statt −7,8 %). In Aufwärtsphasen kostet er viel, weil man nach dem Ausstieg den
Rest des Anstiegs verpasst — bei Aktien, die 2024–2026 fast durchgehend stiegen, war
Halten ohne Stop klar besser.

### Nachtrag (nicht vorab registriert, nur beschreibend)

Mit einem einfachen Wiedereinstieg (neuer 22-Tage-Schlusshochpunkt) nach dem Ausstieg:
Coins k 5 IS +9,8 % / OOS −4,9 %, Aktien k 5 IS +1,4 % / OOS +4,2 %. Der Wiedereinstieg
holt einen Teil der verpassten Rendite zurück, aber nicht alles. Daraus folgt nur eines,
und das steht so in der App: **nach einem Stop gehört der Wert auf die
Wiedereinstiegs-Liste** — der Stop ist kein Urteil über den Wert, sondern über den Moment.
