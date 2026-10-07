# Audit gegen den Final Master Prompt — 07.10.2026

Stand: Repo `ozancanerd-ship-it/cl`, Commit d8e9753. 26 Module, ~51 000 Zeilen Python, 144
Testdateien (alle grün), App `site/template.html` (~8 400 Zeilen). Kein Neubau. Nur
Bestandsaufnahme und eine Reihenfolge.

## 0. Die Zahl, die alles andere bestimmt

Eigene Bilanz (performance.json, 07.10.): **345 entschiedene Signale, im Schnitt −0,36 R je
Trade.**

| Gruppe | n | Ø R | Stop-Quote |
|---|---:|---:|---:|
| alle | 345 | −0,36 | 60 % |
| Note B+ | 108 | −0,26 | 46 % |
| Note B | 118 | −0,22 | 55 % |
| Note A− | 110 | −0,59 | 77 % |
| Coins „Ausbruch aus der Basis" | 29 | +0,05 | — |
| Coins „Rückeroberung nach Liquiditätsgriff" | 33 | +0,04 | — |
| Handy-Alarme seit 21.09. (abgeschlossen) | 9 | ≈ 0 | 4 von 9 |

Der Master Prompt will **mehr Trades, B/B+ handelbar, keine künstliche Mindestqualität**,
und zwar ausdrücklich „solange der Expected Value positiv ist". Genau diese Bedingung ist
heute **nicht erfüllt**. Mehr Trades mit −0,26 R vervielfachen den Verlust. Deshalb gilt:

* **Signale:** so viele wie der Markt hergibt. Alle werden schon heute verfolgt und
  gezählt (Signal ≠ Trade, Abschnitt 101/102).
* **Handy-Alarm bzw. Trade:** nur dort, wo die eigene Bilanz einen positiven
  Erwartungswert zeigt. Das macht das Alarm-Tor, und es hat kein Tageslimit mehr.

Sobald eine Setup-Art belegt im Plus liegt, darf sie so oft handeln, wie sie auftritt.

## 1. Bestand je Bereich

Legende: ✅ vorhanden · 🟡 teilweise/schwach · ❌ fehlt · 🔴 kaputt

| Bereich (Master Prompt) | Stand | Was es gibt / was fehlt |
|---|---|---|
| Architektur Daten → Scan → Signal → Risiko → Depot → Alarm | ✅ | GitHub Actions + Taktgeber (5/10/30 Min), PWA, Wächter |
| Datenqualität / Kill Switch | 🟡 | Frische-Prüfung, toter Scan → keine Stops, `safety/`; kein Spread-/Slippage-Kill |
| Makro (Fed, CPI, NFP, Kalender) | 🟡 | Kalender + Warnungen im Scan; keine Surprise-Engine (Ist vs. Konsens) |
| DXY, Renditen | 🟡 | im Code (Makro-Satz „steigende Renditen"), nicht als Faktor je Wert |
| News | 🟡 | News-Reiter, keine Qualitäts-/Dedup-Wertung |
| Regime | 🟡 | Regime-Modul, BTC-Lage; kein Regime → Strategie-Zuordnung mit Belegen |
| Trend / Pullback („Rücksetzer im Trend") | ✅ | Setup vorhanden; Bilanz −0,09 R |
| Breakout („Ausbruch aus der Basis") | ✅ | bestes Setup bei Coins, ≈ 0 R |
| False Breakout / Liquidity Sweep („Rückeroberung") | ✅ | ≈ 0 R bei Coins, Aktien −1 R (n=6) |
| Momentum / TSMOM | ✅ | Big Plan (Momentum-Regel), Forward-Journal |
| Relative Stärke | ✅ | RS je Klasse; Nachzügler-Verkaufsregel per Studie verworfen |
| Mean Reversion | ❌ | kein Modul |
| Pairs / Stat-Arb | ❌ | nur Ansätze |
| Carry (FX-Zins, Funding-Basis) | ❌ | Funding vorhanden, nicht als Strategie (Studie 09: kein Edge) |
| Value / On-Chain (MVRV …) | ❌ | keine Datenquelle |
| Volatilität (ATR, Kompression) | 🟡 | ATR überall, keine eigene Volatilitäts-Strategie |
| Optionen / IV | ❌ | keine Daten |
| Order Flow (CVD, Orderbuch) | ❌ | nur Ansätze im Code, nicht im Scan |
| Derivate (OI, Funding, Liquidationen) | 🟡 | OI + Funding im Scan für Coins; keine Liquidationsdaten |
| BTC → Altcoin (Beta, Downside-Beta, Dominanz) | ❌ | nur Gleichlauf-Matrix im Depot; keine Beta-Zahl je Coin, keine Dominanz |
| Gold-Engine | 🟡 | PAXGUSD im Scan; Gold-Backtests 08/2026 ohne Edge; kein Day-Trading-Modul, kein COT |
| Aktien (Earnings, Fundamentals) | 🟡 | Quartalszahlen-Termine und Knock-out-Risiko; keine Fundamentaldaten |
| FX | ❌ | im Universum nicht aktiv (kein Broker bei Ozan) |
| Entry-Engine (Ort, Trigger, Invalidierung) | ✅ | Einstiegszone, Bestätigung, Invalidierung, CRV-Tor |
| Stop-Engine | ✅ | Strukturstop + Halte-Stop (Chandelier, Studie 09) |
| Take-Profit / Teilverkauf | ✅ | ⅓-⅓-⅓ an Zielen; **Trailing-Studie läuft** |
| Position Actions (HOLD/REDUCE/CLOSE/ADD) | ✅ | `positionsAktion` mit 7 Regeln, Mindestorder 25 € |
| ADD / Pyramiding | 🟡 | Nachlegen nur bei führendem Wert mit Setup und Stop; kein Pyramiden-Plan |
| Thesis / Thesis-Decay | 🟡 | These wird gespeichert, kein zeitlicher Verfall |
| Opportunity Cost / Rotation | 🟡 | Rotationshinweis nur mit Anlass; kein Ranking „bestes Kapital" |
| Portfolio-Heat / Klumpen / Gleichlauf | ✅ | Gesundheit 0–100, Bündel, schlechter Tag (95 %) |
| Stresstest (BTC −5 %, Nasdaq −3 % …) | ❌ | nur „schlechter Tag" aus Historie |
| Hebel / Liquidation | ✅ | Knock-out-Puffer, Margin-Liquidation, seit heute Depotwert = eigenes Geld |
| Positionsgröße | 🟡 | Risiko-Rechner auf Signalkarte; nicht im Depot-Kontext |
| Backtest / OOS / Walk-Forward / Monte Carlo | ✅ | Replay-Studien, vorab registriert, IS/OOS, Monats-Bootstrap |
| Journal / MFE / MAE | ✅ | Wachliste mit bestes_r/schlechtestes_r, Bilanz je Setup/Note/Klasse/Woche |
| Strategie-Zustände (aktiv/gesperrt) | ✅ | Alarm-Tor sperrt verlierende Setup-Arten automatisch |
| Erklärbarkeit „warum / warum nicht" | ✅ | jede Karte nennt den Grund, das Tor nennt den Sperrgrund |
| ML / Feature-Importance | ❌ | bewusst nicht (zu wenige Fälle) |

**Kaputt (🔴):** zurzeit nichts bekannt. Behoben in den letzten Tagen: ausgefallene
GitHub-Zeitpläne (Taktgeber), Wochenstand-Spam, Margin-Depotwert.

**Schwach:** die Notenskala. A− ist die schlechteste Note (−0,59 R), B die beste. Der Score
trennt nicht. Das ist die größte offene Baustelle.

**Doppelt:** `portfolio/` und `portfolio_intel/` (alt/neu), Python-Depotlogik und dieselbe
Logik in JavaScript (gewollt für die Offline-App, aber zwei Wahrheiten).

## 2. Reihenfolge (Roadmap)

Jeder Schritt einzeln, nach dem Muster **Test → Validierung → Backtest → Review**. Nichts
geht aufs Handy, was in der eigenen Bilanz nicht belegt ist.

1. **Ausstieg (läuft):** Trailing-Stop gegen ⅓-Plan, 21 Monate Replay, Entscheidung nach
   vorab festgelegter Regel.
2. **BTC → Altcoin:** BTC-Beta und Downside-Beta je Coin (aus vorhandenen Tageskerzen),
   in Karte und Depot anzeigen. Danach im Replay prüfen, ob „Alt-Long nur, wenn BTC über
   EMA" die Bilanz verbessert.
3. **Stresstest im Depot:** BTC −5/−10 %, Nasdaq −3/−5 % über Beta und Hebel durchrechnen,
   mit Knock-out- und Liquidations-Treffern.
4. **Notenskala neu eichen:** Score-Bestandteile gegen die 345 Fälle (IS/OOS), damit A
   wirklich besser ist als B.
5. **Gold-Tagesmodul:** London/NY-Session, Vortageshoch/-tief-Sweep, Asia-Range-Ausbruch,
   zunächst als Signale mit Zählung (Ziel bis ~4 je Woche), Alarm erst nach Beleg.
6. **Mean Reversion (Range-Regime + Sweep + Bestätigung)** als neues Setup, zuerst nur
   gezählt.
7. **Depot-Aktionsranking:** eine Liste „bestes Kapital": ADD/REDUCE/HOLD/ROTATE über alle
   Positionen und Chancen zusammen, mit Thesis-Verfall.
8. Später, nur mit Datenquelle: CVD/Orderbuch, Liquidationen, On-Chain, Optionen, COT.

Bleibt: **Paper-Trading, kein Echtgeld.** Das Master-Ziel „mehr Risiko" gilt erst für
Setups mit belegtem positivem Erwartungswert.
