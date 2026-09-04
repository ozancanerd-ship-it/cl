# Unabhängiger Methoden-Audit — 2026-09-03

Unabhängige Prüfung der Validierungskette (`scripts/setup_research.py`,
`src/trading_agent/research/*`) und der daraus abgeleiteten Edge-Aussagen.
Reproduzierbar mit `python3 scripts/audit_multiple_testing.py <research.json>`.

**Kernergebnis: Kein einziges Setup übersteht eine korrekte Multiple-Testing-Korrektur.
Die bisherige Aussage „S9 hat OOS-Edge" ist statistisch nicht gedeckt. Die Entscheidung,
XAUUSDT in SHADOW/PAPER zu halten, war richtig — aber aus einem stärkeren Grund als bisher
angenommen.**

Wichtig zur Einordnung: Der Fehler liegt **nicht** in der Implementierung. Die Simulation ist
sauber (Point-in-Time-Filterung, Entry auf `h4[i+1].open`, Worst-Case-Fill bei Bar-Overlap,
Purge/Embargo an der IS/OOS-Grenze). Der Fehler liegt in der **statistischen Auswertung** der
Ergebnisse.

---

## F1 — Multiple Testing wird nirgends korrigiert (kritisch)

Lauf `setup_research_v11_regime_soft.json` testet **20 Setups × 4 RR-Stufen = 80
Konfigurationen** in einem einzigen Durchlauf. In `data/repository_real/research/` liegen
**14 solche Läufe** (v2–v11 plus Split-Varianten) — die Gesamtzahl je geprüfter
Konfigurationen liegt im hohen dreistelligen Bereich.

Unter der Nullhypothese „kein Setup hat einen Edge" sind bei 80 Konfigurationen und einem
nominalen 5 %-Niveau **~4 scheinbare Treffer rein durch Zufall zu erwarten.**

Tatsächlich gefunden: **7 Setups mit nominal p < 0.05.** Das liegt kaum über dem Rauschniveau.

Exakte t-Statistik je Setup (std rekonstruiert aus `expectancy_r / sharpe_r`, da
`sharpe_r == mean/pstdev` auf der R-Reihe; `t = sharpe_r · √n`):

| Setup | OOS n | exp R | t | p (1-seitig) | nominal 5 % | Bonferroni |
|---|---:|---:|---:|---:|:--:|:--:|
| S9_htf_confluence | 34 | +0.573 | 2.74 | 0.0030 | ja | **nein** |
| S11_htf_conf_session | 24 | +0.626 | 2.66 | 0.0039 | ja | **nein** |
| S16_dxy_headwind | 31 | +0.583 | 2.67 | 0.0038 | ja | **nein** |
| S1_breakout_retest | 216 | +0.195 | 2.56 | 0.0052 | ja | **nein** |
| S8_session_filter | 28 | +0.470 | 2.24 | 0.0125 | ja | **nein** |
| S4_breakout_retest_trendfilter | 41 | +0.409 | 2.17 | 0.0149 | ja | **nein** |
| S18_regime_gate_er25 | 11 | +0.606 | 1.78 | 0.0376 | ja | **nein** |
| S0_sweep_reversal | 670 | +0.046 | 1.10 | 0.1351 | nein | nein |
| *(übrige 7 Setups)* | | | | > 0.05 | nein | nein |

Bonferroni-Schwelle für 5 % familywise error bei 80 Konfigurationen: **p < 0.00063.**
Der beste Wert (S9, p = 0.0030) liegt **um Faktor 5 darüber.**

**0 von 15 auswertbaren Setups bestehen.**

## F2 — `prob_positive` ist kein Signifikanztest (kritisch)

`monte_carlo()` zieht Bootstrap-Stichproben **aus den beobachteten Trades**. Die
Bootstrap-Verteilung ist damit per Konstruktion um den beobachteten Mittelwert zentriert.
`prob_positive` misst also nicht, ob ein Edge existiert, sondern nur, wie oft eine
Neuziehung derselben Stichprobe positiv ausfällt.

`prob_positive = 0.93` entspricht einem **einseitigen Bootstrap-p-Wert von ≈ 0.07** —
nicht signifikant auf 5 %, und völlig unkorrigiert für die 80 Konfigurationen.

Die Zahl ist kein Robustheitsbeleg. Sie ist eine Umformulierung des Stichprobenmittelwerts.

## F3 — Monte-Carlo läuft auf der Gesamtstichprobe, nicht auf OOS (kritisch)

`_evaluate()` ruft `"monte_carlo_full": _mc(trades)` — `trades` ist der **volle** Satz
(IS + OOS). In `docs/BREAKOUT-REGIME-GATE-2026-09.md` steht dieser Wert in einer Tabelle
direkt neben `OOS n` und `OOS exp` und liest sich dadurch wie eine OOS-Kennzahl.

Er enthält die In-Sample-Daten, auf denen zuvor das RR gewählt wurde.

## F4 — Das Kostenmodell ist um Faktor 4–14 zu niedrig (kritisch)

`cost_r = 0.03` wird als **flache Konstante für alle Symbole** abgezogen
(`realized_r = gross_r - cost_r`). Der Stop liegt bei ~0.4–1.2 × ATR(H4); gerechnet mit
0.8 × ATR und den tatsächlichen ATR-Werten aus dem Repository:

| Symbol | Median ATR(H4) | r_unit ≈ 0.8·ATR | Gebühr RT | Slippage | **Kosten in R** | angenommen |
|---|---:|---:|---:|---:|---:|---:|
| XAUUSDT | 0.708 % | 0.566 % | 0.20 % | 0.03 % | **0.41 R** | 0.03 R |
| BTCUSDT | 1.306 % | 1.045 % | 0.20 % | 0.03 % | **0.22 R** | 0.03 R |
| ETHUSDT | 1.675 % | 1.340 % | 0.20 % | 0.04 % | **0.18 R** | 0.03 R |
| SOLUSDT | 2.625 % | 2.100 % | 0.20 % | 0.06 % | **0.12 R** | 0.03 R |
| EURUSD-YF | 0.209 % | 0.168 % | — | 0.012 % | 0.07 R | 0.03 R |

Bei Binance-Spot ist der Einstieg `h4[i+1].open` ein Market-Order (Taker, 0.1 %), der
Stop-Exit ebenfalls — 0.2 % Round-Trip auf das Nominal. Bei einem Stop von 0.57 % des Preises
sind das **41 % des Risikos**, nicht 3 %.

Ausgerechnet **XAUUSDT — das Kernsymbol des Projekts — ist am stärksten betroffen**, weil Gold
die niedrigste relative Volatilität und damit den engsten Stop hat.

Kosten-Sensitivität über alle Setups:

| Zusatzkosten | nominal p<0.05 | Bonferroni | S9 exp | S9 p | S0 exp | S0 p |
|---:|---:|---:|---:|---:|---:|---:|
| +0.00 R | 7 | 0 | +0.573 | 0.0030 | +0.046 | 0.135 |
| +0.10 R | 4 | 0 | +0.473 | 0.0118 | −0.054 | 0.900 |
| +0.20 R | 3 | 0 | +0.373 | 0.0370 | −0.154 | 0.9999 |
| +0.25 R | **0** | 0 | +0.323 | 0.0610 | −0.204 | 1.000 |
| +0.40 R | 0 | 0 | +0.173 | 0.204 | −0.354 | 1.000 |

**Ab realistischen Kosten für XAUUSDT (+0.38 R Zusatzkosten) ist kein einziges Setup mehr
signifikant — nicht einmal nominal.**

## F5 — Die Basisstrategie ist nach realistischen Kosten signifikant negativ

`S0_sweep_reversal` hat mit **n = 670 OOS-Trades** die einzige statistisch belastbare
Stichprobe des ganzen Panels. Berichtet: +0.046 R (t = 1.10, nicht signifikant positiv).

Mit +0.22 R realistischen Zusatzkosten: **−0.174 R bei t = −4.15.**

Das ist kein „unklares" Ergebnis. Bei n = 670 ist das ein **belastbarer Nachweis, dass der
breite Sweep-Reversal-Ansatz nach Kosten Geld verliert.** Dieselbe Aussage gilt für
`COMBINED_S0_plus_best` (n = 685, t = −3.68).

## F6 — Die Overfitting-Signatur ist im Datensatz sichtbar

Korrelation zwischen `log(OOS-Stichprobengröße)` und `OOS-Expectancy` über die 15
auswertbaren Setups: **r = −0.574**, Steigung −0.112 R.

| Setup | OOS n | exp R |
|---|---:|---:|
| S0 (kein Filter) | 670 | +0.046 |
| S1 (+Breakout-Retest) | 216 | +0.195 |
| S4 (+Trendfilter) | 41 | +0.409 |
| S9 (+HTF-Konfluenz) | 34 | +0.573 |
| S11 (+Session) | 24 | +0.626 |
| S14 (+ER-Gate 0.30) | 4 | +0.720 |

Jeder zusätzliche Filter verkleinert die Stichprobe **und** hebt die scheinbare Expectancy —
monoton, über die gesamte Filterkette. Das ist die Signatur von Selektion auf Rauschen. Ein
Filter, der echte Struktur erfasst, würde die Expectancy heben und dann *stabil bleiben*.

## F7 — `purge_embargo()` ist toter Code

`src/trading_agent/research/validation.py:81` ist definiert, exportiert und getestet, wird
aber **an keiner Stelle im Produktivpfad aufgerufen**. Die IS/OOS-Grenze wird stattdessen in
`_evaluate()` mit einem lokalen `from datetime import timedelta as _td` und einem hartkodierten
12-Tage-Puffer nachgebaut. Funktioniert, ist aber inkonsistent — und der Walk-Forward-Pfad hat
**gar keinen** Embargo.

## F8 — `_wf()` ist kein Walk-Forward

`walk_forward_folds(train_days=200, test_days=90, step_days=90)` liefert Folds mit Train- und
Test-Fenster. `_wf()` benutzt aber ausschließlich `f.test_trades(trades)` — das Train-Fenster
wird nie verwendet, es wird nichts je Fold neu gefittet.

Das ist ein **rollierender 90-Tage-Performance-Bericht auf festen Parametern**, keine
Walk-Forward-Validierung. Als Stabilitätsansicht brauchbar, aber es darf nicht als eine der
Validierungsachsen aus dem Masterplan gezählt werden. Zusätzlich: `test_start == train_end`
ohne Embargo, und Folds mit < 5 Trades werden ohne Kennzahlen ausgegeben.

## F9 — Symbol-Stabilität auf ~5 Trades je Symbol

`symbol_stability()` zählt den Anteil der Symbole mit `total_r > 0`. Bei S9 sind das
34 OOS-Trades auf 7 Symbole ≈ **4,9 Trades je Symbol**. „symstab = 1.00" heißt: sieben Symbole
waren auf je fünf Trades zufällig netto positiv. Das ist eine Münzwurf-Statistik.

`fraction_positive_windows()` hat einen `min_trades`-Parameter — `symbol_stability()` hat
keinen. Genau deshalb fiel der Wert auf dem vollen 12-Symbol-Panel (v9) auf 0.79 / 0.83.

## F10 — `cost_stress` im Monte-Carlo modelliert Kosten falsch

`sample = [r * cost_stress if r < 0 else r for r in sample]` — Verluste werden mit 1.5
multipliziert, **Gewinne bleiben unverändert.** Reale Kosten verkleinern aber auch Gewinne.
Korrekt wäre ein additiver Abzug auf beiden Seiten (`r - c`). Bei RR = 3 ist der aktuelle
Ansatz auf der Gewinnseite systematisch zu optimistisch.

## F11 — Split- und Panel-Wechsel zwischen Läufen

v9 lief auf 12 Symbolen (symstab 0.79 / 0.83), v11 auf 7 Symbolen (symstab 1.00).
Split-Datum wechselte zwischen Läufen (2024-06, 2025-01, 2025-06).

Jeder dieser Wechsel ist ein weiterer Blick auf die Testdaten. `docs/strategy/anti-overfitting.md`
fordert selbst: *„die Test-Split wird genau einmal angefasst, ganz am Ende."* Diese Regel ist
faktisch gebrochen — die OOS-Daten wurden über 14 Läufe hinweg wiederholt ausgewertet.

---

## Was das bedeutet

**Nicht:** „Das Projekt ist gescheitert."
**Sondern:** Drei getrennte Aussagen.

1. **Der breite Ansatz (S0/COMBINED) ist widerlegt.** n = 670, nach realistischen Kosten
   signifikant negativ. Das ist ein echtes, belastbares Ergebnis — kein Nullresultat.
   Diese Richtung sollte nicht weiterverfolgt werden.

2. **S9 ist weder belegt noch widerlegt.** Mit 34 OOS-Trades ist die Stichprobe schlicht zu
   klein, um die Frage zu entscheiden. Der beobachtete Effekt ist mit „echter Edge" *und* mit
   „Zufallstreffer unter 80 Versuchen" vereinbar.

3. **Die Zahl, die entscheidet, ist bekannt:** S9 bräuchte bei realistischen Kosten
   **~124 unabhängige OOS-Trades**, um die Bonferroni-Schwelle zu erreichen. Aktuell: 34.
   Es fehlen also **~90 Trades** — erreichbar, aber nur über echte Forward-Sammlung oder
   deutlich breitere Symbol-Abdeckung.

## Empfohlene Reihenfolge

1. **Kostenmodell reparieren** (F4). Ohne das ist jede weitere Zahl falsch.
   Per-Symbol, Worst-Case, Slippage als Funktion von ATR und Session.
2. **Alle bisherigen Läufe mit korrekten Kosten neu rechnen.** Erwartung: die meisten
   Setups verschwinden. Das ist der gewünschte Effekt.
3. **Hypothesen-Register anlegen** (`config/hypothesis_registry.json`) mit allen bisher
   getesteten Konfigurationen. Ab dann zählt jede neue Variante mit.
4. **S9 als einzige Hypothese vorab registrieren**, Zielgröße 124 OOS-Trades, Schwelle
   p < 0.00063 nach Kosten. **Ab diesem Punkt darf an S9 nichts mehr geändert werden** —
   jede Änderung setzt den Zähler zurück.
5. **Forward-Sammlung starten.** Der Daemon muss dauerhaft laufen. Parallel: Symbol-Panel
   verbreitern (20–30 liquide Symbole), weil die fehlenden ~90 Trades sonst Jahre dauern.
6. `prob_positive`, `monte_carlo_full`, `_wf` und `symbol_stability` korrigieren bzw.
   umbenennen, damit die Berichte nicht mehr mehr behaupten, als sie messen.

## Was ausdrücklich gut ist

Die Simulation selbst hält der Prüfung stand: Point-in-Time-Filterung über `confirmed_at`,
Entry auf der Folgebar-Eröffnung, Worst-Case-Fill bei SL/TP-Overlap im selben Bar, Purge/Embargo
an der IS/OOS-Grenze, `busy_until` gegen überlappende Trades. Kein Lookahead, kein Leakage
gefunden.

**Dass dieser Audit überhaupt möglich war, liegt daran, dass die Läufe vollständig als JSON
archiviert und die negativen Ergebnisse ehrlich dokumentiert wurden.** In den meisten Projekten
dieser Art wäre die Frage gar nicht beantwortbar.
