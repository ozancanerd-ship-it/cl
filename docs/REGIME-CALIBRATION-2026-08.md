# Regime-Gate OOS-Kalibrierung — Ergebnis & Entscheidung (2026-08-29)

**Status:** abgeschlossen · **Entscheidung: konservative Baseline bleibt unverändert.**
`strategy_version 0.1.1` · Datengrundlage: `data/repository_real/` (Binance-Vision Spot Klines,
`binance-vision-spot-klines-v1`), BTCUSDT + ETHUSDT M5, native M15/H4/D1.

> Auftrag (wörtlich): *„NICHT einfach Parameter lockern, nur um mehr Trades zu erzeugen. Das Ziel
> ist nicht mehr Trades. Das Ziel ist bessere Entry-/Exit-Qualität."* — und: *„Ein Ergebnis wie
> 'Wir haben jetzt 50x mehr Trades' ist KEIN Erfolg."*

---

## 1. Setup der Analyse

| | |
|---|---|
| Harness | `scripts/regime_calibration.py` (deterministisch, PIT-sauber, worst-case Fill) |
| Zeitraum | 2024-06-01 → 2025-06-30 |
| IS / OOS Split | 2024-12-01 (IS 6 Monate, OOS 7 Monate) |
| Sampling | jede 12. M5-Bar (stündlich), beide Symbole → **18 912 Samples** |
| Forward-Probe | Entry am Close in Bias-Richtung, SL = 1.5·ATR(M5,14), TP = 2R, Time-Stop 96 M5-Bars, worst-case (SL vor TP). **Kein Strategie-Ergebnis — ein Signalqualitäts-Proxy.** |
| Walk-Forward | 4 Folds, rollierend, je ~5 Monate Train / ~2.5 Monate Test |
| Varianten | V0 Baseline · V1 `allow_one_unclear` · V2 nur D1 · V3 nur EXTREME blockt · V4 Regime-Confidence-Schwelle · V5 M15-Vol **nicht** hart blockend |

Fix gegenüber dem ersten Lauf: die Probe-Richtung nutzt jetzt `htf_bias` → D1-Richtung →
Vorzeichen von `regime.slope_norm` als Fallback (vorher ~98 % `None` → verzerrte Varianten).

---

## 2. Kernbefunde

### 2.1 Das Gate ist informativ — das ist nicht das Problem

| Population | n | Expectancy (R) | PF | Win-Rate | Max DD (R) | Loss-Streak |
|---|--:|--:|--:|--:|--:|--:|
| alle Bias-gerichteten Probes (IS) | 7 224 | **+0.008** | 1.01 | 33.8 % | 165 | 25 |
| `gate_ok = true` (IS) | 89 | **+0.382** | 1.71 | 46.1 % | 9 | 9 |
| `gate_ok = false` (IS) | 7 135 | +0.004 | 1.01 | 33.7 % | 165 | 25 |

Wenn das Gate feuert, ist die Entry-Qualität **dramatisch besser** (Expectancy ×48, halbe
Drawdown-Größenordnung). Das Gate trennt sauber. Der Engpass ist die **Abdeckung**, nicht die
Trennschärfe.

### 2.2 Abdeckung ist extrem niedrig und regime-abhängig

| Fenster | `gate_ok` % | Haupt-Ablehngründe |
|---|--:|---|
| IS (2024-06 → 12) | 1.01 % | `regime_unclear` 80 %, `regime_vol_extreme` 18 % |
| OOS (2024-12 → 2025-06) | **0.00 %** | `regime_unclear` 70 %, `regime_vol_extreme` 30 % |

Quartals-Abdeckung: 2024Q3 0.27 %, 2024Q4 1.74 %, **2025Q1/Q2 je 0.00 %**. Alle 89 IS-`gate_ok`
sind BTCUSDT (ETH: 0) → zusätzliche Konzentrations-/Kleinstichproben-Warnung.

### 2.3 Jede Lockerung verliert den Vorteil — und ist OOS negativ

| Variante | IS Expectancy / PF / Cov | OOS Expectancy / PF / Cov |
|---|---|---|
| V0 Baseline | **+0.382 / 1.71 / 1.0 %** | — / — / **0.0 %** |
| V1 allow_one_unclear | +0.078 / 1.12 / 24 % | **−0.031 / 0.95** / 16 % |
| V2 d1_only | +0.084 / 1.13 / 14 % | **−0.051 / 0.93** / 10 % |
| V3 only_extreme_blocked | +0.063 / 1.10 / 27 % | **−0.041 / 0.94** / 17 % |
| V4 regime_confidence | +0.046 / 1.07 / 9 % | **−0.095 / 0.86** / 7 % |
| V5 no_m15_vol_block | +0.074 / 1.12 / 12 % | **−0.114 / 0.84** / 8 % |

Lockern zieht den Erwartungswert IS von +0.38 R zurück auf ~+0.06 R (praktisch das
Bias-only-Niveau) und ist **OOS in allen Varianten netto verlierend**, mit Loss-Streaks von 30
und Drawdowns von 96–118 R.

### 2.4 Walk-Forward bestätigt: kein robuster Vorteil

| Fold (Test) | V0 | V1 | V3 | V5 |
|---|---|---|---|---|
| 0 · 2024-11 → 2025-01 | +0.44 (n=77) | +0.11 | +0.08 | +0.12 |
| 1 · 2025-01 → 2025-04 | n=0 | +0.07 (n=148) | +0.04 | **−0.25 (n=20)** |
| 2 · 2025-04 → 2025-06 | n=0 | **−0.03** | **−0.04** | **−0.07** |
| 3 · 2025-06 → … | n=0 | n=0 | n=0 | n=0 |

Nur Fold 0 (ein günstiges Quartal) ist für alle Varianten positiv. Fold 2 ist für **alle**
Varianten negativ. Das ist das Muster eines period-abhängigen Effekts, kein stabiler Edge.

---

## 3. Root-Cause (gegen die 8 Kandidaten aus dem Auftrag)

| # | Kandidat | Befund |
|---|---|---|
| 1 | Gate zu streng | **Teilwahr, aber schützend.** Die niedrige Abdeckung kostet hier kein Geld — die Varianten, die Abdeckung hinzufügen, sind OOS-negativ. |
| 2 | Struktur-Klassifikator zu konservativ (`derive_structure_state`) | **Bestätigt** (H4 = `unclear` 93 %). Trägt zur niedrigen Abdeckung bei. Aber: Lockern über V1/V2 bringt OOS nichts. Bleibt Backlog (eigenes, isoliertes Kalibrier-Item — nicht „Gate lockern"). |
| 3 | Datenqualität | **Ausgeschlossen.** 0 Quality-Issues, `data_confidence` 1.0. |
| 4 | Zeitraum für dieses Setup ungeeignet | **Stark bestätigt.** OOS-Forward-Probe ist für die *Basis*population UND jede Variante negativ. Das ist kein Gate-Problem — der SMC-Sweep-Reversal-Kontext trägt in 2025 H1 (BTC/ETH) schlicht nicht. |
| 5 | Bias/Richtung falsch | Ausgeschlossen — Long/Short symmetrisch, `conflict` nur 0.4 %. |
| 6 | Fehlende Bars | **Ausgeschlossen.** Keine Lücken. |
| 7 | Score uninformativ | Nicht die Ursache (`conflict` 0.4 %, Bias-Kanal sauber). |
| 8 | M15-Context-Vol als Hard-Block (V5) | **Kein robuster OOS-Vorteil.** V5 ist die *schlechteste* OOS-Variante (−0.114 / PF 0.84), WF Fold 1 −0.25, Fold 2 −0.07. Der scheinbar positive Eindruck aus dem ersten Lauf überlebt die korrigierte Probe **nicht**. |

---

## 4. Entscheidung

1. **Die konservative Baseline bleibt zu 100 % unverändert.** Kein `RegimeGateParams`-,
   `TrendParams`- oder `VolParams`-Default wird angefasst. `context_vol_is_hard_block`
   bleibt `True` (Default).
2. **Kein zweiter End-to-End-Backtest** — er würde nur die (unveränderte) Baseline erneut
   fahren. Nicht gerechtfertigt.
3. **`RegimeGateParams` ist jetzt konfigurierbar** (`MtfParams.regime_gate`, Defaults
   unverändert) + `context_vol_is_hard_block`-Flag — reine Architektur-Vorbereitung, damit
   eine spätere Kalibrierung greifen kann, **ohne** dass jetzt eine Verhaltensänderung
   passiert.
4. **Was die Analyse für später klärt:** Der Hebel ist *nicht* das Gate lockern. Die richtigen
   Hebel sind (a) **mehr Instrumente** (SOL, weitere Coins, XAUUSD) → das informative Gate
   bekommt mehr Gelegenheiten zu feuern, ohne die Trennschärfe zu opfern; (b) **niedrige
   Trade-Frequenz als korrekt akzeptieren** für diese Setup-Familie; (c) den
   Struktur-Klassifikator (#2) *isoliert* gegen einen vollen Marktzyklus (inkl. starkem
   Trend-Regime) prüfen — separat vom Gate.

---

## 5. Reproduktion

```bash
uv run python scripts/regime_calibration.py \
  --repo data/repository_real --symbols BTCUSDT ETHUSDT \
  --start 2024-06-01 --split 2024-12-01 --end 2025-06-30 --every 12
# → data/repository_real/regime_calibration.json  (+ _samples.json)
```

Report-Rohdaten: `data/repository_real/regime_calibration.json` (in Git, 36 KB).
