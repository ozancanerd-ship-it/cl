# Strategy-Edge — Kernblocker-Untersuchung (2026-08-31)

**Auftrag:** Den wichtigsten verbleibenden Blocker lösen — einen **zweiten robusten Swing-
Setup-Typ** finden, damit die Strategie auf XAUUSDT valide Chancen erkennt. Keine Filter
lockern, um Trade-Zahl zu erzeugen. Echte positive Out-of-Sample-Edge oder ein ehrliches „nein".

**Ergebnis:** Über **6 strukturell verschiedene Setup-Konstruktionen**, 2 Trade-Management-
Modelle, 4 RR-Werte, 7 Instrumente und 3+ Jahre — **keine** zeigt eine robuste, regime-
unabhängige, kosten-überlebende OOS-Edge. Der Befund aus Stufe B bestätigt sich: das Problem
ist **nicht** ein fehlender zweiter Setup-Typ und **nicht** das Regime-Gate. SMC/Price-Action-
Swing-Muster in dieser Form haben auf den verfügbaren Daten **keine nachweisbare prädiktive
Edge**.

---

## Teil 1 — Diagnose: Warum erzeugt das aktuelle Setup (fast) keine Trades?

### 1.1 Der Fluss

`strategy.evaluate` schleust **jeden** Kandidaten durch:

```
No-Trade-Gate → regime_gate(D1, H4) → Setup-FSM (SMC-SWEEP-REV-01)
  → Veto → Location → RR → Confirmation → Confluence → Contradictions → Confidence → Score → Decision
```

### 1.2 Welche Gates blockieren (XAUUSDT, aus dem Gold-Backtest + Code-Analyse)

| Gate | Blockrate | Ursache |
|---|---:|---|
| **`REGIME_UNCLEAR`** | **~83 %** | `regime_gate` verlangt **D1 UND H4** beide klar klassifiziert (`TREND_*`/`RANGE`) **und** konsistent. `directional_regime` klassifiziert D1 nur ~20 % als Trend (Struktur-Zustand **plus** `slope_norm ≥ 0.05` — zwei Bedingungen), H4 ~12 %. RANGE braucht eine saubere Box mit ≥ 2 Berührungen je Seite. Alles andere → `UNCLEAR`. → D1∧H4 klar+einig ≈ **3–6 %**. |
| `REGIME_VOL_EXTREME` | ~15 % | M15-**Kontext**-Vol-Spike ist ein **harter** Block (`context_vol_is_hard_block=True`). |
| `V3` / `V5` Vetos | auf den wenigen ARMED | Location- / RR-Veto. |
| `CONFIDENCE_BELOW_MIN`, `SCORE_BELOW_B` | Rest | dünne Evidenz auf den ~5 % durchgelassenen Kandidaten. |

Zusätzlich: der Voll-Pipeline-Backtest auf XAUUSDT **startet ohne `--require-native-higher off`
gar nicht** — er verlangt 200 D1-Warmup-Bars, XAUUSDT hat insgesamt nur 261 D1-Bars
(Launch 2025-12-11).

### 1.3 Welche Bedingungen sind **zu restriktiv**

- Die **D1∧H4-Regime-Konsens-Pflicht** (beide müssen klar klassifiziert sein **und** übereinstimmen). Der größte Einzelfilter — und laut Stufe B + dieser Untersuchung entfernt er überwiegend *neutrale* Setups, keine schlechten (der Klassifikator trägt keine OOS-Information).
- `directional_regime` verlangt Struktur-Zustand **UND** Slope (Doppelbedingung).
- RANGE-Definition (≥ 2 Touches je Seite, flach, Höhe ≤ 8 ATR) ist eng.
- M15-Kontext-Vol als **harter** EXTREME-Block (M15 ist Kontext, kein HTF).
- Die strikt-monotone-3-Swings-Struktur-Definition (Stufe-B-V0).

### 1.4 Welche Bedingungen sind **tatsächlich sinnvoll** (behalten)

- **Kein Look-ahead / PIT** — nicht verhandelbar.
- Sweep braucht Docht + Reclaim-Close (echte Liquiditäts-Signatur).
- Displacement ≥ ~1.5 ATR (echter Impuls, kein Drift).
- Mindest-RR-Gate (keine 0.5-R-Trades).
- SL jenseits der Struktur (nicht willkürlich).
- Nicht in EXTREME-Vol der **Entry**-TF handeln.
- Eine Position je Instrument, Cooldown nach Verlust.
- Score-/Confidence-Floor (nicht auf dünner Evidenz handeln).

### 1.5 Welche Setup-Typen passen zum Swing-Fokus

Breakout+Retest · Trend-Pullback/Continuation · HTF-Structure-Break+LTF-Confirm · Sweep+Reversal
(bestehend). **Nicht** Scalping-Mean-Reversion, **nicht** M1/M5-Momentum.

---

## Teil 2 — Setup-Forschung (`scripts/setup_research.py`)

### 2.1 Methodik (look-ahead-frei)

- **Timeframes:** H4 = Setup + Entry + Management, D1 = HTF-Regime. Swing-Fokus.
- **Kein Look-ahead:** an H4-Bar `i` sieht ein Detektor nur Swings/Breaks mit `confirmed_at ≤ h4[i].close_time` und den D1-Zustand zum selben Cutoff. Entry = **`h4[i+1].open`**. Exits ausschließlich vorwärts. Worst-Case-Fill (SL vor TP bei Bar-Overlap).
- **Primitive:** die projekteigenen `detect_swings` / `structure_breaks` / `atr_series` (getestet, PIT-sicher). Swings einmal je Symbol über die volle Serie berechnet und per `confirmed_at` PIT-gefiltert — identisch zur inkrementellen Rechnung (Swings werden nie revidiert), ~100× schneller.
- **Panel:** XAUUSDT (Ziel, ~8,5 Monate, komplett OOS) + BTC/ETH/SOL/BNB/XRP/DOGE (2023-01 → 2026-08, ~5 470 H4-Bars je Symbol) für statistische Aussagekraft.
- **RR** wird auf **IS** gewählt (max Expectancy, ≥ 15 IS-Trades), OOS/WF/MC an genau diesem RR berichtet — kein Peeking.
- **Management:** `fixed` (fester RR-Zielpunkt, 1R-Stop) **und** `scaled` (50 % @ +1R, SL→BE, Rest bis Ziel).
- **Kosten:** 0.03 R (fixed-Lauf) und 0.06 R (scaled-Lauf, realistischer: Spread+Fees round-trip).
- **Validierung:** IS/OOS-Split · Walk-Forward (200/90/90 Tage) · Monte-Carlo (2000 Läufe, Cost-Stress 1.5 auf Verlierer, 10 % Dropout) · Symbol-Stabilität · 90-Tage-Zeitfenster.

### 2.2 Getestete Setups

| ID | Typ |
|---|---|
| **S0** | Liquidity Sweep + Reversal (baseline-analog, vereinfacht) |
| **S1** | Breakout + Retest (Konsolidierung → Bruch + Displacement → Retest hält) |
| **S2** | Trend Pullback / Continuation (D1-Trend + H4-Pullback 33–85 % + Rejection) |
| **S3** | HTF Structure Break + LTF Confirmation (jüngster D1-BOS → erster H4-Close jenseits Swing-Extrem) |
| **S4** | S1 **nur in Richtung des D1-Struktur-Trends** (Continuation-Filter) |
| **S5** | S3-Variante: D1-**Struktur-Zustand** statt D1-BOS als Bias-Quelle |

### 2.3 Ergebnisse — Split 2025-06-01

**Fixed-Management, Kosten 0.03 R:**

| Setup | n | ALL exp | IS exp | OOS n | OOS exp | WF +/n | MC prob_positiv | Symbol +Anteil |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| S0 | 1259 | +0.005 | **−0.008** | 99 | +0.162 | 7/11 | **0.00** | 0.71 |
| S1 | 495 | +0.052 | +0.053 | 49 | +0.038 | 7/10 | **0.001** | 0.43 |
| S2 | 193 | +0.058 | +0.091 | 17 | **−0.284** | 5/9 | 0.03 | 0.40 |
| S3 | 151 | +0.135 | +0.146 | 12 | +0.004 | 7/9 | 0.17 | 0.67 |
| S4 | 63 | +0.344 | +0.391 | 8 | +0.017 | 4/5 | 0.51 | 0.25 |
| S5 | 131 | +0.191 | +0.202 | 12 | +0.077 | 7/8 | 0.30 | 0.60 |
| Combined S0+S1 | 1219 | +0.018 | +0.015 | 98 | +0.060 | 9/11 | **0.00** | 0.71 |

**Scaled-Management, Kosten 0.06 R:**

| Setup | n | ALL exp | IS exp | OOS exp | MC prob_positiv |
|---|---:|---:|---:|---:|---:|
| S0 | 1570 | **−0.009** | −0.028 | +0.241 | **0.00** |
| S1 | 532 | +0.078 | +0.086 | +0.014 | **0.006** |
| S2 | 216 | +0.037 | +0.063 | **−0.232** | 0.02 |
| S3 | 159 | +0.073 | +0.078 | +0.020 | 0.10 |
| S4 | 64 | +0.182 | +0.172 | +0.253 | 0.38 |
| S5 | 136 | +0.101 | +0.095 | +0.161 | 0.17 |

### 2.4 Ergebnisse — Split 2024-06-01 (längeres OOS, scaled, Kosten 0.06 R)

| Setup | IS exp | OOS n | OOS exp | WF +/n | MC prob_positiv |
|---|---:|---:|---:|---:|---:|
| S1 | **−0.053** | 282 | +0.194 | 9/10 | **0.006** |
| S3 | +0.150 | 80 | **−0.029** | 6/9 | 0.06 |
| S4 | +0.093 | 30 | +0.315 | 4/6 | 0.40 |
| S5 | **+0.205** | 66 | **−0.009** | 5/8 | 0.17 |
| S0 | −0.038 | 902 | −0.006 | 4/11 | **0.00** |

### 2.5 Interpretation — jedes Setup fällt aus einem von drei Gründen durch

1. **Vorzeichen kippt zwischen IS und OOS** (Overfit): S2, S3, S5 — bei jedem Split. S4 IS +0.39 (Split 2025-06) → OOS +0.017. Klassische „IS-Muster, OOS-Rauschen"-Signatur.
2. **„Positive" Phase = Bull-Market-Beta, nicht Edge**: S1 macht bei Split 2024-06 im OOS (2024–2026 Crypto/Gold-Hausse) **+0.19 R**, aber im IS (2023er-Chop) **−0.05 R**. Alle Setups zeigen deutlich bessere Long- als Short-Expectancy (S0 long +0.11 / short −0.09; S4 long +0.63 / short −0.43). Ein long-lastiges Breakout-System druckt in einer Hausse Geld — das ist keine prädiktive Edge.
3. **Kosten-Fragilität**: die einzigen Setups mit MC-`prob_positive` > 0.3 (S4, S5) haben **8–30 OOS-Trades** — statistisch bedeutungslos. Alles mit realem Sample (S0 1259, S1 495, Combined 1219) hat **`prob_positive` ≈ 0** und einen Monte-Carlo-Median von **−80 bis −400 R** — die Setups leben komplett innerhalb des Transaktionskosten-Bandes. S1s „Edge" von +0.04–0.05 R/Trade liegt unter realistischen Round-Trip-Kosten (XAUUSDT-Spread ≈ 0.04 ATR ≈ 0.03–0.06 R; Crypto-Fees+Slippage ≈ 0.05–0.08 R).

**XAUUSDT isoliert** (das Zielinstrument, komplett OOS): nur S0 (+9.5 R fixed) und S1 (+7.5 R fixed) positiv — beide **front-loaded** (S0: 1. Hälfte +19 R, 2. Hälfte −10 R), n = 25–53, und mit scaled-Management + realistischen Kosten fällt S1 auf **−0.72 R**. Es ist „long Gold in der H1-2026-Hausse", kein wiederholbares Muster.

---

## Teil 3 — Verdikt

**Der Kernblocker ist nicht ein fehlender zweiter Setup-Typ.** Er ist: **SMC/Price-Action-
Swing-Muster, so konstruiert, haben auf den verfügbaren Daten (2023–2026 Crypto, 2025–26 Gold)
keine nachweisbare, regime-unabhängige, kosten-überlebende prädiktive Edge.** Sechs
verschiedene Konstruktionen — inklusive aller vier vom Auftrag genannten Kandidaten (Sweep+
Reversal, Breakout+Retest, Trend-Pullback, HTF-Break+Confirm) plus zwei Filter-Varianten —
bestätigen das.

Einen „Setup B" jetzt zu integrieren würde **Trade-Zahl ohne Edge** hinzufügen — genau das,
was der Auftrag ausschließt. Die im Auftrag genannte Bedingung *„wenn der zweite Setup-Typ eine
echte Edge zeigt → integrieren"* ist **nicht erfüllt**. Also **keine Integration in den Live-
Decision-Pfad.**

### Was das *nicht* heißt

- Die Infrastruktur (Analyse, MTF, Primitive, Backtest, Validierung, Portfolio-Intelligence, Ops) ist solide und getestet (1026 Tests grün). Das ist nicht das Problem.
- Es heißt nicht, dass SMC-Swing-Trading grundsätzlich nicht funktioniert — nur, dass es **auf diesen Daten nicht belegbar ist**.

### Empfehlung — Entscheidung liegt beim Nutzer

| Option | Inhalt |
|---|---|
| **A — Daten** | Mehr Gold-Historie (bezahlte Quelle: 5–10 Jahre XAUUSD, unterschiedliche Regimes) + FX via cTrader/OANDA-Demo (sobald Token da). Erst dann Setup-Forschung wiederholen. Bis dahin: keine belegbare Edge = kein Live-Trading. |
| **B — Hypothese neu fassen** | Wenn auch mit mehr Daten nichts trägt: der reine SMC-Ansatz auf H4/D1-Swing trägt evtl. nicht. Alternativen: längere Haltedauer (D1/W1), andere Kante (Vol-Term-Struktur, Cross-Asset-Flows, saisonale Gold-Muster), oder ein reines Regime-/Allokations-Modell statt diskreter Trades. |
| **C — S1 als Beobachtungs-Kandidat** | S1 (Breakout+Retest) ist die *am wenigsten schlechte* Konstruktion (IS ≈ OOS bei Split 2025-06, regime-gate-unabhängig). **Nicht** als Live-Signal, sondern nur im Paper-/Shadow-Betrieb, explizit als **UNVALIDIERT** markiert, mit fortlaufendem Live-Performance-Tracking — um über die nächsten 6–12 Monate echte Forward-Daten zu sammeln. |

---

## Teil 4 — Unabhängige Fortschritte (edge-unabhängig)

- **`scripts/setup_research.py`** ist ab jetzt die permanente Werkbank für **jede** künftige Setup-Hypothese: Detektor als Funktion `(Ctx, i) -> Signal | None` hinzufügen, `DETECTORS`-Dict ergänzen, laufen lassen → voller Metrik-/Validierungs-Block. Look-ahead-frei by construction.
- Roh-Ergebnisse archiviert unter `data/repository_real/research/setup_research_*.json` (3 Läufe).
- **XAUUSDT Live-Paper**: die volle Kette **DATA → 24/7-ANALYSE → MTF → OPPORTUNITY SCORE** läuft live auf XAUUSDT (Binance USD-M Futures, read-only) über `scripts/run_live_daemon.py --exchange binance --symbols XAUUSDT`. **BUY/SELL-Signale, Entry/SL/TP, Re-Evaluation, Paper-Trade** sind vollständig implementiert (Stufen C/D) und würden auslösen, sobald ein Setup ARMED erreicht — was mit der bestehenden (validierten, gesperrten) Baseline und der aktuellen Datenlage praktisch nicht passiert. XAUUSDT läuft damit im **Beobachtungs-Modus**: Kontext + Ranking live, Signal-Ausgabe wartet auf eine belegte Edge.
- **Nicht gebaut:** kein zweiter Setup-Typ im Live-Decision-Pfad (Auftrags-Bedingung nicht erfüllt), kein Filter gelockert.

---

## Teil 5 — Nachtrag (2026-08-31): Gold-Historie → Breakout+Retest **plausibel**

Nach der Klarstellung des Nutzers (Historical = Validierung, Live = Entscheidung) wurde mehr
Gold-Historie beschafft: **Yahoo `GC=F` H1, 2024-04 → 2026-08** (~28 Monate, indikativ) →
`XAUUSD-YF` (H4 2 965 Bars, D1 734). Deckt die **2024er-Konsolidierung + 2025/26-Trend** ab —
Regime-Vielfalt, die der Binance-XAUUSDT-Reihe (erst ab 2025-12) fehlt. Dukascopy-Spot-XAUUSD
(2023–2026, echt) läuft parallel im Ingest (langsam, 503-Flakiness).

**Neu-Lauf** (`setup_research.py`, scaled-Mgmt, 12-Tage-Purge/Embargo, 8 Symbole, 3 Splits
2025-01/06/10, Kosten 0.06–0.08 R):

| Setup | IS exp | OOS exp (3 Splits) | WF +/n | MC prob_positiv | XAUUSD-YF |
|---|---:|---:|---:|---:|---|
| **S4 Breakout+Retest, D1-Trend-Filter** | +0.18…+0.25 | **+0.40 / +0.44 / +0.47** | 5–6/6–7 | **0.59 / 0.65 / 0.78** | +7.7…+13.3 R / 16–18 Trades / PF 2.4–4.4 |
| **S1 Breakout+Retest (roh)** | −0.01…+0.08 | +0.25 / +0.33 / +0.39 | **12/12** | 0.01 | **+23–24 R / 65 Trades / PF 2.2**, wächst über Zeit (1. Hälfte +6, 2. +18), long +19 / short +5 |
| S3 HTF-Break+Confirm | +0.07…+0.10 | +0.10 / +0.13 / +0.16 | 7/9 | 0.12 | +6.6–7.0 R / 19 Trades / PF ~2.1 |
| S5 D1-Trend+H4-Break | +0.08…+0.13 | +0.14 / +0.19 / +0.22 | 5/8 | 0.18 | +5.1–5.5 R / 19 Trades |
| S0 / S2 | — | negativ / kippt | — | 0.0–0.02 | negativ |

### Bewertung — ehrlich

**Erstes belastbares Signal der gesamten Untersuchung.** Die **Breakout+Retest**-Familie (S1/S4)
zeigt auf der Gold-Historie:
- IS ≈ OOS (kein Overfit-Vorzeichen-Kippen) über **alle drei Splits**
- **S1: 12/12 Walk-Forward-Fenster positiv** (die robusteste Kennzahl)
- **S4: als einziges Setup Monte-Carlo-`prob_positive` > 0.5** unter 1.5×-Kosten-Stress
- Gold-Performance **wächst** über die Zeit (nicht front-loaded), long **und** short auf Gold positiv (S1)
- **Wenige, gute Trades** (S4 ≈ 7/Jahr auf Gold) — genau der geforderte Quality-over-Quantity-Charakter

**Aber NICHT `VALIDATED`:**
1. `XAUUSD-YF` ist **indikativ** (Yahoo-Futures-Close, kein Spot-Bid/Ask) — und **widerspricht** der
   Binance-XAUUSDT-Reihe im 2026er-Overlap (S1: +24 R auf YF, **−0.7 R** auf XAUUSDT).
2. Kleine OOS-Stichproben (S4: 10–26; S3/S5: 7–45).
3. 2024–2026 war trotz Konsolidierungs-Phasen **überwiegend Gold-Hausse** (+120 %) — Long-Bias-Risiko bleibt.
4. Auf dem Crypto-Panel ist S1 nur mittelmäßig (PF 1.17–1.24, MC pp 0.02) — evtl. ein **Gold-Setup**, kein universelles.

### Teil 6 — FX-Gegenprobe (2026-08-31, dieselbe Sitzung)

`scripts/ingest_yahoo.py` erweitert um **EURUSD-YF / GBPUSD-YF / USDJPY-YF** (Yahoo H1,
2023-11 → 2026-08, ~4 187 H4-Bars je Symbol). FX ist **kein Bull-only-Markt** — genau der Test,
ob die Breakout+Retest-Edge über die 2024–26-Gold-Hausse hinaus trägt.

**Neu-Lauf** (Gold + 3× FX + 6× Crypto, Split 2025-04, scaled-Mgmt, Kosten 0.06 R):

| Setup | ALL exp | IS exp | OOS exp (n) | WF +/n | MC prob_positiv | symbol_stability |
|---|---:|---:|---:|---:|---:|---|
| **S4 Breakout+Retest, D1-Trend-Filter** | **+0.26** | +0.08 | **+0.57 (46)** | **8/9** | **0.65** | **fraction_positive 1.0** — jedes Instrument positiv |
| S1 Breakout+Retest (roh) | +0.09 | +0.04 | +0.18 (222) | 12/12 | 0.0005 | 7/10, GBPUSD −4 R |
| S3 / S5 | +0.05 / +0.06 | +0.10 / +0.09 | **−0.09 / −0.00** | 6/11 / 5/11 | 0.02 / 0.04 | negativ auf FX |
| S0 | −0.03 | — | −0.01 | 4/12 | 0.0 | GBPUSD −40 R |

**S4 je Instrument (alle positiv):** XAUUSD-YF +13.7 R (PF 5.3), EURUSD-YF +1.4 R, GBPUSD-YF
+1.7 R (PF 1.3 — **auf einem seitwärts laufenden Markt**), USDJPY-YF +1.0 R. Long **und** Short
tragen bei. `total_r_without_best` (bestes Symbol entfernt) = **+15.8 R**.

**Das bricht das „nur Gold-Hausse-Beta"-Argument.** S4 trägt auf FX-Märkten, die 2024–26
**nicht** trendeten. Kombiniert mit WF 8/9, MC `prob_positive` 0.65 und `fraction_positive` 1.0
ist das die **stärkste Evidenz der gesamten Untersuchung**.

### Teil 7 — Overfitting-Check + Port-Bug (2026-08-31, dieselbe Sitzung)

**Port-Bug gefunden und behoben.** Der zunächst integrierte `detect_breakout_retest` armte auf
**jedem** haltenden Retest eines Ausbruchs statt nur auf dem **ersten** (S4: `not earlier`).
Spätere Retests = schlechtere Entries → verwässerte Edge: der integrierte Detektor feuerte ~2×
so viele Signale mit OOS-Expectancy **−0.04 R** statt der Research-S4-**+0.48 R**. Nach dem Fix
ist der integrierte Detektor **byte-äquivalent** zu Research-S4 (OOS n=35, exp +0.4829 R, PF 2.594).

**Parameter-Sensitivität** (`scripts/setup_sensitivity.py`): die 6 Kernparameter
(consolidation_bars, breakout_displacement_atr, retest_touch_atr, stop_buffer_atr,
retest_window_bars, tp2_r) je ±30 % über Gold + FX, gleicher scaled-Sim, exakte Positions-Sperre:

| | Ergebnis |
|---|---|
| **Alle 13 Störungen OOS-positiv** | ja (`all_perturbations_positive: true`) |
| OOS-Expectancy-Bandbreite | **+0.24 … +0.67 R** |
| OOS-PF-Bandbreite | 1.6 … 3.7 |

Die Edge ist **nicht** an spezifische Parameterwerte gefittet. Das ist der vom Auftrag geforderte
Overfitting-Test — und er hat einen echten Bug aufgedeckt.

### Teil 8 — Echt-Gold-Cross-Check (2026-08-31): **Evidenz gemischt**

Dukascopy-Spot-XAUUSD monatsweise ingestiert (die einzige Methode, die die Umgebung nicht
killt): bisher **2023 Jan–Sep + 2026 Mai–Aug** (Lücke dazwischen, D1 = 335 Bars). Research-Lauf
mit `XAUUSD` (echt) **neben** `XAUUSD-YF` (Yahoo):

| S4 auf … | Trades | Total R | Exp | PF |
|---|---:|---:|---:|---:|
| **XAUUSD-YF** (Yahoo GC=F Futures) | 18 | **+13.7 R** | +0.76 | 5.3 |
| **XAUUSD** (echt, Dukascopy Spot) | **7** | **−2.17 R** | −0.31 | 0.49 |

Die beiden Gold-Reihen **widersprechen sich**. Gründe: Yahoo `GC=F` = **Futures** (Contract-Rolls,
Session-Lücken, andere Microstructure); Dukascopy `XAUUSD` = **Spot**. Die echte Stichprobe (n=7,
davon der IS-Anteil negativ, der OOS-Sliver +1.19 R) ist **zu klein für Widerlegung ODER
Bestätigung**. Panel-weit bleibt S4 OOS-positiv (+0.58 R, `fraction_positive` 1.0), aber die
einzige echte Spot-Gold-Reihe trägt das nicht.

### Entscheidung (datenbasiert, ehrlich)

Der **zweite Setup-Typ = „Breakout + Retest mit HTF-Trend-Filter"** (`SETUP-BREAKOUT-RETEST-01`).
Status: **`IN_VALIDATION`** — die Evidenz ist **gemischt**:

- **Pro:** OOS-positiv über 4 Splits auf Yahoo-Gold-Futures + FX-Proxy + Crypto (WF 8–11/9–12,
  MC `prob_positive` 0.55–0.65), **alle 13 Parameter-Störungen positiv** (nicht overfit).
- **Contra:** die einzige **echte** Spot-Gold-Reihe (klein) ist negativ; die positiven Zahlen
  stammen von **indikativen** Feeds.

Baseline (registry) **abgesenkt**: OOS +0.25 R / PF 1.7 / n 47. Live-Signale = **SHADOW**.
**VALIDATED nur** nach (a) vollständiger Dukascopy-Spot-Gold-Historie **mit positivem OOS** und
(b) ≥ 100 Forward-Trades, die die Baseline halten. Zeigt der Real-Gold-Check bei mehr Daten
weiter negativ → `unvalidated` / `retired`. **Der Setup-Code bleibt gebaut + integriert** und
läuft wie SMC im Beobachtungs-Modus; die Forward-Paper-Trades sind der eigentliche Schiedsrichter.

**Integriert** (damit die Forward-Validierung überhaupt laufen kann):
Strategy-Engine (2. Setup-Typ) · Opportunity-Score · Signal-Engine · Paper-Trading · Versionierung.
XAUUSDT-Signale erscheinen als **SHADOW** (IN_VALIDATION) und werden forward-getrackt. Übergang
auf `VALIDATED` erst nach **(a)** Bestätigung auf echter Dukascopy-Gold-Historie **und (b)**
≥ 100 Forward-/Paper-Trades, die die Baseline halten (`scripts/edge_health_check.py`).

## Änderungen an anderen Dokumenten

- `docs/STAGE-B-STRATEGY-VALIDATION-2026-08.md` — Befund erweitert: die Setup-Ebene (nicht nur der Klassifikator) hat keine OOS-Edge. Dieses Dokument ist die tiefere Untersuchung dazu.
- `docs/DATA-ROLES-LIVE-DECISION.md` — `SETUP-BREAKOUT-RETEST-01` in der `ValidationRegistry` als `IN_VALIDATION`.
