# Liquidität und Alarm-Tor — Nachmessung 01.10.2026

## Frage

Sollen Coins mit wenig Tagesumsatz (unter 1 Mio USD auf Kraken) keinen Einstiegs-Alarm
mehr bekommen? Anlass: DCRUSD (Decred) kam am 01.10. als A− mit Alarm auf die Wachliste,
bei rund 0,2 Mio USD Tagesumsatz. Die Vermutung war: dünne Märkte = schlechtere Trades.

Regel des Projekts: eine Sperre nur, wenn die eigenen Zahlen sie tragen. Sonst höchstens
ein Hinweis.

## Daten

* **Trades:** alle gezählten, tatsächlich eingestiegenen Krypto-Trades der eigenen Bilanz
  (`performance.json`, Stand 01.10.2026 14:25 UTC): 29 Trades, 17.09.–29.09.
* **Umsatz bei Einstieg:** Kraken-Tageskerzen (`/0/public/OHLC`, interval 1440),
  Umsatz = Volumen × VWAP, **Median der 7 vollen Tage vor der Aufnahme** (robust gegen
  einen einzelnen Ausreißertag).
* **Ergebnis:** `r_drittel` — das Ergebnis nach dem Plan aus dem Alarm (Drittel an den
  Zielen, Stop nach Ziel 1 auf Einstand). Gegenprobe mit `r_ganz`.
* **Spread:** Kraken-Ticker (bester Bid/Ask), Momentaufnahme 01.10. ~14:50 UTC, umgerechnet
  in R über den Stop-Abstand des jeweiligen Trades.

## Ergebnis

| Tagesumsatz bei Aufnahme | Trades | Summe (Drittel) | Ø je Trade | Profitfaktor | Stops |
|---|---:|---:|---:|---:|---:|
| unter 0,5 Mio | 6 | +3,02 R | +0,50 R | 2,83 | 0 |
| 0,5 Mio und mehr | 23 | +1,64 R | +0,07 R | 1,18 | 10 |
| **unter 1 Mio** | **13** | **+7,15 R** | **+0,55 R** | **2,96** | **2** |
| **1 Mio und mehr** | **16** | **−2,49 R** | **−0,16 R** | **0,64** | **8** |
| unter 2 Mio | 18 | +7,48 R | +0,42 R | 2,61 | 4 |
| 2 Mio und mehr | 11 | −2,82 R | −0,26 R | 0,53 | 6 |

Mit `r_ganz` (alles an einem Stück) dasselbe Bild: unter 1 Mio +11,36 R aus 13,
darüber −5,12 R aus 16.

Unterschied der Mittelwerte (unter/über 1 Mio): +0,71 R je Trade, **Permutationstest
p = 0,09** (20 000 Durchläufe) — **nicht belastbar**. Die Richtung ist aber eindeutig
*nicht* die befürchtete.

Spread-Kosten: 0,01–0,04 R je Trade, auch bei den dünnsten Coins (ORCA, CSPR, DEEP mit
0,05 Mio). Abzüglich Spread bleibt unter 1 Mio +6,89 R.

## Entscheidung

1. **Keine Sperre** für dünne Coins. Die eigene Bilanz spricht nicht dagegen — unter
   1 Mio lagen alle 4 Trades, die bis Ziel 3 liefen (ETC, GRT, AERO, NIGHT).
2. **Auch keine Bevorzugung.** p = 0,09 bei 29 Trades aus zwei Wochen ist kein Beleg.
   Erneut prüfen ab ~60 gezählten Krypto-Trades.
3. **Hinweis im Kaufalarm** (Krypto, unter 1 Mio USD): „Dünner Markt: nur X Mio USD
   Umsatz am Tag. Ein- und Ausstieg mit Limit-Order, nicht zum Marktpreis." Der
   gemessene Spread ist klein; was in einem dünnen Buch Geld kostet, ist eine
   Market-Order, die durch mehrere Preisstufen läuft — und die zeigt kein Ticker.

Umgesetzt in `scanner/watchlist.py` (`DUENN_UMSATZ`, `Wache.umsatz_24h`,
`einstieg_text`), Test `tests/unit/test_alarm_texte.py`.

## Mitgefundene Fehler (gleich behoben)

* Die Analyse schrieb bei **jedem** Coin „… Mio USDT Tagesumsatz — groß genug, um wieder
  herauszukommen", auch bei 0,1 Mio („0 Mio USDT Tagesumsatz — groß genug"), zwei Zeilen
  unter der Warnung „dünn für schnelle Ausstiege". Jetzt nur noch ab 5 Mio.
* Ziel-Alarme für **Shorts** sagten „Ein Drittel verkaufen" (Issue #187, MDLZ). Jetzt
  „Ein Drittel schließen (beim Short-Schein: ein Drittel des Scheins verkaufen)" bzw. beim
  Krypto-Terminkontrakt „zurückkaufen" — in Alarm, Handelsplan und App.
* Preise und R in den Alarmen mit deutschem Komma (vorher „57.78", „+1.57R").
