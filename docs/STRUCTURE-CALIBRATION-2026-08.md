# Struktur-Klassifikator-Kalibrierung — Ergebnis & Entscheidung (2026-08-29)

**Status:** abgeschlossen · **Entscheidung: Baseline `derive_structure_state` bleibt unverändert.**
Kalibriert **isoliert vom Regime-Gate**, wie im Auftrag gefordert.

Harness: `scripts/structure_calibration.py` · Report: `data/repository_real/structure_calibration.json`

---

## 1. Setup

| | |
|---|---|
| Instrumente | BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT, XRPUSDT, DOGEUSDT |
| Zeitraum | 2023-01-01 → 2025-06-30 (**voller Zyklus**: 2023 Bärmarkt/Erholung, 2024 Bull, 2025 Korrektur) |
| Klassifikations-TF | D1 (H4 zusätzlich für MTF-Disagreement) |
| Sampling | jede D1-Bar, beide Fenster → **5 316 Samples** |
| IS / OOS Split | 2024-05-01 (IS 2 760 / OOS 2 556) |
| Truth-Proxy | realisierte Vorwärts-Richtung über 15 D1-Bars: `net_move / ATR(D1,14)`; ≥ 1.5 = `trend_up`, ≤ −1.5 = `trend_down`, sonst `range` |
| R-Probe | standardisierter Entry in Klassifikations-Richtung, SL 1.5·ATR(D1), TP 2R, max-hold 20 D1-Bars, worst-case Fill — **kein Strategie-Ergebnis, ein Signalqualitäts-Maß** |
| Walk-Forward | 4 rollierende Test-Folds (~5,5 Monate) |

**Varianten** (nur die Struktur-Ebene — kein Slope, kein Vol, kein Gate):

| Variante | `detect_swings` | `min_swings` | Idee |
|---|---|---|---|
| **V0 Baseline** | left/right 2, leg 0.5·ATR | 2 | aktueller Default |
| V1 | 2 / 0.5 | 1 | lockerer |
| V2 | 2 / 0.5 | 3 | strenger |
| V3 | 3 / 0.5 | 2 | signifikantere Fraktale |
| V4 | 3 / 0.5 | 3 | Fraktale + strenger |
| V5 | 2 / **1.0·ATR** | 2 | größere Mindest-Leg (Mikro-Swing-Filter) |
| V6 | 3 / 1.0 | 2 | Fraktale + größere Leg |

---

## 2. Kernbefunde

### 2.1 Der Baseline-Klassifikator hat bereits einen robusten OOS-Vorteil

| | Coverage | Accuracy* | false_trend | false_range | Churn | Probe Expectancy / PF |
|---|--:|--:|--:|--:|--:|--:|
| V0 **IS** | 23.1 % | 0.307 | 0.586 | 0.462 | 0.089 | **+0.198 / 1.40** |
| V0 **OOS** | 18.0 % | 0.298 | 0.472 | 0.503 | 0.111 | **+0.315 / 1.65** |

\* `P(realisierte Richtung == Call | Call gerichtet)`. Deckel liegt niedrig, weil ~50 % der
Perioden `range` sind — ein gerichteter Call kann dann nur „daneben" liegen.

**Walk-Forward — V0 Probe-Expectancy in ALLEN 4 Test-Folds positiv:** +0.51 · +0.22 · +0.33 · +0.23 R.
Das ist über einen vollen Zyklus stabil.

### 2.2 Keine Variante liefert einen robusten Vorteil

| Variante | IS Exp / OOS Exp | Walk-Forward | Urteil |
|---|---|---|---|
| **V1** (lockerer) | +0.156 / **+0.017** | Fold 2 & 3 ≈ 0 | ❌ OOS-Edge kollabiert (Coverage 63 %, aber wertlos) — dieselbe Overfit-Falle wie das Regime-Gate |
| **V2** (strenger) | +0.195 / +0.385 | Fold 3 Accuracy 0.056 | ❌ höhere Expectancy nur auf n=140 (Rauschen), Accuracy bricht ein, Coverage 3–10 % |
| **V3/V4/V6** (Fraktal 3) | ≤ +0.036 / ≤ +0.17 | mehrere Folds **negativ** (V4 −0.35) | ❌ größere Fraktale verschlechtern Accuracy durchweg |
| **V5** (Leg 1·ATR) | +0.234 / +0.231 | alle Folds positiv (+0.48/+0.14/+0.15/+0.36) | ➖ **im Rauschen von V0**: IS minimal besser, WF-Folds 1&2 schwächer; Symbol-Split ist ein Wash (V5 besser auf BTC/ETH/BNB, schlechter auf SOL/XRP/DOGE) |

### 2.3 Was die Analyse über die Klassifikations-Grenzen sagt

- **`false_range_rate` ≈ 0.48** — sagt der Klassifikator `UNCLEAR`, folgt in ~der Hälfte der
  Fälle doch ein starker Trend. Das ist die **eigentliche Schwäche**. Aber: jede Variante, die
  das senkt (V1), erkauft es mit vielen **falsch gerichteten** Calls (`wrong_direction_rate`
  0.17–0.26) → Expectancy kollabiert. Der Markt telegrafiert diese Trends auf D1-Swing-Ebene
  schlicht nicht. → **Kein Threshold-Problem**, sondern ein „fehlender Mechanismus" (Kandidat:
  Compression/Expansion-Detektor oder ein „structure forming"-Vorzustand) → Continuous-
  Improvement-Gate, keine Parameteränderung.
- **`false_trend_rate` ≈ 0.50** — dennoch netto positiv (PF 1.4–1.65), weil die richtigen
  Trend-Calls bei 2R landen und die falschen bei −1R.
- **MTF-Disagreement D1↔H4:** `one_unclear` 88 %, `conflict` 4–8 %, **`aligned` nur 5 %**. D1
  und H4 geben selten gleichzeitig einen sauberen gerichteten Read. Das erklärt direkt, warum
  das Regime-Gate (braucht **beide** HTF gerichtet) so selten feuert — und bestätigt die
  Regime-Kalibrierungs-Schlussfolgerung: der Hebel ist **mehr Instrumente**, nicht ein loseres
  Gate.
- **Quartals-Verlauf V0:** Probe-Expectancy negativ in 2023Q1/Q2 (choppy Bär), stark positiv in
  Trend-Quartalen (2023Q4 +0.57, 2024Q4 +0.65). Das ist das **erwartete** Verhalten eines
  Trend-Klassifikators — er ist öfter richtig, wenn es Trends *gibt*.

---

## 3. Entscheidung

1. **`derive_structure_state` + `detect_swings` bleiben unverändert.** `left=2, right=2,
   min_leg_atr=0.5, min_swings=2`. Kein Default angefasst.
2. Grund: V0 hat bereits einen robusten, positiven OOS- + Walk-Forward-Forward-Edge; **keine**
   Variante verbessert das robust. Regel des Auftrags: „Wenn keine robuste Verbesserung
   gefunden wird → Baseline behalten."
3. Keine Optimierung auf Coverage / Trade-Count — die niedrige Coverage (~18–23 %) ist korrekt
   für diese Setup-Familie.

### Backlog (Continuous Improvement, NICHT jetzt)

- **`false_range_rate` ≈ 0.48** — neuer Mechanismus statt Threshold: Compression/Expansion-
  Erkennung, „structure forming"-Vorzustand, oder eine explizite Range-Bruch-Vorwarnung.
  Gegen OOS testen wie hier.
- **`V5_leg_1atr`** als „in Reserve" — mechanisch sauber (Mikro-Swing-Filter), ~neutral,
  Wiedervorlage mit mehr Instrumenten / längerer Historie. **Nicht adoptiert.**
- Struktur-Klassifikator auf **H4** separat kalibrieren (dieser Lauf war D1-fokussiert).

---

## 4. Reproduktion

```bash
uv run python scripts/structure_calibration.py \
  --repo data/repository_real \
  --symbols BTCUSDT ETHUSDT SOLUSDT BNBUSDT XRPUSDT DOGEUSDT \
  --start 2023-01-01 --split 2024-05-01 --end 2025-06-30 --tf D1
# → data/repository_real/structure_calibration.json
```
