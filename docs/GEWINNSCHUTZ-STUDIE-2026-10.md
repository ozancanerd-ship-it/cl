# Gewinnschutz-Studie (2026-10) — rechtzeitig reagieren statt erst nach dem großen Rückgang?

**Vorab festgelegt am 13.10.2026, bevor ein Ergebnis gerechnet wurde.** Was hier steht, wird nach dem
Lauf nicht geändert; das Ergebnis kommt in einen eigenen Abschnitt unten.

## Anlass

Ozan, 13.10.: Eine Position kann +20 % im Plus liegen und fällt dann auf +15 %, +10 %, +5 %, bevor
überhaupt eine Gewinnmitnahme vorgeschlagen wird. Die bisherige Regel `gewinn_rueckgang` meldet erst,
wenn der Höchstgewinn ≥ 10 % war **und** davon schon ≥ max(8 Punkte, 35 %) weg sind. Der Baustein
`portfolio_intel/gewinnschutz.py` soll schon vorher mehrere Schwächesignale (Struktur, Ablehnung,
Momentum, Volumen, Trend D1/H4, Gewinn-Abgabe) bewerten. Hier wird gemessen, ob das etwas bringt.

## Daten (fest)

* Kraken-Kerzen H4 und D1 (öffentliche Schnittstelle, 720 Kerzen je Reihe ≈ 120 Tage H4,
  2 Jahre D1), geladen am 13.10.2026, 24 Coins: XBT ETH SOL XRP ADA DOGE LINK AVAX DOT LTC BCH
  ATOM NEAR UNI AAVE INJ ARB OP SUI FET XLM TRX FIL ALGO. Nur abgeschlossene Kerzen.
* **Das ist ein kurzer Zeitraum mit im Wesentlichen einem Marktumfeld.** Ergebnisse sind
  Hinweise, kein Beweis. Ein Nachtest auf längerer Historie und im Paper-Betrieb bleibt Pflicht.

## Einstiege (fest, bewusst neutral — es wird NICHT der Einstieg getestet)

Long, wenn der H4-Schluss über dem höchsten Hoch der letzten 30 Kerzen liegt; Einstieg zur
Eröffnung der Folgekerze; pro Coin höchstens eine offene Position, danach 6 Kerzen Pause.
Stop = Einstieg − 2 × ATR(14). `R` = Einstieg − Stop. Short wird nicht getestet.

## Varianten (fest)

Alle Varianten haben dieselben Einstiege, denselben Anfangs-Stop und dieselben Kosten
(0,25 % je Seite vom gehandelten Wert). Entscheidungen fallen am Schluss einer H4-Kerze, die
Ausführung zur Eröffnung der nächsten (kein Blick in die Zukunft). Berührt eine Kerze den Stop,
zählt der Stop (zur Eröffnung, falls sie darunter öffnet).

| Kürzel | Regel |
|---|---|
| **V0 Bisher** | ⅓ an Ziel 1/2/3 (= 1R/2R/3R, nur nach bestätigtem Schluss ≥ Ziel), Stop nach Ziel 1 auf Einstand, nach Ziel 2 auf Ziel 1; zusätzlich `gewinn_rueckgang`: Höchstgewinn ≥ 10 % und Rückgang ≥ max(8 Punkte, 35 % des Hochs) ⇒ Hälfte des Rests verkaufen |
| **V2 Bisher + Gewinnschutz** | wie V0, aber `gewinn_rueckgang` ersetzt durch das Gewinnschutz-Urteil: PROFIT_PROTECTION ⇒ Stop auf den Trailing-Vorschlag, PARTIAL ⇒ vorgeschlagenen Anteil des Rests verkaufen (+ Trailing-Stop), EXIT ⇒ Rest verkaufen. Jedes Urteil über HOLD gilt erst, wenn es nach einer weiteren abgeschlossenen Kerze unverändert ist (wie im Wächter) |
| **V1 Nur Gewinnschutz** | keine festen Ziele; sonst wie V2 (Nebenvergleich) |

Die Urteils-Schwellen stehen im Code (`SCHWELLE_*`, Signal-Gewichte) und werden **nicht** nach
dem Ergebnis geändert. Es wurde vor dem Lauf nichts an einzelne Gewinner angepasst.

## Kennzahlen (fest)

Realisierte R je Trade nach Kosten (Erwartungswert), Profit Factor, Trefferquote, größter
Drawdown der R-Kurve (nach Ausstiegszeit), maximaler Buchgewinn (MFE in R), Anteil des Buchgewinns,
der abgegeben wurde (nur Trades mit MFE ≥ 1R: (MFE − realisiert)/MFE), „zu früh verkauft“ (Anteil
der Teil-/Voll-Verkäufe, nach denen der Kurs bis Tradeende noch ≥ 1R höher stieg), mittlere verpasste
Weiterbewegung in R nach diesen Verkäufen.

## Aufteilung und Entscheidungsregel (fest)

* **In-Sample (IS):** Einstiege in den ersten 60 % der Zeit, **Out-of-Sample (OOS):** letzte 40 %.
* **Walk-Forward:** vier aufeinanderfolgende Zeitfenster gleicher Länge, Unterschied V2 − V0 je Fenster.
* Gepaarter Monats-/Block-Bootstrap (2000 Ziehungen, Blöcke = Einstiegswoche) auf den OOS-Unterschied.
* **V2 gilt als „im Test besser“ nur, wenn:** (1) OOS-Erwartungswert V2 − V0 > 0, (2) das
  5-%-Quantil des Bootstrap-Unterschieds > 0, (3) in mindestens 3 von 4 Fenstern V2 ≥ V0, (4) der
  größte Drawdown in V2 nicht größer ist als in V0. Sonst bleibt der Baustein **unbewiesen**; die
  App kennzeichnet ihn dann weiter als Paper/Shadow-Hinweis. In jedem Fall gibt es keine
  automatischen Echtgeld-Aktionen.

## Ergebnis

*(wird nach dem Lauf eingetragen)*
