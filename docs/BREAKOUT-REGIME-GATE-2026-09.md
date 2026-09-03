# Breakout-Regime-Gate — Trending vs. Ranging (2026-09-03)

**Frage (Masterplan-NEXT):** `SETUP-BREAKOUT-RETEST-01` (S9) zusätzlich per Trending-vs-Ranging-
Regime-Gate absichern, damit Breakout-Setups in ungeeigneten Ranging-Regimen zuverlässig
herausgefiltert werden.

## Ausgangslage

S9 hat **bereits** ein implizites Regime-Gate:

1. `require_d1_trend` — D1-Struktur muss `TREND_UP`/`TREND_DOWN` sein (nicht `RANGE`/`UNCLEAR`).
2. `require_htf_bos_confluence` — der jüngste D1-BOS muss in dieselbe Richtung zeigen.

Die dokumentierte Schwäche (`docs/GOLD-BREAKOUT-DIAGNOSIS-2026-08.md`): auf **echtem Dukascopy-
Spot-XAUUSD 2023** (Golds choppigstes, am stärksten range-gebundenes Jahr) liefert S9 nur
4 Signale, −4.0 R, 0 % WR. Der 2-Swing-D1-Struktur-Trend meldet in flachen Pullbacks „TREND",
obwohl der Markt seitwärts läuft — genau der Fall, den ein sauberes Regime-Gate abfangen soll.

## Bereits getestete Gate-Varianten (research bench)

`scripts/setup_research.py`, Panel 12 Instrumente, IS/OOS-Split 2025-01, scaled mgmt
(`data/repository_real/research/setup_research_v9_regime.json`):

| Variante | Gate | n (all) | n (OOS) | OOS exp | Urteil |
|---|---|---|---|---|---|
| **S9** | D1-Trend + D1-BOS-Konfluenz | 112 | 54 | **+0.414 R** | Baseline |
| S14 | S9 + Efficiency-Ratio ≥ 0.30 | **10** | **4** | +0.72 R | **Stichprobe zerstört** |
| S15 | S9 + Efficiency-Ratio ≥ 0.40 | **1** | **0** | n/a | unbrauchbar |
| S11–S13 | S9 + Session / Displacement / Coil-Filter | 60–90 | — | schlechter als S9 | verworfen |
| S16 | S9 + DXY-Gegenwind (nur Gold) | ~108 | — | ≈ S9 | kein Mehrwert |

**Kernbefund:** Ein *harter* ER-Trending-Gate (≥ 0.30) filtert das Ranging-Regime tatsächlich —
aber er schneidet 91 % aller Trades weg. OOS n = 4 ist keine Validierung; Monte-Carlo /
Walk-Forward sind mit n < 5 nicht durchführbar. Die „besseren" Kennzahlen von S14 sind reine
Selektion auf 10 Gewinner, nicht robust. Das entspricht exakt der Kalibrierungs-Regel:
**kein robuster OOS-Beleg → Baseline bleibt (S9), keine Lockerung, keine Verschärfung.**

## Diese Session (2026-09): sanftes Gate S17/S18

Neue Forschungs-Detektoren (nicht integriert, nur bench):

- **S17** = S9 + Efficiency-Ratio ≥ **0.20**
- **S18** = S9 + Efficiency-Ratio ≥ **0.25**

Ziel: der Bereich *zwischen* „kein Gate" (S9) und „zu streng" (S14). Rein preisbasiert
(Kaufman Efficiency Ratio auf 120 H4-Closes ≈ 20 D1-Bars), damit nicht überfittbar.

**Ergebnis** — Teil-Panel (XAUUSDT, XAUUSD-YF, EURUSD-YF, GBPUSD-YF, BTC, ETH, SOL),
Split 2025-01, scaled mgmt (`data/repository_real/research/setup_research_v11_regime_soft.json`):

| Variante | all n | OOS n | OOS exp | OOS PF | WF pos | MC prob_positive | Symbol-Stabilität |
|---|---|---|---|---|---|---|---|
| **S9** | 62 | 34 | +0.573 R | 3.10 | 5/7 | **0.93** | **1.00** |
| S17 (ER ≥ 0.20) | 29 | 16 | +0.439 R | 2.36 | — | **0.58** | **0.60** |
| S18 (ER ≥ 0.25) | 17 | 11 | +0.606 R | 3.16 | — | n/a (zu wenige) | 0.50 |
| S14 (ER ≥ 0.30) | 6 | 4 | +0.72 R | 3.80 | — | n/a | 1.00 |

**Befund:** Auch das *sanfte* Gate (S17/S18) halbiert die Stichprobe und **verschlechtert** die
Robustheit: Monte-Carlo `prob_positive` fällt 0.93 → 0.58, Symbol-Stabilität 1.00 → 0.60. Der
höhere Punkt-Expectancy von S18 (+0.61) steht auf n = 11 OOS und überlebt keine MC-Prüfung. Das
Gate entfernt vor allem gute Continuation-Trades aus klar trendenden FX/Crypto-Phasen — es trifft
nicht die dokumentierte Ranging-Schwäche (die echte XAUUSD-2023-Serie liegt vollständig im
IS-Fenster und hat im OOS ohnehin 0 S9-Trades).

## Verdikt

**Kein zusätzliches Regime-Gate ist ein robuster Fortschritt** — weder hart (S14/S15) noch
sanft (S17/S18). Jede getestete Schwelle senkt die Monte-Carlo-Wahrscheinlichkeit und die
Symbol-Stabilität. S9s bestehendes Doppel-Gate (D1-Struktur-Trend + D1-BOS-Konfluenz) ist der
beste bislang gefundene Trending-Filter.

- **Keine Integration.** S9 bleibt Baseline (`require_htf_bos_confluence=True`).
- `SETUP-BREAKOUT-RETEST-01` bleibt **`IN_VALIDATION` / SHADOW**.
- XAUUSDT bleibt SHADOW/PAPER, kein Echtgeld — bis vollständige Dukascopy-Historie **und**
  ≥ 100 Forward-Trades vorliegen.
- S17/S18 bleiben als bench-Detektoren erhalten; final zu prüfen auf der Nutzer-Maschine mit
  lückenloser Spot-Gold-Historie (12-Instrument-Panel).

## Blocker

- Echte Dukascopy-Spot-XAUUSD-Historie ist in dieser Umgebung **unvollständig** (H4-Lücke
  2024-01 → 2026-05, 820 Tage; M5-Parquet Thrift-defekt). Der Voll-Ingest braucht die
  Nutzer-Maschine (kein Prozess-Zeitlimit). `scripts/validate_s4.py` erkennt den Zustand
  jetzt sauber (H4-Fallback statt Hard-Crash auf der defekten M5-Parquet).
