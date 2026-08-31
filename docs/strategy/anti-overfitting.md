# Anti-Overfitting-Protokoll

**Zweck:** Verbindliche Regeln, damit die Strategie nicht an historische Daten angepasst wird,
sondern einen echten, generalisierbaren Edge nachweist. Gilt für **jeden** Parameter in
`primitives.md`, `regime.md`, `SMC-SWEEP-REV-01.md`, `scoring-rubric.md`, `confidence.md`,
`sizing.md`, `news-rules.md`.

Alle Zahlen `PROPOSED DEFAULT`. Konfig unter `antioverfit.*`.

---

## 1. Grundprinzipien

1. **Definitionen aus Marktmechanik, nicht aus dem Fit.** Ein Parameter wird zuerst aus einer
   Begründung gesetzt (Mikrostruktur, Session-Logik, Volatilitätsskalierung). Erst danach darf er
   innerhalb eines **engen Bereichs** validiert werden — nicht „frei gesucht".
2. **Pre-Registration.** Vor jeder Optimierung: Regelwerk-Docs + alle Parameterwerte als Git-Tag
   einfrieren (`strategy_version`, z. B. `0.1.0`). Jede spätere Änderung ⇒ neue Version + Eintrag
   in der Experiment-Registry + erneute OOS-Prüfung.
3. **Parameter-Budget.** Höchstens `antioverfit.max_free_params` (**PROPOSED DEFAULT `8`**)
   Parameter dürfen im MVP variiert werden. Alle anderen bleiben fix auf `PROPOSED DEFAULT`.
4. **Der unberührte Test-Split wird genau einmal angefasst** — ganz am Ende, als Ja/Nein.
5. **Weniger ist besser.** Neue Faktoren/Regeln müssen einen Mindest-Mehrwert nachweisen
   (§7), sonst werden sie verworfen.

---

## 2. Parameter-Inventar

Jeder Parameter ist genau einer Kategorie zugeordnet:

| Kategorie | Bedeutung | im MVP veränderbar? |
|-----------|-----------|---------------------|
| **THEORY-FIXED** | aus Marktmechanik begründet, nicht zu optimieren | nein (nur mit schriftlicher Begründung + Versionsbump) |
| **TO-VALIDATE** | plausibler Startwert, muss empirisch bestätigt werden | ja, innerhalb `plausible_range`, zählt gegen das Budget |
| **DERIVED** | ergibt sich aus anderen (kein freier Wert) | n/a |

### 2.1 THEORY-FIXED (Auswahl — vollständige Liste in `config/*.example.yaml`, später)
| Parameter | Wert | Begründung |
|-----------|------|-----------|
| `primitives.atr.period` | `14` | Standard; Änderung nur mit Vergleichsstudie |
| `primitives.swing.left/right` | `2/2` | kleinstes sinnvolles Fraktal; strukturell, nicht performance-getrieben |
| `primitives.bos.confirmation` / `choch.confirmation` | `close` | `close` schützt vor Wick-Fakeouts — Designentscheidung, kein Tuning |
| `primitives.fvg`-3-Kerzen-Muster | fix | Definition, nicht Parameter |
| `sizing`-Invarianten (Hebel ändert 1R nicht, kein Martingale) | fix | Risiko-Grundsatz |
| `backtest` SL-vor-TP-Konvention | fix | Konservatismus-Grundsatz |

### 2.2 TO-VALIDATE — die **8** MVP-Kandidaten (PROPOSED DEFAULT)
Diese acht haben den größten Einfluss auf das Setup und werden priorisiert validiert:

| # | Parameter | Default | `plausible_range` | Warum kritisch |
|---|-----------|---------|-------------------|----------------|
| 1 | `primitives.sweep.max_penetration_atr` | `1.00` | `0.5 … 2.0` | trennt Stop-Hunt von Breakout — Kern des Setups |
| 2 | `primitives.sweep.max_reclaim_bars` | `3` | `2 … 6` | „wie schnell" muss die Ablehnung sein |
| 3 | `primitives.displacement.min_atr` | `1.5` | `1.0 … 2.5` | Impuls-Schwelle, regimeabhängig |
| 4 | `primitives.fvg.min_size_atr` | `0.20` | `0.1 … 0.5` | Entry-Häufigkeit vs. Qualität |
| 5 | `setups.SMC-SWEEP-REV-01.entry.max_pd_position` | `0.50` | `0.30 … 0.55` | RR vs. Fill-Rate |
| 6 | `setups.SMC-SWEEP-REV-01.sl.buffer_atr` | `0.50` | `0.25 … 1.0` | Ausstopprate vs. RR |
| 7 | `setups.SMC-SWEEP-REV-01.rr.min_to_tp2` | `2.0` | `1.5 … 3.0` | Trade-Selektivität |
| 8 | `regime.trend.min_slope` | `0.05` | `0.02 … 0.12` | Trend/Range-Grenze |

Alle übrigen `PROPOSED DEFAULT` (Scoring-Gewichte, Confidence-Gewichte, Blackout-Längen,
Session-Regeln, Penalty-Werte …) bleiben im MVP **fix**. Sie werden erst in einer späteren
Runde (nach Paper-Validierung) und einzeln angefasst.

---

## 3. Daten-Splits

| Split | Anteil (PROPOSED DEFAULT) | Nutzung |
|-------|--------------------------|---------|
| **Train** | `antioverfit.split.train_pct` = `50 %` (chronologisch zuerst) | Regelentwicklung, Parameter-Startwerte |
| **Validation** | `25 %` (chronologisch danach) | Parameter-Auswahl innerhalb `plausible_range`, Sensitivität |
| **Test (unberührt)** | `25 %` (chronologisch zuletzt) | **einmalige** finale Prüfung |

### Walk-Forward (zusätzlich, auf Train+Validation)
| Parameter | PROPOSED DEFAULT |
|-----------|------------------|
| `antioverfit.wf.train_months` | `6` |
| `antioverfit.wf.test_months` | `2` |
| `antioverfit.wf.step_months` | `2` |
| `antioverfit.wf.min_folds` | `4` |

### Purge & Embargo (falls K-Fold-CV statt reiner Walk-Forward genutzt wird)
- **Purge:** Trades, deren `[signal_bar, exit]`-Intervall die Fold-Grenze überlappt, werden aus
  dem Training entfernt.
- **Embargo:** nach jedem Test-Fold `antioverfit.embargo_bars` (**PROPOSED DEFAULT = `max_holding_bars`
  des Setups**, hier `96` M5-Bars) Bars aus dem folgenden Training ausschließen.

---

## 4. Sensitivitätsanalyse (Pflicht vor jeder Freigabe)

Für jeden TO-VALIDATE-Parameter:
1. Werte über `plausible_range` in `antioverfit.sensitivity.steps` (**`7`**) Schritten testen.
2. **Kriterium Plateau:** Die Kennzahl (`OOS Expectancy R`) muss über ≥ `antioverfit.sensitivity.plateau_frac`
   (**`0.6`**) des Bereichs **positiv** sein und innerhalb `antioverfit.sensitivity.max_degradation`
   (**`40 %`**) des Bestwerts liegen.
3. **Ein isolierter Peak** (nur bei genau einem Wert gut) ⇒ Parameter gilt als **überangepasst**
   ⇒ konservativsten Wert im Plateau wählen oder Setup-Regel überdenken.

Zusätzlich **2D-Heatmaps** für die stärksten Wechselwirkungen:
`(max_penetration_atr × max_reclaim_bars)`, `(sl.buffer_atr × rr.min_to_tp2)`,
`(displacement.min_atr × fvg.min_size_atr)`.

---

## 4a. Stabilitätsachsen (Pflicht vor jeder Stufen-Freigabe)

Zusätzlich zur Parameter-Sensitivität wird geprüft, ob der Edge **nicht** von einer einzelnen
Dimension getragen wird:

| Achse | Methode | Kriterium |
|-------|---------|-----------|
| **Regime-Stability** | Kennzahlen je Regime-Bucket (`TREND_UP/DOWN`, `RANGE`, Vol `NORMAL/HIGH`) auf OOS | Expectancy in **jedem** Bucket mit ≥ `antioverfit.min_samples` Trades ≥ 0; kein Bucket trägt > `antioverfit.stability.max_bucket_share` (**`0.6`**) des Gesamt-P&L |
| **Time-Stability** | rollierende 3-Monats-Fenster (Schritt 1 Monat) über den OOS-Zeitraum | Anteil profitabler Fenster ≥ `antioverfit.stability.min_positive_windows` (**`0.6`**); kein Fenster mit DD > Monte-Carlo-95-%-Band; Ergebnis nicht von 1–2 Monaten getragen |
| **Symbol-Stability** | Kennzahlen je Instrument (BTC, ETH, …) getrennt | Edge auf ≥ `antioverfit.stability.min_positive_symbols` (**`0.6`**) der Instrumente positiv; entfernt man das beste Instrument, bleibt die Gesamt-Expectancy ≥ `antioverfit.stability.min_expectancy_without_best_r` (**`0.0`**) |

Ein Setup, das nur in **einem** Regime / **einem** Zeitabschnitt / auf **einem** Instrument
funktioniert, gilt als **nicht validiert** und geht zurück in die Spezifikationsphase.

---

## 5. Monte-Carlo- / Robustheits-Suite

| Test | Methode | Report |
|------|---------|--------|
| **Trade-Order-Bootstrap** | ≥ `antioverfit.mc.runs` (**`1000`**) Resamples der Trade-Sequenz | Verteilung Endkapital, Max-DD-Verteilung, 5.-Perzentil-Equity |
| **Trade-Dropout** | zufällig `antioverfit.mc.dropout_pct` (**`10 %`**) der Trades entfernen | Stabilität der Expectancy |
| **Kosten-Stress** | Fees/Slippage/Funding × `antioverfit.mc.cost_mult` (**`1.5`**) | Netto-Expectancy bleibt > 0? |
| **Start-Datum-Jitter** | Backtest-Start um ± `antioverfit.mc.start_jitter_days` (**`30`**) verschieben | Ergebnis-Streuung |
| **Skipped-Signal-Sim** | jedes Signal mit Wahrscheinlichkeit `p` (**`0.1`**) auslassen (simuliert verpasste Fills/Ausfälle) | Robustheit gegen Umsetzungslücken |
| **Ruin-Wahrscheinlichkeit** | aus Bootstrap: P(Drawdown ≥ `risk.drawdown.max_total_pct`) | muss < `antioverfit.mc.max_ruin_prob` (**`5 %`**) |

---

## 6. Multiple-Testing-Disziplin

- **Jeder** Backtest-Lauf wird in der Experiment-Registry protokolliert (Run-Manifest,
  `backtest-labeling.md` §9), auch verworfene.
- Der finale Bericht nennt **`N_configs_tested`** (Anzahl aller Läufe bis zur Freigabe).
- Signifikanzhürde skaliert mit `N`: statt „Expectancy > 0" gilt
  `Expectancy_OOS ≥ antioverfit.mt.base_hurdle_r + antioverfit.mt.per_log_config × log2(N_configs)`
  — **PROPOSED DEFAULT `base_hurdle_r = 0.05`, `per_log_config = 0.01`**.
  (Bewusst grob; ersetzt kein formales Deflated-Sharpe, macht aber die Kosten des Suchens sichtbar.)
- **Kein „best of many"** ohne Offenlegung aller Kandidaten.

---

## 7. Komplexitäts-Ratsche

Eine neue Regel / ein neuer Faktor / ein neuer Setup-Typ wird **nur** aufgenommen, wenn er
**alle** erfüllt:
1. verbessert `OOS Expectancy R` um ≥ `antioverfit.complexity.min_improvement_r` (**`0.05`**)
2. besteht die Sensitivitätsanalyse (§4)
3. reduziert die Trade-Zahl nicht unter `antioverfit.min_samples` je relevantem Bucket
4. hat eine schriftliche mechanistische Begründung (nicht nur „hilft im Backtest")

Sonst: **verwerfen**. Der Default ist Weglassen.

---

## 8. Kill-Kriterien (Setup wird zurückgezogen)

| Kriterium | Schwelle (PROPOSED DEFAULT) |
|-----------|------------------------------|
| OOS-Expectancy negativ über `antioverfit.kill.window` Trades | `50` Trades, `Expectancy_R < 0` |
| IS/OOS-Lücke zu groß | `Expectancy_IS − Expectancy_OOS > antioverfit.kill.max_is_oos_gap_r` = `0.30` |
| Live/Paper-Expectancy unter Backtest | `> antioverfit.kill.max_live_gap_r` = `0.40` schlechter über `50` Trades |
| Score-Bänder nicht monoton (A+ ≤ A ≤ B in realisierter Expectancy) | über `100` Trades je Band |
| Max-DD überschreitet Monte-Carlo-95-%-Band | Realität außerhalb der Simulation ⇒ Modell falsch |

Ein zurückgezogenes Setup geht zurück in die Spezifikations-/Validierungsphase, **nicht** „mit
weniger Risiko weiterlaufen".

---

## 9. Freigabe-Gates je Reifegrad

| Von → nach | Bedingungen |
|------------|-------------|
| **Spec → MVP-Backtest** | alle Docs eingefroren (`0.1.0`), 8 TO-VALIDATE-Parameter + Ranges festgelegt, Datensatz-Version fixiert |
| **MVP-Backtest → Paper** | positiver Edge auf **Validation** UND **unberührtem Test**; Sensitivität = Plateau; Monte-Carlo `ruin_prob < 5 %`; Kostenanteil dokumentiert; ≥ `antioverfit.min_samples` Trades je Hauptregime |
| **Paper → Demo** | ≥ `antioverfit.min_live_samples` (**`100`**) Paper-Trades; Paper-Expectancy innerhalb `kill.max_live_gap_r` des Backtests; Parity-Report (`ARCHITECTURE_GAP_AUDIT.md` G-15) grün |
| **Demo → Live** | separate Entscheidung des Nutzers; nicht Teil dieses Protokolls |

---

## 10. Zu bestätigen / zu validieren

- **`max_free_params = 8`** und die **Auswahl der 8 Parameter** (§2.2): bestätigen oder anpassen.
- **Split 50/25/25 chronologisch**: bestätigen. Alternative 60/20/20.
- **Walk-Forward 6/2/2 Monate**: hängt von der Länge des verfügbaren BTC/ETH-Datensatzes ab —
  final festlegen, wenn der Datenzeitraum steht.
- **Signifikanzhürde §6**: bewusst grob; ggf. durch formales Verfahren ersetzen.
- **Kill-Schwellen §8**: Startwerte.
- **`min_samples = 30`, `min_live_samples = 100`**: Startwerte; für seltene Regime evtl. nicht
  erreichbar ⇒ dann keine regime-spezifische Aussage, nicht „trotzdem entscheiden".
