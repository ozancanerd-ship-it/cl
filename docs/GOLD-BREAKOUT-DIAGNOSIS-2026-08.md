# Gold Breakout-Retest — Diagnose & Verbesserung (2026-08-31)

Auftrag: das schlechte XAUUSD-Shadow-Ergebnis (6 Signale, −4.5R, 17 % WR) verstehen und
**OOS-geprüft** verbessern. Kein Overfitting, keine Filter-Lockerung.

Werkzeuge: `scripts/diag_gold_breakout.py` (Per-Trade-Forensik), `scripts/setup_research.py`
(Panel-Backtest S0–S13, IS/OOS/WF/MC), `scripts/xau_shadow.py` (Forward-Sim).

---

## 1. Was die 6 Trades zeigen (`diag_gold_breakout.py`, echtes Dukascopy-Spot-XAUUSD 2023)

| # | Datum | Ri. | R-Dist | MFE | MAE | Bars→Exit | fwd40 | Ergebnis |
|---|-------|-----|--------|-----|-----|-----------|-------|----------|
| 1 | 02-23 | SHORT | 0.96·ATR | +2.79R | −4.53R | 1 | −2.66R | −1.0R |
| 2 | 02-24 | SHORT | 1.21·ATR | +0.74R | −5.56R | 5 | −4.41R | −1.0R |
| 3 | 06-03 | SHORT | 0.89·ATR | +2.86R | −3.11R | 4 | −1.95R | −1.0R |
| 4 | 07-06 | SHORT | 0.92·ATR | +0.09R | −11.33R | 3 | −6.17R | −1.0R |
| 5 | 07-26 | LONG  | 0.95·ATR | +1.60R | −7.85R | 0 | −5.87R | −1.0R |
| 6 | 09-05 | SHORT | 2.09·ATR | +2.47R | −0.87R | 14 | +1.99R | +0.5R |

**Aggregat:** MAE-Median −5.0R · 5/6 voller Stop · R-Distanz-Median 0.96·ATR · **5 SHORT / 1 LONG**
in einem Jahr, in dem Gold +15 % machte · 3/6 Stopouts in ≤ 12 h · Ausbruch-Wucht-Median ~0.6·ATR.

### Ursachen (nach Wirkung sortiert)

1. **Falsche Richtung.** `fwd20`/`fwd40` sind in 4/6 Fällen klar negativ — die Trades laufen
   *nach* dem Einstieg gegen die Position. Der 2-Swing-D1-Struktur-Trend meldete 5× `trend_down`
   in einem Bullenjahr: jede zweite Pullback-Sequenz kippt ihn. Der „HTF-Trend-Filter" filtert
   nicht, er *verursacht* Gegen-Trend-Shorts auf Pullback-Tiefs.
2. **Zu schwacher Ausbruch.** `breakout_displacement_atr = 0.3` lässt Ausbrüche zu, die real
   ~0.5·ATR über der Kante schließen — Range-Rauschen, kein Continuation-Signal.
3. **Zu weite „Konsolidierung".** `consolidation_max_atr = 5.0` erlaubt 2–4·ATR-Choppy-Ranges
   als „Coil". Echte Continuation-Breakouts kommen aus engen Coils.
4. **Timing/Session.** 4/6 Einstiege 20–00 UTC (illiquide) — Fakeout-Fenster.
5. **Management ist zweitrangig.** Weiter/struktureller Stop macht es *schlechter* (−4.5R). Das
   Problem sind die Signale, nicht der Stop.

---

## 2. Was getestet wurde (`setup_research.py`, 12 Instrumente, Split 2025-01-01, scaled mgmt)

Neue Detektoren, **strukturell** motiviert (nicht an die 6 Trades gefittet):

| Setup | Idee | n | OOS exp | OOS PF | MC pp | sym-stab | Urteil |
|-------|------|---|---------|--------|-------|----------|--------|
| S4 (Basis) | D1-Trend-Filter | 131 | +0.374 | 2.03 | 0.61 | 0.75 | Referenz |
| S6 | Coil ≤2·ATR + Thrust ≥1·ATR | **0** | — | — | — | — | zu streng |
| S7 | Range-Kontraktion | 3 | — | — | — | — | zu streng |
| S8 | Session-Filter (20–04 UTC raus) | 83 | +0.325 | 1.86 | 0.27 | 0.60 | schwach |
| **S9** | **+ jüngster D1-BOS in Trendrichtung** | **112** | **+0.414** | **2.21** | **0.79** | **0.83** | **✓ INTEGRIERT** |
| S11 | S9 + Session | 71 | +0.407 | 2.15 | 0.47 | 0.70 | schlechter |
| S12 | S9 + Thrust ≥0.6·ATR | 52 | +0.065 | 1.13 | 0.49 | 0.56 | schlechter |
| S13 | S9 + Coil ≤3·ATR | 43 | +0.345 | 1.86 | 0.50 | 0.75 | schlechter |

**S9 dominiert S4 auf jeder Achse** bei weniger, besseren Trades. **Filter-Stacking über S9
hinaus (S11–S13) verschlechtert durchweg** → Stopp bei S9. Das ist die Grenze zwischen
Verbesserung und Overfitting.

### Regime-Gate (S14/S15) — geprüft, NICHT integriert

Idee: nur handeln, wenn der Markt tatsächlich trendet (Kaufman Efficiency Ratio auf H4-Closes,
120 Bars). Ergebnis: **zu streng** — S14 (ER ≥ 0.30) = 10 Trades im ganzen 12-Instrument-Panel
über 3 Jahre, S15 (ER ≥ 0.40) = 1 Trade. Die überlebenden Zahlen sehen glänzend aus (S14 OOS
+0.72 R, PF 3.8) — bei **n = 4 OOS** ist das die klassische Overfitting-Falle, kein Signal.
Ein weicheres Regime-Maß oder mehr Daten nötig; die Detektoren bleiben zur Wieder-Prüfung im
Research-Bench (`setup_research.py` S14/S15), werden aber **nicht** in die Strategie übernommen.

### S9-Logik

Zusätzlich zum D1-Struktur-Trend muss der **jüngste D1-BOS** (`structure_breaks`, kind=BOS,
Bruch-Bar in den letzten 20 D1-Bars) in dieselbe Richtung zeigen wie der Ausbruch. Ein echter
Trend hat einen frischen BOS; ein Pullback-getriggerter Struktur-Flip hat ihn nicht.

Integriert als `BreakoutRetestParams.require_htf_bos_confluence = True` (+ `d1_bos_lookback_bars
= 20`). SMC-Kette unverändert. `xau_shadow.py` liefert jetzt PIT-D1-Breaks mit.

---

## 3. Was S9 NICHT löst — ungeschönt

- **Echtes Spot-XAUUSD 2023 bleibt negativ.** S9: 4 Signale, −4.0R, 0 % WR (S4: 6 / −4.5R /
  17 %). Die Konfluenz entfernt 2 Trades, darunter zufällig den einzigen Gewinner → per Trade
  minimal schlechter. **4–6 Trades sind kein Urteil.** Breakout-*Continuation* hat in einem
  Range-Regime strukturell keine Edge — und 2023 war Golds choppigstes Jahr.
- **Ich habe kein reales Spot-Gold aus einer Trendphase.** Dukascopy-Voll-Ingest von der
  Umgebung blockiert (Prozess-Zeitlimit). Reales Spot-XAUUSD = nur 2023 + 2024-01 + 2026-05..08.
- **Die Panel-OOS-Edge ruht stark auf Yahoo-indikativen Daten (GC=F = Futures) + FX-Proxy.**
  Auf XAUUSD-YF (2024–26, Trend) macht S9 +9.0R / 73 % WR — aber das ist Futures, nicht Spot.
- **M5-Parquet für XAUUSD ist korrupt** (`Invalid thrift`) — H4/M15/D1 lesbar, M5 nicht. Kein
  Blocker für H4-Research/Shadow; blockiert M5-Feinsimulation.

---

## 4. Status & nächste Schritte

- **`SETUP-BREAKOUT-RETEST-01` bleibt `IN_VALIDATION` / SHADOW.** Baseline aktualisiert
  (exp 0.28, PF 1.8, WR 0.60, n 112). Kein Live-Signal.
- Forward-Journale: `data/repository_real/live/xau_shadow_realgold_2023_s9.jsonl`,
  `…/xau_shadow_yf_s9.jsonl`.
- **NEXT:** Breakout-Setup zusätzlich per **Trending-vs-Ranging-Regime-Gate** absichern
  (der `htf_regime_gate` liefert die Bausteine) — die Diagnose zeigt, dass das Setup in Ranges
  systematisch verliert. Danach erneut Panel + Real-Gold prüfen.
- **NEXT:** vollständige Dukascopy-Spot-Historie (Nutzer-Rechner) → `scripts/validate_s4.py`
  entscheidet automatisch VALIDATED-Kandidat / RETIRE.
