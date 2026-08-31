# Stufe B — Strategie-Validierung (2026-08-31)

**Frage (Masterplan):** Findet die Strategie tatsächlich robuste, hochwertige Trades — oder
entstehen 0 Trades, weil eine Regel zu streng ist?

**Antwort:** Auf den verfügbaren Daten (2023–2026, 6 Crypto + 9 Monate Gold) hat die
Struktur-/Regime-Klassifikation, die den Trade-Fluss steuert, **keinen robusten
Out-of-Sample-Vorteil**. Das `regime_unclear`-Blocken ist ein **Symptom**, nicht die Ursache.
Regeln zu lockern würde Trades **ohne Edge** erzeugen.

---

## 1. `regime_unclear` quantifiziert

`derive_structure_state` (Baseline V0: `detect_swings` left/right 2, min_leg 0.5·ATR, dann
**strikt monoton steigende letzte 3 Swing-Highs UND letzte 3 Swing-Lows** für TREND_UP) auf
XAUUSDT-Daten:

| TF | `unclear` | `trend_up` | `trend_down` | Periode-Move |
|---|---:|---:|---:|---|
| D1 | 71,8 % | 19,9 % | 8,3 % | +4,0 % |
| H4 | 79,1 % | 12,1 % | 8,8 % | +5,6 % |
| M15 | 80,4 % | 9,9 % | 9,7 % | +6,1 % |

Der D1∧H4-Konsens-Gate braucht **beide** TFs `trend_*` in **derselben Richtung** →
P ≈ 0,28 × 0,21 ≈ **6 %** Best Case. Daher ~100 % NO_TRADE.

**Warum so hoch:** „strikt monoton über 3 Swings **beider** Typen" ist für einen realen Trend
mit Pullbacks (ein tieferes Zwischenhoch bei jeder Korrektur) sehr demanding.

## 2. Isolierte Klassifikator-Kalibrierung (`scripts/structure_calibration.py`)

**Methodik:** IS → OOS → Walk-Forward (4 Folds). Truth-Proxy = realisierte Vorwärts-Richtung
über 15 D1-Bars (ATR-normiert + Pfad-Geradlinigkeit). R-Probe = standardisierter Entry in
Klassifikations-Richtung, worst-case Fill — **reines Signalqualitäts-Maß, kein Strategie-Ergebnis.**

**7 Symbole** (BTC/ETH/SOL/BNB/XRP/DOGE + XAUUSDT), **2023-06 → 2026-08**, Split 2024-12-01,
**n = 4 659 Samples**.

**Getestete Varianten** (nicht nur Param-Sweep — auch **strukturell andere Definitionen**):
`V0` Baseline · `V1` min_swings=1 · `V2` min_swings=3 · `V3–V6` Fraktal-/Leg-Sweeps ·
**`V7` Higher-Lows-only** (Uptrend = nur HL, Highs ignoriert) · **`V8` 1 Verstoß je Serie
erlaubt** · **`V9` BOS-Anker** (Trend = letzter gerichteter BOS in den letzten 20 Bars).

### Ergebnis

| Variante | Cov IS | ExpR IS | PF IS | **Cov OOS** | **ExpR OOS** | **PF OOS** |
|---|---:|---:|---:|---:|---:|---:|
| **V0 Baseline** | 23,0 % | **+0,355** | 1,77 | 17,2 % | **−0,009** | 0,99 |
| V1 min_swings=1 | 65,3 % | +0,183 | 1,34 | 60,5 % | −0,006 | 0,99 |
| V5 leg 1·ATR | 23,9 % | +0,301 | 1,63 | 16,1 % | +0,018 | 1,03 |
| **V7 HL-only** | 52,9 % | +0,227 | 1,45 | 50,2 % | **+0,012** | 1,02 |
| **V8 1-Verstoß** | 67,4 % | +0,184 | 1,35 | 65,9 % | **+0,026** | 1,05 |
| **V9 BOS-Anker** | 24,4 % | +0,179 | 1,37 | 24,7 % | **+0,102** | 1,21 |

**IS sieht alles gut aus (V0 +0,36R). OOS bricht ALLES auf ~null zusammen** (±0,03R,
PF ≈ 1,0) — die klassische „IS-Muster, OOS-Rauschen"-Signatur.

### Walk-Forward (Vorzeichen kippt jeden Fold)

| Test-Fenster | V0 ExpR | V7 ExpR | V8 ExpR | V9 ExpR |
|---|---:|---:|---:|---:|
| 2024-02 → 2024-10 | +0,16 | +0,04 | −0,09 | −0,04 |
| 2024-10 → 2025-05 | +0,43 | +0,19 | +0,29 | +0,39 |
| 2025-05 → 2026-01 | +0,15 | −0,19 | −0,42 | −0,01 |
| 2026-01 → 2026-08 | **−0,46** | +0,19 | +0,18 | −0,28 |

**Keine Variante ist über alle 4 Folds konsistent positiv.** Regime-abhängiges Rauschen.

## 3. Schlussfolgerungen

1. **Die Baseline bleibt gesperrt** — bestätigt zum dritten Mal (Audit 14, 17, jetzt 23).
   Lockern (V1/V7/V8: 3–4× mehr Coverage) bringt **null OOS-Edge** → mehr Trades ohne Vorteil
   = schlechter (Kosten, Drawdown).
2. **`regime_unclear` ist nicht die Ursache** — es ist ein Symptom. Der SMC-Sweep-Reversal-
   Kontext, so wie er über dieses Regime-Modell gesteuert wird, hat auf den getesteten Märkten
   (2023–2026) **keine nachweisbare OOS-Edge**.
3. **Einziges schwaches Signal:** `V9 BOS-Anker` — OOS +0,102R / PF 1,21 bei 25 % Coverage,
   n=374. Nicht als Gate-Änderung, sondern als **eigenes, isoliertes Kalibrier-Item** (BOS
   statt reiner Swing-Monotonie als Trend-Definition).

## 4. Neue Kennzahlen (`research/metrics.py`)

`Metrics` um **`sharpe_r` / `sortino_r` / `calmar_r`** erweitert (auf der R-Sequenz je Trade,
nicht zeit-annualisiert; Sortino mit Downside-Deviation `sqrt(mean(min(r,0)²))`).
`scripts/run_backtest.py` gibt jetzt einen vollen `validation`-Block aus:
**OOS-Split · Walk-Forward · Monte-Carlo (2000 Läufe) · Zeit-Stabilität · Symbol-Stabilität**
— sobald Trades entstehen.

## 5. Nächste Schritte (Strategie)

| Prio | Schritt |
|---|---|
| **P1** | **2. Setup-Typ** (Trend-Continuation im *klaren* Regime, oder ein Mean-Reversion-Setup für Range-Phasen) — mit **eigener** IS/OOS-Kalibrierung. Der aktuelle Sweep-Reversal deckt nur einen schmalen Kontext ab. |
| **P1** | **`V9 BOS-Anker`-Regime** isoliert kalibrieren (eigenes Item, Gate nicht anfassen). |
| **P2** | **FX-Daten** (cTrader, sobald entblockt) + **2 Jahre Gold** (Dukascopy) — anderes Markt­verhalten. Läuft parallel. |
| **P2** | **Strategie-Hypothese hinterfragen:** wenn auch mit 2. Setup-Typ + FX kein OOS-Edge → der SMC-Ansatz in dieser Form trägt möglicherweise nicht, und die Hypothese muss neu gefasst werden. Ehrlich dokumentieren, nicht durch Parameter erzwingen. |

**Kernaussage:** Die Infrastruktur (Analyse, Backtest, Validierung) ist solide. Die **Strategie
selbst hat auf den verfügbaren Daten noch keine bewiesene Edge.** Das ist ein valides,
wichtiges Ergebnis — kein Grund, Regeln zu lockern.

## 6. Nachtrag (2026-08-31) — P1 „2. Setup-Typ" abgearbeitet

Siehe **`docs/STRATEGY-EDGE-INVESTIGATION-2026-08.md`**. `scripts/setup_research.py` testet
**6 strukturell verschiedene Swing-Setups** (Sweep+Reversal, Breakout+Retest, Trend-Pullback,
HTF-Break+Confirm, trend-gefilterter Breakout, D1-Trend+H4-Break) auf H4/D1, look-ahead-frei,
mit IS/OOS/Walk-Forward/Monte-Carlo über 7 Instrumente / 3+ Jahre, in 2 Management-Modellen.

**Ergebnis: keine zeigt eine robuste, regime-unabhängige, kosten-überlebende OOS-Edge.** Jedes
Setup fällt aus einem von drei Gründen durch: Vorzeichen kippt IS↔OOS (Overfit), „positive"
Phase = Bull-Market-Long-Beta, oder Monte-Carlo-`prob_positive` ≈ 0 unter realistischen Kosten.
Der **Befund dieses Dokuments erweitert sich**: nicht nur der Klassifikator, auch die **Setup-
Ebene** hat auf diesen Daten keine Edge. Regime-Gate ist damit endgültig als *Symptom*, nicht
Ursache, bestätigt (die neuen Setups umgehen das Gate und haben trotzdem keine Edge).
